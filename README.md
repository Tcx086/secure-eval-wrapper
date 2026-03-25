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

- **v5** emphasizes parallel portfolio engineering (`v3 + v4`), risk-budget allocation (`0.8/0.2`), one-click reproducibility, checksum auditing, and Monte Carlo workflow.
- **v6** emphasizes architecture hardening for isolated operation (signal layer vs execution layer), `sim`-first deployment policy, shared policy for backtest/live consistency, and risk-suite gating before runtime promotion.

This means the edge evolution is not random iteration:
1. freeze snapshot/config
2. run backtest + risk suite
3. verify reproducibility artifacts/checksums
4. then promote to paper/sim execution

Sanitized performance presentation format (recommended for public sharing):

| Metric | v5 (Sanitized) | v6 (Sanitized) | Delta (v6-v5) |
|---|---:|---:|---:|
| Annualized Return | `[base]` | `[base + x%]` | `+x% to +y%` |
| Max Drawdown | `[base]` | `[improved]` | `-(x to y) pp` |
| Sharpe | `[base]` | `[base + x]` | `+x to +y` |
| Win Rate | `[base]` | `[base + x pp]` | `+x to +y pp` |
| Turnover | `[base]` | `[lower/higher]` | `-(x to y)%` or `+(x to y)%` |
| Monte Carlo P50 Return | `[base]` | `[base + x%]` | `+x% to +y%` |
| Stress Test Worst-Case Return | `[base]` | `[less negative]` | `+x% to +y%` |
| Intrabar Stability Score | `[base]` | `[base + x]` | `+x to +y` |

Note: publish only aggregated/normalized metrics (or percentage deltas), not private model parameters or factor internals.

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

- **v5** 侧重并行组合工程化（`v3 + v4`）、风险预算分配（`0.8/0.2`）、一键复现、checksum 审计与 Monte Carlo 流程。
- **v6** 侧重隔离式架构强化（信号层与执行层分离）、`sim` 优先部署原则、回测/实盘共用 policy、一致性与 risk suite 先行。

这说明 edge 迭代是可审计流程，而非随机试错：
1. 冻结 snapshot/config
2. 执行 backtest + risk suite
3. 校验复现证据（含 checksum/hash）
4. 再进入 paper/sim 执行层

建议公开展示采用脱敏聚合表：

| 指标 | v5（脱敏） | v6（脱敏） | 变化（v6-v5） |
|---|---:|---:|---:|
| 年化收益 | `[基准]` | `[基准 + x%]` | `+x% ~ +y%` |
| 最大回撤 | `[基准]` | `[改善后]` | `-(x ~ y) 个百分点` |
| Sharpe | `[基准]` | `[基准 + x]` | `+x ~ +y` |
| 胜率 | `[基准]` | `[基准 + x 个百分点]` | `+x ~ +y 个百分点` |
| 换手率 | `[基准]` | `[更低/更高]` | `-(x ~ y)%` 或 `+(x ~ y)%` |
| Monte Carlo P50 收益 | `[基准]` | `[基准 + x%]` | `+x% ~ +y%` |
| 压力测试最差场景收益 | `[基准]` | `[更不负]` | `+x% ~ +y%` |
| Intrabar 稳定性评分 | `[基准]` | `[基准 + x]` | `+x ~ +y` |

说明：公开时建议只给聚合指标/百分比变化，不公开参数、阈值、因子内部实现。

### 重要说明
以上数字用于展示 **公开 demo 策略流程** 与评估方法。
不代表真实私有策略实现细节的公开。

### 目录结构
- `open-core/`：公开框架与 demo 策略
- `api-spec/`：接口草案
- `security/`：安全基线说明
- `delivery/`：runbook、模板、演示产物
- `private/`：本地私有接入说明（已被 git 忽略）
