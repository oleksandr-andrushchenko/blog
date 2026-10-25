"""Media selection shared by the API's publishing integrations."""
from urllib.parse import urlsplit

from engagement_service import record_article_share
from shared_utils import get_static_url, logger, utc_now
from web import HTTPException


def authorize_article_owner(article, actor):
    if article.owner_id != actor.id:
        raise HTTPException(403, 'Only the article owner can share this article')


def remember_article_share(article, platform, result, *, actor, request, article_url, post_url=None, event_id=None):
    """Record complete, confirmed shares without turning a remote success into a retry."""
    if result.get('shared') is not True:
        return result
    field = f'{platform}_shared_at'
    now = utc_now()
    remote_id = result.get('post_id', result.get('message_id'))
    try:
        recorded = record_article_share(article, actor, platform + '_publish', event_id or str(remote_id), request,
                                        article_url=article_url, post_url=post_url, published_at=now)
    except Exception:
        logger.exception('Could not save %s share state for article %s', platform, article.id)
    else:
        if recorded:
            setattr(article, field, now)
    return result


def article_media(article, request):
    filename = getattr(article, 'image_filename', None)
    if not filename:
        return []
    url = filename if urlsplit(filename).scheme in ('http', 'https') else get_static_url(request, filename, absolute=True)
    return [{'format': 'image', 'url': url, 'filename': filename}]
