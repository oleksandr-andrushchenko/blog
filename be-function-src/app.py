from fastapi import FastAPI, Request, Response, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_200_OK,
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
import httpx
from urllib.parse import urlencode
from jose import jwt
from jose.exceptions import JWTError
import base64
from contextlib import asynccontextmanager

from config import (
    get_feature,
    get_cognito_domain,
    get_cognito_client_id,
    get_cognito_client_secret,
    is_prod
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
    logger,
    get_cognito_jwks,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if is_prod():
        # Startup: fetch JWKS
        # Fetch JWKS keys for token validation
        app.state.jwks = await get_cognito_jwks()
    else:
        app.state.logged_in = False
    yield
    # Shutdown: nothing special here, but can clean up resources
    # e.g., closing db connections


app = FastAPI(lifespan=lifespan)

# Serve local assets
if not is_prod():
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")


# todo: adding CORS middleware if your frontend ever makes direct JS requests to Lambda

async def get_current_user(request: Request) -> User | None:
    if not is_prod() and request.app.state.logged_in:
        return User(
            username="Test username",
            email="test@example.com",
            name="Test User",
            sub="test-sub"
        )
    token = request.cookies.get("session_token")
    if not token:
        return None  # no token, user is anonymous

    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        jwks = request.app.state.jwks

        # Try to find key
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)

        # If not found → refresh JWKS once
        if key is None:
            jwks = await get_cognito_jwks()
            request.app.state.jwks = jwks
            key = next((k for k in jwks["keys"] if k["kid"] == kid), None)

        if key is None:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid token (unknown kid)"
            )

        # Decode JWT to get claims
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=get_cognito_client_id())

        # Map claims to User model
        user_data = {
            "username": claims.get("cognito:username"),
            "email": claims.get("email"),
            "name": claims.get("name"),
            "sub": claims.get("sub"),
        }
        return User(**user_data)

    except JWTError:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
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
        html_content = get_html_content(
            "index.html",
            request=request,
            current_user=current_user
        )
        return HTMLResponse(content=html_content)

contacts_page = get_feature("contacts")
if contacts_page.get("active"):
    @app.get(contacts_page.get("path", "/contacts"), name="contacts")
    async def contacts(request: Request, current_user: CurrentUser = None):
        html_content = get_html_content(
            "contacts.html",
            request=request,
            current_user=current_user
        )
        return HTMLResponse(
            content=html_content
        )


    @app.post("/message", name="create_message")
    async def create_message(message: MessageDTO):
        if is_prod():
            serve_create_message(message)
        return HTMLResponse(
            status_code=HTTP_201_CREATED
        )

auth = get_feature("auth")
if auth.get("active"):
    # todo: simulate login/logout for non-prod env (setup fake cookies)
    @app.get("/auth/login", name="login")
    async def login(request: Request):
        # todo: make sure referer belongs to the website
        referer = request.headers.get("referer")

        if is_prod():
            redirect_url = (
                f"https://{get_cognito_domain()}/oauth2/authorize"
                f"?client_id={get_cognito_client_id()}"
                f"&response_type=code"
                f"&redirect_uri={referer if referer else get_full_url(request, 'login-callback')}"
                f"&scope=openid+email+profile"
            )
        else:
            request.app.state.logged_in = True
            redirect_url = referer if referer else get_url(request, "index")

        return RedirectResponse(
            url=redirect_url
        )


    @app.get("/auth/callback", name="login-callback")
    async def login_callback(request: Request):
        code = request.query_params.get("code")
        if not code:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="Missing code"
            )

        token_url = f"https://{get_cognito_domain()}/oauth2/token"
        cognito_client_id = get_cognito_client_id()
        cognito_client_secret = get_cognito_client_secret()
        data = {
            "grant_type": "authorization_code",
            "client_id": cognito_client_id,
            "code": code,
            "redirect_uri": get_full_url(request, 'login-callback'),
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        # If client secret is used
        if cognito_client_secret:
            auth_str = f"{cognito_client_id}:{cognito_client_secret}"
            headers["Authorization"] = "Basic " + base64.b64encode(auth_str.encode()).decode()

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(token_url, data=urlencode(data), headers=headers)
            if token_resp.status_code != HTTP_200_OK:
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail="Failed to exchange code"
                )
            tokens = token_resp.json()

        # Store ID token in secure HTTP-only cookie
        # tokens = response from Cognito
        id_token = tokens["id_token"]

        # Decode without verification just to read 'exp'
        unverified_claims = jwt.get_unverified_claims(id_token)
        exp = unverified_claims["exp"]  # UNIX timestamp
        now = int(time.time())
        max_age = exp - now
        if max_age < 0:
            max_age = 0  # token already expired
        response = RedirectResponse(
            url=get_url(request, "index"),
            status_code=HTTP_302_FOUND
        )
        response.set_cookie(
            "session_token",
            id_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=max_age
        )
        return response


    @app.get("/auth/logout", name="logout")
    async def logout(request: Request, response: Response):
        # todo: make sure referer belongs to the website
        referer = request.headers.get("referer")

        if is_prod():
            response.delete_cookie("session_token", path="/")
            redirect_url = (
                f"https://{get_cognito_domain()}/logout"
                f"?client_id={get_cognito_client_id()}"
                f"&logout_uri={referer if referer else get_full_url(request, 'index')}"
            )
        else:
            request.app.state.logged_in = False
            redirect_url = referer if referer else get_url(request, "index")

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
def get_error_response(request: Request, status_code: int, details):
    status_enum = HTTPStatus(status_code)
    content = {
        "code": status_code,
        "title": status_enum.phrase,
        "message": status_enum.description,
        "details": details,
    }
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return JSONResponse(
            status_code=status_code,
            content=content,
        )
    html_content = get_html_content(
        "error.html",
        request=request,
        data=content
    )
    return HTMLResponse(
        status_code=status_code,
        content=html_content,
    )


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    return get_error_response(
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
    return get_error_response(
        request,
        HTTP_422_UNPROCESSABLE_ENTITY,
        details
    )


# -------------------------
# Lambda entrypoint
# -------------------------
handler = Mangum(app)
