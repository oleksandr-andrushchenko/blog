"""Catalog copy is editable, separate from metadata, and backfilled with review guards."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for folder in ('shared', 'api-lambda', 'web-lambda', 'scripts'):
    sys.path.insert(0, str(ROOT / folder))

from article_dtos import UpdateTagDTO
from category_dtos import UpdateCategoryDTO
from enrich_catalog_descriptions import apply_record, proposal
from shared_utils import tag_from_dynamodb, category_from_dynamodb, get_dynamodb_table
from page_metadata import get_articles_page_metadata, get_tags_page_metadata, description_summary
from query_dtos import ArticleQueryDTO, TagQueryDTO

COPY = {'description': 'Understand data placement and consistency across replicas',
        'meta_description': 'Explore database replication, recovery, and consistency trade-offs.'}


@pytest.mark.parametrize('dto', [UpdateTagDTO, UpdateCategoryDTO])
@pytest.mark.parametrize('field', ['description', 'meta_description'])
@pytest.mark.parametrize('value', [None, '<script>bad</script>', 'bad\x00text', 'x' * 1001])
def test_catalog_copy_rejects_invalid_values(dto, field, value):
    with pytest.raises(ValueError):
        dto(**{field: value})


@pytest.mark.parametrize('dto', [UpdateTagDTO, UpdateCategoryDTO])
def test_catalog_copy_can_be_cleared_or_omitted(dto):
    assert dto(description='', meta_description='').get_changes() == {'description': '', 'meta_description': ''}
    assert dto().get_changes() == {}
    assert dto(description='  Привіт 世界  ').description == 'Привіт 世界'
    assert dto(meta_description='x' * 1000).meta_description == 'x' * 1000


def test_saved_copy_round_trips_and_old_records_remain_compatible():
    tag = tag_from_dynamodb({'tag_name_sk': 'replication', 'rating_sk': 1, **COPY})
    category = category_from_dynamodb('data', {'name': 'Data', **COPY})
    assert tag.description == category.description == COPY['description']
    assert tag.meta_description == category.meta_description == COPY['meta_description']
    assert tag_from_dynamodb({'tag_name_sk': 'old', 'rating_sk': 1}).meta_description == ''
    assert category_from_dynamodb('old', {'name': 'Old'}).meta_description == ''


def test_collection_uses_visible_description_and_separate_search_summary():
    tag = tag_from_dynamodb({'tag_name_sk': 'replication', 'rating_sk': 1, **COPY})
    result = get_articles_page_metadata({'article_query': ArticleQueryDTO(tags=[tag.slug]), 'tag': tag})
    assert result['subtitle'] == COPY['description']
    assert result['description'] == COPY['meta_description']
    assert result['heading'] == 'Replication Articles'
    category = category_from_dynamodb('data', {'name': 'Data', **COPY})
    result = get_articles_page_metadata({'article_query': ArticleQueryDTO(category='data'), 'category': category})
    assert result['subtitle'] == COPY['description']
    assert result['description'] == COPY['meta_description']
    intersection = get_articles_page_metadata({'article_query': ArticleQueryDTO(category='data', tags=['replication']),
                                              'category': category, 'tag': tag})
    assert intersection['description'] != COPY['meta_description']
    assert 'Replication' in intersection['description'] and 'Data' in intersection['description']


def test_recent_topic_copy_and_complete_sentence_summary():
    result = get_tags_page_metadata(TagQueryDTO())
    assert result['heading'] == 'Recently Added Topics'
    assert result['subtitle'] != result['heading']
    sentence = 'An explanation ' + 'with context ' * 25 + 'and its conclusion.'
    assert description_summary(sentence, 'Extra detail.') == sentence
    assert description_summary('<p>First.</p><p>Second.</p>') == 'First. Second.'


@pytest.mark.parametrize('kind', ['category', 'tag'])
def test_backfill_dry_run_apply_resume_and_concurrency_against_isolated_dynamodb(kind):
    table = get_dynamodb_table()
    key = {'pk': 'CATEGORY', 'sk': 'catalog-copy-test'} if kind == 'category' else {'pk': 'POST_TAG#catalog-copy-test', 'sk': 'META'}
    item = {**key, 'name': 'Reviewed name', 'description': 'Existing introduction',
            'updated_at': 123, 'created_at': 100, 'posts_count': 8, 'rating_sk': 10}
    try:
        table.put_item(Item=item)
        record = proposal(kind, item)
        record['changes'] = {'meta_description': COPY['meta_description']}
        assert apply_record(table, record) == 'preview'
        assert table.get_item(Key=key, ConsistentRead=True)['Item'] == item
        assert apply_record(table, record, apply=True) == 'applied'
        assert table.get_item(Key=key, ConsistentRead=True)['Item'] == {**item, **record['changes']}
        assert apply_record(table, record, apply=True) == 'already_applied'
        table.put_item(Item={**item, 'name': 'Concurrent edit'})
        assert apply_record(table, record) == 'changed_since_review'
        assert apply_record(table, record, apply=True) == 'changed_since_review'
        assert 'meta_description' not in table.get_item(Key=key, ConsistentRead=True)['Item']
    finally:
        table.delete_item(Key=key)


def test_backfill_atomic_guard_handles_edit_between_read_and_write():
    item = {'pk': 'CATEGORY', 'sk': 'data', 'name': 'Data'}
    record = proposal('category', item)
    record['changes'] = COPY
    table = Mock()
    table.get_item.return_value = {'Item': item}
    table.update_item.side_effect = ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem')
    assert apply_record(table, record, apply=True) == 'changed_since_review'
    params = table.update_item.call_args.kwargs
    assert 'attribute_exists(#pk)' in params['ConditionExpression']
    assert set(params['ExpressionAttributeNames'].values()) == {'pk', 'name', 'description', 'meta_description'}


def test_backfill_refuses_replacements_and_unrelated_fields():
    record = proposal('category', {'pk': 'CATEGORY', 'sk': 'data', 'description': 'Existing'})
    for changes in ({'description': 'Replacement'}, {'name': 'Different'}):
        with pytest.raises(ValueError):
            apply_record(Mock(), {**record, 'changes': changes}, apply=True)


def test_apply_cli_requires_explicit_flag_and_reports_conflicts(monkeypatch, tmp_path, capsys):
    import enrich_catalog_descriptions as command
    record = proposal('category', {'pk': 'CATEGORY', 'sk': 'data'})
    record['changes'] = COPY
    path = tmp_path / 'plan.jsonl'
    path.write_text(json.dumps(record) + '\n')
    table = Mock()
    table.get_item.return_value = {'Item': record['snapshot']}
    monkeypatch.setattr(command, 'bootstrap', Mock(return_value=table))
    monkeypatch.setattr(sys, 'argv', ['backfill', '--apply-plan', str(path)])
    command.main()
    table.update_item.assert_not_called()
    assert json.loads(capsys.readouterr().out.splitlines()[0])['output']['result'] == 'preview'
    table.get_item.return_value = {'Item': {**record['snapshot'], 'name': 'Edited'}}
    monkeypatch.setattr(sys, 'argv', ['backfill', '--apply-plan', str(path), '--apply'])
    with pytest.raises(SystemExit) as error:
        command.main()
    assert error.value.code == 1
    table.update_item.assert_not_called()


def test_category_creation_accepts_separate_copy_and_validates_before_writes(monkeypatch):
    import api_utils
    from types import SimpleNamespace
    monkeypatch.setattr(api_utils, 'verify_authorization', Mock())
    monkeypatch.setattr(api_utils, 'find_category', Mock(return_value=None))
    monkeypatch.setattr(api_utils, 'dynamodb_transact_write', Mock())
    owner = SimpleNamespace(id='owner', status='active')
    result = api_utils.create_category('catalog-copy-new', 'New category', COPY['description'], owner,
                                       meta_description=COPY['meta_description'])
    assert result.description == COPY['description']
    assert result.meta_description == COPY['meta_description']
    api_utils.dynamodb_transact_write.reset_mock()
    with pytest.raises(ValueError):
        api_utils.create_category('catalog-copy-new', 'New category', 'Valid', owner,
                                  meta_description='<b>Invalid</b>')
    api_utils.dynamodb_transact_write.assert_not_called()


@pytest.mark.parametrize('count,expected', [(None, ''), (True, ''), ('200', ''), (0, ''), (15, ''), (16, '16+ '), (1200, '1,200+ ')])
def test_search_title_counts_use_known_totals_only(count, expected):
    from page_metadata import count_prefix
    assert count_prefix(count) == expected


def test_catalog_counts_never_leak_to_intersections_or_private_status():
    category = category_from_dynamodb('data', {'name': 'Data', 'published_articles_count': 200, **COPY})
    tag = tag_from_dynamodb({'tag_name_sk': 'replication', 'rating_sk': 1, 'posts_count': 100, **COPY})
    for query, expected in [(ArticleQueryDTO(category='data'), '200+ '),
                            (ArticleQueryDTO(tags=['replication']), '100+ '),
                            (ArticleQueryDTO(category='data', tags=['replication']), ''),
                            (ArticleQueryDTO(category='data', status='unpublished'), '')]:
        result = get_articles_page_metadata({'article_query': query, 'category': category if query.category else None,
                                             'tag': tag if query.tags else None})
        assert result['title'].startswith(expected or result['heading'])
        assert not result['heading'][0].isdigit()
        if query.category and query.tags:
            assert result['description'] != COPY['meta_description']
        else:
            assert result['description'] == COPY['meta_description']


def test_topic_search_and_profile_search_titles():
    from page_metadata import page_metadata_for
    from types import SimpleNamespace
    search = get_tags_page_metadata(TagQueryDTO(prefix='api'))
    assert search['heading'] == 'Search Topics'
    assert search['title'].startswith('Search System Design Topics')
    user = SimpleNamespace(name='A Writer', username='writer', published_articles_count=200, headline='Engineer', about='')
    profile = page_metadata_for('user.html', {'user': user, 'article_query': ArticleQueryDTO()})
    assert profile['title'].startswith('200+ Articles by A Writer')
    private = page_metadata_for('user.html', {'user': user, 'article_query': ArticleQueryDTO(status='unpublished')})
    assert not private['title'].startswith('200+')
    assert '200+' not in private['description']


def test_creator_summary_mentions_only_available_providers(monkeypatch):
    import page_metadata
    monkeypatch.setattr(page_metadata, 'get_publisher_enabled', lambda provider: provider == 'pinterest')
    result = page_metadata.page_metadata_for('earn.html', {})
    assert 'Pinterest' in result['description']
    assert 'Threads' not in result['description'] and 'Telegram' not in result['description']
