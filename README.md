# Blog

## Prerequisites

- docker & docker compose have to be installed
- AWS account
- AWS CLI installed and configured
    - You should have these files: `~/.aws/credentials` and `~/.aws/config`

## Available commands

```
  aws-login            Obtain AWS auth token
  clean                Remove build artifacts
  create-local-dynamodb Create local DynamoDB table
  create-local-dynamodb-dummy-fixtures Populate local DynamoDB with dummy data
  delete-cert-infra    Delete cert CF stack
  delete-code-infra    Delete code CF stack
  delete-infra         Delete CF stack
  deploy               Deploy certificates, code bucket, Lambdas, application, and static files
  deploy-cert-infra    Deploy ACM certificate for the domain
  deploy-code-files    Zip and upload Lambda code to S3
  deploy-web-lambda    Build, upload, and deploy only the Web Lambda
  deploy-api-lambda    Build, upload, and deploy only the API Lambda
  deploy-img-lambda    Build, upload, and deploy only the Image Lambda
  deploy-code-infra    Deploy S3 bucket for Lambda / CloudFront code
  deploy-infra         Deploy CF stack for the site
  deploy-site-files    Sync local site files to S3
  down                 Stop local Docker containers
  drop-local-dynamodb  Drop DynamoDB table in local DynamoDB
  fetch-local-dynamodb Fetch 100 records from local DynamoDB
  generate-code-files  Build all Lambda zips
  generate-web-lambda-code-files  Build the Web Lambda zip
  generate-api-lambda-code-files  Build the API Lambda zip
  generate-img-lambda-code-files  Build the Image Lambda zip
  # Individual deploy-* targets also upload the selected artifact and update only that Lambda
  generate-site-files  Run content generator inside Docker container
  get-cert-arn         Show the CloudFront ACM certificate ARN
  get-cert-infra       Show cert CF stack events
  get-code-infra       Show code CF stack events
  get-infra            Show CF stack events
  help                 Show this help
  invalidate           Invalidate CloudFront cache for the site
  login                Open shell in Docker container
  login-scripts        Open shell in scripts Docker container
  logs                 Show logs of Docker container
  open                 Show local site URL
  rebuild              Rebuild and start Docker containers
  recreate-local-dynamodb Recreate DynamoDB table in local DynamoDB & populate dummy data
  restart              Restart local Docker containers
  tail-scripts-logs    Tail scripts logs
  tests                Run the test suite in the isolated test Docker Compose stack
  up                   Start local Docker containers
```

## Sign in with Apple

Sign-in uses Cognito's hosted login page. Apple appears alongside Google when
`APPLE_CREDENTIALS_SECRET_ARN` is set in ignored `.env.prod` and the infrastructure
is deployed. Leave it empty to keep Apple disabled. Both providers use the existing
`/login-callback` and application session cookies; no Lambda credentials are needed.

