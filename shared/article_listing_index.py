"""Stable ordering metadata for sparse published tag-listing indexes."""
TAG_RATING_INDEX = 'TAG_COMBOS_BY_RATING'
TAG_LISTING_READY_KEY = ('CATALOG_INDEX#tag-listings', 'META')


def tag_rating_key(rating, article_id):
    return f'{int(rating) + 10 ** 38:039d}#{article_id}'


def tag_listing_fields(article):
    return {'post_id': article.id, 'tag_rating_sk': tag_rating_key(article.rating, article.id),
            'created_at': int(article.created_at), 'category': article.category or 'other'}
