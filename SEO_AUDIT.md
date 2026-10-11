# SEO and technical audit

Audit date: October 10, 2026 (America/Chicago). Production observations describe the site **before** these local changes. Nothing was deployed and no production content was edited.

## Coverage and evidence

- Crawled 983 public URLs initially, including every one of the 969 sitemap entries, all 281 article pages, public directories, policy pages, and representative invalid/filter/tracking URLs.
- Made 324 additional checks of remaining internal article destinations, article schema images, and first-party static assets. Across both passes, checked 1,287 distinct URLs; duplicate requests were retained for comparison.
- Inspected route handlers, URL builders, authorization, templates, canonical/robots metadata, JSON-LD, image markup, JavaScript pagination, sitemap generation, static builds, and CloudFront/API Gateway configuration.
- Local raw crawl results: `.tmp/seo-live.json` and `.tmp/seo-live-extra.json`. These files include response headers, timings, metadata, links, image attributes, and structured data. They are ignored by Git.
- The crawl followed redirects. A successful final response does not imply that the original URL avoided a redirect. HTTP and `www` probes failed before receiving an HTTP response.

This is a public crawl and source audit, not a Search Console, ranking, backlink, or browser performance audit. Google-selected canonicals, indexing coverage, mobile rendering, field Core Web Vitals, accessibility interactions, external links, and every responsive image variant were not independently verified. Authenticated behavior was checked in the isolated local test stack. Dynamic combinations of every filter and pagination cursor were not exhaustively crawled.

## Findings fixed locally

| Priority | Finding and evidence | Change |
| --- | --- | --- |
| High | The static build copied `static/sitemap.xml`. The local file contained `http://web-lambda:5000` test URLs, so a routine asset deployment could overwrite the production-generated sitemap. | Static builds exclude the generated sitemap. |
| High | Private article pages hard-coded `index, follow`, even for drafts/rejected content. Banned profiles also declared themselves indexable. Authorization already restricted access, but the metadata contradicted publication status. | Nonpublished articles, nonactive profiles, and private profile article views emit `noindex, nofollow`. Authorization remains enforced. |
| High | Archive pagination used buttons that fetched API fragments; crawlers had no normal links to successive HTML pages. The shared tag query also discarded its DynamoDB cursor after the batch read and lost chronological ordering through a set. | Made Load more a normal next-page link on the homepage and article, user, tag, and profile archives, with JavaScript enhancement and links back to the first page. Preserved query ordering and the original tag-query cursor. HTML archives retain pagination in canonicals. |
| Medium | Article/profile ID routes served duplicate HTML alongside their slug URLs. Canonicals already pointed to the slug, but both copies were crawlable. | ID routes permanently redirect with 308 when a slug exists, preserving query strings. Entities without slugs retain their ID URLs. |
| Medium | Tag prefix searches and tag pagination canonicalized to the base tag directory despite different content. | Canonicals retain prefix, offset, nondefault limit, and sort; prefix searches emit `noindex, follow`. |
| Medium | Unknown category filters returned an indexable 200 page with the generic archive title; unknown tag/empty result pages were also indexable. | Unknown categories and empty article archives return usable 200 listings with `noindex, follow`, retaining the filtering UI and requested category title. This follows the later sibling commit’s empty-page behavior. |
| Medium | Every article's publisher reused its author Person ID while declaring that identity an Organization. All 281 live article schemas had this collision. | Publisher is the site organization with its own `#organization` ID; author remains a Person. |
| Medium | 215/281 article titles exceeded 65 characters; titles appended `by Author ~ SysDesPro`. This is a display heuristic, not a Google length limit. | Titles use `Article title | SysDesPro`; author remains in the byline, author metadata, and JSON-LD. On the audited corpus, 47 would still exceed 65 characters because the article headings themselves are long. |
| Medium | Article descriptions began by repeating the heading. Privacy and terms reused the home description. Category/tag archives with the same subject shared titles. | Article descriptions start with the preview/body; privacy and terms have distinct descriptions; category titles distinguish categories, and the generic archive no longer shares the System Design tag title. |
| Medium | Sitemap XML inserted URLs directly, omitted privacy, read only one tag page, and assigned regeneration-day `lastmod` dates to static and archive pages. | XML serialization escapes URLs, deduplicates entries, includes privacy, paginates tags, and omits unsupported modification dates. Existing article/profile timestamps are retained, with publication-time fallback for articles. Large sitemaps split into bounded parts and a sitemap index. |
| Medium | The web Lambda stored the current request in shared Jinja globals. Concurrent requests could render navigation/URL context using another request. | Rendering supplies request-local context using a ContextVar; error handlers explicitly pass their request, including 500 handlers outside the middleware context. |
| Low | Article-status breadcrumb links mistakenly passed the status as a sort type. The layout repeated `apple-mobile-web-app-title`, and charset declaration followed analytics markup. | Corrected breadcrumb parameters, removed duplicate metadata, and moved charset to the start of the head. |
| Low | Profiles without usernames could include a stringified null value in descriptions. | Missing usernames now contribute an empty string. |

