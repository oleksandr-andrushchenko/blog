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
    TagQueryDTO,
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
)


def _resolve_user(request: Request) -> User | None:
    token = request.cookies.get("auth_token")
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


CurUserDep = Annotated[User, Depends(get_cur_user)]


def get_opt_cur_user(request: Request) -> User | None:
    user = _resolve_user(request)
    request.state.cur_user = user
    return user


OptCurUserDep = Annotated[Optional[User], Depends(get_opt_cur_user)]


def get_post_by_id(post_id: str, cur_user: OptCurUserDep = None) -> Post:
    try:
        return get_post(post_id, cur_user)
    except PostNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )


PostDep = Annotated[Post, Depends(get_post_by_id)]


def get_user_by_id(user_id: str, cur_user: OptCurUserDep = None) -> User:
    try:
        return get_user(user_id, cur_user)
    except UserNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )


UserDep = Annotated[User, Depends(get_user_by_id)]
UserQueryDep = Annotated[UserQueryDTO, Depends()]


def get_user_query_by_slugs(request: Request, type: str) -> UserQueryDTO:
    data = dict(request.query_params)
    data.update({"type": type})
    try:
        return UserQueryDTO(**data)
    except ValidationError as e:
        raise RequestValidationError(e.errors())


UserQueryBySlugsDep = Annotated[UserQueryDTO, Depends(get_user_query_by_slugs)]


def get_post_query(request: Request, tags: list[str] = Query([])) -> PostQueryDTO:
    data = dict(request.query_params)
    data.update({"tags": tags})
    try:
        return PostQueryDTO(**data)
    except ValidationError as e:
        raise RequestValidationError(e.errors())


PostQueryDep = Annotated[PostQueryDTO, Depends(get_post_query)]
TagQueryDep = Annotated[TagQueryDTO, Depends()]


def get_post_query_by_slugs(request: Request, slugs_path: str) -> PostQueryDTO:
    data = dict(request.query_params)
    data.update(parse_posts_url_slugs_path(slugs_path))
    try:
        return PostQueryDTO(**data)
    except ValidationError as e:
        raise RequestValidationError(e.errors())


PostQueryBySlugsDep = Annotated[PostQueryDTO, Depends(get_post_query_by_slugs)]


async def get_image_file(file: UploadFile = File(...)):
    return ImageFileDTO(
        content=await file.read(),
        filename=file.filename,
    )


ImageFileDTODep = Annotated[ImageFileDTO, Depends(get_image_file)]


def get_update_user_dto(
        update_user_dto: UpdateUserDTO = Body(...)
) -> UpdateUserDTO:
    return update_user_dto


UpdateUserDTODep = Annotated[UpdateUserDTO, Depends(get_update_user_dto)]


def get_update_user_status_dto(
        update_user_status_dto: UpdateUserStatusDTO = Body(...)
) -> UpdateUserStatusDTO:
    return update_user_status_dto


UpdateUserStatusDTODep = Annotated[UpdateUserStatusDTO, Depends(get_update_user_status_dto)]


def get_update_post_dto(
        update_post_dto: UpdatePostDTO = Body(...)
) -> UpdatePostDTO:
    return update_post_dto


UpdatePostDTODep = Annotated[UpdatePostDTO, Depends(get_update_post_dto)]


def get_update_post_status_dto(
        update_post_status_dto: UpdatePostStatusDTO = Body(...)
) -> UpdatePostStatusDTO:
    return update_post_status_dto


UpdatePostStatusDTODep = Annotated[UpdatePostStatusDTO, Depends(get_update_post_status_dto)]


def get_update_post_impression_dto(
        update_post_impression_dto: UpdatePostImpressionDTO = Body(...)
) -> UpdatePostImpressionDTO:
    return update_post_impression_dto


UpdatePostImpressionDTODep = Annotated[UpdatePostImpressionDTO, Depends(get_update_post_impression_dto)]


def get_post_comment_by_id(post_id: str, post_comment_id: str) -> PostComment:
    try:
        return get_post_comment(post_id, post_comment_id)
    except PostNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )


PostCommentDep = Annotated[PostComment, Depends(get_post_comment_by_id)]


def get_update_post_comment_dto(update_post_comment_dto: UpdatePostCommentDTO = Body(...)) -> UpdatePostCommentDTO:
    return update_post_comment_dto


UpdatePostCommentDTODep = Annotated[UpdateUserDTO, Depends(get_update_post_dto)]


def get_update_post_comment_impression_dto(
        update_post_comment_impression_dto: UpdatePostCommentImpressionDTO = Body(...)
) -> UpdatePostCommentImpressionDTO:
    return update_post_comment_impression_dto


UpdatePostCommentImpressionDTODep = Annotated[
    UpdatePostCommentImpressionDTO,
    Depends(get_update_post_comment_impression_dto)
]


def get_update_user_impression_dto(
        update_user_impression_dto: UpdateUserImpressionDTO = Body(...)
) -> UpdateUserImpressionDTO:
    return update_user_impression_dto


UpdateUserImpressionDTODep = Annotated[UpdateUserImpressionDTO, Depends(get_update_user_impression_dto)]


def _get_user_by_slug(slug: str, cur_user: OptCurUserDep = None) -> User:
    try:
        return get_user_by_slug(slug, cur_user)
    except UserNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )


UserBySlugDep = Annotated[User, Depends(_get_user_by_slug)]


def _get_post_by_slugs(user_slug: str, post_slug: str, cur_user: OptCurUserDep = None) -> Post:
    try:
        return get_post_by_slugs(user_slug, post_slug, cur_user)
    except PostNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )
    except UserNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )


PostBySlugsDep = Annotated[Post, Depends(_get_post_by_slugs)]


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
