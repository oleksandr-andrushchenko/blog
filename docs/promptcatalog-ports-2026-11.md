# PromptCatalog monthly review: November 22, 2026

Reviewed all 213 committed changes in `../promptcatalog-2` dated October 22–November 22,
through `851fc6d` (`feature/2`). The sibling checkout was read only. Ports use article
models, existing application services, route names, and SysDesPro's production configuration;
its branding, secrets, production data, and generated assets were not copied.

## Added or adapted

| Source commits | SysDesPro adaptation |
| --- | --- |
| `8447ee2` | Shared JSON parsing rejects empty, malformed, non-object, and invalid-encoding bodies with 422. Access/error logs include bounded diagnostics, never submitted bodies or credentials. |
| `0018485` | UTC date formatting and UTC log formatters, independent of host timezone. |
| `2ff6a16` | Article/comment creation increments compact UTC day/type activity counters in the existing account-guarded transaction. New accounts use counters immediately; old accounts read counters plus full legacy history until reviewed activation. |
| `347c658` | All isolated Python test services install an outbound transport guard before imports; Docker's test network has no public egress. Blocked calls fail the suite, even when application code catches an exception. |
| `f3fc812`, `ebed94d` | Production scripts take a locked configuration snapshot, release the lock before the command, and derive the Lambda-compatible email sender when unset. Deployments still retain their lock throughout execution. The lock stays outside disposable build directories. |
| `6d736db`, `086704e`, `6ed4fa6` | Private article bookmarks with guarded aggregate counts, private pagination, initial server-rendered state, loading feedback, owner notification preference, anonymous recipient copy, and erasure cleanup. Membership is never exposed on public lists. |
| `670320d`, `851fc6d`, `0527f13` | Browser-bound sign-in with state, nonce, PKCE, one-time callback claiming, desired-state action completion, account/session-bound continuations, welcome acknowledgment, and ASCII-safe Unicode return URLs. Existing same-origin/auth-loop and cursor-free return rules are preserved. DynamoDB TTL cleans up temporary records; explicit expiration checks remain authoritative. |
| `a90f0d0`, `434a591` | Guest comment text survives sign-in in tab-scoped browser storage with a 15-minute expiration, is restored for review, and is removed after successful posting. No automatic comment submission. |
| `ee6447d` | Restrained guest signup encouragement after three distinct article views and 60 foreground seconds, once per session, with seven-day dismissal cooldown. Forms, active interactions, and open modals defer it; unavailable storage disables it. No AI-credit promises. |
| `0a0fbdd`, `32e9ee2` | Opt-in verified Telegram/Threads public identities on profiles and article pages, cached without credentials or public-page connection queries. Profile visibility uses the existing Save button. Integration cards remain hidden until API availability is confirmed. |
| `2876937` | Optional Pinterest OAuth, owned public-board selection, and owner-only explicit publishing of an article cover/title/public preview/canonical link. Encrypted tokens stay API-owned; UUID receipts prevent repeating uncertain writes. Optional website Pinterest/Telegram/Threads links are separate public settings. |
| `843914d` | Bounded published-only selection of more articles by the same active author, excluding the current article and preserving related-article pagination. |
| `108f740`, `d7389f2` | Responsive collapsed article/user filter options while sort controls remain available. Existing category/tag serialization and semantic status colors are preserved. |
| `33e2bd9` | Preserve browser replacement placeholders in generated URLs while retaining Unicode encoding and request-free canonical route generation. |
| `3b8c71d` | Remove the obsolete wildcard-cache target from generated worktree wrappers; keep the existing exact-path sitemap/erasure behavior. |
| Action logging guidance | Successful article reactions log their canonical resource, actor, and previous/resulting action after persistence. Replayed desired-state actions remain no-ops. |

## Public terminology follow-up

The public “Tags” → “Topics” presentation change (`145f248` and related wording commits) was missed
in the original pass and is now included: navigation/footer, listing titles and metadata/breadcrumbs,
article filters/editor, finder suggestions, badge tooltips, subscription copy, emails, and public guidance.
Existing `/tags` URLs, route names, form/API fields, stored keys, and user-authored topic names are preserved.

## Already present

The earlier ports documented in `promptcatalog-presentation-ports.md` and `examme-ports.md` already cover shared metadata and badges,
moderation/report forms, count formatting, complete activity pagination, messaging,
website notifications/presence, wallets/support links, and publisher control styling.
The current repository also already has account erasure/recovery, background profile sync,
explicit login identities, Cognito signature/access-token-hash checks, Apple/Facebook setup,
HTTPS redirects, large CloudFormation template uploads, source-neutral importer safeguards,
private media originals, isolated worktrees, dummy-asset exclusion, per-value AWS configuration,
and exact-file sitemap invalidation. These were compared rather than overwritten.

## Intentionally excluded

- Prompt generation, templates/parameters, copy counts/guidance, model catalogs, credits,
  generated examples, prompt enrichment/FAQ generation, and prompt SEO wording do not map to articles.
  Article prose/provenance/editorial rules and existing structured metadata remain authoritative.
- Prompt-specific importer pipelines, source-author account management, saved Threads sources,
  Telegram exports, and website GraphQL source readers are not article importers. In particular,
  the anonymous Threads website reader conflicts with this repository's official-API access policy.
- Media-only card modes, prompt-output viewers, and image/video result comments are designed around
  prompt result collections. Articles retain full rendered bodies, covers, and text discussions.
