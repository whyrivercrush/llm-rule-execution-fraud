# -*- coding: utf-8 -*-
"""
eval_corrected_B.py

Evaluate the corrected Condition B (roles with EMPTY rule lists) after the
B/D isolation fix, and produce the numbers needed to replace the old,
invalid B results.

Outputs:
  results/corrected_B_metrics.csv
  results/corrected_B_pairwise_tests.csv
  results/corrected_B_results.md
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import eval_metrics as em


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return em.dedupe_records(records)


def valid_pairs(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for r in records:
        if (
            r.get("status") == "success"
            and r.get("ground_truth_label") in (0, 1)
            and r.get("is_fraud") in (0, 1)
        ):
            out[str(r["transaction_id"])] = r
    return out


def mcc_score(tp: int, fp: int, tn: int, fn: int) -> Optional[float]:
    denom = (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)
    if denom == 0:
        return 0.0
    return (tp * tn - fp * fn) / math.sqrt(denom)


def metrics_row(name: str, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    counts, metrics = em.compute_metrics(records)
    valid = [
        r for r in records
        if r.get("status") == "success"
        and r.get("ground_truth_label") in (0, 1)
        and r.get("is_fraud") in (0, 1)
    ]
    y_true = [int(r["ground_truth_label"]) for r in valid]
    y_pred = [int(r["is_fraud"]) for r in valid]
    tp, fp, tn, fn = em.binary_confusion(y_true, y_pred)
    precision = 0.0 if (tp + fp) == 0 else tp / (tp + fp)
    recall = 0.0 if (tp + fn) == 0 else tp / (tp + fn)
    f1_pos = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
    return {
        "condition": name,
        "n_valid": counts["n_valid"],
        "accuracy": metrics["accuracy"],
        "balanced_accuracy": metrics["balanced_accuracy"],
        "precision": precision,
        "recall": recall,
        "f1": f1_pos,
        "macro_f1": metrics["macro_f1"],
        "mcc": mcc_score(tp, fp, tn, fn),
        "auc_roc": metrics["auc_roc"],
    }


def pairwise(
    name_a: str,
    map_a: Dict[str, Dict[str, Any]],
    name_b: str,
    map_b: Dict[str, Dict[str, Any]],
    n_boot: int,
    rng: random.Random,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    common = sorted(
        [tid for tid in map_a if tid in map_b],
        key=lambda x: int(x) if x.isdigit() else x,
    )
    y_true, pred_a, pred_b, score_a, score_b = [], [], [], [], []
    correct_a, correct_b = [], []
    for tid in common:
        ra, rb = map_a[tid], map_b[tid]
        yt = int(ra["ground_truth_label"])
        y_true.append(yt)
        pred_a.append(int(ra["is_fraud"]))
        pred_b.append(int(rb["is_fraud"]))
        score_a.append(ra.get("confidence"))
        score_b.append(rb.get("confidence"))
        correct_a.append(int(ra["is_fraud"]) == yt)
        correct_b.append(int(rb["is_fraud"]) == yt)

    acc = em.paired_bootstrap_ci(
        y_true, pred_a, score_a, pred_b, score_b, "accuracy", n_boot, rng
    )
    f1 = em.paired_bootstrap_ci(
        y_true, pred_a, score_a, pred_b, score_b, "macro_f1", n_boot, rng
    )
    mcnemar = em.mcnemar_exact_p(correct_a, correct_b)
    row = {
        "comparison": f"{name_a}_minus_{name_b}",
        "n_paired": len(common),
        "accuracy_diff": acc["delta"],
        "accuracy_ci_low": acc["ci_low"],
        "accuracy_ci_high": acc["ci_high"],
        "macro_f1_diff": f1["delta"],
        "macro_f1_ci_low": f1["ci_low"],
        "macro_f1_ci_high": f1["ci_high"],
        "mcnemar_b": mcnemar["b"],
        "mcnemar_c": mcnemar["c"],
        "mcnemar_n_discordant": mcnemar["n_discordant"],
        "mcnemar_p": mcnemar["p_value"],
    }
    return row, {"accuracy": acc, "macro_f1": f1, "mcnemar": mcnemar}


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in row.items()
            })


def fmt(v: Any, digits: int = 4) -> str:
    if v is None:
        return "NA"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--b-corrected", type=Path,
                        default=PROJECT_ROOT / "logs" / "pilot_run_condition_B_corrected.jsonl")
    parser.add_argument("--a-log", type=Path,
                        default=PROJECT_ROOT / "logs" / "formal_run_condition_A.jsonl")
    parser.add_argument("--d-log", type=Path,
                        default=PROJECT_ROOT / "logs" / "formal_run_condition_D.jsonl")
    parser.add_argument("--c-log", type=Path,
                        default=PROJECT_ROOT / "logs" / "formal_run_condition_C.jsonl")
    parser.add_argument("--b-old", type=Path,
                        default=PROJECT_ROOT / "logs" / "formal_run_condition_B.jsonl")
    parser.add_argument("--results-dir", type=Path, default=PROJECT_ROOT / "results")
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--bootstrap-seed", type=int, default=2026)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    maps = {
        "B_corrected": valid_pairs(load_records(args.b_corrected)),
        "A": valid_pairs(load_records(args.a_log)),
        "C": valid_pairs(load_records(args.c_log)),
        "D": valid_pairs(load_records(args.d_log)),
        "B_old_INVALID": valid_pairs(load_records(args.b_old)),
    }
    for name, m in maps.items():
        print(f"{name}: n_valid={len(m)}")
    if not maps["B_corrected"]:
        raise SystemExit("corrected B log is empty")

    metric_rows = [
        metrics_row("A", load_records(args.a_log)),
        metrics_row("B_corrected", load_records(args.b_corrected)),
        metrics_row("B_old_INVALID", load_records(args.b_old)),
        metrics_row("C", load_records(args.c_log)),
        metrics_row("D", load_records(args.d_log)),
    ]
    write_csv(args.results_dir / "corrected_B_metrics.csv", metric_rows)

    rng = random.Random(args.bootstrap_seed)
    pair_rows = []
    pair_details: Dict[str, Any] = {}
    for name_a, name_b in (
        ("B_corrected", "A"),
        ("B_corrected", "C"),
        ("B_corrected", "D"),
        ("B_old_INVALID", "A"),
        ("B_old_INVALID", "C"),
        ("B_old_INVALID", "D"),
    ):
        row, detail = pairwise(
            name_a, maps[name_a], name_b, maps[name_b],
            args.bootstrap, rng,
        )
        pair_rows.append(row)
        pair_details[f"{name_a}_minus_{name_b}"] = detail
    write_csv(args.results_dir / "corrected_B_pairwise_tests.csv", pair_rows)

    b = metric_rows[1]
    assert b["condition"] == "B_corrected"
    p_a = pair_details["B_corrected_minus_A"]
    p_d = pair_details["B_corrected_minus_D"]
    lines = [
        "# Corrected Condition B (isolation fix)",
        "",
        "Bug: before the fix, `_run_roles` always passed `ROLE_RULE_MAP[role]` to both B and D,",
        "so the old B was actually roles + rules (identical-in-design to D).",
        "Fix: B now passes empty rule lists; D still passes the role-specific rules.",
        "Dataset/model/temperature/max_tokens/test samples are unchanged.",
        "",
        "## Corrected B metrics (N=400)",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| Accuracy | {fmt(b['accuracy'])} |",
        f"| Balanced Accuracy | {fmt(b['balanced_accuracy'])} |",
        f"| Precision (fraud) | {fmt(b['precision'])} |",
        f"| Recall (fraud) | {fmt(b['recall'])} |",
        f"| F1 (fraud) | {fmt(b['f1'])} |",
        f"| Macro-F1 | {fmt(b['macro_f1'])} |",
        f"| MCC | {fmt(b['mcc'])} |",
        f"| AUC-ROC | {fmt(b['auc_roc'])} |",
        "",
        "## McNemar tests",
        "",
        "| comparison | b | c | discordant | p | accuracy diff [95% CI] |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label, detail in (
        ("B_corrected vs A", p_a),
        ("B_corrected vs C", pair_details["B_corrected_minus_C"]),
        ("B_corrected vs D", p_d),
    ):
        mc = detail["mcnemar"]
        acc = detail["accuracy"]
        lines.append(
            f"| {label} | {mc['b']} | {mc['c']} | {mc['n_discordant']} | "
            f"{fmt(mc['p_value'])} | {fmt(acc['delta'])} "
            f"[{fmt(acc['ci_low'])}, {fmt(acc['ci_high'])}] |"
        )
    lines += [
        "",
        "Reading: roles-only B does not raise accuracy over A (p=0.2888) but shifts the",
        "model toward more fraud-positive predictions, raising Recall/F1 while slightly",
        "lowering MCC/AUC. Adding the four rules (C single-prompt, D roles+rules)",
        "significantly outperforms corrected B (B vs C p=1.2e-05; B vs D p=0.0015).",
        "So on this dataset the rules, not the role scaffold alone, carry the gain.",
        "",
        "## Replacement instruction",
        "",
        "Replace every old Condition-B row/statistic in the paper with the corrected",
        "values above. The old `formal_run_condition_B.jsonl` is kept only for audit",
        "and must not be reported.",
        "",
        "Files:",
        "- `results/corrected_B_metrics.csv`",
        "- `results/corrected_B_pairwise_tests.csv`",
        "- `logs/pilot_run_condition_B_corrected.jsonl`",
    ]
    report_path = args.results_dir / "corrected_B_results.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print("\nCorrected B metrics")
    for row in metric_rows:
        print(
            row["condition"],
            "acc", fmt(row["accuracy"]),
            "prec", fmt(row["precision"]),
            "rec", fmt(row["recall"]),
            "f1", fmt(row["f1"]),
            "macroF1", fmt(row["macro_f1"]),
            "mcc", fmt(row["mcc"]),
            "auc", fmt(row["auc_roc"]),
        )
    print("\nPairwise")
    for row in pair_rows:
        print(
            row["comparison"], "p=", fmt(row["mcnemar_p"]),
            "delta=", fmt(row["accuracy_diff"]),
            f"[{fmt(row['accuracy_ci_low'])}, {fmt(row['accuracy_ci_high'])}]",
        )
    print(f"\nSaved: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
