# Secure Eval Wrapper

## English

### What This Is
A secure evaluation framework that demonstrates how to ship reproducible model/strategy evaluation systems **without exposing proprietary logic**.

### What Problem It Solves
Build a production-style evaluation workflow that is:
- reproducible,
- auditable,
- and safe to present publicly.

### Why This Is Hard
- Config, data, and code drift can silently break reproducibility.
- Evaluation claims are fragile without standardized stress/stability checks.
- Promotion decisions are unreliable without deterministic artifacts and audit trails.

### Engineering Challenges Solved
- Deterministic reproducibility with fixed seed + input/config/code hashes.
- Unified risk suite with Monte Carlo, stress windows, and intrabar probes.
- Promotion-grade artifact packaging for review and sim/live gating.
- Public/private isolation with contract-level integration points.

### Top 3 Engineering Strengths
1. Deterministic reproducibility via seed + input/config/code hashes.
2. Sealed private strategy boundary with public contract-only interfaces.
3. Automated risk/evaluation artifact packaging for promotion to sim/live.

### System Architecture
```mermaid
flowchart LR
  A["Strategy Adapter<br/>(public contract)"] --> B["Signal/Scoring Layer"]
  B --> C[Evaluation Engine]
  C --> D["Risk Suite<br/>MC / Stress / Intrabar"]
  D --> E["Repro Manifest<br/>(hash + seed + config)"]
  E --> F[Artifact Packager]
  F --> G[Delivery Bundle]

  P["Private Strategy Code<br/>local-only"] -. injected locally .-> A
```

### Quick Start
From `open-core`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

This command runs demo signal + evaluation + zip packaging.

### Non-Quant Generic Demo
A second demo is included to show framework capability beyond trading domain.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_generic_demo.ps1
```

Outputs in `delivery/generic-demo`:
- `generic_manifest.json`
- `generic_metrics.json`
- `generic_report.md`

### Public vs Private Boundary
Public:
- framework contracts and demo implementation
- evaluation methodology and reproducibility artifacts

Private:
- real strategy internals (`real_test_v5/v6` logic)
- private features, thresholds, and operational secrets

### Current Public Results (Sanitized)
#### v5 Standalone (Math/Stat Edge, v3+v4)
| Metric | Value |
|---|---:|
| Public Metric Cost Basis | `16 bps` |
| Annualized Return | `19.15%` |
| Max Drawdown | `10.98%` |
| Sharpe | `0.9376` |
| Win Rate | `34.75%` |
| Monte Carlo CAGR P50 | `12.18%` |
| Stress Test Worst-Case Return | `-23.36%` |

#### v6 Standalone (News-Driven Edge)
| Metric | Value |
|---|---:|
| Cost Assumption | `22 bps` |
| Annualized Return | `40.55%` |
| Max Drawdown | `-26.78%` |
| Sharpe | `1.3789` |
| Survivability Conclusion @22 bps | `Yes` |

### Deep-Dive Engineering Notes
- See `ENGINEERING_HARD_PARTS.md` for deterministic design, boundary control, and promotion gate details.

### Repo Map
- `open-core/`: public framework + demos
- `delivery/`: generated artifacts, templates, runbook
- `security/`: baseline controls
- `api-spec/`: interface stub
- `private/`: local-only integration notes

---

## 中文

### 这是什么
这是一个“安全评估框架”示例仓库：
目标是在**不暴露私有策略逻辑**的前提下，展示可复现、可审计的评估系统工程能力。

### 解决的问题
构建一套可用于生产门禁思路的评估工作流，要求同时满足：
- 可复现
- 可审计
- 可公开展示

### 难点在哪里
- 配置/数据/代码轻微漂移就会破坏复现。
- 没有压力测试与稳定性检查，结果说服力不足。
- 缺少标准化产物与审计链路时，升级决策不稳定。

### 已解决的工程难点
- 通过 seed + 输入/配置/代码哈希实现确定性复现。
- 建立统一风险套件（Monte Carlo / stress / intrabar）。
- 形成可用于评审与门禁的标准化产物打包流程。
- 通过契约层集成实现公开框架与私有逻辑隔离。

### 3 个最能打的工程点
1. seed + 输入/配置/代码哈希，保证确定性复现。
2. 私有策略边界隔离，只公开接口契约与 demo。
3. 评估与风险产物自动打包，支撑上线前门禁流程。

### 架构图
```mermaid
flowchart LR
  A["策略适配层<br/>公开契约"] --> B["信号/评分层"]
  B --> C[评估引擎]
  C --> D["风险套件<br/>MC/Stress/Intrabar"]
  D --> E["复现清单<br/>哈希+seed+配置"]
  E --> F[产物打包]
  F --> G[交付包]

  P["私有策略代码<br/>本地保留"] -. 本地注入 .-> A
```

### 一键运行
在 `open-core` 下执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

执行 signal demo + 评估 + zip 打包。

### 非量化通用 Demo
为了证明框架能力不局限于量化场景，额外提供通用评估 demo：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_generic_demo.ps1
```

输出目录 `delivery/generic-demo`：
- `generic_manifest.json`
- `generic_metrics.json`
- `generic_report.md`

### 公私边界
公开：
- 框架契约与 demo 实现
- 评估方法与复现产物

私有：
- `real_test_v5/v6` 真实策略实现
- 私有特征、阈值、参数与密钥

### 当前公开结果（脱敏）
#### v5 独立结果（数学/统计 Edge，v3+v4）
| 指标 | 数值 |
|---|---:|
| 公开指标成本口径 | `16 bps` |
| 年化收益 | `19.15%` |
| 最大回撤 | `10.98%` |
| Sharpe | `0.9376` |
| 胜率 | `34.75%` |
| Monte Carlo CAGR P50 | `12.18%` |
| 压力测试最差场景收益 | `-23.36%` |

#### v6 独立结果（新闻驱动 Edge）
| 指标 | 数值 |
|---|---:|
| 成本假设 | `22 bps` |
| 年化收益 | `40.55%` |
| 最大回撤 | `-26.78%` |
| Sharpe | `1.3789` |
| 生存性结论（22 bps） | `是` |

### 工程难点说明
详见 `ENGINEERING_HARD_PARTS.md`（确定性设计、边界隔离、上线门禁流程）。

### 仓库结构
- `open-core/`：公开框架与 demo
- `delivery/`：产物、模板、runbook
- `security/`：安全基线
- `api-spec/`：接口草案
- `private/`：本地私有接入说明
