# Encrypted application configuration

Application credentials can use individual **standard Parameter Store SecureString**
records under `PROD_CONFIG_PREFIX`. Ordinary settings remain individual `String`
records. The explicit `SECRET_KEYS` and `CONFIG_KEYS` sets in
`scripts/production_config.py` define the complete supported key inventory;
empty optional keys have no AWS record. Values are never bundled into an AWS
configuration document or registry.

The bootstrap field `PROD_SECRET_STORE=ssm` selects encrypted parameters. Its absence
retains the legacy Secrets Manager reader. There is no plaintext or legacy fallback
after switching: invalid/missing required values stop configuration loading. Only
public settings enter Make; decrypted credentials remain process-only and reach
CloudFormation through the existing memory-only payload and `NoEcho` parameters.
Lambdas keep their existing injected values and do not read configuration stores
at startup or per request.

## Migration

Upgrade the production loader and `secure_parameters.py` in every checkout sharing
the bootstrap before switching. Existing signing/encryption keys must not change.
Run the migration through the production scripts service, preview first:

```bash
export PROD_CONFIG_DIR="$(dirname "$(readlink -f .env.prod)")"
export PROD_CONFIG_FILENAME="$(basename "$(readlink -f .env.prod)")"
docker compose -f docker-compose.scripts.production.yml run --rm --entrypoint python3 scripts-production \
  scripts/secure_parameters.py --env-file "/production/$PROD_CONFIG_FILENAME"
```

After reviewing the names, types, and tags, append `--apply` for an authorized
migration. The utility checks all destinations before writing, never overwrites
a different value, decrypt-verifies every copy, checks its tags, and compares the
complete old/new configuration before atomically switching the resolved bootstrap.
Worktree symlinks, artifact timestamps, and cache counters remain intact. No secret
values are printed, put in command arguments, or backed up to disk.

Standard parameters are limited to 4 KiB. Customer-managed encryption keys are
preserved. Secrets using managed rotation, replication, provider ownership, or CloudFormation ownership require separate review. Existing encrypted destinations must also use Standard tier.
Secrets Manager's AWS-managed key is replaced by Parameter Store's AWS-managed key
for the new encrypted record; existing custom-key access must support SSM.

The copy utility retains old secrets for rollback. After reader cutover and
value/tag verification, review exact source names/ARNs, lifecycle metadata and current source/destination equality, then obtain a separate explicit cleanup command to retire only the exact superseded scalar records with a
30-day Secrets Manager recovery window. Scheduled deletion stops their storage
charges while allowing restoration during that window; never force-delete them.
Provider credential documents and CloudFormation-generated Cognito credentials
keep their existing ownership and remain outside this scalar-credential migration.

Rollback during the recovery window requires restoring the retired source secrets
before selecting `PROD_SECRET_STORE=secretsmanager` under the shared lock. Verify
the restored values before switching; no signing or encryption key is regenerated.

## CloudFormation ownership and tags

The main CloudFormation template does **not** declare bootstrap-owned Parameter
Store records. `AWS::SSM::Parameter` does not support creating `SecureString`:
https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ssm-parameter.html

Keep the typed key inventory in the loader, the ownership contract here, and the
actual record inventory in the migration's redacted stdout. Do not put decrypted
values in template `Metadata`, `Outputs`, descriptions, or a second manifest.
Nonsecret parameters can be owned by CloudFormation in a separately reviewed
infrastructure change, but existing records must be imported before a stack claims
ownership; do not give both the bootstrap script and a stack competing ownership.

The migration applies the current stack's tags to both encrypted credentials and
existing ordinary parameters, retaining other tags. These include `Project`,
`Owner` when configured, `Stage`, and `Region`. Secret-source tags are retained too.
New application values should follow the same per-value type and ownership tags.
The migration itself does not deploy an application or change any Lambda value.

## Permissions and worktrees

Migration needs scoped `ssm:GetParameters`, `ssm:GetParameter`,
`ssm:DescribeParameters`, `ssm:PutParameter`, `ssm:AddTagsToResource`,
`ssm:ListTagsForResource`, `secretsmanager:GetSecretValue`,
`secretsmanager:DescribeSecret`, and `cloudformation:DescribeStacks`.
Custom encryption keys additionally require `kms:DescribeKey`, `kms:Decrypt`,
and permission to encrypt through Parameter Store. No deletion permission is
needed. Production loader reads use explicit names, with decryption enabled only
for the credential inventory. There is no new Lambda store access or deployment.

`configure_worktree_production` copies the invoking checkout's current
`production_config.py`, `production_lock.py`, and `secure_parameters.py` into
new worktrees, including ones whose committed baseline predates the migration.
Existing worktrees sharing a bootstrap must receive compatible tooling before
cutover; preserve their edits when reconciling helpers. Take the common lock
beside the resolved bootstrap for migration and tooling cutover.

Ported from PromptCatalog commit `97f6dd0`, with SysDesPro's provider-document
exceptions and a Standard-tier readback check. Legacy local-to-cloud migration
and prefix relocation are blocked for a secure-mode bootstrap to prevent moving
credentials back into Secrets Manager accidentally.
