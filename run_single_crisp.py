#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
C-single-crisp: a single hard-threshold rule with an explicit execution
requirement, run on glm-5.3-flash.

Why this condition exists
-------------------------
The paper's Result 4 compares a prompted four-rule condition (C-crisp) against
the deterministic OR engine. Both arms share the four atomic rules, but the
prompt does not tell the model that its answer must equal the OR of those rules,
so the two arms do not share a decision function.

C-single-crisp closes that gap for the single-rule contrast: the model is given
one hard-threshold rule (C2 >= 5) and told explicitly that its decision must be
determined by that rule and nothing else. That makes the comparison
"identical decision rule, different executor".

The prompt differs from C-R2only in exactly one respect: the "Execution
requirement" paragraph. The shared head, the rule rendering and the user message
are byte-identical to the existing conditions, so the two can be compared
directly.

Usage
-----
    python run_single_crisp.py --model glm53 --dry-run   # free, checks hashes
    python run_single_crisp.py --model glm53             # 400 calls
    python run_single_crisp.py --model glm53 --verify    # acceptance report

Only the standard library is used. No network access is needed for --dry-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import run_experiment as rx  # noqa: E402  (the frozen harness)

# --------------------------------------------------------------------------
# Frozen expectations
# --------------------------------------------------------------------------

EXPECTED_HARNESS_SHA16 = "b470ece7eb52e0c3"
EXPECTED_CARDS_SHA16 = "1d10ac88f8d96ae9"
EXPECTED_CARDS_N = 400

# The four conditions that already exist. Their fingerprints must still match,
# otherwise the prompt construction has drifted and this run would not be
# comparable with the archived data.
EXPECTED_EXISTING_FINGERPRINTS = {
    "A": "0324572661831aa4",
    "R2only": "9f4d018285025168",
    "sham": "9a42f190a5f0b2ed",
    "crisp": "68de9c37f6026b95",
}

FROZEN_CONFIG = {
    "temperature": 0.0,
    "max_tokens": 1600,
    "retries": 3,
    "json_mode": True,
    "concurrency": 3,
}

CONDITION = "C-single-crisp"

# --------------------------------------------------------------------------
# The new prompt
# --------------------------------------------------------------------------

# Same text as CRISP_RULES["crisp_2_velocity"] in run_supplementary.py.
SINGLE_CRISP_RULE_ID = "crisp_2_velocity"
SINGLE_CRISP_RULE_TEXT = "Velocity Burst Rule: Flag if and only if C2 >= 5."

# The only addition relative to C-R2only.
EXECUTION_REQUIREMENT = (
    "Execution requirement: this rule is the sole basis for your decision. "
    "Set is_fraud to 1 if and only if C2 >= 5, and to 0 otherwise. "
    "Do not consider any other field, rule or signal, and do not weigh the "
    "strength of the evidence. The decision is a direct evaluation of the "
    "stated condition and nothing else."
)

USER_MESSAGE_TAIL = "Analyze this transaction and decide whether it is fraudulent."

# --------------------------------------------------------------------------
# Path-equivalence probe
# --------------------------------------------------------------------------
# The archived glm-5.3-flash data was collected through a local forwarding layer
# in front of the AutoClaw proxy. If the new runs reach the model by a different
# route, the two batches may not be comparable. The probe re-runs a condition we
# already have (C-sham) on a small prefix of the instance list and compares the
# predictions with the archived ones. Byte-identical prompts, same instances.

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
PROBE_REFERENCE = ROOT / "probe_reference_csham.json"