Follow [AWS's Apple setup guide](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-social-idp-apple.html)
to create an Apple App ID with Sign in with Apple enabled, an associated Services ID,
and a Sign in with Apple private key. Record the Services ID, Team ID, and Key ID.
Configure the Services ID with:

- Domain: `${AWS_STACK}.auth.${AWS_REGION}.amazoncognito.com` (substitute the deployment values).
- Return URL: `https://${AWS_STACK}.auth.${AWS_REGION}.amazoncognito.com/oauth2/idpresponse`.

Create a Secrets Manager secret in the stack's account and region containing JSON
with these fields (use your actual values through the AWS console):

```json
{
  "services_id": "com.example.catalog.login",
  "team_id": "EXAMPLETEAM",
  "key_id": "EXAMPLEKEY",
  "private_key": "<Apple .p8 private key>"
}
```

For `private_key`, remove the `.p8` PEM header/footer and line breaks, leaving the
base64 key body. Set `APPLE_CREDENTIALS_SECRET_ARN` to the secret's ARN. The
CloudFormation deployment identity needs `secretsmanager:GetSecretValue` and,
for a customer-managed encryption key, `kms:Decrypt`. CloudFormation resolves the
secret directly into Cognito; the private key stays out of environment files,
deployment arguments, and Lambda configuration.

On an explicitly authorized infrastructure deployment, Cognito enables Apple with
`email name` scopes and maps email, provider identity, and name. Test first and
repeat sign-ins with both Share My Email and Hide My Email after deployment.
Apple supplies names only on initial consent; a missing name uses the existing
application fallback and no public username until one is chosen. Provider usernames
remain authentication metadata. Hide My Email can create a separate account from Google
because the email addresses differ. Register the application's email sending
domain with Apple's private email relay service to deliver notifications to those
addresses.

For key rotation, create a replacement secret and update its ARN before deploying
the infrastructure. Updating only a secret's value does not cause CloudFormation
to refresh an unchanged Cognito resource. Verify Apple sign-in before revoking
the old key.

## Connecting login methods

Sign in to the profile you want to keep, open **Edit profile → Integrations**, and
select **Connect another login method**. Authenticate with another configured
Cognito sign-in option. Future sign-ins through either identity use the same
profile and articles, even when their email addresses differ.

Sign-in resolves saved provider identities. Matching email addresses do not merge
accounts. Identities already assigned to another application account cannot be
connected; this flow does not merge profiles or move content. Accounts without
email can use the site but cannot receive email notifications.

Release the web and API code together with the infrastructure configuration. The
Cognito client allows `https://api.${DomainName}/auth/login-link/callback`, and the
API receives the existing Cognito domain, user-pool ID, client ID, and client
secret for code exchange. Provider credentials remain in Cognito. Normal sign-in
and login connections verify token signatures, issuer, audience, expiry, and any
access-token hash; connections also verify the nonce. Callback queries are
redacted from request and referrer logs.

## AWS deployment

Use `.env.example` as the template for both environment files. Keep local
development values in `.env`, and configure production deployment values in
`.env.prod`, including the existing public Route 53 hosted zone, domain,
application settings, and AWS profile/region. Both files are ignored by Git.

The single `Makefile` retrieves each value from its intended file without
including or exporting either file wholesale. Local Docker settings, ports, and
the local DynamoDB region come from `.env`. AWS, application, domain, and
deployment settings come from `.env.prod`. Lambda ZIP timestamps are stored in
`.env.prod`, including when a standalone `generate-*-lambda-code-files` command
is used.

To use another production environment file for a command, override its path:

```sh
make PROD_ENV_FILE=.env.staging deploy
```

Authenticate the AWS CLI first (`make aws-login` when needed), then run:

```sh
make deploy
```

The command runs these steps sequentially, including with `make -j`:

1. Deploy `cf-cert.yml` in `us-east-1` and wait for the `static.${DOMAIN_NAME}` CloudFront certificate.
2. Deploy `cf-code.yml` in `AWS_REGION` to create the Lambda artifact bucket.
3. Build and upload all three Lambda ZIPs.
4. Read the CloudFront certificate ARN from its stack output and deploy `cf.yml`
   in `AWS_REGION`, including the API certificate and application resources.
5. Build and upload static files after the application creates the site bucket. The
   website is served directly by API Gateway at `${DOMAIN_NAME}`, while CloudFront
   serves S3 assets from `static.${DOMAIN_NAME}`.

During the first migration from the root-domain CloudFront distribution, the old
`us-east-1` root-domain certificate is retained so CloudFormation can finish the
alias cutover. After the deployment succeeds and CloudFront reports `Deployed`,
that unused certificate can be deleted manually from ACM.

## Lambda layout

- `shared/` — shared backend code and templates
- `web-lambda/` — website Lambda handler and dependencies
- `api-lambda/` — API Lambda

API endpoints are exposed through the dedicated `api.${DOMAIN_NAME}` API Gateway
custom domain, with no `/api` path prefix.

- `img-lambda/` — S3 image variant Lambda

## TODO

- optimize Projections for DynamoDB indexes
- optimize DynamoDB attributes
- map app's endpoints to api gateway
- delete public images func?
- add users email/sms notifications (post published/liked/disliked, user followed/blocked etc.)
- add meta info for tags and images (created_by, created_at)
- add aria attributes (+allow them in tinymce)
- add footer tag for post/articles, put related articles (Like "Futher reading", based on tags)
- replace env secrets with secrets manager storage (CS becomes slower)
- jpeg images have problems with dimensions determination (on uploads)
- add image watermarks
- add author to the footer
- update logo in google auth
- posts page: add popular tags to "filter by tags" block
- improve post comments
- posts form: submit slugs URL version (instead of queries)
- generate tag combos for post pages (article's tag combos for crawlers)
- remove personal contact details
- add user_name and user_slug attributes to articles, render user in article fragments, sync when user changed
- tinymce: on image change - call api to drop the old image
- content on article edit page is not editable
- cover all the avaiable web/API endpoints with integrations tests
- refactor article voting: vote from 0 to 5 (by star selection), user rating recalculated from article rating
- tags aliases: for example: cache=caching, cdn=content-delivery-network, etc.
- article page: similar articles section shows no the all articles
- article page: auto append/generate "More Articles to Read" paragraph
- add tags_url function
- instead of having article slug uniqueness we should have user_id + article slug uniqueness
- add categories and categories+ pages into sitemap

## Links

- favicon - https://realfavicongenerator.net

## Telegram administrator notifications

The application uses standard Python logging with a console handler and an optional
`TelegramHandler`. Business events use `logger.info(..., extra={...})`; failures
use `logger.error()` or `logger.exception()`. Application code does not call
Telegram directly. The handler attaches once to the `app` logger; child loggers
propagate to it. Third-party library logs are excluded.
This follows Python's [logger/handler model](https://docs.python.org/3/howto/logging.html#advanced-logging-tutorial).

Create a bot with [BotFather](https://t.me/BotFather), start a private chat with
it (or add it to your administrator group), and send it a message. Use the Bot
API's [getUpdates](https://core.telegram.org/bots/api#getupdates) method to find
that message's `chat.id`. Keep the token private; do not commit it.

Set these values in your local `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
TELEGRAM_LOG_LEVEL=INFO
```

`TELEGRAM_LOG_LEVEL` controls Telegram independently of the console threshold:

| Level | Records delivered |
| --- | --- |
| DEBUG | All application logs, including request diagnostics |
| INFO (default) | Activity, other informational logs, warnings and errors |
| WARNING | Warnings, errors and critical failures |
| ERROR | Errors and critical failures |
| CRITICAL | Critical failures only |
| OFF | None |

Missing credentials disable the handler. Invalid levels disable it with a console
diagnostic. This replaces `TELEGRAM_EVENTS_ENABLED` / `TELEGRAM_ERRORS_ENABLED`.
Recreate Docker Compose web/API containers after changing configuration. For AWS,
build/upload both Lambdas and run `make deploy-infra` (or use `make deploy`).
The CloudFormation bot-token parameter uses `NoEcho`.

Activity records cover newly saved users, articles, comments, contact messages,
and article status changes. Resource events include canonical URLs in log context when
a request is available; request-free commands retain identifier context. Existing-user logins do not log registration events.
Web/API exception handlers log unexpected exceptions and HTTP 5xx exceptions at
ERROR, and handled HTTP 4xx exceptions at INFO. Returning a 5xx response directly
does not trigger an exception handler; log the failure where it occurs. Existing logged failures, including publication email failures, also
reach Telegram at the configured level. Handled HTTP 4xx diagnostics use INFO.

The Telegram formatter includes stage, severity, logger name, message, exception
class, and supported record IDs/request context. HTTP errors include the method,
path, client IP, user agent, service and request ID. Query strings, cookies and
authorization headers are excluded. The client IP comes from the ASGI connection
(API Gateway `requestContext.http.sourceIp` in Lambda), not arbitrary forwarded
headers; behind a proxy it may identify the proxy. Full tracebacks remain in server
logs. Event messages exclude content bodies and contact details; keep credentials
and private data out of log messages. The handler redacts its bot token and does
not forward exception text, stack traces or arbitrary extra fields.

Delivery uses [sendMessage](https://core.telegram.org/bots/api#sendmessage), is
best effort, and runs synchronously with a two-second network timeout. It can add
latency per record. There is no background thread, retry queue, or guaranteed
delivery; outages and Telegram rate limits can drop alerts. Delivery errors write
a fixed diagnostic to stderr without re-entering logging or failing the request.
For high-volume durable delivery, use a separate worker/queue or CloudWatch log
subscription rather than an in-process background logging thread.

Image Lambda failures, startup failures and hard timeouts are outside these
web/API application logging hooks.

Run offline logging checks with:
`python3 -m unittest discover -s tests -p test_notifications.py`.
Exception-handler routing checks are in `tests/test_exception_handlers.py` and
run with the normal test suite (they require the application dependencies).

## Fonts

Configure optional Google Fonts in ignored `shared/data.json`:

```json
"fonts": {"body": "Rethink Sans", "headings": "Rethink Sans"}
```

Each role is optional. Empty settings use Bootstrap's font defaults without Google
Fonts requests. The same family is downloaded once when used for both roles.

## Telegram article sharing

Profile editing has Personal info, Links, Earn, and an owner-only Integrations tab.
An active user can connect one Telegram channel and manually share published
articles to it. Posts contain the title, category, preview, and canonical article
link. Sharing never happens automatically. The connected user and bot must have
posting permission; the application rechecks both before every share. Disconnecting
removes the connection without deleting existing Telegram posts.

Create a shared publisher bot with BotFather and set these in ignored `.env.prod`:

```dotenv
TELEGRAM_PUBLISHER_BOT_TOKEN=
TELEGRAM_PUBLISHER_WEBHOOK_SECRET=
```

Use a separate bot from Telegram logging, and generate a random webhook secret of
32–256 letters, digits, underscores, or hyphens. Deploy the infrastructure and API
changes together through the normal deployment workflow. Publisher credentials
are passed only to the API Lambda; users never enter their own bot tokens.

Review webhook registration using the deployed API base URL:

```sh
docker compose -f docker-compose.scripts.production.yml run --rm \
  -e API_BASE_URL=https://api.example.com scripts-production \
  python scripts/configure_telegram_webhook.py
```

Add `--apply` to register the reviewed webhook. Review output redacts the secret.
Registration does not send messages or drop pending updates. Until the bot token
and secret are configured, the profile explains that connections are unavailable.
Connection links expire after ten minutes and are bound to the initiating account
and the Telegram user who starts the connection. Webhook requests require the
configured Telegram secret header. Temporary records are removed on completion,
replacement, or disconnect; expired links cannot complete a connection.

### Account email notifications

The profile editor's Notifications tab controls emails for manual article status changes, new profile follows/blocks,
new article likes/dislikes, and articles matching subscribed interests. Existing accounts default to enabled; each
preference can be disabled independently. Toggling or switching an existing reaction does not send another email.
Emails are sent after successful writes, and delivery failures do not undo the action. Production uses SES and
`SES_FROM_EMAIL`; development saves multipart text/HTML messages in `EMAIL_FILES_DIR`.

Both API Gateway stages have access logs with 14-day retention. These infrastructure changes take effect on the
next explicitly requested deployment.

## Sharing, support links, and image branding

Article owners have Telegram and Threads controls on their own article pages. Connect accounts in
**Edit profile → Integrations**. Sharing is manual and requires a published article and an active account.
Confirmed shares save a timestamp independently of article edits; “Share again” is an intentional new post.
A timeout or uncertain provider response keeps the button disabled and asks the owner to check the destination.
Share-history storage failures never turn a successful remote post into a failed share.

Telegram posts attach the article's cover image when present, with the escaped title, category, preview,
and canonical URL in a caption. Text-only posts retain link previews. Only the preview is shortened to fit
Telegram's text/caption limit. Full article content is never sent. Threads uses the same metadata and optional
cover image; image processing continues while the page stays open. An uncertain publish is checked once
against recent posts and is never automatically repeated.

Configure `THREADS_APP_ID`, `THREADS_APP_SECRET`, and `THREADS_TOKEN_ENCRYPTION_KEY` in ignored `.env.prod`.
Generate the encryption key with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
Configure the app's OAuth redirect as `https://api.YOUR_DOMAIN/integrations/threads/callback` and grant
`threads_basic` and `threads_content_publish` access. The API encrypts tokens and binds them to the account,
Threads identity, and app ID. Changing the encryption key requires reconnection. Tokens and authorization
codes are excluded from callback access logs. These credentials are separate from any import provider.
Rebuild the API and test images after adding the API-only `cryptography` dependency.

The **Earn** profile tab preserves Buy Me a Coffee and supports up to 20 additional named HTTPS support
or membership links. Links can default to all articles or selected categories and can be hidden from the public profile independently.
Article forms can inherit these defaults or choose an explicit subset. Existing links default to all articles. Profile editing shows
reader click totals; owners also see article-specific totals beside their article's support links. Counts
exclude owner clicks and are best effort, not unique-visitor analytics. POST navigation uses HTTP 303 redirects, rejects cross-origin
submissions and prefetches, and works without JavaScript; signed internal API notifications keep database mutations in the API Lambda.
Existing Buy Me a Coffee links retain their current behavior and are not included in the new counters.

Optional article watermarks are disabled by default. Enable them in ignored `shared/data.json`:

```json
"article_media_watermark_enabled": true,
"article_media_watermark": {
  "text": "example.com",
  "position": "bottom-right",
  "opacity": 0.5,
  "relative_font_size": 0.025,
  "padding": 0.025,
  "center": false
}
```

Omit `text` to use the website hostname. Only newly uploaded article-editor images are branded; existing
images, avatar/category uploads, and animated images keep their current behavior. Static JPEG, PNG, WebP,
and AVIF originals are saved before publishing resized branded copies. Production stores originals in a
separate encrypted, private, retained S3 bucket. Locally they live in ignored `.media-originals/`, mounted
outside the public static directory. Never configure `MEDIA_ORIGINALS_DIR` inside `STATIC_FILES_DIR`.

## Background sitemap generation

`POST /generate-sitemap` returns HTTP 202 with `status: accepted` and the sitemap URL. A dedicated
15-minute Lambda uses the API bundle to generate and submit the sitemap, reloading the actor and checking
current permission before writing. It processes one invocation at a time and logs completion or failure.
The local API runs this work in a single background thread. Production requires `SITEMAP_FUNCTION_NAME`,
provided by CloudFormation; asynchronous function-error retries are disabled.

Infrastructure and API/static changes must be deployed together when deployment is explicitly requested.
The sitemap worker shares `ApiFuncS3Key`, so subsequent API bundle updates also require an infrastructure
update to refresh the worker. No credentials, webhook registrations, deployment, or live posts are created
by implementing or testing these features.

## Feedback and community activity

Article sidebars include an attribution-report link that opens Contacts with the canonical article URL
and fields for the original creator and source. The navigation and Contacts page offer an improvement
suggestion with a structured message. Prefills are escaped and limited to the form's 1000-character limit;
they do not submit or send anything automatically.

Telegram posts include a subscribe link when the connected channel has a public username. Threads posts
include a link to follow the connected account. Truncation reserves the title, article URL, and footer,
shortening only the preview. Private Telegram channels do not expose an invite link.

Profiles have separate notification preferences for new article comments, comment reactions, and article
shares. New preferences default to enabled. Emails follow successful persistence, skip self-notifications
and inactive recipients, and never undo an action if delivery fails. Comment emails link to the comment's
rendered anchor. Comment reactions toggle and switch like article reactions. Existing self-reaction
behavior is preserved; follow/block actions on one's own account remain unchanged.

The owner's profile includes given/received totals for article and comment reactions, comments, blocks,
and sharing by channel. Share controls support WhatsApp, Telegram, X, Bluesky, Threads, Facebook,
LinkedIn, Reddit, and email using the canonical page URL. Clicks count opening a sharing control; they
do not establish that a post was sent. Confirmed Telegram/Threads posts are counted separately.
Server-issued publishing IDs and client event UUIDs prevent retries from counting the same event twice. Uncertain remote publishing results remain uncounted, and analytics failures never turn a
successful remote post into a failed share. Reaction transactions check the previous saved action and
return 409 when it changed concurrently rather than applying a stale counter transition.

Directional reaction/comment counters need a historical backfill for complete totals. The maintenance
command scans durable relations and comments separately for each selected user. It defaults to read-only,
reports all intended counters, and guards writes against concurrent activity. It does not replay reactions,
send emails, or reconstruct historic share counts from publication timestamps.

```sh
docker compose -f docker-compose.scripts.production.yml run --rm scripts-production \
  python scripts/rebuild_engagement_statistics.py --table-name YOUR_TABLE --user-id USER_ID
```

Review dry-run output before adding `--apply`. Repeat `--user-id` for multiple accounts, or configure
`DYNAMODB_TABLE` instead of supplying `--table-name`. Successful reruns skip unchanged counter sets;
item errors remain retryable and cause a nonzero exit. A concurrent change leaves that user's counters
untouched; rerun it after activity settles. Authentication, permission, and configuration failures stop
execution. No new DynamoDB index is required.

Structured action context appears in console/CloudWatch logs as well as Telegram.
Sign-in and sign-out events include the profile URL; contact submissions include
the message ID and, for signed-in senders, their profile URL.
