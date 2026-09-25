# -*- coding: utf-8 -*-
"""Condition C-neutral: a base-rate-only control.

The referee's objection to the sham condition is that its rules name real fields
(amount, email domain) and use fraud-flavoured wording, so any effect it has
could be attention to those fields rather than an operating-point shift. This
condition isolates the latter: it is byte-identical to Condition A except for
one factual sentence stating the base rate, with no rules, no field names and no
heuristic of any kind.

Interpretation: compare against Condition A on the same instances.
  - if the operating point moves anyway, the shift needs no field naming;
  - if it does not, the shift in the sham arm is attributable to what the sham
    rules name.

Run on the two models that carry the 2x2 (deepseek-v4.1-Flash and glm-4.5-air),
because those are the only models with a Condition A on the standard 400.
"""
import argparse, hashlib, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import run_experiment as rx  # noqa: E402

CONFIGS = {
    "deepseek": ROOT / "config.deepseek.json",
    "glm": ROOT / "config.json",
}
CARDS_PATH = ROOT / "data" / "test_cards_400.json"

EXPECTED_HARNESS_SHA16 = "b470ece7eb52e0c3"
EXPECTED_CARDS_SHA16 = "1d10ac88f8d96ae9"
EXPECTED_CARDS_N = 400
EXPECTED_FINGERPRINTS = {
    "A": "0324572661831aa4",
    "R2only": "9f4d018285025168",
    "sham": "9a42f190a5f0b2ed",
    "crisp": "68de9c37f6026b95",
}
FROZEN_CONFIG = {"temperature": 0.0, "max_tokens": 1600, "retries": 3,
                 "json_mode": True, "concurrency": 3}
MODEL_NAMES = {"deepseek": "deepseek-chat", "glm": "glm-4.5-air"}

CONDITION = "C-neutral"
BASE_RATE = ("Dataset context: approximately half of the transactions in this "
             "set are fraudulent.")
USER_TAIL = "Analyze this transaction and decide whether it is fraudulent."


def build_neutral_messages(card_text: str) -> List[Dict[str, str]]:
    """Condition A plus one base-rate sentence, and nothing else."""
    system = rx.build_final_system(extra="\n" + BASE_RATE)
    user = "[Transaction Description Card]\n" + card_text + "\n\n" + USER_TAIL
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def log_path(model: str) -> Path:
    return ROOT / "logs" / ("neutral_%s_%s.jsonl" % (model, CONDITION))


def preflight(model: str) -> Dict[str, Any]:
    rep: Dict[str, Any] = {}
    harness = ROOT / "src" / "run_experiment.py"
    rep["harness_sha"] = sha16(harness)
    if rep["harness_sha"] != EXPECTED_HARNESS_SHA16:
        raise SystemExit("ABORT: harness has changed (%s)" % rep["harness_sha"])

    rep["cards_sha"] = sha16(CARDS_PATH)
    cards = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
    rep["cards_n"] = len(cards)
    if rep["cards_sha"] != EXPECTED_CARDS_SHA16 or rep["cards_n"] != EXPECTED_CARDS_N:
        raise SystemExit("ABORT: card set has changed")

    cfg_file = CONFIGS[model]
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    for k, want in FROZEN_CONFIG.items():
        if cfg.get(k) != want:
            raise SystemExit("ABORT: config[%s]=%r expected %r" % (k, cfg.get(k), want))
    if str(cfg.get("model", "")).lower() != MODEL_NAMES[model].lower():
        raise SystemExit("ABORT: config model is %r" % cfg.get("model"))
    rep["model"] = cfg["model"]
    rep["base_url"] = cfg["base_url"]

    # the four existing conditions must still be byte-identical
    sA = rx.build_final_system(extra="")
    sR2 = rx.build_a_or_c_messages("CARD", ["rule_2_frequency_burst"])[0]["content"]
    fp = {"A": hashlib.sha256(sA.encode()).hexdigest()[:16],
          "R2only": hashlib.sha256(sR2.encode()).hexdigest()[:16]}
    for name, want in EXPECTED_FINGERPRINTS.items():
        if name in fp and fp[name] != want:
            raise SystemExit("ABORT: %s fingerprint drifted" % name)
    rep["fingerprints"] = fp

    # the new prompt must differ from A by exactly the base-rate sentence
    sN = build_neutral_messages("CARD")[0]["content"]
    if sN.replace("\n" + BASE_RATE, "", 1) != sA:
        raise SystemExit("ABORT: neutral prompt is not A plus the base-rate sentence")
    if sN.count(BASE_RATE) != 1:
        raise SystemExit("ABORT: base-rate sentence appears more than once")
    for bad in ("Anti-fraud rules", "Flag if", "C2", "rule"):
        if bad.lower() in sN.lower():
            raise SystemExit("ABORT: %r leaked into the neutral prompt" % bad)
    rep["neutral_sha"] = hashlib.sha256(sN.encode()).hexdigest()[:16]
    rep["added_chars"] = len(sN) - len(sA)
    print("PRE-FLIGHT OK | " + json.dumps(rep, ensure_ascii=False))
    return rep, cards, cfg


def load_done(path: Path) -> set:
    done = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
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


def is_fatal(msg: str) -> bool:
    m = str(msg).lower()
    return any(k in m for k in ("余额不足", "insufficient", "quota", "无可用资源包",
                                "arrears", "欠费", "invalid api key", "unauthorized",
                                "401", "authentication"))


def one(cfg, logger, sample, model):
    tid = str(sample["TransactionID"])
    rec = {"condition": CONDITION, "experiment": "e6_neutral",
           "transaction_id": tid,
           "ground_truth_label": sample.get("ground_truth_label"),
           "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        msgs = build_neutral_messages(sample["card_text"])
        content, usage, att = rx.call_with_retry(cfg, msgs, logger, rec)
        p = rx.parse_final_response(content)
        rec.update({"raw_response": content, "usage": usage, "attempts": att,
                    "confidence": p.get("confidence"),
                    "reasoning_summary": p.get("reasoning_summary"),
                    "is_fraud": p.get("is_fraud"), "status": "success"})
    except Exception as exc:  # noqa: BLE001
        rec.update({"is_fraud": None, "status": "api_error", "error": str(exc)[:400]})
    return rec


def run(model: str) -> int:
    rep, cards, cfg = preflight(model)
    out = log_path(model)
    done = load_done(out)
    pending = [s for s in cards if str(s["TransactionID"]) not in done]
    print("%s | %s | pending=%d done=%d -> %s"
          % (model, CONDITION, len(pending), len(done), out.name))
    if not pending:
        return 0
    import logging
    logger = logging.getLogger(model)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
        logger.addHandler(h)
    with out.open("a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=int(cfg["concurrency"])) as pool:
        futs = {pool.submit(one, cfg, logger, s, model): s for s in pending}
        aborted = None
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            if aborted is None and r.get("status") != "success" and is_fatal(r.get("error")):
                aborted = r.get("error")
                logger.error("FATAL: %s -- cancelling", str(aborted)[:120])
                for x in futs:
                    x.cancel()
            if i % 50 == 0 or i == len(pending):
                logger.info("  %d/%d", i, len(pending))
        if aborted is not None:
            raise SystemExit("ABORTED on fatal API error. Partial log kept at %s" % out.name)
    print("DONE %s %s" % (model, CONDITION))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["deepseek", "glm"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        preflight(a.model)
        print("DRY RUN: nothing executed")
        return 0
    return run(a.model)


if __name__ == "__main__":
    raise SystemExit(main())
