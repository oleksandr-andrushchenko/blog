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
    article = SimpleNamespace(id='example', category=category.slug, tags=['API', 'Python', 'system-design'],
        created_at=1000, published_at=2000, updated_at=3000, status='published',
        likes_count=2, dislikes_count=1, get_user=lambda: creator)
    html = templates.get_template('fragments/article-meta.html').render(
        article=article, article_user=creator, author=creator, metadata_categories=[category], extended=extended)
    doc = pq(html)
    assert doc('a[href="/articles?category=api-design"]').text() == 'API Design'
    assert doc('a[href="/articles?tags=API"]').text() == 'API'
    assert bool(doc('a[href="/articles?tags=Python"]')) is extended
    assert bool(doc('a[title="View all 3 tags"]:contains("+2")')) is not extended
    assert doc('a[href="/users/author"]').text() == creator.name
    assert ('Published on' in doc.text()) is extended
    assert ('Modified on' in doc.text()) is extended


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
