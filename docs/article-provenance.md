# Article provenance

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
