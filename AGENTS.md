# Repository agent instructions

## Review and delivery authorization

- Leave changes unstaged and uncommitted for the user to review.
- Staging, commits (including amendments), pushes, merges, rebases, reverts, tags/releases, pull requests,
  deployments, and other publishing actions require an explicit user command for that specific action.
- Implementation requests, passing checks, and guidance about organizing commits do not authorize these actions.
  Authorization for one action does not authorize another; a commit command does not authorize a push.

## Change scope and verification

- When committing, split unrelated work into separate commits by logical concern. Include relevant tests and
  documentation with each change.
- Keep refactors scoped to the layer being changed. Do not change controllers for template-only work when templates
  can define their own presentation data.
- Prefer existing project functions and framework or library primitives for common behavior. Add dependencies only
  when their value justifies the maintenance and runtime cost.
- Do not rely on wildcard imports or accidental re-exports for new dependencies; import names explicitly and remove
  imports that are not referenced.
- Format updated files consistently with repository conventions and any applicable `.editorconfig`; verify formatting
  before handing off work, including `git diff --check`.
- Run `make tests` after functional changes and before handing off work. It runs pytest in the isolated
  `docker-compose.test.yml` stack with its own DynamoDB table, rather than the development database. Functional tests
  call the test services through `API_TEST_BASE_URL` and `WEB_TEST_BASE_URL` and use their own database connections for
  fixtures and assertions.
- The isolated test stack mounts tracked `tests/site-config.json` over `shared/data.json` in both Lambdas
  and the test runner. Keep test site settings there rather than relying on ignored local configuration.
- Record useful, durable repository knowledge in this file when work reveals conventions, tooling limitations, or
  build and deployment requirements that will help future changes.

## Shared code and Lambda boundaries

- Keep `shared` limited to genuinely shared domain models, persistence primitives, validation, and helpers used by both Lambdas.
- Before adding or keeping a helper in `shared`, search its callers across `api-lambda` and `web-lambda`. If it is used by only one Lambda, place it in that Lambda's module; web-only helpers belong in `web-lambda/web_utils.py`.
- Lambda-specific orchestration, page composition, and presentation-oriented query helpers must remain in the owning Lambda.
- Shared HTML fragments used by both Lambdas belong in `shared/templates/fragments`; keep initial page renders and
  fragments returned by API endpoints consistent.
- Related-article discovery is shared by the initial web page and the API's paginated fragment. Keep source/tag-scoped
  cursors and public-access filtering consistent; never advance past unprocessed batch-read keys.
- Published popular tag listings rank matching tag-combination members in batches of at most 100, retaining the top
  page plus one result. They still traverse the matching partition on each request; category and OR filters use their
  existing index paths. Preserve deterministic rating/ID ordering and fail if batch reads remain incomplete.
- The web Lambda owns page rendering; resource mutations, uploads, and resource API responses belong to the API Lambda.
  Browser scripts should call the configured API URL directly rather than adding web routes that proxy API requests.
- Homepage comment previews must verify current article publication status and use the current article title and URL;
  comment snapshots alone do not establish public access. Skip missing or nonpublic articles and continue pagination
  to fill the feed.
- Keep authentication cookies and supporting token logic compatible between the web and API Lambdas.
- New article slugs normalize Unicode and repeated separators and must contain letters or digits. Preserve legacy
  tag and subscription keys during lookups; do not apply new slug normalization to existing keys implicitly.
- Keep the web Lambda dependency footprint minimal to reduce bundle size and cold-start time. Add API-only or
  heavyweight dependencies there only when page rendering or authentication requires them.
- Register application environment settings centrally in `shared/app_config.py` and read them through its accessors or
  `get_config()`. Keep direct environment reads in central configuration or explicit process-bootstrap code.
- Template request state belongs in `template_request` (a `ContextVar`), never in cached Jinja environment globals.
  Pass requests explicitly to error rendering when exception handling can occur after middleware resets the context.

## Authentication redirects

- Login/logout return destinations must be same-origin application pages, never authentication endpoints. Validate
  both the incoming return URL/referrer and the saved `redirect_url` cookie, then clear that cookie after the callback.
  Returning to a login callback reuses a single-use authorization code and causes a repeated Cognito `invalid_grant`.
  Authentication responses use `Cache-Control: no-store`.

## Templates and static assets

- Store layouts and page templates in the owning Lambda's templates root; store reusable partial HTML templates in
  `fragments`.
- Build internal links with named route helpers (`url(...)`, `article_url(...)`, etc.). Append section fragments to a
  generated endpoint URL rather than using bare fragment-only links.
