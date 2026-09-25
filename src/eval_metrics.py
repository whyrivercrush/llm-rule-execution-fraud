# -*- coding: utf-8 -*-
"""
eval_metrics.py

Evaluate the four condition JSONL logs:

  * summary table: Accuracy, Balanced Accuracy, Precision, Recall, Macro-F1, AUC-ROC
  * per-sample wide detail CSV
  * paired Bootstrap (default N=1000) 95% CIs
  * pairwise McNemar tests

By default the script prefers `formal_run_condition_*.jsonl` when present,
otherwise it falls back to `pilot_run_condition_*.jsonl`. Use --log-prefix to
override. Duplicate TransactionIDs caused by resumed runs are de-duplicated,
keeping the latest successful record (or the latest record if none succeeded).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONDITION_ORDER = ["A", "B", "C", "D"]

METRIC_HEADERS = [
    "condition",
    "n_total",
    "n_valid",
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "macro_f1",
    "auc_roc",
    "n_invalid",
]


def safe_div(num: float, den: float) -> Optional[float]:
    return None if den == 0 else num / den


def binary_confusion(
    y_true: Sequence[int],
    y_pred: Sequence[int],
) -> Tuple[int, int, int, int]:
    tp = fp = tn = fn = 0
    for yt, yp in zip(y_true, y_pred):
        if yt == 1:
            tp += 1 if yp == 1 else 0
            fn += 0 if yp == 1 else 1
        else:
            fp += 1 if yp == 1 else 0
            tn += 0 if yp == 1 else 1
    return tp, fp, tn, fn


def f1_from_pr(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def macro_f1_score(tp: int, fp: int, tn: int, fn: int) -> Optional[float]:
    f1_pos = f1_from_pr(safe_div(tp, tp + fp), safe_div(tp, tp + fn))
    f1_neg = f1_from_pr(safe_div(tn, tn + fn), safe_div(tn, tn + fp))
    if f1_pos is None or f1_neg is None:
        return None
    return (f1_pos + f1_neg) / 2.0


def auc_roc(y_true: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    pairs = [(int(y), float(s)) for y, s in zip(y_true, scores)]
    pos = [s for y, s in pairs if y == 1]
    neg = [s for y, s in pairs if y == 0]
    if not pos or not neg:
        return None
    concordant = 0
    tied = 0
    for s_pos in pos:
        for s_neg in neg:
            if s_pos > s_neg:
                concordant += 1
            elif s_pos == s_neg:
                tied += 1
    total = len(pos) * len(neg)
    return (concordant + 0.5 * tied) / total if total else None


def metric_accuracy(y_true, y_pred, scores) -> Optional[float]:
    tp, fp, tn, fn = binary_confusion(y_true, y_pred)
    return safe_div(tp + tn, len(y_true))


def metric_macro_f1(y_true, y_pred, scores) -> Optional[float]:
    tp, fp, tn, fn = binary_confusion(y_true, y_pred)
    return macro_f1_score(tp, fp, tn, fn)


def metric_auc(y_true, y_pred, scores) -> Optional[float]:
    if any(s is None for s in scores):
        return None
    return auc_roc(y_true, scores)


METRIC_FUNCS = {
    "accuracy": metric_accuracy,
    "macro_f1": metric_macro_f1,
    "auc_roc": metric_auc,
}


def compute_metrics(
    records: Sequence[Dict],
) -> Tuple[Dict[str, int], Dict[str, Optional[float]]]:
    valid = [
        r for r in records
        if r.get("status") == "success"
        and r.get("ground_truth_label") in (0, 1)
        and r.get("is_fraud") in (0, 1)
    ]
    y_true = [int(r["ground_truth_label"]) for r in valid]
    y_pred = [int(r["is_fraud"]) for r in valid]

    counts = {
        "n_total": len(records),
        "n_valid": len(valid),
        "n_invalid": len(records) - len(valid),
    }
    if not valid:
        return counts, {
            "accuracy": None,
            "balanced_accuracy": None,
            "precision": None,
            "recall": None,
            "macro_f1": None,
            "auc_roc": None,
        }

    tp, fp, tn, fn = binary_confusion(y_true, y_pred)
    n_pos = tp + fn
    n_neg = tn + fp
    recall_pos = 0.0 if n_pos == 0 else tp / n_pos
    recall_neg = 0.0 if n_neg == 0 else tn / n_neg
    balanced_accuracy = (
        (recall_pos + recall_neg) / 2.0 if n_pos > 0 and n_neg > 0 else None
    )
    precision = 0.0 if (tp + fp) == 0 else tp / (tp + fp)

    auc_valid = [r for r in valid if r.get("confidence") is not None]
    auc_value = (
        auc_roc(
            [int(r["ground_truth_label"]) for r in auc_valid],
            [float(r["confidence"]) for r in auc_valid],
        )
        if auc_valid else None
    )

    return counts, {
        "accuracy": safe_div(tp + tn, len(y_true)),
        "balanced_accuracy": balanced_accuracy,
        "precision": precision,
        "recall": recall_pos,
        "macro_f1": macro_f1_score(tp, fp, tn, fn),
        "auc_roc": auc_value,
    }


def dedupe_records(records: Sequence[Dict]) -> List[Dict]:
    """Keep the latest successful record per TransactionID, else the latest."""
    order: List[str] = []
    latest_success: Dict[str, Dict] = {}
    latest_any: Dict[str, Dict] = {}
    for r in records:
        tid = str(r.get("transaction_id") or "")
        if not tid:
            continue
        if tid not in order:
            order.append(tid)
        latest_any[tid] = r
        if r.get("status") == "success":
            latest_success[tid] = r
    return [
        latest_success.get(tid, latest_any[tid])
        for tid in order
    ]


def load_condition_records(log_dir: Path, prefix: str, condition: str) -> List[Dict]:
    path = log_dir / f"{prefix}_condition_{condition}.jsonl"
    if not path.exists():
        return []
    records: List[Dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"{path.name} line {line_no} is not valid JSON: {exc}"
                ) from exc
    return dedupe_records(records)


def resolve_prefix(log_dir: Path, requested: Optional[str]) -> str:
    if requested:
        return requested
    for candidate in ("formal_run", "pilot_run"):
        if (log_dir / f"{candidate}_condition_A.jsonl").exists():
            return candidate
    return "pilot_run"


def fmt(value: Optional[float], digits: int = 4) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


def print_table(rows: Sequence[Dict], headers: Sequence[str]) -> None:
    str_rows = []
    for row in rows:
        line = []
        for key in headers:
            if key == "condition":
                line.append(str(row[key]))
            elif key in {"n_total", "n_valid", "n_invalid", "n_boot_valid"}:
                line.append(str(row[key]))
            else:
                line.append(fmt(row.get(key)))
        str_rows.append(line)
    widths = [
        max(len(h), *(len(r[i]) for r in str_rows))
        for i, h in enumerate(headers)
    ]
    sep = "-+-".join("-" * w for w in widths)
    print(sep)
    print(" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print(sep)
    for row in str_rows:
        print(" | ".join(v.ljust(widths[i]) for i, v in enumerate(row)))
    print(sep)


def write_csv(path: Path, headers: Sequence[str], rows: Sequence[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(headers))
        writer.writeheader()
        for row in rows:
            out = {}
            for key in headers:
                value = row.get(key)
                if isinstance(value, float):
                    out[key] = round(value, 6)
                else:
                    out[key] = "" if value is None else value
            writer.writerow(out)


def paired_bootstrap_ci(
    y_true: Sequence[int],
    y_pred_a: Sequence[int],
    scores_a: Sequence[Optional[float]],
    y_pred_b: Sequence[int],
    scores_b: Sequence[Optional[float]],
    metric_name: str,
    n_boot: int,
    rng: random.Random,
) -> Dict[str, Optional[float]]:
    """Paired bootstrap for metric(A) - metric(B) and each metric separately."""
    n = len(y_true)
    if n == 0:
        return {"delta": None, "ci_low": None, "ci_high": None,
                "a": None, "b": None}
    func = METRIC_FUNCS[metric_name]
    deltas: List[float] = []
    values_a: List[float] = []
    values_b: List[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        yt = [y_true[i] for i in idx]
        va = func(yt, [y_pred_a[i] for i in idx], [scores_a[i] for i in idx])
        vb = func(yt, [y_pred_b[i] for i in idx], [scores_b[i] for i in idx])
        if va is None or vb is None:
            continue
        values_a.append(va)
        values_b.append(vb)
        deltas.append(va - vb)
    if not deltas:
        return {"delta": None, "ci_low": None, "ci_high": None,
                "a": None, "b": None, "n_boot_valid": 0}
    deltas.sort()
    values_a.sort()
    values_b.sort()

    def pct(sorted_values: List[float], q: float) -> float:
        idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
        return sorted_values[idx]

    return {
        "delta": sum(deltas) / len(deltas),
        "ci_low": pct(deltas, 0.025),
        "ci_high": pct(deltas, 0.975),
        "a": sum(values_a) / len(values_a),
        "b": sum(values_b) / len(values_b),
        "n_boot_valid": len(deltas),
    }


def mcnemar_exact_p(correct_a: Sequence[bool], correct_b: Sequence[bool]) -> Dict[str, Any]:
    """Two-sided exact McNemar test (binomial on discordant pairs)."""
    b = sum(1 for x, y in zip(correct_a, correct_b) if x and not y)
    c = sum(1 for x, y in zip(correct_a, correct_b) if (not x) and y)
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "n_discordant": 0, "p_value": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    p = min(1.0, 2.0 * tail)
    return {"b": b, "c": c, "n_discordant": n, "p_value": p}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=PROJECT_ROOT / "logs")
    parser.add_argument("--log-prefix", default=None,
                        help="formal_run / pilot_run; auto-detected when omitted")
    parser.add_argument("--results-dir", type=Path, default=PROJECT_ROOT / "results")
    parser.add_argument("--output", type=Path, default=None,
                        help="summary CSV path")
    parser.add_argument("--detail-csv", type=Path, default=None,
                        help="per-sample wide CSV path")
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--bootstrap-seed", type=int, default=2026)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    prefix = resolve_prefix(args.log_dir, args.log_prefix)
    is_formal = prefix == "formal_run"

    records_by_condition: Dict[str, List[Dict]] = {}
    print(f"log prefix: {prefix}")
    for condition in CONDITION_ORDER:
        records_by_condition[condition] = load_condition_records(
            args.log_dir, prefix, condition
        )

    rows: List[Dict] = []
    for condition in CONDITION_ORDER:
        records = records_by_condition[condition]
        counts, metrics = compute_metrics(records)
        rows.append({"condition": condition, **counts, **metrics})

    print("\nPilot metrics (positive class = fraud; AUC uses logged confidence)\n")
    print_table(rows, METRIC_HEADERS)

    summary_path = args.output or (
        args.results_dir / ("formal_metrics_summary.csv" if is_formal
                            else "pilot_metrics_summary.csv")
    )
    write_csv(summary_path, METRIC_HEADERS, rows)
    print(f"\nSaved summary: {summary_path}")

    # ---------------- per-sample wide detail ----------------
    detail_path = args.detail_csv or (
        args.results_dir / ("formal_predictions_detail.csv" if is_formal
                            else "pilot_predictions_detail.csv")
    )
    by_condition: Dict[str, Dict[str, Dict]] = {}
    for condition in CONDITION_ORDER:
        by_condition[condition] = {
            str(r.get("transaction_id")): r
            for r in records_by_condition[condition]
            if r.get("transaction_id") is not None
        }
    tids = sorted(
        {tid for cond in CONDITION_ORDER for tid in by_condition[cond].keys()},
        key=lambda x: int(x) if x.isdigit() else x,
    )
    detail_headers = ["transaction_id", "ground_truth_label"]
    for condition in CONDITION_ORDER:
        detail_headers += [
            f"{condition}_status",
            f"{condition}_is_fraud",
            f"{condition}_confidence",
            f"{condition}_correct",
        ]
    detail_rows: List[Dict] = []
    for tid in tids:
        y_true = None
        row: Dict[str, Any] = {"transaction_id": tid}
        for condition in CONDITION_ORDER:
            rec = by_condition[condition].get(tid)
            if rec is None:
                row[f"{condition}_status"] = "missing"
                row[f"{condition}_is_fraud"] = ""
                row[f"{condition}_confidence"] = ""
                row[f"{condition}_correct"] = ""
                continue
            if y_true is None and rec.get("ground_truth_label") in (0, 1):
                y_true = int(rec["ground_truth_label"])
            pred = rec.get("is_fraud")
            row[f"{condition}_status"] = rec.get("status")
            row[f"{condition}_is_fraud"] = pred if pred is not None else ""
            row[f"{condition}_confidence"] = (
                rec.get("confidence") if rec.get("confidence") is not None else ""
            )
            row[f"{condition}_correct"] = (
                int(int(pred) == y_true)
                if pred in (0, 1) and y_true in (0, 1) else ""
            )
        row["ground_truth_label"] = y_true if y_true is not None else ""
        detail_rows.append(row)
    write_csv(detail_path, detail_headers, detail_rows)
    print(f"Saved detail : {detail_path}")

    # ---------------- paired bootstrap + McNemar ----------------
    bootstrap_rng = random.Random(args.bootstrap_seed)
    bootstrap_report: Dict[str, Any] = {
        "log_prefix": prefix,
        "bootstrap_n": args.bootstrap,
        "bootstrap_seed": args.bootstrap_seed,
        "condition_intervals": {},
        "pairwise": {},
    }
    pair_rows: List[Dict] = []
    pairs = [
        ("A", "B"), ("A", "C"), ("A", "D"),
        ("B", "C"), ("B", "D"), ("C", "D"),
    ]

    for condition in CONDITION_ORDER:
        recs = [
            r for r in records_by_condition[condition]
            if r.get("status") == "success"
            and r.get("ground_truth_label") in (0, 1)
            and r.get("is_fraud") in (0, 1)
        ]
        y_true = [int(r["ground_truth_label"]) for r in recs]
        y_pred = [int(r["is_fraud"]) for r in recs]
        scores = [
            float(r["confidence"]) if r.get("confidence") is not None else None
            for r in recs
        ]
        cond_stats: Dict[str, Any] = {}
        for metric_name in ("accuracy", "macro_f1", "auc_roc"):
            func = METRIC_FUNCS[metric_name]
            point = func(y_true, y_pred, scores)
            values: List[float] = []
            for _ in range(args.bootstrap):
                idx = [bootstrap_rng.randrange(len(y_true)) for _ in range(len(y_true))]
                value = func(
                    [y_true[i] for i in idx],
                    [y_pred[i] for i in idx],
                    [scores[i] for i in idx],
                )
                if value is not None:
                    values.append(value)
            if values:
                values.sort()
                cond_stats[metric_name] = {
                    "point": point,
                    "ci_low": values[int(round(0.025 * (len(values) - 1)))],
                    "ci_high": values[int(round(0.975 * (len(values) - 1)))],
                    "n_boot_valid": len(values),
                }
            else:
                cond_stats[metric_name] = {
                    "point": point, "ci_low": None, "ci_high": None,
                    "n_boot_valid": 0,
                }
        bootstrap_report["condition_intervals"][condition] = cond_stats

    for cond_a, cond_b in pairs:
        map_a = {str(r["transaction_id"]): r for r in records_by_condition[cond_a]}
        map_b = {str(r["transaction_id"]): r for r in records_by_condition[cond_b]}
        common = sorted(
            [tid for tid in map_a if tid in map_b],
            key=lambda x: int(x) if x.isdigit() else x,
        )
        y_true, pred_a, pred_b, score_a, score_b = [], [], [], [], []
        correct_a, correct_b = [], []
        for tid in common:
            ra, rb = map_a[tid], map_b[tid]
            if (
                ra.get("status") != "success" or rb.get("status") != "success"
                or ra.get("is_fraud") not in (0, 1) or rb.get("is_fraud") not in (0, 1)
                or ra.get("ground_truth_label") not in (0, 1)
            ):
                continue
            yt = int(ra["ground_truth_label"])
            y_true.append(yt)
            pred_a.append(int(ra["is_fraud"]))
            pred_b.append(int(rb["is_fraud"]))
            score_a.append(ra.get("confidence"))
            score_b.append(rb.get("confidence"))
            correct_a.append(int(ra["is_fraud"]) == yt)
            correct_b.append(int(rb["is_fraud"]) == yt)

        acc = paired_bootstrap_ci(
            y_true, pred_a, score_a, pred_b, score_b,
            "accuracy", args.bootstrap, bootstrap_rng,
        )
        f1 = paired_bootstrap_ci(
            y_true, pred_a, score_a, pred_b, score_b,
            "macro_f1", args.bootstrap, bootstrap_rng,
        )
        mcnemar = mcnemar_exact_p(correct_a, correct_b)
        bootstrap_report["pairwise"][f"{cond_a}_minus_{cond_b}"] = {
            "n_paired": len(y_true),
            "accuracy": acc,
            "macro_f1": f1,
            "mcnemar": mcnemar,
        }
        pair_rows.append({
            "comparison": f"{cond_a}_minus_{cond_b}",
            "n_paired": len(y_true),
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
        })

    boot_path = args.results_dir / (
        "formal_metrics_bootstrap.json" if is_formal
        else "pilot_metrics_bootstrap.json"
    )
    boot_path.parent.mkdir(parents=True, exist_ok=True)
    boot_path.write_text(
        json.dumps(bootstrap_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pair_path = args.results_dir / (
        "formal_pairwise_tests.csv" if is_formal
        else "pilot_pairwise_tests.csv"
    )
    write_csv(pair_path, list(pair_rows[0].keys()) if pair_rows else ["comparison"],
              pair_rows)

    print(f"Saved bootstrap: {boot_path}")
    print(f"Saved pairwise : {pair_path}")
    print(
        f"Bootstrap N={args.bootstrap}, seed={args.bootstrap_seed}; "
        "accuracy / macro-F1 / AUC CIs use paired resampling."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
