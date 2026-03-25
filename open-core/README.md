# Open Core (Public)

This folder is safe to open source.

## What is included
- Strategy interface
- Demo strategy (non-edge)
- Data loaders (market + rss features)
- Local CLI runner

## What is NOT included
- real_test_v5 / real_test_v6 logic
- private params or secrets

## Run demo
```powershell
python -m src.cli --input data/sample/features.json --strategy demo
```

## One-click external package (recommended)
This runs signal demo + evaluation + zip packaging in one command:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

## Generate public evaluation artifacts
This command creates:
- `repro_manifest.json`
- `evaluation_report.md`
- `model_card_public.md`
- `signal_output.json`
- `evaluation_metrics.json`

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_eval.ps1
```

## Plug in real strategy (local only)
1. Place your private code under `../private/real_strategy_v5_v6/`
2. Implement the same interface as `src/core/strategy_base.py`
3. Register strategy locally in `src/registry.py` (do not publish that change)
