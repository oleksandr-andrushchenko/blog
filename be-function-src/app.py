from typing import List
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_302_FOUND,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
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
    UserToken,
    User,
    ContactMessageDTO,
    PostDTO,
    Post,
    PublicPost,
    PublicContactMessage,
    TagQueryDTO,
    PublicTag,
    # config
    get_feature,
    is_prod,
    # errors
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    InvalidTokenKidError,
    SlugDuplicationError,
    PostNotFound,
    # helpers
    logger,
    get_html_content,
    get_full_url,
    get_user_token_by_plain_token,
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
)


# -------------------------
# Dependencies
# -------------------------
async def get_user_token(request: Request) -> Optional[UserToken]:
    try:
        return await get_user_token_by_plain_token(
            plain_token=request.cookies.get("session_token"),
            app_state=request.app.state
        )
    except (InvalidTokenKidError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )


UserToken = Annotated[Optional[UserToken], Depends(get_user_token)]


async def get_user(request: Request) -> Optional[UserToken]:
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


User = Annotated[Optional[User], Depends(get_user)]


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
index_page = get_feature("index")
auth_feature = get_feature("auth")
create_post_feature = get_feature("create_post")
posts_feature = get_feature("posts")
post_feature = get_feature("post")
contacts_feature = get_feature("contacts")

if index_page.get("active", True):
    @app.get(index_page.get("path", "/"), name="index", response_class=HTMLResponse)
    async def index(request: Request, user_token: UserToken = None):
        data = await get_index_page_data({
            "request": request,
            "user_token": user_token
        })
        return get_html_content("index.html", data)

if create_post_feature.get("active"):
    @app.get(create_post_feature.get("path", "/create-post"), name="create-post-page", response_class=HTMLResponse)
    async def create_post_page(request: Request, user_token: UserToken = None):
        data = await get_create_post_page_data({
            "request": request,
            "user_token": user_token
        })
        return get_html_content("create-post.html", data)


    @app.post(create_post_feature.get("path", "/post"), name="create-post", status_code=HTTP_201_CREATED,
              response_model=PublicPost, response_class=JSONResponse)
    async def _create_post(post_dto: PostDTO, user: User = None) -> Post:
        if not user:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED
            )
        try:
            post = await create_post(post_dto, user)
        except SlugDuplicationError as e:
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=str(e)
            )
        return post

if contacts_feature.get("active"):
    @app.get(contacts_feature.get("path", "/contacts"), name="contacts-page", response_class=HTMLResponse)
    async def contacts_page(request: Request, user_token: UserToken = None):
        data = await get_contacts_page_data({
            "request": request,
            "user_token": user_token
        })
        return get_html_content("contacts.html", data)


    @app.post("/contacts/message", name="create-contact-message", status_code=HTTP_201_CREATED,
              response_model=PublicContactMessage, response_class=JSONResponse)
    async def _create_contact_message(message_dto: ContactMessageDTO, user: User = None):
        return await create_contact_message(message_dto, user)

if auth_feature.get("active"):
    @app.get("/auth/login", name="login", response_class=RedirectResponse)
    async def login(request: Request):
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


    @app.get("/auth/callback", name="login-callback")
    async def login_callback(request: Request):
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


    @app.get("/auth/logout", name="logout")
    async def logout(request: Request):
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


@app.get(posts_feature.get("path", "/posts"), name="posts-page", response_class=HTMLResponse)
async def posts_page(request: Request, user_token: UserToken = None):
    data = await get_posts_page_data({
        "request": request,
        "user_token": user_token
    })
    return get_html_content("posts.html", data)


@app.get(post_feature.get("path", "/posts/{post_id}"), name="post-page", response_class=HTMLResponse)
async def post_page(post_id: str, request: Request, user_token: UserToken = None):
    try:
        post = await get_post(post_id)
        data = await get_post_page_data(post, {
            "request": request,
            "user_token": user_token
        })
        return get_html_content("post.html", data)
    except PostNotFound:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Post not found",
        )


@app.get("/tags", name="get-tags", response_model=List[PublicTag], response_class=JSONResponse)
async def _get_tags(query_dto: TagQueryDTO = Depends()):
    return await get_tags(query_dto)


# -------------------------
# Exception handlers
# -------------------------
async def get_error_response(request: Request, status_code: int, details):
    status_enum = HTTPStatus(status_code)
    public_data = {
        "code": status_code,
        "title": status_enum.phrase,
        "message": status_enum.description,
        "details": details,
    }
    data = await get_error_page_data({})
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return JSONResponse(
            status_code=status_code,
            content=public_data,
        )
    data.update(public_data)
    data.update({"request": request})
    content = get_html_content("error.html", data)
    return HTMLResponse(
        status_code=status_code,
        content=content,
    )


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.debug(f"custom_http_exception_handler: {exc}")
    return await get_error_response(
        request,
        exc.status_code,
        exc.detail
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.debug(f"validation_exception_handler: {exc}")
    details = {}
    for error in exc.errors():
        field = error["loc"][-1] if len(error["loc"]) > 1 else error["loc"][0]
        details[field] = error["msg"]
    return await get_error_response(
        request,
        HTTP_422_UNPROCESSABLE_ENTITY,
        details
    )


# -------------------------
# Lambda entrypoint
# -------------------------
handler = Mangum(app)