The shared query fix remains in `shared` because both the web archive and API article fragments call it through `get_articles`. Web rendering and pagination composition remain in `web-lambda`. No stylesheet changes were needed.

## Remaining work, in priority order

1. **Restore HTTP entry-point redirects.** `http://sysdespro.com/` refused connections in this audit. The root website is served directly by API Gateway, while CloudFront hosts only static assets. Provide an HTTP-capable edge entry point that permanently redirects to the corresponding HTTPS path and preserves the query. This requires a hosting design change; application middleware cannot redirect a connection that never reaches it.
2. **Add a `www` redirect if that hostname should work.** `www.sysdespro.com` did not resolve. Provide DNS, TLS coverage, and a permanent redirect to the apex HTTPS site. Missing `www` is a usability/link-recovery issue, not evidence of duplicate indexing.
3. **Reduce repetitive taxonomy pages.** Of 672 observed CollectionPage responses, 385 displayed one item. Grouping by the unordered set of listed item URLs found 271 repeated groups covering 651 responses. For example, the gRPC, Protocol Buffers, and HTTP/2 latest/popular archives listed the same article. This does not establish duplicate article content or a penalty. Choose which categories/tags should be search landing pages, merge genuine aliases (for example, investigate `database`/`databases` and `read-replica`/`read-replicas`), add useful topic introductions, and consider canonical/noindex rules for alternate sorts and combinations. Do not canonicalize distinct topics together solely because their current article sets happen to match.
4. **Automate sitemap refresh after publication, slug changes, and removals.** Generation currently requires the admin endpoint. IndexNow notifications exist but do not refresh the sitemap. A scheduled or queued refresh would keep it current without making publication wait for a full-site scan. Regenerate once after deploying this patch.
5. **Normalize stored internal links.** Eleven additional checks followed redirects, including old article slugs and author paths without `@`. Update those stored links to their final canonical destinations through an explicit content migration. Redirects already prevent broken destinations; production content was not changed in this audit.
6. **Improve measured performance.** Public HTML fetches had roughly 1.25-second median, 2.20-second p95, and 4.91-second maximum elapsed time in the initial crawl. These are whole network fetch measurements from one location, not browser TTFB or Core Web Vitals. Measure with Lighthouse/PageSpeed and Search Console, then address Lambda cold starts, repeated DynamoDB lookups, and cacheable anonymous rendering. Any HTML caching must protect personalized/authenticated pages.
7. **Improve remaining structured-data details.** Author profiles now use ProfilePage with a stable Person identity, and same-day modifications are included consistently. Track content modification time separately if generic `updated_at` can change for bookkeeping. Twelve articles had no article-specific schema image; add relevant editorial images where useful rather than treating the site logo as article content.
8. **Prepare for data growth.** Sitemap generation now splits automatically at 50,000 URLs/50 MiB, and category-plus-tag queries scan successive category pages to fill results. Sparse filter combinations can still require many reads. The shared tag batch read still needs chunking above DynamoDB's 100-key batch limit and handling of unprocessed keys under throttling; the normal 40-item public page avoids the batch-size limit. These need separate persistence/query work and load-oriented tests.
9. **Review asset freshness and accessibility.** Static JavaScript/CSS and images retain their existing aggressive cache policy. Continue changing their version query values or filenames on updates. Seven observed article-page images lacked explicit width/height, though surrounding aspect-ratio containers may reserve space; confirm actual layout shifts in a browser. Label icon-only social links when configured, and verify keyboard navigation and filter form labels in a browser.

