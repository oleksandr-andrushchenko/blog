from typing import List
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_204_NO_CONTENT,
    HTTP_302_FOUND,
    HTTP_400_BAD_REQUEST,
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
    AuthorizationFailedError,
    logger,
    get_html_content,
    get_full_url,
    configure_app_state,
    get_url,
    create_post,
    create_contact_message,
    get_user_token_by_code,
    get_login_redirect_url,
    get_logout_redirect_url,
    get_post_tags,
    create_dummy_fixtures,
    approve_post,
    get_latest_active_users,
    get_latest_published_posts_by_user,
    get_published_posts,
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
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_app_state(app.state)
    yield
    # Shutdown: nothing special here, but can clean up resources
    # e.g., closing db connections


app = FastAPI(lifespan=lifespan)

# Serve local static files
if not is_prod():
    app.mount("/static", StaticFiles(directory="static"), name="static")


# TODO: add CORS middleware if needed (fastapi.middleware.cors.CORSMiddleware)

@app.middleware("http")
async def inject_template_global_vars(request: Request, call_next):
    jinja2_env().globals["request"] = request
    return await call_next(request)


@app.get("/", name="view-index", response_class=HTMLResponse)
async def view_index(cur_user: OptCurUserDep) -> str:
    posts_query = PostQueryDTO()
    return get_html_content("view-index.html", {
        "cur_user": cur_user,
        "popular_tags": await get_popular_post_tags(),
        "posts_query": posts_query,
        "posts": await get_published_posts(posts_query),
        "popular_posts": await get_popular_published_posts(),
        "popular_users": await get_popular_active_users(),
    })


@app.post("/public-file", name="upload-public-file", response_class=JSONResponse)
async def upload_public_file(file_dto: FileDTODep) -> str:
    return await save_public_file(file_dto)


@app.get("/posts/new", name="new-post", response_class=HTMLResponse)
async def new_post(cur_user: CurUserDep) -> str:
    return get_html_content("new-post.html", {
        "cur_user": cur_user
    })


@app.post("/posts", name="create-post", response_class=JSONResponse)
async def _create_post(post_dto: PostDTO, cur_user: CurUserDep, request: Request) -> str:
    try:
        post = await create_post(post_dto, cur_user)
        return get_url(request, "view-post", post_id=post.id)
    except SlugDuplicationError as e:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail={"title": str(e)}
        )


@app.get("/posts", name="view-posts", response_class=HTMLResponse)
async def view_posts(query_dto: PostQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("view-posts.html", {
        "cur_user": cur_user,
        "posts_query": query_dto,
        "posts": await get_published_posts(query_dto),
    })


@app.get("/posts-fragment", name="view-posts-fragment", response_class=HTMLResponse)
async def view_posts_fragment(query_dto: PostQueryDep) -> str:
    return get_html_content("fragments/posts.html", {
        "posts": await get_published_posts(query_dto)
    })


@app.get("/posts/{post_id}", name="view-post", response_class=HTMLResponse)
async def view_post(post: PostDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("view-post.html", {
        "cur_user": cur_user,
        "post": post,
        "author": await find_user(post.user_id)
    })


@app.get("/posts/{post_id}/edit", name="edit-post", response_class=HTMLResponse)
async def edit_post(post: PostDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, post)
    return get_html_content("edit-post.html", {
        "cur_user": cur_user,
        "post": post
    })


@app.patch("/posts/{post_id}", name="update-post", response_class=JSONResponse)
async def _update_post(post: PostDep, update_post_dto: UpdatePostDTODep, cur_user: CurUserDep, request: Request) -> str:
    try:
        await update_post(post, update_post_dto, cur_user)
        return get_url(request, "view-post", post_id=post.id)
    except SlugDuplicationError as e:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail={"title": str(e)}
        )


@app.post("/posts/{post_id}/approve", name="approve-post", status_code=HTTP_204_NO_CONTENT)
async def _approve_post(post: PostDep, cur_user: CurUserDep) -> None:
    await approve_post(
        post=post,
        user=cur_user
    )


@app.get("/contacts", name="view-contacts", response_class=HTMLResponse)
async def view_contacts(cur_user: OptCurUserDep) -> str:
    return get_html_content("view-contacts.html", {
        "cur_user": cur_user
    })


@app.post("/contacts/message", name="create-contact-message", status_code=HTTP_204_NO_CONTENT)
async def _create_contact_message(message_dto: ContactMessageDTO, cur_user: OptCurUserDep) -> None:
    await create_contact_message(message_dto, cur_user)


@app.get("/post-tags", name="get-post-tags", response_model=List[PublicTag], response_class=JSONResponse)
async def _get_post_tags(query_dto: TagQueryDep) -> List[Tag]:
    return await get_post_tags(query_dto)


@app.get("/users", name="view-users", response_class=HTMLResponse)
async def view_users(query_dto: UserQueryDep, cur_user: OptCurUserDep) -> str:
    return get_html_content("view-users.html", {
        "cur_user": cur_user,
        "users_query": query_dto,
        "users": await get_latest_active_users(query_dto)
    })


@app.get("/users-fragment", name="view-users-fragment", response_class=HTMLResponse)
async def view_users_fragment(query_dto: UserQueryDep) -> str:
    return get_html_content("fragments/users.html", {
        "users": await get_latest_active_users(query_dto)
    })


@app.get("/users/{user_id}", name="view-user", response_class=HTMLResponse)
async def view_user(user: UserDep, cur_user: OptCurUserDep) -> str:
    posts_query_dto = PostQueryDTO()
    return get_html_content("view-user.html", {
        "cur_user": cur_user,
        "user": user,
        "posts_query": posts_query_dto,
        "posts": await get_latest_published_posts_by_user(user)
    })


@app.get("/users/{user_id}/edit", name="edit-user", response_class=HTMLResponse)
async def edit_user(user: UserDep, cur_user: CurUserDep) -> str:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)
    return get_html_content("edit-user.html", {
        "cur_user": cur_user,
        "user": user
    })


