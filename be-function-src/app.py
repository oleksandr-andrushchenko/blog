from web import Application, Request, Response, HTTPException, HTMLResponse, JSONResponse, RedirectResponse,\
    RequestValidationError, CORSMiddleware, FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from lambda_adapter import make_handler
from utils import (
    to_thread,
    ContactMessageDTO,
    ArticleDTO,
    ArticleQueryDTO,
    ArticleCommentDTO,
    ArticleCommentQueryDTO,
    ArticleTag,
    is_prod,
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    SlugDuplicationError,
    NotAuthorizedError,
    ArticleByOldSlugRequestedError,
    ArticleTagByOldSlugRequestedError,
    UserByOldSlugRequestedError,
    logger,
    get_html_content,
    get_url,
    get_article_url,
    create_article,
    create_contact_message,
    get_article_tags,
    update_article_status,
    get_users,
    get_latest_articles_by_user,
    get_articles,
    get_latest_published_articles,
    get_popular_article_tags,
    get_popular_published_articles,
    find_user,
    jinja2_env,
    get_popular_active_users,
    Permission,
    check_authorization,
    verify_authorization,
    update_user,
    update_user_activity_settings,
    update_article,
    find_article_impression,
    update_article_impression,
    update_user_impression,
    find_user_impression,
    get_user_url,
    NotAuthenticatedError,
    update_user_status,
    get_static_files_dir,
    UserStatus,
    UserBannedError,
    utc_now,
    get_allowed_origins,
    get_redirect_url,
    should_show_popular_articles,
    get_article_related_articles,
    find_article,
    create_article_comment,
    get_article_comments,
    get_latest_article_comments,
    update_article_comment,
    get_article_comment_url,
    get_user_by_auth_token,
    get_cdn_cache_version,
    get_article_tag_url,
    update_article_tag,
    find_article_tag,
    get_user_activity,
)
from deps import (
    OptCurUserDep,
    ImageFileDTODep,
    CurUserDep,
    ArticleQueryDep,
    ArticleDep,
    ArticleTagQueryDep,
    UserQueryDep,
    UserDep,
    UpdateUserDTODep,
    UpdateUserActivitySettingsDTODep,
    get_error_response,
    UpdateArticleDTODep,
    UpdateArticleStatusDTODep,
    UpdateArticleImpressionDTODep,
    UpdateUserImpressionDTODep,
    UserBySlugDep,
    ArticleBySlugsDep,
    UpdateUserStatusDTODep,
    ArticleCommentDep,
    UpdateArticleCommentDTODep,
    ArticleQueryBySlugsDep,
    UserQueryBySlugsDep,
    set_token_cookie,
    drop_token_cookie,
    set_cdn_cache_cookie,
    drop_cdn_cache_cookie,
    get_cdn_cache_cookie,
    ArticleTagDep,
    UpdateArticleTagDTODep,
)
import asyncio

app = Application()

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
async def redirect_legacy_article_endpoints(request: Request, call_next):
    path = request.url.path
    replacements = (
        ("/api/posts", "/api/articles"),
        ("/api/post-tags", "/api/article-tags"),
        ("/post-tags", "/article-tags"),
        ("/posts-fragment", "/articles-fragment"),
    )
    for old, new in replacements:
        if old in path:
            path = path.replace(old, new, 1)
            url = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(url=url, status_code=308)
    return await call_next(request)


@app.middleware("http")
async def add_no_robots_to_api(request: Request, call_next):
    response = await call_next(request)

    if request.url.path.startswith("/api/"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"

    return response


@app.middleware("http")
async def inject_template_global_vars(request: Request, call_next):
    jinja2_env().globals["request"] = request
    return await call_next(request)


@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)

    if "Cache-Control" in response.headers:
        return response

    if request.method not in ("GET", "HEAD"):
        response.headers["Cache-Control"] = "no-store"
        return response

    path = request.url.path

    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        return response

    cur_user = getattr(request.state, "cur_user", None)
    if cur_user:
        response.headers["Cache-Control"] = "private, no-store"
        return response

    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def sync_cdn_cache_cookie_middleware(request: Request, call_next):
    response = await call_next(request)

    set_cookie_headers = response.headers.getlist("set-cookie")
    token = request.cookies.get("token")
    token_was_set = None
    token_was_deleted = None

    for header in set_cookie_headers:
        if "token=" in header:
            token_was_set = True
        if "token=;" in header or "Max-Age=0" in header:
            token_was_deleted = True

    if token and not token_was_set and not token_was_deleted:
        cur_user = getattr(request.state, "cur_user", None)
        if cur_user:
            req_version = get_cdn_cache_cookie(request)
            cdn_version = get_cdn_cache_version(cur_user)

            if req_version != cdn_version:
                set_cdn_cache_cookie(cur_user, response)

    return response


