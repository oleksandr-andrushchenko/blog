"""Run sitemap generation outside the browser request's time limit."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import boto3

from app_config import get_config
from sitemap_handler import get_web_request
from shared_utils import Permission, get_static_url, get_user, logger, verify_authorization

_local_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sitemap")


def submit_sitemap(user, request):
    verify_authorization(user, Permission.GENERATE_SITEMAP)
    event = {"task": "generate-sitemap", "payload": {"actor_user_id": user.id}}
    function_name = get_config().get("sitemap_function_name")
    if function_name:
        response = boto3.client("lambda").invoke(
            FunctionName=function_name, InvocationType="Event", Payload=json.dumps(event).encode(),
        )
        if response["StatusCode"] != 202:
            raise RuntimeError("Sitemap worker did not accept the request")
    elif get_config().get("app_stage") == "local":
        _local_workers.submit(run_sitemap_event, event)
    else:
        raise RuntimeError("SITEMAP_FUNCTION_NAME is required for sitemap generation")
    return {"status": "accepted", "sitemap_url": get_static_url(request, "sitemap.xml", absolute=True)}


def run_sitemap_event(event):
    if not isinstance(event, dict) or event.get("task") != "generate-sitemap":
        raise ValueError("Unknown sitemap task")
    payload = event.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("actor_user_id"), str) or not payload[
        "actor_user_id"]:
        raise ValueError("Sitemap task requires an actor_user_id")
    from api_utils import generate_sitemap

    actor_user_id = payload["actor_user_id"]
    started = time.monotonic()
    logger.info("Sitemap generation started: actor=%s", actor_user_id)
    try:
        # Reload the actor and recheck permission in generate_sitemap before any writes.
        count, url = generate_sitemap(get_user(actor_user_id), get_web_request())
    except Exception:
        logger.exception("Sitemap generation failed: actor=%s duration_seconds=%.2f",
                         actor_user_id, time.monotonic() - started)
        raise
    logger.info("Sitemap generation completed: actor=%s urls_count=%s duration_seconds=%.2f",
                actor_user_id, count, time.monotonic() - started, extra={"context": url})
    return {"urls_count": count, "sitemap_url": url}
