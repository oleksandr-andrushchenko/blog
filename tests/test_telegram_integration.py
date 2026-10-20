"""Shared-bot channel selection binds application accounts without user credentials."""
import copy
import io
import json
import os
from pathlib import Path
import sys
from html import escape
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from pyquery import PyQuery as pq

ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))
for directory in ('shared', 'api-lambda'):
    sys.path.insert(0, str(ROOT / directory))

import shared_utils
import telegram_service as service
from web import HTTPException

TOKEN = '123456789:' + 'a' * 35
SECRET = 'synthetic-webhook-secret-' + 'b' * 32
TG_USER = 56789


@pytest.fixture
def actor():
    return SimpleNamespace(id='example-creator', status='active')


@pytest.fixture
def provider(monkeypatch, actor):
    config = {'telegram_publisher_bot_token': TOKEN, 'telegram_publisher_webhook_secret': SECRET,
              'api_base_url': 'https://api.catalog.example'}
    monkeypatch.setattr(service, 'get_config', lambda: config)
    with monkeypatch.context() as templates:
        templates.setattr(shared_utils, 'get_function_templates_dir',
                          lambda: str(ROOT / 'api-lambda/templates'))
        environment = shared_utils.get_jinja2_env()
    monkeypatch.setattr(service, 'jinja2_env', lambda: environment)
    monkeypatch.setattr(service, 'find_user', lambda user_id: actor if user_id == actor.id else None)

    def call(method, **payload):
        return {
            'getMe': {'id': 123456789, 'username': 'example_bot'},
            'getChat': {'id': -100123, 'type': 'channel', 'title': 'Example channel', 'username': 'example_channel'},
            'getChatMember': {'status': 'administrator', 'can_post_messages': True},
            'sendMessage': {'message_id': 42}, 'setWebhook': True,
        }[method]
    mock = Mock(side_effect=call)
    monkeypatch.setattr(service, 'telegram_call', mock)
    return mock, config


@pytest.fixture
def storage(monkeypatch):
    records = {}
    monkeypatch.setattr(service, 'get_dynamodb_item', lambda pk, sk, **kwargs: copy.deepcopy(records.get((pk, sk))))

    def write(transacts):
        for transaction in transacts:
            if 'Put' in transaction:
                item = transaction['Put']['Item']
                records[(item['pk'], item['sk'])] = copy.deepcopy(item)
            elif 'Update' in transaction:
                update = transaction['Update']
                key = update['Key']
                item = records.setdefault((key['pk'], key['sk']), dict(key))
                for alias, field in update['ExpressionAttributeNames'].items():
                    item[field] = update['ExpressionAttributeValues'][':new_' + field]
            elif 'Delete' in transaction:
                key = transaction['Delete']['Key']
                records.pop((key['pk'], key['sk']), None)
    monkeypatch.setattr(service, 'dynamodb_transact_write', write)
    return records


def private_message(**fields):
    return {'message': {'chat': {'id': TG_USER, 'type': 'private'},
                        'from': {'id': TG_USER, 'is_bot': False}, **fields}}


def start(actor):
    result = service.begin_connection(actor, actor)
    nonce = parse_qs(urlsplit(result['connect_url']).query)['start'][0]
    service.handle_update(private_message(text='/start ' + nonce))
    return result


def selection(actor):
    pending = service._read('USER#' + actor.id, service.PENDING_KEY)
    session = service._read(*service._session_key(pending['session_hash']))
    return private_message(chat_shared={'request_id': session['request_id'], 'chat_id': -100123})


def connect(actor):
    start(actor)
    service.handle_update(selection(actor))


def test_shared_bot_connects_with_channel_picker_and_no_user_token(actor, storage, provider):
    call, _ = provider
    result = start(actor)
    assert result['connect_url'].startswith('https://t.me/example_bot?start=')
    assert len(parse_qs(urlsplit(result['connect_url']).query)['start'][0]) <= 64
    assert not any(invocation.args[0] == 'getChat' for invocation in call.call_args_list)
    picker = call.call_args.kwargs['reply_markup']['keyboard'][0][0]['request_chat']
    assert picker['chat_is_channel'] and picker['bot_administrator_rights']['can_post_messages']
    assert not picker['bot_administrator_rights']['can_delete_messages']
    assert picker['user_administrator_rights']['can_promote_members']
    update = selection(actor)
    service.handle_update(update)
    status = service.connection_status(actor, actor)
    assert status['connected'] and status['channel_id'] == '-100123'
    assert not status['pending']
    assert all(key not in str(storage) for key in (TOKEN, SECRET, 'encrypted_bot_token'))
    assert len(storage) == 2  # The connection and user flag remain, without temporary records.
    assert storage[('USER#' + actor.id, 'META')]['has_telegram_integration'] is True
    assert not any(key in status for key in ('telegram_user_id', 'bot_id', 'session_hash'))
    call.reset_mock()
    service.handle_update(update)
    call.assert_not_called()  # Replayed webhook cannot recreate or replace a connection.
    service.disconnect(actor, actor)
    assert list(storage) == [('USER#' + actor.id, 'META')]
    assert storage[('USER#' + actor.id, 'META')]['has_telegram_integration'] is False