@app.patch("/users/{user_id}", name="update-user", response_class=JSONResponse)
async def _update_user(update_user_dto: UpdateUserDTODep, user: UserDep, cur_user: CurUserDep, request: Request) -> str:
    await update_user(user, update_user_dto, cur_user)
    return get_url(request, "view-user", user_id=user.id)


@app.get("/users/{user_id}/posts-fragment", name="view-user-posts-fragment", response_class=HTMLResponse)
async def view_user_posts_fragment(user: UserDep, query_dto: PostQueryDep) -> str:
    return get_html_content("fragments/posts.html", {
        "query": query_dto,
        "posts": await get_latest_published_posts_by_user(user, query_dto)
    })


@app.get("/auth/login", name="login", response_class=RedirectResponse)
async def login(request: Request) -> str:
    # todo: make sure referer belongs to the website
    referer = request.headers.get('referer')
    index_url = get_full_url(request, 'view-index')
    callback_url = f"{get_full_url(request, 'login-callback')}?redirect_url={referer if referer else index_url}"
    redirect_url = await get_login_redirect_url(callback_url)
    return redirect_url


@app.get("/auth/callback", name="login-callback", response_class=RedirectResponse)
async def login_callback(request: Request) -> RedirectResponse:
    try:
        redirect_url = request.query_params.get('redirect_url')
        callback_url = f"{get_full_url(request, 'login-callback')}?redirect_url={redirect_url}"

        user_token = await get_user_token_by_code(
            code=request.query_params.get("code"),
            callback_url=callback_url
        )
        response = RedirectResponse(redirect_url, HTTP_302_FOUND)
        response.set_cookie(
            key="session_token",
            value=user_token.plain_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=user_token.max_age
        )
        return response
    except (InvalidCodeError, CodeExchangeFailedError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@app.get("/auth/logout", name="logout", response_class=RedirectResponse)
async def logout(request: Request) -> RedirectResponse:
    # todo: make sure referer belongs to the website
    referer = request.headers.get("referer")
    callback_url = referer if referer else get_full_url(request, 'view-index')
    redirect_url = await get_logout_redirect_url(callback_url)
    response = RedirectResponse(redirect_url)
    response.delete_cookie("session_token")
    return response


@app.post("/dummy-fixtures", name="create-dummy-fixtures")
async def _create_dummy_fixtures() -> None:
    return await create_dummy_fixtures()


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.warning(f"HTTP exception: {exc}")
    return await get_error_response(
        request,
        exc.status_code,
        exc.detail
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Validation failed: {exc}")
    details = {}
    for error in exc.errors():
        field = error["loc"][-1] if len(error["loc"]) > 1 else error["loc"][0]
        details[field] = error["msg"]
    return await get_error_response(
        request,
        HTTP_422_UNPROCESSABLE_ENTITY,
        details,
    )


@app.exception_handler(AuthorizationFailedError)
async def authorization_failed_handler(request: Request, exc: AuthorizationFailedError):
    logger.warning(f"Authorization failed: {exc}")
    return await get_error_response(
        request,
        HTTP_403_FORBIDDEN,
        {"permission": exc.permission},
    )


handler = Mangum(app)
