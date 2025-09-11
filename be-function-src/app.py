from typing import List, Dict, Union
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_204_NO_CONTENT,
    HTTP_302_FOUND,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_ENTITY,
)
from typing_extensions import Annotated
from typing import Optional
from mangum import Mangum
from http import HTTPStatus
from contextlib import asynccontextmanager
from utils import (
    # models
    User,
    ContactMessageDTO,
    PostDTO,
    Post,
    PublicPost,
    Tag,
    TagQueryDTO,
    PublicTag,
    # config
    is_prod,
    # errors
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    InvalidTokenKidError,
    SlugDuplicationError,
    PostNotFoundError,
    AuthorizationFailedError,
    UserNotFoundError,
    # helpers
    logger,
    get_html_content,
    get_full_url,
    get_user_by_plain_token,
    configure_app_state,
    # services
    get_posts_page_data,
    get_post,
    get_post_page_data,
    get_create_post_page_data,
    create_post,
    create_contact_message,
    get_index_page_data,
    get_contacts_page_data,
    get_user_token_by_code,
    get_login_redirect_url,
    get_logout_redirect_url,
    get_tags,
    get_error_page_data,
    create_dummy_fixtures,
    approve_post,
    get_users_page_data,
    get_user,
    get_user_page_data,
)


# -------------------------
# Dependencies
# -------------------------

async def get_cur_user(request: Request) -> User:
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
        )

    try:
        return await get_user_by_plain_token(
            plain_token=token,
            app_state=request.app.state
        )
    except (InvalidTokenKidError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )


async def get_opt_cur_user(request: Request) -> Optional[User]:
    try:
        return await get_user_by_plain_token(
            plain_token=request.cookies.get("session_token"),
            app_state=request.app.state
        )
    except (InvalidTokenKidError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )


async def get_post_by_id(post_id: str) -> Post:
    try:
        return await get_post(post_id)
    except PostNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=str(e),
        )


async def get_user_by_id(user_id: str) -> User:
    try:
        return await get_user(user_id)
    except UserNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=str(e),
        )


CurUserDep = Annotated[User, Depends(get_cur_user)]
OptCurUserDep = Annotated[Optional[User], Depends(get_opt_cur_user)]
PostDep = Annotated[Post, Depends(get_post_by_id)]
UserDep = Annotated[User, Depends(get_user_by_id)]


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

# -------------------------
# Routes
# -------------------------

@app.get("/", name="index", response_class=HTMLResponse)
async def index(request: Request, cur_user: OptCurUserDep) -> str:
    data = await get_index_page_data(
        request=request,
        cur_user=cur_user
    )
    return get_html_content("index.html", data)


@app.get("/create-post", name="create-post-page", response_class=HTMLResponse)
async def create_post_page(request: Request, cur_user: OptCurUserDep) -> str:
    data = await get_create_post_page_data(
        request=request,
        cur_user=cur_user
    )
    return get_html_content("create-post.html", data)


@app.post("/posts", name="create-post", status_code=HTTP_204_NO_CONTENT)
async def _create_post(post_dto: PostDTO, cur_user: CurUserDep) -> None:
    try:
        await create_post(post_dto, cur_user)
    except SlugDuplicationError as e:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=str(e)
        )


@app.get("/contacts", name="contacts-page", response_class=HTMLResponse)
async def contacts_page(request: Request, cur_user: OptCurUserDep) -> str:
    data = await get_contacts_page_data(
        request=request,
        cur_user=cur_user
    )
    return get_html_content("contacts.html", data)


@app.post("/contacts/message", name="create-contact-message", status_code=HTTP_204_NO_CONTENT)
async def _create_contact_message(message_dto: ContactMessageDTO, cur_user: OptCurUserDep) -> None:
    await create_contact_message(message_dto, cur_user)


@app.get("/auth/login", name="login", response_class=RedirectResponse)
async def login(request: Request) -> str:
    # todo: make sure referer belongs to the website
    referer = request.headers.get('referer')
    index_url = get_full_url(request, 'index')
    callback_url = f"{get_full_url(request, 'login-callback')}?redirect_url={referer if referer else index_url}"
    logger.info(f"login: callback_url: {callback_url}")

    redirect_url = await get_login_redirect_url(
        callback_url=callback_url
    )
    logger.info(f"login: redirect_url: {redirect_url}")

    return redirect_url


