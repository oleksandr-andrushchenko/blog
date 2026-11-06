"""Card/detail metadata and accessible image descriptions share consistent presentation."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pyquery import PyQuery as pq
import pytest

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'shared'))

from query_dtos import ArticleStatus


@pytest.fixture
def templates():
    env = Environment(loader=FileSystemLoader(ROOT / 'shared/templates'), autoescape=select_autoescape())
    env.filters['iso_utc'] = lambda timestamp: str(timestamp)
    env.filters['unix_to_full_date'] = lambda timestamp: str(timestamp)
    env.globals.update(
        ArticleStatus=ArticleStatus,
        articles_url=lambda **filters: '/articles?' + urlencode(filters, doseq=True),
        article_url=lambda article: '/articles/' + article.id,
        user_url=lambda user: '/users/' + user.id,
        static_url=lambda filename: '/static/' + filename,
    )
    return env


@pytest.mark.parametrize('extended', [False, True])
def test_metadata_preserves_category_case_and_limits_only_compact_tags(templates, extended):
    creator = SimpleNamespace(id='author', name='A Creator')
    category = SimpleNamespace(slug='api-design', name='API Design')
    article = SimpleNamespace(id='example', category=category.slug, category_name=category.name,
        tags=['API', 'Python', 'system-design'],
        created_at=1000, published_at=2000, updated_at=3000, status='published',
        likes_count=2000, dislikes_count=1, reports_count=0, comments_count=0, get_user=lambda: creator)
    context = dict(article=article, article_user=creator, author=creator, extended=extended)
    html = ''.join(templates.get_template('fragments/' + name + '.html').render(**context)
                   for name in ('article-badges', 'article-meta'))
    doc = pq(html)
    assert doc('a[href="/articles?category=api-design"]').text() == 'API Design'
    assert doc('a[href="/articles?tags=API"]').text() == 'API'
    assert bool(doc('a[href="/articles?tags=Python"]')) is extended
    assert bool(doc('a[title="View all 3 tags"]:contains("+2")')) is not extended
    assert doc('a[href="/users/author"]').text() == creator.name
    assert ('Published on' in doc.text()) is extended
    assert ('Modified on' in doc.text()) is extended
    assert doc('[title="Likes count"]').text() == ('2,000 Likes' if extended else '2,000')
    assert not doc('[title="Reports count"]')


def test_card_badge_uses_saved_name_and_escapes_it_without_catalog_reads(templates):
    def unexpected_lookup():
        raise AssertionError('Card rendering must not query categories')

    templates.globals['get_categories'] = unexpected_lookup
    article = SimpleNamespace(id='example', category='api-design', category_name='API <Design> & "guide"', tags=[])
    template = templates.get_template('fragments/article-badges.html')
    doc = pq(template.render(article=article, category=SimpleNamespace(slug='api-design', name='New name')))
    badge = doc('a[href="/articles?category=api-design"]')
    assert badge.text() == article.category_name
    assert not badge('design')
    assert badge.attr('title') == 'Browse articles in ' + article.category_name
    # The collection wrapper must not add a lookup even for an empty result page.
    assert not templates.get_template('fragments/articles.html').render(articles=[]).strip()


@pytest.mark.parametrize('context,expected', [
    ({}, 'api design'),
    ({'categories': [SimpleNamespace(slug='api-design', name='API Design')]}, 'API Design'),
])
def test_legacy_card_badges_fall_back_without_fetching_categories(templates, context, expected):
    article = SimpleNamespace(id='legacy', category='api-design', tags=[])
    doc = pq(templates.get_template('fragments/article-badges.html').render(article=article, **context))
    assert doc('a').text() == expected


@pytest.mark.parametrize('caption,expected', [(None, 'Diagram description'),
    ('Visible explanation <script>plain text</script>', 'Visible explanation <script>plain text</script>'), ('', '')])
def test_image_caption_can_differ_from_alt_text_with_legacy_fallback(templates, caption, expected):
    context = {'filename': 'diagram_640x320.png', 'alt': 'Diagram description', 'figure': True}
    if caption is not None:
        context['caption'] = caption
    doc = pq(templates.get_template('fragments/image.html').render(**context))
    assert doc('img').attr('alt') == 'Diagram description'
    assert doc('figcaption').text() == expected
    assert not doc('figcaption script')
