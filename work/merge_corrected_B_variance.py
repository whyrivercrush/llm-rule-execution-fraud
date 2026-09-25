# -*- coding: utf-8 -*-
"""
Merge the corrected-B variance data into the canonical root report.

The root results/model_variance_50samples.json was generated before the B/D
isolation fix, so its B column is invalid. A fixed-code full variance rerun
already exists under rerun_2026-09-12/. We keep A/C/D from the original root
report (they were unaffected), recompute B from the fixed-code B logs, and
write the merged canonical report. No API calls.
"""

import json
import shutil
import statistics
import csv
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ROOT_REPORT = PROJECT / "results" / "model_variance_50samples.json"
RERUN_DIR = PROJECT / "rerun_2026-09-12"
RERUN_REPORT = RERUN_DIR / "results" / "model_variance_50samples.json"
RERUN_B_LOGS = sorted((RERUN_DIR / "logs").glob("variance_fixed_r*_condition_B.jsonl"))
BACKUP = PROJECT / "results" / "model_variance_50samples_prerefix.json"
METRICS = PROJECT / "results" / "variance_run_metrics.csv"
METRICS_BACKUP = PROJECT / "results" / "variance_run_metrics_prerefix.csv"
RERUN_METRICS = RERUN_DIR / "results" / "variance_run_metrics.csv"


def latest_success(path):
    latest = {}
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        record = json.loads(line)
        tid = str(record.get("transaction_id") or "")
        if tid and record.get("status") == "success":
            latest[tid] = record
    return latest


