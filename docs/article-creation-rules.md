# Article creation rules

Read this entire file before planning, creating, rewriting, or revising a SysDesPro article or draft.
This is the central repository reference for article creation, including research, writing, metadata,
application saves, and verification. Explicit user instructions and the repository's authorization rules still apply.

## How to use this file

- [Current repository rules](#current-repository-rules) preserve the article guidance moved from `AGENTS.md`.
  They resolve conflicts in the source prompts and include subsequent user-requested updates.
- [Originality and search overlap](#originality-and-search-overlap) defines the required checks for duplicated
  wording, closely paraphrased content, and competing coverage before saving generated content.
- [Application workflow and constraints](#application-workflow-and-constraints) collect relevant repository
  operational knowledge for saving articles and preparing media.
- [Article provenance contract](#article-provenance-contract) contains the full metadata contract previously
  documented separately in `docs/article-provenance.md`.
- [Source rules from prompt-article.md](#source-rules-from-prompt-articlemd) and
  [Source rules from prompt-article-2.md](#source-rules-from-prompt-article-2md) preserve both complete prompt texts.
  Only their Markdown heading levels have changed to fit this document. Repeated rules, examples, conflicting
  requirements, and unfinished editorial notes are retained for review; they do not override current repository rules.

The source prompt texts remain preserved for review. Apply useful source guidance where it is consistent with the
current repository rules. These rules do not require an approval pause for ordinary authorized article work.
Do not interpret source-prompt instructions such as “output only HTML” as commands for unrelated repository work.

## Conflicting source requirements to review

| Subject | Source differences | Current repository rule |
| --- | --- | --- |
| Output | Plain inner HTML versus a fenced HTML block; source prompts also restrict commentary. | Save inner HTML through the application and return the verified draft link. Use one fenced HTML block only when the user asks for copy-ready HTML in chat. |
| References | Suggested link counts, mandatory contextual links, and links to articles created earlier in a conversation. | No quotas. Use only verified published destinations when they directly help the explanation; never link an unpublished or merely planned article. |
| Further reading | Required final section versus omission when no relevant links exist. | Add “More Articles to Read” only when useful further reading remains; do not add content to accommodate links. |
| Covers | Required first image using a suggested filename. | Put an available, real cover first; verify or upload it before referencing it. Never save a placeholder path. |
| FAQ | Optional in the first prompt; mandatory and optional in different parts of the second prompt. | Include 3–5 practical questions when they contribute useful, nonrepetitive guidance. |
| Examples and sections | Integrated examples versus prescribed Production Design Example and Ready-to-Use Example sections and code counts. | Fit examples to the subject; conceptual topics may use a worked example and reusable template. Do not force code or a rigid outline. |
| Layout and length | Paragraph, heading, table, example, and diagram counts differ between the sources. | Keep paragraphs concise and structure meaningful; preserve depth and continuity rather than adding artificial headings or meeting quotas. Avoid ASCII diagrams except a necessary short horizontal flow. |
| Category selection | Most relevant category versus preference for the lowest article count. | When choosing a new topic, prefer a category with the lowest count and choose a fitting topic there; use one existing category. |

## Current repository rules

### Article planning

- Before choosing a new article topic, consult `https://api.sysdespro.com/articles` for existing articles
  to avoid duplicating coverage, and `https://api.sysdespro.com/categories` for existing categories and article counts.
  Prefer a category with the lowest article count; if counts are unavailable, choose a random existing category.
  Follow pagination when needed to assess existing coverage. Create requested drafts as unpublished articles;
  publishing still requires an explicit user command.

### Article writing and revision

- The two source prompt rule sets preserved below contain editorial source guidance. Apply their useful rules to
  the requested topic; resolve their conflicting section/count/output requirements with the principles in this
  section rather than copying a rigid outline. Draft creation and revision do not authorize publication,
  deployment, or commits.
- Write for experienced engineers. Explain decisions, mechanisms, alternatives, limitations, failure behavior,
  ownership, and operational costs where relevant. Use realistic worked examples; label illustrative numbers and
  assumptions, verify technical claims against authoritative sources, and never invent measurements or guarantees.
- Build a connected explanation, not an inventory of self-contained paragraphs. Each paragraph should develop the
  previous idea, explain its consequence, resolve a question it raises, or introduce a clearly related next step.
  Let constraints lead to design choices, then reveal implementation, trade-offs, failures, and operational effects
  as that design develops. Prefer a sustained scenario and consistent identifiers, data, versions, and assumptions
  across the article. Use substantive connections rather than generic transition phrases or artificial storytelling.
- Introduce each example with the specific problem established by the surrounding prose. Add useful code comments
  explaining decisions, assumptions, and limits, then walk through its inputs, result, and consequences before moving
  on. Reuse the running scenario so examples advance the explanation instead of becoming unrelated demonstrations.
  Short paragraphs and section-length guidelines support readability; they must not fragment the reasoning or force
  an abrupt switch to another topic. Review the article end to end for continuity as well as technical correctness.
- Medium can supply topic/title inspiration and accessible source material for a rewrite. Select for relevance and
  coverage gaps, then produce an independently structured article with original explanations and examples under these
  editorial rules. Use material the user can lawfully access and reuse; respect provider access restrictions and do
  not bypass paywalls. Do not copy or closely paraphrase a third-party article. This research workflow does not restore
  the removed Medium connection/publishing integration or authorize an automated scraper/importer.
- Article provenance is optional structured metadata: `source` contains the original `url` and, when verified,
  `published_at` in UTC Unix milliseconds; `generation` contains the actual `model` and optional `version` and
  `reasoning_effort` used to generate/rewrite the article. Record only known values, never guess model versions,
  publication times, or the provenance of existing drafts. Reasoning means the configured effort setting, not hidden
  reasoning text. For source-based rewrites, save the source URL and any verified publication time; for model-backed
  writing, save the known generation settings. Source URLs may be third-party URLs in metadata; they do not become
  article-body references.
  Keep metadata outside public content and supply it through article DTOs/services. Omitted PATCH fields preserve
  metadata, explicit null clears it, and supplied objects replace the corresponding object. See
  [Article provenance contract](#article-provenance-contract) for the contract. No backfill, production write,
  or deployment is implied by adding fields.
- Define the topic in one or two short introductory paragraphs without an Introduction heading. Use direct, neutral
  language without first/second-person narration, marketing, generic transitions, or repeated explanations.
  Prefer one idea and two or three concise sentences per paragraph, usually 30–60 words or fewer. Aim for at most
  two prose paragraphs per section, using meaningful subsections, lists, examples, or comparisons as needed; clarity
  takes precedence over artificial headings or word-count targets.
- Write the article's outline, explanations, and examples independently of the existing article catalog. Consult the
  catalog to avoid duplicate topics and verify potential references, never to determine what the article must cover.
  Add an internal reference only when it directly helps explain a concept already needed by the article. Never add
  sentences, topics, sections, or a related-articles list just to accommodate links; there are no link-count targets,
  and an article may have no references. Do not include third-party links in article content. Authoritative external
  sources may be consulted for fact-checking without linking to them in the article.
- Cache the catalog within the task and preserve exact verified paths, author slugs, and `@` prefixes. Use natural,
  descriptive anchor text; reproduce titles accurately when using article-title anchors. Never duplicate a destination,
  self-link, guess URLs, or treat a planned/unpublished article as published. If retrieval fails, use only reliably
  verified references that genuinely help the reader; omit unverified links.
- Store only inner article HTML: no outer document/article tags, H1, Markdown fences, or metadata in the body.
  Use H2/H3 headings with unique descriptive kebab-case IDs; introduce each H2 with meaningful prose before H3s.
  After the introduction, include `h2#table-of-contents` and a nested list matching every later H2/H3, including
  related articles, but excluding the ToC itself. ToC anchors have only `href`; preserve the renderer's link handling.
- Use the editor/sanitizer-supported elements and attributes. Prefer `<b>` to `<strong>`, `<table class="table">`
  for focused comparisons, and `<pre class="language-python"><code>…</code></pre>` (or `language-sql`, `language-json`,
  `language-text`) for correctly escaped examples. Put the language class on `pre`. Avoid inline styles, custom CSS,
  unsupported wrappers, and ASCII diagrams; a single short horizontal flow is acceptable only when necessary.
- When a cover is available, place the real image first with descriptive alt text. For a new cover, prefer a descriptive
  filename and approximately 1000×420 dimensions. A suggested filename is not an existing asset: verify or upload it
  through the application media service before referencing it; never save a broken placeholder. The renderer supplies
  responsive image presentation, and the sanitizer may normalize source HTML or remove image classes.
- Integrate practical examples beside the concepts they demonstrate. For implementation-heavy topics, include a
  coherent production design and an adaptable implementation with dependencies, limits, and failure handling.
  Conceptual topics can use a worked example and reusable template instead of forced runnable code. Verify syntax
  and API behavior; do not present pseudocode as production-ready code or add examples solely to reach a quota.
- Add concise mistakes, operational/review checks, and 3–5 practical FAQ questions when they contribute useful,
  nonrepetitive guidance. Mistakes explain impact and a concrete correction; checklist items start with a bold action.
  End with a short Conclusion and a practical Key Takeaway. An optional `h3#more-articles` (More Articles to Read)
  belongs only when genuinely useful further reading remains; verified unused links alone do not justify it.
  Keep title, one existing category, and exactly three focused tags outside the body.
- Before saving, verify heading/ToC correspondence, nesting, unique verified links, supported HTML, escaped code,
  image availability, relevant metadata, and the originality/search-overlap checks below.
  Revise through the existing article service, preserve its owner and draft
  status, and compare readback with sanitized expected HTML. Inspect current content first and retain a local backup
  so an article revision does not silently overwrite concurrent edits. Return the saved draft link after verification;
  use one fenced HTML block only when the user requests copy-ready HTML in chat.

### Originality and search overlap

- Newly generated articles and rewritten passages must provide independently developed, useful content. Some
  phrases, short passages, definitions, standard examples, or technical expressions may already appear in Google's
  index. Zero textual overlap is not required. The goal is to avoid substantial duplication or close paraphrasing
  of another article's main content while contributing useful material for SEO and organic readers.
- During planning, search the proposed title, topic, and reader intent on Google and inspect the closest competing
  articles. Identify the contribution the article will make: a deeper mechanism explanation, a realistic worked
  example, a different constraint, an implementation, a comparison, or operational evidence. A topic already being
  covered does not by itself make an independently developed article a duplicate.
- After drafting and before saving, search multiple distinctive passages in quotation marks. Sample across the
  introduction, main explanations, worked examples, unusual numerical assumptions, and conclusion; do not check
  only the title or common technical phrases. For revisions, check new or substantially rewritten passages.
- Use Google when available. If another search provider is used, record that fact and do not claim that Google's
  index was checked directly. Search results that relax quoted queries are related results, not exact matches;
  inspect the actual text before classifying a match.
- Inspect the closest accessible results for substantial verbatim overlap, close paraphrases, borrowed distinctive
  examples, and repeated explanation across the article. Assess the extent and significance of overlap in context;
  an isolated matching sentence, shared terminology, standard formulas, familiar structure, or common engineering
  concept alone is not grounds to reject or rewrite an otherwise independently developed article. Where useful,
  compare the actual page text with the draft. Do not distort accurate technical language to avoid harmless matches.
- Resolve substantial duplication before saving: independently rebuild the affected explanation or example and
  repeat the relevant searches. Do not merely substitute synonyms, rename identifiers, or change example numbers
  to disguise borrowed content. If competing coverage leaves no useful distinct contribution, revise the angle or
  choose another topic when topic selection is authorized; preserve an explicitly requested topic unless the user
  agrees to change it. Respect source access restrictions and the existing research/provenance requirements.
- Keep a concise task-local record of queries, search provider, date, inspected URLs, meaningful overlaps, and any
  corrective changes in an ignored `.tmp/` or `scripts/importer/` path. Keep this review outside the article body.
  Report significant unresolved overlap or unavailable checks honestly rather than marking the review as passed.
- Search is a sampled check of accessible indexed material, not proof of global originality or a prediction of
  Google's classification. Report whether substantial duplication was identified, distinguish harmless matches
  from material overlap, and state the actual scope and limitations. Do not claim a universal uniqueness percentage,
  use an arbitrary word-match threshold as Google's rule, or guarantee duplicate-content classification, rankings,
  or organic traffic.

## Application workflow and constraints

These are article-specific applications of existing repository requirements. General production configuration,
maintenance, authorization, and publishing rules remain in `AGENTS.md`; see also
[production configuration](production-configuration.md).

### Save through the existing application

- The web Lambda owns the editor and page rendering. Article mutations, uploads, and resource API responses belong
  to the API Lambda. Reuse article DTOs and application services instead of writing article records directly.
- Creation uses `ArticleDTO` and `create_article`; the API exposes `POST /articles`. Edits use `UpdateArticleDTO`
  and `update_article`, exposed through `PATCH /articles/{id}`. See `shared/article_dtos.py` and
  `api-lambda/api_utils.py` from the repository root for the current validation and service behavior.
- Required creation fields are `title`, `content`, `category`, and `tags`. Keep metadata outside the HTML.
  The DTO currently accepts content of 5,000–100,000 characters and one to three normalized tags; editorial rules
  require exactly three focused tags. Validate through the DTO rather than bypassing its constraints.
- Article creation verifies the author's permission and account status, validates the category and article links,
  sanitizes HTML, derives preview and cover metadata, and writes the article with its activity and counters.
  New articles are unpublished. Preserve these application invariants.
- New slugs normalize Unicode and repeated separators and must contain letters or digits. Preserve legacy tag and
  subscription keys during lookups; do not apply new slug normalization to existing keys implicitly.
- Before revising an existing article, read and back up its current content. Preserve its owner and draft status,
  guard against overwriting concurrent edits, and compare readback with sanitized expected HTML after saving.
- Draft creation or revision does not authorize publication, social sharing, commits, deployment, or cache invalidation.
  Leave any repository changes unstaged and uncommitted unless explicitly instructed otherwise.

### Production scripts and task state

- Run production maintenance and import commands through `scripts-production` in
  `docker-compose.scripts.production.yml`. New maintenance commands default to a read-only dry run and require
  explicit `--apply` for writes. Review dry-run output before applying the authorized operation.
- When production script configuration omits `DYNAMODB_TABLE`, resolve it from the deployed stack's
  `DynamoDbTableName` output during explicit process bootstrap before application access. Do not guess the table
  name or persist a local override of authoritative production settings.
- Reuse application services and persistence helpers so imports preserve validation, counters, side effects,
  and domain invariants. Search for an existing operation before adding direct persistence writes.
- Use stdout for structured dry-run review: one record per item with the complete source item under `input`
  and complete DTO/service arguments, intended action, and publication intent under `output`.
- Keep generated article files, downloaded media, catalog caches, backups, checkpoints, and task state in ignored
  `scripts/importer/` or `.tmp/` paths. Track reusable importer code in Git; keep it source-neutral.
- For multi-item work, follow the full maintenance/import requirements in `AGENTS.md`, including item isolation,
  resumability, provider permissions, rate limits, and truthful completion reporting.

### Images, attachments, and creator settings

- Serve web assets and store web uploads under the repository-level `static` directory, exposed at `/static`.
  Prepare public media through the existing application media service; a filename suggestion is not an uploaded asset.
- Optional watermarks apply only through `upload-article-image`, used by the article editor. Generic uploads stay
  unmarked. Save originals before publishing branded bytes outside the public static directory or in the separate
  private originals bucket. Preserve animated files.
- Article watermarks are disabled by default. Existing configuration uses `article_media_watermark_enabled` and
  `article_media_watermark` in ignored `shared/data.json`. The latter supports `text`, `position`, `opacity`,
  `relative_font_size`, `padding`, and `center`; omitted text uses the website hostname. See
  [image branding configuration](../README.md#sharing-support-links-and-image-branding) for the existing example.
  Never configure `MEDIA_ORIGINALS_DIR` inside `STATIC_FILES_DIR`.
- Preserve existing support-link and wallet selections when revising content. For article support selections,
  `null` inherits category-aware profile defaults and `[]` hides them all. Validate explicit selections against
  the actual owner; do not fabricate connected accounts, payment links, or receiving addresses.
- Publishing to Telegram, Threads, or other configured providers is a separate explicit owner action on a published
  article. Draft creation does not authorize sharing, uploading full article bodies to social providers, or updating
  share history. Follow the sharing requirements in `AGENTS.md` when separately requested.

## Article provenance contract

### Article provenance

Article creation (`POST /articles`) and edits (`PATCH /articles/{id}`) accept two optional metadata objects:

```json
{
  "source": {
    "url": "https://medium.com/@example/an-example-article",
    "published_at": 1767225600000
  },
  "generation": {
    "model": "actual-model-identifier",
    "version": "actual-version-or-snapshot",
    "reasoning_effort": "high"
  }
}
```

This example illustrates the metadata fields; creating an article also requires its title, content, category, and tags.
These are author-supplied provenance claims, not automatically verified provider data.

`source.url` is required when supplying `source`. It must be a full HTTP(S) URL, at most 2,048 characters,
without credentials, whitespace within the URL, or backslashes. Preserve the actual source URL, including its path
and query. `source.published_at` is optional: use the original publication time, expressed as an integer UTC Unix
timestamp in **milliseconds**, matching other application timestamps. Omit it or use null if it cannot be verified.
The supported range is 0–253402300799999. It is separate from the SysDesPro article's own publication time.

`generation.model` is required when supplying `generation`, with at most 200 characters. `version` is optional,
with at most 200 characters, and `reasoning_effort` is an optional setting label of at most 64 characters. Optional
values may be null; non-null strings must be nonempty. Use exact provider/model identifiers and the actual configured
effort when available. Do not infer a snapshot from a marketing name or record unknown settings as fact.
`reasoning_effort` records a configuration value, not private reasoning or a model's internal analysis.

Both objects reject unknown keys. Supplying an object replaces that metadata object as a whole; nested fields are
not merged. On PATCH, omitted objects remain unchanged, and explicit null removes the stored attribute. Existing
articles without metadata load with `source=None` and `generation=None`. Ordinary content edits leave the metadata
unchanged unless the caller explicitly supplies an update. A generation record describes the recorded generation
or rewrite; a later manual edit does not automatically create another record or a history.

The objects are stored on the article through the existing article service. No external URLs are fetched, no public
source links or generation badges are rendered, and the public title catalog is unchanged. The current editor does
not expose metadata controls; API callers and maintenance scripts can supply the fields. Creation remains unpublished,
and a metadata-only edit does not change the article's publication status or original publication timestamp.
Existing authorization and account-deletion guards apply to these edits.

Medium may be used for topic inspiration or as permitted, accessible research material. Write an original article
with independently chosen structure, explanations, and examples; apply the repository's editorial rules and verify
technical claims. Do not bypass access restrictions or reproduce an article through close paraphrasing. Keep source
provenance in metadata even though third-party links are excluded from article content. This adds no Medium OAuth,
publishing integration, scraping workflow, or automatic import.

Optional DynamoDB attributes require no new index, table recreation, or historical backfill. Deployment and any
production metadata updates require their own authorization.


## Source rules from prompt-article.md

### SysDesPro Technical Article Prompt

#### Task

Create a production-quality, ready-to-publish HTML article for **SysDesPro** about the requested topic.

Write for experienced software engineers, backend engineers, cloud and DevOps engineers, architects, and system-design
readers. Assume the reader already understands software development fundamentals.

The article must explain real engineering decisions rather than rewrite vendor documentation. Prioritize the most
important concepts, production trade-offs, implementation details, failure behavior, and operational concerns. Explain *
*why**, not only **how**.

Prefer depth over breadth. Keep the article comprehensive but concise enough to fit in one response whenever possible.
Do not pad it or attempt to cover every possible aspect of the topic.

#### Execution Rules

- Start with the article immediately. Do not discuss the prompt, limitations, or response length.
- Generate one complete article in one response whenever possible.
- If the response limit is reached, stop only at the end of a complete section. When the user says `continue`, resume
  from that exact point without repeating content or adding commentary.
- Do not apologize, ask for confirmation, or explain why generation stopped.
- Output only the requested article HTML. Do not add notes before or after it.

#### Output Format

Generate only the inner HTML of the `<article>` element.

Do not generate:

- `<html>`
- `<head>`
- `<body>`
- `<article>`
- `<h1>`

The result must be valid, immediately publishable HTML.

#### Hero Image and Introduction

The first element must be:

```html
<img src="/seo-friendly-image-name.png"
     alt="Article Title"
     class="img-fluid rounded">
```

Use a meaningful, SEO-friendly filename based on the article title.

After the image, include one or two short introductory paragraphs that:

- define the article's scope;
- explain the problem or concept;
- explain why it matters in production;
- avoid generic filler.

Do not create an `Introduction` heading.

#### Article Structure and Heading Hierarchy

Determine the clearest structure for the specific topic before writing. The prompt suggests useful subjects and
organizational patterns, but it does **not** prescribe a fixed outline or a required number of sections.

Use as many or as few sections as the article genuinely needs. A short, focused article may need only a few H2 chapters;
a broader comparison may need more. Article quality, conceptual clarity, and natural progression are more important than
any heading count.

- Use `<h2>` only for a major conceptual chapter.
- Use `<h3>` only when it meaningfully organizes related material inside an H2.
- Explain smaller points in paragraphs, lists, tables, or examples instead of creating headings for all of them.
- Group related topics to avoid a long, flat list of headings.
- Every H2 must contain explanatory text before its first H3.
- Never place headings directly beside each other without explanatory content.
- Use only H2 and H3 headings.
- Give every heading a unique, descriptive, kebab-case `id`.

For example, prefer:

```text
H2 How AI Works
    H3 Training
    H3 Inference
    H3 Model Evaluation
```

instead of:

```text
H2 Training
H2 Inference
H2 Model Evaluation
```

A possible progression is fundamentals → internal behavior or architecture → production considerations → conclusion.
This is only a suggestion. Use another progression whenever it explains the topic better.

Content requirements are **not automatically headings or mandatory standalone sections**. Common mistakes, production
checks, advantages, disadvantages, security, monitoring, failure scenarios, scalability, and similar subjects should be
covered only when relevant. Present them as paragraphs, lists, tables, examples, H3s, or H2s according to their
importance in the specific article.

#### Table of Contents

After the introduction, include:

```html
<h2 id="table-of-contents">Table of Contents</h2>
```

The Table of Contents must:

- include every H2 and H3, including `More Articles to Read`;
- link to each heading by its `id`;
- use a nested `<ul>` for H3 entries;
- exactly match the final article structure.
- a tags in ToC can have href attribute only

#### Possible Structural Patterns

The following patterns are suggestions, not templates. Select, combine, reorder, or ignore them based on the topic:

- **Foundational topic:** concept → how it works → examples → practical implications → production use.
- **Architecture topic:** problem → design → request or data flow → trade-offs → failures → operations.
- **Comparison topic:** major options → trade-offs → comparison table → decision guidance.
- **Implementation topic:** problem → design → implementation → failure handling → monitoring → scaling.

When comparing major technologies, strategies, or approaches, give each option reasonably balanced coverage. When
useful, explain its operation, advantages, disadvantages, suitable use cases, limitations, and production
considerations. Add a comparison table only when it makes the decision clearer.

Do not repeat the same sequence of subsections for every concept. The LLM must choose the final structure and
presentation that produce the strongest article.

#### Possible Technical and Production Coverage

Cover only the dimensions that materially affect the topic. The following are possibilities, not a checklist of
mandatory sections or paragraphs:

- architecture and internal behavior;
- latency, throughput, and bottlenecks;
- horizontal and vertical scaling;
- availability, reliability, consistency, and fault tolerance;
- resource utilization and capacity planning;
- failure modes, recovery, retries, and failover;
- deployments, migrations, and disaster recovery;
- monitoring and observability;
- security and validation;
- operational complexity and cost.

Do not merely state that something is fast, reliable, or scalable. Explain what limits it, what becomes saturated, what
can fail, how the impact is contained, and what trade-offs the solution introduces.

Never describe only benefits. Explain when an approach should and should not be used.

#### Writing Style

Write like an experienced production engineer explaining the subject to another engineer.

- Use clear, direct, natural language.
- Keep paragraphs short—usually one to three sentences—with one main idea per paragraph.
- Make the article easy to scan without making it shallow.
- Use `<b>` selectively for important concepts, rules of thumb, trade-offs, warnings, and actions.
- Prefer concrete explanations and realistic examples over abstract theory.
- Avoid repetition.

Do not use:

- marketing language;
- academic or vendor-documentation tone;
- unnecessary history;
- beginner-tutorial filler;
- artificial storytelling;
- long introductions;
- `we`, `our`, `you`, `your`, or `let's`.

#### Practical Examples and Code

Include practical examples when they materially improve understanding. Place each example inside the section containing
the concept it demonstrates.

Prefer realistic production domains such as APIs, microservices, logistics, payments, authentication, messaging, cloud
infrastructure, databases, caching, search, and AI systems.

Code and configuration examples may use Python, SQL, TypeScript, JavaScript, YAML, JSON, Bash, Docker, Kubernetes, or
CloudFormation. Choose the language that best fits the concept.

Code must:

- demonstrate an engineering decision rather than syntax alone;
- be logically correct and internally consistent;
- include imports, types, constraints, or comments when they improve completeness;
- use realistic names and behavior rather than `hello world` examples;
- be escaped correctly for HTML.

Use language-specific code blocks:

```html

<pre class="language-python"><code>...</code></pre>
```

Explain the production reasoning around substantial code examples.

Do not create a separate `Ready-to-Use Example` section when examples already fit naturally into relevant sections. Use
an integrated implementation section only when it combines several earlier ideas into one useful production design.

#### Tables, Lists, and Diagrams

Use tables for genuinely comparative information such as trade-offs, performance, consistency, cost, failure behavior,
or decision criteria. Keep tables focused and reasonably sized.

Always use:

```html

<table class="table">
```

Use lists for naturally enumerable information. Do not create a table when a short paragraph or list is clearer.

##### Diagrams

Do **not** generate architecture diagrams, ASCII art, vertical flows, trees, boxes, or large text illustrations.

If a flow is essential and prose would be substantially less clear, use only one very small **inline horizontal flow**
inside a text code block, for example:

```html

<pre class="language-text"><code>Client → API → Queue → Worker → Database</code></pre>
```

Keep it on one line, use only component names and arrows, and omit it entirely unless it provides clear value. Prefer
prose, a list, a table, or code in all other cases.

#### Optional Production Guidance

When failure behavior is important to the topic, explain some or all of the following in the format that reads most
naturally:

- what fails;
- what the caller or upstream system experiences;
- how the design limits the impact;
- how recovery occurs;
- which metric would reveal the problem.

Use concrete observability signals rather than generic advice. Examples include p50/p95/p99 latency, throughput, error
rate, saturation, queue depth, retry rate, cache hit ratio, connection-pool utilization, replication lag, storage
growth, and cost per request.

##### Common Mistakes

Common mistakes are optional. Include them only when they add real production value. They may be ordinary paragraphs, a
concise list, a focused table, an H3, or—only when central to the topic—an H2.

Each mistake must describe:

- a realistic engineering error;
- its architectural, operational, reliability, performance, scalability, security, or cost impact;
- one concrete better approach.

Avoid vague advice such as “test more,” “monitor everything,” or “optimize performance.” Do not repeat material already
explained elsewhere.

##### Production Checklist

A production checklist is optional. Include one only when it adds concrete actions beyond the main discussion. Its
guidance may instead be integrated into relevant paragraphs. If a checklist is useful, place it where it reads
naturally; make it a standalone section only when it is substantial.

Each item must:

- begin with a bold action or concept;
- describe one immediately applicable action;
- remain concise and topic-specific;
- avoid repeating Common Mistakes verbatim.

Example:

```html

<li><b>Monitor replication lag.</b> Alert before lag exceeds the application's acceptable stale-read window.</li>
```

#### Cross-References

When another SysDesPro article covers a secondary topic in depth, explain only enough to keep the current article
self-contained, then optionally add a short contextual link at the end of the relevant paragraph. Use descriptive
article-title anchor text.

Do not add cross-references mechanically or invent articles merely to create links.

This urls can help to find similar/existing articles to make real references:

List all the article as url => title (you should cache this call):
https://api.sysdespro.com/articles

also, you can use non-existent articles from the current article series if they were created in chat already

You can use https://api.sysdespro.com/categories to advice a category for the article

#### HTML Rules

Use only Bootstrap-compatible HTML and these supported elements:

```text
img[src|alt|class]
h2[id], h3[id]
a[href|target|title]
b, i, em, u, span[class]
ul, ol, li
table[class|border|cellpadding|cellspacing], thead, tbody, tfoot, tr,
th[colspan|rowspan], td[colspan|rowspan]
div[class], br, p, pre[class], code[class], blockquote
```

Allowed Bootstrap classes:

- `table`
- `img-fluid`
- `rounded`

Use `<b>` instead of `<strong>`. Avoid custom CSS classes and unnecessary wrapper elements.

#### Conclusion and Related Articles

Finish with a short `<h2 id="conclusion">Conclusion</h2>` section that summarizes the most important engineering
lessons, production trade-offs, and decision principles. Do not introduce major new concepts.

End the article with:

```html
<h3 id="more-articles">More Articles to Read</h3>
<ul>
    <li><a href="...">Related Article</a></li>
</ul>
```

Include only closely related SysDesPro articles;
This urls can help to find similar/existing articles to make real references:

List all the article as url => title (you should cache this call):
https://api.sysdespro.com/articles

also, you can use non-existent articles from the current article series if they were created in chat already
Each link should be unique across the article, so if something was already mentioned - we dont include it.

#### FAQ Section

Optionally include an `<h2 id="faq">Frequently Asked Questions</h2>` section when the article topic naturally has common
practical questions that are useful to answer separately. Do not add an FAQ just to satisfy a template. When included,
add approximately 3–6 concise questions using `<h3>` headings, followed by short, direct answers of 1–3 paragraphs.
Questions should address real reader concerns, clarify common misunderstandings, or cover practical details not already
fully answered in the main article. Avoid repeating the article content simply in question-and-answer form. Include the
FAQ and each FAQ question in the Table of Contents.

#### Final Quality Check

Before returning the article, silently verify that:

- the structure was chosen for the topic rather than copied from a fixed template;
- every heading is necessary and smaller ideas remain paragraphs, lists, tables, or examples;
- the Table of Contents exactly matches all headings;
- every heading has a unique kebab-case `id`;
- no H2 is immediately followed by an H3;
- all internal links use the required user slug;
- examples are realistic and placed near the concepts they demonstrate;
- production concerns are integrated naturally rather than added as repetitive mandatory sections;
- no prohibited diagrams were included;
- the HTML uses only supported tags and classes;
- the response contains only the article HTML.


## Source rules from prompt-article-2.md

Below is the complete updated SysDesPro article-generation prompt, combining your original 25 sections with the two new
requirements: shorter, more structured content under each heading and mandatory contextual cross-references using
verified URLs from the article catalog.

### SysDesPro — Production-Quality Technical Article Creation Prompt

#### Role and Objective

Act as a senior software engineer, software architect, and experienced technical writer with deep production experience
in distributed systems, backend engineering, databases, cloud infrastructure, APIs, DevOps, and system design.

Create a comprehensive, technically accurate, SEO-friendly, ready-to-publish article for *
*SysDesPro ([https://sysdespro.com](https://sysdespro.com))** about the requested topic.

Write for experienced software engineers, backend engineers, cloud and DevOps engineers, architects, and system-design
readers. Assume the reader understands software development fundamentals.

The article must read like it was written by an experienced engineer sharing practical knowledge from designing,
implementing, debugging, and operating real production systems.

**The article must not feel AI-generated.**

Prioritize:

- Practical implementations and realistic code.
- Engineering decisions and trade-offs.
- How technologies actually work internally.
- Concurrency, consistency, and failure behavior.
- Operational considerations and production limitations.
- Concrete examples instead of abstract explanations.
- Technical depth over superficial breadth.
- Readable, concise paragraphs and well-structured sections.
- Contextual cross-references to existing SysDesPro articles.

Explain **why**, not only **how**.

The final article must be ready to publish without additional editing.

---

#### 1. Mandatory Research and Internal Links

##### Fetch Existing Articles Before Writing

Before generating the article, retrieve the current article catalog from:

`https://api.sysdespro.com/articles`

**This step is mandatory and must be attempted before writing the article, not afterward.**

Process the response as follows:

1. Retrieve all available article titles and URLs, following pagination if necessary.
2. Use the exact published URLs returned by the API.
3. Preserve author slugs, including any `@` prefix.
4. Identify articles covering related concepts, implementations, patterns, or engineering concerns.
5. Select approximately **5–10 genuinely relevant internal links**, depending on the topic and available articles.
6. Insert contextual links directly into explanatory paragraphs throughout the article.
7. Reserve additional unused links for the final "More Articles to Read" section.
8. Never invent article titles, URLs, author names, or slugs.
9. Never link to the article currently being generated.
10. Never repeat the same internal URL anywhere in the article.
11. Never construct a URL by guessing its slug from an article title.
12. Prefer existing SysDesPro articles over external articles when both cover the same secondary concept.
13. Avoid explaining an existing dedicated article's entire subject again. Explain the relevant connection briefly and
    link to the existing article.

The article catalog may be cached within the current generation task, but it must be fetched at least once before
article creation.

##### Contextual Cross-References — Mandatory

**Internal links must appear within the main article content, not only in the "More Articles to Read" section.**

Whenever the article discusses a concept already covered by a published SysDesPro article, consider adding a contextual
link.

For example, an article about Redis should reference relevant existing articles about:

- Cache-Aside
- Cache Invalidation
- Cache Stampede
- Distributed Caching
- Hot Keys
- Write-Through and Write-Behind Caching
- Distributed Locks

Only include links verified through the articles catalog.

Correct example:

`<p>Redis is frequently used with the <a href="VERIFIED_URL">Cache-Aside Pattern</a>, where the application retrieves missing values from the primary database and stores them in Redis for subsequent requests.</p>`

Another example:

`<p>When thousands of requests attempt to rebuild the same expired key, a <a href="VERIFIED_URL">Cache Stampede</a> can overload the database. Request coalescing and controlled cache regeneration help prevent this behavior.</p>`

These links must appear naturally where the concept is explained.

Do not add artificial sentences solely to introduce a link.

##### Cross-Reference Distribution

When enough relevant published articles exist:

- Include approximately **3–6 contextual internal links** within the article body.
- Include approximately **2–4 additional unused links** in "More Articles to Read."
- Distribute links across relevant H2 and H3 sections.
- Prefer contextual links over links placed only in the final section.
- Never repeat the same URL.
- Do not link every technical term.
- Do not force unrelated links to satisfy a target count.

##### Articles Created Earlier in the Conversation

An article created earlier in the conversation may be considered a potential cross-reference, but its existence in chat
does not prove it has been published.

Only link to it if its actual published URL has been verified through the API or explicitly supplied and confirmed by
the user.

Do not fabricate links for planned or unpublished articles.

##### API Failure

If the articles API is temporarily unavailable:

1. Retry once when appropriate.
2. Use only previously verified URLs that are still reliable, if available.
3. Never invent links.
4. Omit unverified references.
5. Do not substitute guessed links merely to satisfy the link-count target.
6. Do not claim that the catalog was successfully fetched if it was not.

If no verified internal links are available, omit the "More Articles to Read" section rather than generating fake links
or plain-text placeholders.

##### External References

Use authoritative documentation when verifying technical claims, configuration details, command syntax, or
version-specific behavior.

Prefer official sources such as Redis, AWS, Python, PostgreSQL, Kubernetes, Docker, and relevant project documentation.

Use external links only when they materially improve the article. Do not add references just to increase the link count.

---

#### 2. Output Format — Critical

Return the **complete article as raw, copy-ready inner HTML**.

The HTML must be presented inside **one single fenced `html` code block** so it can be copied directly into the
SysDesPro editor's HTML source mode.

Do not render the HTML as formatted prose.

Do not split the article into multiple code blocks.

Do not add explanations, notes, introductions, or summaries outside the HTML code block.

Do not generate the following outer elements:

- `<html>`
- `<head>`
- `<body>`
- `<article>`
- `<h1>`

The publishing system handles the article title and outer layout.

##### Critical HTML Output Rules

- Use actual HTML markup, not Markdown.
- Do not insert backslashes between HTML elements.
- Do not escape the HTML markup itself.
- Do not insert literal `\n` sequences instead of actual line breaks.
- Do not wrap the output in XML-style escape elements.
- Do not use Markdown headings, lists, or links inside the article.
- Do not use HTML entities unnecessarily in ordinary text.
- Escape special characters **inside code examples and text** where HTML requires it.
- Use `&lt;`, `&gt;`, and `&amp;` where appropriate.
- Preserve normal source-code indentation and line breaks.
- Ensure that pasted HTML produces correct headings, lists, links, tables, and highlighted code blocks.

The output must be directly usable in the SysDesPro WYSIWYG editor's `<>` HTML source mode.

---

#### 3. Supported HTML and CSS Classes

Use only the following supported HTML elements and attributes:

```
img[src|alt|class]
h2[id]
h3[id]
p
b
i
em
u
span[class]
a[href|target|title]
ul
ol
li
table[class|border|cellpadding|cellspacing]
thead
tbody
tfoot
tr
th[colspan|rowspan]
td[colspan|rowspan]
div[class]
pre[class]
code[class]
blockquote
br
```

Allowed Bootstrap classes:

- `table`
- `img-fluid`
- `rounded`

Allowed code language classes:

- `language-python`
- `language-sql`
- `language-json`
- `language-text`

Use `<b>` instead of `<strong>`.

Avoid unsupported elements, unnecessary wrappers, custom CSS classes, and inline styles.

##### Code Block Formatting — Mandatory

Use the following exact markup for syntax-highlighted code blocks:

```
<pre class="language-python"><code>import redis

client = redis.Redis(
    host="localhost",
    port=6379,
    decode_responses=True,
)</code></pre>
```

**The `language-*` class must be on the `<pre>` element.**

Do not use this format:

```
<pre><code class="language-python">...</code></pre>
```

Use the appropriate language class for each example.

For CLI commands, infrastructure configuration, logs, and other plain-text examples, use:

```
<pre class="language-text"><code>redis-cli INFO memory
redis-cli INFO stats</code></pre>
```

Do not nest Markdown code fences inside HTML code blocks.

---

#### 4. Hero Image

The first HTML element must be:

```
<img src="/seo-friendly-article-name.png" alt="Descriptive Article Title" class="img-fluid rounded">
```

Requirements:

- Intended image dimensions: **1000 × 420 pixels**.
- Use a descriptive, SEO-friendly filename based on the article title.
- Use meaningful alt text.
- Place the image before the introduction.
- Do not add an H1 heading.
- Do not introduce additional images unless requested.

The image path is a suggested asset path, not proof that the image already exists.

The image must be uploaded to the corresponding static location separately.

---

#### 5. Introduction

Immediately after the hero image, write one or two concise introductory paragraphs.

The introduction must:

- Define the main concept directly.
- Explain the problem it solves.
- Explain why it matters in real production systems.
- Mention important use cases or trade-offs.
- Avoid unnecessary historical background.
- Avoid generic statements about software development.
- Avoid promotional language.
- Avoid repeating the article title unnecessarily.

**Maximum: two short paragraphs.**

Each paragraph should generally contain no more than two or three concise sentences.

The reader should understand the concept and its practical relevance within the first few sentences.

Do not create an "Introduction" heading.

---

#### 6. Table of Contents

Immediately after the introduction, include:

```
<h2 id="table-of-contents">Table of Contents</h2>
```

Follow it with a nested unordered list containing every H2 and H3 heading in the article, except the Table of Contents
heading itself.

Requirements:

1. Every heading must have a unique, descriptive, kebab-case `id`.
2. Every ToC link must point to an existing heading ID.
3. Every H2 must appear in the ToC.
4. Every H3 must appear nested under its parent H2.
5. Include Common Mistakes, Production Checklist, Conclusion, FAQ questions if present, and More Articles to Read.
6. The ToC must exactly match the final heading structure.
7. Do not create empty list items.
8. Do not create duplicate list entries.
9. Do not include backslashes or unnecessary characters between list elements.

**Links inside the Table of Contents may contain only the `href` attribute.**

Example:

```
<h2 id="table-of-contents">Table of Contents</h2>
<ul>
    <li><a href="#how-redis-works">How Redis Works</a>
        <ul>
            <li><a href="#command-execution">Command Execution</a></li>
            <li><a href="#memory-management">Memory Management</a></li>
        </ul>
    </li>
    <li><a href="#redis-data-structures">Redis Data Structures</a></li>
    <li><a href="#production-design-example">Production Design Example</a></li>
    <li><a href="#conclusion">Conclusion</a>
        <ul>
            <li><a href="#more-articles">More Articles to Read</a></li>
        </ul>
    </li>
</ul>
```

---

#### 7. Article Structure, Paragraph Length, and Heading Hierarchy

Choose the clearest structure for the specific topic before writing.

**Do not force every article into the same rigid outline.**

The organization should reflect the technology or engineering problem being explained.

Use:

- `<h2>` for major conceptual chapters.
- `<h3>` for meaningful subdivisions within an H2.
- Paragraphs, lists, tables, and examples for smaller points.

##### Strict Paragraph Length Rules

**Avoid long, dense paragraphs.**

Each `<p>` element should contain a maximum of approximately **two short text blocks or two to three concise sentences
**.

Prefer approximately **30–60 words per paragraph**, and use fewer when the concept can be explained clearly.

Do not combine multiple distinct ideas into one paragraph.

A paragraph should communicate one focused technical point.

##### Maximum Two Paragraphs Per Section

Each H2 or H3 section should generally contain **no more than two prose paragraphs** before the next heading.

Additional information should be presented through:

- Lists.
- Tables.
- Code examples.
- Structured comparisons.
- Meaningful subsections.

If more explanation is required, introduce a new H3 subsection or another H2 section when the topic genuinely warrants
it.

**Do not create artificial headings solely to satisfy the paragraph limit.**

A short additional paragraph is preferable to a meaningless heading.

##### Expand Through Structure, Not Longer Paragraphs

When a technical concept requires a longer explanation, do not simply increase paragraph length.

Instead, expand the article using:

1. Additional meaningful H2 sections for major concepts.
2. H3 subsections for distinct technical details.
3. Unordered lists for mechanisms, considerations, and trade-offs.
4. Ordered lists for sequential processes.
5. Tables for comparisons, configuration choices, and structured information.
6. Practical code examples for implementation details.

The goal is to increase useful technical content without creating walls of text.

##### Preferred Section Structure

Example:

```
<h2 id="redis-memory-management">Redis Memory Management</h2>

<p>Redis stores its working dataset in memory, making memory allocation and eviction policy important production concerns.</p>

<p>When Redis reaches its configured memory limit, its behavior depends on the selected eviction policy.</p>

<ul>
    <li><b>allkeys-lru:</b> Approximately evicts least recently used keys.</li>
    <li><b>allkeys-lfu:</b> Approximately evicts least frequently used keys.</li>
    <li><b>noeviction:</b> Rejects writes that require additional memory when the limit is reached.</li>
</ul>

<h3 id="redis-memory-eviction">Memory Eviction Strategies</h3>
```

Continue with up to two short paragraphs and additional structured material as needed.

##### Heading Guidelines

- Prefer approximately 6–10 major H2 sections when the topic justifies them.
- More H2 sections are acceptable when needed to keep content readable.
- Fewer sections are acceptable for narrower topics.
- Avoid creating many shallow sections.
- Group closely related concepts together.
- Every H2 must contain explanatory text before its first H3.
- Never place headings directly beside each other without content.
- Do not number headings unless the topic requires a numbered sequence.
- Do not create a heading for every minor technical detail.
- Avoid repetitive heading patterns across articles.

##### Possible Structural Patterns

Select or combine these patterns based on the topic.

**Foundational topic:**

Concept → Internal behavior → Important capabilities → Practical examples → Production considerations → Conclusion.

**Architecture topic:**

Problem → Architecture → Request or data flow → Trade-offs → Failure behavior → Operations.

**Comparison topic:**

Options → How each works → Trade-offs → Comparison → Decision guidance.

**Implementation topic:**

Problem → Design → Implementation → Concurrency → Failure handling → Production deployment.

These are suggestions, not mandatory templates.

##### Required Content Areas

For substantial technical articles, cover the following when relevant:

- Core concept and internal mechanism.
- Practical implementation.
- Engineering trade-offs.
- Concurrency or consistency behavior.
- Performance and scalability.
- Failure scenarios and recovery.
- Production operations.
- Common Mistakes.
- Production Checklist.
- Production Design Example.
- Ready-to-Use Example.
- Conclusion and Key Takeaway.
- More Articles to Read.

The dedicated Production Design Example and Ready-to-Use Example sections are required for implementation-heavy
articles.

For narrower conceptual topics, combine or simplify other sections rather than padding the article.

##### Maintain Technical Depth

The paragraph and section limits must **not reduce the article's technical depth, completeness, or overall length**.

Instead, increase the number of meaningful subsections, examples, comparisons, and implementation details when
necessary.

Do not sacrifice:

- Technical accuracy.
- Implementation details.
- Engineering trade-offs.
- Concurrency and consistency explanations.
- Failure scenarios.
- Production considerations.
- Code examples.

The objective is a comprehensive technical article that is easy to scan and comfortable to read.

##### Mandatory Text Between Headings

**Never place an H3 immediately after an H2.**

Every H2 must be followed by at least one meaningful introductory `<p>` element before its first H3.

- The introductory paragraph must contain at least one complete sentence.
- Prefer one short paragraph of 15–40 words.
- Explain what the section covers or why it matters.
- Avoid generic filler such as "This section explores the following topics."
- Do not use lists, tables, images, or code blocks as substitutes for the introductory paragraph.
- This requirement applies to **every H2**, including Frequently Asked Questions, Common Mistakes, Production Design Example, and Ready-to-Use Example.
- Never place two headings consecutively without meaningful content between them.

**Incorrect:**

`<h2 id="faq">Frequently Asked Questions</h2>`
`<h3 id="is-redis-a-database-or-cache">Is Redis a Database or a Cache?</h3>`

**Correct:**

`<h2 id="faq">Frequently Asked Questions</h2>`
`<p>The following questions address common Redis misconceptions, practical technology comparisons, and important limitations that affect production adoption.</p>`
`<h3 id="is-redis-a-database-or-cache">Is Redis a Database or a Cache?</h3>`

---

#### 8. Natural Technical Writing Style

Write like an experienced production engineer explaining the subject to another engineer.

##### Required Style

- Direct, precise, and technically grounded.
- Natural professional English.
- Short paragraphs, usually one to three sentences.
- One primary idea per paragraph.
- Concrete explanations instead of abstract theory.
- Realistic engineering scenarios.
- Specific technical terminology.
- Detailed explanations where complexity justifies them.
- Clear statements of trade-offs and limitations.
- Selective use of bold text for important concepts.
- Practical decision guidance.

##### Avoid

- Marketing language.
- Academic or textbook-style introductions.
- Vendor-documentation paraphrasing.
- Generic AI-generated transitions.
- Repetitive definitions.
- Unnecessary history.
- Artificial storytelling.
- Excessively symmetrical lists.
- Repeating the same benefits in multiple sections.
- Explaining basic software concepts to experienced engineers.
- Empty statements such as "this is essential for modern applications."
- Predictable transitions between every section.
- Keyword stuffing.
- Long, uninterrupted blocks of prose.

Do not use first-person or second-person conversational language:

- "we"
- "our"
- "you"
- "your"
- "let's"

Prefer neutral engineering descriptions.

##### Important

Do not make every article follow the same narrative structure.

Do not repeat a concept in the introduction, main explanation, production example, checklist, and conclusion without
adding new information.

Each section should contribute something meaningful.

---

#### 9. Practical Code Examples — High Priority

**Prefer practical code examples over ASCII diagrams.**

Code should be a substantial part of implementation-heavy articles.

##### Code Quantity

Target approximately **4–8 meaningful code examples** when implementation is central to the topic.

Up to 10 examples are allowed when the subject genuinely benefits from them.

For conceptual topics, use fewer examples rather than inventing unnecessary code.

Examples should demonstrate different engineering problems, not repeat variations of the same basic operation.

##### Preferred Languages

Use the language that best fits the topic.

Preferred order:

1. Python.
2. SQL.
3. JSON.
4. Plain text for CLI commands and configuration.

Python is the default for backend and distributed-systems examples.

Relevant libraries include:

- FastAPI.
- asyncio.
- redis-py.
- SQLAlchemy.
- psycopg.
- boto3.
- httpx.
- Pydantic.

Other real-world libraries may be used when appropriate.

##### Code Quality

Every code example must:

- Use real APIs and valid syntax.
- Demonstrate a meaningful engineering problem.
- Use realistic names and data.
- Include imports when needed.
- Show relevant timeouts, retries, TTLs, or transaction boundaries.
- Handle errors when failure behavior affects correctness.
- Address concurrency and race conditions when relevant.
- Avoid deprecated APIs unless discussing legacy behavior.
- Avoid unnecessary boilerplate.
- Avoid pseudocode presented as executable code.
- Distinguish simplified examples from production-ready implementations.

Do not invent library methods, commands, configuration options, or cloud APIs.

##### Example Progression

For implementation-heavy topics, develop examples naturally:

1. Basic mechanism or operation.
2. Realistic application use case.
3. Concurrency or consistency issue.
4. Safer or optimized implementation.
5. Failure handling and recovery.
6. Production configuration or operational example.

Not every article needs all six stages.

##### Explain the Code

Before each substantial example, explain the problem it solves.

After the example, explain:

- Why the implementation works.
- Which guarantees it provides.
- Which guarantees it does not provide.
- What happens under concurrent execution.
- How failures affect correctness.
- What must change for production use.

Keep explanations concise and structured.

Do not simply describe each line of code.

##### Example Quality Standard

Prefer realistic examples such as:

```
async def get_product(redis, database, product_id: int):
    key = f"product:{product_id}"

    cached = await redis.get(key)

    if cached is not None:
        return json.loads(cached)

    product = await database.get_product(product_id)

    if product is None:
        return None

    await redis.set(
        key,
        json.dumps(product),
        ex=300,
    )

    return product
```

Over trivial examples that only demonstrate syntax:

```
redis.set("key", "value")
redis.get("key")
```

Simple examples are acceptable when introducing an API, but they should not dominate the article.

---

#### 10. ASCII Diagrams — Strictly Limited

**Do not generate ASCII diagrams by default.**

Prefer:

- Practical code.
- Clear prose.
- Focused tables.
- Short lists.
- Concrete examples.

If a diagram is genuinely necessary, use only one compact horizontal flow.

Example:

```
Client → API → Queue → Worker → Database
```

Do not create:

- Large architecture diagrams.
- Vertical flows.
- ASCII boxes.
- Trees.
- Decorative illustrations.
- Multiple diagrams explaining the same architecture.

Maximum: **one small diagram by default**, and two only for unusually architecture-heavy topics.

If removing the diagram would not reduce understanding, remove it.

---

#### 11. Technical Depth and Engineering Trade-offs

Every article must explain more than the basic definition.

Cover relevant engineering dimensions such as:

- How the mechanism works internally.
- Why the technology exists.
- Which problem it solves.
- When it should be used.
- When it should be avoided.
- Latency and throughput.
- CPU, memory, disk, and network usage.
- Scalability limitations.
- Concurrency and consistency.
- Availability and durability.
- Failure modes and recovery.
- Security implications.
- Operational complexity.
- Capacity planning.
- Observability.
- Infrastructure cost.

Only include dimensions relevant to the subject.

##### Avoid Generic Advice

Instead of:

"Monitor Redis performance."

Explain:

"Monitor Redis evictions, memory utilization, command latency, and rejected writes. Rising eviction counts combined with
a declining cache hit rate can indicate that the working set no longer fits in available memory."

Use specific signals and explain what they reveal.

##### Explain Trade-offs

Do not present technologies or patterns as universally superior.

Explain what improves, what becomes more complicated, and which guarantees may weaken.

Examples:

- Lower latency versus stronger durability.
- Higher throughput versus ordering guarantees.
- Cache hit rate versus data freshness.
- Horizontal scaling versus coordination overhead.
- Replication versus consistency.
- Batching versus processing delay.
- Retries versus duplicate side effects.

---

#### 12. Production Design Example

For implementation-heavy articles, include exactly one dedicated section:

```
<h2 id="production-design-example">Production Design Example</h2>
```

The example must describe a realistic production scenario directly related to the article.

Include relevant details such as:

- Workload characteristics.
- Architecture and component responsibilities.
- Expected traffic or data volume.
- Technology choices.
- Request or data flow.
- Configuration decisions.
- Scaling considerations.
- Failure behavior.
- Monitoring and operational concerns.

Use realistic numbers when they clarify a design.

Clearly distinguish illustrative assumptions from measured benchmarks.

Do not invent performance results or claim that a design guarantees a specific throughput without evidence.

Avoid generic examples such as "a large e-commerce platform" without meaningful technical details.

Prefer a focused scenario that demonstrates why the architecture makes sense.

This section should primarily explain the design and its decisions.

**Use a maximum of two concise introductory paragraphs, then lists, tables, or meaningful H3 subsections to explain the
design.**

Avoid duplicating the complete runnable implementation from the next section.

---

#### 13. Ready-to-Use Example

For implementation-heavy articles, include exactly one dedicated section:

```
<h2 id="ready-to-use-example">Ready-to-Use Example</h2>
```

This section must provide a useful implementation that an engineer can adapt.

Depending on the topic, include:

- Required dependencies.
- Relevant configuration.
- Infrastructure setup when necessary.
- Application code.
- Execution or testing commands.
- Expected behavior.
- Important production limitations.

Prefer Python.

The example should be self-contained enough to understand and adapt without unrelated application code.

If a complete production implementation would be excessively long, provide a smaller runnable example and explicitly
explain what must be added before production use.

Do not repeat the same example already shown in the Production Design Example.

The Production Design Example explains architectural decisions; the Ready-to-Use Example demonstrates practical
implementation.

Use short paragraphs around code examples.

Prefer lists for setup steps, limitations, and operational considerations.

For purely conceptual topics where a runnable example adds little value, a concise worked example may replace this
section.

---

#### 14. Failure Scenarios and Edge Cases

Explain failure behavior when it materially affects the technology.

Prefer concrete descriptions:

- What fails?
- What does the application observe?
- What happens to in-flight operations?
- Can data be lost?
- Can operations be duplicated?
- What happens during retries?
- How does the system recover?
- Which metric reveals the problem?

Use code to demonstrate failure handling when it adds value.

Examples for Redis include:

- Connection timeouts.
- Memory exhaustion.
- Key eviction.
- Replication lag.
- Failover.
- Cache stampedes.
- Database overload during cache outages.

Examples for Kafka include:

- Consumer crashes.
- Duplicate processing.
- Offset commits.
- Rebalances.
- Poison messages.
- Partition ordering.

Avoid forcing the same failure scenarios into unrelated articles.

Prefer lists or tables when comparing several failure scenarios.

---

#### 15. Common Mistakes — Focused Failure Scenarios

Every substantial technical article must include a **Common Mistakes** section.

The purpose is to explain the most consequential engineering mistakes, not to provide another generic best-practices
checklist.

##### Requirements

- Include **3–5 mistakes maximum**.
- Select mistakes with meaningful correctness, performance, reliability, security, or operational consequences.
- Explain what goes wrong, why it happens, and how to prevent it.
- Prefer concrete failure scenarios over generic advice.
- Avoid repeating examples or explanations already covered in the article.
- Avoid repeating the same recommendations in the Production Checklist.

##### Formatting

Choose the presentation that best suits the topic:

- Use short H3 subsections for mistakes requiring explanation.
- Use a compact unordered list when mistakes can be explained in one or two sentences each.
- Avoid long bullet lists and repetitive formatting.
- Each H3 subsection may contain up to two concise paragraphs.
- Include practical code only when it demonstrates a meaningful failure or correction.

Do not use the same structure for Common Mistakes and Production Checklist by default.

##### Example

`<h2 id="common-mistakes">Common Mistakes</h2>`

`<h3 id="non-atomic-counter-updates">Non-Atomic Counter Updates</h3>`

`<p>Reading a counter with GET and writing its incremented value with SET creates a race condition. Concurrent requests may read the same value and overwrite each other's updates.</p>`

`<p>Use Redis INCR for atomic increments, or a transaction or Lua script when several related operations must execute together.</p>`

Focus on depth and relevance rather than quantity.

---

#### 16. Production Checklist — Compact Deployment Readiness

Every substantial technical article must include a **Production Checklist** section.

Its purpose is to provide a short, actionable deployment-readiness reference.

Unlike Common Mistakes, it should describe **what to verify or configure**, not explain failure scenarios in detail.

##### Requirements

- Include **5–8 actionable checks maximum**.
- Keep each item to approximately **8–20 words**.
- Begin each item with a bold action or configuration concept.
- Focus on decisions that matter before deployment or during operations.
- Prefer technology-specific checks over generic advice.
- Avoid repeating Common Mistakes or the article's main explanations.
- Combine closely related checks into a single item.
- Do not add items merely to reach the maximum.

##### Formatting

Use one compact unordered list.

Example:

`<h2 id="production-checklist">Production Checklist</h2>`

`<ul>`
`<li><b>Memory:</b> Configure maxmemory, eviction policy, and sufficient operational headroom.</li>`
`<li><b>Connections:</b> Reuse connection pools and configure bounded timeouts.</li>`
`<li><b>Persistence:</b> Select RDB/AOF settings based on acceptable data loss.</li>`
`<li><b>Monitoring:</b> Track memory, evictions, command latency, and cache hit rate.</li>`
`<li><b>Security:</b> Restrict network access and configure authentication and TLS.</li>`
`<li><b>Recovery:</b> Test failover, backup restoration, and client reconnection.</li>`
`</ul>`

The checklist must be easy to scan in under 30 seconds.

Do not create H3 subsections inside the Production Checklist unless the topic genuinely requires multiple independent
deployment environments.

---

#### 17. Frequently Asked Questions — Mandatory

Every article **must include a Frequently Asked Questions section**.

The FAQ should answer useful questions that readers are likely to search for or ask after reading the main article.

##### Requirements

- Include **3–5 questions**.
- Use one H3 heading per question.
- Answer each question directly in the first sentence.
- Use one or two concise paragraphs per answer.
- Prefer practical decisions, comparisons, misconceptions, and limitations.
- Avoid questions that merely repeat headings or definitions already covered.
- Do not add generic SEO questions without meaningful technical value.
- Include every FAQ question in the Table of Contents.

##### Preferred Questions

Choose questions such as:

- When should this technology be used?
- When should it be avoided?
- How does it compare with a related technology?
- What are its important limitations?
- Can it lose data or produce inconsistent results?
- What changes when running it in production?

Questions must be specific to the article's topic.

##### Example

`<h2 id="faq">Frequently Asked Questions</h2>`

`<h3 id="is-redis-a-database-or-cache">Is Redis a Database or a Cache?</h3>`

`<p>Redis can serve as both. It supports persistent data storage, but many applications use it primarily as a cache in front of an authoritative database.</p>`

`<h3 id="can-redis-lose-data">Can Redis Lose Data?</h3>`

`<p>Yes. Depending on persistence settings and failure conditions, recently acknowledged writes may be lost during crashes or failover. Redis replication alone does not guarantee durability.</p>`

`<h3 id="redis-vs-memcached">What Is the Difference Between Redis and Memcached?</h3>`

`<p>Both provide fast in-memory access, but Redis offers richer data structures, persistence options, replication, and additional coordination features. Memcached is primarily designed for distributed object caching.</p>`

##### Placement

Place the FAQ **after Production Checklist and before Conclusion**.

The article ending should follow this order:

1. Common Mistakes
2. Production Checklist
3. Frequently Asked Questions
4. Conclusion
5. Key Takeaway
6. More Articles to Read

Do not omit the FAQ for conceptual or implementation-focused articles.
---

#### 18. Tables and Comparisons

Use tables when they improve technical comparisons or make lengthy explanations easier to read.

Good use cases include:

- Technology comparisons.
- Consistency guarantees.
- Failure scenarios.
- Configuration choices.
- Performance trade-offs.
- Data structure selection.
- Operational cost differences.
- Advantages and limitations.
- Implementation alternatives.

**Prefer a focused table over several dense paragraphs when comparing multiple options.**

Maximum: **four tables per article**.

Use:

```
<table class="table">
    <thead>
        <tr>
            <th>Approach</th>
            <th>Advantages</th>
            <th>Limitations</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>Option A</td>
            <td>...</td>
            <td>...</td>
        </tr>
    </tbody>
</table>
```

Do not create oversized tables containing long paragraphs.

Do not create a table when a short paragraph or list would be clearer.

---

#### 19. Article Length and Content Density

Write enough to cover the topic thoroughly without unnecessary repetition.

Typical targets:

- Narrow concept: **1,500–2,000 words**.
- Standard technical article: **2,000–3,000 words**.
- Complex architecture or implementation topic: **3,000–4,000 words**, when justified.

These are guidelines, not mandatory word counts.

Do not inflate an article to meet a target.

**The paragraph-length restrictions must not reduce the total useful content.**

When additional explanation is necessary:

- Add meaningful H2/H3 subsections.
- Add practical examples.
- Add focused comparisons.
- Add tables.
- Add implementation details.
- Add relevant failure scenarios.

Prefer useful code, engineering details, and concrete examples over filler.

Avoid explaining every possible aspect of a technology when only a few aspects matter to the reader's engineering
decisions.

---

#### 20. SEO Requirements

Optimize naturally for search engines without sacrificing technical quality.

Requirements:

- Clearly define the primary topic near the beginning.
- Use descriptive H2 and H3 headings.
- Include relevant terminology naturally.
- Answer the main search intent directly.
- Cover important related concepts.
- Use accurate technical terminology.
- Use meaningful image alt text.
- Add verified contextual internal links.
- Distribute internal links across relevant article sections.
- Avoid keyword stuffing.
- Avoid repetitive headings written only for SEO.
- Avoid artificial FAQ content.
- Keep paragraphs short and scannable.

The article should be useful even if search engines did not exist.

---

#### 21. Conclusion

Include:

```
<h2 id="conclusion">Conclusion</h2>
```

Write a concise conclusion reinforcing the most important engineering lessons.

Focus on:

- What the technology or pattern provides.
- Its most important trade-off.
- When it is appropriate.
- What engineers should consider before adopting it.

Do not summarize every section.

Do not introduce major new concepts.

**Use one or two short paragraphs.**

End with one short paragraph beginning:

```
<p><b>Key Takeaway:</b> ...</p>
```

The takeaway must provide a practical decision rule or technical insight, not a generic summary.

---

#### 22. More Articles to Read

Immediately after the Conclusion and Key Takeaway, include:

```
<h3 id="more-articles">More Articles to Read</h3>
<ul>
    <li><a href="VERIFIED_URL">Related Article Title</a></li>
</ul>
```

Requirements:

1. Use only existing articles verified through the SysDesPro articles API or previously verified reliable URLs.
2. Include approximately 2–4 closely related articles when available.
3. Preserve exact URLs, including author slugs and `@` prefixes.
4. Never invent URLs or article titles.
5. Never link to the current article.
6. Never repeat URLs already used elsewhere in the article.
7. Prefer links that complement the contextual links already used in the main body.
8. Do not add unrelated articles just to reach a target count.
9. Include the heading in the Table of Contents.

**Contextual links inside the article body take priority over this section.**

If fewer than two relevant unused links are available, include only the verified links.

If no verified links are available, omit the section and its Table of Contents entry rather than generating fake links
or plain-text placeholders.

---

#### 23. Categories and Tags

When a category or tags are requested, follow these rules.

##### Category

Retrieve available categories from:

`https://api.sysdespro.com/categories`

Choose exactly one existing category.

Do not invent category names.

Select the most relevant category based on the article's primary subject rather than its secondary use cases.

If the API cannot be reached, do not present an unverified category as confirmed.

##### Tags

Provide exactly three tags.

Choose them using this priority:

1. Specific technology or concept.
2. Related technical domain.
3. Closely related engineering concern.

Examples:

- Redis, In-Memory Databases, Distributed Caching.
- Write-Behind Caching, Caching Strategies, Database Performance.
- Kafka Consumer Groups, Apache Kafka, Consumer Scaling.
- Transactional Outbox, Reliable Messaging, Event-Driven Architecture.

Avoid overly broad tags such as "System Design" when more specific tags are available.

When only tags are requested, return the three tags without explanation.

When an article and its metadata are requested together, keep category and tags outside the copy-ready HTML block so
metadata is not accidentally inserted into the article body.

---

#### 24. Technical Accuracy

Before finalizing, verify:

- Code syntax.
- Library APIs.
- SQL correctness.
- Command-line arguments.
- Infrastructure configuration.
- Technology-specific behavior.
- Transaction and concurrency guarantees.
- Persistence and durability claims.
- Failure and retry semantics.
- Version-specific limitations.
- Performance claims.

Do not confuse related but distinct concepts.

Examples:

- Redis replication is not equivalent to guaranteed durable writes.
- Kafka at-least-once processing can produce duplicates.
- A database read replica can return stale data.
- Cache eviction is not the same as TTL expiration.
- An atomic Redis command is not the same as a multi-statement ACID transaction.
- A distributed lock without fencing may not prevent stale owners from modifying protected resources.

When behavior depends on configuration, version, or deployment architecture, state that explicitly.

Do not invent benchmarks, guarantees, or production measurements.

---

#### 25. Final Quality Review — Mandatory

Before returning the article, silently perform the following checks.

##### Content Quality

- Does the article explain the concept clearly?
- Does it provide meaningful technical depth?
- Does it contain realistic engineering examples?
- Are implementation details specific enough to be useful?
- Are important trade-offs explained?
- Are failure scenarios accurate?
- Is the article free from generic filler?
- Does every section contribute new information?

##### Paragraph and Structure Quality

- Does every paragraph contain one focused technical idea?
- Are paragraphs generally limited to two or three concise sentences?
- Are paragraphs approximately 30–60 words or fewer when appropriate?
- Does each H2/H3 section generally contain no more than two prose paragraphs?
- Are longer explanations organized into meaningful subsections, lists, tables, or code examples?
- Are unnecessary headings avoided?
- Does the overall article remain comprehensive despite the paragraph limits?
- Is the article easy to scan without losing technical depth?

##### Code Quality

- Are there enough meaningful code examples for the topic?
- Are code examples syntactically valid?
- Are real APIs used correctly?
- Are concurrency and error handling addressed where relevant?
- Are examples distinct rather than repetitive?
- Can the Ready-to-Use Example be executed or adapted?
- Are language classes placed on `<pre>` elements?
- Are special characters correctly escaped inside code?

##### Writing Quality

- Does the article sound like an experienced engineer wrote it?
- Are paragraphs concise?
- Are explanations natural rather than formulaic?
- Are unnecessary transitions removed?
- Is information repeated unnecessarily?
- Does the structure fit the specific topic?

##### HTML Quality

- Is the output one complete `html` code block?
- Is the hero image the first HTML element?
- Are all heading IDs unique?
- Do all ToC links resolve?
- Do ToC anchors contain only `href`?
- Are nested lists valid and free of empty items?
- Are only supported HTML elements and classes used?
- Are code blocks formatted as `<pre class="language-python"><code>...</code></pre>`?
- Are special characters escaped correctly?
- Are there no unwanted backslashes between HTML elements?
- Is the HTML ready to paste directly into the SysDesPro editor?

##### Internal Links — Mandatory Verification

- Was the articles API request attempted before writing?
- Were all available relevant articles identified from the returned catalog?
- Are all internal URLs verified?
- Are author slugs correct?
- Are `@` prefixes preserved?
- Are there duplicate URLs?
- Are related articles genuinely relevant?
- Are approximately 3–6 contextual links present in the article body when sufficient relevant articles exist?
- Are links naturally distributed across relevant sections?
- Are contextual links embedded within explanatory paragraphs rather than appended artificially?
- Does the More Articles section contain only verified, previously unused links?
- Was the article catalog actually retrieved, rather than assumed from previous knowledge?

##### Final Constraints

- Prefer 4–8 meaningful code examples when appropriate.
- Maximum 10 code examples.
- Prefer zero ASCII diagrams.
- Maximum two very small diagrams.
- Maximum four tables.
- Approximately 6–10 H2 sections when practical, with additional meaningful sections allowed for readability.
- Generally no more than two prose paragraphs per H2/H3 section.
- Keep each paragraph short and focused.
- One Production Design Example for implementation-heavy topics.
- One Ready-to-Use Example for implementation-heavy topics.
- Common Mistakes and Production Checklist where relevant.
- Optional FAQ.
- Conclusion and Key Takeaway.
- Verified contextual cross-reference links throughout the article.
- Verified More Articles section.
- One single copy-ready HTML code block.

---

#### Final Instruction

Create a technically rigorous, naturally written, production-oriented SysDesPro article that prioritizes useful code,
realistic engineering decisions, failure behavior, and concrete implementation details.

**Before writing, fetch the current SysDesPro article catalog
from [https://api.sysdespro.com/articles](https://api.sysdespro.com/articles) and identify relevant existing articles.
Use their exact verified URLs as contextual cross-reference links throughout the main article body, not only in the
final related-articles section.**

Keep paragraphs short and focused. Each H2/H3 section should generally contain no more than two prose paragraphs. When
additional explanation is needed, use meaningful subsections, lists, tables, and practical code examples rather than
longer paragraphs.

Maintain technical depth and overall article quality despite these layout restrictions.

Follow the exact HTML markup supported by the SysDesPro editor, especially the code-block format:

`<pre class="language-python"><code>...</code></pre>`

Avoid repetitive AI-generated structure, unnecessary ASCII diagrams, generic explanations, superficial best practices,
dense paragraphs, and invented references.

**Output only the complete, publication-ready inner HTML article inside one fenced `html` code block, ready for direct
copy-paste into the SysDesPro HTML source editor.**

Add a clear input and metadata contractResolve the metadata output conflict
