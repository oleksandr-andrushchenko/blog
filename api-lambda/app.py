import asyncio

from starlette.exceptions import HTTPException as StarletteHTTPException

from deps import (
    OptCurUserDep,
    ImageFileDTODep,
    CurUserDep,
    ArticleQueryDep,
    ArticleCommentQueryDep,
    ArticleDep,
    ArticleTagQueryDep,
    UserQueryDep,
    UserDep,
    UpdateUserDTODep,
    UpdateUserActivitySettingsDTODep,
    UpdateUserInterestsSettingsDTODep,
    get_error_response,
    UpdateArticleDTODep,
    UpdateArticleStatusDTODep,
    UpdateArticleImpressionDTODep,
    UpdateUserImpressionDTODep,
    UpdateUserStatusDTODep,
    ArticleCommentDep,
    UpdateArticleCommentDTODep,
    ArticleTagDep,
    UpdateArticleTagDTODep,
    ArticleTagSubscriptionDTODep,
)
from utils import (
    to_thread,
    ContactMessageDTO,
    ArticleDTO,
    ArticleQueryDTO,
    ArticleCommentDTO,
    ArticleTag,
    SlugDuplicationError,
    NotAuthorizedError,
    ArticleByOldSlugRequestedError,
    ArticleTagByOldSlugRequestedError,
    UserByOldSlugRequestedError,
    UserNotFoundError,
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
    find_user,
    jinja2_env,
    update_user,
    update_user_activity_settings,
    update_user_interests_settings,
    update_article,
    find_article_impression,
    update_article_impression,
    update_user_impression,
    find_user_impression,
    get_user_url,
    NotAuthenticatedError,
    update_user_status,
    UserBannedError,
    get_allowed_origins,
    find_article,
    create_article_comment,
    get_article_comments,
    update_article_comment,
    get_article_comment_url,
    get_article_tag_url,
    update_article_tag,
    get_user_article_tag_subscriptions,
    create_article_tag_subscription,
    delete_article_tag_subscription,
)
from web import Application, Request, Response, HTTPException, HTMLResponse, JSONResponse, RedirectResponse, \
    RequestValidationError, CORSMiddleware

app = Application()

from api_route_metadata import API_URL_ROUTES


def route(method, name, **kwargs):
    return getattr(app, method)(API_URL_ROUTES[name], name=name, **kwargs)


from web_route_metadata import WEB_URL_ROUTES

for _name, _path in WEB_URL_ROUTES.items():
    app.add_url_route(_path, _name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def redirect_legacy_api_endpoints(request: Request, call_next):
    path = request.url.path
    replacements = (
        ("/api/posts", "/api/articles"),
        ("/api/post-tags", "/api/article-tags"),
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

    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.middleware("http")
async def inject_template_global_vars(request: Request, call_next):
    jinja2_env().globals["request"] = request
    return await call_next(request)


@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


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


@route("post", "upload-public-file", response_class=JSONResponse)
async def upload_public_file(image_file_dto: ImageFileDTODep) -> str:
    from utils import save_public_file

    return save_public_file(image_file_dto)


@route("post", "create-article", response_class=JSONResponse)
async def _create_article(article_dto: ArticleDTO, cur_user: CurUserDep, request: Request) -> str:
    try:
        article = create_article(article_dto, cur_user)
        return get_article_url(request, article)
    except SlugDuplicationError as e:
        raise HTTPException(status_code=409, detail=e.to_dict())


@route("get", "articles-fragment", response_class=HTMLResponse)
async def articles_fragment(query_dto: ArticleQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/articles.html", {
        "articles": get_articles(query_dto, cur_user)
    })


@route("get", "article-comments-fragment", response_class=HTMLResponse)
async def article_comments_fragment(article: ArticleDep, query_dto: ArticleCommentQueryDep) -> str:
    return get_html_content("fragments/article-comments.html", {
        "comments": get_article_comments(article, query_dto)
    })


@route("patch", "update-article", response_class=JSONResponse)
async def _update_article(article: ArticleDep, update_article_dto: UpdateArticleDTODep, cur_user: CurUserDep,
                          request: Request) -> str:
    try:
        update_article(article, update_article_dto, cur_user, request)
        return get_article_url(request, article)
    except SlugDuplicationError as e:
        raise HTTPException(status_code=409, detail=e.to_dict())


@route("post", "update-article-status", response_class=JSONResponse)
async def _update_article_status(article: ArticleDep, update_article_status_dto: UpdateArticleStatusDTODep,
                                 cur_user: CurUserDep, request: Request) -> str:
    update_article_status(article, update_article_status_dto, cur_user, request)
    return get_article_url(request, article)


@route("post", "update-article-impression", response_class=HTMLResponse)
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


@route("post", "create-article-comment", response_class=JSONResponse)
async def _create_article_comment(article: ArticleDep, article_comment_dto: ArticleCommentDTO, cur_user: CurUserDep,
                                  request: Request) -> str:
    article_comment = create_article_comment(article, article_comment_dto, cur_user, request)
    return get_article_comment_url(request, article, article_comment)


@route("patch", "update-article-comment",
       response_class=JSONResponse)
async def _update_article_comment(article: ArticleDep, article_comment: ArticleCommentDep,
                                  update_article_comment_dto: UpdateArticleCommentDTODep, cur_user: CurUserDep,
                                  request: Request) -> str:
    update_article_comment(article, article_comment, update_article_comment_dto, cur_user, request)
    return get_article_comment_url(request, article, article_comment)


@route("post", "create-contact-message", status_code=204)
async def _create_contact_message(message_dto: ContactMessageDTO, cur_user: OptCurUserDep) -> None:
    create_contact_message(message_dto, cur_user)


@route("get", "get-article-tag-subscriptions", response_class=JSONResponse)
async def _get_article_tag_subscriptions(cur_user: CurUserDep):
    return get_user_article_tag_subscriptions(cur_user)


@route("post", "create-article-tag-subscription", response_class=HTMLResponse)
async def _create_article_tag_subscription(dto: ArticleTagSubscriptionDTODep, cur_user: CurUserDep):
    try:
        article_tag_subscription = create_article_tag_subscription(dto, cur_user)
        return get_html_content("fragments/article-tag-subscription.html", {
            "cur_user": cur_user,
            "article_query": ArticleQueryDTO(tags=article_tag_subscription.tags),
            "article_tag_subscription": article_tag_subscription,
        })
    except SlugDuplicationError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict())


