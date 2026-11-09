# Repository agent instructions

## Review and delivery authorization

- Leave changes unstaged and uncommitted for the user to review.
- Staging, commits (including amendments), pushes, merges, rebases, reverts, tags/releases, pull requests,
  deployments, and other publishing actions require an explicit user command for that specific action.
- Implementation requests, passing checks, and guidance about organizing commits do not authorize these actions.
  Authorization for one action does not authorize another; a commit command does not authorize a push.
- Questions about commands (for example, "make deploy-site-files?") request an
  explanation or validation, not execution. Do not treat them as deployment authorization.
- When the user requests tests, run `make tests`. When the user authorizes deployment,
  use `make deployment` (an alias for `make deploy`) for a full deployment, or follow an
  explicitly requested target such as `make deploy`. Use scoped targets such as
  `make deploy-web-lambda`, `make deploy-api-lambda`, `make deploy-img-lambda`, or
  `make deploy-site-files` when only those artifacts need updating. Shared templates,
  API-backed workers, static assets, and infrastructure must ship together when required.
  These command preferences do not replace the explicit authorization rules above.
- CDN cache invalidations must list only the exact paths of specific changed files.
  Never invalidate `/*`, directory wildcards, or an entire distribution. The production
  CDN contains thousands of unrelated assets; preserve their cached copies.
  The legacy wildcard `make drop-cdn-cache` target has been removed; do not restore it.
  Upload authorization does not authorize a separate cache invalidation; obtain an
  explicit instruction for that action, then use only the specific affected paths.

## Committing and deploying alongside unrelated changes

- When the user authorizes committing and deploying only the current task, prefer
  the straightforward stash → isolate → commit → deploy → stash-apply workflow.
  Keep the authorization rules above; this workflow does not itself authorize publishing.
- Coordinate a pause in parallel edits during the swap. Record the starting commit,
  index state, and changed/untracked file contents so restoration can be verified.
  If task changes already share files with unrelated work, save a task-only patch
  outside the stash (for example under ignored `.tmp`) and reverse only that patch
  first. Include new task files in the patch. This leaves only unrelated work to stash
  and avoids restoring untracked task files over files committed during the workflow.
- Use `git stash push --include-untracked` with a descriptive message, record the
  resulting stash commit ID, and verify success and a clean working tree before
  applying the task patch. Do not use `--all`: ignored environment files and local
  configuration must remain available. Stop dependent steps if any command fails.
- Apply only the task patch, run the required checks, inspect the staged diff,
  and commit only its explicit files/hunks. Keep commits focused by logical concern.
- Deploy from that isolated state. For infrastructure-only CloudFormation updates,
  compare the starting template with production, preserve existing stack parameters
  (especially Lambda ZIP keys) and stack tags, and override only intended values.
  Review a change set before executing it; do not blindly run `make deploy-infra`
  when local `.env.prod` values could introduce unrelated updates. Preserve the
  existing template-upload bucket for templates exceeding the inline size limit.
- Restore unrelated work with `git stash apply <recorded-stash-id>`, including if
  deployment fails or is blocked. Retain the stash until restoration is verified;
  resolve conflicts without discarding parallel edits. Verify original file contents
  and index state, leaving originally unstaged work unstaged. Restoration can happen
  while AWS finishes deploying once all deployment inputs have been captured and
  uploaded. Verify deployment completion and live behavior before reporting success.

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
- Published tag listings use the ordered `TAG_COMBOS_BY_RATING` index only after guarded backfill activation.
  Normal popular-tag pages read a page plus lookahead; category intersections use bounded relation batches.
  Maintain relation ordering transactionally on votes, publication, tag/category edits, and erasure cleanup.
  Preserve legacy cursors, deterministic rating/ID ordering, and complete batch reads. Unactivated and OR queries
  retain their existing paths. See `docs/tag-listing-performance.md` for rollout.
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

- The `dummy-` filename prefix is reserved for local/test assets. Static builds
  must exclude these files at every directory depth while retaining the source
  files under `static` for local fixtures. Uploading a corrected build does not
  remove previously uploaded objects from S3; any production cleanup requires
  an explicit command and must target only the specific affected keys.
- Local fixtures use six tracked UUID-named files under `static` (four WebP images and two MP4 clips),
  listed in `tests/test_dummy_fixtures.py`. Keep fixture references within this set and preserve dimensions
  in image filenames so the existing article cover extractor recognizes them. Static builds exclude UUID assets.
