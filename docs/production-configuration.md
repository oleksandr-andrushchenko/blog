# Production configuration resolved at deployment

Each production value has its own AWS record, named after its uppercase environment variable:

| Value | AWS record | Stored value |
| --- | --- | --- |
| `AUTH_JWT_SECRET` | Secrets Manager `sysdespro/prod/AUTH_JWT_SECRET` | The raw signing secret |
| `TELEGRAM_BOT_TOKEN` | Secrets Manager `sysdespro/prod/TELEGRAM_BOT_TOKEN` | The raw bot token |
| `DOMAIN_NAME` | Parameter Store String `/sysdespro/prod/DOMAIN_NAME` | The domain name |
| `TELEGRAM_LOG_LEVEL` | Parameter Store String `/sysdespro/prod/TELEGRAM_LOG_LEVEL` | The log level |

There is no aggregate JSON record or stored key manifest. Bootstrap prefixes identify the namespaces;
the explicit code classification identifies individual keys. Blank optional values are represented by absent
records, because SSM does not allow empty strings. Migration prints an explicit `skip empty` for those keys.

Both stores use the project/stage prefix directly, without a `settings/` or `secrets/` segment.
Parameter Store names start with `/`; Secrets Manager names do not.
Prefixes are configurable through the bootstrap file. The explicit classification is in
`scripts/production_config.py`; unknown keys fail migration rather than silently becoming plaintext settings.
OAuth client IDs, public TinyMCE/analytics IDs and the public IndexNow ownership key are nonsecrets.
Credentials remain partitioned by consumer in CloudFormation: API publisher secrets are not added to the web Lambda.

The local `.env.prod` becomes the small bootstrap shown in `.env.prod.example`: AWS profile, region, stack,
namespace prefixes, generated ZIP timestamps, and CSS/JS cache counters. Keep its existing timestamps and cache
counters. Stack-owned resources continue using CloudFormation references. Existing Apple, Facebook and Search
Console credential secrets remain separate; this migration neither copies nor changes their contents.

## Runtime and update behavior

The production lock is acquired before settings are read. Make receives only the nonsecret settings it needs
for certificate and build commands. Immediately before deploying the application stack, the deployment helper
reads the current parameters and secrets by name. It passes their values to the existing AWS CLI CloudFormation
deploy command as a JSON payload through a seekable memory-only descriptor (`--parameter-overrides file:///proc/self/fd/...`).
The descriptor is needed because the AWS CLI may read its input more than once; no secret file is written to disk.
There are no pinned versions, version hashes, dynamic secret references for these new records, or refresh markers.

CloudFormation receives ordinary parameter values. If a value changes, its resource properties change and the
next deployment applies it to Lambda environments, Cognito, and other consuming resources. Unchanged values
produce no configuration update. The existing template-upload bucket/prefix, certificate checks, tags, and
empty-change-set handling are preserved. No raw credentials pass through Make, command arguments, temporary
files, or console output. Raw provider deployment errors are captured and replaced by a redacted error message.

The application still uses `shared/app_config.py` and ordinary environment variables. There is no new SDK
lookup, extension, cache, background refresh, or network call in Lambda startup or request handling. Updating
AWS configuration alone does not change deployed functions. The next explicit infrastructure/full deployment
applies it. Code/static builds alone do not apply configuration changes. Pre-existing feature-specific lazy
reads of separate provider secrets retain their existing behavior.

Shared authentication/encryption keys must remain compatible between web, API and workers; this tooling never
generates replacements or rotates them. Lambda environment values remain encrypted at rest and accessible to
appropriately authorized principals; storing their source in Secrets Manager does not remove copies from Lambda
configuration. CloudFormation sensitive parameters retain their existing `NoEcho` protection.

Production scripts load raw credentials into their process environment **after container startup**, before
importing application modules. Credentials are not placed in Docker's configured environment, build artifacts,
temporary env files, command arguments, or configuration-review output. Scripts reload AWS configuration on
launch; running jobs retain their snapshot. The shared production lock covers the job's lifetime, so pause long
maintenance runs before deploying. Unknown/malformed schemas, unsafe Make interpolation, and AWS errors stop
execution without falling back to stale local secrets.

## Review and migrate the existing file

Implementation does not migrate resources or authorize deployment. The migration defaults to a local, read-only
review. It prints one redacted JSON review line per value with its service, record name and intended action,
never values, and makes no AWS calls in dry-run mode. Run it through the production scripts service from the
main checkout:

```sh
docker compose -f docker-compose.scripts.production.yml run --rm --build scripts-production \
  python3 scripts/production_config.py migrate \
  --parameter-prefix /sysdespro/prod/ --secret-prefix sysdespro/prod/
```