@pytest.mark.parametrize('method', ['connection_status', 'disconnect', 'begin_connection'])
def test_only_account_owner_can_manage_connection(actor, storage, provider, method):
    other = SimpleNamespace(id='administrator', status='active', permissions=['*'])
    with pytest.raises(HTTPException) as exc:
        getattr(service, method)(actor, other)
    assert exc.value.status_code == 403
    assert not storage
    provider[0].assert_not_called()


def test_banned_owner_cannot_begin_or_share(actor, storage, provider):
    actor.status = 'banned'
    with pytest.raises(HTTPException) as exc:
        service.begin_connection(actor, actor)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        service.share_article(SimpleNamespace(status='published'), actor, None)
    provider[0].assert_not_called()


def test_expired_attempt_cannot_attach_channel(actor, storage, provider, monkeypatch):
    start(actor)
    update = selection(actor)
    original = dict(storage)
    with monkeypatch.context() as clock:
        clock.setattr(service, 'utc_now', lambda: 10**16)
        service.handle_update(update)
    assert storage == original


def test_disconnect_cancels_pending_and_start_links(actor, storage, provider):
    result = start(actor)
    update = selection(actor)
    service.disconnect(actor, actor)
    assert list(storage) == [('USER#' + actor.id, 'META')]
    assert storage[('USER#' + actor.id, 'META')]['has_telegram_integration'] is False
    service.handle_update(update)
    nonce = parse_qs(urlsplit(result['connect_url']).query)['start'][0]
    service.handle_update(private_message(text='/start ' + nonce))
    assert list(storage) == [('USER#' + actor.id, 'META')]
    assert storage[('USER#' + actor.id, 'META')]['has_telegram_integration'] is False


def test_new_attempt_invalidates_previous_picker(actor, storage, provider):
    start(actor)
    stale = selection(actor)
    result = service.begin_connection(actor, actor)
    service.handle_update(stale)
    assert not service.connection_status(actor, actor)['connected']
    assert service.connection_status(actor, actor)['pending']
    assert len(storage) == 2
    assert TOKEN not in str(result)


def test_link_cannot_be_claimed_by_another_telegram_account(actor, storage, provider):
    result = start(actor)
    nonce = parse_qs(urlsplit(result['connect_url']).query)['start'][0]
    other = private_message(text='/start ' + nonce)
    other['message']['chat']['id'] = other['message']['from']['id'] = TG_USER + 1
    before = copy.deepcopy(storage)
    service.handle_update(other)
    assert storage == before
    assert 'another Telegram account' in provider[0].call_args.kwargs['text']


@pytest.mark.parametrize('invalid', ['group', 'bot-rights', 'user-rights'])
def test_selection_verifies_channel_and_both_posting_permissions(actor, storage, provider, invalid):
    call, _ = provider
    start(actor)
    update = selection(actor)
    normal = call.side_effect

    def fail(method, **payload):
        result = normal(method, **payload)
        if method == 'getChat' and invalid == 'group':
            result['type'] = 'supergroup'
        if method == 'getChatMember' and payload['user_id'] == (TG_USER if invalid == 'user-rights' else 123456789):
            result['can_post_messages'] = False
        return result
    call.side_effect = fail
    service.handle_update(update)
    assert not service.connection_status(actor, actor)['connected']
    assert service.connection_status(actor, actor)['pending']


def test_channel_selection_is_bound_to_private_chat_and_request(actor, storage, provider):
    start(actor)
    before = copy.deepcopy(storage)
    update = selection(actor)
    update['message']['from']['id'] = TG_USER + 1
    update['message']['chat']['id'] = TG_USER + 1
    service.handle_update(update)
    update = selection(actor)
    update['message']['chat']['type'] = 'group'
    service.handle_update(update)
    update = selection(actor)
    update['message']['chat_shared']['request_id'] += 1
    service.handle_update(update)
    assert storage == before


def test_account_banned_after_start_cannot_complete(actor, storage, provider):
    start(actor)
    update = selection(actor)
    actor.status = 'banned'
    service.handle_update(update)
    assert ('USER#' + actor.id, service.INTEGRATION_KEY) not in storage


