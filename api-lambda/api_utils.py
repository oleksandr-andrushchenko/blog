from article_dtos import (
    ArticleCommentDTO, ArticleDTO, UpdateArticleCommentDTO, UpdateArticleDTO, UpdateArticleImpressionDTO,
    UpdateArticleStatusDTO, UpdateArticleTagDTO,
)
from article_tag_subscription_dtos import ArticleTagSubscriptionDTO
from basic_dtos import ContactMessageDTO, FileDTO
from shared_utils import *
from user_dtos import (
    UpdateUserDTO, UpdateUserImpressionDTO, UpdateUserStatusDTO,
    UpdateUserActivitySettingsDTO, UpdateUserInterestsSettingsDTO,
    UserImpressionAction,
)


def drop_cdn_cache(user: User) -> tuple[bool, int]:
    verify_authorization(user, Permission.DROP_CDN_CACHE)
    res = _drop_cdn_cache()
    return res.get("success"), res.get("items_count")


def _drop_cdn_cache(*urls) -> dict[str, Any]:
    items = set()
    for u in urls:
        if isinstance(u, str):
            items.add(u)
        elif isinstance(u, (list, tuple, set)):
            items.update(u)
        else:
            raise TypeError(f"Unsupported type: {type(u)}")

    # Resolve paths
    if items:
        paths = []
        for p in items:
            if not isinstance(p, str):
                raise TypeError(f"Invalid path type: {type(p)} (expected str)")

            if not p.startswith("/"):
                raise ValueError(f"Invalid CloudFront path (must start with '/'): {p}")

            paths.append(p)

        if len(paths) > 3000:
            raise ValueError("CloudFront supports max 3000 paths per invalidation request")
    else:
        paths = ["/*"]

    if not is_prod():
        return {
            "success": True,
            "invalidation_id": "",
            "status": "InProgress",
            "items_count": len(paths),
        }

    client = _get_cf_client()
    distribution_id = get_cf_distribution_id()
    response = client.create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "Paths": {
                "Quantity": len(paths),
                "Items": paths,
            },
            "CallerReference": str(uuid.uuid4()),
        },
    )

    metadata = response.get("ResponseMetadata", {})
    invalidation = response.get("Invalidation", {})

    return {
        "success": metadata.get("HTTPStatusCode") == 201,
        "invalidation_id": invalidation.get("Id"),
        "status": invalidation.get("Status"),
        "items_count": len(paths),
    }


