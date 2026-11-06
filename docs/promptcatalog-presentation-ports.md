# PromptCatalog presentation ports

Reviewed committed history through `2c70e12` on sibling branch `feature/2` (November 4, 2026).
Prompt generation, credit, importer, and prompt-template features are excluded where they have no article equivalent.

- `044d5fb`, `1de0940`: shared metadata for article cards, detail pages and API fragments. Compact cards show
  one tag and an overflow count; detail pages show all tags. Existing category display casing is preserved.
  Category lookup for card names has since been replaced by article snapshots (see below).
  Author and publication/modification dates remain available in the metadata.
- `5cf7665`: a status dropdown, grouped feedback, and a reason-based article report form. Status submission
  uses the shared JSON form helper with inline errors. SysDesPro's backend still requires a comment when
  rejecting an article. Reports initially used the public contact API (superseded below), include the canonical article URL,
  and bound the generated message to its existing size limit. Moderation authorization remains server-owned.
- `0eb327c`, `291472e`: compact card heading/rating hierarchy and title-first detail metadata/actions.
- `9266225`: the shared image template accepts an independent `caption`; omitted captions retain the alt-text
  fallback and an explicit empty caption stays empty. Text continues to be escaped.
- `64f2ef9`: plain creator support cards. Connected-provider publishing remains below the author details.

Keep metadata in `shared/templates/fragments/article-meta.html` so initial and paginated cards stay aligned.
Keep article-specific moderation/report scripts in their own web template fragments, initialized after deferred
shared dependencies. Do not restore the replaced global publish/reject click handlers.

Verification uses `make tests` and the existing JavaScript harnesses, plus
`node tests/test_article_controls_ui.cjs` for moderation and `node tests/test_user_report_ui.cjs` for report forms.

## November 4 update

- `c1aa3c8`: activity calendars follow every query page, including pages containing only hidden activities.
  Current contribution counters no longer suppress historical reads; recent entries remain separately limited.
- `88d884a`: user/card metadata and page actions use reusable shared and web-owned fragments respectively.
  Article category/tag badges are separate from author/date/statistics metadata. Configured category casing,
  one-tag compact cards and publication/modification dates are preserved.
  Counts use comma grouping and omit empty statistics; reaction controls keep their labels and small sizing
  on initial renders and API replacements. Detailed profile engagement remains owner-only.
- `88d884a`: user and article reports use dedicated API endpoints. Contact storage and cumulative target/owner
  counters commit atomically without changing resource dates. Self-reports are rejected for all roles, invisible
  articles cannot be reported, and writes guard target existence, article status/owner, and pending erasure.
  Saved target IDs integrate with existing erasure cleanup. Existing free-form contacts do not affect counters.
  Details use the `message` field so backend errors attach to the visible control. Profile pages explicitly
  load validation. Operator notifications run best-effort after storage to preserve successful submissions.
- `d031e4b`: comment submission uses a small outlined dark button.

Earlier creator, navigation, notification, publisher, and integration styling commits are already represented
in this repository. `2c70e12` collapses prompt-template internals beside its copy action; articles have neither
that template nor that action, so it is excluded. No index migration or environment setting is required.

Additional regression coverage: `tests/test_reports.py`, `tests/test_user_activities.py`, and the functional
concurrent report test. `node tests/test_user_report_ui.cjs` now covers both user and article report forms,
including validation errors, draft preservation, guest submission, and permission failures.

## Homepage article finder

The sibling homepage autocomplete is adapted for article categories and tags. The web-owned
`fragments/article-search.html` uses the homepage's existing category catalog and the existing
published-only tag API; it adds no controller queries or backend endpoints. Suggestions include public
article counts and navigate to the existing category/tag listings. Browse and write links remain usable
without JavaScript. Article-title full-text search and prompt-specific model filters are outside this port.
The total beside Meet the community sums all category publication counters already loaded for the page.
Article listings also offer a category dropdown alongside tag filters. The category catalog is loaded
once in the listing template for the options. Changing category retains tag, sort, and status
filters; selecting All categories omits the empty slug. Run `node tests/test_article_filters_ui.cjs`
for form serialization coverage.

Run `node tests/test_home_search_ui.cjs` for debounce, cancellation, selection, escaping, and fallback
behavior, alongside `make tests` for rendered public suggestions and filtered listing coverage.

## Article category snapshots

Articles retain the existing `category` slug and save `category_name` using the category lookup already
required for creation/category changes. Cards use that saved name, preserving casing and escaping it,
without category queries from list fragments. Older records fall back to already available category context
or the slug; the category catalog is still needed by homepage/category pages and listing filter options.
Category metadata edits do not automatically rewrite article snapshots.

For existing records, run `python scripts/backfill_article_category_names.py` through `scripts-production`
for dry-run review, then add `--apply` only with explicit production-write authorization. The script loads
categories once, follows scan pages, skips existing names, and guards each write against a changed category
or newly saved name. It changes no dates, counters, indexes, or publication state. No index migration is needed.