def test_share_checks_current_permissions_and_preserves_message_limits(actor, storage, provider, monkeypatch):
    connect(actor)
    call, _ = provider
    call.reset_mock()
    monkeypatch.setattr(service, 'get_article_url', lambda *args, **kwargs: 'https://catalog.example/@creator/example')
    monkeypatch.setattr(service, 'find_category', lambda slug: SimpleNamespace(name='Photography'))
    article = SimpleNamespace(status='published', title='Example', category='photography',
                             preview='<p>' + '🌻' * 5000 + '</p>',
                             content='NEVER SHARE THIS CONTENT')
    assert service.share_article(article, actor, None)['message_id'] == 42
    assert [invocation.args[0] for invocation in call.call_args_list] == ['getChatMember', 'getChatMember', 'sendMessage']
    text = call.call_args.kwargs['text']
    doc = pq('<div>' + text + '</div>')
    visible = ''.join(doc[0].itertext())
    assert len(visible.encode('utf-16-le')) // 2 <= 4096
    assert call.call_args.kwargs['parse_mode'] == 'HTML'
    assert call.call_args.kwargs['link_preview_options'] == {'url': 'https://catalog.example/@creator/example'}
    assert text.startswith('<b>📚 New article in Photography</b>\n\n<b>Example</b>\n\n🌻')
    assert doc('a').text() == 'Read article 👇'
    assert doc('a').attr('href') == 'https://catalog.example/@creator/example'
    assert 'NEVER SHARE THIS CONTENT' not in text
    assert '<p>' not in text
    call.reset_mock()
    article.status = 'unpublished'
    with pytest.raises(HTTPException) as exc:
        service.share_article(article, actor, None)
    assert exc.value.status_code == 404
    call.assert_not_called()
    article.status = 'published'
    call.side_effect = lambda *args, **kwargs: {'status': 'left'}
    with pytest.raises(HTTPException):
        service.share_article(article, actor, None)
    assert all(invocation.args[0] != 'sendMessage' for invocation in call.call_args_list)


def test_webhook_secret_is_required_and_compared_without_exposing_it(provider):
    _, config = provider
    service.verify_webhook(SECRET)
    for value in (None, 'wrong', 'é'):
        with pytest.raises(HTTPException) as exc:
            service.verify_webhook(value)
        assert exc.value.status_code == 403
        assert SECRET not in str(exc.value)
    config['telegram_publisher_webhook_secret'] = ''
    with pytest.raises(HTTPException) as exc:
        service.verify_webhook(SECRET)
    assert exc.value.status_code == 503


def test_webhook_configuration_is_dry_run_and_redacts_secrets(provider):
    call, _ = provider
    plan = service.configure_webhook()
    assert not plan['applied'] and TOKEN not in str(plan) and SECRET not in str(plan)
    assert plan['url'] == 'https://api.catalog.example/integrations/telegram/webhook'
    call.assert_not_called()
    service.configure_webhook(apply=True)
    assert call.call_args.args == ('setWebhook',)
    assert call.call_args.kwargs['secret_token'] == SECRET
    assert call.call_args.kwargs['allowed_updates'] == ['message']


@pytest.mark.parametrize('error,status', [
    (HTTPError('secret-url', 401, 'secret-token', {}, io.BytesIO()), 422),
    (HTTPError('secret-url', 429, 'secret-token', {}, io.BytesIO()), 429),
    (HTTPError('secret-url', 500, 'secret-token', {}, io.BytesIO()), 502),
    (URLError('secret-token'), 502),
])
def test_provider_errors_do_not_expose_credentials(monkeypatch, provider, error, status):
    monkeypatch.setattr(service, 'telegram_call', telegram_transport)
    monkeypatch.setattr(service, 'urlopen', Mock(side_effect=error))
    with pytest.raises(HTTPException) as exc:
        service.telegram_call('getMe')
    assert exc.value.status_code == status
    assert 'secret' not in exc.value.detail and TOKEN not in exc.value.detail


telegram_transport = service.telegram_call


