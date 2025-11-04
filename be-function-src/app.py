from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_204_NO_CONTENT,
    HTTP_302_FOUND,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_ENTITY,
)
from mangum import Mangum
from contextlib import asynccontextmanager
from utils import (
    ContactMessageDTO,
    PostDTO,
    PostQueryDTO,
    Tag,
    PublicTag,
    is_prod,
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    SlugDuplicationError,
    NotAuthorizedError,
    logger,
    get_html_content,
    get_url,
    configure_app_state,
    get_post_url,
    create_post,
    create_contact_message,
    get_user_token_by_code,
    get_login_redirect_url,
    get_logout_redirect_url,
    get_post_tags,
    create_dummy_fixtures,
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
    save_public_file,
    update_post,
    find_post_impression,
    update_post_impression,
    update_user_impression,
    find_user_impression,
    get_user_url,
    NotAuthenticatedError,
    update_user_status,
    Me,
    get_static_files_dir,
    UserStatus,
    UserBannedError,
    utc_now,
    get_allowed_origins,
    get_redirect_url,
)
from deps import (
    OptCurUserDep,
    FileDTODep,
    CurUserDep,
    PostQueryDep,
    PostDep,
    TagQueryDep,
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
)
from urllib.parse import quote, unquote
import asyncio
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_app_state(app.state)
    yield
    # Shutdown: nothing special here, but can clean up resources
    # e.g., closing db connections


app = FastAPI(lifespan=lifespan)

if not is_prod():
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
async def inject_template_global_vars(request: Request, call_next):
    jinja2_env().globals["request"] = request
    return await call_next(request)


@app.get("/", name="index", response_class=HTMLResponse)
async def index(cur_user: OptCurUserDep) -> str:
    latest_posts_query = PostQueryDTO()
    (
        popular_post_tags,
        latest_posts,
        popular_posts,
        popular_users,
    ) = await asyncio.gather(
        get_popular_post_tags(),
        get_latest_published_posts(limit=latest_posts_query.limit),
        get_popular_published_posts(limit=5),
        get_popular_active_users(limit=5),
    )
    return get_html_content("index.html", {
        "cur_user": cur_user,
        "popular_post_tags": popular_post_tags,
        "latest_posts_query": latest_posts_query,
        "latest_posts": latest_posts,
        "popular_posts": popular_posts,
        "popular_users": popular_users,
    })


@app.post("/public-file", name="upload-public-file", response_class=JSONResponse)
async def upload_public_file(file_dto: FileDTODep) -> str:
    return await save_public_file(file_dto)


@app.get("/posts/new", name="new-post", response_class=HTMLResponse)
async def new_post(cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.CREATE_POST)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("new-post.html", {
        "cur_user": cur_user
    })


@app.post("/posts", name="create-post", response_class=JSONResponse)
async def _create_post(post_dto: PostDTO, cur_user: CurUserDep, request: Request) -> str:
    try:
        post = await create_post(post_dto, cur_user)
        return get_post_url(request, post)
    except SlugDuplicationError as e:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=e.to_dict()
        )


@app.get("/posts", name="posts", response_class=HTMLResponse)
async def posts_page(query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("posts.html", {
        "cur_user": cur_user,
        "posts_query": query_dto,
        "posts": await get_posts(query_dto, cur_user),
    })


@app.get("/posts-fragment", name="posts-fragment", response_class=HTMLResponse)
async def posts_fragment(query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/posts.html", {
        "posts": await get_posts(query_dto, cur_user)
    })


@app.get("/posts/{post_id}", name="post", response_class=HTMLResponse)
async def post_page(post: PostDep, cur_user: OptCurUserDep) -> str:
    if cur_user:
        (
            author,
            post_impression,
        ) = await asyncio.gather(
            find_user(post.user_id),
            find_post_impression(post, cur_user),
        )
    else:
        author = await find_user(post.user_id)
        post_impression = None

    return get_html_content("post.html", {
        "cur_user": cur_user,
        "post": post,
        "author": author,
        "post_impression": post_impression,
    })


@app.get("/posts/{post_id}/edit", name="edit-post", response_class=HTMLResponse)
async def edit_post(post: PostDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, post)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("edit-post.html", {
        "cur_user": cur_user,
        "post": post
    })


