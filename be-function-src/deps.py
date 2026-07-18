from web import Body, Depends, HTMLResponse, HTTPException, JSONResponse, Query, Request, RequestValidationError
from typing import Annotated, Optional
from utils import (
    User,
    ArticleQueryDTO,
    Article,
    ArticleTagQueryDTO,
    UserQueryDTO,
    InvalidTokenError,
    ArticleNotFoundError,
    ArticleCommentNotFoundError,
    UserNotFoundError,
    get_html_content,
    get_user_by_auth_token,
    get_article,
    get_user,
    UpdateUserDTO,
    ImageFileDTO,
    UpdateArticleDTO,
    UpdateArticleStatusDTO,
    UpdateArticleImpressionDTO,
    UpdateUserImpressionDTO,
    get_user_by_slug,
    get_article_by_slugs,
    ArticleComment,
    UpdateUserStatusDTO,
    UpdateArticleCommentDTO,
    UpdateArticleCommentImpressionDTO,
    get_article_comment,
    parse_articles_url_slugs_path,
    get_cdn_cache_version,
    is_prod,
    get_auth_token_max_age,
    ArticleTag,
    ArticleTagNotFoundError,
    get_article_tag,
    UpdateArticleTagDTO,
)


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


def get_article_tag_by_slug(slug: str, cur_user: CurUserDep) -> ArticleTag:
    try:
        return get_article_tag(slug, cur_user)
    except ArticleTagNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_update_article_tag_dto(update_article_tag_dto: UpdateArticleTagDTO = Body(...)) -> UpdateArticleTagDTO:
    return update_article_tag_dto


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
    data.update({"tags": tags})
    try:
        return ArticleQueryDTO(**data)
    except ValueError as e:
        raise RequestValidationError({"query": str(e)})


def get_article_query_by_slugs(request: Request, slugs_path: str) -> ArticleQueryDTO:
    data = dict(request.query_params)
    data.update(parse_articles_url_slugs_path(slugs_path))
    try:
        return ArticleQueryDTO(**data)
    except ValueError as e:
        raise RequestValidationError({"query": str(e)})


async def get_image_file(request: Request):
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=422, detail="Missing file")
    try:
        return ImageFileDTO(
            content=await file.read(),
            filename=file.filename,
        )
    except ValueError as exc:
        raise RequestValidationError({"file": str(exc)}) from exc


def get_update_user_dto(update_user_dto: UpdateUserDTO = Body(...)) -> UpdateUserDTO:
    return update_user_dto


def get_update_user_status_dto(update_user_status_dto: UpdateUserStatusDTO = Body(...)) -> UpdateUserStatusDTO:
    return update_user_status_dto


def get_update_article_dto(update_article_dto: UpdateArticleDTO = Body(...)) -> UpdateArticleDTO:
    return update_article_dto


def get_update_article_status_dto(update_article_status_dto: UpdateArticleStatusDTO = Body(...)) -> UpdateArticleStatusDTO:
    return update_article_status_dto


def get_update_article_impression_dto(
        update_article_impression_dto: UpdateArticleImpressionDTO = Body(...)) -> UpdateArticleImpressionDTO:
    return update_article_impression_dto


def get_article_comment_by_id(article_id: str, comment_id: str) -> ArticleComment:
    try:
        return get_article_comment(article_id, comment_id)
    except ArticleCommentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_update_article_comment_dto(update_article_comment_dto: UpdateArticleCommentDTO = Body(...)) -> UpdateArticleCommentDTO:
    return update_article_comment_dto


def get_update_article_comment_impression_dto(update_article_comment_impression_dto: UpdateArticleCommentImpressionDTO = Body(
    ...)) -> UpdateArticleCommentImpressionDTO:
    return update_article_comment_impression_dto


def get_update_user_impression_dto(
        update_user_impression_dto: UpdateUserImpressionDTO = Body(...)) -> UpdateUserImpressionDTO:
    return update_user_impression_dto


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
    from http import HTTPStatus
    status_enum = HTTPStatus(status_code)
    public_data = {
        "code": status_code,
        "title": status_enum.phrase,
        "message": status_enum.description,
        "details": details,
    }

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
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


def set_token_cookie(token, response):
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        secure=is_prod(),
        samesite="lax",
        max_age=get_auth_token_max_age(),
    )


def drop_token_cookie(response):
    response.delete_cookie("token")


def get_cdn_cache_cookie(request):
    return request.cookies.get("cdn_version")


def set_cdn_cache_cookie(user: User, response):
    response.set_cookie(
        key="cdn_version",
        value=get_cdn_cache_version(user),
        httponly=True,
        secure=is_prod(),
        samesite="lax",
        max_age=get_auth_token_max_age(),
    )


def drop_cdn_cache_cookie(response):
    response.delete_cookie("cdn_version")


UserDep = Annotated[User, Depends(get_user_by_id)]
UserBySlugDep = Annotated[User, Depends(_get_user_by_slug)]
UpdateUserDTODep = Annotated[UpdateUserDTO, Depends(get_update_user_dto)]
UpdateUserStatusDTODep = Annotated[UpdateUserStatusDTO, Depends(get_update_user_status_dto)]
UserQueryDep = Annotated[UserQueryDTO, Depends()]
UserQueryBySlugsDep = Annotated[UserQueryDTO, Depends(get_user_query_by_slugs)]
ArticleDep = Annotated[Article, Depends(get_article_by_id)]
ArticleBySlugsDep = Annotated[Article, Depends(_get_article_by_slugs)]
UpdateArticleDTODep = Annotated[UpdateArticleDTO, Depends(get_update_article_dto)]
UpdateArticleStatusDTODep = Annotated[UpdateArticleStatusDTO, Depends(get_update_article_status_dto)]
UpdateArticleImpressionDTODep = Annotated[UpdateArticleImpressionDTO, Depends(get_update_article_impression_dto)]
ArticleQueryDep = Annotated[ArticleQueryDTO, Depends(get_article_query)]
ArticleQueryBySlugsDep = Annotated[ArticleQueryDTO, Depends(get_article_query_by_slugs)]
ArticleCommentDep = Annotated[ArticleComment, Depends(get_article_comment_by_id)]
UpdateArticleCommentDTODep = Annotated[UpdateArticleCommentDTO, Depends(get_update_article_comment_dto)]
UpdateArticleCommentImpressionDTODep = Annotated[
    UpdateArticleCommentImpressionDTO, Depends(get_update_article_comment_impression_dto)]
ArticleTagQueryDep = Annotated[ArticleTagQueryDTO, Depends()]
ArticleTagDep = Annotated[ArticleTag, Depends(get_article_tag_by_slug)]
UpdateArticleTagDTODep = Annotated[UpdateArticleTagDTO, Depends(get_update_article_tag_dto)]
ImageFileDTODep = Annotated[ImageFileDTO, Depends(get_image_file)]
UpdateUserImpressionDTODep = Annotated[UpdateUserImpressionDTO, Depends(get_update_user_impression_dto)]
