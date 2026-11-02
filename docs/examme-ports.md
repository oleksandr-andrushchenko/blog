# Reviewed Examme ports, November 2026

The source was the committed Examme tree `a8f941e`, reviewed over the previous two weeks.
The application vocabulary and ownership checks are adapted to articles.

- `c64d522`: uploaded image reads stop at the DTO size limit plus one byte.
- `71d9c84`: shared JSON form submission rejects duplicate submits, validates dynamic fields inline,
  preserves drafts on failures, and supports asynchronous preparation and completion hooks.
- `3e4a889`: confirmed Telegram receipts are scoped to article, user, and channel. Publishing
  history failures preserve remote success. Opening the article loads current-channel state through the API;
  old article-level timestamps cannot establish that a new channel already received the post.
- `3925efb`: participant-scoped messages, private inboxes and notification history, atomic unread counts,
  and throttled presence. Blocks in either direction prevent sending. Read history remains participant-only.
  Presence is written at most every five minutes with a six-minute online window. Enabled account updates
  create website history; offline users with an email address also receive email. Login and operator email
  delivery remains separate. Message text is excluded from emails and notification previews.
- `c252986`: creator-owned receiving wallets, currency/network controls, copyable public addresses,
  profile visibility and category-aware article defaults. Explicit article wallet selections override defaults.
  Wallets store destinations, not payment keys or an external connection. `[]` hides all wallets.
- `b35e7b8`: neutral controls use Bootstrap's `btn-outline-secondary`.
- `f2934cf`: reviewed, resumable category image normalization. Certification references are excluded.

The existing DynamoDB table and indexes support these features. No table recreation or migration is required.
Account deletion removes private conversations and receipts, the surviving peer's inbox/read pointers,
owned notification history, wallet metadata, and channel receipts. Message transactions and notification
writes guard against pending account deletion. Private endpoints and error responses are non-cacheable.

Run `make tests` plus these JavaScript harnesses:

```sh
node tests/test_form_submit_ui.cjs
node tests/test_direct_message_ui.cjs
node tests/test_site_notification_ui.cjs
node tests/test_crypto_wallet_ui.cjs
```

The API, web and background worker must receive compatible code when deployment is authorized.
Upload the changed shared JavaScript as well; its production cache counter is maintained in ignored `.env.prod`.
Implementation and passing checks do not authorize deployment, webhook registration, or CDN invalidation.

## Reviewed image normalization

Use `scripts-production` with `STATIC_S3_BUCKET` configured and an active authorized category editor.
The JSON input has an `images` array with records containing `old_filename`, `path` (relative to the media
root), `sha256`, and `references: [{"kind": "category", "id": "category-slug"}]`.

```sh
docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
  python scripts/normalize_public_images.py .tmp/reviewed-images.json \
  --media-root .tmp/reviewed-media --user-id USER_ID \
  --checkpoint .tmp/image-normalization-progress.json
```

The default is a dry run with one structured review record per item. `--apply` uploads through the existing
application helper under a generated filename, saves an upload checkpoint, verifies remote bytes and the
responsive WebP variants, then updates category references without removing prior shared images.
`--upload-only --apply` prepares uploads before variants become available; a later normal `--apply` resumes.
The script verifies that references still match the reviewed old or checkpointed new filename, skips
completed items, and leaves errors retryable. A limit counts successful items rather than failures/skips.
Responsive variants must be available before any category is pointed at a new image. No cache invalidation
or production object deletion is performed by this command.
