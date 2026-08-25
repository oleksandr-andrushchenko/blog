from typing import Annotated, Optional
from urllib.parse import urlparse

from shared_utils import (
    User,
    ArticleQueryDTO,
    ArticleCommentQueryDTO,
    Article,
    TagQueryDTO,
    UserQueryDTO,
    InvalidTokenError,
    ArticleNotFoundError,
    UserNotFoundError,
    get_html_content,
    get_web_base_url,
    get_user_by_auth_token,
    get_article,
    get_user,
    get_user_by_slug,
    get_article_by_slugs,
    parse_articles_url_slugs_path,
    is_prod,
    get_auth_token_max_age,
    Tag,
    TagNotFoundError,
    get_tag,
)
from web import Depends, HTMLResponse, HTTPException, JSONResponse, Query, Request, RequestValidationError


def _resolve_user(request: Request) -> User | None:
    token = request.cookies.get("token")
    if not token:
        return None

    try:
        return get_user_by_auth_token(token)
    except InvalidTokenError:
        return None


def get_cur_user(request: Request) -> User:
    user = _resolve_user(request)
    request.state.cur_user = user

    if not user:
        raise HTTPException(status_code=401)

    return user


def get_opt_cur_user(request: Request) -> User | None:
    user = _resolve_user(request)
    request.state.cur_user = user
    return user


CurUserDep = Annotated[User, Depends(get_cur_user)]
OptCurUserDep = Annotated[Optional[User], Depends(get_opt_cur_user)]


def get_article_by_id(article_id: str, cur_user: OptCurUserDep = None) -> Article:
    try:
        return get_article(article_id, cur_user)
    except ArticleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_tag_by_slug(slug: str, cur_user: CurUserDep) -> Tag:
    try:
        return get_tag(slug, cur_user)
    except TagNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_user_by_id(user_id: str, cur_user: OptCurUserDep = None) -> User:
    try:
        return get_user(user_id, cur_user)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_user_query_by_slugs(request: Request, type: str) -> UserQueryDTO:
    data = dict(request.query_params)
    data.update({"type": type})
    try:
        return UserQueryDTO(**data)
    except ValueError as e:
        raise RequestValidationError({"query": str(e)})


def get_article_query(request: Request, tags: list[str] = Query([])) -> ArticleQueryDTO:
    data = dict(request.query_params)
    data.pop("activities_year", None)
    data.update({"tags": tags})
    try:
        return ArticleQueryDTO(**data)
    except ValueError as e:
        raise RequestValidationError({"query": str(e)})


def get_article_query_by_slugs(request: Request, slugs_path: str) -> ArticleQueryDTO:
    data = dict(request.query_params)
    data.pop("activities_year", None)
    data.update(parse_articles_url_slugs_path(slugs_path))
    try:
        return ArticleQueryDTO(**data)
    except ValueError as e:
        raise RequestValidationError({"query": str(e)})


def _get_user_by_slug(slug: str, cur_user: OptCurUserDep = None) -> User:
    try:
        return get_user_by_slug(slug, cur_user)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _get_article_by_slugs(user_slug: str, article_slug: str, cur_user: OptCurUserDep = None) -> Article:
    try:
        return get_article_by_slugs(user_slug, article_slug, cur_user)
    except ArticleNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_error_response(request: Request, status_code: int, details: dict | str = None):
    from api_route_metadata import API_URL_ROUTES
    from http import HTTPStatus
    status_enum = HTTPStatus(status_code)
    public_data = {
        "code": status_code,
        "title": status_enum.phrase,
        "message": status_enum.description,
        "details": details,
    }

    content_type = request.headers.get("content-type", "")
    is_api_request = request.scope.get("route_name") in API_URL_ROUTES
    if is_api_request or "application/json" in content_type:
        return JSONResponse(
            status_code=status_code,
            content=public_data
        )

    # cur_user = None
    # if status_code != 401:
    #     try:
    #         cur_user =  get_cur_user(request)
    #     except HTTPException:
    #         pass

    content = get_html_content("error.html", {
        **public_data,
        # "cur_user": cur_user
    })

    return HTMLResponse(
        status_code=status_code,
        content=content
    )


def _auth_cookie_domain() -> str | None:
    hostname = urlparse(get_web_base_url()).hostname
    if not hostname or hostname in {"localhost", "127.0.0.1"} or "." not in hostname:
        return None
    return f".{hostname}"


def set_token_cookie(token, response):
    response.delete_cookie("token")
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        secure=is_prod(),
        domain=_auth_cookie_domain(),
        samesite="lax",
        max_age=get_auth_token_max_age(),
    )


def drop_token_cookie(response):
    response.delete_cookie("token")
    response.delete_cookie("token", domain=_auth_cookie_domain())


UserDep = Annotated[User, Depends(get_user_by_id)]
UserBySlugDep = Annotated[User, Depends(_get_user_by_slug)]
UserQueryDep = Annotated[UserQueryDTO, Depends()]
UserQueryBySlugsDep = Annotated[UserQueryDTO, Depends(get_user_query_by_slugs)]
ArticleDep = Annotated[Article, Depends(get_article_by_id)]
ArticleBySlugsDep = Annotated[Article, Depends(_get_article_by_slugs)]
ArticleQueryDep = Annotated[ArticleQueryDTO, Depends(get_article_query)]
ArticleCommentQueryDep = Annotated[ArticleCommentQueryDTO, Depends()]
ArticleQueryBySlugsDep = Annotated[ArticleQueryDTO, Depends(get_article_query_by_slugs)]
TagQueryDep = Annotated[TagQueryDTO, Depends()]
TagDep = Annotated[Tag, Depends(get_tag_by_slug)]
