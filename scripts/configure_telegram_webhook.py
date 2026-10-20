"""Review publisher bot webhook registration; use --apply to register it.

Run through scripts-production with API_BASE_URL set to the deployed HTTPS API.
Credentials are read from configuration and never included in review output.
"""
import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Register the webhook')
    args = parser.parse_args()
    try:
        from app_config import is_prod
        if not is_prod():
            raise ValueError('Webhook configuration requires APP_STAGE=prod')
        from telegram_service import configure_webhook
        print(json.dumps({'input': {'service': 'telegram'}, 'output': configure_webhook(apply=args.apply)}))
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'reason': str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
