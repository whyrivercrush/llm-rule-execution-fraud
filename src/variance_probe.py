# -*- coding: utf-8 -*-
"""
variance_probe.py

Sub-track variance probe:

  * draw a stratified 25 fraud + 25 normal subset (seed=200) from the formal
    400-sample test set;
  * run all four conditions A/B/C/D on that subset, 5 independent repeats;
  * report per-sample label agreement rate and confidence variance;
  * save results/model_variance_50samples.json.

Nominal API calls: 50 samples x 10 calls/sample x 5 repeats = 2,500.

Usage:
    python src/variance_probe.py --config config.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import run_experiment as rx


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def stratified_subset(
    samples: Sequence[Dict[str, Any]],
    n_pos: int,
    n_neg: int,
    seed: int,
) -> List[Dict[str, Any]]:
    pos = [s for s in samples if int(s["ground_truth_label"]) == 1]
    neg = [s for s in samples if int(s["ground_truth_label"]) == 0]
    if len(pos) < n_pos or len(neg) < n_neg:
        raise ValueError(
            f"not enough samples for {n_pos}/{n_neg} split: "
            f"have {len(pos)} pos, {len(neg)} neg"
        )
    rng = random.Random(seed)
    chosen = rng.sample(pos, n_pos) + rng.sample(neg, n_neg)
    rng.shuffle(chosen)
    return chosen


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.json")
    parser.add_argument("--input", type=Path, default=None,
                        help="formal 400-sample JSON; defaults to config input_path")
    parser.add_argument("--log-dir", type=Path, default=PROJECT_ROOT / "logs")
    parser.add_argument("--log-prefix", default="variance")
    parser.add_argument("--results-dir", type=Path, default=PROJECT_ROOT / "results")
    parser.add_argument("--n-pos", type=int, default=25)
    parser.add_argument("--n-neg", type=int, default=25)
    parser.add_argument("--seed", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--sample-concurrency", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def build_runtime_cfg(args: argparse.Namespace, input_path: Path) -> Dict[str, Any]:
    ns = argparse.Namespace(
        config=args.config,
        input=input_path,
        log_dir=args.log_dir,
        base_url=None,
        api_key=None,
        model=None,
        temperature=None,
        max_tokens=None,
        timeout_seconds=None,
        retries=None,
        concurrency=args.concurrency,
        sample_concurrency=args.sample_concurrency,
        conditions="A,B,C,D",
        limit=None,
        dry_run=args.dry_run,
        json_mode=None,
        log_prefix=args.log_prefix,
        resume=True,
        force_resume=False,
    )
    return rx.build_cfg(ns)


def latest_success_records(path: Path) -> Dict[str, Dict[str, Any]]:
    records = rx.read_jsonl_records(path)
    latest: Dict[str, Dict[str, Any]] = {}
    for r in records:
        tid = str(r.get("transaction_id") or "")
        if tid and r.get("status") == "success":
            latest[tid] = r
    return latest


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logger = rx.setup_logger()
    try:
        if args.input:
            input_path = Path(args.input)
        else:
            raw_cfg = (
                json.loads(Path(args.config).read_text(encoding="utf-8"))
                if Path(args.config).exists() else {}
            )
            if not raw_cfg.get("input_path"):
                raise ValueError("no --input given and config.json has no input_path")
            input_path = Path(raw_cfg["input_path"])
        cfg = build_runtime_cfg(args, input_path)
        cfg["input_path"] = input_path
        samples = rx.load_samples(input_path)
        subset = stratified_subset(samples, args.n_pos, args.n_neg, args.seed)
    except Exception as exc:
        logger.error("variance probe setup failed: %s", exc)
        return 2

    expected_calls = (
        len(subset) * sum(rx.CALLS_PER_SAMPLE[c] for c in ("A", "B", "C", "D"))
        * args.repeats
    )
    logger.info(
        "variance probe | subset=%d (%d pos / %d neg) | repeats=%d | "
        "expected_calls=%d | model=%s | temperature=%s",
        len(subset), args.n_pos, args.n_neg, args.repeats,
        expected_calls, cfg["model"], cfg["temperature"],
    )

    actual_calls = 0
    per_repeat_status: Dict[str, Dict[str, int]] = {}
    abort_reason: Optional[str] = None

    try:
        for repeat in range(1, args.repeats + 1):
            repeat_status: Dict[str, int] = {}
            for condition in ("A", "B", "C", "D"):
                log_path = args.log_dir / (
                    f"{args.log_prefix}_r{repeat}_condition_{condition}.jsonl"
                )
                completed = rx.completed_ids_from_log(log_path)
                pending = [
                    s for s in subset
                    if str(s["TransactionID"]) not in completed
                ]
                if not log_path.exists():
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_path.write_text("", encoding="utf-8")
                logger.info(
                    "repeat %d/%d condition %s | pending=%d | already_done=%d",
                    repeat, args.repeats, condition, len(pending), len(completed),
                )
                start = time.monotonic()
                fatal_error: Optional[str] = None

                def handle(record: Dict[str, Any], idx: int, sample: Dict[str, Any]) -> bool:
                    nonlocal actual_calls, fatal_error
                    repeat_status[record["status"]] = (
                        repeat_status.get(record["status"], 0) + 1
                    )
                    actual_calls += int(record.get("api_calls") or 0)
                    rx.append_jsonl(log_path, record)
                    if record.get("fatal"):
                        fatal_error = (
                            f"variance repeat {repeat} condition {condition} "
                            f"fatal: {record.get('error')}"
                        )
                        return True
                    if idx % 10 == 0 or idx == len(pending):
                        logger.info(
                            "repeat %d/%d condition %s progress %d/%d",
                            repeat, args.repeats, condition, idx, len(pending),
                        )
                    return False

                runner = rx.CONDITION_RUNNERS[condition]
                if args.sample_concurrency <= 1:
                    for idx, sample in enumerate(pending, start=1):
                        record = runner(cfg, logger, condition, sample)
                        if handle(record, idx, sample):
                            break
                else:
                    with ThreadPoolExecutor(max_workers=args.sample_concurrency) as pool:
                        future_map = {
                            pool.submit(runner, cfg, logger, condition, sample): sample
                            for sample in pending
                        }
                        for idx, future in enumerate(as_completed(future_map), start=1):
                            sample = future_map[future]
                            if future.cancelled():
                                continue
                            try:
                                record = future.result()
                            except CancelledError:
                                continue
                            except Exception as exc:
                                record = {
                                    "condition": condition,
                                    "transaction_id": str(sample["TransactionID"]),
                                    "ground_truth_label": sample.get("ground_truth_label"),
                                    "status": "api_error",
                                    "api_calls": 0,
                                    "error": str(exc),
                                    "fatal": False,
                                }
                            if handle(record, idx, sample):
                                for pending_future in future_map:
                                    pending_future.cancel()
                if fatal_error:
                    raise rx.RunAborted(fatal_error)
                logger.info(
                    "repeat %d/%d condition %s done | %.1fs",
                    repeat, args.repeats, condition, time.monotonic() - start,
                )
            per_repeat_status[f"repeat_{repeat}"] = repeat_status
    except KeyboardInterrupt:
        abort_reason = "keyboard_interrupt"
    except rx.RunAborted as exc:
        abort_reason = str(exc)

    # Build per-condition, per-sample agreement / variance across repeats.
    condition_reports: Dict[str, Any] = {}
    for condition in ("A", "B", "C", "D"):
        per_sample: Dict[str, Any] = {}
        agreements: List[float] = []
        variances: List[float] = []
        for sample in subset:
            tid = str(sample["TransactionID"])
            labels: List[int] = []
            confidences: List[float] = []
            for repeat in range(1, args.repeats + 1):
                path = args.log_dir / (
                    f"{args.log_prefix}_r{repeat}_condition_{condition}.jsonl"
                )
                record = latest_success_records(path).get(tid)
                if record is None:
                    continue
                if record.get("is_fraud") in (0, 1):
                    labels.append(int(record["is_fraud"]))
                if record.get("confidence") is not None:
                    confidences.append(float(record["confidence"]))
            n_valid = len(labels)
            if n_valid:
                counts = {0: labels.count(0), 1: labels.count(1)}
                agreement = max(counts.values()) / n_valid
            else:
                agreement = None
            variance = (
                statistics.pvariance(confidences) if len(confidences) >= 2 else None
            )
            std = (
                statistics.pstdev(confidences) if len(confidences) >= 2 else None
            )
            per_sample[tid] = {
                "ground_truth_label": int(sample["ground_truth_label"]),
                "n_valid_runs": n_valid,
                "labels": labels,
                "agreement_rate": agreement,
                "confidences": confidences,
                "confidence_mean": (
                    statistics.fmean(confidences) if confidences else None
                ),
                "confidence_variance": variance,
                "confidence_std": std,
            }
            if agreement is not None:
                agreements.append(agreement)
            if variance is not None:
                variances.append(variance)

        condition_reports[condition] = {
            "n_samples": len(subset),
            "n_samples_with_repeats": len(agreements),
            "mean_agreement_rate": (
                statistics.fmean(agreements) if agreements else None
            ),
            "min_agreement_rate": min(agreements) if agreements else None,
            "n_samples_agreement_below_1": sum(
                1 for a in agreements if a < 1.0
            ),
            "mean_confidence_variance": (
                statistics.fmean(variances) if variances else None
            ),
            "mean_confidence_std": (
                statistics.fmean(
                    [v ** 0.5 for v in variances]
                ) if variances else None
            ),
            "samples": per_sample,
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "temperature": cfg["temperature"],
        "json_mode": cfg.get("json_mode"),
        "repeats": args.repeats,
        "subset": {
            "seed": args.seed,
            "n_pos": args.n_pos,
            "n_neg": args.n_neg,
            "n": len(subset),
            "transaction_ids": [str(s["TransactionID"]) for s in subset],
        },
        "expected_calls": expected_calls,
        "actual_calls": actual_calls,
        "aborted": abort_reason,
        "per_repeat_status": per_repeat_status,
        "conditions": condition_reports,
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.results_dir / "model_variance_50samples.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nVariance probe summary")
    print("condition | mean_agreement | min_agreement | mean_conf_var | n(<1.0)")
    for condition in ("A", "B", "C", "D"):
        r = condition_reports[condition]
        print(
            f"{condition:9s} | {rx_fmt(r['mean_agreement_rate'])} | "
            f"{rx_fmt(r['min_agreement_rate'])} | "
            f"{rx_fmt(r['mean_confidence_variance'])} | "
            f"{r['n_samples_agreement_below_1']}"
        )
    print(f"\nSaved: {report_path}")
    if abort_reason:
        print(f"ABORTED: {abort_reason}")
        return 3
    return 0


def rx_fmt(value: Optional[float], digits: int = 4) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


if __name__ == "__main__":
    raise SystemExit(main())
