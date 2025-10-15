from fastapi import Request, Depends, HTTPException, Query, UploadFile, File, Body
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
)
from typing_extensions import Annotated
from typing import Optional
from http import HTTPStatus
from utils import (
    User,
    PostQueryDTO,
    Post,
    TagQueryDTO,
    UserQueryDTO,
    InvalidTokenError,
    InvalidTokenKidError,
    PostNotFoundError,
    UserNotFoundError,
    get_html_content,
    get_user_by_plain_token,
    get_post,
    get_user,
    UpdateUserDTO,
    FileDTO,
    UpdatePostDTO,
    UpdatePostStatusDTO,
    UpdatePostImpressionDTO,
    UpdateUserImpressionDTO,
    get_user_by_username,
)


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
UserQueryDep = Annotated[UserQueryDTO, Depends()]


async def get_post_query(request: Request, tags: list[str] = Query([])) -> PostQueryDTO:
    data = dict(request.query_params)
    data['tags'] = tags
    return PostQueryDTO(**data)


PostQueryDep = Annotated[PostQueryDTO, Depends(get_post_query)]
TagQueryDep = Annotated[TagQueryDTO, Depends()]


async def get_file(file: UploadFile = File(...)):
    return FileDTO(
        content=await file.read(),
        filename=file.filename,
    )


FileDTODep = Annotated[FileDTO, Depends(get_file)]


def get_update_user_dto(
        update_user_dto: UpdateUserDTO = Body(...)
) -> UpdateUserDTO:
    return update_user_dto


UpdateUserDTODep = Annotated[UpdateUserDTO, Depends(get_update_user_dto)]


def get_update_post_dto(
        update_post_dto: UpdatePostDTO = Body(...)
) -> UpdatePostDTO:
    return update_post_dto


UpdatePostDTODep = Annotated[UpdateUserDTO, Depends(get_update_post_dto)]


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


def get_update_user_impression_dto(
        update_user_impression_dto: UpdateUserImpressionDTO = Body(...)
) -> UpdateUserImpressionDTO:
    return update_user_impression_dto


UpdateUserImpressionDTODep = Annotated[UpdateUserImpressionDTO, Depends(get_update_user_impression_dto)]


async def _get_user_by_username(username: str) -> User:
    try:
        return await get_user_by_username(username)
    except UserNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=str(e),
        )


UserByUsernameDep = Annotated[User, Depends(_get_user_by_username)]


async def get_error_response(request: Request, status_code: int, details: dict | str):
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
    # if status_code != HTTP_401_UNAUTHORIZED:
    #     try:
    #         cur_user = await get_cur_user(request)
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
