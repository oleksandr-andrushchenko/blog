"""Resolve saved creator links and authenticate internal click notifications."""
import time

from jose import JWTError, jwt

from creator_link_models import CreatorLink, validate_link_url
from app_config import get_config


def get_creator_links(user, kind) -> list[CreatorLink]:
    if kind != 'support':
        raise ValueError('Unsupported creator link kind')
    return getattr(user, f'{kind}_links')


def get_creator_link(user, kind, link_id, article=None) -> CreatorLink:
    if user.status != 'active' or (article is not None and article.owner_id != user.id):
        raise ValueError('Creator link is unavailable')
    links = get_creator_links(user, kind)
    if article is not None and getattr(article, f'{kind}_providers') is not None and link_id not in getattr(article, f'{kind}_providers'):
        raise ValueError('Creator link is unavailable')
    link = next((link for link in links if link.id == link_id), None)
    if link and validate_link_url(link.url):
        return link
    raise ValueError('Creator link is unavailable')


def encode_creator_click(owner_id, kind, link_id, article_id=None, actor_id=None):
    secret = get_config().get('app_secret')
    if not secret:
        raise ValueError('APP_SECRET is required for click notifications')
    now = int(time.time())
    return jwt.encode({
        'iss': 'web', 'aud': 'creator-link-click', 'iat': now, 'exp': now + 60,
        'owner_id': owner_id, 'kind': kind, 'link_id': link_id,
        'article_id': article_id, 'actor_id': actor_id,
    }, secret, algorithm='HS256')


def decode_creator_click(token):
    secret = get_config().get('app_secret')
    if not secret:
        raise ValueError('APP_SECRET is required for click notifications')
    try:
        claims = jwt.decode(token, secret, algorithms=['HS256'], audience='creator-link-click',
                            issuer='web', options={'require_exp': True, 'require_iat': True})
    except JWTError as exc:
        raise ValueError('Invalid click notification') from exc
    if (claims.get('kind') != 'support'
            or any(not isinstance(claims.get(key), str) or not claims[key]
            for key in ('owner_id', 'kind', 'link_id'))
            or any(claims.get(key) is not None and (not isinstance(claims[key], str) or not claims[key])
                   for key in ('article_id', 'actor_id'))):
        raise ValueError('Invalid click notification')
    return claims
