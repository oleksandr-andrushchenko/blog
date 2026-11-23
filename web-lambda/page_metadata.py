"""Web-owned page copy, composed from already loaded article/catalog/profile data."""
import re

from app_config import config, get_publisher_enabled
from query_dtos import ArticleStatus, ArticleQueryType, TagQueryType, UserQueryType, UserStatus
from shared_utils import get_html_content, html_to_text


def description_summary(*parts, target=190):
    """Prefer complete sentences; the reading budget is not a search-engine limit."""
    sentences = []
    for part in parts:
        text = ' '.join(html_to_text(part or '').split())
        if not text:
            continue
        if not re.search(r'[.!?…][\"\u201d\u2019\)\]]?$', text):
            text = text.rstrip(' ,;:') + '.'
        for sentence in re.split(r'(?<=[.!?…])\s+', text):
            if sentences and len(' '.join([*sentences, sentence])) > target:
                return ' '.join(sentences)
            sentences.append(sentence)
    return ' '.join(sentences)


def count_prefix(count):
    """Only prefix known matching totals, never a page size or an inferred count."""
    if isinstance(count, (bool, str)) or count is None:
        return ''
    try:
        value = int(count)
        return f'{value:,}+ ' if value == count and value > 15 else ''
    except (ValueError, TypeError, OverflowError):
        return ''


def metadata_title(heading, *, count=None):
    return f"{count_prefix(count)}{heading} | {config['site_name']}"


def simple_metadata(title, *, subtitle='', description=''):
    return {'title': metadata_title(title), 'heading': title, 'subtitle': subtitle,
            'description': description}


def get_tags_page_metadata(query):
    if query is not None and query.prefix:
        result = simple_metadata('Search Topics',
            subtitle='Find topics by prefix, in alphabetical order',
            description='Search system design topics by prefix to find engineering concepts and related articles.')
        result['title'] = metadata_title('Search System Design Topics')
        return result
    popular = query is not None and query.type == TagQueryType.POPULAR
    heading = 'Popular Topics' if popular else 'Recently Added Topics'
    result = simple_metadata(heading,
        subtitle=('Explore popular topics to find articles about the systems and engineering decisions that interest you'
                  if popular else 'Explore newly added topics to find articles about the systems and engineering decisions that interest you'),
        description=('Explore popular system design topics and discover articles about backend engineering, architecture, and production systems.'
                     if popular else 'Explore newly added system design topics and discover articles about backend engineering, architecture, and production systems.'))

    result['title'] = metadata_title('Popular System Design Topics' if popular else 'Recently Added System Design Topics')
    return result


def get_categories_page_metadata():
    result = simple_metadata('Categories',
        subtitle='Choose an engineering area to explore architecture, databases, distributed systems, and production practices',
        description='Find system design articles by category, including architecture, databases, distributed systems, cloud infrastructure, and reliability.')

    result['title'] = metadata_title('System Design Articles by Category')
    return result


def get_users_page_metadata(query):
    if query.status != UserStatus.ACTIVE:
        return simple_metadata('Banned Users', subtitle='Browse banned user profiles', description='Browse banned user profiles.')
    popular = query.type == UserQueryType.POPULAR
    result = simple_metadata('Popular Users' if popular else 'Latest Users',
        subtitle=('Explore popular community members, visit their profiles, and discover the articles they share'
                  if popular else 'Meet the newest members and explore their profiles and shared articles'),
        description=('Discover popular members of the system design community and explore their public profiles and articles.'
                     if popular else 'Meet the newest members of the system design community and explore their public profiles and articles.'))

    result['title'] = metadata_title('Popular System Design Community Members' if popular else 'System Design Community')
    return result


def get_articles_page_metadata(context):
    query = context['article_query']
    category = context.get('category')
    tag = context.get('tag')
    names = context.get('article_query_tag_names') or query.tags
    names = [name if name != slug else slug.title() for name, slug in zip(names, query.tags)]
    topic = ' and '.join(names)
    category_name = category.name if category else (query.category.title() if query.category else None)
    category_context = category_name if category_name and topic else None
    subject = topic or category_name
    order = 'Popular' if query.type == ArticleQueryType.POPULAR else None
    status = query.status.title() if query.status != ArticleStatus.PUBLISHED else None
    heading = ' '.join(value for value in (order, subject, status, 'Articles') if value)
    if not subject and not status and not order:
        heading = 'Recently Added Articles'
    if category_context:
        heading += f' in {category_context}'
    single_entity = (category if category and not query.tags else
                     tag if len(query.tags) == 1 and not query.category else None)
    guide = html_to_text(getattr(single_entity, 'description', ''))
    if not guide and category:
        guide = html_to_text(category.description)
    saved_meta = html_to_text(getattr(single_entity, 'meta_description', ''))
    scope = (f'Articles about {subject}' if subject else
             'System design articles ranked by community feedback' if order else
             'The newest articles about system design, backend engineering, and production systems')
    if category_context:
        scope += f' in {category_context}'
    subtitle = guide or (scope if subject else
                        'Browse system design articles ranked by community feedback' if order else
                        'Explore the newest articles about architecture, databases, distributed systems, and production practices')
    result = simple_metadata(heading, subtitle=subtitle,
                             description=description_summary(saved_meta or scope, guide if not saved_meta else ''))
    count = None
    if query.status == ArticleStatus.PUBLISHED:
        if category and not query.tags:
            count = getattr(category, 'published_articles_count', None)
        elif tag and len(query.tags) == 1 and not query.category:
            count = getattr(tag, 'articles_count', None)
    result['title'] = metadata_title(heading, count=count)
    return result