- The default social image is `sysdespro_1200x630.png`; keep `site_img_filename`
  aligned in local site configuration and `tests/site-config.json`. Its white
  diagram-tree icon on blue is also used by the tracked favicon and manifest assets.

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
- Form validation uses explicit `handleFormSubmit` / JustValidate rules, never native HTML constraints or
  `checkValidity` / `reportValidity`. Keep API validation authoritative. Register dynamic creator fields with the
  returned validator instance and remove their rules when controls are removed or disabled.
- Keep `static/scripts.js` limited to JavaScript reused across pages. Put page-specific scripts in their owning template
  and initialize them after dependencies are available, using `DOMContentLoaded` for dependencies loaded with `defer`.
- Homepage article autocomplete lives in `web-lambda/templates/fragments/article-search.html`.
  Reuse the existing category context and published-only tag API; preserve returned legacy/Unicode tag
  keys when navigating. Run `node tests/test_home_search_ui.cjs` when changing finder behavior.
- Article listing category controls load the category catalog for their options. Omit an empty
  category on form serialization; it is not a valid slug. Run `node tests/test_article_filters_ui.cjs` for filter changes.
- Initialize Masonry through `data-masonry-options` rather than its `data-masonry` auto-initialization; keep initial and
  dynamically appended cards covered by shared relayout behavior.

## Article creation and revision

- Before planning, creating, rewriting, or revising any article or draft, read
  [docs/article-creation-rules.md](docs/article-creation-rules.md) in full and follow its rules.
  It is the central reference for article planning, editorial guidance, source prompts, metadata, application saves,
  and verification. Maintain article-creation knowledge there rather than duplicating it in this file.
- Draft creation and revision do not authorize publication, social sharing, deployment, or commits.

## Maintenance and import scripts

- Production configuration records use `/sysdespro/prod/KEY` in Parameter Store and
  `sysdespro/prod/KEY` in Secrets Manager. Use `scripts/production_config.py relocate`
  through `scripts-production` to review prefix changes, then an explicitly authorized
  `--apply` to copy, verify, and switch the bootstrap. Old records remain for separate cleanup.
- Run production maintenance and import commands through `scripts-production` in
  `docker-compose.scripts.production.yml`. Default new maintenance commands to a read-only dry run and require explicit
  `--apply` for writes.
- When production script configuration omits `DYNAMODB_TABLE`, resolve it from the deployed stack's
  `DynamoDbTableName` output during explicit process bootstrap before application access. Do not guess the table name
  or persist a local override of authoritative production settings.
- Assign reviewed category illustrations with `python scripts/set_category_images.py PATH_TO_REVIEWED_ASSIGNMENTS.json
  --user-id USER_ID` through that service. Review the default dry-run output before adding `--apply`; production requires
  `STATIC_S3_BUCKET`. The command preserves other assigned images, skips completed assignments, and verifies existing
  public bytes before reusing a filename. Supply a reviewed manifest mapping category slugs to available image files;
  no default assignments are bundled.
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
- Sharing is an explicit user action on a published article. Post only title, category, preview, canonical URL,
  and the connected account/channel follow footer;
  escape Telegram HTML and respect its visible UTF-16 text limit. Keep provider errors free of bot tokens.
- Run webhook setup through scripts-production with `API_BASE_URL`; `scripts/configure_telegram_webhook.py` defaults
  to a redacted dry run and requires `--apply` to register. Registration and deployment require explicit authorization.
- Profile integration sections use the API connection status `configured` flag for visibility. Hide the Integrations
  tab when none are configured; failed availability checks must not reveal provider names or settings. Keep provider
  secrets API-owned; do not copy credentials into the web Lambda to decide which settings to show.
- Provider connect buttons use outline-dark while disconnected and dark when connected; reset the style
  after a successful disconnect. Connect-login-method, Add support link, and Add crypto wallet buttons
  use outline-dark. Run `node tests/test_integration_controls_ui.cjs` when changing connection controls.
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
- Request-free scripts resolve notification links through canonical route metadata and configured base URLs.
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
  links remain compatible. `support_providers: null` inherits category-aware profile defaults; `[]` hides them all.
  Legacy support links default to all articles and stay visible on profiles; per-link profile visibility is independent.
- Optional watermarks apply only through `upload-article-image`, used by the article editor. Generic uploads stay
  unmarked. Save originals before publishing branded bytes, outside the public static directory or in the separate
  private originals bucket. Preserve animated files.
- Sitemap generation returns 202 and runs in `sitemap_handler.handler`. Its worker shares the API artifact; refreshing
  that worker after API bundle changes requires an explicitly authorized infrastructure update.


## Engagement statistics and feedback

