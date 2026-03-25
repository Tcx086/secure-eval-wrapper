# Secure Eval Wrapper

## English

### Overview
Secure Eval Wrapper is a public demonstration framework for:
- reproducible strategy evaluation,
- risk/stability diagnostics,
- and clean confidentiality boundaries.

This repository is designed to show engineering quality and research discipline without exposing proprietary strategy code (edge).

### What Is Public vs Private
Public in this repo:
- `open-core/` (framework + demo strategy)
- reproducible evaluation pipeline (Monte Carlo, stress tests, intrabar probe)
- delivery artifacts and methodology

Private (never published):
- real strategy logic (`real_test_v5/v6` internals)
- proprietary feature engineering, weights, thresholds
- private credentials and operational secrets

### One-Click Run (Recommended)
From `open-core/`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

This single command will:
1. run a signal demo
2. generate reproducible evaluation artifacts
3. package a delivery zip

Output:
- folder: `delivery/demo-run/`
- zip: `delivery/demo-run.zip`

### Reproducibility Evidence
Generated files:
- `delivery/demo-run/repro_manifest.json`
- `delivery/demo-run/evaluation_report.md`
- `delivery/demo-run/evaluation_metrics.json`
- `delivery/demo-run/model_card_public.md`
- `delivery/demo-run/signal_output.json`

`repro_manifest.json` includes:
- input snapshot hash
- config hash
- code hashes
- deterministic seed settings

### Current Demo Results (Public Sample)
Source: `delivery/demo-run/evaluation_metrics.json`

#### Signal Output
- Action: `LONG`
- Score: `0.24`
- Confidence: `0.24`

#### Monte Carlo (200 paths, 220 bars/path)
- P05 Total Return: `-0.173715`
- P50 Total Return: `0.175452`
- P95 Total Return: `0.579558`
- P95 Max Drawdown: `-0.077911`
- P50 Sharpe: `1.028002`

#### Stress Test Highlights
- Best sample case (0 bps slippage, vol x1.6): Total Return `0.5256`, Max DD `-0.2502`, Sharpe `1.3558`
- Harsh sample case (15 bps slippage, vol x1.0): Total Return `-0.1511`, Max DD `-0.2364`, Sharpe `-0.6439`

#### Intrabar Probe (280 bars)
- Stop Hit Rate: `0.385714`
- Take Profit Hit Rate: `0.371429`
- Avg Intrabar PnL: `0.000817`

### Private Edge Track Record (Sanitized: v5 vs v6)
Based on `d:/qt/real_test_v5/README.md` and `d:/qt/real_test_v6/README.md`:

- **v5** is an independent math/stat edge line (`v3 + v4`), with engineering around parallel portfolio construction, reproducibility, checksum auditing, and Monte Carlo workflow.
- **v6** is an independent news-driven edge line, with isolated architecture (signal vs execution), `sim`-first deployment policy, and risk-suite gating before runtime promotion.

Both tracks are validated under the same cost-survival assumption (`22 bps`) and are presented as separate, reproducible research lines.

This means the research process is not random iteration:
1. freeze snapshot/config
2. run backtest + risk suite
3. verify reproducibility artifacts/checksums
4. then promote to paper/sim execution

Sanitized standalone presentation format (recommended for public sharing):

#### v5 Standalone Results (Math/Stat Edge, v3+v4)
| Metric | Value (Sanitized) |
|---|---:|
| Cost Assumption | `22 bps` |
| Annualized Return | `[fill value or range]` |
| Max Drawdown | `[fill value or range]` |
| Sharpe | `[fill value or range]` |
| Win Rate | `[fill value or range]` |
| Turnover | `[fill value or range]` |
| Monte Carlo P50 Return | `[fill value or range]` |
| Stress Test Worst-Case Return | `[fill value or range]` |
| Intrabar Stability Score | `[fill value or range]` |
| Survivability Conclusion | `Survives at 22 bps: Yes/No` |

#### v6 Standalone Results (News-Driven Edge)
| Metric | Value (Sanitized) |
|---|---:|
| Cost Assumption | `22 bps` |
| Annualized Return | `[fill value or range]` |
| Max Drawdown | `[fill value or range]` |
| Sharpe | `[fill value or range]` |
| Win Rate | `[fill value or range]` |
| Turnover | `[fill value or range]` |
| Monte Carlo P50 Return | `[fill value or range]` |
| Stress Test Worst-Case Return | `[fill value or range]` |
| Intrabar Stability Score | `[fill value or range]` |
| Survivability Conclusion | `Survives at 22 bps: Yes/No` |

#### Common Survival Criteria
- Same transaction-cost assumption: `22 bps`
- Same reproducibility requirements: snapshot/config freeze + hash/checksum audit
- Same validation gates: backtest + risk suite + stress/intrabar checks

Note: publish aggregated/range metrics only; do not disclose private model parameters or factor internals.

### Important Note
These published numbers are for the **demo strategy pipeline** and public methodology demonstration.
They are not a disclosure of the proprietary real strategy implementation.

### Repository Structure
- `open-core/`: public framework and demo strategy
- `api-spec/`: API draft
- `security/`: baseline security notes
- `delivery/`: runbook, templates, generated demo artifacts
- `private/`: local-only integration guidance (ignored from git)

---

## 中文

