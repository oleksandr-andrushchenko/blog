import json
import os
import re
from pathlib import Path
from typing import Any


def _get_live_config() -> dict[str, Any]:
    return {
        "app_stage": os.getenv("APP_STAGE"),
        "app_env": os.getenv("APP_ENV"),
        "app_debug": os.getenv("APP_DEBUG"),
        "app_secret": os.getenv("APP_SECRET"),
        "web_base_url": os.getenv("WEB_BASE_URL"),
        "api_base_url": os.getenv("API_BASE_URL"),
        "static_base_url": os.getenv("STATIC_BASE_URL"),
        "aws_region": os.getenv("AWS_REGION"),
        "dynamodb_endpoint": os.getenv("DYNAMODB_ENDPOINT"),
        "dynamodb_table": os.getenv("DYNAMODB_TABLE"),
        "google_analytics_id": os.getenv("GOOGLE_ANALYTICS_ID"),
        "google_search_console_credentials_secret_arn": os.getenv("GOOGLE_SEARCH_CONSOLE_CREDENTIALS_SECRET_ARN"),
        "google_search_console_site_url": os.getenv("GOOGLE_SEARCH_CONSOLE_SITE_URL"),
        "bing_webmaster_api_key": os.getenv("BING_WEBMASTER_API_KEY"),
        "yandex_webmaster_oauth_token": os.getenv("YANDEX_WEBMASTER_OAUTH_TOKEN"),
        "yandex_webmaster_user_id": os.getenv("YANDEX_WEBMASTER_USER_ID"),
        "yandex_webmaster_host_id": os.getenv("YANDEX_WEBMASTER_HOST_ID"),
        "indexnow_key": os.getenv("INDEXNOW_KEY"),
        "tinymce_api_key": os.getenv("TINYMCE_API_KEY"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "telegram_log_level": os.getenv("TELEGRAM_LOG_LEVEL", "INFO"),
        "contact_topic_arn": os.getenv("CONTACT_TOPIC_ARN"),
        "ses_from_email": os.getenv("SES_FROM_EMAIL"),
        "allowed_origin": os.getenv("ALLOWED_ORIGIN"),
        "cognito_domain": os.getenv("COGNITO_DOMAIN"),
        "cognito_client_id": os.getenv("COGNITO_CLIENT_ID"),
        "cognito_client_secret": os.getenv("COGNITO_CLIENT_SECRET"),
        "cognito_user_pool_id": os.getenv("COGNITO_USER_POOL_ID"),
        "email_files_dir": os.getenv("EMAIL_FILES_DIR", "/app-emails"),
        "static_files_dir": os.getenv("STATIC_FILES_DIR", "/app-static"),
        "function_templates_dir": os.getenv("FUNCTION_TEMPLATES_DIR", ""),
        "static_s3_bucket": os.getenv("STATIC_S3_BUCKET"),
        "cloudfront_distribution_id": os.getenv("CLOUDFRONT_DISTRIBUTION_ID"),
        "css_cache_counter": os.getenv("CSS_CACHE_COUNTER", 0),
        "js_cache_counter": os.getenv("JS_CACHE_COUNTER", 0),
        "auth_token_max_age": os.getenv("AUTH_TOKEN_MAX_AGE", 86_400 * 7),
        "auth_jwt_secret": os.getenv("AUTH_JWT_SECRET"),
        "permission_hierarchy": {
            "regular": [
                "update_user_impression",
                "create_post",
                "toggle_post_impression",
                "create_post_comment",
                "create_contact_message",
            ],
            "root": ["*"],
        },
        "default_avatar_colors": {
            "A": "#F44336", "B": "#E91E63", "C": "#9C27B0", "D": "#673AB7",
            "E": "#3F51B5", "F": "#2196F3", "G": "#03A9F4", "H": "#00BCD4",
            "I": "#009688", "J": "#4CAF50", "K": "#8BC34A", "L": "#CDDC39",
            "M": "#FFEB3B", "N": "#FFC107", "O": "#FF9800", "P": "#FF5722",
            "Q": "#795548", "R": "#9E9E9E", "S": "#607D8B", "T": "#FF1744",
            "U": "#D500F9", "V": "#00E676", "W": "#00B0FF", "X": "#FFD600",
            "Y": "#FF6D00", "Z": "#C51162",
        },
        **json.load(open(Path(__file__).with_name("data.default.json"))),
        **json.load(open(Path(__file__).with_name("data.json"))),
    }


config = _get_live_config()


def get_config() -> dict[str, Any]:
    return config


def is_prod() -> bool:
    return config.get("app_stage") == "prod"


def get_static_files_dir() -> str:
    return config.get("static_files_dir") or ""


def get_function_templates_dir() -> str:
    return config.get("function_templates_dir") or ""


def get_web_base_url() -> str:
    return config.get("web_base_url") or ""


def get_api_base_url() -> str:
    return config.get("api_base_url") or ""


def get_static_base_url() -> str:
    return config.get("static_base_url") or ""


def get_indexnow_key() -> str:
    key = config.get("indexnow_key") or ""
    if key and not re.fullmatch(r"[A-Za-z0-9-]{8,128}", key):
        raise ValueError("INDEXNOW_KEY must contain 8-128 letters, numbers, or dashes")
    return key


def get_google_search_console_credentials_secret_arn() -> str:
    return config.get("google_search_console_credentials_secret_arn") or ""


def get_google_search_console_site_url() -> str:
    return config.get("google_search_console_site_url") or get_web_base_url().rstrip('/') + '/'


def get_bing_webmaster_api_key() -> str:
    return config.get("bing_webmaster_api_key") or ""


def get_yandex_webmaster_oauth_token() -> str:
    return config.get("yandex_webmaster_oauth_token") or ""


def get_yandex_webmaster_user_id() -> str:
    return config.get("yandex_webmaster_user_id") or ""


def get_yandex_webmaster_host_id() -> str:
    return config.get("yandex_webmaster_host_id") or ""


def get_aws_region() -> str | None:
    return config.get("aws_region")


def get_dynamodb_endpoint() -> str | None:
    return config.get("dynamodb_endpoint")


def get_dynamodb_table_name() -> str | None:
    return config.get("dynamodb_table")


def get_allowed_origins() -> list[str]:
    return [config.get("allowed_origin", "")]


def get_cognito_domain() -> str | None:
    return config.get("cognito_domain")


def get_cognito_client_id() -> str | None:
    return config.get("cognito_client_id")


def get_cognito_client_secret() -> str | None:
    return config.get("cognito_client_secret")


def get_cognito_user_pool_id() -> str | None:
    return config.get("cognito_user_pool_id")


def get_permission_hierarchy() -> dict[str, list[str]]:
    return config.get("permission_hierarchy", "")


def get_auth_token_max_age() -> int:
    return int(config.get("auth_token_max_age", ""))


def get_auth_jwt_secret() -> str | None:
    return config.get("auth_jwt_secret")


def get_email_files_dir() -> str:
    return config.get("email_files_dir", "")


def get_contact_topic_arn() -> str | None:
    return config.get("contact_topic_arn")


def get_ses_from_email() -> str | None:
    return config.get("ses_from_email")


def get_static_s3_bucket() -> str | None:
    return config.get("static_s3_bucket")


def get_cloudfront_distribution_id() -> str | None:
    return config.get("cloudfront_distribution_id")


def get_telegram_bot_token() -> str:
    return (config.get("telegram_bot_token") or "").strip()


def get_telegram_chat_id() -> str:
    return (config.get("telegram_chat_id") or "").strip()


def get_telegram_log_level() -> str:
    return (config.get("telegram_log_level") or "INFO").upper()
