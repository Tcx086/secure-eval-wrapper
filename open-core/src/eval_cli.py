from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

from src.data.loader import load_feature_file
from src.eval.repro import build_manifest, compute_code_hashes
from src.eval.reporting import render_evaluation_report, render_model_card_public
from src.eval.simulations import build_base_returns, intrabar_probe, monte_carlo, stress_test
from src.registry import get_strategy


def run_eval(
    input_path: str,
    strategy_name: str,
    out_dir: str,
    seed: int,
    bars: int,
    mc_paths: int,
    mc_path_len: int,
) -> Dict[str, object]:
    features = load_feature_file(input_path)
    strategy = get_strategy(strategy_name)
    signal_obj = strategy.generate_signal(features)
    signal = {
        "action": signal_obj.action,
        "score": signal_obj.score,
        "confidence": signal_obj.confidence,
        "meta": signal_obj.meta,
    }

    base_returns = build_base_returns(
        score=float(signal_obj.score),
        confidence=float(signal_obj.confidence),
        bars=bars,
        seed=seed,
    )

    mc = monte_carlo(
        base_returns=base_returns,
        n_paths=mc_paths,
        path_len=mc_path_len,
        seed=seed + 7,
        fee_bps=2.0,
        slippage_bps=3.0,
    )
    stress = stress_test(
        base_returns=base_returns,
        slippage_bps_grid=[0.0, 3.0, 8.0, 15.0],
        fee_bps=2.0,
        volatility_mult_grid=[1.0, 1.3, 1.6],
    )
    intrabar = intrabar_probe(
        action=signal_obj.action,
        base_returns=base_returns,
        seed=seed + 13,
        stop_loss_pct=0.004,
        take_profit_pct=0.006,
        steps_per_bar=12,
    )

    project_root = Path(__file__).resolve().parents[1]
    code_hashes = compute_code_hashes(
        project_root=project_root,
        rel_paths=[
            "src/core/strategy_base.py",
            "src/strategies/demo_strategy.py",
            "src/eval/simulations.py",
            "src/eval/metrics.py",
            "src/eval/repro.py",
            "src/eval/reporting.py",
            "src/eval_cli.py",
        ],
    )

    config = {
        "seed": seed,
        "bars": bars,
        "mc_paths": mc_paths,
        "mc_path_len": mc_path_len,
        "fee_bps": 2.0,
        "slippage_bps": 3.0,
        "stress_slippage_bps_grid": [0.0, 3.0, 8.0, 15.0],
        "stress_volatility_mult_grid": [1.0, 1.3, 1.6],
        "intrabar_stop_loss_pct": 0.004,
        "intrabar_take_profit_pct": 0.006,
        "intrabar_steps_per_bar": 12,
    }
    manifest = build_manifest(
        strategy=strategy_name,
        input_path=Path(input_path).resolve(),
        config=config,
        code_hashes=code_hashes,
    )

    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    manifest_path = out / "repro_manifest.json"
    report_path = out / "evaluation_report.md"
    model_card_path = out / "model_card_public.md"
    signal_path = out / "signal_output.json"
    metrics_path = out / "evaluation_metrics.json"

    metrics = {
        "signal": signal,
        "monte_carlo": mc,
        "stress_test": stress,
        "intrabar": intrabar,
    }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(
        render_evaluation_report(
            strategy=strategy_name,
            signal=signal,
            monte_carlo_result=mc,
            intrabar_result=intrabar,
            stress_rows=stress,
            manifest_path=str(manifest_path),
        ),
        encoding="utf-8",
    )
    model_card_path.write_text(render_model_card_public(), encoding="utf-8")
    signal_path.write_text(json.dumps(signal, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "output_dir": str(out),
        "files": [
            str(manifest_path),
            str(report_path),
            str(model_card_path),
            str(signal_path),
            str(metrics_path),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reproducible public evaluation artifacts")
    parser.add_argument("--input", required=True, help="Path to feature JSON")
    parser.add_argument("--strategy", default="demo", help="Strategy name")
    parser.add_argument("--out-dir", default="../delivery/demo-run", help="Output directory")
    parser.add_argument("--seed", type=int, default=20260325, help="Deterministic seed")
    parser.add_argument("--bars", type=int, default=280, help="Synthetic bars for evaluation")
    parser.add_argument("--mc-paths", type=int, default=200, help="Monte Carlo path count")
    parser.add_argument("--mc-path-len", type=int, default=220, help="Bars per Monte Carlo path")
    args = parser.parse_args()

    result = run_eval(
        input_path=args.input,
        strategy_name=args.strategy,
        out_dir=args.out_dir,
        seed=args.seed,
        bars=args.bars,
        mc_paths=args.mc_paths,
        mc_path_len=args.mc_path_len,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

