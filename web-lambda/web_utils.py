from activity_counters import ACTIVITY_COUNTER_VERSION, ACTIVITY_TIMEZONE, activity_counts, activity_day
from shared_utils import get_dynamodb_item
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

from query_dtos import ArticleCommentQueryDTO, ArticleQueryDTO
from creator_links import encode_creator_click, get_creator_link
from starlette.background import BackgroundTask
from starlette.responses import RedirectResponse, Response
from web import HTTPException
from web_route_metadata import WEB_AUTH_URL_ROUTES

from app_config import (
    get_auth_jwt_secret, get_auth_token_max_age, get_cognito_client_id,
    get_cognito_domain, get_web_base_url, is_prod,
)
from shared_utils import *
from shared_utils import (
    Article, ArticleStatus, ArticleQueryType, ArticleNotFoundError,
    ArticleByOldSlugRequestedError, User, UserStatus, UserNotFoundError,
    UserByOldSlugRequestedError, NotAuthenticatedError, Permission,
    find_user, get_latest_articles_by_user, find_article, find_article_by_slug_follow_redirects, find_user_by_username_follow_redirects,
    check_authorization, verify_authorization,
    article_comment_from_dynamodb, decode_offset, get_url, get_article_url, get_user_url, logger,
    query_dynamodb_table, Key,
)


def get_login_redirect_url(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/oauth2/authorize"
            f"?client_id={get_cognito_client_id()}"
            f"&response_type=code"
            f"&redirect_uri={quote(callback_url, safe='')}"
            f"&scope=openid+email+profile"
        )

    return callback_url


def get_user_token_by_code(code: str, callback_url: str, *, req=None, verifier=None, nonce=None) -> UserTokenDTO:
    from cognito_auth import exchange_login_code

    user_token = exchange_login_code(code, callback_url, verifier=verifier, nonce=nonce)
    upsert_user_by_user_token(user_token, req=req)
    return user_token


def create_auth_jwt_token(token: UserTokenDTO) -> str:
    expires_in = get_auth_token_max_age()

    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_in)

    from jose import jwt
    return jwt.encode(
        claims={
            "sub": token.sub,
            "iss": "internal_auth",
            "origin_iss": token.iss,
            "sid": uuid.uuid4().hex,
            "email": token.email,
            "name": token.name,
            "username": token.username,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
            "type": "auth_token",
            "aud": "blog",
            "origin_aud": token.aud,
        },
        key=get_auth_jwt_secret(),
        algorithm="HS256"
    )


def get_logout_redirect_url(callback_url: str) -> str:
    if is_prod():
        return (
            f"https://{get_cognito_domain()}/logout"
            f"?client_id={get_cognito_client_id()}"
            # f"&response_type=code"
            f"&logout_uri={quote(callback_url, safe='')}"
            # f"&scope=openid+email+profile"
        )

    return callback_url


def get_auth_return_url(req, value: str | None) -> str:
    from login_flow import return_url
    return return_url(value)


def get_redirect_url(req) -> str:
    return get_auth_return_url(req, req.query_params.get("redirect_url") or req.headers.get("referer"))


