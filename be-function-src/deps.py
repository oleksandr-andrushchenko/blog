from fastapi import Request, Depends, HTTPException, Query, UploadFile, File, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from typing_extensions import Annotated
from typing import Optional
from utils import (
    User,
    PostQueryDTO,
    Post,
    PostTagQueryDTO,
    UserQueryDTO,
    InvalidTokenError,
    PostNotFoundError,
    UserNotFoundError,
    get_html_content,
    get_user_by_auth_token,
    get_post,
    get_user,
    UpdateUserDTO,
    ImageFileDTO,
    UpdatePostDTO,
    UpdatePostStatusDTO,
    UpdatePostImpressionDTO,
    UpdateUserImpressionDTO,
    get_user_by_slug,
    get_post_by_slugs,
    PostComment,
    UpdateUserStatusDTO,
    UpdatePostCommentDTO,
    UpdatePostCommentImpressionDTO,
    get_post_comment,
    parse_posts_url_slugs_path,
    get_cdn_cache_version,
    is_prod,
    get_auth_token_max_age,
    PostTag,
    PostTagNotFoundError,
    get_post_tag,
    UpdatePostTagDTO,
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


def get_post_by_id(post_id: str, cur_user: OptCurUserDep = None) -> Post:
    try:
        return get_post(post_id, cur_user)
    except PostNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_post_tag_by_name(post_tag_name: str, cur_user: CurUserDep) -> PostTag:
    try:
        return get_post_tag(post_tag_name, cur_user)
    except PostTagNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_update_post_tag_dto(update_post_tag_dto: UpdatePostTagDTO = Body(...)) -> UpdatePostTagDTO:
    return update_post_tag_dto


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
    except ValidationError as e:
        raise RequestValidationError(e.errors())


def get_post_query(request: Request, tags: list[str] = Query([])) -> PostQueryDTO:
    data = dict(request.query_params)
    data.update({"tags": tags})
    try:
        return PostQueryDTO(**data)
    except ValidationError as e:
        raise RequestValidationError(e.errors())


def get_post_query_by_slugs(request: Request, slugs_path: str) -> PostQueryDTO:
    data = dict(request.query_params)
    data.update(parse_posts_url_slugs_path(slugs_path))
    try:
        return PostQueryDTO(**data)
    except ValidationError as e:
        raise RequestValidationError(e.errors())


async def get_image_file(file: UploadFile = File(...)):
    return ImageFileDTO(
        content=await file.read(),
        filename=file.filename,
    )


def get_update_user_dto(update_user_dto: UpdateUserDTO = Body(...)) -> UpdateUserDTO:
    return update_user_dto


def get_update_user_status_dto(update_user_status_dto: UpdateUserStatusDTO = Body(...)) -> UpdateUserStatusDTO:
    return update_user_status_dto


def get_update_post_dto(update_post_dto: UpdatePostDTO = Body(...)) -> UpdatePostDTO:
    return update_post_dto


def get_update_post_status_dto(update_post_status_dto: UpdatePostStatusDTO = Body(...)) -> UpdatePostStatusDTO:
    return update_post_status_dto


def get_update_post_impression_dto(
        update_post_impression_dto: UpdatePostImpressionDTO = Body(...)) -> UpdatePostImpressionDTO:
    return update_post_impression_dto


def get_post_comment_by_id(post_id: str, post_comment_id: str) -> PostComment:
    try:
        return get_post_comment(post_id, post_comment_id)
    except PostNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def get_update_post_comment_dto(update_post_comment_dto: UpdatePostCommentDTO = Body(...)) -> UpdatePostCommentDTO:
    return update_post_comment_dto


def get_update_post_comment_impression_dto(update_post_comment_impression_dto: UpdatePostCommentImpressionDTO = Body(
    ...)) -> UpdatePostCommentImpressionDTO:
    return update_post_comment_impression_dto


def get_update_user_impression_dto(
        update_user_impression_dto: UpdateUserImpressionDTO = Body(...)) -> UpdateUserImpressionDTO:
    return update_user_impression_dto


def _get_user_by_slug(slug: str, cur_user: OptCurUserDep = None) -> User:
    try:
        return get_user_by_slug(slug, cur_user)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _get_post_by_slugs(user_slug: str, post_slug: str, cur_user: OptCurUserDep = None) -> Post:
    try:
        return get_post_by_slugs(user_slug, post_slug, cur_user)
    except PostNotFoundError as e:
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
PostDep = Annotated[Post, Depends(get_post_by_id)]
PostBySlugsDep = Annotated[Post, Depends(_get_post_by_slugs)]
UpdatePostDTODep = Annotated[UpdatePostDTO, Depends(get_update_post_dto)]
UpdatePostStatusDTODep = Annotated[UpdatePostStatusDTO, Depends(get_update_post_status_dto)]
UpdatePostImpressionDTODep = Annotated[UpdatePostImpressionDTO, Depends(get_update_post_impression_dto)]
PostQueryDep = Annotated[PostQueryDTO, Depends(get_post_query)]
PostQueryBySlugsDep = Annotated[PostQueryDTO, Depends(get_post_query_by_slugs)]
PostCommentDep = Annotated[PostComment, Depends(get_post_comment_by_id)]
UpdatePostCommentDTODep = Annotated[UpdatePostCommentDTO, Depends(get_update_post_dto)]
UpdatePostCommentImpressionDTODep = Annotated[
    UpdatePostCommentImpressionDTO, Depends(get_update_post_comment_impression_dto)]
PostTagQueryDep = Annotated[PostTagQueryDTO, Depends()]
PostTagDep = Annotated[PostTag, Depends(get_post_tag_by_name)]
UpdatePostTagDTODep = Annotated[UpdatePostTagDTO, Depends(get_update_post_tag_dto)]
ImageFileDTODep = Annotated[ImageFileDTO, Depends(get_image_file)]
UpdateUserImpressionDTODep = Annotated[UpdateUserImpressionDTO, Depends(get_update_user_impression_dto)]
