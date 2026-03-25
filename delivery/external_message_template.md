# External Message Template

## 中文版（可直接发）
你好，以下是我这套交易研究系统的公开演示包（不含私有策略源码）：

1. `evaluation_report.md`：决策输出、Monte Carlo、压力测试、intrabar 结果  
2. `repro_manifest.json`：输入快照哈希、配置哈希、代码哈希（用于复现）  
3. `model_card_public.md`：模型与流程的公开拆解（高层，不泄露 edge）  
4. `evaluation_metrics.json`：结构化指标数据，便于你们二次验证  

说明边界：
- 公开部分：框架能力、评估方法、稳定性证据、复现流程
- 私有部分：真实策略逻辑、关键特征工程、阈值/权重、训练细节

这套材料的目标是证明：
- 不是随机试错，而是可复现、可审计的迭代流程
- 能清楚定位 edge 所在模块，但不暴露核心实现细节

## English Version
Hi, please find the public demo package for my trading research system (without proprietary strategy source code):

1. `evaluation_report.md`: decision output, Monte Carlo, stress tests, and intrabar analysis  
2. `repro_manifest.json`: input/config/code hashes for reproducibility  
3. `model_card_public.md`: high-level model/process breakdown (no edge disclosure)  
4. `evaluation_metrics.json`: structured metrics for independent verification  

Disclosure boundary:
- Public: framework capability, evaluation methodology, robustness evidence, reproducibility workflow
- Private: real strategy logic, critical feature engineering, thresholds/weights, training details

This package is designed to demonstrate:
- disciplined and reproducible iteration (not random trial-and-error)
- clear understanding of where edge is generated, without exposing proprietary implementation

