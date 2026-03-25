# Performance and Scale Notes

## Current Design
- Batch-oriented CLI pipeline.
- Deterministic artifact generation.
- Stage-level process orchestration.

## Scale Controls
- Stage-level timeout (`--timeout-sec`).
- Retry/backoff controls (`--retries`, `--retry-backoff-sec`).
- Artifact partitioning by run directory.

## Throughput/Lag Considerations
- Suitable for scheduled batch evaluation jobs.
- For higher throughput, split stage execution across workers and queue runs by run-id.
- For lower latency, replace script-process boundaries with in-process task execution.

## Future Upgrades
- Job queue (Redis / SQS style)
- Parallel stage fan-out where safe
- Incremental data snapshots
- Structured metrics export (Prometheus/OpenTelemetry)
