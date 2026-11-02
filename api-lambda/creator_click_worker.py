"""Acknowledge authenticated clicks before counter writes and email delivery."""
from concurrent.futures import ThreadPoolExecutor
import json
import re

import boto3
from botocore.config import Config

from app_config import config
from sitemap_handler import get_web_request
from shared_utils import logger
from web import HTTPException

_local_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='creator-click')


def dispatch_creator_click(payload):
    event = {'task': 'creator-link-click', 'payload': payload}
    function_name = config.get('background_function_name')
    if function_name:
        client = boto3.client('lambda', config=Config(
            connect_timeout=2, read_timeout=5, retries={'total_max_attempts': 1},
        ))
        response = client.invoke(FunctionName=function_name, InvocationType='Event',
                                 Payload=json.dumps(event).encode())
        if response['StatusCode'] != 202:
            raise HTTPException(503, 'Click worker did not accept the request')
    elif config.get('app_stage') == 'local':
        _local_workers.submit(run_creator_click_event, event)
    else:
        raise HTTPException(503, 'Click worker is unavailable')


def run_creator_click_event(event):
    if not isinstance(event, dict) or event.get('task') != 'creator-link-click':
        raise ValueError('Unknown creator click task')
    payload = event.get('payload')
    if (not isinstance(payload, dict) or payload.get('kind') != 'support'
            or any(not isinstance(payload.get(key), str) or not payload[key]
                   for key in ('owner_id', 'link_id'))
            or not isinstance(payload.get('event_id'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', payload['event_id'])
            or any(payload.get(key) is not None and (not isinstance(payload[key], str) or not payload[key])
                   for key in ('article_id', 'actor_id'))):
        raise ValueError('Invalid creator click payload')
    from api_utils import process_creator_link_click

    try:
        process_creator_link_click(payload, get_web_request())
    except HTTPException as error:
        if error.status_code not in (403, 404):
            raise
        # The link, account or article can become unavailable after enqueueing.
        logger.debug('Creator click skipped: event=%s status=%s', payload['event_id'], error.status_code)
    except Exception:
        logger.exception('Creator click processing failed: event=%s', payload['event_id'])
        raise