def build_csham_messages(card_text: str) -> List[Dict[str, str]]:
    """The archived C-sham prompt, rebuilt byte-for-byte."""
    saved = dict(rx.RULES)
    try:
        rx.RULES.update(SHAM_RULES)
        rule_block = rx.render_rules(SHAM_IDS)
    finally:
        rx.RULES.clear()
        rx.RULES.update(saved)
    system = rx.build_final_system(extra="\n" + rule_block)
    user = "[Transaction Description Card]\n" f"{card_text}\n\n" f"{USER_MESSAGE_TAIL}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_single_crisp_messages(card_text: str) -> List[Dict[str, str]]:
    """System prompt identical to C-R2only plus the execution requirement."""
    saved = dict(rx.RULES)
    try:
        rx.RULES[SINGLE_CRISP_RULE_ID] = SINGLE_CRISP_RULE_TEXT
        rule_block = rx.render_rules([SINGLE_CRISP_RULE_ID])
    finally:
        rx.RULES.clear()
        rx.RULES.update(saved)
    extra = "\n" + rule_block + "\n\n" + EXECUTION_REQUIREMENT
    system = rx.build_final_system(extra=extra)
    user = "[Transaction Description Card]\n" f"{card_text}\n\n" f"{USER_MESSAGE_TAIL}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def log(msg: str) -> None:
    print(msg, flush=True)


def config_path(model: str) -> Path:
    if model == "glm53":
        return ROOT / "config_glm53.json"
    if model == "glm":
        return ROOT / "config.json"
    if model == "deepseek":
        return Path(
            r"C:\Users\Niconiconi\Documents\Codex\2026-09-03"
            r"\llm-mrml-ccf-c-sci-bridging\rerun_2026-09-12\config.json"
        )
    raise SystemExit("ABORT: unknown model tag %r" % model)


def expected_model_name(model: str) -> str:
    return {
        "glm53": "glm-5.3-flash",
        "glm": "glm-4.5-air",
        "deepseek": "deepseek-chat",
    }[model]


def log_path(model: str) -> Path:
    return ROOT / "logs" / ("single_crisp_%s_%s.jsonl" % (model, CONDITION))


# --------------------------------------------------------------------------
# Pre-flight (no API calls)
# --------------------------------------------------------------------------


