#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_card_clusters.py — export the entity (card1) clustering needed by the
card-level analyses of the manuscript, so that `recompute.py` can verify them
without the 1.3 GB raw competition files.

`card1` is deliberately *not* part of the serialized card text shown to the
model (manuscript Sec 4.6(a): "the serialized card does not contain card1, so
no classifier in our study can condition on card identity directly"). It is
therefore not present in `data/test_cards_400*.json`, but the cluster-robust
intervals and the card-disjoint subsample analysis both need it.

This script reads the public IEEE-CIS benchmark plus the pilot development
file and writes `data/card_clusters.json`:

    {
      "pilot_card1":  [<distinct card1 values in the pilot set>],
      "card1_by_transaction": {"<TransactionID>": "<card1>", ...}   # 800 rows
    }

card1 is an anonymised, non-identifying integer supplied by the dataset
provider; exporting it leaks nothing that is not already public.

Usage
-----
    python work/build_card_clusters.py --raw-dir <dir with train_transaction.csv
                                                   and pilot_sample_100.csv>
    # or set IEEE_CIS_RAW=<dir>

Rebuilding is only needed if the evaluation sets are re-drawn; the exported
`data/card_clusters.json` is committed and is what `recompute.py` reads.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

EVAL_FILES = ["test_cards_400.json", "test_cards_400_carddisjoint.json"]


def read_card1_map(train_csv: Path, wanted: set) -> dict:
    """Stream the (large) competition CSV and keep only the ids we need."""
    found = {}
    with train_csv.open(encoding="utf-8", errors="ignore", newline="") as fh:
        for row in csv.DictReader(fh):
            tid = (row.get("TransactionID") or "").strip()
            if tid in wanted:
                found[tid] = (row.get("card1") or "").strip()
                if len(found) == len(wanted):
                    break
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", default=os.environ.get("IEEE_CIS_RAW"),
                    help="directory holding train_transaction.csv and pilot_sample_100.csv")
    ap.add_argument("--out", default=str(DATA / "card_clusters.json"))
    args = ap.parse_args(argv)

    if not args.raw_dir:
        ap.error("--raw-dir is required (or set IEEE_CIS_RAW)")

    raw = Path(args.raw_dir)
    train_csv = raw / "train_transaction.csv"
    pilot_csv = raw / "pilot_sample_100.csv"
    for p in (train_csv, pilot_csv):
        if not p.exists():
            print("MISSING: %s" % p, file=sys.stderr)
            return 1

    wanted = set()
    for name in EVAL_FILES:
        for rec in json.loads((DATA / name).read_text(encoding="utf-8")):
            wanted.add(str(rec["TransactionID"]))
    print("evaluation instances requested: %d" % len(wanted))

    card1 = read_card1_map(train_csv, wanted)
    missing = sorted(wanted - set(card1))
    if missing:
        print("!! %d ids not found in train_transaction.csv (first: %s)"
              % (len(missing), missing[:5]), file=sys.stderr)
        return 1

    pilot_card1 = set()
    with pilot_csv.open(encoding="utf-8", errors="ignore", newline="") as fh:
        for row in csv.DictReader(fh):
            c = (row.get("card1") or "").strip()
            if c:
                pilot_card1.add(c)

    distinct = sorted({v for v in card1.values() if v}, key=lambda x: int(x) if x.isdigit() else 0)
    payload = {
        "description": ("card1 (issuer card identifier) for every evaluation instance, plus the "
                        "distinct card1 values present in the 100-record pilot development set. "
                        "Needed by the card-level cluster-robust and card-disjoint analyses; "
                        "card1 is not part of the serialized card text."),
        "source": "IEEE-CIS Fraud Detection benchmark (Kaggle, 2019), train_transaction.csv",
        "pilot_card1": sorted(pilot_card1, key=lambda x: int(x) if x.isdigit() else 0),
        "card1_by_transaction": card1,
    }

    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    overlap = sum(1 for t, c in card1.items() if c in pilot_card1)
    print("wrote %s" % out)
    print("  instances              : %d" % len(card1))
    print("  distinct card1 (eval)  : %d" % len(distinct))
    print("  distinct card1 (pilot) : %d" % len(pilot_card1))
    print("  pilot-overlapping rows : %d" % overlap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
