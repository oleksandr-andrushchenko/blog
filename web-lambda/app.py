import asyncio

from starlette.exceptions import HTTPException as StarletteHTTPException

from query_dtos import (
    TagQueryDTO,
)
from shared_deps import (
    OptCurUserDep,
    CurUserDep,
    ArticleQueryDep,
    ArticleDep,
    UserQueryDep,
    UserDep,
    get_error_response,
    UserBySlugDep,
    ArticleBySlugsDep,
    ArticleQueryBySlugsDep,
    UserQueryBySlugsDep,
    set_token_cookie,
    drop_token_cookie,
    TagDep,
    TagQueryDep,
)
from shared_utils import get_tags
from web import Application, Request, HTTPException, HTMLResponse, JSONResponse, RedirectResponse, \
    RequestValidationError, CORSMiddleware, FileResponse
from web_utils import (
    to_thread,
    ArticleQueryDTO,
    ArticleCommentQueryDTO,
    is_prod,
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    NotAuthorizedError,
    ArticleByOldSlugRequestedError,
    TagByOldSlugRequestedError,
    UserByOldSlugRequestedError,
    logger,
    get_html_content,
    get_url,
    get_article_url,
    get_users,
    get_latest_articles_by_user,
    get_articles,
    get_latest_published_articles,
    get_popular_tags,
    get_popular_published_articles,
    find_user,
    jinja2_env,
    get_popular_active_users,
    Permission,
    verify_authorization,
    find_article_impression,
    find_user_impression,
    get_user_url,
    NotAuthenticatedError,
    get_static_files_dir,
    UserStatus,
    UserBannedError,
    utc_now,
    get_allowed_origins,
    get_redirect_url,
    should_show_popular_articles,
    get_article_related_articles,
    get_article_comments,
    get_latest_article_comments,
    get_user_by_auth_token,
    get_tag_url,
    find_tag,
    get_user_activities,
    get_user_tag_subscription_for_tags,
    get_user_tag_subscriptions,
)

app = Application()

from api_route_metadata import API_URL_ROUTES
from web_route_metadata import WEB_URL_ROUTES


def route(method, name, **kwargs):
    return getattr(app, method)(WEB_URL_ROUTES[name], name=name, **kwargs)


# Templates link to API endpoints, but this Lambda does not import or
# register API handlers. Keep only their URL shape/name as metadata.
for _name, _path in API_URL_ROUTES.items():
    app.add_url_route(_path, _name)