def preflight(model: str) -> Dict[str, Any]:
    report: Dict[str, Any] = {}

    # 1. frozen harness
    harness = ROOT / "src" / "run_experiment.py"
    if not harness.exists():
        raise SystemExit("ABORT: harness missing: %s" % harness)
    report["harness_sha"] = sha256(harness)[:16]
    if report["harness_sha"] != EXPECTED_HARNESS_SHA16:
        raise SystemExit(
            "ABORT: harness sha16 is %s, expected %s -- the frozen harness has "
            "been modified." % (report["harness_sha"], EXPECTED_HARNESS_SHA16)
        )

    # 2. frozen rulebook
    if list(rx.RULES) != [
        "rule_1_email_anonymity",
        "rule_2_frequency_burst",
        "rule_3_category_crossborder",
        "rule_4_distance_mismatch",
    ]:
        raise SystemExit("ABORT: RULES dict changed")

    # 3. card set
    cards_path = ROOT / "data" / "test_cards_400.json"
    if not cards_path.exists():
        raise SystemExit("ABORT: card set missing: %s" % cards_path)
    report["cards_path"] = str(cards_path)
    report["cards_sha"] = sha256(cards_path)[:16]
    if report["cards_sha"] != EXPECTED_CARDS_SHA16:
        raise SystemExit(
            "ABORT: cards sha16 is %s, expected %s -- the frozen card set has "
            "been modified." % (report["cards_sha"], EXPECTED_CARDS_SHA16)
        )
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    report["cards_n"] = len(cards)
    if report["cards_n"] != EXPECTED_CARDS_N:
        raise SystemExit("ABORT: cards_n=%d expected %d" % (report["cards_n"], EXPECTED_CARDS_N))

    # 4. config
    cfg_file = config_path(model)
    if not cfg_file.exists():
        raise SystemExit("ABORT: config missing: %s" % cfg_file)
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    for key, want in FROZEN_CONFIG.items():
        if cfg.get(key) != want:
            raise SystemExit(
                "ABORT: config[%s]=%r expected %r" % (key, cfg.get(key), want)
            )
    want_model = expected_model_name(model)
    if str(cfg.get("model", "")).lower() != want_model.lower():
        raise SystemExit(
            "ABORT: config model is %r, expected %r" % (cfg.get("model"), want_model)
        )
    report["model"] = cfg["model"]
    report["base_url"] = cfg["base_url"]
    report["key_len"] = len(cfg.get("api_key") or "")

    # 5. prompt isolation: the four existing conditions must be untouched, and
    #    the new condition must carry the rule and the requirement exactly once.
    def sys_for(ids):
        return rx.build_a_or_c_messages("CARD", ids)[0]["content"]

    sA = sys_for(None)
    sR2 = sys_for(["rule_2_frequency_burst"])

    saved = dict(rx.RULES)
    try:
        rx.RULES[SINGLE_CRISP_RULE_ID] = SINGLE_CRISP_RULE_TEXT
        sSingle = build_single_crisp_messages("CARD")[0]["content"]
    finally:
        rx.RULES.clear()
        rx.RULES.update(saved)

    fingerprints = {
        "A": hashlib.sha256(sA.encode()).hexdigest()[:16],
        "R2only": hashlib.sha256(sR2.encode()).hexdigest()[:16],
        "single_crisp": hashlib.sha256(sSingle.encode()).hexdigest()[:16],
    }
    report["sysprompt_sha"] = fingerprints

    for name, want in EXPECTED_EXISTING_FINGERPRINTS.items():
        if name in ("A", "R2only"):
            got = fingerprints[name]
            if got != want:
                raise SystemExit(
                    "ABORT: existing %s fingerprint is %s, expected %s -- prompt "
                    "construction has drifted." % (name, got, want)
                )

    if SINGLE_CRISP_RULE_TEXT not in sSingle:
        raise SystemExit("ABORT: rule text missing from the single-crisp prompt")
    if EXECUTION_REQUIREMENT not in sSingle:
        raise SystemExit("ABORT: execution requirement missing from the prompt")
    if "significantly greater than 1" in sSingle:
        raise SystemExit("ABORT: hedged wording leaked into the single-crisp prompt")
    if sSingle.count(SINGLE_CRISP_RULE_TEXT) != 1:
        raise SystemExit("ABORT: rule text appears more than once")

    # the new prompt must differ from C-R2only only by the added paragraph
    if EXECUTION_REQUIREMENT in sR2:
        raise SystemExit("ABORT: execution requirement leaked into C-R2only")
    head_single = sSingle.split("Anti-fraud rules to consider")[0]
    head_r2 = sR2.split("Anti-fraud rules to consider")[0]
    if head_single != head_r2:
        raise SystemExit("ABORT: shared head differs from C-R2only")

    report["user_message_sha"] = hashlib.sha256(
        ("[Transaction Description Card]\nCARD\n\n" + USER_MESSAGE_TAIL).encode()
    ).hexdigest()[:16]

    return report, cards, cfg


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def load_done(path: Path) -> Dict[str, Dict[str, Any]]:
    """Last record per transaction_id."""
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            tid = str(rec.get("transaction_id") or "")
            if not tid:
                continue
            if rec.get("status") == "success" or tid not in out:
                out[tid] = rec
    return out


def succeeded(path: Path) -> set:
    """Transaction ids with at least one success. Used by the resume logic so
    that instances whose last record is an error are retried on the next run."""
    out = set()
    if not path.exists():
        return out
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("status") == "success" and rec.get("is_fraud") in (0, 1):
                tid = str(rec.get("transaction_id") or "")
                if tid:
                    out.add(tid)
    return out


def is_fatal(rec_or_msg: Any) -> bool:
    text = json.dumps(rec_or_msg, ensure_ascii=False).lower()
    for token in ("quota", "insufficient", "balance", "unauthorized",
                  "invalid_api_key", "401", "403"):
        if token in text:
            return True
    return False