## Checks that passed

- Every sitemap destination resolved to HTTP 200; the sitemap contained 969 unique URLs and valid XML. The sitemap itself did not contain local/test URLs in production at audit time.
- No identical article titles or identical article bodies among the 281 articles. A normalized five-word-shingle comparison found no article pair above 20% Jaccard overlap; this is a lexical check, not a semantic originality or topic-cannibalization judgment.
- Every crawled HTML page had an H1. Public HTML pages had canonical tags; the deliberately requested 404 correctly omitted one.
- Tracking parameters canonicalized to the clean homepage. Reordered tag combinations canonicalized to a sorted tag URL. Trailing slashes and sampled legacy article URLs redirected successfully.
- All crawled JSON-LD parsed successfully. Every article supplied a publication date, author, and English language metadata. JSON validity alone is not rich-result eligibility.
- Checked remaining internal article links, schema images, favicons, manifest, stylesheet, and JavaScript assets resolved successfully. Article-page images had nonempty alt text in the initial crawl.
- Live robots.txt advertised the production sitemap and allowed public crawling. Login/logout paths were disallowed; auth responses also carry noindex headers in code. Static-host robots.txt allowed image/asset crawling.
- Templates server-render their principal content and use HTML autoescaping plus JSON serialization for structured data. HTTPS canonicals use the configured site origin, and public API Gateway default endpoints are disabled in infrastructure.

## Validation and deployment

Regression checks cover draft/published indexing, permanent article/profile ID redirects, query preservation, tag-search canonicals, crawlable archive pagination, empty category filters, sitemap XML escaping/deduplication/tag pagination/dates, ordered tag-query pagination, concurrent render isolation, static-build sitemap exclusion, and exception rendering. The complete isolated Docker suite passed: **207 tests and 57 subtests**, in 15.80 seconds. `git diff --check` also passed. Run the complete suite with `make tests`.

The existing CloudFront cache policy is retained. Sitemap generation requests invalidation of `/sitemap*` so the root sitemap and any split files are refreshed on every generation; `cf.yml` is unchanged.

Deployment requires the web Lambda and API Lambda changes. Then use the authenticated admin sitemap-generation action to replace the existing sitemap and invalidate its CDN cache; confirm the sitemap contains canonical production URLs and changed archive pages expose next-page anchors. Follow up with Search Console URL Inspection and sitemap submission status.

## October 10 promptcatalog comparison

Reviewed the October 10, 2026 commit series in `../promptcatalog`. The transferable SEO work is concentrated in `36278d4`, `cf1d965`, `11c0f23`, and the stylesheet-loading portion of `05bf4b4`.

| Source | Applied here | Reason |
| --- | --- | --- |
| `11c0f23` sitemap generation | Stream entries into bounded XML parts; retain a plain `sitemap.xml` for small sites and publish a sitemap index for large sites. Keep deduplication, XML escaping, real dates, and complete source pagination. Invalidate `/sitemap*` after generation. Exclude generated parts from static builds too. | Avoid failing as content grows or constructing one large XML tree. The deduplication set still grows with the number of unique URLs. |
| `11c0f23` filtered URLs and queries | Sort and deduplicate tag filters in the DTO and URL builder. Fill category-plus-tag pages across category-query pages while retaining the scanned cursor; clear it on exhaustion. | Equivalent tag sets get consistent internal links, and filtering no longer hides later matching articles behind an empty or incomplete first page. Both Lambdas use these shared domain/query helpers. |
| `cf1d965` progressive pagination | Use one crawlable Load more anchor, preserve modified clicks/new tabs, and update its href as AJAX advances. Add first-page links and keep profile pagination parameters consistent with its canonical. | The prior separate next-page link could become stale after automatic loading. The web-only fragment now lives in the web Lambda. Comments retain a button because they have no standalone HTML pagination route. |
| `11c0f23` structured data | ProfilePage wraps a Person with a stable `#person` ID across pagination. Article/comment authors reference that identity; articles have `#article` IDs, and breadcrumbs belong to their WebPage entity. Include same-day modification dates. | Separate page and person/article identities and use page-specific schema properties correctly. Keep Article for editorial articles; the sibling's CreativeWork choice is specific to prompts. |
| `36278d4` metadata | Plain-text descriptions truncated at word boundaries, clearer archive descriptions, category social images, accurate fallback-image alt text, and Twitter image alt text/card fallback. | Cleaner snippets and more accurate social previews. The character target is an editorial convention, not a search-engine limit. |
| `05bf4b4` stylesheet loading | Load icon CSS and conditional Tagify CSS in the head. | Avoid rendering initially unstyled icons and filter controls. This makes those styles render-blocking; browser performance measurement remains follow-up work. |