After reviewing that output, an explicitly authorized run with `--apply` creates each nonempty value as a
separate record, verifies the individual values by reading them back, then atomically replaces the resolved local `.env.prod` with a mode-0600
bootstrap. Worktree symlinks are preserved. No secret backup is written. Other existing private backups are not
deleted by this command. It never overwrites existing record values. Interrupted runs can reuse records only if
they exactly match the corresponding local values; mismatches leave the local source intact and require review.
A partial failure can leave some AWS records created; retain the local file and retry after correcting the failure.

Migration itself does not change deployed resources. Once migration is verified, use an explicitly authorized
`make deployment` (or the requested scoped infrastructure deployment) to apply the current values.
Review the CloudFormation change set as usual. No webhook registration or CDN invalidation is part of migration.
The legacy local-file deployment path is retained until migration completes.

For an existing cloud-mode bootstrap, changing a prefix alone does not rename AWS records.
Create and verify records under the shorter names before switching the active bootstrap prefixes.
Use the read-only relocation review through the production service:

```sh
docker compose -f docker-compose.scripts.production.yml run --rm --build scripts-production \
  python3 scripts/production_config.py relocate \
  --parameter-prefix /sysdespro/prod/ --secret-prefix sysdespro/prod/
```

After review, add `--apply` to an explicitly authorized run. Relocation copies the current values,
reuses identical destination records without overwriting, verifies the complete destination snapshot,
and atomically updates the bootstrap while preserving generated deployment state and symlinks.
A failure leaves the original prefixes active; rerunning resumes safely. The shared production lock
covers the operation. Relocation retains the old records; removing them requires a separately authorized cleanup.

## Permissions and operations

The deployment principal needs scoped
`ssm:GetParameters` and `secretsmanager:GetSecretValue` on the configured record prefixes, plus `kms:Decrypt`
if using a customer managed key. Migration additionally needs `ssm:PutParameter` and `secretsmanager:CreateSecret`. Production script
credentials need the corresponding read permissions. **Application Lambda roles and the CloudFormation service role need no new store-read permissions.**
The migration uses the default Secrets Manager encryption key; existing custom-key stores can be provisioned
separately and reused only with identical reviewed values.

Each record stores its value directly as a string, not JSON. Optional integrations use absent records to
stay disabled; documented defaults apply to omitted log-level and API-version settings. Required app/signing
keys and deployment settings must remain present. Each standard SSM parameter is limited to 4 KB; each secret
is limited to 64 KB. Migration checks each value's UTF-8 size. Normal deployment settings may not contain
Make/shell syntax or whitespace; credentials can contain punctuation because they are sent through a memory-only descriptor.
When adding a provider, classify its fields explicitly before creating its records. Deployment reads explicit
names, so it does not need `ListSecrets` or `GetParametersByPath` permission. SSM batched reads use
`ssm:GetParameters`; resuming a partially applied migration also uses `ssm:GetParameter`.

Deployments use the host AWS CLI/login cache without requiring host Python packages. Production containers
use the CLI when installed, otherwise their existing boto3 dependency. Provider errors are redacted.

New worktrees record `PROD_ENV_FILE`, `PROD_CONFIG_DIR`, and `PROD_CONFIG_FILENAME` in their local `.env`.
The production Compose service mounts the main bootstrap directory for shared locking and atomic migration.
It also mounts that directory at its absolute host path when configured, so worktree symlinks resolve. Never
bind-mount `.env.prod` itself: a file mount makes atomic replacement fail even through a directory alias.
For an existing worktree or a custom bootstrap filename, export the resolved locations before running Compose:

```sh
export PROD_ENV_FILE="$(realpath .env.prod)"
export PROD_CONFIG_DIR="$(dirname "$PROD_ENV_FILE")"
export PROD_CONFIG_FILENAME="$(basename "$PROD_ENV_FILE")"
```

Make exports these locations automatically. Older checkouts without this configuration loader must be upgraded
before deploying against a migrated file; they fail rather than deploying empty settings.

Validation: `make tests`, including synthetic store reads/writes, redaction, Make command expansion, partial
migration recovery, mode/symlink preservation, credential isolation, and secure CloudFormation parameter delivery. Tests
never call live Secrets Manager or Parameter Store. Run the migration dry run to review the actual local key set.

## Makefile maintenance

Make inherits the nonsecret owner, stage, domain and hosted-zone settings required for certificate and code-bucket
commands. Application parameters and asset cache counters are read by `production_config.py`; do not duplicate
those settings as Make variables. A minimal local fallback supports pre-migration certificate/bootstrap commands.

`make clean` removes `.site-build`, `.code-build`, and the three Lambda vendor directories (`.tmp/web`,
`.tmp/api`, `.tmp/img`). It preserves importer checkpoints, caches and other state elsewhere in `.tmp`.
Full code builds clean their own vendor directories and do not remove the entire temporary directory.
The legacy wildcard `drop-cdn-cache` target is removed. Cache invalidation requires an explicit instruction
and a command listing only exact affected file paths.
