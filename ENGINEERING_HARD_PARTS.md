# Engineering Hard Parts

## 1) Deterministic Reproducibility
- Every demo run is tied to a fixed seed.
- Input snapshots are hashed.
- Config and code are hashed in reproducibility manifests.
- Same input + seed + config + code hash should produce the same artifact outputs.

## 2) Sealed Private Strategy Boundary
- Public repository exposes only framework contracts and demo strategy.
- Proprietary strategy logic lives in local/private paths excluded by `.gitignore`.
- Public outputs are intentionally aggregated to reduce reverse-engineering risk.

## 3) Promotion Gate (Research -> Sim)
- Snapshot freeze.
- Backtest and risk suite execution.
- Stress and intrabar checks.
- Artifact packaging for audit trail before promotion.

## 4) Cost Assumption Hygiene
- Different strategy lines can use different cost assumptions (`v5 = 16 bps`, `v6 = 22 bps`).
- Cost basis is stated explicitly next to each metric table.
- Avoid cross-line delta claims when entry/exit mechanics differ.

## 5) Why This Is Engineering-Centric
This repository focuses on system trustworthiness:
- deterministic runs,
- auditable artifacts,
- reproducibility contracts,
- secure delivery boundaries.

The goal is to demonstrate software architecture and evaluation rigor, not only strategy outcomes.

## 6) Migration Runner Discipline
- Local migration metadata is bootstrapped before migration application.
- Each migration is tracked immediately after successful application.
- Previously recorded migrations are skipped only when their stored SHA256 matches the local file.
- Failed migrations stop clearly without silently leaving untracked partial state.