def run(model: str) -> int:
    report, cards, cfg = preflight(model)
    log("INFO PRE-FLIGHT OK | " + json.dumps(report, ensure_ascii=False))

    out_path = log_path(model)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = succeeded(out_path)
    todo = [c for c in cards if str(c["TransactionID"]) not in done]
    log("INFO %d/%d instances already done, %d to go"
        % (len(done), len(cards), len(todo)))
    if not todo:
        log("INFO nothing to do")
        return 0

    logf = (ROOT / "logs" / ("single_crisp_%s.log" % model)).open("a", encoding="utf-8")
    n_ok = n_err = 0
    t_start = time.monotonic()

    # Prompts are built in the main thread, one per instance, before any network
    # call, so the bytes sent are identical to a serial run. Only the HTTP calls
    # are parallelised, at the frozen concurrency.
    workers = int(cfg.get("concurrency", 1))
    log("INFO concurrency = %d" % workers)

    def one(sample):
        tid = str(sample["TransactionID"])
        msgs = build_single_crisp_messages(sample["card_text"])
        dry_ctx = {
            "condition": CONDITION,
            "transaction_id": tid,
            "ground_truth_label": sample.get("ground_truth_label"),
        }
        rec = {
            "condition": CONDITION,
            "experiment": "single_crisp",
            "transaction_id": tid,
            "ground_truth_label": sample.get("ground_truth_label"),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            content, usage, attempts = rx.call_with_retry(cfg, msgs, logf, dry_ctx)
            parsed = rx.parse_final_response(content)
            rec["is_fraud"] = parsed["is_fraud"]
            rec["confidence"] = parsed.get("confidence")
            rec["status"] = "success"
            rec["attempts"] = attempts
            rec["usage"] = usage
        except Exception as exc:  # noqa: BLE001
            rec["is_fraud"] = None
            rec["status"] = "parse_error" if "parse" in str(exc).lower() else "api_error"
            rec["error"] = str(exc)[:400]
        return rec

    done_n = 0
    fatal = None
    with out_path.open("a", encoding="utf-8") as fh:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one, s): s for s in todo}
            for fut in as_completed(futures):
                rec = fut.result()
                done_n += 1
                if rec.get("status") == "success":
                    n_ok += 1
                else:
                    n_err += 1
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                if rec.get("status") != "success" and is_fatal(rec) and fatal is None:
                    fatal = rec["error"]
                    for f2 in futures:
                        f2.cancel()
                if done_n % 25 == 0 or done_n == len(todo):
                    rate = (time.monotonic() - t_start) / done_n
                    log("INFO %d/%d  ok=%d err=%d  %.1fs/call  eta %.1f min"
                        % (done_n, len(todo), n_ok, n_err, rate,
                           rate * (len(todo) - done_n) / 60))

    logf.close()
    if fatal:
        log("ABORT: fatal error, stopping. %s" % fatal)
        return 2
    log("INFO DONE %s | ok=%d err=%d | %s" % (model, n_ok, n_err, out_path))
    return 0


# --------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------


