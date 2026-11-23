# Pinterest integration

Configure public website links in `.env` locally or individual production AWS configuration records for production, independently of account publishing:

```dotenv
SITE_TELEGRAM_CHANNEL_URL=https://t.me/example_catalog
SITE_THREADS_PROFILE_URL=https://www.threads.com/@example_catalog
SITE_PINTEREST_PROFILE_URL=https://www.pinterest.com/example_catalog/
```

Empty values hide the links. Valid links appear in the footer, Contacts, and the
website publisher's existing structured data. These settings never connect a
user's account or authorize publishing.

For user Pinterest connections, register a Pinterest developer app and configure:

```dotenv
PINTEREST_APP_ID=
PINTEREST_APP_SECRET=
PINTEREST_TOKEN_ENCRYPTION_KEY=
```

For production, store `PINTEREST_APP_ID` as an individual Parameter Store String,
and `PINTEREST_APP_SECRET` and `PINTEREST_TOKEN_ENCRYPTION_KEY` as separate raw-value
Secrets Manager records. Local development reads these ordinary values from `.env`.
No AWS records are created for local development. Generate the encryption key once with:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Register the API callback URL, for example
`https://api.catalog.example/integrations/pinterest/callback`. It is derived from
`API_BASE_URL`. A real connection requires HTTPS web/API URLs; use HTTPS development
hosts or a tunnel when testing locally. Recreate containers after changing environment
values. Production requires deploying the CloudFormation environment mappings and
application code together; no new settings are applied by a code-only deployment.

The app requests `user_accounts:read`, `boards:read`, and `pins:write`. Users connect
from their own profile's Integrations tab, choose one of their public boards, and
optionally enable profile links on their user/article pages. Visibility preferences
use the profile Save action; board changes save immediately. Disconnecting hides
public profile links while retaining visibility preferences and existing Pins.

Only active article owners can publish their published articles. Sharing uses the
article cover image, title, public preview, and canonical article link. The full article body is never sent.
Articles without a public HTTPS cover image cannot be shared to Pinterest. Each new explicit share uses a UUID receipt; retries
of the same request do not create another Pin. Unconfirmed writes keep the button
disabled and direct the user to check their Pinterest profile.

Pinterest requires a business account to administer the developer app and app
approval. Trial access creates sandbox Pins visible only to their creator. Obtain
Standard access before offering public publishing to regular users:

- [Connect an app](https://developer.pinterest.com/docs/getting-started/connect-app/)
- [Access tiers](https://dev.pinterest.com/docs/key-concepts/access-tiers/)
- [OAuth and token refresh](https://developer.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/)
- [Create boards and Pins](https://developer.pinterest.com/docs/work-with-organic-content-and-users/create-boards-and-pins/)

See [configuration and secret storage](production-configuration.md) for runtime injection, local overrides and deployment details.