- Guard reaction updates/deletes against the previously read action. Handle both raw botocore `ClientError` and
  wrapped `DynamoDBTransactionError` cancellation reasons; stale transitions return 409. Do not repeat uncertain
  writes. Merge directional counter changes into existing user transactions so each item is updated only once.
- Keep confirmed `telegram_publish` / `threads_publish` counts separate from `*_share_click` counts. Durable
  `POST_SHARE#<article_id>` event markers deduplicate provider IDs (Telegram includes the channel ID) or client UUIDs.
  Never record incomplete or uncertain provider responses as confirmed shares. Persistence failures must preserve
  remote success and must not encourage a duplicate post.
- Comment, comment-reaction, and share emails honor independent default-enabled preferences, skip self-notifications
  and inactive recipients, and run after persistence. Detailed profile statistics are rendered only for the owner.
- Historical engagement repairs use `scripts/rebuild_engagement_statistics.py` through scripts-production, dry-run
  by default and `--apply` for guarded metadata writes. The backfill checks both engagement revision and update time
  so scans cannot overwrite concurrent activity. It does not reconstruct historical sharing from saved timestamps.
- Feedback and attribution reporting use escaped, bounded contact-form prefills. New page sections belong in their
  own fragments. Functional HTTP tests route new API paths through `_is_api_request` in `tests/test_utils.py`.


## Cognito Apple sign-in and profile forms

- Optional Apple sign-in is enabled by `AppleCredentialsSecretArn` in CloudFormation, wired from
  `APPLE_CREDENTIALS_SECRET_ARN` in the deployment environment. Resolve Apple key material directly from Secrets
  Manager into Cognito; do not pass it to Lambdas or commit it. Empty configuration retains existing providers.
- Omit unchanged usernames from profile form requests so display-name edits do not trigger legacy username
  validation. Preserve actual username edits and the existing support-link prepareData behavior.
- Support-link POSTs return non-cacheable HTTP 303 redirects with the existing origin, prefetch, and referrer-policy
  checks. Keep background API click notification independent of navigation and omit noreferrer on POST forms.
- Owner-only publishing controls render once in `fragments/article-sharing.html`, before related articles.
  Use nonsecret web capability flags for Telegram, Threads, and LinkedIn. Unavailable providers have disabled
  light buttons without API actions; available but unconnected providers use outline-dark connection links,
  and connected providers use dark share buttons. Preserve semantic publishing outcome colors.
  Keep connection, prior-share, processing, and uncertain states; uncertain writes must never auto-retry.

- Legacy `bmc_username` records without a saved `support_links` list are read as a standard Buy Me a Coffee
  support link. The first tracked click or profile link save persists the list; an explicit list (including `[]`)
  takes precedence so removing a legacy link does not restore it.

## Connected login identities

- Resolve authentication through saved issuer/subject mappings only; never merge accounts by email. Preserve
  `origin_iss` in session cookies, and omit email index attributes when no email is supplied.
- Login connections are an explicit active-owner API flow in `login_link_service.py`. Bind pending state to the
  authenticated session, browser cookie, PKCE verifier, and nonce; claim callbacks once before exchanging codes.
  Atomically associate only unassigned identities, guarded against replacement attempts and account bans.
- Verify Cognito ID-token signatures, issuer, audience, expiry, nonce, and optional access-token hashes through
  `shared/cognito_auth.py`. Pass the exchanged access token to verification; never disable `at_hash` validation.
- Redact login/logout, login-link, and Threads callback queries from request and referrer logs.
- Ship web/API code with the Cognito callback and API environment configuration together. Infrastructure deployment
  uses the existing code bucket with the `cloudformation` prefix to support templates larger than 51,200 bytes.
- The test runner explicitly sets both `DYNAMODB_TABLE` and `TEST_DYNAMODB_TABLE` to `app_test`.
- Main-site HTTPS redirects use a separate, uncached CloudFront distribution. Deploy the certificate stack in
  `us-east-1` before the main stack so `WebCertificateArn` is available; see `docs/https-redirect.md`.

## Removed integrations

- Medium connection and publishing support was removed because its API no longer accepts new integrations.
  Do not restore token entry, publishing routes, or deployment credentials for it. Legacy stored attributes
  and share records may remain in DynamoDB and must not prevent loading users or articles.

## LinkedIn publishing

- LinkedIn connects personal accounts through API-owned OAuth2 using `openid profile w_member_social`.
  The app requires Share on LinkedIn and Sign In with LinkedIn using OpenID Connect products. Keep
  client credentials, encrypted tokens, and `LINKEDIN_API_VERSION` API-owned. The callback is
  `/integrations/linkedin/callback`; redact its query and referrers in access logs.
