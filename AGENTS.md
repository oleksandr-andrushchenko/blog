# Repository agent instructions

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
- Record useful, durable repository knowledge in this file when work reveals conventions, tooling limitations, or
  build and deployment requirements that will help future changes.

## Shared code and Lambda boundaries

- Keep `shared` limited to genuinely shared domain models, persistence primitives, validation, and helpers used by both Lambdas.
- Before adding or keeping a helper in `shared`, search its callers across `api-lambda` and `web-lambda`. If it is used by only one Lambda, place it in that Lambda's module; web-only helpers belong in `web-lambda/web_utils.py`.
- Lambda-specific orchestration, page composition, and presentation-oriented query helpers must remain in the owning Lambda.
- Shared HTML fragments used by both Lambdas belong in `shared/templates/fragments`; keep initial page renders and
  fragments returned by API endpoints consistent.
- The web Lambda owns page rendering; resource mutations, uploads, and resource API responses belong to the API Lambda.
  Browser scripts should call the configured API URL directly rather than adding web routes that proxy API requests.
- Keep authentication cookies and supporting token logic compatible between the web and API Lambdas.
- Keep the web Lambda dependency footprint minimal to reduce bundle size and cold-start time. Add API-only or
  heavyweight dependencies there only when page rendering or authentication requires them.
- Register application environment settings centrally in `shared/app_config.py` and read them through its accessors or
  `get_config()`. Keep direct environment reads in central configuration or explicit process-bootstrap code.
- Template request state belongs in `template_request` (a `ContextVar`), never in cached Jinja environment globals.
  Pass requests explicitly to error rendering when exception handling can occur after middleware resets the context.

## Templates and static assets

- Store layouts and page templates in the owning Lambda's templates root; store reusable partial HTML templates in
  `fragments`.
- Build internal links with named route helpers (`url(...)`, `article_url(...)`, etc.). Append section fragments to a
  generated endpoint URL rather than using bare fragment-only links.
- Serve web assets and store web uploads under the repository-level `static` directory, exposed at `/static`.
- Prefer native Bootstrap classes and components for UI changes; avoid modifying `static/styles.css` unless the requirement cannot be implemented cleanly with Bootstrap.
- Generate responsive Bootstrap grid sizes with the Jinja `row_classes` and `col_classes` filters rather than
  hardcoding `row-cols-*` or sized `col-*` classes. Plain `row` and `col` may remain when no sizes are specified.
  `col_classes` includes the base `col` class; use `"def": 12` when a responsive column must stack at full width.
- Bootstrap and Bootstrap Icons are loaded centrally in `web-lambda/templates/layout.html`.
- Do not underline non-link headings. Apply link decoration to anchors rather than heading containers.
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
