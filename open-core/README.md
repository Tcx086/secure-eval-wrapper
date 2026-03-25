# Open Core (Public)

This folder is safe to open source.

## Included
- Strategy interface contract
- Demo strategy (non-edge)
- Reproducible evaluation CLI (MC / stress / intrabar)
- Generic non-quant evaluator demo
- Local scripts for one-click artifact generation

## Not Included
- real_test_v5 / real_test_v6 private logic
- private params or secrets

## Commands
### Signal demo
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1
```

### Evaluation package (quant demo)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_eval.ps1
```

### One-click package (signal + eval + zip)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

### Generic non-quant demo
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_generic_demo.ps1
```

## Plug in real strategy locally
1. Place private code under `../private/real_strategy_v5_v6/`
2. Implement `src/core/strategy_base.py` contract
3. Register locally in `src/registry.py` (do not publish that change)