def page_metadata_for(template, context):
    user = context.get('user')
    article = context.get('article')
    if template == 'tags.html':
        return get_tags_page_metadata(context.get('tags_query'))
    if template == 'categories.html':
        return get_categories_page_metadata()
    if template == 'articles.html':
        return get_articles_page_metadata(context)
    if template == 'users.html':
        return get_users_page_metadata(context['user_query'])
    if template == 'user.html':
        identity = user.name + (f' (@{user.username})' if user.username else '')
        query = context['article_query']
        count = user.published_articles_count if query.status == ArticleStatus.PUBLISHED else None
        intro = f'{count_prefix(count)}Articles by {identity}' if count else f"View {identity} on {config['site_name']}"
        result = simple_metadata(user.name, description=description_summary(intro, user.headline, user.about))
        result['title'] = metadata_title(f'Articles by {user.name}' if count else user.name, count=count)
        return result
    if template == 'article.html':
        return simple_metadata(article.title, description=description_summary(article.preview or article.title))
    if template == 'direct-conversation.html':
        names = context['peer'].name.split()
        return simple_metadata(f'Messages with {names[0] if names else "Member"}')
    if template == 'earn.html':
        platforms = [name for name in ('Telegram', 'Threads', 'LinkedIn', 'Pinterest')
                     if get_publisher_enabled(name.lower())]
        audience = ' Share your work on ' + ' and '.join(platforms) + '.' if platforms else ''
        return simple_metadata('For Creators', description=(
            f"Publish articles on {config['site_name']}, grow your audience, and invite reader support." + audience))
    pages = {
        'new-article.html': ('New Article', 'Share a technical article with the explanations and examples readers need'),
        'edit-article.html': (f'Edit {article.title}' if article else 'Edit Article', 'Update your explanations, examples, and article details'),
        'edit-category.html': (f"Edit {context['category'].name}" if context.get('category') else 'Edit Category', 'Set the category name, description, and search summary'),
        'edit-tag.html': (f"Edit {context['tag'].name}" if context.get('tag') else 'Edit Topic', 'Update the topic name, description, and search summary'),
        'edit-user.html': (f'Edit {user.name}' if user else 'Edit Profile', 'Manage public profile details and account settings'),
        'contacts.html': ('Contacts', 'Ask a question, report a problem, or share an idea with the team'),
        'utils.html': ('Utils', 'Manage sitemaps, cached assets, and redirects'),
        'bookmarks.html': ('Bookmarks', 'Your private saved articles, with the most recently saved first'),
        'notification-center.html': ('Notifications', ''),
        'direct-messages.html': ('Messages', ''),
        'auth-complete.html': ('Completing Sign-In', 'Finish your sign-in action'),
        'policy.html': ('Privacy Policy', ''),
        'rules.html': ('Community Rules & Content Publishing Guidelines', ''),
        'terms.html': ('Terms of Service', ''),
        'account-deletion.html': ('Account Deletion', ''),
        'account-deletion-status.html': ('Account Deletion', ''),
    }
    if template == 'index.html':
        return simple_metadata('System Design Articles', subtitle=config.get('site_desc', ''),
                               description=description_summary(config.get('site_desc')))
    title, subtitle = pages.get(template, (config['site_name'], ''))
    descriptions = {
        'contacts.html': f"Contact {config['site_name']} to ask questions, share feedback, discuss partnerships, or request support. Use our contact form to report issues and reach the team.",
        'policy.html': f"Read the {config['site_name']} privacy policy to learn how account information, cookies, analytics, and submitted content are handled.",
        'rules.html': f"Read the {config['site_name']} community rules for technical articles and discussions, including accuracy, AI use, source credit, privacy, reporting, and moderation.",
        'terms.html': f"Review the {config['site_name']} terms of service, including account responsibilities, publishing rules, content ownership, and content reuse.",
        'bookmarks.html': 'Your private saved articles, ready to revisit.',
    }
    return simple_metadata(title, subtitle=subtitle, description=descriptions.get(template, description_summary(subtitle)))


def render_page(template, context):
    """Page metadata performs no persistence or provider requests."""
    return get_html_content(template, {**context, 'page_metadata': page_metadata_for(template, context)})
