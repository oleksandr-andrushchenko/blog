"""Media selection shared by the API's publishing integrations."""
from urllib.parse import urlsplit

from shared_utils import add_dynamodb_update_transact, dynamodb_transact_write, get_static_url, logger, utc_now
from web import HTTPException


def authorize_article_owner(article, actor):
    if article.owner_id != actor.id:
        raise HTTPException(403, 'Only the article owner can share this article')


def remember_article_share(article, platform, result, *, article_url, post_url=None):
    """Record complete, confirmed shares without turning a remote success into a retry."""
    if result.get('shared') is not True:
        return result
    field = f'{platform}_shared_at'
    context = {
        'article_url': article_url, 'post_url': post_url, 'article_id': article.id,
        'owner_id': article.owner_id, 'reshare': bool(getattr(article, field, None)),
        **{key: result[key] for key in ('message_id', 'post_id', 'channel_title', 'username') if key in result},
    }
    logger.info('Article shared to %s', platform.title(), extra={'context': context})
    now = utc_now()
    transacts = []
    add_dynamodb_update_transact(transacts, (f'POST#{article.id}', 'META'),
                                 changes={field: now}, add_updated_at=False)
    update = transacts[0]['Update']
    update['ConditionExpression'] = 'attribute_exists(pk) AND #share_owner = :share_owner'
    update['ExpressionAttributeNames']['#share_owner'] = 'user_id'
    update['ExpressionAttributeValues'][':share_owner'] = article.owner_id
    try:
        dynamodb_transact_write(transacts)
    except Exception:
        logger.exception('Could not save %s share state for article %s', platform, article.id)
    else:
        setattr(article, field, now)
    return result


def article_media(article, request):
    filename = getattr(article, 'image_filename', None)
    if not filename:
        return []
    url = filename if urlsplit(filename).scheme in ('http', 'https') else get_static_url(request, filename, absolute=True)
    return [{'format': 'image', 'url': url, 'filename': filename}]