- Bind consent to the active owner, browser cookie, and authenticated session; claim callbacks once
  before exchanging codes. Retain guards against replaced or disconnected attempts and account bans.
- Publish only title, category, public preview, and the canonical article link through the current
  Posts API. This creates a public feed link post without a thumbnail, not a native long-form article.
  Count `linkedin_publish` separately from `linkedin_share_click`; only a confirmed 201 with a valid
  post URN updates share history. Preserve remote success on history failures; never retry uncertain
  publishing requests. Expired or revoked tokens require reconnection.

## Background work, erasure, and worktrees

- `background_handler.handler` dispatches profile sync, creator clicks, account deletion, and deletion recovery.
  It shares the API ZIP but has its own Lambda. API, web, worker, and infrastructure updates ship together only
  after explicit deployment authorization. Local services use bounded thread pools.
- Profile saves atomically persist `PROFILE_SYNC` with profile/slug edits. Propagate identities in bounded
  checkpointed transactions; unchanged saves resume interrupted work. Keep content timestamps intact.
- Signed creator click events include a unique `jti`. Authenticate expiry at API receipt, return 202 after
  dispatch, and atomically deduplicate `CREATOR_CLICK#<event-id>` with counters. Keep self-clicks excluded.
- Account deletion is owner-confirmed or provider-signature-verified, with a durable outbox and scheduled
  recovery. Pending erasure invalidates sessions; guard ordinary writes, OAuth completion, and profile sync
  against `deletion_id`. Erasure uses raw guarded persistence to repair counters without notifications.
  Preserve shared/site media; invalidate only exact deleted upload paths. Redact private receipt URLs,
  omit analytics on status pages, and retain only non-identifying completed receipts.
- New worktrees use ignored `.env.test.local` with isolated ports, Compose projects, and databases. Keep
  `.env.test` as a legacy fallback and `.env.test.example` generic. Shared production environment links must
  resolve before updates, and production tooling must take the common lock before rereading settings.

## Private messaging, website notifications, and wallets

- Participant-scoped message reads live in `shared/direct_messages.py`; API-only writes live in
  `direct_message_service.py`. Keep message bodies out of emails, notifications, public activity and logs.
  Stable client UUIDs acknowledge uncertain sends without duplicating them. Blocks in either direction prevent
  new sends. Account deletion erases the entire participant conversation and both inbox/read pointers.
- Website notification history and unread counters commit together. Presence is server-timed and throttled
  to five minutes, with a six-minute online window. Online recipients get website updates; offline recipients
  with an address also get email. Preferences control both channels. Never apply this routing to authentication
  or operator emails. Keep private endpoint responses and errors `private, no-store`.
- Guard message, presence and notification writes against pending account deletion. Presence and unread/read
  updates never change profile `updated_at`, activity, engagement counters or profile synchronization state.
- Crypto wallets are plain receiving addresses with stable IDs, not credentials or payment integrations.
  Validate article selections against its actual owner. `support_wallets: null` inherits category-aware profile
  defaults, while `[]` hides wallets. Profile visibility is independent. Never fabricate connected accounts or keys.
- Article and profile support links and wallet dropdowns share the card in `fragments/creator-support-links.html`.
  `fragments/creator-wallets.html` supplies only the wallet controls, without a separate card or heading. Visible
  wallets alone count as configured support and must not trigger the owner's faded setup preview.
  The empty owner-only preview includes a disabled generic crypto-wallet example without an address, dropdown menu,
  or copy action; never show this example to visitors or alongside configured support controls.
- Use `btn-dark` for forms and main actions, `btn-light` for neutral secondary actions, and
  `btn-primary` sparingly for exceptional emphasis. Preserve success, warning, and danger styles for
  status controls, reactions, publishing outcomes, and destructive actions. Neutral outline buttons use
  `btn-outline-dark`, never `btn-outline-secondary`. Keep status meaning explicit in labels and icons.
  Latest/Popular sort buttons use dark for the active selection and light for inactive choices.
  Article comment submission uses a small `btn-outline-dark` button.
  See `docs/examme-ports.md` for source commits, maintenance setup, delivery behavior, and JavaScript regression harnesses. Run those harnesses when changing their features.
- Category image normalization uses `scripts/normalize_public_images.py` through scripts-production. Review the
  dry run before `--apply`; retain host-mounted checkpoints, preserve prior shared objects, and verify variants
  before replacing references. The script does not register webhooks, invalidate caches, or delete public assets.
