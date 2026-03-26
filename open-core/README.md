# Open Core (Public)

## What this is
Runtime core for a production-style evaluation system.

## Main entrypoint
Run full pipeline:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode all
```

Run specific mode:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode quant
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode generic
```

## Included
- Strategy interface contract
- Demo strategy (non-edge)
- Reproducible quant evaluation pipeline
- Generic non-quant evaluator demo
- Artifact generation scripts

## Not Included
- real_test_v5 / real_test_v6 private logic
- private params or secrets