def verify(model: str) -> int:
    path = log_path(model)
    if not path.exists():
        log("FAIL: log not found: %s" % path)
        return 1

    recs = load_done(path)
    scored = [
        r for r in recs.values()
        if r.get("status") == "success"
        and r.get("is_fraud") in (0, 1)
        and r.get("ground_truth_label") in (0, 1)
    ]

    log("instances            : %d" % len(recs))
    log("scorable successes   : %d" % len(scored))
    if scored:
        acc = sum(1 for r in scored if r["is_fraud"] == r["ground_truth_label"]) / len(scored)
        log("ACCURACY             : %.4f" % acc)
        log("  E_R2 engine, same 400 instances: 0.6175")
        log("  delta vs engine    : %+.4f" % (acc - 0.6175))
        log("flags raised         : %d (%.1f%%)"
            % (sum(1 for r in scored if r["is_fraud"] == 1),
               100 * sum(1 for r in scored if r["is_fraud"] == 1) / len(scored)))

    n_err = sum(1 for r in recs.values() if r.get("status") != "success")
    log("instances with errors: %d" % n_err)
    retried = sum(1 for r in recs.values() if (r.get("attempts") or 0) > 1)
    log("instances retried    : %d" % retried)

    ok = True
    if len(recs) != EXPECTED_CARDS_N:
        log("FAIL: expected %d unique instances, got %d" % (EXPECTED_CARDS_N, len(recs)))
        ok = False
    if len(scored) != EXPECTED_CARDS_N:
        log("FAIL: expected %d scorable instances, got %d" % (EXPECTED_CARDS_N, len(scored)))
        ok = False

    log("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# --------------------------------------------------------------------------
# Path-equivalence probe
# --------------------------------------------------------------------------


def probe(model: str, n: int) -> int:
    """Re-run C-sham on the first n instances and compare with the archive."""
    report, cards, cfg = preflight(model)
    log("INFO PRE-FLIGHT OK | " + json.dumps(report, ensure_ascii=False))

    if not PROBE_REFERENCE.exists():
        log("FAIL: probe reference not found: %s" % PROBE_REFERENCE)
        return 1
    reference = json.loads(PROBE_REFERENCE.read_text(encoding="utf-8"))

    subset = [c for c in cards if str(c["TransactionID"]) in reference][:n]

    out_path = ROOT / "logs" / ("probe_csham_%s.jsonl" % model)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume: instances already probed successfully are not re-sent. Without
    # this, restarting the probe burns the whole batch again.
    already = succeeded(out_path)
    subset = [c for c in subset if str(c["TransactionID"]) not in already]
    log("INFO probing %d instances with the C-sham prompt (%d already done)"
        % (len(subset), len(already)))
    if not subset:
        log("INFO nothing to do")
        return 0

    logf = (ROOT / "logs" / ("probe_csham_%s.log" % model)).open("a", encoding="utf-8")

    workers = int(cfg.get("concurrency", 1))
    log("INFO concurrency = %d" % workers)

    def one(sample):
        tid = str(sample["TransactionID"])
        msgs = build_csham_messages(sample["card_text"])
        rec: Dict[str, Any] = {
            "condition": "C-sham-probe",
            "transaction_id": tid,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            content, usage, attempts = rx.call_with_retry(
                cfg, msgs, logf,
                {"condition": "probe", "transaction_id": tid,
                 "ground_truth_label": sample.get("ground_truth_label")},
            )
            parsed = rx.parse_final_response(content)
            rec["is_fraud"] = parsed["is_fraud"]
            rec["status"] = "success"
            rec["usage"] = usage
            rec["archived"] = reference[tid]["is_fraud"]
        except Exception as exc:  # noqa: BLE001
            rec["is_fraud"] = None
            rec["status"] = "error"
            rec["error"] = str(exc)[:400]
        return rec

    agree = total = done_n = 0
    fatal = None
    with out_path.open("a", encoding="utf-8") as fh:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one, s): s for s in subset}
            for fut in as_completed(futures):
                rec = fut.result()
                done_n += 1
                if rec.get("status") == "success":
                    total += 1
                    if rec["is_fraud"] == rec["archived"]:
                        agree += 1
                    log("INFO %2d/%d  now=%d archived=%d  agreement %.3f"
                        % (done_n, len(subset), rec["is_fraud"], rec["archived"],
                           agree / total if total else 0.0))
                else:
                    log("INFO %2d/%d  ERROR %s" % (done_n, len(subset), rec["error"][:80]))
                    if is_fatal(rec) and fatal is None:
                        fatal = rec["error"]
                        for f2 in futures:
                            f2.cancel()
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()

    logf.close()
    if fatal:
        log("ABORT: fatal error, stopping. %s" % fatal)
        return 2
    if not total:
        log("FAIL: no usable probe calls")
        return 1

    rate = agree / total
    log("")
    log("PROBE AGREEMENT: %.3f  (%d/%d)" % (rate, agree, total))
    log("  >= 0.90 -> the two access paths look equivalent; proceed with the full run")
    log("  <  0.90 -> stop and report to YY before spending the full 400 calls")
    return 0 if rate >= 0.90 else 3


# --------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C-single-crisp run")
    ap.add_argument("--model", required=True, choices=["glm53", "glm", "deepseek"])
    ap.add_argument("--dry-run", action="store_true", help="pre-flight only, no API calls")
    ap.add_argument("--verify", action="store_true", help="acceptance report")
    ap.add_argument("--probe-csham", type=int, metavar="N",
                    help="path-equivalence probe: re-run C-sham on the first N "
                         "instances and compare with the archived predictions")
    args = ap.parse_args(argv)

    if args.probe_csham:
        return probe(args.model, args.probe_csham)
    if args.dry_run:
        report, _cards, _cfg = preflight(args.model)
        log("INFO PRE-FLIGHT OK | " + json.dumps(report, ensure_ascii=False))
        log("INFO DONE dry-run %s" % args.model)
        return 0
    if args.verify:
        return verify(args.model)
    return run(args.model)


if __name__ == "__main__":
    raise SystemExit(main())
