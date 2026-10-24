"""Validation for creator support links and per-article selections."""
from creator_link_models import SupportLink, validate_link_ids, validate_links


def validate_support_links(values: list[dict | SupportLink]) -> list[SupportLink]:
    return validate_links(values, link_type=SupportLink, kind='support')


def validate_support_providers(values: list[str]) -> list[str]:
    return validate_link_ids(values, field_name='support_providers')