- In the shared semicolonless JavaScript, prefix standalone IIFEs with `;` when they follow an expression.
  The notification harness includes a whole-script startup case: testing only the extracted initializer
  misses earlier runtime errors that prevent its DOMContentLoaded handler from registering.

## Article metadata and dropdown controls

- Cards and article pages reuse shared `article-meta.html` for author/date/statistics and `article-badges.html`
  for category/tags; compact cards show one tag plus an overflow count. User metadata lives in shared
  `user-meta.html`; page controls live in owning web fragments. Article `category` remains the slug;
  `category_name` snapshots its display name on creation/category change using the existing validation lookup.
  Card/list fragments must not query categories. Legacy cards use context already available or the slug;
  backfill missing names through `scripts/backfill_article_category_names.py` (dry-run, then explicit `--apply`).
  The guarded backfill preserves existing snapshots, dates, counters, and indexes.
- Article moderation and reporting use owning web template fragments and the shared JSON form helper.
  Rejection reasons remain required by the API. Report submissions use dedicated report endpoints with the
  canonical article URL and a bounded message; do not introduce a web proxy or restore global moderation handlers.
- The shared image fragment accepts independent `caption` and `alt` values. Omitted captions inherit alt;
  explicit empty captions stay empty. Keep both values escaped. See `docs/promptcatalog-presentation-ports.md`.
- Run `node tests/test_article_controls_ui.cjs` for moderation and `node tests/test_user_report_ui.cjs` for both
  report forms, alongside shared form/notification startup harnesses and the required isolated Python suite.

- Profile actions share the wrapping `#user-controls` row. Keep user impression fragments inline
  without vertical margins, including API replacements, and the profile hero overflow visible for dropdowns.
- User status and report dropdowns live in owning web fragments, use explicit shared-form validation,
  and retain moderator/self restrictions. Banning requires a reason; activation clears the saved reason.
  User reports use the dedicated report API, canonical profile URLs with ID fallback, and a 1,000-character
  message budget. Run `node tests/test_user_status_ui.cjs` and `node tests/test_user_report_ui.cjs`.

- Profile calendars must follow every activity query page, including hidden-only pages. Current article/comment
  counters cannot establish whether historical activity exists. Limit recent entries separately from calendar totals.
- User/article reports use `POST /users/{user_id}/report` and `POST /articles/{article_id}/report`. Persist contact
  target IDs and cumulative report counters atomically, guard visibility/existence/erasure, reject self-reports
  including administrators, and preserve content dates. Never infer counters from historical contact text.
  Report Details fields are named `message` for inline backend validation; load the validation asset on both pages.
  Keep detailed engagement metadata owner-only and operator notifications after successful persistence.

## AWS-backed production configuration

- Production configuration migration is implemented in `scripts/production_config.py`; see
  `docs/production-configuration.md` and `.env.prod.example`. Each credential has its own raw-string Secrets Manager
  secret; each nonsecret setting has its own SSM String parameter. Never bundle values into aggregate JSON records.
  Classify new keys explicitly; never infer sensitivity from names or store unreviewed credentials in plaintext parameters.
- Resolve current records by name under the shared production lock. Make receives only nonsecret settings;
  the deployment helper sends raw CloudFormation parameter values through a memory-only descriptor, never argv or disk files.
  Do not pin versions or add refresh markers. Application startup remains environment-only; do not add runtime
  store calls or automatic refresh. Store updates require explicit deployment.
- Production scripts bootstrap credentials after container startup; keep raw values out of Docker Config.Env,
  argv, generated env/build files and logs. Their shared lock covers execution. Worktrees use `PROD_CONFIG_DIR`
  and `PROD_CONFIG_FILENAME` to mount the resolved main bootstrap directory. Older checkouts require the loader.
- The migration runs through scripts-production, defaults to a redacted dry run, and requires an explicit command
  for `--apply`. Read back all individual records before replacing the local file; preserve existing keys,
  artifact timestamps, cache counters and worktree symlinks. Never regenerate signing/encryption keys or overwrite different records.
  Migration does not authorize deployment, commits, pushes, webhook changes or cache invalidation.

- The shared production lock lives beside the resolved bootstrap as `.production-deploy.lock`, outside `.tmp`.
  Build and clean targets remove Lambda vendor directories; never place the common lock inside those directories.
- Production Compose must mount bootstrap directories, not `.env.prod` as an individual file. A file bind mount
  prevents atomic bootstrap replacement even when migration uses another directory alias of the same file.

- Make only consumes nonsecret owner/stage/domain/zone deployment settings; application values and asset counters
  belong to `production_config.py`. Build cleanup must preserve unrelated `.tmp` checkpoints and importer state.
