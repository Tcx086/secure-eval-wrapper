# Secure Eval Wrapper

## What this is
Production-grade evaluation system for deterministic validation and secure artifact delivery.

Built for systems where logic cannot be open-sourced.

---

## System Overview

```text
                +--------------+
                | Strategy API |
                +------+-------+
                       |
                       v
               +---------------+
               | Eval Engine   |
               +------+--------+
                      |
                      v
        +-------------------------------+
        | Stress / Risk Suite           |
        | - Monte Carlo                 |
        | - Intrabar Probe              |
        +--------------+----------------+
                       |
                       v
            +----------------------+
            | Audit Layer          |
            | (hash, cfg, seed)    |
            +----------+-----------+
                       |
                       v
           +-------------------------+
           | Artifacts               |
           | - report                |
           | - metrics               |
           | - manifest              |
           +-------------------------+
```

---

## Key Features
- Deterministic pipeline execution (`seed + input snapshot + config snapshot + code hash`)
- Built-in stress testing (Monte Carlo and intrabar perturbation)
- Audit layer with checksum/hash verification
- Standardized artifact generation (`report`, `metrics`, `manifest`)
- Public infrastructure with private-logic isolation
- One-command orchestration for repeatable system runs

---

## How it works
1. Strategy adapter receives normalized inputs.
2. Eval engine computes decision outputs.
3. Stress/risk suite runs Monte Carlo and perturbation checks.
4. Audit layer records hashes and configuration snapshot.
5. Artifact packager writes delivery-ready outputs.

Execution chain:
`open-core/main.py` -> `src/eval_cli.py` -> `src/eval/*` -> `delivery/*`

---

## Quick Start
Run full pipeline from `open-core`:

```powershell
cd open-core
D:\qt\.python\python.exe main.py
```

Or run specific mode:

```powershell
D:\qt\.python\python.exe main.py --mode quant
D:\qt\.python\python.exe main.py --mode generic
```

---

## Output Artifacts
Quant pipeline (`delivery/demo-run`):
- `repro_manifest.json`
- `evaluation_report.md`
- `evaluation_metrics.json`
- `signal_output.json`
- `model_card_public.md`

Generic pipeline (`delivery/generic-demo`):
- `generic_manifest.json`
- `generic_report.md`
- `generic_metrics.json`

System run log (`delivery/system-run`):
- `system_run_summary.json`

---

## Design Principles
- Deterministic pipeline behavior
- Audit-first delivery
- Explicit public/private boundary
- Standardized outputs for promotion decisions

---

## Notes on Confidentiality

### Confidentiality Design
This system is designed to support:
- Public infrastructure
- Private strategy logic

Only aggregated metrics are exposed.
Trade-level logs and feature attribution are intentionally excluded to reduce reverse-engineering risk.

Show the system. Protect the edge.

---

## Example Output
Public sample highlights:
- Deterministic manifest with hash chain
- Quant stress evidence (Monte Carlo + intrabar)
- Generic non-quant evaluator artifacts

---

## Appendix
### A) Public Sample Metrics (Sanitized)
#### v5 Standalone (Math/Stat Edge, v3+v4)
| Metric | Value |
|---|---:|
| Public Metric Cost Basis | `16 bps` |
| Annualized Return | `19.15%` |
| Max Drawdown | `10.98%` |
| Sharpe | `0.9376` |
| Win Rate | `34.75%` |
| Monte Carlo CAGR P50 | `12.18%` |
| Stress Test Worst-Case Return | `-23.36%` |

#### v6 Standalone (News-Driven Edge)
| Metric | Value |
|---|---:|
| Cost Assumption | `22 bps` |
| Annualized Return | `40.55%` |
| Max Drawdown | `-26.78%` |
| Sharpe | `1.3789` |
| Survivability Conclusion @22 bps | `Yes` |

### B) Repository Map
- `open-core/`: runtime pipeline and evaluation engine
- `api-spec/`: API contract stub
- `security/`: baseline controls and threat model
- `delivery/`: generated artifacts and run outputs
- `private/`: local-only integration notes
- `docs/`: flow, reliability, extensibility, scale notes