@app.get("/auth/callback", name="login-callback", response_class=RedirectResponse)
async def login_callback(request: Request) -> RedirectResponse:
    try:
        redirect_url = request.query_params.get('redirect_url')
        logger.info(f"login_callback: redirect_url: {redirect_url}")

        callback_url = f"{get_full_url(request, 'login-callback')}?redirect_url={redirect_url}"
        logger.info(f"login_callback: callback_url: {callback_url}")

        user_token = await get_user_token_by_code(
            code=request.query_params.get("code"),
            callback_url=callback_url
        )
        response = RedirectResponse(
            url=redirect_url,
            status_code=HTTP_302_FOUND
        )
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
    callback_url = referer if referer else get_full_url(request, 'index')
    logger.info(f"logout: callback_url: {callback_url}")

    redirect_url = await get_logout_redirect_url(
        callback_url=callback_url
    )
    logger.info(f"logout: redirect_url: {redirect_url}")

    response = RedirectResponse(
        url=redirect_url
    )
    response.delete_cookie("session_token")
    return response


@app.get("/posts", name="posts-page", response_class=HTMLResponse)
async def posts_page(request: Request, cur_user: OptCurUserDep) -> str:
    data = await get_posts_page_data(
        request=request,
        cur_user=cur_user
    )
    return get_html_content("posts.html", data)


@app.get("/posts/{post_id}", name="post-page", response_class=HTMLResponse)
async def post_page(post: PostDep, request: Request, cur_user: OptCurUserDep) -> str:
    data = await get_post_page_data(
        post=post,
        request=request,
        cur_user=cur_user
    )
    return get_html_content("post.html", data)


@app.post("/posts/{post_id}/approve", name="approve-post", status_code=HTTP_204_NO_CONTENT)
async def _approve_post(post: PostDep, cur_user: CurUserDep) -> None:
    await approve_post(
        post=post,
        user=cur_user
    )


@app.get("/tags", name="get-tags", response_model=List[PublicTag], response_class=JSONResponse)
async def _get_tags(query_dto: TagQueryDTO = Depends()) -> List[Tag]:
    return await get_tags(query_dto)


@app.post("/dummy-fixtures", name="create-dummy-fixtures")
async def _create_dummy_fixtures() -> None:
    return await create_dummy_fixtures()


@app.get("/users", name="users-page", response_class=HTMLResponse)
async def users_page(request: Request, cur_user: OptCurUserDep) -> str:
    data = await get_users_page_data(
        request=request,
        cur_user=cur_user
    )
    return get_html_content("users.html", data)


@app.get("/users/{user_id}", name="user-page", response_class=HTMLResponse)
async def user_page(user: UserDep, request: Request, cur_user: OptCurUserDep) -> str:
    data = await get_user_page_data(
        user=user,
        request=request,
        cur_user=cur_user
    )
    return get_html_content("user.html", data)


# -------------------------
# Exception handlers
# -------------------------

async def get_error_response(request: Request, status_code: int, details: Union[Dict, str]):
    status_enum = HTTPStatus(status_code)
    public_data = {
        "code": status_code,
        "title": status_enum.phrase,
        "message": status_enum.description,
        "details": details,
    }

    data = await get_error_page_data()
    accept = request.headers.get("accept", "")

    if "application/json" in accept:
        return JSONResponse(
            status_code=status_code,
            content=public_data
        )

    cur_user = None
    if status_code != HTTP_401_UNAUTHORIZED:
        try:
            cur_user = await get_cur_user(request)
        except HTTPException:
            pass

    data.update(public_data)
    data.update({"request": request, "cur_user": cur_user})
    content = get_html_content("error.html", data)

    return HTMLResponse(
        status_code=status_code,
        content=content
    )


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


# -------------------------
# Lambda entrypoint
# -------------------------
handler = Mangum(app)
