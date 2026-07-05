from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from mangum import Mangum
from utils import (
    to_thread,
    ContactMessageDTO,
    PostDTO,
    PostQueryDTO,
    PostCommentDTO,
    PostCommentQueryDTO,
    PostTag,
    is_prod,
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    SlugDuplicationError,
    NotAuthorizedError,
    PostByOldSlugRequestedError,
    PostTagByOldSlugRequestedError,
    UserByOldSlugRequestedError,
    logger,
    get_html_content,
    get_url,
    get_post_url,
    create_post,
    create_contact_message,
    get_post_tags,
    update_post_status,
    get_users,
    get_latest_posts_by_user,
    get_posts,
    get_latest_published_posts,
    get_popular_post_tags,
    get_popular_published_posts,
    find_user,
    jinja2_env,
    get_popular_active_users,
    Permission,
    verify_authorization,
    update_user,
    update_post,
    find_post_impression,
    update_post_impression,
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
    should_show_popular_posts,
    get_post_related_posts,
    find_post,
    create_post_comment,
    get_post_comments,
    get_latest_post_comments,
    update_post_comment,
    get_post_comment_url,
    get_user_by_auth_token,
    get_cdn_cache_version,
    get_post_tag_url,
    update_post_tag,
    find_post_tag,
)
from deps import (
    OptCurUserDep,
    ImageFileDTODep,
    CurUserDep,
    PostQueryDep,
    PostDep,
    PostTagQueryDep,
    UserQueryDep,
    UserDep,
    UpdateUserDTODep,
    get_error_response,
    UpdatePostDTODep,
    UpdatePostStatusDTODep,
    UpdatePostImpressionDTODep,
    UpdateUserImpressionDTODep,
    UserBySlugDep,
    PostBySlugsDep,
    UpdateUserStatusDTODep,
    PostCommentDep,
    UpdatePostCommentDTODep,
    PostQueryBySlugsDep,
    UserQueryBySlugsDep,
    set_token_cookie,
    drop_token_cookie,
    set_cdn_cache_cookie,
    drop_cdn_cache_cookie,
    get_cdn_cache_cookie,
    PostTagDep,
    UpdatePostTagDTODep,
)
import asyncio

app = FastAPI(
    docs_url=None if is_prod() else "/docs",
    redoc_url=None if is_prod() else "/redoc",
    openapi_url=None if is_prod() else "/openapi.json",
)

if not is_prod():
    import os


    @app.middleware("http")
    async def serve_static(request: Request, call_next):
        path = request.url.path.lstrip("/")
        if "." in path:  # file-like (e.g. robots.txt, sitemap.xml)
            static_dir = get_static_files_dir()
            file_path = os.path.join(static_dir, path)
            if os.path.isfile(file_path):
                from fastapi.responses import FileResponse

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
    latest_posts_query = PostQueryDTO()
    latest_post_comments_query = PostCommentQueryDTO(limit=5)
    (
        popular_post_tags,
        latest_posts,
        popular_posts,
        latest_post_comments,
        popular_users,
    ) = await asyncio.gather(
        to_thread(get_popular_post_tags),
        to_thread(get_latest_published_posts, limit=latest_posts_query.limit),
        to_thread(get_popular_published_posts, limit=5),
        to_thread(get_latest_post_comments, limit=latest_post_comments_query.limit),
        to_thread(get_popular_active_users, limit=5),
    )
    return get_html_content("index.html", {
        "cur_user": cur_user,
        "popular_topic_post_tags": popular_post_tags[:8],
        "popular_post_tags": popular_post_tags[8:],
        "latest_posts_query": latest_posts_query,
        "latest_posts": latest_posts,
        "popular_posts": popular_posts,
        "show_popular_posts": should_show_popular_posts(latest_posts, popular_posts),
        "latest_post_comments": latest_post_comments,
        "popular_users": popular_users,
    })


@app.post("/api/public-file", name="upload-public-file", response_class=JSONResponse)
async def upload_public_file(image_file_dto: ImageFileDTODep) -> str:
    from utils import save_public_file

    return save_public_file(image_file_dto)


async def _post_page(post: PostDep, cur_user: OptCurUserDep) -> HTMLResponse:
    (
        author,
        post_impression,
        related_posts,
        comments,
    ) = await asyncio.gather(
        to_thread(find_user, post.user_id),
        to_thread(find_post_impression, post, cur_user) if cur_user else asyncio.sleep(0, result=None),
        to_thread(get_post_related_posts, post),
        to_thread(get_post_comments, post),
    )

    html_content = get_html_content("post.html", {
        "cur_user": cur_user,
        "post": post,
        "author": author,
        "post_impression": post_impression,
        "related_posts": related_posts,
        "comments": comments,
        "comments_query": PostCommentQueryDTO()
    })
    return HTMLResponse(html_content)