@app.get("/", name="index", response_class=HTMLResponse)
async def index(cur_user: OptCurUserDep) -> str:
    latest_articles_query = ArticleQueryDTO()
    latest_article_comments_query = ArticleCommentQueryDTO(limit=5)
    (
        popular_article_tags,
        latest_articles,
        popular_articles,
        latest_article_comments,
        popular_users,
    ) = await asyncio.gather(
        to_thread(get_popular_article_tags),
        to_thread(get_latest_published_articles, limit=latest_articles_query.limit),
        to_thread(get_popular_published_articles, limit=5),
        to_thread(get_latest_article_comments, limit=latest_article_comments_query.limit),
        to_thread(get_popular_active_users, limit=5),
    )
    return get_html_content("index.html", {
        "cur_user": cur_user,
        "popular_topic_article_tags": popular_article_tags[:8],
        "popular_article_tags": popular_article_tags[8:],
        "latest_articles_query": latest_articles_query,
        "latest_articles": latest_articles,
        "popular_articles": popular_articles,
        "show_popular_articles": should_show_popular_articles(latest_articles, popular_articles),
        "latest_article_comments": latest_article_comments,
        "popular_users": popular_users,
    })


@app.post("/api/public-file", name="upload-public-file", response_class=JSONResponse)
async def upload_public_file(image_file_dto: ImageFileDTODep) -> str:
    from utils import save_public_file

    return save_public_file(image_file_dto)


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
    article_tag_slug = query_dto.tags[0] if query_dto.tags and len(query_dto.tags) == 1 else None
    (
        articles,
        article_tag,
        article_query_tags,
    ) = await asyncio.gather(
        to_thread(get_articles, query_dto, cur_user),
        to_thread(find_article_tag, article_tag_slug) if article_tag_slug else asyncio.sleep(0, result=None),
        asyncio.gather(*(to_thread(find_article_tag, tag) for tag in query_dto.tags)),
    )
    if article_tag and article_tag_slug and article_tag.slug != article_tag_slug:
        raise ArticleTagByOldSlugRequestedError(article_tag_slug, article_tag)

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
        "article_tag": article_tag,
    })


@app.get("/articles/new", name="new-article", response_class=HTMLResponse)
async def new_article(cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.CREATE_ARTICLE)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("new-article.html", {
        "cur_user": cur_user
    })


@app.post("/api/articles", name="create-article", response_class=JSONResponse)
async def _create_article(article_dto: ArticleDTO, cur_user: CurUserDep, request: Request) -> str:
    try:
        article = create_article(article_dto, cur_user)
        return get_article_url(request, article)
    except SlugDuplicationError as e:
        raise HTTPException(status_code=409, detail=e.to_dict())


@app.get("/articles", name="articles", response_class=HTMLResponse)
async def articles_page(query_dto: ArticleQueryDep, cur_user: OptCurUserDep):
    return await _articles_page(query_dto, cur_user)


@app.get("/api/articles-fragment", name="articles-fragment", response_class=HTMLResponse)
async def articles_fragment(query_dto: ArticleQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/articles.html", {
        "articles": get_articles(query_dto, cur_user)
    })


@app.get("/articles/{article_id}", name="article")
async def article_page(article: ArticleDep, cur_user: OptCurUserDep):
    return await _article_page(article, cur_user)


@app.get("/articles/{article_id}/edit", name="edit-article", response_class=HTMLResponse)
async def edit_article(article: ArticleDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, article)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("edit-article.html", {
        "cur_user": cur_user,
        "article": article
    })


@app.patch("/api/articles/{article_id}", name="update-article", response_class=JSONResponse)
async def _update_article(article: ArticleDep, update_article_dto: UpdateArticleDTODep, cur_user: CurUserDep, request: Request) -> str:
    try:
        update_article(article, update_article_dto, cur_user, request)
        return get_article_url(request, article)
    except SlugDuplicationError as e:
        raise HTTPException(status_code=409, detail=e.to_dict())


@app.post("/api/articles/{article_id}/status", name="update-article-status", response_class=JSONResponse)
async def _update_article_status(article: ArticleDep, update_article_status_dto: UpdateArticleStatusDTODep,
                              cur_user: CurUserDep, request: Request) -> str:
    update_article_status(article, update_article_status_dto, cur_user, request)
    return get_article_url(request, article)