@app.patch("/posts/{post_id}", name="update-post", response_class=JSONResponse)
async def _update_post(post: PostDep, update_post_dto: UpdatePostDTODep, cur_user: CurUserDep, request: Request) -> str:
    try:
        await update_post(post, update_post_dto, cur_user)
        return get_post_url(request, post)
    except SlugDuplicationError as e:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=e.to_dict()
        )


@app.post("/posts/{post_id}/status", name="update-post-status", response_class=JSONResponse)
async def _update_post_status(post: PostDep, update_post_status_dto: UpdatePostStatusDTODep,
                              cur_user: CurUserDep, request: Request) -> str:
    await update_post_status(post, update_post_status_dto, cur_user)
    return get_post_url(request, post)


@app.post("/posts/{post_id}/impression", name="update-post-impression", status_code=HTTP_204_NO_CONTENT)
async def _update_post_impression(post: PostDep, update_post_impression_dto: UpdatePostImpressionDTODep,
                                  cur_user: CurUserDep) -> None:
    return await update_post_impression(post, update_post_impression_dto, cur_user)


@app.get("/contacts", name="contacts", response_class=HTMLResponse)
async def contacts(cur_user: OptCurUserDep) -> str:
    return get_html_content("contacts.html", {
        "cur_user": cur_user
    })


@app.post("/contacts/message", name="create-contact-message", status_code=HTTP_204_NO_CONTENT)
async def _create_contact_message(message_dto: ContactMessageDTO, cur_user: OptCurUserDep) -> None:
    await create_contact_message(message_dto, cur_user)


@app.get("/post-tags", name="get-post-tags", response_model=list[PublicTag], response_class=JSONResponse)
async def _get_post_tags(query_dto: TagQueryDep) -> list[Tag]:
    return await get_post_tags(query_dto)


@app.get("/users", name="users", response_class=HTMLResponse)
async def users(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("users.html", {
        "cur_user": cur_user,
        "users_query": query_dto,
        "users": await get_users(query_dto, cur_user)
    })


@app.get("/users-fragment", name="users-fragment", response_class=HTMLResponse)
async def users_fragment(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/users.html", {
        "users": await get_users(query_dto, cur_user),
        "cur_user": cur_user,
    })


