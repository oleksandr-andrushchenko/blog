from typing import Annotated, Optional

from shared_utils import (
    User,
    ArticleQueryDTO,
    Article,
    TagQueryDTO,
    UserQueryDTO,
    InvalidTokenError,
    ArticleNotFoundError,
    UserNotFoundError,
    get_user_by_auth_token,
    get_article,
    get_user,
    Tag,
    TagNotFoundError,
    get_tag,
)
from web import (
    Depends,
    HTTPException,
    Query,
    Request,
    parse_dto,
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


def get_article_query(request: Request, tags: list[str] = Query([])) -> ArticleQueryDTO:
    data = dict(request.query_params)
    data.update({"tags": tags})
    return parse_dto(ArticleQueryDTO, data)


UserDep = Annotated[User, Depends(get_user_by_id)]
UserQueryDep = Annotated[UserQueryDTO, Depends()]
ArticleDep = Annotated[Article, Depends(get_article_by_id)]
ArticleQueryDep = Annotated[ArticleQueryDTO, Depends(get_article_query)]
TagQueryDep = Annotated[TagQueryDTO, Depends()]
TagDep = Annotated[Tag, Depends(get_tag_by_slug)]