def test_dynamodb_connection_consumption_and_disconnect_race(actor, provider, monkeypatch):
    """Exercise actual DynamoDB transaction conditions, including a disconnect during verification."""
    import uuid
    from test_utils import dynamodb_table, recreate_dynamodb_table

    try:
        dynamodb_table.load()
    except dynamodb_table.meta.client.exceptions.ResourceNotFoundException:
        recreate_dynamodb_table()
    actor.id = 'telegram-test-' + str(uuid.uuid4())
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table', lambda: dynamodb_table)
    monkeypatch.setattr(shared_utils, 'get_dynamodb_table_name', lambda: dynamodb_table.name)
    requests = []

    def transport(request, timeout):
        method = request.full_url.rsplit('/', 1)[-1]
        payload = json.loads(request.data)
        requests.append((method, payload))
        result = provider[0](method, **payload)
        return io.BytesIO(json.dumps({'ok': True, 'result': result}).encode())

    monkeypatch.setattr(service, 'telegram_call', telegram_transport)
    monkeypatch.setattr(service, 'urlopen', transport)
    user_key = {'pk': 'USER#' + actor.id, 'sk': 'META'}
    dynamodb_table.put_item(Item={**user_key, 'id': actor.id, 'name': 'Example', 'created_at': 1})
    try:
        start(actor)
        assert not dynamodb_table.get_item(Key=user_key, ConsistentRead=True)['Item'].get('has_telegram_integration', False)
        update = selection(actor)
        service.handle_update(update)
        assert dynamodb_table.get_item(Key=user_key, ConsistentRead=True)['Item']['has_telegram_integration'] is True
        status = service.connection_status(actor, actor)
        assert status['connected']
        assert isinstance(json.loads(json.dumps(status))['verified_at'], int)
        picker = next(payload for method, payload in requests
                      if method == 'sendMessage' and 'keyboard' in payload.get('reply_markup', {}))
        request_id = picker['reply_markup']['keyboard'][0][0]['request_chat']['request_id']
        assert isinstance(request_id, int)
        assert request_id == update['message']['chat_shared']['request_id']
        assert all(isinstance(payload['user_id'], int) for method, payload in requests if method == 'getChatMember')
        service.handle_update(update)
        assert service.connection_status(actor, actor)['connected']
        service.disconnect(actor, actor)
        assert not service.connection_status(actor, actor)['connected']

        start(actor)
        update = selection(actor)
        verify = service._verify_posting

        def disconnect_during_verification(*args):
            verify(*args)
            service.disconnect(actor, actor)
        monkeypatch.setattr(service, '_verify_posting', disconnect_during_verification)
        service.handle_update(update)
        assert not service.connection_status(actor, actor)['connected']
        assert not service.connection_status(actor, actor)['pending']
        assert dynamodb_table.get_item(Key=user_key, ConsistentRead=True)['Item']['has_telegram_integration'] is False
    finally:
        service.disconnect(actor, actor)
        dynamodb_table.delete_item(Key=user_key)


def test_webhook_rejects_unauthenticated_body_before_parsing(provider):
    import asyncio
    import importlib.util
    from web import Request

    spec = importlib.util.spec_from_file_location('telegram_api_tests', ROOT / 'api-lambda/app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reads = []

    async def receive():
        reads.append(True)
        return {'type': 'http.request', 'body': b'not-json'}

    def request(secret):
        return Request({'type': 'http', 'method': 'POST', 'path': '/integrations/telegram/webhook',
                        'headers': [(b'x-telegram-bot-api-secret-token', secret.encode())]}, receive)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.telegram_webhook(request('wrong')))
    assert exc.value.status_code == 403
    assert not reads
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.telegram_webhook(request(SECRET)))
    assert exc.value.status_code == 400
    assert reads == [True]


def test_storage_outages_are_not_treated_as_connection_conflicts(monkeypatch):
    from botocore.exceptions import ClientError

    error = ClientError({'Error': {'Code': 'TransactionCanceledException', 'Message': 'Temporary failure'},
                         'CancellationReasons': [{'Code': 'ProvisionedThroughputExceeded'}]}, 'TransactWriteItems')
    monkeypatch.setattr(service, 'dynamodb_transact_write', Mock(side_effect=error))
    with pytest.raises(ClientError):
        service._write([])


def test_user_connection_flag_defaults_for_legacy_records():
    user = {'id': 'example', 'name': 'Example', 'created_at': 1}
    assert shared_utils.user_from_dynamodb(user).has_telegram_integration is False
    assert shared_utils.user_from_dynamodb({**user, 'has_telegram_integration': True}).has_telegram_integration is True


def test_share_flag_does_not_replace_connection_authorization(actor, storage, provider):
    actor.has_telegram_integration = True
    with pytest.raises(HTTPException) as exc:
        service.share_article(SimpleNamespace(status='published'), actor, None)
    assert exc.value.status_code == 409
    provider[0].assert_not_called()


