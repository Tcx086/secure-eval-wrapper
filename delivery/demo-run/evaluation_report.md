# Evaluation Report (Public Demo)

## 1) Decision Output
- Strategy: `demo`
- Action: `LONG`
- Score: `0.24`
- Confidence: `0.24`
- Metadata: `{'strategy': 'demo', 'is_demo': True}`

## 2) Monte Carlo Summary
- Paths: 200
- Bars per Path: 220
- P05 Total Return: -0.173715
- P50 Total Return: 0.175452
- P95 Total Return: 0.579558
- P95 Max Drawdown: -0.077911
- P50 Sharpe: 1.028002

## 3) Stress Test Matrix
| slippage_bps | volatility_mult | total_return | max_drawdown | sharpe |
|---:|---:|---:|---:|---:|
| 0 | 1.00 | 0.2919 | -0.1655 | 1.261 |
| 0 | 1.30 | 0.4066 | -0.2087 | 1.319 |
| 0 | 1.60 | 0.5256 | -0.2502 | 1.356 |
| 3 | 1.00 | 0.1879 | -0.1783 | 0.880 |
| 3 | 1.30 | 0.2934 | -0.2207 | 1.026 |
| 3 | 1.60 | 0.4028 | -0.2616 | 1.118 |
| 8 | 1.00 | 0.0327 | -0.2024 | 0.245 |
| 8 | 1.30 | 0.1245 | -0.2426 | 0.538 |
| 8 | 1.60 | 0.2197 | -0.2816 | 0.721 |
| 15 | 1.00 | -0.1511 | -0.2364 | -0.644 |
| 15 | 1.30 | -0.0756 | -0.2746 | -0.146 |
| 15 | 1.60 | 0.0026 | -0.3118 | 0.165 |

## 4) Intrabar Probe
- Bars Simulated: 280
- Stop Hit Rate: 0.385714
- Take Profit Hit Rate: 0.371429
- Avg Intrabar PnL: 0.000817

## 5) Reproducibility
- Manifest: `D:\qt\secure-eval-wrapper\delivery\demo-run\repro_manifest.json`
- Method: fixed seed + input snapshot hash + config hash + code hashes.
- Interpretation: this demonstrates controlled iteration and reproducibility, not random trial-and-error.

## 6) Confidentiality Boundary
- Public: framework, evaluation methodology, risk/stability evidence.
- Private: real strategy logic, features, thresholds, weights, and training details.