async def _posts_page(query_dto: PostQueryDep, cur_user: OptCurUserDep) -> HTMLResponse:
    post_tag_slug = query_dto.tags[0] if query_dto.tags and len(query_dto.tags) == 1 else None
    (
        posts,
        post_tag,
        post_query_tags,
    ) = await asyncio.gather(
        to_thread(get_posts, query_dto, cur_user),
        to_thread(find_post_tag, post_tag_slug) if post_tag_slug else asyncio.sleep(0, result=None),
        asyncio.gather(*(to_thread(find_post_tag, tag) for tag in query_dto.tags)),
    )
    if post_tag and post_tag_slug and post_tag.slug != post_tag_slug:
        raise PostTagByOldSlugRequestedError(post_tag_slug, post_tag)

    post_query_tag_names = [tag.name if tag else slug for tag, slug in zip(post_query_tags, query_dto.tags)]
    post_query_tag_items = [
        {"value": slug, "name": name}
        for slug, name in zip(query_dto.tags, post_query_tag_names)
    ]
    return get_html_content("posts.html", {
        "cur_user": cur_user,
        "post_query": query_dto,
        "post_query_tag_names": post_query_tag_names,
        "post_query_tag_items": post_query_tag_items,
        "posts": posts,
        "post_tag": post_tag,
    })


@app.get("/posts/new", name="new-post", response_class=HTMLResponse)
async def new_post(cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.CREATE_POST)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("new-post.html", {
        "cur_user": cur_user
    })


@app.post("/api/posts", name="create-post", response_class=JSONResponse)
async def _create_post(post_dto: PostDTO, cur_user: CurUserDep, request: Request) -> str:
    try:
        post = create_post(post_dto, cur_user)
        return get_post_url(request, post)
    except SlugDuplicationError as e:
        raise HTTPException(status_code=409, detail=e.to_dict())


@app.get("/posts", name="posts", response_class=HTMLResponse)
async def posts_page(query_dto: PostQueryDep, cur_user: OptCurUserDep):
    return await _posts_page(query_dto, cur_user)


@app.get("/api/posts-fragment", name="posts-fragment", response_class=HTMLResponse)
async def posts_fragment(query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/posts.html", {
        "posts": get_posts(query_dto, cur_user)
    })


@app.get("/posts/{post_id}", name="post")
async def post_page(post: PostDep, cur_user: OptCurUserDep):
    return await _post_page(post, cur_user)


@app.get("/posts/{post_id}/edit", name="edit-post", response_class=HTMLResponse)
async def edit_post(post: PostDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, post)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("edit-post.html", {
        "cur_user": cur_user,
        "post": post
    })


@app.patch("/api/posts/{post_id}", name="update-post", response_class=JSONResponse)
async def _update_post(post: PostDep, update_post_dto: UpdatePostDTODep, cur_user: CurUserDep, request: Request) -> str:
    try:
        update_post(post, update_post_dto, cur_user, request)
        return get_post_url(request, post)
    except SlugDuplicationError as e:
        raise HTTPException(status_code=409, detail=e.to_dict())


@app.post("/api/posts/{post_id}/status", name="update-post-status", response_class=JSONResponse)
async def _update_post_status(post: PostDep, update_post_status_dto: UpdatePostStatusDTODep,
                              cur_user: CurUserDep, request: Request) -> str:
    update_post_status(post, update_post_status_dto, cur_user, request)
    return get_post_url(request, post)


@app.post("/api/posts/{post_id}/impression", name="update-post-impression", response_class=HTMLResponse)
async def _update_post_impression(post: PostDep, update_post_impression_dto: UpdatePostImpressionDTODep,
                                  cur_user: CurUserDep, request: Request) -> str:
    update_post_impression(post, update_post_impression_dto, cur_user, request)
    (
        post,
        post_impression,
    ) = await asyncio.gather(
        to_thread(find_post, post.id),
        to_thread(find_post_impression, post, cur_user),
    )
    return get_html_content("fragments/post-impressions.html", {
        "post": post,
        "post_impression": post_impression,
        "cur_user": cur_user,
    })


@app.post("/api/posts/{post_id}/comment", name="create-post-comment", response_class=JSONResponse)
async def _create_post_comment(post: PostDep, post_comment_dto: PostCommentDTO, cur_user: CurUserDep,
                               request: Request) -> str:
    post_comment = create_post_comment(post, post_comment_dto, cur_user, request)
    return get_post_comment_url(request, post, post_comment)


@app.patch("/api/posts/{post_id}/comments/{comment_id}", name="update-post-comment", response_class=JSONResponse)
async def _update_post_comment(post: PostDep, post_comment: PostCommentDep,
                               update_post_comment_dto: UpdatePostCommentDTODep, cur_user: CurUserDep,
                               request: Request) -> str:
    update_post_comment(post, post_comment, update_post_comment_dto, cur_user, request)
    return get_post_comment_url(request, post, post_comment)


