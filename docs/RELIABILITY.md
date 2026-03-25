# Reliability Design

## Failure Handling
- Per-stage retries with exponential backoff pattern.
- Fail-fast on irrecoverable stage failure.
- Timeout guard per stage.

## Logging
- Stage attempt logs in `delivery/orchestrator/`.
- Run summary JSON for postmortem trace.
- Captures stdout/stderr for each attempt.

## Observability Hooks
- Run summaries can be scraped by external monitor.
- Add heartbeat + status callbacks if integrating with a scheduler.

## Operational Posture
- Deterministic reruns supported by fixed seed and manifests.
- Artifact-first workflow for promotion decisions.
