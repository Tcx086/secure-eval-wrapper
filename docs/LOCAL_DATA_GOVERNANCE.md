# Local Data Governance

## Purpose
Local data governance prevents private data, secrets, raw exports, and sensitive logs from entering
the public repository. The project is public, but future workflows may touch private local data.
The boundary must be explicit.

## Local Runtime Folders
The following folders are local-only and should be ignored by Git:

| Path | Purpose | Public-safe |
|---|---|---|
| `var/cache/` | Provider caches and derived temporary caches | No |
| `var/raw/` | Raw provider downloads and raw private exports | No |
| `var/tmp/` | Temporary working files | No |
| `var/logs/` | Local logs | No |
| `var/postgres/` | Dockerized PostgreSQL data directory | No |
| `var/private/` | Private local-only experiments or exports | No |

These folders may be created locally by developers or scripts, but their contents must not be
committed.

## Secret and Private File Rules
Never commit:
- `.env`
- `.env.*`, except `.env.example`
- API key files.
- Exchange credential files.
- Private strategy files.
- Raw account exports.
- Raw trade logs.
- Private partner delivery material.
- Database dumps containing private data.

Suspicious filename patterns:
- `*api-key*`
- `*apikey*`
- `*secret*`
- `*secrets*`
- `*credential*`
- `*private*`
- `*account*`
- `*trade_log*`
- `*.pem`
- `*.key`
- `*.p12`
- `*.pfx`
- `*.kdbx`

## Automatic Stale Cache Cleanup
Future cleanup scripts should support:
- Deleting cache files older than a configured TTL.
- Deleting temporary files older than a short TTL.
- Keeping raw private exports only when explicitly retained.
- Producing a cleanup summary.
- Refusing to delete files outside approved local runtime directories.

Suggested TTL defaults:
- `var/tmp/`: 24 hours.
- `var/cache/`: 7 days.
- `var/logs/`: 14 days, unless needed for an active investigation.
- `var/raw/`: manual review before deletion.

## Local File Audit
Before public delivery or commit, run a local file audit.

Audit checks:
- Detect ignored runtime folders that accidentally became tracked.
- Detect suspicious filenames.
- Detect large raw exports.
- Detect secret-like tokens in staged files.
- Detect private account terms in public artifacts.
- Detect unredacted trade-level logs in delivery outputs.

The audit should produce file path, classification guess, triggered rule, and recommended action.

## Sensitive Data Scan
Sensitive scans should look for API key patterns, private key headers, exchange account IDs, email
addresses where not expected, access tokens, raw order IDs from real venues, account balances, and
private strategy names. The scan should err on the side of warning.

## Generated Artifact Classification
Every generated artifact should be classified:
- `public_safe`: safe to commit or share.
- `public_redacted`: safe only after redaction has been applied.
- `private_only`: must stay local.
- `secret`: must be removed immediately and rotated if exposed.

Examples:
- Public model card: `public_safe`.
- Aggregate backtest metrics without private logic: `public_safe` or `public_redacted`.
- Repro manifest with local private paths: `public_redacted`.
- Raw account snapshot: `private_only`.
- API key file: `secret`.

## Public Delivery Rules
Public delivery may include architecture docs, public demo code, public alpha examples, synthetic
sample data, aggregated metrics, redacted manifests, public model cards, and hash references.

Public delivery must not include private strategy source, real account snapshots, raw private
exports, API keys or secrets, sensitive trade-level logs, or partner-specific confidential context.

## Git Ignore Policy
The repository should ignore local runtime data under `var/`, secret files, private exports,
archives and database dumps unless explicitly public-safe, logs, and temporary files. The ignore
policy is a guardrail, not the only defense. Commit review and local audits are still required.

## Developer Workflow
Recommended workflow before sharing:
1. Run tests or documentation checks.
2. Run local file audit.
3. Inspect `git status`.
4. Inspect staged diff.
5. Confirm no ignored private paths are force-added.
6. Confirm artifacts are classified.
7. Commit only public-safe files.