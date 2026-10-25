"""Typed creator links and common validation for affiliate and support destinations."""
from dataclasses import asdict, dataclass, field
import re
from typing import TypeVar
from urllib.parse import urlsplit

from validation import validate_catalog_text


@dataclass(slots=True)
class CreatorLink:
    id: str
    name: str
    url: str
    text: str = ""
    clicks_count: int = 0
    default_all_articles: bool = True
    default_categories: list[str] = field(default_factory=list)
    show_on_user_page: bool = True


@dataclass(slots=True)
class SupportLink(CreatorLink):
    pass


LinkType = TypeVar('LinkType', bound=CreatorLink)


def validate_link_url(value, *, label='Link'):
    if value is None:
        return None
    validate_catalog_text(value)
    if (any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char.isspace() for char in value.strip()) or "\\" in value):
        raise ValueError(f'{label} must be a valid HTTPS URL')
    value = value.strip()
    if not value:
        return None
    if len(value) > 2048:
        raise ValueError(f'{label} is too long')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or '.' not in parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)):
        raise ValueError(f'{label} must be a valid HTTPS URL without login details')
    return value


def validate_link_ids(values, *, field_name):
    if not isinstance(values, list) or any(
            not isinstance(value, str) or not re.fullmatch(r'[a-z0-9-]{1,64}', value) for value in values):
        raise ValueError(f'{field_name} must be a list of link IDs')
    return list(dict.fromkeys(values))


def validate_links(values: list[dict | LinkType], *, link_type: type[LinkType], kind: str) -> list[LinkType]:
    if not isinstance(values, list):
        raise ValueError(f'{kind}_links must be a list')
    if len(values) > 20:
        raise ValueError("At most 20 support links are allowed")
    result, seen = [], set()
    for value in values:
        if isinstance(value, link_type):
            value = asdict(value)
            value.pop('clicks_count')
        if not isinstance(value, dict) or set(value) - {'id', 'name', 'text', 'url', 'default_all_articles', 'default_categories', 'show_on_user_page'}:
            raise ValueError(f'Each {kind} link needs an ID, name, button text, and URL')
        link_id = value.get('id')
        validate_link_ids([link_id], field_name=f'{kind}_links')
        if link_id in seen:
            raise ValueError('Link IDs must be unique within each list')
        seen.add(link_id)
        name = validate_catalog_text(value.get('name')).strip()
        text = validate_catalog_text(value.get('text', '')).strip()
        if not 1 <= len(name) <= 100 or len(text) > 150:
            raise ValueError('Link names must contain 1–100 characters and button text at most 150')
        url = validate_link_url(value.get('url'), label=f'{kind.title()} link')
        if not url:
            raise ValueError(f'Each {kind} link needs a full HTTPS URL')
        all_articles, user_page = value.get('default_all_articles', True), value.get('show_on_user_page', True)
        if type(all_articles) is not bool or type(user_page) is not bool:
            raise ValueError('Link default options must be true or false')
        categories = validate_link_ids(value.get('default_categories', []), field_name='default_categories')
        result.append(link_type(id=link_id, name=name, text=text, url=url,
                                default_all_articles=all_articles, default_categories=categories,
                                show_on_user_page=user_page))
    return result