def test_telegram_onboarding_transaction_has_backend_iam_permissions(actor, storage, provider, monkeypatch):
    """DynamoDB Local does not enforce IAM; check the deployed role against actual operations."""
    import yaml

    class CloudFormationLoader(yaml.SafeLoader):
        pass

    def intrinsic(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    CloudFormationLoader.add_multi_constructor('!', intrinsic)
    resources = yaml.load((ROOT / 'cf.yml').read_text(), Loader=CloudFormationLoader)['Resources']
    role_reference = resources['ApiFunction']['Properties']['Role']
    role = resources[role_reference.removesuffix('.Arn')]['Properties']
    allowed = set()
    for policy in role['Policies']:
        for statement in policy['PolicyDocument']['Statement']:
            actions = statement['Action']
            actions = actions if isinstance(actions, list) else [actions]
            targets = statement['Resource']
            targets = targets if isinstance(targets, list) else [targets]
            if statement['Effect'] == 'Allow' and 'DynamoDBTable.Arn' in targets:
                allowed.update(actions)
                if 'dynamodb:ConditionCheckItem' in actions:
                    assert targets == ['DynamoDBTable.Arn']

    result = service.begin_connection(actor, actor)
    nonce = parse_qs(urlsplit(result['connect_url']).query)['start'][0]
    write = Mock(wraps=service.dynamodb_transact_write)
    monkeypatch.setattr(service, 'dynamodb_transact_write', write)
    service._start_connection(private_message()['message'], nonce)
    operations = {operation for transaction in write.call_args.args[0] for operation in transaction}
    assert 'ConditionCheck' in operations
    permission = {'Put': 'dynamodb:PutItem', 'Update': 'dynamodb:UpdateItem',
                  'Delete': 'dynamodb:DeleteItem', 'ConditionCheck': 'dynamodb:ConditionCheckItem'}
    assert {permission[operation] for operation in operations} <= allowed


@pytest.mark.parametrize('category,display_name,heading', [
    ('code-dev', 'Code & Dev', '📚 New article in Code & Dev'),
    ('new-category', None, '📚 New article in New Category'),
    (None, None, '📚 New article in Other'),
])
def test_telegram_share_description_preview_and_category_fallback(actor, storage, provider, monkeypatch,
                                                               category, display_name, heading):
    connect(actor)
    monkeypatch.setattr(service, 'find_category', lambda slug: SimpleNamespace(name=display_name) if display_name else None)
    monkeypatch.setattr(service, 'get_article_url', lambda *args, **kwargs: 'https://catalog.example/article')
    article = SimpleNamespace(status='published', title='Example article', category=category,
                             preview='<p>A <strong>useful</strong> description &amp; example.</p>')
    service.share_article(article, actor, None)
    assert provider[0].call_args.kwargs['text'] == (
        '<b>' + escape(heading) + '</b>\n\n<b>Example article</b>\n\nA useful description &amp; example.\n\n'
        '<a href="https://catalog.example/article">Read article 👇</a>')
    article.preview = ''
    service.share_article(article, actor, None)
    assert provider[0].call_args.kwargs['text'] == (
        '<b>' + escape(heading) + '</b>\n\n<b>Example article</b>\n\n'
        '<a href="https://catalog.example/article">Read article 👇</a>')


def test_telegram_html_escapes_content_and_truncates_before_escaping(actor, storage, provider, monkeypatch):
    connect(actor)
    url = 'https://catalog.example/article?ref=1&source=2'
    monkeypatch.setattr(service, 'find_category', lambda slug: SimpleNamespace(name='Art & <Design>'))
    monkeypatch.setattr(service, 'get_article_url', lambda *args, **kwargs: url)
    description = 'Use <b>literal tags</b> & symbols 🌻. ' * 1000
    article = SimpleNamespace(status='published', category='art-illustration', title='A & B <img src="x">',
                             preview='<p>' + escape(description) + '</p>')
    service.share_article(article, actor, None)
    payload = provider[0].call_args.kwargs
    text = payload['text']
    doc = pq('<div>' + text + '</div>')
    visible = ''.join(doc[0].itertext())
    assert payload['parse_mode'] == 'HTML'
    assert len(doc('b')) == 2
    assert not doc('img, script, design')
    assert doc('b').eq(0).text() == '📚 New article in Art & <Design>'
    assert doc('b').eq(1).text() == 'A & B <img src="x">'
    assert 'Use <b>literal tags</b> & symbols 🌻.' in visible
    assert len(visible.encode('utf-16-le')) // 2 <= 4096
    assert len(text.encode('utf-16-le')) // 2 > 4096  # Escaped entities/markup do not consume visible text capacity.
    assert '…\n\nRead article 👇' in visible
    assert doc('a').attr('href') == url
    assert text.endswith('Read article 👇</a>')
