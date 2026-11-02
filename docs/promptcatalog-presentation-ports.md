# PromptCatalog presentation ports

Reviewed committed history through `64f2ef9` (October 31, 2026). Uncommitted generation and credit work
in the sibling checkout is excluded.

- `044d5fb`, `1de0940`: shared metadata for article cards, detail pages and API fragments. Compact cards show
  one tag and an overflow count; detail pages show all tags. Existing category display casing is preserved.
  Article-list fragments load the category catalog once when it is not already available, avoiding a query
  per card. Author and publication/modification dates remain available in the metadata.
- `5cf7665`: a status dropdown, grouped feedback, and a reason-based article report form. Status submission
  uses the shared JSON form helper with inline errors. SysDesPro's backend still requires a comment when
  rejecting an article. Reports use the existing public contact API, include the canonical article URL,
  and bound the generated message to its existing size limit. Moderation authorization remains server-owned.
- `0eb327c`, `291472e`: compact card heading/rating hierarchy and title-first detail metadata/actions.
- `9266225`: the shared image template accepts an independent `caption`; omitted captions retain the alt-text
  fallback and an explicit empty caption stays empty. Text continues to be escaped.
- `64f2ef9`: plain creator support cards. Connected-provider publishing remains below the author details.

Keep metadata in `shared/templates/fragments/article-meta.html` so initial and paginated cards stay aligned.
Keep article-specific moderation/report scripts in their own web template fragments, initialized after deferred
shared dependencies. Do not restore the replaced global publish/reject click handlers.

Verification uses `make tests` and the existing JavaScript harnesses, plus
`node tests/test_article_controls_ui.cjs` for moderation and report preparation.
