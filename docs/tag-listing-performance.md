# Ordered tag listings and rollout

Published tag relations retain their existing `POST_TAG_COMBO#...` / `POST#...`
keys and gain compact category, creation-time, and rating metadata. The sparse
`TAG_COMBOS_BY_RATING` GSI orders each tag combination by rating and article ID,
including negative ratings and deterministic ties.

Normal popular-tag pages load one page plus one lookahead article. Category/tag
intersections can evaluate multiple bounded relation pages. Batch reads must
finish before a cursor advances; incomplete reads fail rather than silently
skipping results. Latest listings keep creation-time ordering. Category-only,
unpublished, and OR-filter queries retain their existing paths.

Rating, publication, and category/tag mutations maintain relation metadata
transactionally. Snapshot guards reject concurrent stale updates. Account
erasure maintains the same metadata when removing reactions on surviving
articles. Existing relation keys and legacy pagination cursors remain usable.

## Deployment and activation

The application uses the legacy reader until a readiness marker is written.
Adding infrastructure or deploying writers alone does not activate the index.
Each deployment and production write requires explicit authorization.

1. Deploy the GSI and wait for it to become `ACTIVE`. Deploy API/background
   writers and web code together; stop any older long-running writers.
2. Review a dry run through the production scripts service:

   ```bash
   docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
     python scripts/backfill_tag_listing_index.py --table-name YOUR_TABLE --limit 5
   ```

3. After review and authorization, pause article edits and imports. Backfill all
   published relations, verify the complete GSI, and activate:

   ```bash
   docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
     python scripts/backfill_tag_listing_index.py --table-name YOUR_TABLE --apply --activate
   ```

The script reloads each article and conditions writes on its metadata snapshot.
It does not modify article content, timestamps, publication state, or counters.
Reruns are safe. `--activate` requires `--apply` and rejects limited runs. If the
GSI differs from expected keys or ratings, activation fails; allow propagation
and rerun with edits still paused. Resume writes after verification.

Test initial and subsequent pages, latest/popular ordering, and category/tag
combinations after rollout. Local development must recreate its table with
`make recreate-local-dynamodb` after this schema change. No static asset or CDN
invalidation is needed for the index change.