The rest of the series mainly concerns prompt discovery, creator terminology, model/media navigation, template readability, copy feedback, and card/homepage styling (`7a0e206`, `de8500f`, `6f3c38b`, `33244de`, `03ae78c`). Those product-specific changes were not copied. Responsive media priorities should be chosen from this site's own layout and measured LCP rather than copied with prompt-specific breakpoints. `a39f595` supplies tests for that sibling's changed homepage/filter behavior; equivalent behavior here is covered by this project's own regressions. The importer changes are unrelated to website SEO.

Sitemap filenames are stable, matching the sibling's approach. Parts are uploaded before the index, and stale parts are no longer referenced after a smaller generation; automatic deletion is not included. Multi-file updates are not transactional, so a failed generation can leave changed parts behind. Generation is still an admin action; no production regeneration or deployment was performed.

Additional validation: JavaScript syntax check plus an isolated pagination-handler check confirmed modified clicks preserve navigation and AJAX advances both the cursor and fallback href while preserving filters. The complete Docker suite includes URL-count/byte splitting, XML escaping/deduplication, oversized-entry rejection, sitemap coverage/pagination/dates/invalidation, filtered query exhaustion, and profile pagination/schema identity. The sibling repository was inspected without modification.

## Latest sibling commit: 3abd474 (October 11)

Adapted `Improve autocomplete counts and empty filtered page handling` after the October 10 comparison:

- The tag API accepts `published_only=true` and scans successive pages to fill suggestions with tags whose published article count is positive. Public article filters request this option; editors retain the unfiltered API behavior. Dropdown labels now show escaped article counts with singular/plural labels. This site has no model catalog or homepage autocomplete, so their equivalents were applied to its existing article-filter autocomplete instead.
- Tag prefix queries use the common cursor-aware query executor in ascending name order, respecting incoming offsets and returning continuation tokens. Both API suggestions and the web tag directory use this persistence helper.
- DynamoDB query errors, including ValidationException, are logged and propagated instead of being converted to empty results. Invalid starting keys and missing indexes therefore surface as errors requiring correction rather than misleading empty content.
- Unknown category filters render empty 200 listings with the requested category in the heading. Empty listings keep this site's existing `noindex, follow` policy. The sibling's `index, follow` choice for empty pages was not copied.
- Prompt parameter whitespace changes do not apply to article templates. Model suggestions and homepage category chips do not exist here.

The API-only suggestion orchestration remains in `api-lambda/app.py`. Added functional coverage checks pagination past empty suggestions, published-only discovery, unfiltered editor compatibility, and missing prefixes; persistence tests cover prefix ordering/cursors and propagated query errors. All **207 tests and 57 subtests** pass. An isolated JavaScript check confirms counts survive API mapping, text is escaped, singular/plural labels are correct, and the published-only option survives autocomplete requests. `cf.yml` remains unchanged.

## References

- [Google: canonical URL consolidation](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
- [Google: pagination and incremental loading](https://developers.google.com/search/docs/specialty/ecommerce/pagination-and-incremental-page-loading)
- [Google: article structured data](https://developers.google.com/search/docs/appearance/structured-data/article)
- [Google: building and submitting sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- [Google: managing faceted navigation](https://developers.google.com/search/docs/crawling-indexing/crawling-managing-faceted-navigation)

- [Google: profile page structured data](https://developers.google.com/search/docs/appearance/structured-data/profile-page)
- [Google: sitemap indexes](https://developers.google.com/search/docs/crawling-indexing/sitemaps/large-sitemaps)
