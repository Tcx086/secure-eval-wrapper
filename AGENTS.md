# AGENTS.md

## Scope
These instructions apply to the entire repository.

This repository is being rebuilt as a public crypto-focused trading system framework. Codex must
preserve the public/private boundary and keep implementation progress auditable.

## Project Control Rules
- Before any functional work, read `docs/IMPLEMENTATION_STATUS.md` and `.project/implementation_status.json`.
- Every future functional PR must update both status files in the same change:
  - `docs/IMPLEMENTATION_STATUS.md` for the human-readable status.
  - `.project/implementation_status.json` for the machine-readable status.
- Keep completed work under `Completed` in the Markdown file and unfinished work under `Todo`.
- Keep JSON phase status synchronized with the Markdown phase status.
- If a functional change is made without status updates, add the missing status updates before finishing.
- Do not implement runtime features during documentation/control phases.

## Non-Negotiable Engineering Boundaries
- PostgreSQL is the only authoritative storage target.
- SQLite is explicitly disallowed as authoritative storage.
- Live trading must be disabled by default.
- Do not add real live trading unless a future phase explicitly enables guarded live execution.
- Do not add secrets, API keys, private strategies, real account data, or real trade logs.
- Do not commit raw private exports, local database state, or sensitive local logs.

## Public/Private Boundary
Public repository content may include:
- Architecture documents.
- Public framework interfaces.
- Public alpha examples.
- Synthetic or public sample data.
- Simulated execution and monitoring examples.
- Redacted public reports and aggregate metrics.

Private or local-only content must stay out of Git:
- Proprietary strategies.
- Private feature engineering.
- Exchange credentials.
- Real account snapshots.
- Real trade logs.
- Raw private data exports.
- Partner-specific confidential delivery material.

## Runtime Design Rules For Future Phases
- Public alphas must output standardized signals, not direct broker orders.
- Backtests must use the shared execution contract.
- Backtests must generate order intents, pass them through `SimulatedBroker`, receive fills, update positions, and then compute metrics.
- Paper and future live trading must use the same broker contract as backtesting.
- Live execution must require explicit environment flags, local-only API keys, max notional limits, dry-run support, kill switch support, and risk summaries.
- Simulated FIX-style monitoring must remain simulated unless a future approved phase introduces a real integration.

## Storage Rules
- Design and implementation must target PostgreSQL.
- Use migrations and repository abstractions for persistent storage.
- Do not introduce SQLite as the authoritative database, local primary database, or production-like storage substitute.
- Local PostgreSQL state belongs under ignored local runtime paths such as `var/postgres/`.

## Local Data Governance
- Treat `var/cache/`, `var/raw/`, `var/tmp/`, `var/logs/`, `var/postgres/`, and `var/private/` as local-only.
- Classify generated artifacts before sharing or committing.
- Public-safe artifacts may include aggregate metrics and redacted manifests.
- Private-only artifacts include raw account data, raw trade logs, private strategy outputs, and unredacted local exports.

## Change Checklist
Before finishing a functional change, verify:
- No secrets or private data were added.
- PostgreSQL remains the only authoritative storage target.
- Live trading remains disabled by default.
- Markdown and JSON implementation status files are synchronized.
- Runtime code changes include appropriate tests or documented verification.
- Public delivery artifacts are classified and redacted where needed.