- Directory badges (Dang, Startup Fame, Submit AI Tools), AI-credit signup promises, and vendor-specific
  promotions require site-specific eligibility/configuration and were not enabled or copied.
- Prompt catalog home-page ordering, sparse-list indexing thresholds,
  and blanket typography/spacing rewrites were not imposed on an editorial article site.
  Existing article canonicals, tag indexes, pagination restrictions, and metadata casing remain intact.
- No legacy cleanup, publication, deployment, invalidation, or secret creation was performed.
  Catalog metadata backfill was subsequently requested explicitly; see below. The existing uncommitted `navbar-expand-lg` edit was retained.

## Rollout and verification

Run `make tests` and all `tests/test_*_ui.cjs` browser harnesses. New coverage includes private bookmarks,
notification preferences and erasure, sign-in replay/cross-browser/session rejection, Unicode/cursor return URLs,
UTC behavior, activity migration races, network isolation, integration visibility, and mocked Pinterest transport.
No live provider requests are required for tests.

When separately authorized, ship API, web, background worker, shared static scripts, and infrastructure together.
`static/comment-draft.js` is a tracked static asset. The shared JavaScript production cache counter was incremented.
The infrastructure adds DynamoDB TTL and optional Pinterest/public-social settings; credentials must be provisioned
separately before enabling a provider. No new indexes are required.

Activity history maintenance is optional and defaults to dry run:

```sh
docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
  python3 scripts/migrate_activity_counters.py --all-users
```

Only an explicitly authorized apply may add `--apply --writers-ready`, after compatible writers are deployed and
old invocations have drained. Historical details are retained unless `--prune-legacy` is explicitly requested.
Migration stages each source record atomically with counters, verifies totals before activation, and resumes safely.
Existing users continue to work without migration. Account erasure removes both raw and daily activity partitions.

Bookmark count repairs are exceptional maintenance, not a page-rendering operation:

```sh
docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
  python3 scripts/rebuild_bookmark_counts.py --production --all
```

Only an explicit `--apply` command writes guarded repairs. See `pinterest-integration.md` for provider setup and
`production-configuration.md` for individual production records and lock behavior.


## Catalog descriptions and page copy

The follow-up ports separate visible `description` and `meta_description` fields on topics and categories,
including topic-card descriptions, edit/create controls, plain-text validation (optional, at most 1,000 characters), and compatible defaults
for old records. Web-owned `page_metadata.py` composes page headings, descriptive paragraphs, and search
summaries from already loaded entities. Topic listings say “Recently Added Topics”. Topic/category
collections show saved descriptions below the H1 and use their independent search summaries in metadata.
Intersections retain scope-specific summaries. Article previews and user headline/about fields supply
existing article/profile descriptions; no duplicate stored fields were added to these entities.

`scripts/enrich_catalog_descriptions.py` exports missing catalog fields with saved-value snapshots and
optional public article examples. It never calls an LLM API. Fill the exported `changes` objects with
reviewed copy, then preview and apply through `scripts-production`:

```sh
docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
  python3 scripts/enrich_catalog_descriptions.py --production --limit 10000 \
  --include-examples --plan-file .tmp/catalog-copy-input.jsonl
# Author/review the changes in an ignored plan file.
docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
  python3 scripts/enrich_catalog_descriptions.py --production --limit 10000 \
  --apply-plan .tmp/catalog-copy-reviewed.jsonl
# Repeat the preview command with --apply only when the backfill is authorized.
```

Updates conditionally compare name and both description fields, preserve timestamps/counters, and skip
already applied values. Changed-since-review conflicts exit nonzero and require a fresh review. Existing
nonempty copy is preserved unless an explicit refresh plan permits replacement. Production backfills do
not deploy page or API code.


On November 22, 2026, the explicitly requested production backfill filled visible descriptions and
search summaries on 383 topics and missing search summaries on 16 categories. Existing category
introductions were preserved. Final consistent readback matched all 399 reviewed records, and the
catalog inventory had no remaining missing description/search-summary fields. Review inputs,
dry-run/apply output, and verification output remain under ignored `.tmp/catalog-copy-sync/`.
Validation: `make tests` passed 1,296 tests and 131 subtests; all 14 UI harnesses and
`git diff --check` passed. Application changes remain uncommitted and undeployed.


The fixture/SEO follow-up also updates `generate_dummy_fixtures.py`: all 24 fixture topics and
16 categories receive separate visible descriptions and search summaries through application services.
Existing nonempty copy is retained. Topic names are made readable only while they still match the slug.
The fixture CLI test verifies the complete catalog fields and existing article/media behavior.

Additional adapted SEO parity includes descriptive directory and author search titles, a content-focused
homepage H1, prefix-search topic headings, and optional count prefixes from already loaded public
category/topic/author totals above 15. Counts never use page sizes or unfiltered totals for intersections
or private statuses. No whole-catalog count query is added to the homepage. Message, account, and creator
pages use the same web metadata helper; creator summaries mention only enabled publishers. The layout
no longer cuts descriptions at 155 characters, so complete summaries reach search/social/structured data.
Prompt-specific model, output-format, premium/free-copy claims, and sparse-prompt indexing thresholds
remain outside this article adaptation.

Fixture/SEO follow-up validation: `make tests` passed 1,306 tests and 131 subtests;
all 14 UI harnesses and `git diff --check` passed. No deployment, commit, production
maintenance, or development database reset was performed in this follow-up.
