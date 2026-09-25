# -*- coding: utf-8 -*-
"""
check_determinism.py

Empirically probe whether the configured LLM endpoint returns identical
outputs for the same prompt at temperature 0.0.

Some OpenAI-compatible endpoints do not document or honor a `seed` parameter;
this script therefore defaults to repeated identical calls. Pass
`--include-seed` to test whether the endpoint accepts a `seed` field at all.

The report is written to `results/determinism_report.json`.

Usage:
    python src/check_determinism.py --config config.json --runs 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import run_experiment as rx


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe LLM output determinism at temperature 0.0."
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--sample-index", type=int, default=0)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--with-rules", action="store_true",
                        help="use the condition-C prompt (with all four rules)")
    parser.add_argument("--include-seed", action="store_true",
                        help="send seed=1..runs (many providers reject this)")
    parser.add_argument("--fixed-seed", type=int, default=None,
                        help="send the same seed on every run (tests whether seed is honored)")
    parser.add_argument("--json-mode", action="store_true", dest="json_mode",
                        default=None)
    parser.add_argument("--no-json-mode", action="store_false", dest="json_mode")
    parser.add_argument("--results-dir", type=Path, default=PROJECT_ROOT / "results")
    parser.add_argument("--dry-run", action="store_true",
                        help="offline self-test only; the result is not evidence about an API")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    # Build a Namespace compatible with run_experiment.build_cfg.
    ns = argparse.Namespace(
        config=args.config,
        input=args.input,
        log_dir=PROJECT_ROOT / "logs",
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        temperature=0.0,          # forced: this probe is specifically about temp=0
        max_tokens=None,
        timeout_seconds=None,
        retries=None,
        concurrency=1,
        sample_concurrency=1,
        conditions="A",
        limit=None,
        dry_run=args.dry_run,
        json_mode=args.json_mode,
        log_prefix="probe",
        resume=False,
        force_resume=False,
    )
    logger = rx.setup_logger()
    try:
        cfg = rx.build_cfg(ns)
        samples = rx.load_samples(cfg["input_path"])
        if not samples:
            raise ValueError("input file contains no samples")
        sample = samples[args.sample_index % len(samples)]
    except Exception as exc:
        logger.error("probe setup failed: %s", exc)
        return 2

    rule_ids = rx.ALL_RULE_IDS if args.with_rules else None
    messages = rx.build_a_or_c_messages(sample["card_text"], rule_ids)
    dry_ctx = {
        "condition": "PROBE",
        "transaction_id": str(sample["TransactionID"]),
        "ground_truth_label": sample.get("ground_truth_label"),
    }

    logger.info(
        "determinism probe | model=%s | temperature=%s | runs=%d | include_seed=%s | fixed_seed=%s | json_mode=%s",
        cfg["model"], cfg["temperature"], args.runs,
        args.include_seed, args.fixed_seed, cfg.get("json_mode"),
    )

    runs: List[Dict[str, Any]] = []
    for i in range(args.runs):
        if args.fixed_seed is not None:
            seed_value: Optional[int] = args.fixed_seed
        elif args.include_seed:
            seed_value = i + 1
        else:
            seed_value = None
        extra_body = {"seed": seed_value} if seed_value is not None else None
        item: Dict[str, Any] = {"run": i + 1, "seed": seed_value}
        try:
            content, usage, attempts = rx.call_with_retry(
                cfg, messages, logger, dry_ctx=dry_ctx, extra_body=extra_body
            )
            item["status"] = "success"
            item["attempts"] = attempts
            item["usage"] = usage
            item["raw_response"] = content
            item["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
            try:
                item["parsed"] = rx.parse_final_response(content)
            except Exception as exc:
                item["parsed"] = None
                item["parse_error"] = str(exc)
        except Exception as exc:
            item["status"] = "api_error"
            item["error"] = str(exc)
        runs.append(item)

    successes = [r for r in runs if r["status"] == "success"]
    hashes = {r["sha256"] for r in successes}
    parsed_signatures = {
        json.dumps(r.get("parsed"), sort_keys=True, ensure_ascii=False)
        for r in successes
    }
    confidences = [
        float(r["parsed"]["confidence"])
        for r in successes
        if r.get("parsed") is not None and "confidence" in r["parsed"]
    ]

    exact_identical = bool(successes) and len(hashes) == 1
    parsed_identical = bool(successes) and len(parsed_signatures) == 1
    report = {
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "temperature": cfg["temperature"],
        "json_mode": cfg.get("json_mode"),
        "include_seed": args.include_seed or args.fixed_seed is not None,
        "fixed_seed": args.fixed_seed,
        "runs_requested": args.runs,
        "runs_succeeded": len(successes),
        "runs_failed": len(runs) - len(successes),
        "exact_raw_identical": exact_identical,
        "parsed_fields_identical": parsed_identical,
        "distinct_raw_outputs": len(hashes),
        "distinct_parsed_outputs": len(parsed_signatures),
        "confidence_min": min(confidences) if confidences else None,
        "confidence_max": max(confidences) if confidences else None,
        "runs": runs,
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.results_dir / "determinism_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nDeterminism probe")
    print(f"  runs succeeded      : {len(successes)}/{args.runs}")
    print(f"  distinct raw outputs: {len(hashes)}")
    print(f"  exact raw identical : {exact_identical}")
    print(f"  parsed identical    : {parsed_identical}")
    print(f"  confidence range    : {report['confidence_min']} .. {report['confidence_max']}")
    print(f"  report saved to     : {report_path}")
    if args.dry_run:
        print("  NOTE: dry-run result is a self-test only, not evidence about an API.")
    return 0 if successes else 1


if __name__ == "__main__":
    raise SystemExit(main())