### 项目说明
Secure Eval Wrapper 是一个用于公开展示的方法论框架，重点是：
- 可复现的策略评估流程
- 风险与稳定性诊断能力
- 清晰的保密边界管理

这个仓库用于展示工程能力与研究严谨性，不公开真实策略 edge。

### 公开与私有边界
公开内容：
- `open-core/`（框架 + demo 策略）
- 可复现评估流程（Monte Carlo、压力测试、intrabar）
- 对外交付材料与方法说明

私有内容（不公开）：
- `real_test_v5/v6` 真实策略实现
- 关键特征工程、权重、阈值与训练细节
- 密钥与生产环境配置

### 一键运行（推荐）
在 `open-core/` 下执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

该命令会自动完成：
1. 运行 signal demo
2. 生成评估报告与指标文件
3. 打包 zip 交付包

输出位置：
- 目录：`delivery/demo-run/`
- 压缩包：`delivery/demo-run.zip`

### 可复现证据
自动生成：
- `delivery/demo-run/repro_manifest.json`
- `delivery/demo-run/evaluation_report.md`
- `delivery/demo-run/evaluation_metrics.json`
- `delivery/demo-run/model_card_public.md`
- `delivery/demo-run/signal_output.json`

其中 `repro_manifest.json` 包含：
- 输入快照哈希
- 配置哈希
- 代码哈希
- 固定随机种子信息

### 当前 Demo 结果摘要（公开样例）
数据来源：`delivery/demo-run/evaluation_metrics.json`

#### 信号输出
- Action: `LONG`
- Score: `0.24`
- Confidence: `0.24`

#### Monte Carlo（200 路径，每条 220 bars）
- P05 Total Return: `-0.173715`
- P50 Total Return: `0.175452`
- P95 Total Return: `0.579558`
- P95 Max Drawdown: `-0.077911`
- P50 Sharpe: `1.028002`

#### 压力测试示例
- 较优场景（滑点 0bps，波动 x1.6）：Total Return `0.5256`，Max DD `-0.2502`，Sharpe `1.3558`
- 严苛场景（滑点 15bps，波动 x1.0）：Total Return `-0.1511`，Max DD `-0.2364`，Sharpe `-0.6439`

#### Intrabar 探针（280 bars）
- Stop Hit Rate: `0.385714`
- Take Profit Hit Rate: `0.371429`
- Avg Intrabar PnL: `0.000817`

### 私有 Edge 轨迹（脱敏版：v5 vs v6）
依据 `d:/qt/real_test_v5/README.md` 与 `d:/qt/real_test_v6/README.md`：

- **v5** 是一条独立的数学/统计 edge 线路（`v3 + v4`），重点在并行组合工程化、复现审计、Monte Carlo 验证。
- **v6** 是一条独立的新闻驱动 edge 线路，重点在信号层/执行层隔离、`sim` 优先、risk suite 先行。

两条线路都基于相同的成本生存假设（`22 bps`）进行验证，属于并行独立研究结果，不是同一策略的简单迭代替换。

这说明研究流程是可审计的，而非随机试错：
1. 冻结 snapshot/config
2. 执行 backtest + risk suite
3. 校验复现证据（含 checksum/hash）
4. 再进入 paper/sim 执行层

建议公开展示采用“各自独立结果表”：

#### v5 独立结果（数学/统计 Edge，v3+v4）
| 指标 | 数值（脱敏） |
|---|---:|
| 成本假设 | `22 bps` |
| 年化收益 | `[填写具体值或区间]` |
| 最大回撤 | `[填写具体值或区间]` |
| Sharpe | `[填写具体值或区间]` |
| 胜率 | `[填写具体值或区间]` |
| 换手率 | `[填写具体值或区间]` |
| Monte Carlo P50 收益 | `[填写具体值或区间]` |
| 压力测试最差场景收益 | `[填写具体值或区间]` |
| Intrabar 稳定性评分 | `[填写具体值或区间]` |
| 生存性结论 | `22 bps 下可生存：是/否` |

#### v6 独立结果（新闻驱动 Edge）
| 指标 | 数值（脱敏） |
|---|---:|
| 成本假设 | `22 bps` |
| 年化收益 | `[填写具体值或区间]` |
| 最大回撤 | `[填写具体值或区间]` |
| Sharpe | `[填写具体值或区间]` |
| 胜率 | `[填写具体值或区间]` |
| 换手率 | `[填写具体值或区间]` |
| Monte Carlo P50 收益 | `[填写具体值或区间]` |
| 压力测试最差场景收益 | `[填写具体值或区间]` |
| Intrabar 稳定性评分 | `[填写具体值或区间]` |
| 生存性结论 | `22 bps 下可生存：是/否` |

#### 统一生存标准
- 相同交易成本假设：`22 bps`
- 相同复现要求：snapshot/config 冻结 + hash/checksum 审计
- 相同验证流程：backtest + risk suite + stress/intrabar 检查

说明：公开时建议只给聚合值/区间，不公开参数、阈值、因子内部实现。

### 重要说明
以上数字用于展示 **公开 demo 策略流程** 与评估方法。
不代表真实私有策略实现细节的公开。

### 目录结构
- `open-core/`：公开框架与 demo 策略
- `api-spec/`：接口草案
- `security/`：安全基线说明
- `delivery/`：runbook、模板、演示产物
- `private/`：本地私有接入说明（已被 git 忽略）
