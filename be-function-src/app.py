from fastapi import FastAPI, Request, Response, Depends, HTTPException, Body
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
    HTTP_422_UNPROCESSABLE_ENTITY,
)
from typing_extensions import Annotated
from typing import Optional
from models import User
from mangum import Mangum
from http import HTTPStatus
import time
from contextlib import asynccontextmanager
from config import (
    get_feature,
    is_prod,
)
from models import (
    MessageDTO,
)
from utils import (
    list_posts,
    get_post,
    create_post,
    serve_create_message,
    get_html_content,
    get_full_url,
    get_url,
    get_user_from_token,
    configure_app_state,
    serve_index,
    serve_contacts,
    serve_login_callback,
    serve_login,
    serve_logout,
    serve_error,
)
from errors import (
    InvalidTokenError,
    InvalidCodeError,
    CodeExchangeFailedError,
    InvalidTokenKidError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_app_state(app.state)
    yield
    # Shutdown: nothing special here, but can clean up resources
    # e.g., closing db connections


app = FastAPI(lifespan=lifespan)

# Serve local assets
if not is_prod():
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")


# todo: adding CORS middleware if your frontend ever makes direct JS requests to Lambda

async def get_current_user(request: Request) -> Optional[User]:
    try:
        return await get_user_from_token(
            token=request.cookies.get("session_token"),
            app_state=request.app.state
        )
    except (InvalidTokenKidError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )


CurrentUser = Annotated[Optional[User], Depends(get_current_user)]
# TODO: add CORS middleware if needed (fastapi.middleware.cors.CORSMiddleware)

# -------------------------
# Routes
# -------------------------
index_page = get_feature("index")
if index_page.get("active", True):
    @app.get(index_page.get("path", "/"), name="index")
    async def index(request: Request, current_user: CurrentUser = None):
        data = await serve_index({
            "request": request,
            "current_user": current_user
        })
        content = get_html_content("index.html", data)
        return HTMLResponse(
            content=content
        )

contacts_page = get_feature("contacts")
if contacts_page.get("active"):
    @app.get(contacts_page.get("path", "/contacts"), name="contacts")
    async def contacts(request: Request, current_user: CurrentUser = None):
        data = await serve_contacts({
            "request": request,
            "current_user": current_user
        })
        content = get_html_content("contacts.html", data)
        return HTMLResponse(
            content=content
        )


    @app.post("/message", name="create_message")
    async def create_message(message: MessageDTO):
        serve_create_message(message)
        return HTMLResponse(
            status_code=HTTP_201_CREATED
        )

auth = get_feature("auth")
if auth.get("active"):
    @app.get("/auth/login", name="login")
    async def login(request: Request):
        redirect_url = await serve_login(
            callback_url=get_full_url(request, 'login-callback'),
            non_prod_callback_url=get_url(request, "index"),
            referer=request.headers.get("referer"),
            app_state=request.app.state
        )
        return RedirectResponse(
            url=redirect_url
        )


    @app.get("/auth/callback", name="login-callback")
    async def login_callback(request: Request):
        try:
            token = await serve_login_callback(
                code=request.query_params.get("code"),
                redirect_url=get_full_url(request, 'login-callback')
            )
            response = RedirectResponse(
                url=get_url(request, "index"),
                status_code=HTTP_302_FOUND
            )
            response.set_cookie(
                key="session_token",
                value=token.plain,
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=min(0, token.exp - int(time.time()))
            )
            return response
        except (InvalidCodeError, CodeExchangeFailedError, InvalidTokenError) as e:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=str(e)
            )


    @app.get("/auth/logout", name="logout")
    async def logout(request: Request, response: Response):
        response.delete_cookie("session_token")
        redirect_url = await serve_logout(
            callback_url=get_full_url(request, 'index'),
            non_prod_callback_url=get_url(request, "index"),
            referer=request.headers.get("referer"),
            app_state=request.app.state
        )
        return RedirectResponse(
            url=redirect_url
        )


@app.get("/posts")
async def posts():
    return list_posts()


@app.get("/posts/{post_id}")
async def post(post_id: str):
    item = get_post(post_id)
    if not item:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    return item


@app.post("/posts")
async def new_post(data: dict = Body(...)):
    return create_post(
        data["title"],
        data["slug"],
        data["author_id"],
        data["content"],
        data.get("tags", []),
    )


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
    data = await serve_error({})
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
    return await get_error_response(
        request,
        exc.status_code,
        exc.detail
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
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
