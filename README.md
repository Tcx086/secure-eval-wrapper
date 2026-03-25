# Secure Eval Wrapper

本仓库用于实现：
1. 开源系统框架（open-core）
2. 假策略演示（demo strategy）
3. 真实策略私有保留（private/real_strategy_v5_v6，不纳入版本控制）

## 目录
- open-core/: 可开源部分（框架 + demo策略）
- private/: 私有部分（真实策略、真实参数、密钥）
- api-spec/: 对外接口文档草案
- security/: 防泄露与访问控制说明
- delivery/: 对外交付模板

## 快速开始
```powershell
cd open-core
python -m src.cli --input data/sample/features.json --strategy demo
```

## 一键对外交付包
```powershell
cd open-core
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```
输出目录：`delivery/demo-run`  
打包文件：`delivery/demo-run.zip`

## 原则
- 对外只展示 `open-core`。
- `private` 永不公开、永不提交。
- 真实策略通过接口注入，不改变框架代码。

## 私有接入说明
- 见 `private/LOCAL_INTEGRATION.md`（本地接入 real_test_v5/v6，不泄露源码）。