@app.get("/{slugs_path:path}/posts", name="posts-by-slugs", response_class=HTMLResponse)
async def posts_page_by_slugs(query_dto: PostQueryBySlugsDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return await _posts_page(query_dto, cur_user)


@app.get("/contacts", name="contacts", response_class=HTMLResponse)
async def contacts(cur_user: OptCurUserDep) -> str:
    return get_html_content("contacts.html", {
        "cur_user": cur_user
    })


@app.post("/api/contacts/message", name="create-contact-message", status_code=204)
async def _create_contact_message(message_dto: ContactMessageDTO, cur_user: OptCurUserDep) -> None:
    create_contact_message(message_dto, cur_user)


@app.get("/post-tags/{slug}/edit", name="edit-post-tag", response_class=HTMLResponse)
async def edit_post_tag(post_tag: PostTagDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_POST_TAG, post_tag)
    return get_html_content("edit-post-tag.html", {
        "cur_user": cur_user,
        "post_tag": post_tag,
    })


@app.patch("/api/post-tags/{slug}", name="update-post-tag", response_class=JSONResponse)
async def _update_post_tag(update_post_tag_dto: UpdatePostTagDTODep, post_tag: PostTagDep, cur_user: CurUserDep,
                           request: Request) -> str:
    update_post_tag(post_tag, update_post_tag_dto, cur_user, request)
    return get_post_tag_url(request, post_tag)


@app.get("/api/post-tags", name="get-post-tags", response_class=JSONResponse)
async def _get_post_tags(query_dto: PostTagQueryDep) -> list[PostTag]:
    return get_post_tags(query_dto)


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


async def _user_page(user: UserDep, posts_query_dto: PostQueryDep, cur_user: OptCurUserDep) -> HTMLResponse:
    if cur_user:
        (
            posts,
            user_impression,
        ) = await asyncio.gather(
            to_thread(get_latest_posts_by_user, user, posts_query_dto, cur_user),
            to_thread(find_user_impression, user, cur_user),
        )
    else:
        posts = get_latest_posts_by_user(user, posts_query_dto, cur_user)
        user_impression = None

    html_content = get_html_content("user.html", {
        "cur_user": cur_user,
        "user": user,
        "post_query": posts_query_dto,
        "posts": posts,
        "user_impression": user_impression,
    })
    return HTMLResponse(html_content)


@app.get("/users/{user_id}", name="user")
async def user_page(user: UserDep, posts_query_dto: PostQueryDep, cur_user: OptCurUserDep):
    return await _user_page(user, posts_query_dto, cur_user)


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


@app.patch("/api/users/{user_id}", name="update-user", response_class=JSONResponse)
async def _update_user(update_user_dto: UpdateUserDTODep, user: UserDep, cur_user: CurUserDep, request: Request) -> str:
    update_user(user, update_user_dto, cur_user, request)
    return get_user_url(request, user)


@app.get("/api/users/{user_id}/posts-fragment", name="user-posts-fragment", response_class=HTMLResponse)
async def user_posts_fragment(user: UserDep, query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/posts.html", {
        "query": query_dto,
        "posts": get_latest_posts_by_user(user, query_dto, cur_user),
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
async def user_page_by_slug(user: UserBySlugDep, posts_query_dto: PostQueryDep,
                            cur_user: OptCurUserDep) -> HTMLResponse:
    return await _user_page(user, posts_query_dto, cur_user)


@app.get("/{user_slug}/{post_slug}", name="post-by-slugs", response_class=HTMLResponse)
async def post_page_by_slugs(post: PostBySlugsDep, cur_user: OptCurUserDep) -> HTMLResponse:
    return await _post_page(post, cur_user)


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


@app.exception_handler(PostByOldSlugRequestedError)
async def post_redirect_exception_handler(request: Request, exc: PostByOldSlugRequestedError):
    logger.info(f"Redirect: {str(exc.slug)} -> {exc.post.slug}")
    url = get_post_url(request, exc.post)
    return RedirectResponse(url=url, status_code=301)


@app.exception_handler(UserByOldSlugRequestedError)
async def post_redirect_exception_handler(request: Request, exc: UserByOldSlugRequestedError):
    logger.info(f"Redirect: {str(exc.slug)} -> {exc.user.username}")
    url = get_user_url(request, exc.user)
    return RedirectResponse(url=url, status_code=301)


@app.exception_handler(PostTagByOldSlugRequestedError)
async def post_tag_redirect_exception_handler(request: Request, exc: PostTagByOldSlugRequestedError):
    logger.info(f"Redirect: {str(exc.slug)} -> {exc.post_tag.slug}")
    if request.url.path.startswith("/post-tags/"):
        url = get_url(request, "edit-post-tag", slug=exc.post_tag.slug)
    else:
        url = get_post_tag_url(request, exc.post_tag)
    return RedirectResponse(url=url, status_code=301)


handler = Mangum(app)