if not is_prod():
    import os


    @app.middleware("http")
    async def serve_static(request: Request, call_next):
        path = request.url.path.lstrip("/")
        if "." in path:  # file-like (e.g. robots.txt, sitemap.xml)
            static_dir = get_static_files_dir()
            file_path = os.path.join(static_dir, path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
        return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def redirect_legacy_web_endpoints(request: Request, call_next):
    path = request.url.path
    replacements = (
        ("/post-tags", "/tags"),
        ("/posts-fragment", "/articles-fragment"),
    )
    for old, new in replacements:
        if old in path:
            path = path.replace(old, new, 1)
            url = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(url=url, status_code=308)
    return await call_next(request)


@app.middleware("http")
async def inject_template_global_vars(request: Request, call_next):
    jinja2_env().globals["request"] = request
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.error(f"HTTP exception: {str(exc)}")
    return get_error_response(request, exc.status_code, exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation failed: {str(exc)}")
    details = {}
    for error in exc.errors():
        field = error["loc"][-1] if len(error["loc"]) > 1 else error["loc"][0]
        details[field] = error["msg"]
    return get_error_response(request, 422, details)


@app.exception_handler(NotAuthenticatedError)
async def not_authenticated_error_handler(request: Request, exc: NotAuthenticatedError):
    logger.error(f"Not authenticated: {str(exc)}")
    return get_error_response(request, 401)


@app.exception_handler(UserBannedError)
async def user_banned_error_handler(request: Request, exc: UserBannedError):
    raise NotAuthorizedError("BANNED")


@app.exception_handler(NotAuthorizedError)
async def not_authorized_error_handler(request: Request, exc: NotAuthorizedError):
    logger.error(f"Not authorized: {str(exc)}")
    return get_error_response(request, 403, {"permission": exc.permission})


@app.exception_handler(ArticleByOldSlugRequestedError)
async def article_redirect_exception_handler(request: Request, exc: ArticleByOldSlugRequestedError):
    logger.info(f"Redirect: {str(exc.slug)} -> {exc.article.slug}")
    url = get_article_url(request, exc.article)
    return RedirectResponse(url=url, status_code=301)


@app.exception_handler(UserByOldSlugRequestedError)
async def article_redirect_exception_handler(request: Request, exc: UserByOldSlugRequestedError):
    logger.info(f"Redirect: {str(exc.slug)} -> {exc.user.username}")
    url = get_user_url(request, exc.user)
    return RedirectResponse(url=url, status_code=301)


@app.exception_handler(TagByOldSlugRequestedError)
async def tag_redirect_exception_handler(request: Request, exc: TagByOldSlugRequestedError):
    logger.info(f"Redirect: {str(exc.slug)} -> {exc.tag.slug}")
    if request.url.path.startswith("/tags/"):
        url = get_url(request, "edit-tag", slug=exc.tag.slug)
    else:
        url = get_tag_url(request, exc.tag)
    return RedirectResponse(url=url, status_code=301)


@route("get", "index", response_class=HTMLResponse)
async def index(cur_user: OptCurUserDep) -> str:
    latest_articles_query = ArticleQueryDTO()
    latest_article_comments_query = ArticleCommentQueryDTO(limit=5)
    (
        popular_tags,
        latest_articles,
        popular_articles,
        latest_article_comments,
        popular_users,
    ) = await asyncio.gather(
        to_thread(get_popular_tags, TagQueryDTO(limit=40)),
        to_thread(get_latest_published_articles, limit=latest_articles_query.limit),
        to_thread(get_popular_published_articles, limit=5),
        to_thread(get_latest_article_comments, limit=latest_article_comments_query.limit),
        to_thread(get_popular_active_users, limit=5),
    )
    return get_html_content("index.html", {
        "cur_user": cur_user,
        "featured_tags": popular_tags[:20],
        "popular_tags": popular_tags[20:],
        "latest_articles_query": latest_articles_query,
        "latest_articles": latest_articles,
        "popular_articles": popular_articles,
        "show_popular_articles": should_show_popular_articles(latest_articles, popular_articles),
        "latest_article_comments": latest_article_comments,
        "popular_users": popular_users,
    })


async def _article_page(article: ArticleDep, cur_user: OptCurUserDep) -> HTMLResponse:
    (
        author,
        article_impression,
        related_articles,
        comments,
    ) = await asyncio.gather(
        to_thread(find_user, article.user_id),
        to_thread(find_article_impression, article, cur_user) if cur_user else asyncio.sleep(0, result=None),
        to_thread(get_article_related_articles, article),
        to_thread(get_article_comments, article),
    )

    html_content = get_html_content("article.html", {
        "cur_user": cur_user,
        "article": article,
        "author": author,
        "article_impression": article_impression,
        "related_articles": related_articles,
        "comments": comments,
        "comments_query": ArticleCommentQueryDTO()
    })
    return HTMLResponse(html_content)


async def _articles_page(query_dto: ArticleQueryDep, cur_user: OptCurUserDep) -> HTMLResponse:
    tag_slug = query_dto.tags[0] if query_dto.tags and len(query_dto.tags) == 1 else None
    (
        articles,
        tag,
        article_query_tags,
    ) = await asyncio.gather(
        to_thread(get_articles, query_dto, cur_user),
        to_thread(find_tag, tag_slug) if tag_slug else asyncio.sleep(0, result=None),
        asyncio.gather(*(to_thread(find_tag, tag) for tag in query_dto.tags)),
    )
    if tag and tag_slug and tag.slug != tag_slug:
        raise TagByOldSlugRequestedError(tag_slug, tag)

    article_query_tag_names = [tag.name if tag else slug for tag, slug in zip(article_query_tags, query_dto.tags)]
    article_query_tag_items = [
        {"value": slug, "name": name}
        for slug, name in zip(query_dto.tags, article_query_tag_names)
    ]
    return get_html_content("articles.html", {
        "cur_user": cur_user,
        "article_query": query_dto,
        "article_query_tag_names": article_query_tag_names,
        "article_query_tag_items": article_query_tag_items,
        "articles": articles,
        "tag": tag,
        "tag_subscription": get_user_tag_subscription_for_tags(cur_user,
                                                               query_dto.tags) if cur_user and query_dto.tags else None,
    })


@route("get", "new-article", response_class=HTMLResponse)
async def new_article(cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.CREATE_ARTICLE)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("new-article.html", {
        "cur_user": cur_user
    })


@route("get", "articles", response_class=HTMLResponse)
async def articles_page(query_dto: ArticleQueryDep, cur_user: OptCurUserDep):
    return await _articles_page(query_dto, cur_user)


@route("get", "tags", response_class=HTMLResponse)
async def tags_page(query_dto: TagQueryDep, cur_user: OptCurUserDep) -> str:
    tags = get_tags(query_dto)
    return get_html_content("tags.html", {
        "cur_user": cur_user,
        "tags": tags,
        "tags_query": query_dto,
    })


@route("get", "article")
async def article_page(article: ArticleDep, cur_user: OptCurUserDep):
    return await _article_page(article, cur_user)


@route("get", "edit-article", response_class=HTMLResponse)
async def edit_article(article: ArticleDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, article)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("edit-article.html", {
        "cur_user": cur_user,
        "article": article
    })


@route("get", "articles-by-slugs", response_class=HTMLResponse)
async def articles_page_by_slugs(query_dto: ArticleQueryBySlugsDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return await _articles_page(query_dto, cur_user)


def _legacy_articles_redirect(request: Request) -> RedirectResponse:
    path = request.url.path
    if path == "/posts" or path.startswith("/posts/"):
        path = "/articles" + path[len("/posts"):]
    elif path == "/post" or path.startswith("/post/"):
        path = "/articles" + path[len("/post"):]
    elif path.endswith("/posts"):
        path = path[:-len("/posts")] + "/articles"
    url = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(url=url, status_code=301)


@route("get", "legacy-posts")
@route("get", "legacy-singular-articles")
@route("get", "legacy-new-article")
@route("get", "legacy-singular-new-article")
@route("get", "legacy-article")
@route("get", "legacy-singular-article")
@route("get", "legacy-edit-article")
@route("get", "legacy-singular-edit-article")
@route("get", "legacy-posts-by-slugs")
async def legacy_articles_redirect(request: Request) -> RedirectResponse:
    return _legacy_articles_redirect(request)


@route("get", "contacts", response_class=HTMLResponse)
async def contacts(cur_user: OptCurUserDep) -> str:
    return get_html_content("contacts.html", {
        "cur_user": cur_user
    })


@route("get", "edit-tag", response_class=HTMLResponse)
async def edit_tag(tag: TagDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_TAG, tag)
    return get_html_content("edit-tag.html", {
        "cur_user": cur_user,
        "tag": tag,
    })


def _users_page(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return get_html_content("users.html", {
        "cur_user": cur_user,
        "user_query": query_dto,
        "users": get_users(query_dto, cur_user)
    })


@route("get", "users", response_class=HTMLResponse)
async def users_page(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> str:
    return _users_page(query_dto, cur_user)


@route("get", "users-by-slugs", response_class=HTMLResponse)
async def users_page_by_slugs(query_dto: UserQueryBySlugsDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return _users_page(query_dto, cur_user)


async def _user_page(user: UserDep, articles_query_dto: ArticleQueryDep, cur_user: OptCurUserDep,
                     request: Request) -> HTMLResponse:
    activities_year = request.query_params.get("activities_year")
    activities_year = int(activities_year) if activities_year else None

    async def get_activities():
        if user.published_articles_count == 0 and user.article_comments_count == 0:
            return []
        return await to_thread(get_user_activities, user, activities_year)

    async def get_interests():
        if user.tag_subscriptions_count == 0:
            return []
        return await to_thread(get_user_tag_subscriptions, user)

    (
        articles,
        user_impression,
        activities,
        tag_subscriptions,
    ) = await asyncio.gather(
        to_thread(get_latest_articles_by_user, user, articles_query_dto, cur_user),
        to_thread(find_user_impression, user, cur_user) if cur_user else asyncio.sleep(0, result=None),
        get_activities(),
        get_interests(),
    )

    html_content = get_html_content("user.html", {
        "cur_user": cur_user,
        "user": user,
        "article_query": articles_query_dto,
        "articles": articles,
        "user_impression": user_impression,
        "activities": activities,
        "activities_year": activities_year,
        "tag_subscriptions": tag_subscriptions,
    })
    return HTMLResponse(html_content)


@route("get", "user")
async def user_page(user: UserDep, articles_query_dto: ArticleQueryDep, cur_user: OptCurUserDep, request: Request):
    return await _user_page(user, articles_query_dto, cur_user, request)


@route("get", "edit-user", response_class=HTMLResponse)
async def edit_user(user: UserDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("edit-user.html", {
        "cur_user": cur_user,
        "user": user
    })


@route("get", "login", response_class=RedirectResponse)
async def login(request: Request) -> RedirectResponse:
    from web_utils import get_login_redirect_url

    redirect_url = get_redirect_url(request)
    callback_url = get_url(request, 'login-callback', absolute=True)
    provider_redirect_url = get_login_redirect_url(callback_url)
    response = RedirectResponse(provider_redirect_url)
    response.set_cookie("redirect_url", redirect_url, httponly=True, secure=True)
    return response


@route("get", "login-callback", response_class=RedirectResponse)
async def login_callback(request: Request) -> RedirectResponse:
    from web_utils import create_auth_jwt_token, get_user_token_by_code

    try:
        redirect_url = request.cookies.get("redirect_url") or get_url(request, "index")
        callback_url = get_url(request, 'login-callback', absolute=True)

        cognito_user_token = get_user_token_by_code(
            code=request.query_params.get("code"),
            callback_url=callback_url
        )
        response = RedirectResponse(redirect_url, 302)
        token = create_auth_jwt_token(cognito_user_token)
        set_token_cookie(token, response)
        user = get_user_by_auth_token(token)
        return response
    except (InvalidCodeError, CodeExchangeFailedError, InvalidTokenError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@route("get", "logout", response_class=RedirectResponse)
async def logout(request: Request) -> RedirectResponse:
    from web_utils import get_logout_redirect_url

    redirect_url = get_redirect_url(request)
    callback_url = get_url(request, 'logout-callback', absolute=True)
    provider_redirect_url = get_logout_redirect_url(callback_url)
    response = RedirectResponse(provider_redirect_url)
    response.set_cookie("redirect_url", redirect_url, httponly=True, secure=True)
    drop_token_cookie(response)
    request.state.cur_user = None
    return response


@route("get", "logout-callback", response_class=RedirectResponse)
async def logout_callback(request: Request):
    response = RedirectResponse(request.cookies.get("redirect_url") or get_url(request, "index"))

    drop_token_cookie(response)

    response.headers["Cache-Control"] = "no-store"

    return response


@route("get", "policy", response_class=HTMLResponse)
async def policy(cur_user: OptCurUserDep) -> str:
    return get_html_content("policy.html", {
        "cur_user": cur_user,
        "utc_now": utc_now(),
    })


@route("get", "rules", response_class=HTMLResponse)
async def rules(cur_user: OptCurUserDep) -> str:
    return get_html_content("rules.html", {
        "cur_user": cur_user,
        "utc_now": utc_now(),
    })


@route("get", "terms", response_class=HTMLResponse)
async def terms(cur_user: OptCurUserDep) -> str:
    return get_html_content("terms.html", {
        "cur_user": cur_user,
        "utc_now": utc_now(),
    })


@route("get", "earn", response_class=HTMLResponse)
async def contribute(cur_user: OptCurUserDep) -> str:
    return get_html_content("earn.html", {
        "cur_user": cur_user,
    })


@route("get", "utils", response_class=HTMLResponse)
async def utils(cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UTILS)
    return get_html_content("utils.html", {
        "cur_user": cur_user,
    })


@route("get", "user-by-slug", response_class=HTMLResponse)
async def user_page_by_slug(user: UserBySlugDep, articles_query_dto: ArticleQueryDep,
                            cur_user: OptCurUserDep, request: Request) -> HTMLResponse:
    return await _user_page(user, articles_query_dto, cur_user, request)


@route("get", "article-by-slugs", response_class=HTMLResponse)
async def article_page_by_slugs(article: ArticleBySlugsDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return await _article_page(article, cur_user)