def main():
    root = json.loads(ROOT_REPORT.read_text(encoding="utf-8"))
    rerun = json.loads(RERUN_REPORT.read_text(encoding="utf-8"))
    prerefix = (
        json.loads(BACKUP.read_text(encoding="utf-8"))
        if BACKUP.exists() else root
    )
    assert set(root["subset"]["transaction_ids"]) == set(rerun["subset"]["transaction_ids"]), (
        "subset mismatch between root and rerun variance reports"
    )
    subset_ids = [str(x) for x in root["subset"]["transaction_ids"]]

    repeat_maps = [latest_success(p) for p in RERUN_B_LOGS]
    assert len(repeat_maps) == 5, f"expected 5 B logs, found {len(repeat_maps)}"
    bad_rule_records = sum(
        1
        for m in repeat_maps
        for r in m.values()
        if any(x.get("rule_ids") for x in r.get("roles") or [])
    )
    assert bad_rule_records == 0, "fixed B logs unexpectedly contain rule_ids"
    covered = {tid for m in repeat_maps for tid in m}
    assert set(subset_ids) <= covered, "fixed B logs do not cover the full subset"

    per_sample = {}
    agreements, variances = [], []
    for tid in subset_ids:
        labels, confidences = [], []
        for m in repeat_maps:
            r = m.get(tid)
            if r is None:
                continue
            if r.get("is_fraud") in (0, 1):
                labels.append(int(r["is_fraud"]))
            if r.get("confidence") is not None:
                confidences.append(float(r["confidence"]))
        n_valid = len(labels)
        agreement = (
            max(labels.count(0), labels.count(1)) / n_valid if n_valid else None
        )
        variance = statistics.pvariance(confidences) if len(confidences) >= 2 else None
        std = statistics.pstdev(confidences) if len(confidences) >= 2 else None
        per_sample[tid] = {
            "ground_truth_label": next(
                (int(m[tid]["ground_truth_label"]) for m in repeat_maps if tid in m), None
            ),
            "n_valid_runs": n_valid,
            "labels": labels,
            "agreement_rate": agreement,
            "confidences": confidences,
            "confidence_mean": statistics.fmean(confidences) if confidences else None,
            "confidence_variance": variance,
            "confidence_std": std,
        }
        if agreement is not None:
            agreements.append(agreement)
        if variance is not None:
            variances.append(variance)

    corrected_b = {
        "n_samples": len(subset_ids),
        "n_samples_with_repeats": len(agreements),
        "mean_agreement_rate": statistics.fmean(agreements),
        "min_agreement_rate": min(agreements),
        "n_samples_agreement_below_1": sum(1 for a in agreements if a < 1.0),
        "mean_confidence_variance": statistics.fmean(variances),
        "mean_confidence_std": statistics.fmean([v ** 0.5 for v in variances]),
        "samples": per_sample,
    }
    rerun_b = rerun["conditions"]["B"]
    for key in ("mean_agreement_rate", "mean_confidence_variance"):
        assert abs(corrected_b[key] - rerun_b[key]) < 1e-9, (key, corrected_b[key], rerun_b[key])

    if not BACKUP.exists():
        shutil.copy2(ROOT_REPORT, BACKUP)

    updated = dict(root)
    updated["conditions"] = dict(root["conditions"])
    updated["conditions"]["B"] = corrected_b
    updated["corrections"] = {
        "B": {
            "reason": (
                "pre-fix B variance was produced while B also received the D "
                "per-role rules; replaced by fixed-code B variance"
            ),
            "source_logs": [
                str(p.relative_to(PROJECT)).replace("\\", "/") for p in RERUN_B_LOGS
            ],
            "source_report": str(RERUN_REPORT.relative_to(PROJECT)).replace("\\", "/"),
            "previous_B_aggregates": {
                k: prerefix["conditions"]["B"][k]
                for k in (
                    "mean_agreement_rate",
                    "min_agreement_rate",
                    "mean_confidence_variance",
                    "n_samples_agreement_below_1",
                )
            },
            "corrected_B_aggregates": {
                k: corrected_b[k]
                for k in (
                    "mean_agreement_rate",
                    "min_agreement_rate",
                    "mean_confidence_variance",
                    "n_samples_agreement_below_1",
                )
            },
            "substituted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "A_C_D": (
            "kept from the original root report; they are unaffected by the "
            "B/D rule-routing bug"
        ),
    }
    ROOT_REPORT.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    note = f"""# Corrected-B variance merge

- Canonical report updated: `results/model_variance_50samples.json`
- Pre-fix backup: `results/model_variance_50samples_prerefix.json`
- Corrected-B source logs: `rerun_2026-09-12/logs/variance_fixed_r1..r5_condition_B.jsonl`
- Same subset as the original probe: seed={root['subset']['seed']}, n={root['subset']['n']}, {root['subset']['n_pos']}/{root['subset']['n_neg']}
- Fixed B logs rule_ids violations: 0 / 250 records

## B aggregates

| metric | pre-fix B (invalid) | corrected B |
| --- | --- | --- |
| mean agreement | {prerefix['conditions']['B']['mean_agreement_rate']:.4f} | {corrected_b['mean_agreement_rate']:.4f} |
| min agreement | {prerefix['conditions']['B']['min_agreement_rate']:.4f} | {corrected_b['min_agreement_rate']:.4f} |
| mean confidence variance | {prerefix['conditions']['B']['mean_confidence_variance']:.6f} | {corrected_b['mean_confidence_variance']:.6f} |
| samples with disagreement | {prerefix['conditions']['B']['n_samples_agreement_below_1']}/50 | {corrected_b['n_samples_agreement_below_1']}/50 |

A/C/D were not rerun for this merge; their values are the original root-report
values and are unaffected by the bug. No API calls were made.
"""
    (PROJECT / "results" / "model_variance_B_correction_note.md").write_text(
        note, encoding="utf-8", newline="\n"
    )

    # Replace only the B rows in the per-repeat metrics CSV, keeping A/C/D.
    with METRICS.open(encoding="utf-8-sig", newline="") as f:
        root_rows = list(csv.DictReader(f))
    with RERUN_METRICS.open(encoding="utf-8-sig", newline="") as f:
        rerun_rows = list(csv.DictReader(f))
    if not METRICS_BACKUP.exists():
        shutil.copy2(METRICS, METRICS_BACKUP)
    merged_rows = [r for r in root_rows if r["condition"] != "B"]
    merged_rows += [r for r in rerun_rows if r["condition"] == "B"]
    merged_rows.sort(key=lambda r: (int(r["repeat"]), r["condition"]))
    with METRICS.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(root_rows[0].keys()))
        writer.writeheader()
        writer.writerows(merged_rows)
    print("per-repeat metrics updated:", METRICS)
    print("per-repeat metrics backup:", METRICS_BACKUP)
    print("canonical report updated:", ROOT_REPORT)
    print("backup:", BACKUP)
    print("pre-fix B:", updated["corrections"]["B"]["previous_B_aggregates"])
    print("corrected B:", updated["corrections"]["B"]["corrected_B_aggregates"])
    print("note:", PROJECT / "results" / "model_variance_B_correction_note.md")


if __name__ == "__main__":
    main()