def get_user_activities(user: User, year: int | None = None) -> dict[str, Any]:
    now = datetime.now(ACTIVITY_TIMEZONE)
    if year is None:
        # Show the current calendar month plus the preceding 11 months.
        month_index = now.year * 12 + (now.month - 1) - 11
        start_year, start_month = divmod(month_index, 12)
        start = datetime(start_year, start_month + 1, 1, tzinfo=ACTIVITY_TIMEZONE)
        end, calendar_year = now, None
    else:
        if year < 1970 or year > now.year:
            raise ValueError("invalid activity year")
        start, end, calendar_year = datetime(year, 1, 1, tzinfo=ACTIVITY_TIMEZONE), datetime(year + 1, 1, 1,
                                                                                        tzinfo=ACTIVITY_TIMEZONE), year
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    first_day = start.date()
    last_day = (end - timedelta(milliseconds=1)).date()
    ready = getattr(user, 'activity_version', 0) == ACTIVITY_COUNTER_VERSION

    def daily_counts(include_legacy):
        counts = {}
        cursor = None
        while True:
            page = query_dynamodb_table(
                key_condition_expr=Key('pk').eq(f'USER_ACTIVITY#{user.id}') & Key('sk').between(
                    f'DAY#{first_day.isoformat()}', f'DAY#{last_day.isoformat()}'
                ),
                exclusive_start_key=cursor,
                consistent_read=True,
            )
            for item in page.get('Items', []):
                total = sum(activity_counts(item, include_legacy=include_legacy).values())
                if total:
                    counts[item['sk'].removeprefix('DAY#')] = total
            cursor = page.get('LastEvaluatedKey')
            if not cursor:
                return counts

    counts = daily_counts(ready)
    if not ready:
        # Until migration activates counters, legacy rows remain intact. Historical
        # counter fields are ignored here so migrated events cannot count twice.
        cursor = None
        while True:
            page = query_dynamodb_table(
                key_condition_expr=Key('pk').eq(f'USER_ACTIVITY#{user.id}') & Key('sk').between(
                    f'ACTIVITY#{start_ms}', f'ACTIVITY#{end_ms - 1}#~'
                ),
                projection_expr='event_type, created_at, profile_visible',
                consistent_read=True,
                exclusive_start_key=cursor,
            )
            for item in page.get('Items', []):
                if item.get('profile_visible'):
                    day = activity_day(item['created_at'])
                    counts[day] = counts.get(day, 0) + 1
            cursor = page.get('LastEvaluatedKey')
            if not cursor:
                break
        # Activation may have happened while this request was reading raw rows.
        # Re-read counters after activation, before trusting a partially pruned history.
        current = get_dynamodb_item(f'USER#{user.id}', 'META', consistent_read=True)
        if current and current.get('activity_v') == ACTIVITY_COUNTER_VERSION:
            counts = daily_counts(True)
    calendar_days = []
    cursor = first_day
    while cursor <= last_day:
        key = cursor.isoformat()
        calendar_days.append({"date": key, "count": counts.get(key, 0)})
        cursor += timedelta(days=1)
    months = []
    for day in calendar_days:
        month_key = day["date"][:7]
        if not months or months[-1]["key"] != month_key:
            month_date = datetime.strptime(month_key, "%Y-%m")
            months.append({"key": month_key, "label": month_date.strftime("%b"), "days": []})
        months[-1]["days"].append(day)
    for month in months:
        first = datetime.strptime(month["key"], "%Y-%m").date()
        leading = (first.weekday() + 1) % 7
        cells = [{"date": None, "count": 0}] * leading + month["days"]
        while len(cells) % 7:
            cells.append({"date": None, "count": 0})
        month["weeks"] = [cells[index:index + 7] for index in range(0, len(cells), 7)]
        month["week_count"] = len(month["weeks"])
        while len(month["weeks"]) < 6:
            month["weeks"].append([{"date": None, "count": 0} for _ in range(7)])
    return {
        "year": calendar_year,
        "current_year": now.year,
        "total": sum(counts.values()),
        "days": counts,
        "calendar_days": calendar_days,
        "months": months
    }


def get_article_author_articles(article: Article):
    """A bounded public selection; no draft access even for its owner."""
    author = find_user(article.user_id)
    if author is None or author.status != UserStatus.ACTIVE:
        return author, []
    query = ArticleQueryDTO(limit=9, status=ArticleStatus.PUBLISHED)
    candidates = get_latest_articles_by_user(author, query)
    return author, [candidate for candidate in candidates if candidate.id != article.id][:8]


