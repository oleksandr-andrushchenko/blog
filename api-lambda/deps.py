"""API-only request dependencies.

The web lambda keeps the shared read/query dependencies in ``shared``;
upload and mutation request parsing belongs to the API lambda.
"""

from shared_deps import *
from article_dtos import UpdateArticleCommentDTO, UpdateArticleCommentImpressionDTO, UpdateArticleDTO, UpdateArticleImpressionDTO, UpdateArticleStatusDTO, UpdateArticleTagDTO
from article_tag_subscription_dtos import ArticleTagSubscriptionDTO
from basic_dtos import ImageFileDTO
from query_dtos import ArticleTagQueryDTO
from user_dtos import UpdateUserDTO, UpdateUserActivitySettingsDTO, UpdateUserImpressionDTO, UpdateUserInterestsSettingsDTO, UpdateUserStatusDTO
from shared_utils import ArticleComment, ArticleCommentNotFoundError, get_article_comment
from web import Body, HTTPException, Request


async def get_image_file(request: Request):
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=422, detail="Missing file")
    try:
        return ImageFileDTO(content=await file.read(), filename=file.filename)
    except ValueError as exc:
        raise RequestValidationError({"file": str(exc)}) from exc


def get_update_user_dto(value: UpdateUserDTO = Body(...)) -> UpdateUserDTO:
    return value


def get_article_tag_subscription_dto(value: ArticleTagSubscriptionDTO = Body(...)) -> ArticleTagSubscriptionDTO:
    return value


def get_update_user_activity_settings_dto(
        value: UpdateUserActivitySettingsDTO = Body(...)) -> UpdateUserActivitySettingsDTO:
    return value


def get_update_user_interests_settings_dto(
        value: UpdateUserInterestsSettingsDTO = Body(...)) -> UpdateUserInterestsSettingsDTO:
    return value


def get_update_user_status_dto(value: UpdateUserStatusDTO = Body(...)) -> UpdateUserStatusDTO:
    return value


def get_update_article_dto(value: UpdateArticleDTO = Body(...)) -> UpdateArticleDTO:
    return value


def get_update_article_status_dto(value: UpdateArticleStatusDTO = Body(...)) -> UpdateArticleStatusDTO:
    return value


def get_update_article_impression_dto(value: UpdateArticleImpressionDTO = Body(...)) -> UpdateArticleImpressionDTO:
    return value


def get_article_comment_by_id(article_id: str, comment_id: str) -> ArticleComment:
    try:
        return get_article_comment(article_id, comment_id)
    except ArticleCommentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def get_update_article_comment_dto(value: UpdateArticleCommentDTO = Body(...)) -> UpdateArticleCommentDTO:
    return value


def get_update_article_comment_impression_dto(
        value: UpdateArticleCommentImpressionDTO = Body(...)) -> UpdateArticleCommentImpressionDTO:
    return value


def get_update_user_impression_dto(value: UpdateUserImpressionDTO = Body(...)) -> UpdateUserImpressionDTO:
    return value


def get_update_article_tag_dto(value: UpdateArticleTagDTO = Body(...)) -> UpdateArticleTagDTO:
    return value


UpdateUserDTODep = Annotated[UpdateUserDTO, Depends(get_update_user_dto)]
UpdateUserActivitySettingsDTODep = Annotated[
    UpdateUserActivitySettingsDTO, Depends(get_update_user_activity_settings_dto)]
UpdateUserInterestsSettingsDTODep = Annotated[
    UpdateUserInterestsSettingsDTO, Depends(get_update_user_interests_settings_dto)]
UpdateUserStatusDTODep = Annotated[UpdateUserStatusDTO, Depends(get_update_user_status_dto)]
UpdateArticleDTODep = Annotated[UpdateArticleDTO, Depends(get_update_article_dto)]
UpdateArticleStatusDTODep = Annotated[UpdateArticleStatusDTO, Depends(get_update_article_status_dto)]
UpdateArticleImpressionDTODep = Annotated[UpdateArticleImpressionDTO, Depends(get_update_article_impression_dto)]
ArticleCommentDep = Annotated[ArticleComment, Depends(get_article_comment_by_id)]
UpdateArticleCommentDTODep = Annotated[UpdateArticleCommentDTO, Depends(get_update_article_comment_dto)]
UpdateArticleCommentImpressionDTODep = Annotated[
    UpdateArticleCommentImpressionDTO, Depends(get_update_article_comment_impression_dto)]
ArticleTagQueryDep = Annotated[ArticleTagQueryDTO, Depends()]
ArticleTagDep = Annotated[ArticleTag, Depends(get_article_tag_by_slug)]
UpdateArticleTagDTODep = Annotated[UpdateArticleTagDTO, Depends(get_update_article_tag_dto)]
ArticleTagSubscriptionDTODep = Annotated[ArticleTagSubscriptionDTO, Depends(get_article_tag_subscription_dto)]
ImageFileDTODep = Annotated[ImageFileDTO, Depends(get_image_file)]
UpdateUserImpressionDTODep = Annotated[UpdateUserImpressionDTO, Depends(get_update_user_impression_dto)]
