# Background work and account deletion

`background_handler.handler` runs from the API ZIP in a dedicated CloudFormation
`BackgroundFunction`. The API receives its name through `BACKGROUND_FUNCTION_NAME`;
the worker can invoke itself to continue bounded work. Local development uses
bounded thread pools. Existing sitemap generation retains its separate worker.

Deploying API code alone does not refresh worker code. Ship API, web, and
CloudFormation changes together after explicit deployment authorization.

## Profile propagation

Profile edits atomically save the profile, username reservations, and a
`USER#<id>` / `PROFILE_SYNC` checkpoint. Only name, username, and avatar edits
create a new checkpoint. The worker updates article and comment identity
snapshots in pages of 50, with each page and checkpoint advancement in one
transaction. A newer edit invalidates older work. Content timestamps are retained.

Failed delivery leaves the checkpoint available. Saving the profile again,
including an unchanged save, resumes propagation. Async Lambda invocations also
have bounded retries. Profile saves do not load every contribution or split an
arbitrary domain transaction to bypass DynamoDB's transaction limit.

## Creator support clicks

Same-origin POST navigation remains a non-cacheable 303 redirect. The web Lambda
sends a signed, short-lived notification with a unique identifier to the API.
The API validates it before dispatching `creator-link-click` and returns 202.
The worker reloads current access rules and links. A `CREATOR_CLICK#<event-id>`
completion marker commits atomically with counters, making replay harmless.
The signature's expiry is checked at API receipt; accepted work can run later.
Delivery failure does not interrupt navigation, and signed-in self-clicks do not
increment counters. These are accepted click counts, not unique visitor counts.

## Account deletion

An owner types `DELETE` in profile settings and submits `POST /users/{id}/deletion`.
This persists a durable outbox, invalidates the account's application sessions,
and returns a private confirmation URL. The worker erases articles, comments,
reactions, subscriptions, identities, aliases, activities, integration records,
and associated uploads while repairing surviving counters. Each source-record
deletion and its counter changes are atomic. Requests and worker invocations can
be resumed without repeating completed side effects.

The scheduled recovery rule runs every five minutes and redelivers pending
outbox entries. Failed dispatch does not undo a committed deletion request.
Mutations, OAuth completion, and profile propagation guard against pending
erasure so in-flight requests cannot recreate an erased account.

Files referenced by surviving contributions or site configuration are preserved.
Cleanup handles HTML article images, videos, attachments, avatars, private image
originals, and deterministic image variants. Only upload-shaped filenames can
be removed. CDN invalidations contain exact deleted file paths. No production
cleanup or invalidation occurs merely by deploying or reviewing this code.

Confirmation pages contain no account identity, disable analytics and caching,
and suppress referrers. Application access logs redact private receipt paths;
web API Gateway logs retain route keys rather than raw paths. Finished receipts
contain only completion state and timestamps.

## Provider callbacks

`POST /auth/threads/data-deletion` verifies Meta signed requests using the API's
existing Threads app secret. The optional Facebook endpoint uses
`FACEBOOK_CREDENTIALS_SECRET_ARN`, an existing Secrets Manager secret containing
`app_secret`; CloudFormation grants scoped read access. This setting does not
enable Facebook sign-in by itself. No upstream app credentials are committed.

Configure provider dashboards separately after deployment. Both callbacks
delete the linked application account, including contributions made through
other connected sign-in methods. Resolution uses saved provider identities,
never email. Replayed callbacks cannot resolve and delete a newly recreated
account. Google and Apple users can delete their local account through the same
profile flow; upstream grants must be disconnected through provider settings.
Already published Telegram, Threads, and LinkedIn posts are retained.
