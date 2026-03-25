# Generic Evaluation Report (Non-Quant Demo)

## Core Metrics
- Accuracy: 1.0
- Precision: 1.0
- Recall: 1.0
- F1: 1.0
- Brier: 0.093417
- LogLoss: 0.341884

## Bootstrap Stability
- Paths: 1000
- P05 Accuracy: 1.0
- P50 Accuracy: 1.0
- P95 Accuracy: 1.0

## Stress Scenarios
| scenario | accuracy | f1 | brier | logloss |
|---|---:|---:|---:|---:|
| base | 1.0000 | 1.0000 | 0.0934 | 0.3419 |
| confidence_drop | 0.9167 | 0.9091 | 0.1121 | 0.3899 |
| calibration_shift | 0.9167 | 0.9091 | 0.0972 | 0.3460 |
| combined_stress | 0.8333 | 0.8000 | 0.1282 | 0.4089 |

## Reproducibility
- Manifest: `D:\qt\secure-eval-wrapper\delivery\generic-demo\generic_manifest.json`
- Fixed seed + input hash + code hash.