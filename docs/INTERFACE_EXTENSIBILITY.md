# Interface and Extensibility

## Existing Abstractions
- `StrategyBase` defines strategy contract.
- `registry.py` maps strategy identifiers to implementations.
- CLI runners separate orchestration from strategy logic.

## Plugin Direction
- Keep stable interface: input schema -> signal schema.
- Register plugins through explicit registry (or plugin loader in future).
- Preserve deterministic behavior constraints for all plugins.

## Why It Matters
- New strategies can be added without changing orchestration core.
- Same evaluation harness works across quant and non-quant demos.
