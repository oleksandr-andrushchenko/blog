# Facebook Page publishing

An administrator with `manage-facebook-page` and `utils` can connect a site-wide Page in **Utils → Facebook Page**. Connect to Meta, choose a Page with publishing access, and select **Use this Page**. Reconnecting keeps the selected Page available until a replacement is selected. Root users inherit both permissions.

An active user with those permissions can share a published article they own from the article’s **Share article** panel. Each post includes the title, plain-text public preview, canonical article URL, and hashtags derived from the category and topics. A post with an article cover attaches that image. A post without a cover sends a Page feed link. The Facebook login integration and public Facebook share clicks use independent configuration and accounting.

## Meta setup

Configure these settings in the API environment for local development or through the existing production configuration store.

| Setting | Production parameter type |
| --- | --- |
| `FACEBOOK_PAGE_APP_ID` | String |
| `FACEBOOK_PAGE_API_VERSION` (defaults to `v27.0`) | String |
| `FACEBOOK_PAGE_APP_SECRET` | SecureString |
| `FACEBOOK_PAGE_TOKEN_ENCRYPTION_KEY` | SecureString |

The legacy configuration store uses an individual Secrets Manager record for each credential. Use a persistent Fernet key and reconnect the Page if the key changes. The web Lambda receives the `FACEBOOK_PAGE_PUBLISHER_ENABLED` capability flag. The API Lambda receives the credentials. Ship the API and web code with the CloudFormation configuration.

Register `/integrations/facebook-page/callback` on the configured API HTTPS origin as a valid Meta OAuth redirect URI. The web origin also needs HTTPS. For local OAuth testing, set the web capability flag explicitly and use a Compose override for the HTTPS origins and allowed origin. The Meta app requests `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`. The connecting account needs the Page’s `CREATE_CONTENT` task. Configure app review and access as applicable for the intended operators. [Meta Page Feed documentation](https://developers.facebook.com/docs/graph-api/reference/page/feed/).

Site-hosted covers in formats such as WebP and AVIF are converted to JPEG before uploading to the Page. Photos are uploaded unpublished and attached to a subsequent Page Feed request. [Meta Page Photos documentation](https://developers.facebook.com/docs/graph-api/reference/page/photos/).

## Connection and publishing behavior

OAuth binds a short-lived state to the active operator, browser cookie, and signed-in session. The API consumes the callback claim before exchanging the code and verifies the requested permissions. Page selection consumes the encrypted temporary authorization. The selected Page token is encrypted in the connecting account’s partition, with an account reference at the site level. Disconnect or account erasure removes the corresponding stored connection. Disconnecting in Utils leaves existing posts on Facebook. Provider access can be withdrawn in Meta’s settings.

Each share uses a UUID receipt. Before the public post, the API checks that the account, published article, and selected Page still qualify. This check is repeated after cover upload. A timeout or an incomplete response leaves the share unconfirmed and requires checking the Page before another attempt. Confirmed shares update `facebook_shared_at` and `facebook_publish` without changing the article’s content timestamps. Remote success is returned even when recording local history fails.

Verification uses mocked Graph responses in `make tests` and runs the connection and share controls with `node --test tests/test_facebook_page_ui.cjs`.