def get_latest_published_articles(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[Article]:
    query_dto = ArticleQueryDTO(limit=limit)
    return get_latest_articles(query_dto)


def should_show_popular_articles(latest_articles: list[Article], popular_articles: list[Article]) -> bool:
    """
    Show popular posts only if popular_posts differ from latest_posts.
    Comparison is based on post IDs.
    """
    latest_ids = [article.id for article in latest_articles]
    popular_ids = [article.id for article in popular_articles]

    # Show popular posts only if the lists are not exactly equal
    return latest_ids != popular_ids


def get_popular_published_articles(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[Article]:
    query_dto = ArticleQueryDTO(limit=limit)
    return get_popular_articles(query_dto)


def get_latest_article_comments(query_dto: ArticleCommentQueryDTO = None) -> list[dict]:
    if query_dto is None:
        query_dto = ArticleCommentQueryDTO()

    results = []
    articles = {}
    offset = decode_offset(query_dto.offset) if query_dto.offset else None
    while len(results) < query_dto.limit:
        response = query_dynamodb_table(
            index_name="POST_COMMENTS_BY_CREATED_AT",
            key_condition_expr=Key("post_comment_pk").eq("POST_COMMENT"),
            scan_index_forward=False,
            limit=max(12, query_dto.limit),
            exclusive_start_key=offset,
        )
        for item in response.get("Items", []):
            article_id = item["post_id"]
            if article_id not in articles:
                articles[article_id] = find_article(article_id)
            article = articles[article_id]
            if article is None or article.status != ArticleStatus.PUBLISHED:
                continue
            results.append({"comment": article_comment_from_dynamodb(item), "article": article})
            if len(results) == query_dto.limit:
                break
        offset = response.get("LastEvaluatedKey")
        if not offset:
            break
    return results


def get_popular_active_users(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[User]:
    query_dto = UserQueryDTO(limit=limit)
    return get_popular_users(query_dto)


def parse_articles_url_slugs_path(slugs_path: str) -> dict:
    data = {}
    slugs = [p for p in slugs_path.split("/") if p]

    if not slugs:
        return {}

    try:
        data["type"] = ArticleQueryType(slugs[0])
        slugs = slugs[1:]
    except ValueError:
        pass

    data["tags"] = slugs

    return data


def get_article_by_slugs(user_slug: str, article_slug: str, cur_user: User = None) -> Article:
    article = find_article_by_slug_follow_redirects(article_slug)
    if article is None:
        raise ArticleNotFoundError(f"Post '{article_slug}' not found")
    if article.user_slug != user_slug:
        raise UserNotFoundError(f"User '{user_slug}' not found")
    if article.status != ArticleStatus.PUBLISHED and (
            not cur_user or not check_authorization(cur_user, Permission.READ_NON_PUBLISHED_ARTICLE, article)):
        raise ArticleNotFoundError(f"Post '{article_slug}' not found")
    if article.slug != article_slug:
        raise ArticleByOldSlugRequestedError(article_slug, article)
    return article


def get_user_by_slug(username: str, cur_user: User = None) -> User:
    user = find_user_by_username_follow_redirects(username)
    if user is None:
        raise UserNotFoundError(f"User '{username}' not found")
    if user.status != UserStatus.ACTIVE:
        if not cur_user:
            raise NotAuthenticatedError()
        verify_authorization(cur_user, Permission.READ_NON_ACTIVE_USER, user)
    if user.username != username:
        raise UserByOldSlugRequestedError(username, user)
    return user


def get_legacy_user_redirect_url(req, slug: str) -> str | None:
    user = find_user_by_username_follow_redirects(slug)
    return get_user_url(req, user) if user else None


def get_legacy_article_redirect_url(req, user_slug: str, article_slug: str) -> str | None:
    user = find_user_by_username_follow_redirects(user_slug)
    article = find_article_by_slug_follow_redirects(article_slug)
    if not user or not article or article.user_slug != user.username:
        return None
    return get_article_url(req, article)


def get_legacy_article_id_redirect_url(req, user_slug: str, article_id: str) -> str | None:
    user = find_user_by_username_follow_redirects(user_slug)
    article = find_article(article_id)
    if not user or not article or article.user_slug != user.username:
        return None
    return get_article_url(req, article)


def _auth_cookie_domain() -> str | None:
    hostname = urlparse(get_web_base_url()).hostname
    if not hostname or hostname in {"localhost", "127.0.0.1"} or "." not in hostname:
        return None
    return f".{hostname}"


def set_token_cookie(token, response):
    response.delete_cookie("token")
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        secure=is_prod(),
        domain=_auth_cookie_domain(),
        samesite="lax",
        max_age=get_auth_token_max_age(),
    )


def drop_token_cookie(response):
    response.delete_cookie("token")
    response.delete_cookie("token", domain=_auth_cookie_domain())


def notify_creator_link_click(owner_id, kind, link_id, article_id, actor_id, req):
    """Deliver an authenticated event to the API; delivery failures preserve navigation."""
    import httpx

    try:
        token = encode_creator_click(owner_id, kind, link_id, article_id, actor_id)
        with httpx.Client(timeout=httpx.Timeout(15, connect=3, write=3, pool=3)) as client:
            response = client.post(get_url(req, 'notify-creator-link-click', absolute=True),
                                   headers={'Authorization': f'Bearer {token}'})
            response.raise_for_status()
    except Exception:
        logger.warning('Creator link click notification failed', exc_info=True)


def redirect_creator_link(user: User, link_id: str, req, cur_user: User | None = None,
                          article: Article | None = None, kind: str = 'support') -> Response:
    try:
        link = get_creator_link(user, kind, link_id, article)
    except ValueError:
        raise HTTPException(status_code=404)
    if req.method != 'POST':
        raise HTTPException(status_code=405, headers={'Allow': 'POST'})
    web_origin = urlparse(get_web_base_url())
    if (req.headers.get('origin') != f'{web_origin.scheme}://{web_origin.netloc}'
            or req.headers.get('sec-fetch-site') == 'cross-site'):
        raise HTTPException(status_code=403, detail='Open this link from its creator or article page')
    headers = {
        'Cache-Control': 'no-store', 'Referrer-Policy': 'strict-origin-when-cross-origin',
        'X-Robots-Tag': 'noindex, nofollow',
    }
    background = None
    prefetch = any('prefetch' in req.headers.get(header, '').lower()
                   for header in ('purpose', 'sec-purpose', 'x-purpose'))
    if prefetch:
        return Response(status_code=204, headers=headers)
    source_url = (get_article_url(req, article, absolute=True) if article is not None
                  else get_user_url(req, user, absolute=True))
    logger.info('Support link clicked', extra={'context': {
        'source': source_url, 'owner_id': user.id, 'link_id': link_id, 'link_name': link.name,
    }})
    if cur_user is None or cur_user.id != user.id:
        background = BackgroundTask(
            notify_creator_link_click, user.id, kind, link_id,
            article.id if article is not None else None, cur_user.id if cur_user is not None else None, req,
        )
    return RedirectResponse(link.url, status_code=303, background=background, headers=headers)