@app.post("/api/articles/{article_id}/impression", name="update-article-impression", response_class=HTMLResponse)
async def _update_article_impression(article: ArticleDep, update_article_impression_dto: UpdateArticleImpressionDTODep,
                                  cur_user: CurUserDep, request: Request) -> str:
    update_article_impression(article, update_article_impression_dto, cur_user, request)
    (
        article,
        article_impression,
    ) = await asyncio.gather(
        to_thread(find_article, article.id),
        to_thread(find_article_impression, article, cur_user),
    )
    return get_html_content("fragments/article-impressions.html", {
        "article": article,
        "article_impression": article_impression,
        "cur_user": cur_user,
    })


@app.post("/api/articles/{article_id}/comment", name="create-article-comment", response_class=JSONResponse)
async def _create_article_comment(article: ArticleDep, article_comment_dto: ArticleCommentDTO, cur_user: CurUserDep,
                               request: Request) -> str:
    article_comment = create_article_comment(article, article_comment_dto, cur_user, request)
    return get_article_comment_url(request, article, article_comment)


@app.patch("/api/articles/{article_id}/comments/{comment_id}", name="update-article-comment", response_class=JSONResponse)
async def _update_article_comment(article: ArticleDep, article_comment: ArticleCommentDep,
                               update_article_comment_dto: UpdateArticleCommentDTODep, cur_user: CurUserDep,
                               request: Request) -> str:
    update_article_comment(article, article_comment, update_article_comment_dto, cur_user, request)
    return get_article_comment_url(request, article, article_comment)


@app.get("/{slugs_path:path}/articles", name="articles-by-slugs", response_class=HTMLResponse)
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


@app.get("/posts", name="legacy-posts")
@app.get("/post", name="legacy-singular-articles")
@app.get("/posts/new", name="legacy-new-article")
@app.get("/post/new", name="legacy-singular-new-article")
@app.get("/posts/{article_id}", name="legacy-article")
@app.get("/post/{article_id}", name="legacy-singular-article")
@app.get("/posts/{article_id}/edit", name="legacy-edit-article")
@app.get("/post/{article_id}/edit", name="legacy-singular-edit-article")
@app.get("/{slugs_path:path}/posts", name="legacy-posts-by-slugs")
async def legacy_articles_redirect(request: Request) -> RedirectResponse:
    return _legacy_articles_redirect(request)


@app.get("/contacts", name="contacts", response_class=HTMLResponse)
async def contacts(cur_user: OptCurUserDep) -> str:
    return get_html_content("contacts.html", {
        "cur_user": cur_user
    })


@app.post("/api/contacts/message", name="create-contact-message", status_code=204)
async def _create_contact_message(message_dto: ContactMessageDTO, cur_user: OptCurUserDep) -> None:
    create_contact_message(message_dto, cur_user)


@app.get("/article-tags/{slug}/edit", name="edit-article-tag", response_class=HTMLResponse)
async def edit_article_tag(article_tag: ArticleTagDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_TAG, article_tag)
    return get_html_content("edit-article-tag.html", {
        "cur_user": cur_user,
        "article_tag": article_tag,
    })


@app.patch("/api/article-tags/{slug}", name="update-article-tag", response_class=JSONResponse)
async def _update_article_tag(update_article_tag_dto: UpdateArticleTagDTODep, article_tag: ArticleTagDep, cur_user: CurUserDep,
                           request: Request) -> str:
    update_article_tag(article_tag, update_article_tag_dto, cur_user, request)
    return get_article_tag_url(request, article_tag)


@app.get("/api/article-tags", name="get-article-tags", response_class=JSONResponse)
async def _get_article_tags(query_dto: ArticleTagQueryDep) -> list[ArticleTag]:
    return get_article_tags(query_dto)