@route("delete", "delete-article-tag-subscription",
       response_class=HTMLResponse)
async def _delete_article_tag_subscription(article_tag_subscription_id: str, cur_user: CurUserDep):
    try:
        article_tag_subscription = delete_article_tag_subscription(article_tag_subscription_id, cur_user)
        return get_html_content("fragments/article-tag-subscription.html", {
            "cur_user": cur_user,
            "article_query": ArticleQueryDTO(tags=article_tag_subscription.tags),
            "article_tag_subscription": None,
        })
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@route("patch", "update-article-tag", response_class=JSONResponse)
async def _update_article_tag(update_article_tag_dto: UpdateArticleTagDTODep, article_tag: ArticleTagDep,
                              cur_user: CurUserDep,
                              request: Request) -> str:
    update_article_tag(article_tag, update_article_tag_dto, cur_user, request)
    return get_article_tag_url(request, article_tag)


@route("get", "get-article-tags", response_class=JSONResponse)
async def _get_article_tags(query_dto: ArticleTagQueryDep) -> list[ArticleTag]:
    return get_article_tags(query_dto)


@route("get", "users-fragment", response_class=HTMLResponse)
async def users_fragment(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/users.html", {
        "users": get_users(query_dto, cur_user),
        "cur_user": cur_user,
    })


@route("post", "update-user-status", response_class=JSONResponse)
async def _update_user_status(user: UserDep, update_user_status_dto: UpdateUserStatusDTODep,
                              cur_user: CurUserDep, request: Request) -> str:
    update_user_status(user, update_user_status_dto, cur_user, request)
    return get_user_url(request, user)


@route("post", "update-user-impression", response_class=HTMLResponse)
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


@route("patch", "update-user-activity-settings", response_class=JSONResponse)
async def _update_user_activity_settings(dto: UpdateUserActivitySettingsDTODep, user: UserDep, cur_user: CurUserDep,
                                         request: Request) -> str:
    update_user_activity_settings(user, dto, cur_user)
    return get_user_url(request, user)


@route("patch", "update-user-interests-settings",
       response_class=JSONResponse)
async def _update_user_interests_settings(dto: UpdateUserInterestsSettingsDTODep, user: UserDep, cur_user: CurUserDep,
                                          request: Request) -> str:
    update_user_interests_settings(user, dto, cur_user)
    return get_user_url(request, user)


@route("patch", "update-user", response_class=JSONResponse)
async def _update_user(update_user_dto: UpdateUserDTODep, user: UserDep, cur_user: CurUserDep, request: Request) -> str:
    update_user(user, update_user_dto, cur_user, request)
    return get_user_url(request, user)


@route("get", "user-articles-fragment", response_class=HTMLResponse)
async def user_articles_fragment(user: UserDep, query_dto: ArticleQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/articles.html", {
        "query": query_dto,
        "articles": get_latest_articles_by_user(user, query_dto, cur_user),
        "cur_user": cur_user,
    })


@route("post", "create-dummy-fixtures")
async def _create_dummy_fixtures(request: Request) -> None:
    from utils import create_dummy_fixtures

    return create_dummy_fixtures(request)


@route("post", "generate-sitemap")
async def _generate_sitemap(cur_user: CurUserDep, request: Request) -> dict:
    from utils import generate_sitemap

    urls_count, sitemap_url = generate_sitemap(cur_user, request)
    return {"urls_count": urls_count, "sitemap_url": sitemap_url}


@route("post", "drop-cdn-cache")
async def _drop_cdn_cache(cur_user: CurUserDep) -> dict:
    from utils import drop_cdn_cache

    success, items_count = drop_cdn_cache(cur_user)
    return {"success": success, "items_count": items_count}
