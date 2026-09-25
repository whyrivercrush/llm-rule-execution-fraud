# -*- coding: utf-8 -*-
"""
run_supplementary.py  --  supplementary experiments for REVISED_MANUSCRIPT_v1

CONSISTENCY GUARANTEE
---------------------
This script imports `src/run_experiment.py` VERBATIM and reuses
`build_a_or_c_messages()` / `build_final_system()` / `call_with_retry()` /
`parse_final_response()`.  It never re-implements a prompt.  The system
template, output schema and serialized card text are therefore byte-identical
to the runs that produced the existing logs.  Only the injected rules block
differs, which is exactly the manipulated variable.

Harness fingerprints (verified 2026-09-17):
  rerun_2026-09-12/src/run_experiment.py          11572E153FE4598A  45955 B
  glm_run_2026-09-12/src/run_experiment.py        B470ECE7EB52E0C3  45956 B
  -> the ONLY difference is an argparse help string; prompt construction,
     API call and decoding logic are identical.

EXPERIMENTS
-----------
  e1_r2only      C with ONLY Rule 2 injected          (400 x 1) x model
  e2_asc         Condition A sampled 4x + majority    (400 x 4) x model
  e3_sham        C with 4 semantically empty rules    (400 x 1) x model
  e4_crisp       C with the 4 real rules de-hedged    (400 x 1) x model
  sample_carddisjoint   build a card-disjoint 400 (no API calls)
  e5_main        A and C on the card-disjoint set     (400 x 2) x model

USAGE
-----
  python run_supplementary.py --exp sample_carddisjoint
  python run_supplementary.py --exp e1_r2only --model deepseek
  python run_supplementary.py --exp e2_asc    --model deepseek
  python run_supplementary.py --exp e3_sham   --model deepseek
  python run_supplementary.py --exp e3_sham   --model glm
  ...
  Add --dry-run to validate prompts with zero API calls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import run_experiment as rx  # noqa: E402

# --------------------------------------------------------------------------
# Model configs: verbatim copies of the frozen originals
# --------------------------------------------------------------------------
CONFIGS = {
    "deepseek": ROOT / "config.deepseek.json",
    "glm": ROOT / "config.json",
    # third and fourth model origins, for the model-dependence claim (Sec 4.1 Result 3)
    "qwen": ROOT / "config_qwen.json",
    "kimi": ROOT / "config_kimi.json",
}

CARDS_STANDARD = ROOT / "data" / "test_cards_400.json"
CARDS_DISJOINT = ROOT / "data" / "test_cards_400_carddisjoint.json"
SOURCE_CSV = Path(r"C:\Users\Niconiconi\sci\数据训练集\train_transaction.csv")
PILOT_JSON = Path(r"C:\Users\Niconiconi\sci\数据训练集\pilot_cards_100.json")

EXPECTED_CARDS_SHA = None  # fill after first check; recorded in the pre-flight log

# --------------------------------------------------------------------------
# Custom rule sets.  Structure is identical to the real rulebook
# ("<Name> Rule: <condition> <claim>"); only the content differs.
# --------------------------------------------------------------------------
SHAM_RULES = {
    "sham_1_identifier_parity": (
        "Identifier Parity Rule: Transaction identifiers terminating in an odd digit, "
        "when combined with order amounts whose integer component is even, strongly "
        "indicate elevated manual review priority."
    ),
    "sham_2_digit_repetition": (
        "Digit Repetition Rule: Order amounts containing the same digit twice in "
        "succession (e.g., 11.00, 5.55, 33.20) strongly signal anomalous processing "
        "conditions requiring additional scrutiny."
    ),
    "sham_3_round_number_proximity": (
        "Round-Number Proximity Rule: Order amounts falling within ten US dollars "
        "below a multiple of one hundred (e.g., 89.00, 190.00) strongly correlate "
        "with irregular account behaviour."
    ),
    "sham_4_domain_length": (
        "Domain Length Rule: Purchaser email domains whose character count is "
        "divisible by three strongly associate with irregular registration patterns "
        "and warrant additional verification."
    ),
}
SHAM_IDS = list(SHAM_RULES)

# Same four rules as the frozen rulebook, with all hedging removed.
CRISP_RULES = {
    "crisp_1_email": (
        "Email Anonymity Rule: Flag if and only if the purchaser email domain is "
        "'Not provided' or is exactly 'anonymous.com'."
    ),
    "crisp_2_velocity": (
        "Velocity Burst Rule: Flag if and only if C2 >= 5."
    ),
    "crisp_3_category": (
        "High-Risk Category & Cross-Border Rule: Flag if and only if the product "
        "category is 'C' and card3 != addr2."
    ),
    "crisp_4_distance": (
        "Geographical & Identity Inconsistency Rule: Flag if and only if dist1 >= 100, "
        "or if any one of M1, M2, M6 equals 'F'."
    ),
}
CRISP_IDS = list(CRISP_RULES)

R2_IDS = ["rule_2_frequency_burst"]

# --------------------------------------------------------------------------
# Pre-flight consistency checks
# --------------------------------------------------------------------------


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def preflight(model: str, exp: str, logger) -> dict:
    """Hard checks. Any failure aborts the run."""
    report = {}

    # 1. harness fingerprint
    harness = ROOT / "src" / "run_experiment.py"
    report["harness_sha"] = sha256(harness)[:16]
    report["harness_size"] = harness.stat().st_size
    if report["harness_size"] not in (45955, 45956):
        raise SystemExit(f"ABORT: harness size {report['harness_size']} unexpected")

    # 2. frozen rulebook unchanged
    report["rules"] = {k: len(v) for k, v in rx.RULES.items()}
    if list(rx.RULES) != ["rule_1_email_anonymity", "rule_2_frequency_burst",
                          "rule_3_category_crossborder", "rule_4_distance_mismatch"]:
        raise SystemExit("ABORT: RULES dict changed")
    report["role_rule_map"] = rx.ROLE_RULE_MAP

    # 3. card set
    cards_path = CARDS_DISJOINT if exp == "e5_main" else CARDS_STANDARD
    if not cards_path.exists():
        raise SystemExit(f"ABORT: card set missing: {cards_path}")
    report["cards_path"] = str(cards_path)
    report["cards_sha"] = sha256(cards_path)[:16]
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    report["cards_n"] = len(cards)
    report["cards_labels"] = sum(int(c["ground_truth_label"]) for c in cards)

    # 4. config
    cfg = json.loads(CONFIGS[model].read_text(encoding="utf-8"))
    for k, want in (("temperature", 0.0), ("max_tokens", 1600),
                    ("retries", 3), ("json_mode", True), ("concurrency", 3)):
        if cfg.get(k) != want:
            raise SystemExit(f"ABORT: config[{k}]={cfg.get(k)!r} expected {want!r}")
    if model == "deepseek" and cfg["model"] != "deepseek-chat":
        raise SystemExit("ABORT: model mismatch")
    if model == "glm" and cfg["model"] != "glm-4.5-air":
        raise SystemExit("ABORT: model mismatch")
    if model == "qwen" and not str(cfg["model"]).lower().startswith("qwen"):
        raise SystemExit("ABORT: model mismatch")
    if model == "kimi" and not str(cfg["model"]).lower().startswith("kimi"):
        raise SystemExit("ABORT: model mismatch")
    report["model"] = cfg["model"]
    report["base_url"] = cfg["base_url"]
    report["key_len"] = len(cfg.get("api_key", ""))

    # 5. PROMPT ISOLATION: every rule text must appear ONLY in its own condition
    def sys_for(ids):
        return rx.build_a_or_c_messages("CARD", ids)[0]["content"]

    sA = sys_for(None)
    sR2 = sys_for(R2_IDS)
    for name, d, ids in (("sham", SHAM_RULES, SHAM_IDS), ("crisp", CRISP_RULES, CRISP_IDS)):
        for k, v in d.items():
            rx.RULES[k] = v
    sSham = sys_for(SHAM_IDS)
    sCrisp = sys_for(CRISP_IDS)
    for k in SHAM_IDS + CRISP_IDS:
        rx.RULES.pop(k, None)

    # every rules variant must share an identical head with the baseline
    head = sA.split("Analysis principles:")[0]
    for nm, s in (("R2only", sR2), ("sham", sSham), ("crisp", sCrisp)):
        if not s.startswith(head):
            raise SystemExit(f"ABORT: {nm} system prompt head differs from baseline")
    # A must contain NO rule text at all
    for k, v in list(rx.RULES.items()):
        if v.split(":")[0] in sA:
            raise SystemExit("ABORT: rule text leaked into Condition A prompt")
    # sham condition must not contain any real rule text
    for k, v in rx.RULES.items():
        if v in sSham:
            raise SystemExit("ABORT: real rule leaked into sham prompt")
    # crisp condition must not contain the hedged originals
    for k, v in rx.RULES.items():
        if v in sCrisp:
            raise SystemExit("ABORT: hedged original leaked into crisp prompt")
    # R2-only must contain exactly one rule
    n_rules_r2 = sR2.count("- ") - sA.count("- ")
    report["r2only_rule_bullets"] = n_rules_r2

    report["sysprompt_sha"] = {
        "A": hashlib.sha256(sA.encode()).hexdigest()[:16],
        "R2only": hashlib.sha256(sR2.encode()).hexdigest()[:16],
        "sham": hashlib.sha256(sSham.encode()).hexdigest()[:16],
        "crisp": hashlib.sha256(sCrisp.encode()).hexdigest()[:16],
    }
    logger.info("PRE-FLIGHT OK | %s", json.dumps(report, ensure_ascii=False))
    return report


# --------------------------------------------------------------------------
# Sampling: card-disjoint 400
# --------------------------------------------------------------------------


def sample_carddisjoint(seed: int = 200, per_class: int = 200) -> None:
    """Reservoir-sample 200/200 EXCLUDING every pilot card1 (not just pilot IDs)."""
    pilot = json.loads(PILOT_JSON.read_text(encoding="utf-8"))
    pilot_ids = {str(r["TransactionID"]) for r in pilot}

    # pass 1: pilot card1 values
    pilot_card1 = set()
    with SOURCE_CSV.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        for r in csv.DictReader(f):
            if r["TransactionID"] in pilot_ids:
                pilot_card1.add(r["card1"])
    print(f"pilot records={len(pilot_ids)} distinct card1={len(pilot_card1)}")

    # pass 2: reservoir sample excluding those card1
    rng = random.Random(seed)
    res = {0: [], 1: []}
    seen = {0: 0, 1: 0}
    scanned = excluded = 0
    with SOURCE_CSV.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        for r in csv.DictReader(f):
            scanned += 1
            if r["card1"] in pilot_card1:
                excluded += 1
                continue
            lab = r.get("isFraud")
            if lab not in ("0", "1"):
                continue
            lab = int(lab)
            seen[lab] += 1
            b = res[lab]
            if len(b) < per_class:
                b.append(r)
            else:
                j = rng.randrange(seen[lab])
                if j < per_class:
                    b[j] = r
    print(f"scanned={scanned} excluded_by_card1={excluded} eligible={seen}")

    # reuse the ORIGINAL serializer so card text is identical in form
    sys.path.insert(0, str(ROOT / "src"))
    import sample_formal_test as sft

    selected = []
    for lab in (1, 0):
        for row in res[lab]:
            tid = str(row["TransactionID"]).strip()
            selected.append({
                "TransactionID": tid,
                "ground_truth_label": int(row["isFraud"]),
                "card_text": sft.serialize_card(row, tid),
            })
    rng.shuffle(selected)

    ids = [x["TransactionID"] for x in selected]
    assert len(set(ids)) == len(ids), "duplicate ids"
    # verify disjointness at BOTH levels
    assert not (set(ids) & pilot_ids), "instance-level overlap"
    new_c1 = set()
    with SOURCE_CSV.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        for r in csv.DictReader(f):
            if r["TransactionID"] in set(ids):
                new_c1.add(r["card1"])
    ov = new_c1 & pilot_card1
    assert not ov, f"card1-level overlap remains: {len(ov)}"

    CARDS_DISJOINT.parent.mkdir(parents=True, exist_ok=True)
    CARDS_DISJOINT.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK -> {CARDS_DISJOINT}")
    print(f"  n={len(selected)} fraud={sum(x['ground_truth_label'] for x in selected)}")
    print(f"  distinct card1={len(new_c1)}  card1 overlap with pilot = 0")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def load_samples(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# Errors that make every subsequent call futile. Abort immediately rather than
# burning the remaining queue (learned the hard way: a balance exhaustion at
# 02:35 once burned 1,217 further calls that could never succeed).
FATAL_PATTERNS = (
    "余额不足", "无可用资源包", "insufficient", "Insufficient",
    "quota", "Quota", "unauthorized", "Unauthorized", "invalid_api_key",
    "401", "402",
)


def is_fatal_error(rec: dict) -> bool:
    if rec.get("status") == "fatal_api_error":
        return True
    e = rec.get("error") or ""
    return any(p in e for p in FATAL_PATTERNS)


def already_done(log_path: Path) -> set:
    if not log_path.exists():
        return set()
    done = set()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("status") == "success":
            done.add(str(r["transaction_id"]))
    return done


def one_sample(cfg, logger, exp, condition, sample, samples_all):
    """Returns one jsonl record per sample."""
    tid = str(sample["TransactionID"])
    card = sample["card_text"]
    dry_ctx = {"condition": condition, "transaction_id": tid,
               "ground_truth_label": sample.get("ground_truth_label")}

    rec = {
        "condition": condition,
        "experiment": exp,
        "transaction_id": tid,
        "ground_truth_label": sample.get("ground_truth_label"),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    t0 = time.monotonic()

    try:
        if exp == "e2_asc":
            votes, confs, errs = [], [], None
            for _ in range(4):
                msgs = rx.build_a_or_c_messages(card, None)
                try:
                    content, usage, att = rx.call_with_retry(cfg, msgs, logger, dry_ctx)
                    p = rx.parse_final_response(content)
                    votes.append(p["is_fraud"])
                    confs.append(p["confidence"])
                except Exception as exc:  # noqa: BLE001
                    errs = str(exc)
            rec["votes"] = votes
            rec["n_valid_calls"] = len(votes)
            rec["mean_confidence"] = (sum(confs) / len(confs)) if confs else None
            if len(votes) == 4:
                if votes.count(1) > votes.count(0):
                    rec["is_fraud"] = 1
                elif votes.count(0) > votes.count(1):
                    rec["is_fraud"] = 0
                else:
                    rec["is_fraud"] = 1 if rec["mean_confidence"] >= 0.5 else 0
                rec["status"] = "success"
                rec["tie"] = votes.count(1) == 2
            else:
                rec["is_fraud"] = None
                rec["status"] = "api_error"
                rec["error"] = errs
        else:
            if exp == "e1_r2only":
                ids = R2_IDS
            elif exp == "e3_sham":
                for k, v in SHAM_RULES.items():
                    rx.RULES[k] = v
                ids = SHAM_IDS
            elif exp == "e4_crisp":
                for k, v in CRISP_RULES.items():
                    rx.RULES[k] = v
                ids = CRISP_IDS
            elif exp == "e5_main":
                ids = rx.ALL_RULE_IDS if condition == "C" else None
            else:
                raise SystemExit(f"unknown exp {exp}")
            msgs = rx.build_a_or_c_messages(card, ids)
            content, usage, att = rx.call_with_retry(cfg, msgs, logger, dry_ctx)
            rec["raw_response"] = content
            rec["usage"] = usage
            rec["attempts"] = att
            try:
                p = rx.parse_final_response(content)
                rec["status"] = "success"
                rec["reasoning_summary"] = p["reasoning_summary"]
                rec["is_fraud"] = p["is_fraud"]
                rec["confidence"] = p["confidence"]
            except Exception as exc:  # noqa: BLE001
                rec["status"] = "parse_error"
                rec["error"] = str(exc)
    except rx.FatalAPIError as exc:
        rec["status"] = "fatal_api_error"
        rec["error"] = str(exc)
        rec["fatal"] = True
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "api_error"
        rec["error"] = str(exc)
    rec["elapsed_s"] = round(time.monotonic() - t0, 3)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True,
                    choices=["e1_r2only", "e2_asc", "e3_sham", "e4_crisp",
                             "e5_main", "sample_carddisjoint"])
    ap.add_argument("--model", choices=["deepseek", "glm", "qwen", "kimi"], default="deepseek")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=200)
    args = ap.parse_args(argv)

    logger = rx.setup_logger()

    if args.exp == "sample_carddisjoint":
        sample_carddisjoint(seed=args.seed)
        return 0

    report = preflight(args.model, args.exp, logger)
    cfg = json.loads(CONFIGS[args.model].read_text(encoding="utf-8"))
    if args.dry_run:
        cfg["dry_run"] = True

    cards_path = CARDS_DISJOINT if args.exp == "e5_main" else CARDS_STANDARD
    samples = load_samples(cards_path)

    conditions = ["A", "C"] if args.exp == "e5_main" else ["C" if args.exp != "e2_asc" else "A-SC"]
    tag = {"e1_r2only": "C-R2only", "e2_asc": "A-SC", "e3_sham": "C-sham",
           "e4_crisp": "C-crisp", "e5_main": "E5"}[args.exp]

    for cond in conditions:
        cond_label = tag if args.exp != "e5_main" else cond
        # CRITICAL: dry runs must NEVER share a path with real runs, otherwise
        # already_done() would treat synthetic records as completed work.
        if args.dry_run:
            log_dir = ROOT / "logs" / "_dryrun"
        else:
            log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"supp_{args.exp}_{args.model}_{cond_label}.jsonl"
        done = already_done(log_path)
        pending = [s for s in samples if str(s["TransactionID"]) not in done]
        logger.info("%s | %s | %s | pending=%d done=%d -> %s",
                    args.exp, args.model, cond_label, len(pending), len(done), log_path.name)
        if not pending:
            continue
        n_calls = 0
        with log_path.open("a", encoding="utf-8") as f, \
                ThreadPoolExecutor(max_workers=int(cfg["concurrency"])) as pool:
            futs = {pool.submit(one_sample, cfg, logger, args.exp, cond_label, s, samples): s
                    for s in pending}
            aborted = None
            for i, fut in enumerate(as_completed(futs), 1):
                rec = fut.result()
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                n_calls += 4 if args.exp == "e2_asc" else 1
                if aborted is None and is_fatal_error(rec):
                    aborted = rec.get("error") or rec.get("status")
                    logger.error("FATAL: %s -- cancelling remaining queue", aborted)
                    for x in futs:
                        x.cancel()
                if i % 50 == 0 or i == len(pending):
                    logger.info("  %d/%d | calls~%d", i, len(pending), n_calls)
            if aborted is not None:
                raise SystemExit(
                    f"ABORTED on fatal API error ({aborted}). "
                    f"Partial log kept at {log_path.name}; re-run after fixing the account."
                )
    logger.info("DONE %s %s", args.exp, args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