@app.get("/users/{user_id}", name="user", response_class=HTMLResponse)
async def user_page(user: UserDep, posts_query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    if cur_user:
        (
            posts,
            user_impression,
        ) = await asyncio.gather(
            get_latest_posts_by_user(user, posts_query_dto, cur_user),
            find_user_impression(user, cur_user),
        )
    else:
        posts = await get_latest_posts_by_user(user, posts_query_dto, cur_user)
        user_impression = None

    return get_html_content("user.html", {
        "cur_user": cur_user,
        "user": user,
        "posts_query": posts_query_dto,
        "posts": posts,
        "user_impression": user_impression,
    })


@app.post("/users/{user_id}/status", name="update-user-status", response_class=JSONResponse)
async def _update_user_status(user: UserDep, update_user_status_dto: UpdateUserStatusDTODep,
                              cur_user: CurUserDep, request: Request) -> str:
    await update_user_status(user, update_user_status_dto, cur_user)
    return get_user_url(request, user)


@app.post("/users/{user_id}/impression", name="update-user-impression", status_code=HTTP_204_NO_CONTENT)
async def _update_user_impression(user: UserDep, update_user_impression_dto: UpdateUserImpressionDTODep,
                                  cur_user: CurUserDep) -> None:
    return await update_user_impression(user, update_user_impression_dto, cur_user)


@app.get("/users/{user_id}/edit", name="edit-user", response_class=HTMLResponse)
async def edit_user(user: UserDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError
    return get_html_content("edit-user.html", {
        "cur_user": cur_user,
        "user": user
    })


@app.patch("/users/{user_id}", name="update-user", response_class=JSONResponse)
async def _update_user(update_user_dto: UpdateUserDTODep, user: UserDep, cur_user: CurUserDep, request: Request) -> str:
    await update_user(user, update_user_dto, cur_user)
    return get_user_url(request, user)


@app.get("/users/{user_id}/posts-fragment", name="user-posts-fragment", response_class=HTMLResponse)
async def user_posts_fragment(user: UserDep, query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("fragments/posts.html", {
        "query": query_dto,
        "posts": await get_latest_posts_by_user(user, query_dto, cur_user),
        "cur_user": cur_user,
    })


@app.get("/login", name="login", response_class=RedirectResponse)
async def login(request: Request) -> RedirectResponse:
    redirect_url = get_redirect_url(request)
    callback_url = get_url(request, 'login-callback', full_url=True)
    provider_redirect_url = await get_login_redirect_url(callback_url)
    response = RedirectResponse(provider_redirect_url)
    response.set_cookie("redirect_url", redirect_url, httponly=True, secure=True)
    return response


@app.get("/login-callback", name="login-callback", response_class=RedirectResponse)
async def login_callback(request: Request) -> RedirectResponse:
    try:
        redirect_url = request.cookies.get("redirect_url") or get_url(request, "index")
        callback_url = get_url(request, 'login-callback', full_url=True)

        user_token = await get_user_token_by_code(
            code=request.query_params.get("code"),
            callback_url=callback_url
        )
        response = RedirectResponse(redirect_url, HTTP_302_FOUND)
        response.set_cookie(
            key="session_token",
            value=user_token.plain_token,
            httponly=True,
            secure=is_prod(),
            samesite="lax",
            max_age=user_token.max_age
        )
        return response
    except (InvalidCodeError, CodeExchangeFailedError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@app.get("/logout", name="logout", response_class=RedirectResponse)
async def logout(request: Request) -> RedirectResponse:
    redirect_url = get_redirect_url(request)
    callback_url = get_url(request, 'logout-callback', full_url=True)
    provider_redirect_url = await get_logout_redirect_url(callback_url)
    response = RedirectResponse(provider_redirect_url)
    response.set_cookie("redirect_url", redirect_url, httponly=True, secure=True)
    response.delete_cookie("session_token")
    return response


@app.get("/logout-callback", name="logout-callback", response_class=RedirectResponse)
async def logout_callback(request: Request) -> str:
    redirect_url = request.cookies.get("redirect_url") or get_url(request, "index")
    return redirect_url


@app.post("/dummy-fixtures", name="create-dummy-fixtures")
async def _create_dummy_fixtures() -> None:
    return await create_dummy_fixtures()


@app.get("/me", name="me", response_model=Me, response_class=JSONResponse)
async def post_page_by_slugs(cur_user: CurUserDep) -> Me:
    return cur_user


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


@app.get("/{slug}", name="user-by-slug", response_class=HTMLResponse)
async def user_page_by_slug(user: UserBySlugDep, posts_query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    return await user_page(user, posts_query_dto, cur_user)


@app.get("/{user_slug}/{post_slug}", name="post-by-slugs", response_class=HTMLResponse)
async def post_page_by_slugs(post: PostBySlugsDep, cur_user: OptCurUserDep) -> str:
    return await post_page(post, cur_user)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.error(f"HTTP exception: {str(exc)}")
    return await get_error_response(
        request,
        exc.status_code,
        exc.detail
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation failed: {str(exc)}")
    details = {}
    for error in exc.errors():
        field = error["loc"][-1] if len(error["loc"]) > 1 else error["loc"][0]
        details[field] = error["msg"]
    return await get_error_response(
        request,
        HTTP_422_UNPROCESSABLE_ENTITY,
        details,
    )


@app.exception_handler(NotAuthenticatedError)
async def not_authenticated_error_handler(request: Request, exc: NotAuthenticatedError):
    logger.error(f"Not authenticated: {str(exc)}")
    return await get_error_response(
        request,
        HTTP_401_UNAUTHORIZED,
    )


@app.exception_handler(UserBannedError)
async def user_banned_error_handler(request: Request, exc: UserBannedError):
    raise NotAuthorizedError("BANNED")


@app.exception_handler(NotAuthorizedError)
async def not_authorized_error_handler(request: Request, exc: NotAuthorizedError):
    logger.error(f"Not authorized: {str(exc)}")
    return await get_error_response(
        request,
        HTTP_403_FORBIDDEN,
        {"permission": exc.permission},
    )


handler = Mangum(app)
