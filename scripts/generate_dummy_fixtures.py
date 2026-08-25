from api_utils import (
    ArticleCommentDTO,
    ArticleDTO,
    ArticleImpressionAction,
    ArticleStatus,
    Permission,
    TagSubscriptionDTO,
    UpdateArticleImpressionDTO,
    UpdateArticleStatusDTO,
    UpdateTagDTO,
    UpdateUserDTO,
    UpdateUserImpressionDTO,
    UserImpressionAction,
    create_article,
    create_article_comment,
    create_tag_subscription,
    find_tag,
    get_dummy_user_token,
    is_prod,
    update_article_impression,
    update_article_status,
    update_dynamodb_item,
    update_tag,
    update_user,
    update_user_impression,
    upsert_user_by_user_token,
)


def create_dummy_fixtures(req=None) -> None:
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
    create_tag_subscription(TagSubscriptionDTO(tags=["tag3"]), root_user)
    create_tag_subscription(TagSubscriptionDTO(tags=["tag1"]), user3)
    create_tag_subscription(TagSubscriptionDTO(tags=["tag2", "tag3"]), user4)

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
        tag = find_tag(tag_name)
        update_tag(tag, UpdateTagDTO(
            name=tag_name,
            image_action="replace",
            image_filename=image_filename,
        ), root_user, req)
        tag.image_filename = image_filename

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


if __name__ == "__main__":
    create_dummy_fixtures()
