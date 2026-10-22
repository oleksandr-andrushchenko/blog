"""API email delivery through SES, with local messages saved for development."""
from email.message import EmailMessage
from functools import lru_cache
from pathlib import Path
import uuid

from app_config import get_aws_region, get_email_files_dir, get_ses_from_email, is_prod
from shared_utils import logger, utc_now


@lru_cache
def get_ses_client():
    import boto3
    return boto3.client('ses', region_name=get_aws_region())


def send_email(recipient: str, subject: str, text_body: str, html_body: str) -> None:
    sender = get_ses_from_email()
    if is_prod():
        if not sender:
            logger.warning('Email notification skipped: SES_FROM_EMAIL is not configured')
            return
        get_ses_client().send_email(
            Source=sender, Destination={'ToAddresses': [recipient]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {
                    'Text': {'Data': text_body, 'Charset': 'UTF-8'},
                    'Html': {'Data': html_body, 'Charset': 'UTF-8'},
                },
            },
        )
        return

    message = EmailMessage()
    message['From'] = sender or 'no-reply@localhost'
    message['To'] = recipient
    message['Subject'] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype='html')
    directory = Path(get_email_files_dir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{utc_now()}-{uuid.uuid4()}.eml'
    path.write_bytes(message.as_bytes())
    logger.info('Saved development email to %s', path)