- Serve web assets and store web uploads under the repository-level `static` directory, exposed at `/static`.
- When changing `static/styles.css`, increment `CSS_CACHE_COUNTER` in `.env.prod`; when changing
  `static/scripts.js`, increment `JS_CACHE_COUNTER` in `.env.prod`.
- Prefer native Bootstrap classes and components for UI changes; avoid modifying `static/styles.css` unless the requirement cannot be implemented cleanly with Bootstrap.
- Generate responsive Bootstrap grid sizes with the Jinja `row_classes` and `col_classes` filters rather than
  hardcoding `row-cols-*` or sized `col-*` classes. Plain `row` and `col` may remain when no sizes are specified.
  `col_classes` includes the base `col` class; use `"def": 12` when a responsive column must stack at full width.
- Bootstrap and Bootstrap Icons are loaded centrally in `web-lambda/templates/layout.html`.
- Optional `fonts.body` and `fonts.headings` site settings load Google Fonts after Bootstrap. Empty settings retain
  Bootstrap defaults and make no font requests; keep typography out of hardcoded styles.
- Do not underline non-link headings. Apply link decoration to anchors rather than heading containers.
- Define article listing breadcrumbs once in the template and reuse them for navigation and structured metadata.
  Preserve configured display-name casing. Collection pagination uses buttons and API fragments, never crawlable
  next-page anchors. Exclude pagination offsets from canonical and social URLs, and validate incoming cursors before
  querying persistence so malformed API offsets return 422 instead of 500. Web GET/HEAD requests containing
  `offset` redirect to the same URL without that parameter before page rendering or legacy redirects; preserve other
  query parameters, including repeated filters. Pagination belongs only to API endpoints.
- Guest article reactions link to login with the current path and query as the return destination. Keep donation links
  public in the article sidebar directly after the author section. Render other article actions once in the compact
  article-controls group.
- External HTTP(S) links must include `rel="nofollow"`; preserve `noopener noreferrer` when present, including on
  user links, references, configured social links, and sharing controls.
- Keep `static/scripts.js` limited to JavaScript reused across pages. Put page-specific scripts in their owning template
  and initialize them after dependencies are available, using `DOMContentLoaded` for dependencies loaded with `defer`.
- Initialize Masonry through `data-masonry-options` rather than its `data-masonry` auto-initialization; keep initial and
  dynamically appended cards covered by shared relayout behavior.

## Maintenance and import scripts

- Run production maintenance and import commands through `scripts-production` in
  `docker-compose.scripts.production.yml`. Default new maintenance commands to a read-only dry run and require explicit
  `--apply` for writes.
- Assign reviewed category illustrations with `python scripts/set_category_images.py scripts/fixtures/category-images.json
  --user-id USER_ID` through that service. Review the default dry-run output before adding `--apply`; production requires
  `STATIC_S3_BUCKET`. The command preserves other assigned images, skips completed assignments, and verifies existing
  public bytes before reusing a filename. The manifest maps only categories with a matching reviewed illustration.
- Reuse application services and persistence helpers so imports preserve validation, counters, side effects, and domain
  invariants. Search for an existing operation before adding direct DynamoDB, S3, or filesystem writes; reserve direct
  writes for explicitly scoped migrations, repairs, or metadata that existing services cannot express.
- For independent source items, process each through planning, application, and result reporting before advancing.
  Do not collect or preflight the entire run before applying writes unless the task requires atomic batch behavior.
  Make reruns skip completed items and resume interrupted work; mark completion only after successful writes or an
  intentional terminal skip during `--apply`, never during dry runs.
- Isolate independent item failures, report source identity, exception type, and reason, and leave failures retryable.
  Include parsing, media preparation, DTO validation, and application writes in the item-level error boundary.
  Continue other items, report success/skip/error totals, and exit nonzero if any failed. Stop immediately for invalid
  configuration, authentication, permission, policy, exhausted quota, or sustained rate limits.
- Check stable source IDs and completion state before expensive requests or processing. Keep checkpoints in ignored,
  host-mounted paths so they survive container recreation. Use the same canonical source identifier for live imports
  and backfills from persisted source URLs. Backfill completion state through existing application helpers,
  incrementally and with dry-run as the default.
- Use stdout for structured dry-run review: print one record per item with the complete source item under `input` and
  complete DTO/service arguments, intended action, and publication intent under `output`. Do not emit separate candidate
  and outcome records for the same dry-run item or create extra review manifests unless requested.
- Persist only source metadata needed for provenance, attribution, rerun matching, or application behavior. Keep raw
  records and volatile operational data in review output rather than domain records.
- Track reusable importer code in Git; ignore exports, downloaded media, caches, checkpoints, and generated state.
  Store generated importer state in ignored `scripts/importer/` or `.tmp/` paths. Keep reusable application code and
  committed guidance source-neutral.
