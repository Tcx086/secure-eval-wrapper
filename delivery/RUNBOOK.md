# Delivery Runbook

## One Command
From `open-core`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

## Output
Generated under `../delivery/demo-run/`:
- `signal_output.json`
- `evaluation_metrics.json`
- `evaluation_report.md`
- `repro_manifest.json`
- `model_card_public.md`

Packaged zip:
- `../delivery/demo-run.zip`

## Reproducibility Check
- Keep the same input snapshot file.
- Keep the same seed/config.
- Keep the same code hashes in `repro_manifest.json`.