def safe_execute(label: str, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.warning(f"{label} failed: {e}")
        return None


def generate_sitemap(user: User, req) -> tuple[int, str]:
    verify_authorization(user, Permission.GENERATE_SITEMAP)

    today = datetime.utcnow().date().isoformat()

    def lastmod(ts_ms, fallback_ts_ms=None):
        ts_ms = ts_ms or fallback_ts_ms
        if not ts_ms:
            return today
        return datetime.fromtimestamp(
            float(ts_ms) / 1000,
            tz=timezone.utc
        ).date().isoformat()

    urls = []

    # Static
    def url(route: str) -> str:
        return get_url(req, route, True)

    urls.extend([
        (url("index"), today),
        (url("contacts"), today),
        (url("rules"), today),
        (url("terms"), today),
        (url("earn"), today),
    ])

    # Post lists
    def articles_url(tp: ArticleQueryType, tg: ArticleTag | None = None) -> str:
        return get_articles_url(req, type=tp, tags=[tg.slug] if tg else [], full=True)

    for type_ in ArticleQueryType:
        urls.append((articles_url(type_), today))
        for tag in get_article_tags(ArticleTagQueryDTO(limit=1000)):
            if tag.articles_count > 0:
                urls.append((articles_url(type_, tag), today))

    # Posts
    def article_url(article: Article) -> str:
        return get_article_url(req, article, full=True)

    offset = None
    while articles := get_latest_articles(ArticleQueryDTO(status=ArticleStatus.PUBLISHED, limit=1000, offset=offset)):
        urls.extend([(article_url(article), lastmod(article.updated_at, article.created_at)) for article in articles])
        offset = articles[-1].offset
        if not offset:
            break

    # User lists
    def users_url(tp: UserQueryType) -> str:
        return get_users_url(req, type=tp, full=True)

    for type_ in UserQueryType:
        urls.append((users_url(type_), today))

    # Users
    def user_url(user_: User) -> str:
        return get_user_url(req, user_, full=True)

    offset = None
    while users := get_latest_users(
            UserQueryDTO(status=UserStatus.ACTIVE, limit=1000, offset=offset)):
        urls.extend([(user_url(user), lastmod(user.updated_at, user.created_at)) for user in users])
        offset = users[-1].offset
        if not offset:
            break

    # Save
    sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{''.join([f"""<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>""" for (loc, lastmod) in urls])}</urlset>"""

    sitemap_filename = save_public_file(
        FileDTO(content=sitemap_xml.encode("utf-8"), filename="sitemap.xml"),
        filename="sitemap.xml",
    )
    sitemap_url = get_static_url(req, sitemap_filename, full=True)

    # Invalidate CDN cache
    if is_prod():
        safe_execute("CF invalidation", _drop_cdn_cache, ["/sitemap.xml"])

    # Notify engines
    if is_prod():
        import httpx
        with httpx.Client(timeout=5.0) as client:
            safe_execute("Google SM notify", client.get, "https://www.google.com/ping", params={"sitemap": sitemap_url})
            safe_execute("Bing SM notify", client.get, "https://www.bing.com/ping", params={"sitemap": sitemap_url})

    return len(urls), sitemap_url


def create_dummy_fixtures(req) -> None:
    import random
    if is_prod():
        return
    created_articles = []
    created_users = []
    user_token = get_dummy_user_token()
    root_user = upsert_user_by_user_token(user_token)
    created_users.append(root_user)
    update_dynamodb_item((f"USER#{root_user.id}", "META"), {"permissions": [Permission.ROOT]})
    root_user.permissions = [Permission.ROOT]
    update_user_dto = UpdateUserDTO(
        name="John Doe",
        avatar_action="replace",
        avatar_filename="5b027ec7-c018-4744-9eda-00abf75cf685_1111x712.png",
        username="j-doe",
        headline="Software Engineer",
        website="https://example.com",
        about=("Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the "
               "industry's standard dummy text ever since the 1500s, when an unknown printer took a galley of type "
               "and scrambled it to make a type specimen book. It has survived not only five centuries, but also the "
               "leap into electronic typesetting, remaining essentially unchanged. It was popularised in the 1960s "
               "with the release of Letraset sheets containing Lorem Ipsum passages, and more recently with desktop "
               "publishing software like Aldus PageMaker including versions of Lorem Ipsum."),
        address="1600 Pennsylvania Ave NW, Washington, DC 20500"
    )
    update_user(root_user, update_user_dto, root_user, req)
    user_token3 = get_dummy_user_token(sub="p3", email="test3@example.com")
    user3 = upsert_user_by_user_token(user_token3)
    created_users.append(user3)
    update_user(user3, UpdateUserDTO(
        name=user3.name,
        avatar_action="replace",
        avatar_filename="6a5118c0-a073-483b-ac5b-79e0a554e703_988x494.png",
    ), root_user, req)
    user3.avatar_filename = "6a5118c0-a073-483b-ac5b-79e0a554e703_988x494.png"
    user_token4 = get_dummy_user_token(sub="p4", email="test4@example.com")
    user4 = upsert_user_by_user_token(user_token4)
    created_users.append(user4)
    update_user(user4, UpdateUserDTO(
        name=user4.name,
        avatar_action="replace",
        avatar_filename="5b027ec7-c018-4744-9eda-00abf75cf685_1111x712.png",
    ), root_user, req)
    user4.avatar_filename = "5b027ec7-c018-4744-9eda-00abf75cf685_1111x712.png"
    create_article_tag_subscription(ArticleTagSubscriptionDTO(tags=["tag3"]), root_user)
    create_article_tag_subscription(ArticleTagSubscriptionDTO(tags=["tag1"]), user3)
    create_article_tag_subscription(ArticleTagSubscriptionDTO(tags=["tag2", "tag3"]), user4)

    generated_image_filenames = [
        "3d7af01f-819e-4c2f-bc69-eb7245b76a74_1809x1247.png",
        "45e97e68-a321-4657-9956-e942d9d757a7_1279x518.png",
        "5b027ec7-c018-4744-9eda-00abf75cf685_1111x712.png",
        "6a5118c0-a073-483b-ac5b-79e0a554e703_988x494.png",
        "a167891d-7e91-40d6-a5c4-1a3ddb27dcc2_1575x842.png",
    ]

    def random_figure(alt: str) -> str:
        if random.random() >= 0.3:
            return ""
        filename = random.choice(generated_image_filenames)
        return f'<img src="/{filename}" alt="{alt}">'

    articles = [
        ArticleDTO(
            title="Article title #111111111111111111111111",
            content=random_figure(
                "Message Queues Explained: Producers, Consumers, and Brokers") + "<p>Article content #111111111111111111111111" * 150 + "</p>",
            tags=["tag1", "tag2", "tag3"]
        ),
        ArticleDTO(
            title="Article title #22222222222222222222222",
            content=random_figure(
                "Event-Driven Architecture: Connecting Services with Events") + "<p>Article content #222222222222222222222222" * 150 + "</p>",
            tags=["tag2", "tag3"]
        ),
        ArticleDTO(
            title="Article title #3333333333333333333333333",
            content=random_figure(
                "Designing Reliable Distributed Systems") + "<p>Article content #33333333333333333333333" * 150 + "</p>",
            tags=["tag1", "tag3"]
        ),
    ]
    for article in articles:
        created_article = create_article(article, root_user)
        update_article_status(created_article, UpdateArticleStatusDTO(status=ArticleStatus.PUBLISHED), root_user, req)
        created_articles.append(created_article)
    user_token2 = get_dummy_user_token(sub="p2", email="test2@example.com", name="Some test user")
    user2 = upsert_user_by_user_token(user_token2)
    created_users.append(user2)
    update_user(user2, UpdateUserDTO(
        name=user2.name,
        avatar_action="replace",
        avatar_filename="6a5118c0-a073-483b-ac5b-79e0a554e703_988x494.png",
    ), root_user, req)
    user2.avatar_filename = "6a5118c0-a073-483b-ac5b-79e0a554e703_988x494.png"
    articles = [
        ArticleDTO(
            title="Article title #111111111111111111111111 for user 2",
            content=random_figure(
                "Scaling Systems: From a Single Service to a Platform") + "<p>Article content #111111111111111111111111" * 150 + "</p>",
            tags=["tag3"]
        ),
        ArticleDTO(
            title="Article title #22222222222222222222222 for user 2",
            content=random_figure(
                "Article title #22222222222222222222222 for user 2") + "<p>Article content #222222222222222222222222" * 150 + "</p>",
            tags=["tag2"]
        ),
        ArticleDTO(
            title="Article title #3333333333333333333333333 for user 2",
            content=random_figure(
                "Article title #3333333333333333333333333 for user 2") + "<p>Article content #33333333333333333333333" * 150 + "</p>",
            tags=["tag4"]
        ),
    ]
    for article in articles:
        created_article = create_article(article, user2)
        update_article_status(created_article, UpdateArticleStatusDTO(status=ArticleStatus.PUBLISHED), root_user, req)
        created_articles.append(created_article)

    # Add enough published articles to exercise sitemap generation with a larger dataset.
    # Keep the six deterministic articles above unchanged, then bring the total to 75 articles.
    for article_index in range(len(created_articles), 75):
        generated_article = create_article(ArticleDTO(
            title=f"Generated Fixture Article #{article_index + 1:03d} for Sitemap Testing",
            content=random_figure("Generated fixture article")
                    + f"<p>Generated fixture article content #{article_index + 1:03d}.</p>"
                      "This article exists to exercise article creation, publication, pagination, "
                      "and sitemap generation with a larger local dataset. " * 120,
            tags=["tag1", "tag2"] if article_index % 2 else ["tag3"],
        ), root_user)
        update_article_status(
            generated_article,
            UpdateArticleStatusDTO(status=ArticleStatus.PUBLISHED),
            root_user,
            req,
        )
        created_articles.append(generated_article)

    for tag_name, image_filename in [("tag1", "45e97e68-a321-4657-9956-e942d9d757a7_1279x518.png"),
                                     ("tag2", "a167891d-7e91-40d6-a5c4-1a3ddb27dcc2_1575x842.png")]:
        article_tag = find_article_tag(tag_name)
        update_article_tag(article_tag, UpdateArticleTagDTO(
            name=tag_name,
            image_action="replace",
            image_filename=image_filename,
        ), root_user, req)
        article_tag.image_filename = image_filename

    comment_texts = [
        "This helped clarify the trade-offs. Thanks for writing it.",
        "Good walkthrough. I would like to see more examples around scaling this design.",
        "The section about operational limits is especially useful.",
        "Nice article. The diagrams and constraints make the approach easier to follow.",
    ]
    for article_index, article in enumerate(created_articles):
        commenters = [user for user in created_users if user.id != article.owner_id]
        for comment_index, user in enumerate(commenters[:2]):
            text = comment_texts[(article_index + comment_index) % len(comment_texts)]
            create_article_comment(article, ArticleCommentDTO(text=text), user, req)

    # Seed deterministic article feedback so every rating state is visible in local development.
    # Each tuple is (likes, dislikes), limited by the number of dummy users.
    article_feedback_patterns = [
        (0, 0),  # unrated
        (1, 0),  # five stars from one positive vote
        (3, 1),  # mostly positive
        (1, 3),  # mostly negative
        (2, 2),  # neutral
        (4, 0),  # fully positive
    ]
    for article, (likes_count, dislikes_count) in zip(created_articles, article_feedback_patterns):
        for user in created_users[:likes_count]:
            update_article_impression(article, UpdateArticleImpressionDTO(
                action=ArticleImpressionAction.LIKE), user, req)
        for user in created_users[likes_count:likes_count + dislikes_count]:
            update_article_impression(article, UpdateArticleImpressionDTO(
                action=ArticleImpressionAction.DISLIKE), user, req)

    for user in created_users:
        for user2 in created_users:
            if user.id != user2.id:
                update_user_impression(user, UpdateUserImpressionDTO(
                    action=UserImpressionAction.FOLLOW if random.random() < .5 else UserImpressionAction.BLOCK), user2,
                                       req)
    unpublished_articles = [
        ArticleDTO(
            title="Unpublished Article title #111111111111111111111111",
            content="Article content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        ArticleDTO(
            title="Unpublished Article title #22222222222222222222222",
            content="Article content #2222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        ArticleDTO(
            title="Unpublished Article title #3333333333333333333333333",
            content="Article content #333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for article in unpublished_articles:
        create_article(article, user2)
    rejected_articles = [
        ArticleDTO(
            title="Rejected Article title #111111111111111111111111",
            content="Article content #111111111111111111111111" * 150,
            tags=["tag1", "tag2", "tag3"]
        ),
        ArticleDTO(
            title="Rejected Article title #22222222222222222222222",
            content="Article content #2222222222222222222222" * 150,
            tags=["tag2", "tag3"]
        ),
        ArticleDTO(
            title="Rejected Article title #3333333333333333333333333",
            content="Article content #333333333333333333333" * 150,
            tags=["tag1", "tag3"]
        ),
    ]
    for article in rejected_articles:
        created_article = create_article(article, user3)
        update_article_status(created_article,
                              UpdateArticleStatusDTO(status=ArticleStatus.REJECTED, comment="Some rejection reason"),
                              root_user, req)


def create_article_tag_subscription(dto: ArticleTagSubscriptionDTO, user: User) -> ArticleTagSubscription:
    article_tag_subscription_id, now, key = str(uuid.uuid4()), utc_now(), article_tag_subscription_key(dto.tags)
    transacts = []
    add_dynamodb_put_transact(transacts, (f"USER#{user.id}", f"ARTICLE_TAG_SUBSCRIPTION#{article_tag_subscription_id}"),
                              {"article_tag_subscription_id": article_tag_subscription_id, "user_id": user.id,
                               "tags": dto.tags, "article_tag_subscription_key": key, "created_at": now})
    add_dynamodb_put_transact(transacts, (f"ARTICLE_TAG_SUBSCRIBERS#{key}", f"USER#{user.id}"),
                              {"user_id": user.id, "article_tag_subscription_id": article_tag_subscription_id,
                               "article_tag_subscription_key": key, "created_at": now}, new_pk_only=True)
    add_dynamodb_user_update_transact(transacts, user, deltas={"article_tag_subscriptions_count": 1})
    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as exc:
        if exc.is_conditional():
            raise SlugDuplicationError("Article tag subscription already exists", "tags")
        raise
    return ArticleTagSubscription(article_tag_subscription_id, user.id, dto.tags, now)


def delete_article_tag_subscription(article_tag_subscription_id: str, user: User) -> ArticleTagSubscription:
    item = get_dynamodb_item(f"USER#{user.id}", f"ARTICLE_TAG_SUBSCRIPTION#{article_tag_subscription_id}")
    if not item:
        raise UserNotFoundError("Article tag subscription not found")
    subscription = article_tag_subscription_from_dynamodb(item)
    key = item["article_tag_subscription_key"]
    transacts = []
    add_dynamodb_delete_transact(
        transacts, (f"USER#{user.id}", f"ARTICLE_TAG_SUBSCRIPTION#{article_tag_subscription_id}")
    )
    add_dynamodb_delete_transact(
        transacts, (f"ARTICLE_TAG_SUBSCRIBERS#{key}", f"USER#{user.id}")
    )
    add_dynamodb_user_update_transact(
        transacts, user, deltas={"article_tag_subscriptions_count": -1}
    )
    dynamodb_transact_write(transacts)
    return subscription


def update_article_tag(article_tag: ArticleTag, update_article_tag_dto: UpdateArticleTagDTO, cur_user: User,
                       req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_TAG, article_tag)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_article_tag_dto.get_changes(article_tag)
    if not changes:
        return

    now = utc_now()

    new_name = changes.pop("name", None)
    if new_name is not None:
        new_name = new_name.strip()
        if new_name != article_tag.name:
            changes["name"] = new_name

    image_action = changes.pop("image_action", "keep")

    if image_action == "delete":
        changes["image_filename"] = None
    elif image_action == "keep":
        changes.pop("image_filename", None)

    if not changes:
        return

    old_image = article_tag.image_filename
    old_slug = article_tag.slug
    slug = to_kebab_case(changes["name"]) if "name" in changes else old_slug
    slug_changed = slug != old_slug
    transacts = []

    if slug_changed:
        old_item = get_dynamodb_item(f"POST_TAG#{old_slug}", "META")
        if old_item is None:
            raise ArticleTagNotFoundError(f"Article tag '{old_slug}' not found")

        new_item = {k: v for k, v in old_item.items() if k not in {"pk", "sk"}}
        new_item.update(changes)
        new_item["tag_name_sk"] = slug
        new_item["updated_at"] = now

        redirect_item = {
            "tag_name_sk": old_slug,
            "redirect_to": slug,
            "created_at": now,
        }
        add_dynamodb_put_transact(transacts, (f"POST_TAG_REDIRECT#{old_slug}", "META"), redirect_item, new_pk_only=True)
        add_dynamodb_put_transact(transacts, (f"POST_TAG#{slug}", "META"), new_item, new_pk_only=True)
        add_dynamodb_delete_transact(transacts, (f"POST_TAG#{old_slug}", "META"))

        for article in get_latest_articles_by_tags(ArticleQueryDTO(tags=[old_slug], limit=1000)):
            old_tags = list(article.tags)
            tags = list(dict.fromkeys(slug if tag == old_slug else tag for tag in old_tags))

            add_delete_article_tag_combos_transact(transacts, article, old_slug)
            add_dynamodb_article_update_transact(transacts, article, {"tags": tags})
            add_put_article_tag_combos_transact(transacts, article, slug)
    else:
        add_dynamodb_article_tag_update_transact(transacts, article_tag, changes)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="name")
        raise

    if "name" in changes:
        article_tag.name = changes["name"]
    if slug_changed:
        article_tag.slug = slug
    if "image_filename" in changes:
        article_tag.image_filename = changes["image_filename"]

    if old_image and image_action in {"delete", "replace"}:
        drop_public_file(old_image)


def save_public_file(file_dto: FileDTO, filename: str = None) -> str:
    if not filename:
        file_ext = file_dto.extension
        filename = str(uuid.uuid4())
        if isinstance(file_dto, ImageFileDTO):
            try:
                width, height = get_image_dimensions(file_dto.content)
                filename += f"_{width}x{height}"
            except ValueError:
                pass
        filename += f".{file_ext}"

    if not is_prod():
        filepath = os.path.join(get_static_files_dir(), filename)
        with open(filepath, "wb") as f:
            f.write(file_dto.content)
        return filename
    from io import BytesIO
    stream = BytesIO(file_dto.content)
    stream.seek(0)

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    get_s3_client().upload_fileobj(
        stream,
        get_static_s3_bucket(),
        filename,
        ExtraArgs={
            "ContentType": content_type,
            "ContentDisposition": "inline",
        },
    )
    return filename


def drop_public_file(filename: str) -> None:
    if not is_prod():
        # filepath = os.path.join(get_static_files_dir(), filename)
        # if os.path.exists(filepath):
        #     os.remove(filepath)
        return

    get_s3_client().delete_object(Bucket=get_static_s3_bucket(), Key=filename)


def create_article(article_dto: ArticleDTO, cur_user: User) -> Article:
    verify_authorization(cur_user, Permission.CREATE_ARTICLE)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    now = utc_now()
    status = ArticleStatus.UNPUBLISHED
    article_id = str(uuid.uuid4())
    title = article_dto.title
    content = sanitize_forbidden_html(article_dto.content)
    preview = find_preview(content)
    image_filename = find_static_image_filename(content)
    tags = sanitize_tags(article_dto.tags)
    slug = to_kebab_case(title)

    transacts = []

    article_item = {
        "id": article_id,
        "title": title,
        "post_slug": slug,
        "user_id": cur_user.id,
        "user_name": cur_user.name,
        "content": content,
        "tags": tags,
        "rating_sk": compute_rating_sk(0, now),
        "status": status,
        "created_at": now,
        "post_status_pk": f"POST#{status}",
        "post_user_status_pk": f"POST#{cur_user.id}#{status}",
    }
    if preview:
        article_item["preview"] = preview
    if image_filename:
        article_item["image_filename"] = image_filename
    if cur_user.username:
        article_item["user_slug"] = cur_user.username
    add_dynamodb_put_transact(transacts, (f"POST#{article_id}", "META"), article_item, new_pk_only=True)

    add_user_activity_transact(transacts, cur_user, "article.created", "article", article_id, title,
                               f"/articles/{article_id}", cur_user.id, now)
    add_dynamodb_user_update_transact(transacts, cur_user, deltas={
        "unpublished_posts_count": 1,
    })
    # todo: should be unique in combination with username (cur_user, post)
    add_dynamodb_put_transact(transacts, (f"POST_SLUG#{slug}", "META"), {"post_id": article_id}, new_pk_only=True)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    return article_from_dynamodb(article_item)


def update_article(article: Article, update_article_dto: UpdateArticleDTO, cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE, article)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_article_dto.get_changes(article)
    if not changes:
        return

    if "content" in changes:
        changes["content"] = sanitize_forbidden_html(changes["content"])
    if "tags" in changes:
        changes["tags"] = sanitize_tags(changes["tags"])
    old_status = article.status
    published_already = old_status == ArticleStatus.PUBLISHED
    now = utc_now()

    transacts = []

    old_title = article.title
    if "title" in changes:
        new_title = changes["title"]
        if published_already and get_text_diff_percentage(old_title, new_title) > 10:
            changes["status"] = ArticleStatus.UNPUBLISHED
        old_slug = article.slug
        slug = to_kebab_case(new_title)
        if old_slug != slug:
            changes["post_slug"] = slug
            # Create redirect item so old slug resolves
            redirect_item = {
                "post_slug": old_slug,
                "redirect_to": slug,
                "created_at": now
            }
            add_dynamodb_put_transact(transacts, (f"POST_REDIRECT#{old_slug}", "META"), redirect_item, new_pk_only=True)
            # Create new slug lock
            add_dynamodb_put_transact(transacts, (f"POST_SLUG#{slug}", "META"), {"post_id": article.id},
                                      new_pk_only=True)

    old_content = article.content
    if "content" in changes:
        content = changes["content"]
        if published_already and get_text_diff_percentage(old_content, content) > 10:
            changes["status"] = ArticleStatus.UNPUBLISHED
        changes["preview"] = find_preview(content)
        changes["image_filename"] = find_static_image_filename(content)

    old_tags = list(article.tags)
    tags_changed = False
    if "tags" in changes:
        changes["tags"] = sanitize_tags(changes["tags"])
        tags_changed = sorted(changes["tags"]) != sorted(old_tags)
        if published_already and tags_changed:
            changes["status"] = ArticleStatus.UNPUBLISHED

    if published_already and changes.get("status") == ArticleStatus.UNPUBLISHED:
        add_decrease_article_tags_rating_transact(transacts, old_tags, now)
        add_delete_article_tag_combos_transact(transacts, article)
    elif tags_changed:
        add_delete_article_tag_combos_transact(transacts, article)

    article_owner = get_user(article.owner_id)
    if article.user_name != article_owner.name:
        changes["user_name"] = article_owner.name
    if article.user_slug != article_owner.username:
        changes["user_slug"] = article_owner.username

    article_owner_deltas = {}

    status = changes.get("status", article.status)
    status_changed = status != old_status
    if status_changed:
        # Update post lists
        changes["post_status_pk"] = f"POST#{status}"
        changes["post_user_status_pk"] = f"POST#{article.user_id}#{status}"

        # User post counters
        article_owner_deltas[f"{old_status}_posts_count"] = -1
        article_owner_deltas[f"{status}_posts_count"] = 1

    add_dynamodb_user_update_transact(transacts, article_owner, deltas=article_owner_deltas)
    add_dynamodb_article_update_transact(transacts, article, changes)

    if cur_user.id != article_owner.id:
        add_dynamodb_user_update_transact(transacts, cur_user)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    for k, v in changes.items():
        if k == "post_slug":
            k = "slug"
        if hasattr(article, k):
            setattr(article, k, v)


def create_article_comment(article: Article, article_comment_dto: ArticleCommentDTO, cur_user: User,
                           req) -> ArticleComment:
    verify_authorization(cur_user, Permission.CREATE_ARTICLE_COMMENT)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    now = utc_now()
    comment_id = f"{now}#{str(uuid.uuid4())}"

    transacts = []

    article_comment_item = {
        "id": comment_id,

        "user_id": cur_user.id,
        "user_name": cur_user.name,
        "user_avatar_filename": cur_user.avatar_filename,
        "user_username": cur_user.username,

        "post_id": article.id,
        "post_title": article.title,
        "comment_post_slug": article.slug,
        "post_comment_pk": "POST_COMMENT",
        "post_comment_user_pk": f"USER#{cur_user.id}",

        "text": article_comment_dto.text,
        "created_at": now,
    }

    add_dynamodb_put_transact(transacts, (f"POST#{article.id}", f"COMMENT#{comment_id}"), article_comment_item)
    add_dynamodb_article_update_transact(transacts, article, deltas={"comments_count": 1})
    add_user_activity_transact(transacts, cur_user, "comment.created", "comment", comment_id, article.title,
                               f"/articles/{article.id}#comment-{comment_id}", cur_user.id, now)
    add_dynamodb_user_update_transact(transacts, cur_user, deltas={
        "post_comments_count": 1,
    })

    if cur_user.id != article.owner_id:
        article_owner = get_user(article.owner_id)
        add_dynamodb_user_update_transact(transacts, article_owner)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="title")
        raise

    return article_comment_from_dynamodb(article_comment_item)


def update_article_comment(article: Article, article_comment: ArticleComment,
                           update_article_comment_dto: UpdateArticleCommentDTO,
                           cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_COMMENT, article_comment)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    if article_comment.likes_count != 0 or article_comment.dislikes_count != 0:
        raise ArticleCommentNonEditableError()

    changes = update_article_comment_dto.get_changes(article_comment)
    if not changes:
        return

    transacts = []

    add_dynamodb_update_transact(transacts, (f"POST#{article.id}", f"COMMENT#{article_comment.id}"), changes)

    add_dynamodb_user_update_transact(transacts, cur_user)

    if cur_user.id != article.owner_id:
        article_owner = get_user(article.owner_id)
        add_dynamodb_user_update_transact(transacts, article_owner)

    dynamodb_transact_write(transacts)

    for k, v in changes.items():
        if hasattr(article_comment, k):
            setattr(article_comment, k, v)


def update_dynamodb_item(
        key: tuple[str, str],
        changes: dict[str, Any] | None = None,
        deltas: dict[str, Any] | None = None,
        add_updated_at: bool = True
) -> None:
    param_dict = dict(locals())
    update_item_params = build_dynamodb_update_item_params(**param_dict)
    get_dynamodb_table().update_item(**update_item_params["Update"])


def update_user(user: User, update_user_dto: UpdateUserDTO, cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_user_dto.get_changes(user)
    if not changes:
        return

    now = utc_now()

    if "website" in changes and changes["website"]:
        website = str(changes["website"]).rstrip("/")
        if website == user.website:
            changes.pop("website")
        else:
            changes["website"] = website

    transacts = []
    avatar_action = changes.pop("avatar_action", "keep")

    if avatar_action == "delete":
        changes["avatar_filename"] = None
    elif avatar_action == "keep":
        changes.pop("avatar_filename", None)

    if not changes:
        return

    old_avatar = user.avatar_filename
    article_user_changes = {}
    comment_user_changes = {}

    if "name" in changes:
        article_user_changes["user_name"] = changes["name"]
        comment_user_changes["user_name"] = changes["name"]

    if "username" in changes:
        old_slug = user.username
        slug = changes["username"]

        if old_slug and slug:
            redirect_item = {
                "username": old_slug,
                "redirect_to": slug,
                "created_at": now
            }
            add_dynamodb_put_transact(transacts, (f"USER_REDIRECT#{old_slug}", "META"), redirect_item, new_pk_only=True)

        if slug:
            add_dynamodb_put_transact(transacts, (f"USER_SLUG#{slug}", "META"), {"user_id": user.id}, new_pk_only=True)
        elif old_slug:
            add_dynamodb_delete_transact(transacts, (f"USER_SLUG#{old_slug}", "META"))

        article_user_changes["user_slug"] = slug
        comment_user_changes["user_username"] = slug

    if "avatar_filename" in changes:
        comment_user_changes["user_avatar_filename"] = changes["avatar_filename"]

    if article_user_changes:
        for article in get_all_articles_by_user(user):
            add_dynamodb_article_update_transact(transacts, article, article_user_changes)

    if comment_user_changes:
        for comment in get_all_article_comments_by_user(user):
            add_dynamodb_update_transact(
                transacts,
                (f"POST#{comment.article_id}", f"COMMENT#{comment.id}"),
                comment_user_changes,
            )

    add_dynamodb_user_update_transact(transacts, user, changes, {})

    if user.id != cur_user.id:
        add_dynamodb_user_update_transact(transacts, cur_user)

    try:
        dynamodb_transact_write(transacts)
    except DynamoDBTransactionError as e:
        if e.is_conditional():
            raise SlugDuplicationError(field="username")
        raise

    if old_avatar and avatar_action in {"delete", "replace"}:
        drop_public_file(old_avatar)


def update_user_activity_settings(user: User, dto: UpdateUserActivitySettingsDTO, cur_user: User) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()
    changes = dto.get_changes(user)
    if not changes:
        return
    update_dynamodb_item((f"USER#{user.id}", "META"), changes=changes)
    for k, v in changes.items():
        setattr(user, k, v)


def update_user_interests_settings(user: User, dto: UpdateUserInterestsSettingsDTO, cur_user: User) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER, user)
    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()
    changes = dto.get_changes(user)
    if not changes:
        return
    update_dynamodb_item((f"USER#{user.id}", "META"), changes=changes)
    user.show_interests = changes["show_interests"]


def update_user_status(user: User, update_user_status_dto: UpdateUserStatusDTO, cur_user: User, req) -> None:
    # logger.debug(f"update_user_status: user: {user}, cur_user: {cur_user}")
    verify_authorization(cur_user, Permission.UPDATE_USER_STATUS)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    changes = update_user_status_dto.get_changes()
    if not changes:
        return
    if not "comment" in changes:
        changes["comment"] = None

    status = changes["status"]
    changes["user_status_pk"] = f"USER#{status}"

    transacts = []

    add_dynamodb_user_update_transact(transacts, cur_user)

    if cur_user.id != user.id:
        add_dynamodb_user_update_transact(transacts, user, changes, {})

    # logger.debug(transacts)

    dynamodb_transact_write(transacts)


def update_article_status(article: Article, update_article_status_dto: UpdateArticleStatusDTO, cur_user: User,
                          req) -> None:
    # logger.debug(f"update_post_status: post: {post}, cur_user: {cur_user}")
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_STATUS)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    if article.status == ArticleStatus.PUBLISHED:
        raise ArticleAlreadyPublishedError()

    changes = update_article_status_dto.get_changes()
    if not changes:
        return
    if not "comment" in changes:
        changes["comment"] = None

    old_status = article.status
    status = changes["status"]
    now = utc_now()

    transacts = []

    article_owner = get_user(article.owner_id)
    add_dynamodb_user_update_transact(transacts, article_owner, deltas={
        # User post counters
        f"{old_status}_posts_count": -1,
        f"{status}_posts_count": 1,
    })

    crossed_published_boundary = (old_status == ArticleStatus.PUBLISHED) != (status == ArticleStatus.PUBLISHED)
    if crossed_published_boundary and status == ArticleStatus.PUBLISHED:
        if not article.published_at:
            changes["published_at"] = now
        if article_owner:
            changes["user_slug"] = article_owner.username

        add_increase_article_tags_rating_transact(transacts, article.tags, now)
        add_put_article_tag_combos_transact(transacts, article)
    elif crossed_published_boundary:
        add_decrease_article_tags_rating_transact(transacts, article.tags, now)
        add_delete_article_tag_combos_transact(transacts, article)

    changes["post_status_pk"] = f"POST#{status}"
    changes["post_user_status_pk"] = f"POST#{article.user_id}#{status}"

    add_dynamodb_article_update_transact(transacts, article, changes)

    if cur_user.id != article_owner.id:
        add_dynamodb_user_update_transact(transacts, cur_user)

    # logger.debug(transacts)

    dynamodb_transact_write(transacts)

    if status == ArticleStatus.PUBLISHED:
        try:
            dispatch_article_published_event(article)
        except Exception:
            logger.exception("Unable to dispatch article published event")


def create_contact_message(message_dto: ContactMessageDTO, user: User = None) -> ContactMessage:
    user and verify_authorization(user, Permission.CREATE_CONTACT_MESSAGE)

    now = utc_now()
    message_id = str(uuid.uuid4())

    name = message_dto.name
    message = message_dto.message

    if is_prod():
        text = (
            f"New contact form submission:\n"
            f"ID: {message_id}\n"
            f"Name: {name}\n"
            f"Email: {message_dto.email}\n"
            f"Message: {message}\n"
            f"User ID: {user.id if user else 'N/A'}"
        )
        get_sns_client().publish(
            TopicArn=get_contact_topic_arn(),
            Message=text,
            Subject="New Contact Form Submission"
        )

    message_item = {
        "pk": f"CONTACT_MESSAGE#{message_id}",
        "sk": "META",
        "message_id": message_id,
        "name": name,
        "email": message_dto.email,
        "message": message,
        "created_at": now,
    }
    if user:
        message_item["user_id"] = user.id

    get_dynamodb_table().put_item(Item=message_item)

    return ContactMessage(
        id=message_id,
        name=message_item["name"],
        email=str(message_item["email"]),
        message=message_item["message"],
        user_id=message_item.get("user_id"),
        created_at=now,
    )


def update_article_impression(article: Article, update_article_impression_dto: UpdateArticleImpressionDTO,
                              cur_user: User,
                              req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_ARTICLE_IMPRESSION, article)

    if cur_user.status == UserStatus.BANNED:
        raise UserBannedError()

    current_impression = find_article_impression(article, cur_user)
    current_action = current_impression.action if current_impression else None
    action = update_article_impression_dto.action
    article_impression_item = {
        "post_id": article.id,
        "user_id": cur_user.id,
        "action": action,
    }
    transacts = []

    article_deltas = {}
    article_imp_key = (f"POST#{article.id}", f"IMP#{cur_user.id}")

    if action == ArticleImpressionAction.LIKE:
        if current_action == ArticleImpressionAction.LIKE:
            add_dynamodb_delete_transact(transacts, article_imp_key)
            article_deltas["likes_count"] = -1
            article_deltas["rating_sk"] = compute_rating_sk(-1)
        elif current_action == ArticleImpressionAction.DISLIKE:
            add_dynamodb_update_transact(transacts, article_imp_key, {"action": ArticleImpressionAction.LIKE})
            article_deltas["dislikes_count"] = -1
            article_deltas["likes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(2)
        else:
            add_dynamodb_put_transact(transacts, article_imp_key,
                                      {**article_impression_item, "action": ArticleImpressionAction.LIKE},
                                      new_pk_only=True)
            article_deltas["likes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(1)

    elif action == ArticleImpressionAction.DISLIKE:
        if current_action == ArticleImpressionAction.DISLIKE:
            add_dynamodb_delete_transact(transacts, article_imp_key)
            article_deltas["dislikes_count"] = -1
            article_deltas["rating_sk"] = compute_rating_sk(1)
        elif current_action == ArticleImpressionAction.LIKE:
            add_dynamodb_update_transact(transacts, article_imp_key, {"action": ArticleImpressionAction.DISLIKE})
            article_deltas["likes_count"] = -1
            article_deltas["dislikes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(-2)
        else:
            add_dynamodb_put_transact(transacts, article_imp_key,
                                      {**article_impression_item, "action": ArticleImpressionAction.DISLIKE},
                                      new_pk_only=True)
            article_deltas["dislikes_count"] = 1
            article_deltas["rating_sk"] = compute_rating_sk(-1)

    add_dynamodb_article_update_transact(transacts, article, deltas=article_deltas)

    add_dynamodb_user_update_transact(transacts, cur_user)

    if cur_user.id != article.owner_id:
        article_owner = get_user(article.owner_id)
        add_dynamodb_user_update_transact(transacts, article_owner)

    logger.debug(transacts)
    dynamodb_transact_write(transacts)


def update_user_impression(user: User, update_relation_dto: UpdateUserImpressionDTO, cur_user: User, req) -> None:
    verify_authorization(cur_user, Permission.UPDATE_USER_IMPRESSION, user)

    if user.status == UserStatus.BANNED:
        raise UserBannedError()

    if user.id == cur_user.id:
        return

    current_relation = find_user_impression(user, cur_user)
    current_action = current_relation.action if current_relation else None
    action = update_relation_dto.action
    relation_item = {
        "user_id": cur_user.id,
        "target_user_id": user.id,
        "action": action,
    }
    transacts = []

    cur_user_deltas = {
    }
    user_deltas = {
    }
    relation_key = (f"USER#{cur_user.id}", f"REL#{user.id}")

    if action == UserImpressionAction.FOLLOW:
        if current_action == UserImpressionAction.FOLLOW:
            # Unfollow
            add_dynamodb_delete_transact(transacts, relation_key)
            cur_user_deltas["following_count"] = -1
            user_deltas["followers_count"] = -1
            user_deltas["rating_sk"] = compute_rating_sk(-1)
        elif current_action == UserImpressionAction.BLOCK:
            # Switching from block to follow
            add_dynamodb_update_transact(transacts, relation_key, {"action": UserImpressionAction.FOLLOW})
            cur_user_deltas["following_count"] = 1
            user_deltas["followers_count"] = 1
            user_deltas["rating_sk"] = compute_rating_sk(2)
        else:
            # New follow
            add_dynamodb_put_transact(transacts, relation_key, relation_item, new_pk_only=True)
            cur_user_deltas["following_count"] = 1
            user_deltas["followers_count"] = 1
            user_deltas["rating_sk"] = compute_rating_sk(1)

    elif action == UserImpressionAction.BLOCK:
        if current_action == UserImpressionAction.BLOCK:
            # Unblock
            add_dynamodb_delete_transact(transacts, relation_key)
            user_deltas["rating_sk"] = compute_rating_sk(1)
        elif current_action == UserImpressionAction.FOLLOW:
            # Switching from follow to block
            add_dynamodb_update_transact(transacts, relation_key, {"action": UserImpressionAction.BLOCK})
            cur_user_deltas["following_count"] = -1
            user_deltas["followers_count"] = -1
            user_deltas["rating_sk"] = compute_rating_sk(-2)
        else:
            # New block
            add_dynamodb_put_transact(transacts, relation_key, relation_item, new_pk_only=True)
            user_deltas["rating_sk"] = compute_rating_sk(-1)

    add_dynamodb_user_update_transact(transacts, cur_user, deltas=cur_user_deltas)
    add_dynamodb_user_update_transact(transacts, user, deltas=user_deltas)

    dynamodb_transact_write(transacts)


def get_email_files_dir() -> str:
    return config.get("email_files_dir")


def get_static_s3_bucket() -> str:
    return config.get("static_s3_bucket")


def get_contact_topic_arn():
    return get_config().get("contact_topic_arn")


def get_ses_from_email():
    return get_config().get("ses_from_email")


def dispatch_article_published_event(article: Article) -> None:
    handle_article_published_event(ArticlePublishedEvent(article))


def save_email_to_disk(sender: str, recipient: str, subject: str, text_body: str, html_body: str) -> None:
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    emails_dir = get_email_files_dir()
    os.makedirs(emails_dir, exist_ok=True)
    email_path = os.path.join(emails_dir, f"{utc_now()}-{uuid.uuid4()}.eml")
    with open(email_path, "wb") as email_file:
        email_file.write(message.as_bytes())
    logger.info("Saved development email to %s", email_path)


def handle_article_published_event(event: ArticlePublishedEvent) -> None:
    from itertools import combinations

    article = event.article
    matching_subscription_tags = {}

    for size in range(1, len(article.tags) + 1):
        for combo in combinations(sorted(article.tags), size):
            subscription_key = "ARTICLE_TAG_SUBSCRIBERS#" + "#".join(combo)
            exclusive_start_key = None
            while True:
                response = query_dynamodb_table(
                    key_condition_expr=Key("pk").eq(subscription_key),
                    exclusive_start_key=exclusive_start_key,
                )
                for item in response.get("Items", []):
                    matching_subscription_tags.setdefault(item["user_id"], set()).add(combo)
                exclusive_start_key = response.get("LastEvaluatedKey")
                if not exclusive_start_key:
                    break

    if not matching_subscription_tags:
        return

    sender = get_ses_from_email()
    if is_prod() and not sender:
        logger.warning("Article publication notification skipped: SES_FROM_EMAIL is not configured")
        return
    sender = sender or "no-reply@localhost"

    base_url = get_base_url().rstrip('/')
    article_url = f"{base_url}/articles/{article.id}"
    subject = f"New article matching your interests: {article.title}"

    for user_id, subscriptions in matching_subscription_tags.items():

        if user_id == article.user_id:
            continue
        user = find_user(user_id)
        if not user or not user.email:
            continue

        article_tag_links = [
            {
                "name": " + ".join(subscription_tags),
                "url": f"{base_url}/articles?{urlencode([('type', 'latest'), ('status', 'published')] + [('tags', tag) for tag in subscription_tags])}",
            }
            for subscription_tags in sorted(subscriptions)
        ]
        subscribed_tags_text = ", ".join(link["name"] for link in article_tag_links)
        text_body = (
                f"Hello {user.name or 'there'},\n\n"
                "A new article matching your interests was published:\n\n"
                f"{article.title}\n"
                f"Subscribed interests: {subscribed_tags_text}\n"
                + "\n".join(f"{tag['name']}: {tag['url']}" for tag in article_tag_links)
                + f"\n\nRead it here: {article_url}\n\n"
                  f"Best regards,\n{get_config().get('site_name', 'The team')}\n"
        )
        html_body = get_html_content("emails/article-published-notification.html", {
            "recipient_name": user.name or "there",
            "article_title": article.title,
            "article_url": article_url,
            "article_tag_links": article_tag_links,
        })
        try:
            if not is_prod():
                save_email_to_disk(sender, user.email, subject, text_body, html_body)
                continue
            get_ses_client().send_email(
                Source=sender,
                Destination={"ToAddresses": [user.email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                    },
                },
            )
        except Exception:
            logger.exception(
                "Unable to send article publication notification",
                extra={"user_id": user_id, "article_id": article.id},
            )


def get_cf_distribution_id() -> str:
    return get_config().get("cf_distribution_id")


@lru_cache
def get_s3_client():
    import boto3
    return boto3.client("s3")


@lru_cache
def _get_cf_client():
    import boto3
    return boto3.client("cloudfront")


@lru_cache
def get_sns_client():
    import boto3
    return boto3.client("sns")


@lru_cache
def get_ses_client():
    import boto3
    return boto3.client("ses", region_name=get_aws_region())


def get_image_dimensions(data: bytes) -> tuple[int, int]:
    """Return (width, height) for JPEG, PNG, GIF images from raw bytes."""

    import struct

    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24:
            raise ValueError("PNG file too short")
        width, height = struct.unpack(">II", data[16:24])
        return width, height

    # GIF
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) < 10:
            raise ValueError("GIF file too short")
        width, height = struct.unpack("<HH", data[6:10])
        return width, height

    # JPEG
    elif data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 1 < len(data):
            if data[offset] != 0xFF:
                raise ValueError("Invalid JPEG marker")
            marker = data[offset + 1]

            if 0xC0 <= marker <= 0xC3:
                # need at least 5 bytes for >xHH
                segment = data[offset + 5:offset + 10]
                if len(segment) < 5:
                    raise ValueError("JPEG SOF segment too short")
                _, height, width = struct.unpack(">xHH", segment)
                return width, height
            else:
                if offset + 4 > len(data):
                    raise ValueError("Truncated JPEG")
                seg_len = struct.unpack(">H", data[offset + 2:offset + 4])[0]
                if seg_len < 2:
                    raise ValueError("Invalid segment length")
                offset += 2 + seg_len

        raise ValueError("No SOF marker found in JPEG")

    raise ValueError("Unsupported image type")


def find_preview(html_content: str) -> str | None:
    from html_parsers import FirstPExtractor
    parser = FirstPExtractor()
    parser.feed(html_content)

    text = " ".join(part.strip() for part in parser.text_parts if part.strip())

    if not text:
        return None

    return text[:300]


def find_static_image_filename(html_content: str) -> str | None:
    allowed_extensions = "|".join(ImageFileDTO.ALLOWED_IMAGE_EXTENSIONS)

    # Matches filenames generated by save_public_file:
    # static/<uuid>[optional _<width>x<height>].<ext>
    pattern = (
        rf'<img[^>]+src=["\']'
        rf'/?'
        rf'([0-9a-fA-F-]+(?:_[0-9]+x[0-9]+)?\.'
        rf'(?:{allowed_extensions}))["\']'
    )

    match = re.search(pattern, html_content, flags=re.IGNORECASE)
    if not match:
        return None

    return match.group(1)