def _users_page(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return get_html_content("users.html", {
        "cur_user": cur_user,
        "user_query": query_dto,
        "users": get_users(query_dto, cur_user)
    })


@app.get("/users", name="users", response_class=HTMLResponse)
async def users_page(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> str:
    return _users_page(query_dto, cur_user)


@app.get("/{type}/users", name="users-by-slugs", response_class=HTMLResponse)
async def users_page_by_slugs(query_dto: UserQueryBySlugsDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return _users_page(query_dto, cur_user)


@app.get("/api/users-fragment", name="users-fragment", response_class=HTMLResponse)
async def users_fragment(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/users.html", {
        "users": get_users(query_dto, cur_user),
        "cur_user": cur_user,
    })


async def _user_page(user: UserDep, articles_query_dto: ArticleQueryDep, cur_user: OptCurUserDep, request: Request) -> HTMLResponse:
    activity_year = request.query_params.get("activity_year")
    try:
        activity_year = int(activity_year) if activity_year else None
    except ValueError:
        activity_year = None

    can_manage_activity = bool(cur_user and check_authorization(cur_user, Permission.UPDATE_USER, user))
    should_load_activity = user.show_activity_calendar or can_manage_activity

    async def load_activity():
        if not should_load_activity:
            return None
        try:
            return await to_thread(get_user_activity, user, activity_year)
        except ValueError:
            return await to_thread(get_user_activity, user, None)

    (
        articles,
        user_impression,
        activity,
    ) = await asyncio.gather(
        to_thread(get_latest_articles_by_user, user, articles_query_dto, cur_user),
        to_thread(find_user_impression, user, cur_user) if cur_user else asyncio.sleep(0, result=None),
        load_activity(),
    )

    html_content = get_html_content("user.html", {
        "cur_user": cur_user,
        "user": user,
        "article_query": articles_query_dto,
        "articles": articles,
        "user_impression": user_impression,
        "activity": activity,
        "activity_year": activity_year,
    })
    return HTMLResponse(html_content)


@app.get("/users/{user_id}", name="user")
async def user_page(user: UserDep, articles_query_dto: ArticleQueryDep, cur_user: OptCurUserDep, request: Request):
    return await _user_page(user, articles_query_dto, cur_user, request)


@app.post("/api/users/{user_id}/status", name="update-user-status", response_class=JSONResponse)
async def _update_user_status(user: UserDep, update_user_status_dto: UpdateUserStatusDTODep,
                              cur_user: CurUserDep, request: Request) -> str:
    update_user_status(user, update_user_status_dto, cur_user, request)
    return get_user_url(request, user)


@app.post("/api/users/{user_id}/impression", name="update-user-impression", response_class=HTMLResponse)
async def _update_user_impression(user: UserDep, update_user_impression_dto: UpdateUserImpressionDTODep,
                                  cur_user: CurUserDep, request: Request) -> str:
    update_user_impression(user, update_user_impression_dto, cur_user, request)
    (
        user,
        user_impression,
    ) = await asyncio.gather(
        to_thread(find_user, user.id),
        to_thread(find_user_impression, user, cur_user),
    )
    return get_html_content("fragments/user-impressions.html", {
        "user": user,
        "user_impression": user_impression,
        "cur_user": cur_user,
    })


@app.get("/users/{user_id}/edit", name="edit-user", response_class=HTMLResponse)
async def edit_user(user: UserDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("edit-user.html", {
        "cur_user": cur_user,
        "user": user
    })


@app.patch("/api/users/{user_id}/activity-settings", name="update-user-activity-settings", response_class=JSONResponse)
async def _update_user_activity_settings(dto: UpdateUserActivitySettingsDTODep, user: UserDep, cur_user: CurUserDep, request: Request) -> str:
    update_user_activity_settings(user, dto, cur_user)
    return get_user_url(request, user)


@app.patch("/api/users/{user_id}", name="update-user", response_class=JSONResponse)
async def _update_user(update_user_dto: UpdateUserDTODep, user: UserDep, cur_user: CurUserDep, request: Request) -> str:
    update_user(user, update_user_dto, cur_user, request)
    return get_user_url(request, user)


@app.get("/api/users/{user_id}/articles-fragment", name="user-articles-fragment", response_class=HTMLResponse)
async def user_articles_fragment(user: UserDep, query_dto: ArticleQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/articles.html", {
        "query": query_dto,
        "articles": get_latest_articles_by_user(user, query_dto, cur_user),
        "cur_user": cur_user,
    })


@app.get("/login", name="login", response_class=RedirectResponse)
async def login(request: Request) -> RedirectResponse:
    from utils import get_login_redirect_url

    redirect_url = get_redirect_url(request)
    callback_url = get_url(request, 'login-callback', full=True)
    provider_redirect_url = get_login_redirect_url(callback_url)
    response = RedirectResponse(provider_redirect_url)
    response.set_cookie("redirect_url", redirect_url, httponly=True, secure=True)
    return response


@app.get("/login-callback", name="login-callback", response_class=RedirectResponse)
async def login_callback(request: Request) -> RedirectResponse:
    from utils import create_auth_jwt_token, get_user_token_by_code

    try:
        redirect_url = request.cookies.get("redirect_url") or get_url(request, "index")
        callback_url = get_url(request, 'login-callback', full=True)

        cognito_user_token = get_user_token_by_code(
            code=request.query_params.get("code"),
            callback_url=callback_url
        )
        response = RedirectResponse(redirect_url, 302)
        token = create_auth_jwt_token(cognito_user_token)
        set_token_cookie(token, response)
        user = get_user_by_auth_token(token)
        set_cdn_cache_cookie(user, response)
        return response
    except (InvalidCodeError, CodeExchangeFailedError, InvalidTokenError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/logout", name="logout", response_class=RedirectResponse)
async def logout(request: Request) -> RedirectResponse:
    from utils import get_logout_redirect_url

    redirect_url = get_redirect_url(request)
    callback_url = get_url(request, 'logout-callback', full=True)
    provider_redirect_url = get_logout_redirect_url(callback_url)
    response = RedirectResponse(provider_redirect_url)
    response.set_cookie("redirect_url", redirect_url, httponly=True, secure=True)
    drop_token_cookie(response)
    drop_cdn_cache_cookie(response)
    request.state.cur_user = None
    return response


@app.get("/logout-callback", name="logout-callback", response_class=RedirectResponse)
async def logout_callback(request: Request):
    response = RedirectResponse(request.cookies.get("redirect_url") or get_url(request, "index"))

    drop_token_cookie(response)
    drop_cdn_cache_cookie(response)

    response.headers["Cache-Control"] = "no-store"

    return response


@app.post("/api/dummy-fixtures", name="create-dummy-fixtures")
async def _create_dummy_fixtures(request: Request) -> None:
    from utils import create_dummy_fixtures

    return create_dummy_fixtures(request)


@app.get("/privacy-policy", name="policy", response_class=HTMLResponse)
async def policy(cur_user: OptCurUserDep) -> str:
    return get_html_content("policy.html", {
        "cur_user": cur_user,
        "utc_now": utc_now(),
    })


@app.get("/rules", name="rules", response_class=HTMLResponse)
async def rules(cur_user: OptCurUserDep) -> str:
    return get_html_content("rules.html", {
        "cur_user": cur_user,
        "utc_now": utc_now(),
    })


@app.get("/terms-of-service", name="terms", response_class=HTMLResponse)
async def terms(cur_user: OptCurUserDep) -> str:
    return get_html_content("terms.html", {
        "cur_user": cur_user,
        "utc_now": utc_now(),
    })


@app.get("/earn-with-us", name="earn", response_class=HTMLResponse)
async def contribute(cur_user: OptCurUserDep) -> str:
    return get_html_content("earn.html", {
        "cur_user": cur_user,
    })


@app.get("/utils", name="utils", response_class=HTMLResponse)
async def utils(cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UTILS)
    return get_html_content("utils.html", {
        "cur_user": cur_user,
    })


@app.post("/api/generate-sitemap", name="generate-sitemap")
async def _generate_sitemap(cur_user: CurUserDep, request: Request) -> dict:
    from utils import generate_sitemap

    urls_count, sitemap_url = generate_sitemap(cur_user, request)
    return {"urls_count": urls_count, "sitemap_url": sitemap_url}


@app.post("/api/drop-cdn-cache", name="drop-cdn-cache")
async def _drop_cdn_cache(cur_user: CurUserDep) -> dict:
    from utils import drop_cdn_cache

    success, items_count = drop_cdn_cache(cur_user)
    return {"success": success, "items_count": items_count}


@app.get("/{slug}", name="user-by-slug", response_class=HTMLResponse)
async def user_page_by_slug(user: UserBySlugDep, articles_query_dto: ArticleQueryDep,
                            cur_user: OptCurUserDep, request: Request) -> HTMLResponse:
    return await _user_page(user, articles_query_dto, cur_user, request)


@app.get("/{user_slug}/{article_slug}", name="article-by-slugs", response_class=HTMLResponse)
async def article_page_by_slugs(article: ArticleBySlugsDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return await _article_page(article, cur_user)


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


@app.exception_handler(ArticleTagByOldSlugRequestedError)
async def article_tag_redirect_exception_handler(request: Request, exc: ArticleTagByOldSlugRequestedError):
    logger.info(f"Redirect: {str(exc.slug)} -> {exc.article_tag.slug}")
    if request.url.path.startswith("/article-tags/"):
        url = get_url(request, "edit-article-tag", slug=exc.article_tag.slug)
    else:
        url = get_article_tag_url(request, exc.article_tag)
    return RedirectResponse(url=url, status_code=301)


handler = make_handler(app)
