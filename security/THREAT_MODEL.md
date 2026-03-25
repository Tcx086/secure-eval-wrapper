# Threat Model

## Threat Actors
- External reviewers attempting reverse engineering.
- Leaked artifacts reused outside intended scope.
- Misconfigured pipeline exposing sensitive internals.

## Attack Surfaces
- Overly detailed reports/logs.
- Public repo commits with private files.
- Runtime outputs containing secrets or raw private features.

## Mitigations
- Public/private boundary via repository layout and `.gitignore`.
- Sanitized aggregate outputs in public artifacts.
- Hash-based manifests without secret payloads.
- Local-only private strategy integration.

## Residual Risk
- Aggregate metrics can still leak weak signals about behavior.
- Avoid publishing trade-level logs and feature-level attribution.
