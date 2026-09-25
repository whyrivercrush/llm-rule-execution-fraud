# -*- coding: utf-8 -*-
"""
sample_formal_test.py

Build the formal 400-sample blind test set from IEEE-CIS train_transaction.csv:

  * 200 fraud + 200 normal
  * uniform reservoir sampling with seed=100
  * IDs already used in pilot_cards_100.json are strictly excluded
  * serialized with the same card template as the pilot
  * output: data/test/test_cards_400.json
  * config.json "input_path" is updated to the new file

Usage:
    python src/sample_formal_test.py
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = Path(r"C:\Users\Niconiconi\sci\数据训练集\train_transaction.csv")
DEFAULT_PILOT = Path(r"C:\Users\Niconiconi\sci\数据训练集\pilot_cards_100.json")
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "test" / "test_cards_400.json"
DEFAULT_CONFIG = PROJECT_ROOT / "config.json"


def clean(value: Any, default: str = "Not specified") -> str:
    text = "" if value is None else str(value).strip()
    return text if text else default


def serialize_card(row: Dict[str, str], tid: str) -> str:
    amt = clean(row.get("TransactionAmt"))
    p_cd = clean(row.get("ProductCD"))
    c_net = clean(row.get("card4"), "Unknown network")
    c_type = clean(row.get("card6"), "card")
    c_bank = clean(row.get("card2"), "N/A")
    c_country = clean(row.get("card3"), "N/A")

    p_mail = clean(row.get("P_emaildomain"), "Not provided")
    r_mail = clean(row.get("R_emaildomain"), "Not provided")
    addr1 = clean(row.get("addr1"), "Unknown")
    addr2 = clean(row.get("addr2"), "Unknown")
    dist = clean(row.get("dist1"), "N/A")

    c1 = clean(row.get("C1"), "0")
    c2 = clean(row.get("C2"), "0")
    c14 = clean(row.get("C14"), "0")
    d1 = clean(row.get("D1"), "N/A")
    d2 = clean(row.get("D2"), "N/A")

    m1 = clean(row.get("M1"), "Unknown")
    m2 = clean(row.get("M2"), "Unknown")
    m4 = clean(row.get("M4"), "Unknown")
    m6 = clean(row.get("M6"), "Unknown")

    return (
        f"[Transaction Record #{tid}]\n"
        f"- Financial Details: Order amount is ${amt} USD under product category '{p_cd}'.\n"
        f"- Payment Method: {c_net} {c_type} (Bank ID: {c_bank}, Card Country: {c_country}).\n"
        f"- User Profile & Environment: Purchaser email domain: '{p_mail}', "
        f"Recipient email domain: '{r_mail}'. Billing region code: {addr1}, "
        f"country code: {addr2}. Address distance offset: {dist} miles.\n"
        f"- Historical Frequencies & Timing: Operation counts associated with "
        f"account/card (C1={c1}, C2={c2}, C14={c14}). Days since reference or "
        f"previous activity: D1={d1} days, D2={d2} days.\n"
        f"- Identity Consistency Checks: Verification match attributes: "
        f"M1={m1}, M2={m2}, M4={m4}, M6={m6}."
    )


def reservoir_sample(
    csv_path: Path,
    excluded_ids: set,
    per_class: int,
    seed: int,
) -> tuple:
    rng = random.Random(seed)
    reservoirs: Dict[int, List[Dict[str, str]]] = {0: [], 1: []}
    seen_counts = {0: 0, 1: 0}
    rows_scanned = 0
    excluded_hits = 0

    with csv_path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"no header found in {csv_path}")
        for row in reader:
            rows_scanned += 1
            tid = clean(row.get("TransactionID"), "")
            if not tid:
                continue
            if tid in excluded_ids:
                excluded_hits += 1
                continue
            label_raw = clean(row.get("isFraud"), "")
            if label_raw not in ("0", "1"):
                continue
            label = int(label_raw)
            seen_counts[label] += 1
            bucket = reservoirs[label]
            if len(bucket) < per_class:
                bucket.append(row)
            else:
                j = rng.randrange(seen_counts[label])
                if j < per_class:
                    bucket[j] = row

    for label in (0, 1):
        if len(reservoirs[label]) < per_class:
            raise RuntimeError(
                f"only found {len(reservoirs[label])} class-{label} rows after exclusion; "
                f"need {per_class}"
            )
    return reservoirs, seen_counts, rows_scanned, excluded_hits


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seed", type=int, default=100)
    parser.add_argument("--per-class", type=int, default=200)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.csv.exists():
        raise FileNotFoundError(f"train file not found: {args.csv}")
    if not args.pilot.exists():
        raise FileNotFoundError(f"pilot file not found: {args.pilot}")

    pilot = json.loads(args.pilot.read_text(encoding="utf-8"))
    excluded_ids = {str(x["TransactionID"]) for x in pilot}
    print(f"pilot IDs to exclude: {len(excluded_ids)}")

    reservoirs, seen_counts, rows_scanned, excluded_hits = reservoir_sample(
        args.csv, excluded_ids, args.per_class, args.seed
    )

    rng = random.Random(args.seed)
    selected: List[Dict[str, Any]] = []
    for label in (1, 0):
        for row in reservoirs[label]:
            tid = clean(row.get("TransactionID"), "")
            selected.append({
                "TransactionID": tid,
                "ground_truth_label": int(row["isFraud"]),
                "card_text": serialize_card(row, tid),
            })
    rng.shuffle(selected)

    selected_ids = [x["TransactionID"] for x in selected]
    if len(set(selected_ids)) != len(selected_ids):
        raise RuntimeError("duplicate TransactionID detected in formal sample")
    overlap = set(selected_ids) & excluded_ids
    if overlap:
        raise RuntimeError(f"pilot overlap remains: {len(overlap)} ids")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_csv": str(args.csv),
        "pilot_exclusion_file": str(args.pilot),
        "pilot_ids_excluded": len(excluded_ids),
        "seed": args.seed,
        "per_class": args.per_class,
        "rows_scanned": rows_scanned,
        "eligible_counts": {"normal": seen_counts[0], "fraud": seen_counts[1]},
        "excluded_id_hits": excluded_hits,
        "selected_counts": {
            "normal": sum(1 for x in selected if x["ground_truth_label"] == 0),
            "fraud": sum(1 for x in selected if x["ground_truth_label"] == 1),
        },
        "output_file": str(args.output),
        "selected_ids": selected_ids,
    }
    meta_path = args.output.with_name(args.output.stem + "_meta.json")
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cfg_path = Path(args.config)
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        cfg = {}
    cfg["input_path"] = str(args.output)
    cfg_path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"selected: {meta['selected_counts']} (total {len(selected)})")
    print(f"output  : {args.output}")
    print(f"meta    : {meta_path}")
    print(f"config  : {cfg_path} input_path updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
