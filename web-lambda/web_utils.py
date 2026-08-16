from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from shared_utils import *


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


def get_user_token_by_code(code: str, callback_url: str) -> UserTokenDTO:
    if is_prod():
        if not code:
            raise InvalidCodeError("Missing code")

        token_url = f"https://{get_cognito_domain()}/oauth2/token"
        cognito_client_id = get_cognito_client_id()
        cognito_client_secret = get_cognito_client_secret()
        data = {
            "grant_type": "authorization_code",
            "client_id": cognito_client_id,
            "code": code,
            "redirect_uri": callback_url,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + base64.b64encode(
                f"{cognito_client_id}:{cognito_client_secret}".encode()
            ).decode()
        }

        import httpx
        with httpx.Client() as client:
            token_resp = client.post(token_url, data=data, headers=headers)
            if token_resp.status_code != 200:
                logger.error(f"Token exchange failed: {token_resp.status_code} {token_resp.text}")
                raise CodeExchangeFailedError("Failed to exchange code")
            tokens = token_resp.json()
            # logger.debug(f"Cognito token response: {tokens}")

        id_token = tokens.get("id_token")
        if not id_token:
            raise InvalidTokenError("Missing id_token in Cognito response")
        from jose import jwt
        claims = jwt.get_unverified_claims(id_token)
        if claims.get("token_use") != "id":
            raise InvalidTokenError(f"Unexpected token_use: {claims.get('token_use')}")

        tokens = {"id_token": id_token}
        user_token = user_token_from_jwt_claims(claims, encode_offset(tokens))
    else:
        try:
            token_args = decode_offset(code) if code else {}
        except (ValueError, UnicodeError) as exc:
            raise InvalidCodeError("Invalid code") from exc
        user_token = get_dummy_user_token(**token_args)

    upsert_user_by_user_token(user_token)
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


def get_redirect_url(req) -> str:
    redirect_url = req.query_params.get("redirect_url")

    if not redirect_url:
        referer = req.headers.get("referer")
        if referer:
            parsed = urlparse(referer)
            base_url = urlparse(get_web_base_url())

            # If referer has no netloc (relative path) → safe
            # If referer belongs to your domain → safe
            if not parsed.netloc or parsed.netloc == base_url.netloc:
                redirect_url = referer

    if not redirect_url:
        redirect_url = get_url(req, "index")

    return redirect_url


def get_user_activity(user: User, year: int | None = None, recent_limit: int = 10) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if year is None:
        # Show the current calendar month plus the preceding 11 months.
        month_index = now.year * 12 + (now.month - 1) - 11
        start_year, start_month = divmod(month_index, 12)
        start = datetime(start_year, start_month + 1, 1, tzinfo=timezone.utc)
        end, calendar_year = now, None
    else:
        if year < 1970 or year > now.year:
            raise ValueError("invalid activity year")
        start, end, calendar_year = datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1,
                                                                                        tzinfo=timezone.utc), year
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    resp = query_dynamodb_table(
        key_condition_expr=Key("pk").eq(f"USER_ACTIVITY#{user.id}") & Key("sk").between(f"ACTIVITY#{start_ms}",
                                                                                        f"ACTIVITY#{end_ms}#~"),
        scan_index_forward=False, limit=1000)
    activities = [user_activity_from_dynamodb(item) for item in resp.get("Items", []) if item.get("profile_visible")]
    counts = {}
    for activity in activities:
        day = datetime.fromtimestamp(float(activity.created_at) / 1000, tz=timezone.utc).date().isoformat()
        counts[day] = counts.get(day, 0) + 1
    first_day = start.date()
    last_day = (end - timedelta(milliseconds=1)).date()
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
    return {"year": calendar_year, "current_year": now.year, "total": len(activities),
            "days": counts, "calendar_days": calendar_days, "months": months, "recent": activities[:recent_limit]}


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


def get_article_related_articles(article: Article, limit: int = 10) -> list[Article]:
    if not article.tags:
        return []

    query_dto = ArticleQueryDTO()
    query_dto.tags = article.tags
    articles = get_popular_articles_by_tags(query_dto, or_mode=True)
    article_tags = set(article.tags)
    related_articles = [candidate for candidate in articles if candidate.id != article.id]
    return sorted(
        related_articles,
        key=lambda candidate: len(article_tags.intersection(candidate.tags)),
        reverse=True,
    )[:limit]


def get_latest_article_comments(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[ArticleComment]:
    query_dto = ArticleCommentQueryDTO(limit=limit)

    return query_dynamodb_items(
        query_dto=query_dto,
        index_name="POST_COMMENTS_BY_CREATED_AT",
        key_condition_expr=Key("post_comment_pk").eq(f"POST_COMMENT"),
        map_fn=article_comment_from_dynamodb,
    )


def get_popular_active_users(limit: int = BaseQueryDTO.DEFAULT_LIMIT) -> list[User]:
    query_dto = UserQueryDTO(limit=limit)
    return get_popular_users(query_dto)
