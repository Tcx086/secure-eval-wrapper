from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _load(path: str) -> List[Dict[str, float]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("Input must be a JSON list")
    return data


def _binary_metrics(rows: List[Dict[str, float]], threshold: float = 0.5) -> Dict[str, float]:
    if not rows:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "brier": 0.0, "logloss": 0.0}

    tp = fp = tn = fn = 0
    brier_sum = 0.0
    logloss_sum = 0.0

    for r in rows:
        y = int(r["label"])
        p = _clamp(float(r["score"]), 1e-8, 1.0 - 1e-8)
        yhat = 1 if p >= threshold else 0

        if y == 1 and yhat == 1:
            tp += 1
        elif y == 0 and yhat == 1:
            fp += 1
        elif y == 0 and yhat == 0:
            tn += 1
        else:
            fn += 1

        brier_sum += (p - y) ** 2
        logloss_sum += -(y * math.log(p) + (1 - y) * math.log(1 - p))

    n = len(rows)
    acc = (tp + tn) / n
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 0.0 if (prec + rec) == 0 else 2.0 * prec * rec / (prec + rec)

    return {
        "accuracy": round(acc, 6),
        "precision": round(prec, 6),
        "recall": round(rec, 6),
        "f1": round(f1, 6),
        "brier": round(brier_sum / n, 6),
        "logloss": round(logloss_sum / n, 6),
    }


def _bootstrap_accuracy(rows: List[Dict[str, float]], paths: int, seed: int) -> Dict[str, float]:
    rng = random.Random(seed)
    accs = []
    n = len(rows)
    if n == 0:
        return {"paths": paths, "p05_accuracy": 0.0, "p50_accuracy": 0.0, "p95_accuracy": 0.0}

    for _ in range(paths):
        sampled = [rows[rng.randrange(0, n)] for _ in range(n)]
        accs.append(_binary_metrics(sampled)["accuracy"])

    accs.sort()

    def pct(p: float) -> float:
        idx = int((len(accs) - 1) * p)
        return round(accs[idx], 6)

    return {
        "paths": paths,
        "p05_accuracy": pct(0.05),
        "p50_accuracy": pct(0.50),
        "p95_accuracy": pct(0.95),
    }


def _stress(rows: List[Dict[str, float]]) -> List[Dict[str, float]]:
    scenarios = [
        {"name": "base", "scale": 1.00, "shift": 0.00},
        {"name": "confidence_drop", "scale": 0.85, "shift": 0.00},
        {"name": "calibration_shift", "scale": 1.00, "shift": -0.08},
        {"name": "combined_stress", "scale": 0.85, "shift": -0.08},
    ]
    out = []
    for s in scenarios:
        adjusted = []
        for r in rows:
            p = _clamp(float(r["score"]) * s["scale"] + s["shift"], 0.0, 1.0)
            adjusted.append({"label": int(r["label"]), "score": p})
        m = _binary_metrics(adjusted)
        out.append({"scenario": s["name"], **m})
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            c = f.read(8192)
            if not c:
                break
            h.update(c)
    return h.hexdigest()


def _report(base: Dict[str, float], boot: Dict[str, float], stress_rows: List[Dict[str, float]], manifest_path: str) -> str:
    lines = [
        "# Generic Evaluation Report (Non-Quant Demo)",
        "",
        "## Core Metrics",
        f"- Accuracy: {base['accuracy']}",
        f"- Precision: {base['precision']}",
        f"- Recall: {base['recall']}",
        f"- F1: {base['f1']}",
        f"- Brier: {base['brier']}",
        f"- LogLoss: {base['logloss']}",
        "",
        "## Bootstrap Stability",
        f"- Paths: {boot['paths']}",
        f"- P05 Accuracy: {boot['p05_accuracy']}",
        f"- P50 Accuracy: {boot['p50_accuracy']}",
        f"- P95 Accuracy: {boot['p95_accuracy']}",
        "",
        "## Stress Scenarios",
        "| scenario | accuracy | f1 | brier | logloss |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in stress_rows:
        lines.append(f"| {r['scenario']} | {r['accuracy']:.4f} | {r['f1']:.4f} | {r['brier']:.4f} | {r['logloss']:.4f} |")
    lines.extend([
        "",
        "## Reproducibility",
        f"- Manifest: `{manifest_path}`",
        "- Fixed seed + input hash + code hash.",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic deterministic evaluator")
    parser.add_argument("--input", required=True, help="Path to JSON list with fields: id,label,score")
    parser.add_argument("--out-dir", default="../delivery/generic-demo", help="Output folder")
    parser.add_argument("--seed", type=int, default=20260325, help="Seed")
    parser.add_argument("--bootstrap-paths", type=int, default=1000, help="Bootstrap paths")
    args = parser.parse_args()

    rows = _load(args.input)
    base = _binary_metrics(rows)
    boot = _bootstrap_accuracy(rows, paths=args.bootstrap_paths, seed=args.seed)
    stress_rows = _stress(rows)

    project_root = Path(__file__).resolve().parents[1]
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    input_path = Path(args.input).resolve()
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_file": str(input_path),
        "input_sha256": _sha256(input_path),
        "seed": args.seed,
        "bootstrap_paths": args.bootstrap_paths,
        "code_sha256": _sha256(Path(__file__).resolve()),
    }

    manifest_path = out / "generic_manifest.json"
    metrics_path = out / "generic_metrics.json"
    report_path = out / "generic_report.md"

    metrics = {"core": base, "bootstrap": boot, "stress": stress_rows}

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(_report(base, boot, stress_rows, str(manifest_path)), encoding="utf-8")

    print(json.dumps({"output_dir": str(out), "files": [str(manifest_path), str(metrics_path), str(report_path)]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