- Use official APIs for external providers and respect permissions, terms, pagination, quotas, and rate-limit headers.
  Honor `Retry-After`, use conservative concurrency and bounded exponential backoff with jitter only for retryable
  failures, and rely on resumability rather than aggressive retries. Do not bypass access restrictions through scraping,
  client spoofing, or credential/proxy rotation.
- For publication-time windows, make `--since` inclusive and `--until` exclusive, enforcing source timestamps locally.
  Successful-item limits exclude skipped and failed items.
- When adding LLM-backed importers, keep deterministic filtering, deduplication, ordering, field parsing, media handling,
  defaults, DTO validation, persistence, and rerun handling in code. Send only source text and the minimal context needed
  for semantic decisions; omit raw API envelopes and fields the script can compute.
- Prefer one structured LLM call per viable source item that can return either a skip decision or the complete semantic
  result. Reuse that result throughout the item, and avoid model calls for completed or deterministically rejected items.
- After DynamoDB schema/index changes, use `make recreate-local-dynamodb` to recreate the persistent local table.
  Local AWS CLI commands default to `us-west-2`.

## Telegram publishing and profile settings

- Keep Telegram publishing separate from Telegram logging. Publisher credentials belong only to the API Lambda;
  register `TELEGRAM_PUBLISHER_BOT_TOKEN` and `TELEGRAM_PUBLISHER_WEBHOOK_SECRET` in central configuration and deployment.
- Only the active account owner may manage their channel connection. Keep onboarding nonces hashed, expiring, and
  transactionally bound to pending sessions and Telegram user IDs. Keep webhook secret verification and current
  channel posting permission checks for both the user and bot before sharing.
- Sharing is an explicit user action on a published article. Post only title, category, preview, and canonical URL;
  escape Telegram HTML and respect its visible UTF-16 text limit. Keep provider errors free of bot tokens.
- Run webhook setup through scripts-production with `API_BASE_URL`; `scripts/configure_telegram_webhook.py` defaults
  to a redacted dry run and requires `--apply` to register. Registration and deployment require explicit authorization.
- Profile tabs share one profile save button; Telegram connections are saved separately through the API. Preserve
  hash-based tab selection and reveal invalid fields in hidden tabs before focusing them.

## User identity

- Provider usernames are authentication metadata, never fallback public slugs. Generated signup slugs must fit the
  same 30-character limit as profile edits.

## Activity logging

- Put canonical resource URLs in `logger.info`'s `extra={"context": ...}` when the request is available.
  Comments link to their rendered anchor; categories and tag subscriptions link to matching article collections.
  Pass requests explicitly rather than relying on template context. Request-free callers retain identifier context;
  do not invent public links for resources without a public page or move success logs before persistence.

## Account email notifications

- Account notification preferences default to enabled for existing users. Deliver status emails only for actual manual
  status changes and reaction emails only for newly created reactions; skip self reactions and inactive recipients.
- Send account emails after successful persistence through the API's `email_service`; delivery failures must not fail
  the committed action. Tag subscription emails honor `notify_tag_articles`. Development emails are saved as `.eml`.


## Sharing and creator support

- Article sharing is owner-only. Persist `telegram_shared_at` and `threads_shared_at` only after confirmed remote
  success, without changing article timestamps or publication indexes. History write failures must preserve remote
  success. Never automatically repeat uncertain publishing requests; keep browser retries disabled until the owner
  checks the destination. Attach only the article cover, never the full article body.
- Threads OAuth credentials and encrypted tokens remain API-owned. Redact callback queries from request and referrer
  access logs. Continuations are encrypted and bound to the account, article, and connection session.
- Support-link navigation uses same-origin POST forms. The web Lambda sends signed, short-lived click notifications
  to the API; persistence stays API-owned. Guard link-list edits against concurrent click increments. Existing BMC
  links remain compatible. `support_providers: null` inherits all profile links; `[]` hides them all.
- Optional watermarks apply only through `upload-article-image`, used by the article editor. Generic uploads stay
  unmarked. Save originals before publishing branded bytes, outside the public static directory or in the separate
  private originals bucket. Preserve animated files.
- Sitemap generation returns 202 and runs in `sitemap_handler.handler`. Its worker shares the API artifact; refreshing
  that worker after API bundle changes requires an explicitly authorized infrastructure update.


## Engagement statistics and feedback

- Feedback and attribution reporting use escaped, bounded contact-form prefills. New page sections belong in their
  own fragments. Functional HTTP tests route new API paths through `_is_api_request` in `tests/test_utils.py`.
