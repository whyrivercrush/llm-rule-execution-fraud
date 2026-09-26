#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recompute.py — regenerate every number reported in the manuscript from the
archived logs, and check each one against the value printed in the paper.

No network access, no API key, no third-party packages: it reads
`logs/` and `data/` and writes `results/recomputed/`.

Usage
-----
    python recompute.py              # verify and print a report
    python recompute.py --quiet      # only print mismatches
    python recompute.py --json       # also dump machine-readable results

Exit status is 0 if every checked value matches the manuscript, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
DATA = ROOT / "data"
OUT = ROOT / "results" / "recomputed"

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 2026

# --------------------------------------------------------------------------
# Card parsing (for the deterministic engines)
# --------------------------------------------------------------------------

_NUM = r"(-?\d+(?:\.\d+)?)"


def parse_card(text: str) -> Dict[str, Any]:
    """Parse the serialized card text into the fields the rules need."""
    f: Dict[str, Any] = {}

    m = re.search(r"product category '([^']*)'", text)
    f["product_cd"] = m.group(1) if m else None

    m = re.search(r"Card Country:\s*" + _NUM, text)
    f["card3"] = float(m.group(1)) if m else None

    m = re.search(r"country code:\s*" + _NUM, text)
    f["addr2"] = float(m.group(1)) if m else None

    m = re.search(r"Purchaser email domain:\s*'([^']*)'", text)
    f["p_email"] = m.group(1) if m else None

    m = re.search(r"Address distance offset:\s*" + _NUM, text)
    f["dist1"] = float(m.group(1)) if m else None

    m = re.search(r"C1=" + _NUM + r",\s*C2=" + _NUM, text)
    if m:
        f["C1"] = float(m.group(1))
        f["C2"] = float(m.group(2))
    else:
        f["C1"] = f["C2"] = None

    for flag in ("M1", "M2", "M4", "M6"):
        m = re.search(flag + r"=([A-Za-z0-9]+)", text)
        f[flag] = m.group(1) if m else None

    return f


# --------------------------------------------------------------------------
# Deterministic engines
# --------------------------------------------------------------------------

def rule1_email(f: Dict[str, Any]) -> bool:
    """Missing purchaser email domain, or an anonymous/throwaway domain."""
    dom = f.get("p_email")
    if dom is None:
        return True
    return dom.strip().lower() in {"", "not provided", "anonymous.com"}


def rule2_velocity(f: Dict[str, Any]) -> bool:
    """C2 >= 5 (the engine implements only this threshold)."""
    c2 = f.get("C2")
    return c2 is not None and c2 >= 5


def rule3_category(f: Dict[str, Any]) -> bool:
    """Product category 'C' with a non-domestic card country."""
    if f.get("product_cd") != "C":
        return False
    c3, a2 = f.get("card3"), f.get("addr2")
    if c3 is None or a2 is None:
        return False
    return c3 != a2


def rule4_distance(f: Dict[str, Any]) -> bool:
    """dist1 >= 100, or any of M1/M2/M6 flagged 'F'."""
    d = f.get("dist1")
    if d is not None and d >= 100:
        return True
    return any((f.get(k) or "").upper() == "F" for k in ("M1", "M2", "M6"))


def engine_r2(f: Dict[str, Any]) -> int:
    """Single best rule only."""
    return int(rule2_velocity(f))


def engine_or(f: Dict[str, Any]) -> int:
    """Disjunction of all four rules."""
    return int(any(r(f) for r in (rule1_email, rule2_velocity,
                                  rule3_category, rule4_distance)))


# --------------------------------------------------------------------------
# Sham rules (the zero-call control of Sec 4.1)
# --------------------------------------------------------------------------

_AMOUNT = re.compile(r"Order amount is \$([0-9.]+) USD")


def amount_string(text: str, mode: str = "rendered") -> Optional[str]:
    """The order amount as the card renders it, or rounded to two decimals."""
    m = _AMOUNT.search(text)
    if not m:
        return None
    return m.group(1) if mode == "rendered" else "%.2f" % float(m.group(1))


def sham_rules(text: str, tid: str, amount_mode: str = "rendered",
               domain_mode: str = "full") -> Tuple[bool, bool, bool, bool]:
    """The four fraud-free rules of C-sham as deterministic predicates.

    S1  transaction identifier ends in an odd digit while the integer part of
        the amount is even
    S2  the amount contains a repeated digit in succession
    S3  the amount lies within ten dollars below a multiple of one hundred
    S4  the purchaser email domain has a character count divisible by three

    Two operationalisations of the wording are defensible and the manuscript
    reports the sensitivity across them (Sec 4.1):

      amount_mode  'rendered'    the amount substring as printed on the card
                   '2dp'         the same value rounded to two decimals
      domain_mode  'full'        every character of the rendered domain
                   'firstlabel'  only the part before the first dot
    """
    amt_s = amount_string(text, amount_mode)
    amt = float(amt_s) if amt_s is not None else None

    dom = (parse_card(text).get("p_email") or "").strip().lower()
    if domain_mode == "firstlabel":
        dom = dom.split(".")[0]

    s1 = (int(str(tid)[-1]) % 2 == 1) and (amt is not None and int(amt) % 2 == 0)
    s2 = bool(amt_s) and any(amt_s[i] == amt_s[i + 1] and amt_s[i].isdigit()
                             for i in range(len(amt_s) - 1))
    s3 = amt is not None and (amt % 100) >= 90
    s4 = len(dom) > 0 and (len(dom) % 3 == 0)
    return s1, s2, s3, s4


def engine_sham(text: str, tid: str, **kw) -> int:
    """Disjunction of the four sham rules. Requires no model calls."""
    return int(any(sham_rules(text, tid, **kw)))


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def confusion(y_true: Sequence[int], y_pred: Sequence[int]) -> Tuple[int, int, int, int]:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    return tp, fp, tn, fn


def metrics(y_true: Sequence[int], y_pred: Sequence[int]) -> Dict[str, float]:
    tp, fp, tn, fn = confusion(y_true, y_pred)
    n = len(y_true)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0

    def f1(p: float, r: float) -> float:
        return 0.0 if (p + r) == 0 else 2 * p * r / (p + r)

    return {
        "accuracy": (tp + tn) / n if n else float("nan"),
        "precision": prec,
        "recall": rec,
        "specificity": spec,
        "macro_f1": (f1(prec, rec) + f1(spec, tn / (tn + fn) if (tn + fn) else 0.0)) / 2,
        "flags": int(sum(y_pred)),
    }


def mcnemar_exact(correct_a: Sequence[bool],
                  correct_b: Sequence[bool]) -> Dict[str, Any]:
    """Two-sided exact McNemar (binomial on discordant pairs)."""
    b = sum(1 for x, y in zip(correct_a, correct_b) if x and not y)
    c = sum(1 for x, y in zip(correct_a, correct_b) if (not x) and y)
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "p": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {"b": b, "c": c, "p": min(1.0, 2.0 * tail)}


def holm(pvals: Sequence[float]) -> List[float]:
    """Holm-Bonferroni adjusted p-values."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        val = (m - rank) * pvals[i]
        running = max(running, val)
        adj[i] = min(1.0, running)
    return adj


def paired_bootstrap_ci(y_true: Sequence[int],
                        pred_a: Sequence[int],
                        pred_b: Sequence[int],
                        n_boot: int = BOOTSTRAP_N,
                        seed: int = BOOTSTRAP_SEED) -> Dict[str, float]:
    """95% paired bootstrap CI on accuracy(A) - accuracy(B)."""
    rng = random.Random(seed)
    n = len(y_true)
    deltas: List[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        ya = [y_true[i] for i in idx]
        pa = [pred_a[i] for i in idx]
        pb = [pred_b[i] for i in idx]
        da = sum(1 for t, p in zip(ya, pa) if t == p) / n
        db = sum(1 for t, p in zip(ya, pb) if t == p) / n
        deltas.append(da - db)
    deltas.sort()

    def pct(q: float) -> float:
        return deltas[min(len(deltas) - 1, max(0, int(round(q * (len(deltas) - 1)))))]

    return {"delta": sum(deltas) / len(deltas), "lo": pct(0.025), "hi": pct(0.975)}


def cluster_bootstrap_ci(y_true: Sequence[int],
                         pred_a: Sequence[int],
                         pred_b: Sequence[int],
                         groups: Dict[Any, List[int]],
                         n_boot: int = BOOTSTRAP_N,
                         seed: int = BOOTSTRAP_SEED) -> Dict[str, float]:
    """95% CI on accuracy(A) - accuracy(B), resampling entities (card1 clusters)
    rather than rows.

    Rows within a card are not independent (SI Statistical protocol: 400 instances, 250 cards),
    so the row-level paired bootstrap understates uncertainty. Clusters are
    drawn with replacement until the original number of clusters is reached.
    """
    rng = random.Random(seed)
    keys = list(groups)
    n_keys = len(keys)
    deltas: List[float] = []
    for _ in range(n_boot):
        num = den = num_a = 0
        for _ in range(n_keys):
            for i in groups[keys[rng.randrange(n_keys)]]:
                den += 1
                if y_true[i] == pred_a[i]:
                    num += 1
                if y_true[i] == pred_b[i]:
                    num_a += 1
        deltas.append((num - num_a) / den if den else 0.0)
    deltas.sort()

    def pct(q: float) -> float:
        return deltas[min(len(deltas) - 1, max(0, int(round(q * (len(deltas) - 1)))))]

    return {"delta": (sum(1 for t, a in zip(y_true, pred_a) if t == a)
                      - sum(1 for t, b in zip(y_true, pred_b) if t == b)) / len(y_true),
            "lo": pct(0.025), "hi": pct(0.975)}


def signflip_permutation_p(contrasts: Sequence[float],
                           n_perm: int = 100000,
                           seed: int = BOOTSTRAP_SEED) -> float:
    """Two-sided sign-flip permutation test on the mean of per-instance contrasts."""
    rng = random.Random(seed)
    n = len(contrasts)
    observed = sum(contrasts) / n
    hits = 0
    for _ in range(n_perm):
        mask = rng.getrandbits(n)
        s = sum(-c if (mask >> i) & 1 else c for i, c in enumerate(contrasts)) / n
        if abs(s) >= abs(observed) - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


# --------------------------------------------------------------------------
# Log loading
# --------------------------------------------------------------------------

def load_log(path: Path) -> Dict[str, Dict[str, Any]]:
    """Return {transaction_id: record}, keeping the last successful record."""
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = str(rec.get("transaction_id") or "")
            if not tid:
                continue
            if rec.get("status") == "success" or tid not in out:
                out[tid] = rec
    return out


def scored(recs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in recs.values()
            if r.get("status") == "success"
            and r.get("ground_truth_label") in (0, 1)
            and r.get("is_fraud") in (0, 1)]


def predictions(recs: Dict[str, Dict[str, Any]]) -> Tuple[List[int], List[int]]:
    rows = sorted(scored(recs), key=lambda r: str(r["transaction_id"]))
    return ([int(r["ground_truth_label"]) for r in rows],
            [int(r["is_fraud"]) for r in rows])


def aligned(recs_a: Dict[str, Dict[str, Any]],
            recs_b: Dict[str, Dict[str, Any]]
            ) -> Optional[Tuple[List[int], List[int], List[int]]]:
    """Align two conditions on their common scored transaction ids."""
    sa = {str(r["transaction_id"]): r for r in scored(recs_a)}
    sb = {str(r["transaction_id"]): r for r in scored(recs_b)}
    common = sorted(set(sa) & set(sb), key=lambda x: int(x) if x.isdigit() else 0)
    if not common:
        return None
    yt = [int(sa[t]["ground_truth_label"]) for t in common]
    pa = [int(sa[t]["is_fraud"]) for t in common]
    pb = [int(sb[t]["is_fraud"]) for t in common]
    return yt, pa, pb


# --------------------------------------------------------------------------
# Verification bookkeeping
# --------------------------------------------------------------------------

class Checker:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def check(self, label: str, got: Optional[float], want: float,
              tol: float = 0.0005, note: str = "") -> None:
        if got is None:
            ok = False
            shown = "n/a"
        else:
            ok = abs(got - want) <= tol
            shown = "%.4f" % got
        self.rows.append({"label": label, "expected": want, "got": got,
                          "shown": shown, "ok": ok, "note": note})

    def flag(self, label: str, ok: bool, want: bool = True, note: str = "") -> None:
        """For claims that are qualitative (e.g. 'the interval excludes zero')."""
        self.rows.append({"label": label, "expected": 1.0 if want else 0.0,
                          "got": 1.0 if ok else 0.0,
                          "shown": "yes" if ok else "no",
                          "ok": ok == want, "note": note})

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.rows if r["ok"])

    @property
    def n_total(self) -> int:
        return len(self.rows)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quiet", action="store_true", help="print only mismatches")
    ap.add_argument("--json", action="store_true", help="dump JSON results")
    args = ap.parse_args(argv)

    C = Checker()
    computed: Dict[str, Any] = {}

    # ---------------------------------------------------------------- engines
    cards = json.loads((DATA / "test_cards_400.json").read_text(encoding="utf-8"))
    parsed = [(r["TransactionID"], int(r["ground_truth_label"]),
               parse_card(r["card_text"])) for r in cards]
    yt_e = [t for _, t, _ in parsed]
    pred_r2 = [engine_r2(f) for _, _, f in parsed]
    pred_or = [engine_or(f) for _, _, f in parsed]

    m_r2, m_or = metrics(yt_e, pred_r2), metrics(yt_e, pred_or)
    computed["engine_r2"] = m_r2
    computed["engine_or"] = m_or

    # Engine predictions keyed by instance, for the per-model crisp-vs-engine
    # comparison of the SI table.
    ENG_LABEL = {t: y for t, y, _ in parsed}
    ENG_R2 = {t: p for (t, _, _), p in zip(parsed, pred_r2)}
    ENG_OR = {t: p for (t, _, _), p in zip(parsed, pred_or)}

    C.check("Table 2  E_R2 accuracy", m_r2["accuracy"], 0.6175)
    C.check("Table 2  E_OR accuracy", m_or["accuracy"], 0.5650)

    # ------------------------------------------------------- deepseek main 2x2
    # Run 2 is the execution whose numbers Table 5 reports.
    ds = {c: load_log(LOGS / "deepseek" / ("main_run2_condition_%s.jsonl" % c))
          for c in "ABCD"}
    ds_metrics = {c: metrics(*predictions(ds[c])) for c in "ABCD"}
    computed["deepseek_main_run2"] = ds_metrics

    for cond, want in (("A", 0.4875), ("B", 0.4425), ("C", 0.5825), ("D", 0.5375)):
        C.check("Table 5  DeepSeek %s accuracy (Run 2)" % cond,
                ds_metrics[cond]["accuracy"], want)

    # Run 1, reported for the cross-execution comparison in the robustness section.
    ds1 = {c: load_log(LOGS / "deepseek" / ("main_run1_condition_%s.jsonl" % c))
           for c in "ACD"}
    ds1["B"] = load_log(LOGS / "deepseek" / "main_run1_condition_B_corrected.jsonl")
    ds1_metrics = {c: metrics(*predictions(ds1[c])) for c in "ABCD"}
    computed["deepseek_main_run1"] = ds1_metrics

    for cond, want in (("A", 0.4875), ("B", 0.4475), ("C", 0.5950), ("D", 0.5500)):
        C.check("SI Robustness  DeepSeek %s accuracy (Run 1)" % cond,
                ds1_metrics[cond]["accuracy"], want)

    # ------------------------------------------------------------ deepseek controls
    controls = {
        "C-R2only": "supp_C-R2only.jsonl",
        "C-sham":   "supp_C-sham.jsonl",
        "C-crisp":  "supp_C-crisp.jsonl",
        "A-SC":     "supp_A-SC.jsonl",
    }
    ctl = {}
    for name, fn in controls.items():
        recs = load_log(LOGS / "deepseek" / fn)
        ctl[name] = metrics(*predictions(recs))
        ctl[name + "__recs"] = recs
    computed["deepseek_controls"] = {k: v for k, v in ctl.items() if not k.endswith("__recs")}

    C.check("Table 2  C-R2only accuracy", ctl["C-R2only"]["accuracy"], 0.6075)
    C.check("Table 2  C-sham accuracy",   ctl["C-sham"]["accuracy"],   0.5725)
    C.check("Table 2  C-crisp accuracy",  ctl["C-crisp"]["accuracy"],  0.5400)

    # ------------------------------------------------------------- glm-4.5-air
    glm = {c: load_log(LOGS / "glm-4.5-air" / ("main_condition_%s.jsonl" % c))
           for c in "ABCD"}
    glm_metrics = {c: metrics(*predictions(glm[c])) for c in "ABCD"}
    computed["glm_45_air"] = glm_metrics

    for cond, want in (("A", 0.4300), ("B", 0.4925), ("C", 0.5950), ("D", 0.5625)):
        C.check("Table 5  glm-4.5-air %s accuracy" % cond,
                glm_metrics[cond]["accuracy"], want)

    # ----------------------------------------------------------- glm-5.3-flash
    f53 = {
        "C-sham":   "supp_C-sham.jsonl",
        "C-crisp":  "supp_C-crisp.jsonl",
        "C-R2only": "supp_C-R2only.jsonl",
        "A":        "main_A.jsonl",
        "C":        "main_C.jsonl",
    }
    f53_metrics, f53_recs = {}, {}
    for name, fn in f53.items():
        recs = load_log(LOGS / "glm-5.3-flash" / fn)
        f53_recs[name] = recs
        f53_metrics[name] = metrics(*predictions(recs))
    computed["glm_53_flash"] = f53_metrics

    C.check("Table 3  glm-5.3-flash C-sham accuracy", f53_metrics["C-sham"]["accuracy"], 0.5050)
    C.check("Sec 4.1  glm-5.3-flash C accuracy (card-disjoint)",
            f53_metrics["C"]["accuracy"], 0.5550)
    C.check("Sec 4.1  glm-5.3-flash A accuracy (card-disjoint)",
            f53_metrics["A"]["accuracy"], 0.5075)

    # ------------------------------------------- the remaining three models
    # glm-4.5-air received the supplementary controls on 2026-09-20; qwen3-max and
    # kimi-k2.6 received the sham and de-hedged controls only. Together with
    # deepseek-chat and glm-5.3-flash these give the five-model Table 3.
    extra = {
        "glm-4.5-air": {
            "C-sham":   "supp_C-sham.jsonl",
            "C-crisp":  "supp_C-crisp.jsonl",
            "C-R2only": "supp_C-R2only.jsonl",
        },
        "qwen3-max": {
            "C-sham":  "supp_C-sham.jsonl",
            "C-crisp": "supp_C-crisp.jsonl",
        },
        "kimi-k2.6": {
            "C-sham":  "supp_C-sham.jsonl",
            "C-crisp": "supp_C-crisp.jsonl",
        },
    }
    extra_recs = {}
    for model, spec in extra.items():
        extra_recs[model] = {}
        for name, fn in spec.items():
            extra_recs[model][name] = load_log(LOGS / model / fn)

    # ------------------------------------------------- grok-4.3 (frontier model)
    # Added on the frozen prompts of the supplementary controls. The C-crisp and
    # C-single-crisp archives keep their api_error retry rows; load_log keeps
    # the successful attempt per instance, so the scored set is the standard 400.
    gk = {
        "C-sham":         load_log(LOGS / "grok-4.3" / "supp_C-sham.jsonl"),
        "C-crisp":        load_log(LOGS / "grok-4.3" / "supp_C-crisp.jsonl"),
        "C-R2only":       load_log(LOGS / "grok-4.3" / "supp_C-R2only.jsonl"),
        "C-single-crisp": load_log(LOGS / "grok-4.3" / "supp_C-single-crisp.jsonl"),
    }
    gk_metrics = {c: metrics(*predictions(gk[c])) for c in gk}
    computed["grok_43"] = {c: gk_metrics[c]["accuracy"] for c in gk}

    WANT = {
        ("glm-4.5-air", "C-sham"): 0.4925, ("glm-4.5-air", "C-crisp"): 0.5600,
        ("qwen3-max",   "C-sham"): 0.5100, ("qwen3-max",   "C-crisp"): 0.6025,
        ("kimi-k2.6",   "C-sham"): 0.5300, ("kimi-k2.6",   "C-crisp"): 0.5675,
        ("grok-4.3",    "C-sham"): 0.4850, ("grok-4.3",    "C-crisp"): 0.5700,
    }
    for (model, cond), want in WANT.items():
        recs = extra_recs[model][cond] if model in extra_recs else gk[cond]
        acc = metrics(*predictions(recs))["accuracy"]
        C.check("Table 3  %s %s accuracy" % (model, cond), acc, want)

    C.check("Result 1  glm-4.5-air C-R2only accuracy",
            metrics(*predictions(extra_recs["glm-4.5-air"]["C-R2only"]))["accuracy"], 0.6225)

    # crisp minus sham, the matched contrast of Table 3, on every model
    CRISP_SHAM = {
        "deepseek-chat": (ctl["C-crisp__recs"], ctl["C-sham__recs"], -0.0325, 0.407),
        "glm-5.3-flash": (f53_recs["C-crisp"],  f53_recs["C-sham"],  +0.0650, 0.093),
        "glm-4.5-air":   (extra_recs["glm-4.5-air"]["C-crisp"],
                          extra_recs["glm-4.5-air"]["C-sham"],       +0.0675, 0.064),
        "qwen3-max":     (extra_recs["qwen3-max"]["C-crisp"],
                          extra_recs["qwen3-max"]["C-sham"],         +0.0925, 0.0126),
        "kimi-k2.6":     (extra_recs["kimi-k2.6"]["C-crisp"],
                          extra_recs["kimi-k2.6"]["C-sham"],         +0.0375, 0.357),
        "grok-4.3":      (gk["C-crisp"], gk["C-sham"],              +0.0850, 0.025),
    }
    for model, (rc, rs, want_d, want_p) in CRISP_SHAM.items():
        al = aligned(rc, rs)
        if al is None:
            C.check("Table 3  %s crisp-sham" % model, None, want_d)
            continue
        yt, pc, ps = al
        cc = [t == p for t, p in zip(yt, pc)]
        cs = [t == p for t, p in zip(yt, ps)]
        r = mcnemar_exact(cc, cs)
        delta = (sum(cc) - sum(cs)) / len(yt)
        C.check("Table 3  %s crisp minus sham  delta" % model, delta, want_d, tol=0.0001)
        C.check("Table 3  %s crisp minus sham  p" % model, r["p"], want_p, tol=0.002)

    # ------------------- C-crisp against both engines, all five models (SI table)
    # The Result 4 contrast on every model for which C-crisp was run. Both engines
    # are deterministic over the same parsed card text, so only the prompted column
    # changes between rows.
    SI_ENGINE = {
        "deepseek-chat": (ctl["C-crisp__recs"], 0.5400, -0.0250, 0.132, -0.0775, 0.0080),
        "glm-5.3-flash": (f53_recs["C-crisp"],  0.5700, +0.0050, 0.500, -0.0475, 0.1401),
        "glm-4.5-air":   (extra_recs["glm-4.5-air"]["C-crisp"],
                          0.5600, -0.0050, 0.888, -0.0575, 0.0616),
        "qwen3-max":     (extra_recs["qwen3-max"]["C-crisp"],
                          0.6025, +0.0375, 0.184, -0.0150, 0.5258),
        "kimi-k2.6":     (extra_recs["kimi-k2.6"]["C-crisp"],
                          0.5675, +0.0025, 1.000, -0.0500, 0.1105),
        "grok-4.3":      (gk["C-crisp"],
                          0.5700, +0.0050, 0.860, -0.0475, 0.101),
    }
    for model, (recs, w_acc, w_dor, w_por, w_dr2, w_pr2) in SI_ENGINE.items():
        tids = [t for t in recs if t in ENG_R2 and recs[t].get("is_fraud") in (0, 1)]
        if not tids:
            C.check("SI engine  %s" % model, None, w_acc)
            continue
        yt = [ENG_LABEL[t] for t in tids]
        cc = [a == b for a, b in zip(yt, [recs[t]["is_fraud"] for t in tids])]
        co = [a == b for a, b in zip(yt, [ENG_OR[t] for t in tids])]
        cr = [a == b for a, b in zip(yt, [ENG_R2[t] for t in tids])]
        acc = sum(cc) / len(cc)
        ror, rr2 = mcnemar_exact(cc, co), mcnemar_exact(cc, cr)
        C.check("SI engine  %s  C-crisp accuracy" % model, acc, w_acc, tol=0.0001)
        C.check("SI engine  %s  delta vs E_OR" % model, acc - sum(co) / len(co), w_dor, tol=0.0001)
        C.check("SI engine  %s  p vs E_OR" % model, ror["p"], w_por, tol=0.002)
        C.check("SI engine  %s  delta vs E_R2" % model, acc - sum(cr) / len(cr), w_dr2, tol=0.0001)
        C.check("SI engine  %s  p vs E_R2" % model, rr2["p"], w_pr2, tol=0.002)

    # ------------------------------------------------------ card-disjoint deepseek
    dsc = {"A": load_log(LOGS / "deepseek" / "carddisjoint_A.jsonl"),
           "C": load_log(LOGS / "deepseek" / "carddisjoint_C.jsonl")}
    dsc_metrics = {c: metrics(*predictions(dsc[c])) for c in "AC"}
    computed["deepseek_carddisjoint"] = dsc_metrics

    C.check("SI Table 9  card-disjoint C accuracy", dsc_metrics["C"]["accuracy"], 0.5875)
    C.check("SI Table 9  card-disjoint A accuracy", dsc_metrics["A"]["accuracy"], 0.4950)

    # ----------------------------------------------------------- McNemar tests
    def mc(recs_a, recs_b):
        al = aligned(recs_a, recs_b)
        if al is None:
            return None
        yt, pa, pb = al
        ca = [t == p for t, p in zip(yt, pa)]
        cb = [t == p for t, p in zip(yt, pb)]
        r = mcnemar_exact(ca, cb)
        r["delta"] = (sum(ca) - sum(cb)) / len(yt)
        return r

    tests = {
        "C vs E_R2 (main)":  None,   # handled below via engine predictions
        "C-R2only vs E_R2":  None,
        "C vs E_OR":         None,
        "C-crisp vs E_OR":   None,
    }

    # engine predictions keyed by transaction id, so they can be paired with logs
    eng_r2 = {tid: p for (tid, _, _), p in zip(parsed, pred_r2)}
    eng_or = {tid: p for (tid, _, _), p in zip(parsed, pred_or)}
    gt_map = {tid: t for tid, t, _ in parsed}

    def mc_vs_engine(recs, eng_map, new_gt=None):
        g = gt_map if new_gt is None else new_gt
        rows = {str(r["transaction_id"]): r for r in scored(recs)}
        common = sorted(set(rows) & set(eng_map), key=lambda x: int(x) if x.isdigit() else 0)
        yt = [g[t] for t in common]
        pa = [int(rows[t]["is_fraud"]) for t in common]
        pb = [eng_map[t] for t in common]
        ca = [t == p for t, p in zip(yt, pa)]
        cb = [t == p for t, p in zip(yt, pb)]
        r = mcnemar_exact(ca, cb)
        r["delta"] = (sum(ca) - sum(cb)) / len(yt)
        return r

    r = mc_vs_engine(ctl["C-R2only__recs"], eng_r2)
    C.check("Table 2  C-R2only vs E_R2  delta", r["delta"], -0.0100)
    C.check("Table 2  C-R2only vs E_R2  p", r["p"], 0.344, tol=0.002)

    # ------------------------------------------- glm-5.3-flash C-R2only (Result 1)
    # The reported run of this condition is the second execution of the same
    # frozen prompt through the same serving path, run to verify path
    # equivalence after the supplementary controls were added. An earlier run
    # of the identical prompt is archived alongside it and checked as a
    # run-to-run disclosure; the two runs differ on 14 of 400 instances.
    f53_r2 = mc_vs_engine(f53_recs["C-R2only"], eng_r2)
    C.check("Result 1  glm-5.3-flash C-R2only accuracy",
            f53_metrics["C-R2only"]["accuracy"], 0.6100)
    C.check("Result 1  glm-5.3-flash C-R2only vs E_R2  delta",
            f53_r2["delta"], -0.0075)
    C.check("Result 1  glm-5.3-flash C-R2only vs E_R2  b (model correct, engine wrong)",
            f53_r2["b"], 5)
    C.check("Result 1  glm-5.3-flash C-R2only vs E_R2  c (engine correct, model wrong)",
            f53_r2["c"], 8)
    C.check("Result 1  glm-5.3-flash C-R2only vs E_R2  p",
            f53_r2["p"], 0.581, tol=0.002)

    def flag_mcnemar(recs: Dict[str, Dict[str, Any]],
                     eng_map: Dict[str, int]) -> Dict[str, Any]:
        """McNemar on the flag decisions, not on correctness (Sec 4.1)."""
        rows = {str(x["transaction_id"]): x for x in scored(recs)}
        common = sorted(set(rows) & set(eng_map),
                        key=lambda x: int(x) if x.isdigit() else 0)
        pa = [int(rows[t]["is_fraud"]) for t in common]
        pb = [eng_map[t] for t in common]
        r = mcnemar_exact([a == 1 for a in pa], [b == 1 for b in pb])
        r["model_flags"] = sum(pa)
        r["model_only"] = sum(1 for a, b in zip(pa, pb) if a == 1 and b == 0)
        r["engine_only"] = sum(1 for a, b in zip(pa, pb) if a == 0 and b == 1)
        return r

    fr = flag_mcnemar(f53_recs["C-R2only"], eng_r2)
    C.check("Sec 4.1  glm-5.3-flash C-R2only flags", fr["model_flags"], 114)
    C.check("Sec 4.1  glm-5.3-flash over-flag discordance (model flags, engine does not)",
            fr["model_only"], 13)
    C.check("Sec 4.1  glm-5.3-flash under-flag discordance (engine flags, model does not)",
            fr["engine_only"], 0)
    C.check("Sec 4.1  glm-5.3-flash flag-decision McNemar p",
            fr["p"], 0.0002, tol=0.0002)

    # Run-to-run disclosure: the earlier C-R2only run on the same model, prompt
    # and instances, archived as supp_C-R2only-prior-run.jsonl.
    f53_prior = load_log(LOGS / "glm-5.3-flash" / "supp_C-R2only-prior-run.jsonl")
    f53_prior_metrics = metrics(*predictions(f53_prior))
    f53_prior_r = mc_vs_engine(f53_prior, eng_r2)
    C.check("Run-to-run disclosure  glm-5.3-flash C-R2only prior-run accuracy",
            f53_prior_metrics["accuracy"], 0.6000)
    C.check("Run-to-run disclosure  glm-5.3-flash C-R2only prior-run vs E_R2  delta",
            f53_prior_r["delta"], -0.0175)
    C.check("Run-to-run disclosure  glm-5.3-flash C-R2only prior-run vs E_R2  b (model correct, engine wrong)",
            f53_prior_r["b"], 4)
    C.check("Run-to-run disclosure  glm-5.3-flash C-R2only prior-run vs E_R2  c (engine correct, model wrong)",
            f53_prior_r["c"], 11)
    C.check("Run-to-run disclosure  glm-5.3-flash C-R2only prior-run vs E_R2  p",
            f53_prior_r["p"], 0.1185, tol=0.002)
    computed["glm_53_flash_C_R2only"] = {"reported": f53_r2, "prior_run": f53_prior_r}

    # ------------------------------------------- grok-4.3  Result 1 and Sec 4.1
    # C-R2only versus the single-rule engine: one discordant instance overall,
    # which is an instance the engine classifies correctly and the model does
    # not (b/c = 0/1); on flag decisions the single excess is the model flagging
    # a record the engine does not (13-style over-flagging in miniature).
    gk_r2 = mc_vs_engine(gk["C-R2only"], eng_r2)
    C.check("Result 1  grok-4.3 C-R2only vs E_R2  delta", gk_r2["delta"], -0.0025)
    C.check("Result 1  grok-4.3 C-R2only vs E_R2  b (model correct, engine wrong)",
            float(gk_r2["b"]), 0)
    C.check("Result 1  grok-4.3 C-R2only vs E_R2  c (engine correct, model wrong)",
            float(gk_r2["c"]), 1)
    C.check("Result 1  grok-4.3 C-R2only vs E_R2  p", gk_r2["p"], 1.000, tol=0.002)

    gk_fr = flag_mcnemar(gk["C-R2only"], eng_r2)
    C.check("Sec 4.1  grok-4.3 C-R2only flags", gk_fr["model_flags"], 102)
    C.check("Sec 4.1  grok-4.3 C-R2only over-flag discordance (model flags, engine does not)",
            gk_fr["model_only"], 1)
    C.check("Sec 4.1  grok-4.3 C-R2only under-flag discordance (engine flags, model does not)",
            gk_fr["engine_only"], 0)
    C.check("Sec 4.1  grok-4.3 C-R2only flag-decision McNemar p",
            gk_fr["p"], 1.000, tol=0.002)

    # C-single-crisp: verdict-for-verdict identity with the engine (SI table).
    gk_sc = mc_vs_engine(gk["C-single-crisp"], eng_r2)
    C.check("SI Table  grok-4.3 C-single-crisp vs E_R2  delta", gk_sc["delta"], 0.0000)
    C.check("SI Table  grok-4.3 C-single-crisp  discordant b", float(gk_sc["b"]), 0)
    C.check("SI Table  grok-4.3 C-single-crisp  discordant c", float(gk_sc["c"]), 0)
    gk_sc_fr = flag_mcnemar(gk["C-single-crisp"], eng_r2)
    C.check("SI Table  grok-4.3 C-single-crisp flags", gk_sc_fr["model_flags"], 101)
    C.check("SI Table  grok-4.3 C-single-crisp  flag discordance (b + c)",
            float(gk_sc_fr["model_only"] + gk_sc_fr["engine_only"]), 0)

    # Table 3 grok row: b/c of the crisp-sham contrast and the paired bootstrap
    # CI of the per-instance difference quoted in the main text.
    al_gk = aligned(gk["C-crisp"], gk["C-sham"])
    if al_gk is None:
        C.check("Table 3  grok-4.3 crisp-sham b/c", None, 126.0)
    else:
        yt_gk, pc_gk, ps_gk = al_gk
        cc_gk = [t == p for t, p in zip(yt_gk, pc_gk)]
        cs_gk = [t == p for t, p in zip(yt_gk, ps_gk)]
        r_gk = mcnemar_exact(cc_gk, cs_gk)
        C.check("Table 3  grok-4.3 crisp minus sham  b (crisp correct, sham wrong)",
                float(r_gk["b"]), 126)
        C.check("Table 3  grok-4.3 crisp minus sham  c (sham correct, crisp wrong)",
                float(r_gk["c"]), 92)
        d_gk_simple = [int(a) - int(b) for a, b in zip(cc_gk, cs_gk)]
        rng_gk = random.Random(BOOTSTRAP_SEED)
        boots_gk = sorted(sum(d_gk_simple[rng_gk.randrange(len(d_gk_simple))]
                              for _ in range(len(d_gk_simple))) / len(d_gk_simple)
                          for _ in range(BOOTSTRAP_N))
        C.check("Table 3  grok-4.3 crisp-sham  CI low",
                boots_gk[int(round(0.025 * (BOOTSTRAP_N - 1)))], 0.0150, tol=0.0065)
        C.check("Table 3  grok-4.3 crisp-sham  CI high",
                boots_gk[int(round(0.975 * (BOOTSTRAP_N - 1)))], 0.1550, tol=0.0065)


    r = mc_vs_engine(ctl["C-crisp__recs"], eng_or)
    C.check("Result 4  C-crisp vs E_OR  delta", r["delta"], -0.0250)
    C.check("Result 4  C-crisp vs E_OR  p", r["p"], 0.133, tol=0.002)
    computed["result4"] = r

    # bootstrap CI on the primary comparison
    ci = mc_vs_engine(ctl["C-crisp__recs"], eng_or)
    rows = {str(x["transaction_id"]): x for x in scored(ctl["C-crisp__recs"])}
    common = sorted(set(rows) & set(eng_or), key=lambda x: int(x) if x.isdigit() else 0)
    yt = [gt_map[t] for t in common]
    pa = [int(rows[t]["is_fraud"]) for t in common]
    pb = [eng_or[t] for t in common]
    bs = paired_bootstrap_ci(yt, pa, pb)
    computed["result4_bootstrap"] = bs
    C.check("Result 4  C-crisp vs E_OR  CI low", bs["lo"], -0.0550, tol=0.0025)
    C.check("Result 4  C-crisp vs E_OR  CI high", bs["hi"], 0.0050, tol=0.0025)

    # sham versus rule-free, on deepseek.  Reported as C-sham minus A.
    r = mc(ctl["C-sham__recs"], ds["A"])
    C.check("Result 3  C-sham minus A  delta", r["delta"], 0.0850, tol=0.002)
    C.check("Result 3  C-sham minus A  p", r["p"], 0.0020, tol=0.002)

    # sham versus the real rulebook (not significant, as reported)
    r = mc(ctl["C-sham__recs"], ds["C"])
    C.check("Result 3  C-sham minus C  delta", r["delta"], -0.0100, tol=0.002)

    # ------------------------------------------------------ base-rate control
    # C-neutral versus the rule-free baseline, on the two models that carry the
    # 2x2 (SI: the base-rate control).  Checks the condition's own accuracy and
    # flag count, and its paired contrast against Condition A.
    neu = {"deepseek":     load_log(LOGS / "deepseek" / "supp_C-neutral.jsonl"),
           "glm-4.5-air":  load_log(LOGS / "glm-4.5-air" / "supp_C-neutral.jsonl")}
    neu_metrics = {m: metrics(*predictions(neu[m])) for m in neu}
    computed["neutral"] = neu_metrics

    C.check("SI base-rate  deepseek C-neutral accuracy",
            neu_metrics["deepseek"]["accuracy"], 0.4575)
    C.check("SI base-rate  deepseek C-neutral flags",
            neu_metrics["deepseek"]["flags"], 81)
    C.check("SI base-rate  glm-4.5-air C-neutral accuracy",
            neu_metrics["glm-4.5-air"]["accuracy"], 0.4525)
    C.check("SI base-rate  glm-4.5-air C-neutral flags",
            neu_metrics["glm-4.5-air"]["flags"], 231)

    r = mc(neu["deepseek"], ds["A"])
    C.check("SI base-rate  deepseek C-neutral minus A  delta",
            r["delta"], -0.0300, tol=0.002)
    C.check("SI base-rate  deepseek C-neutral minus A  p", r["p"], 0.2242, tol=0.002)
    r = mc(neu["glm-4.5-air"], glm["A"])
    C.check("SI base-rate  glm-4.5-air C-neutral minus A  delta",
            r["delta"], +0.0225, tol=0.002)
    C.check("SI base-rate  glm-4.5-air C-neutral minus A  p", r["p"], 0.5478, tol=0.002)

    # --------------------------------------------------------------- variance
    def majority_agreement(pattern: str) -> Optional[float]:
        files = sorted(LOGS.glob(pattern))
        if len(files) < 2:
            return None
        maps = [load_log(p) for p in files]
        common = set.intersection(*[set(m) for m in maps])
        vals: List[float] = []
        for tid in common:
            labels = [m[tid].get("is_fraud") for m in maps]
            labels = [int(x) for x in labels if x in (0, 1)]
            if labels:
                vals.append(max(labels.count(0), labels.count(1)) / len(labels))
        return sum(vals) / len(vals) if vals else None

    var_agree = {c: majority_agreement("variance/variance_r*_condition_%s.jsonl" % c)
                 for c in "ABCD"}
    computed["variance_agreement"] = var_agree

    # The manuscript reports A and C in 0.9400-0.9760 and B and D in 0.8400-0.8600.
    C.check("SI Robustness  agreement A", var_agree["A"], 0.9760, tol=0.0005)
    C.check("SI Robustness  agreement C", var_agree["C"], 0.9400, tol=0.0005)
    C.check("SI Robustness  agreement B", var_agree["B"], 0.8400, tol=0.0005)
    C.check("SI Robustness  agreement D", var_agree["D"], 0.8600, tol=0.0005)

    # ------------------------------------------------------------------------
    # Sec 4.1  the zero-call control: the sham rules executed deterministically
    # ------------------------------------------------------------------------
    clusters_path = DATA / "card_clusters.json"
    clusters = (json.loads(clusters_path.read_text(encoding="utf-8"))
                if clusters_path.exists() else None)
    if clusters is None:
        print("!! data/card_clusters.json missing — card-level checks skipped")

    carddisjoint = json.loads(
        (DATA / "test_cards_400_carddisjoint.json").read_text(encoding="utf-8"))

    # The manuscript reports the rendered-card reading as primary and three
    # values across the two defensible operationalisations of the wording.
    sham_acc: Dict[Tuple[str, str, str], float] = {}
    for set_name, rows in (("standard", cards), ("card-disjoint", carddisjoint)):
        for amount_mode in ("rendered", "2dp"):
            for domain_mode in ("full", "firstlabel"):
                yt = [int(r["ground_truth_label"]) for r in rows]
                pr = [engine_sham(r["card_text"], str(r["TransactionID"]),
                                  amount_mode=amount_mode, domain_mode=domain_mode)
                      for r in rows]
                sham_acc[(set_name, amount_mode, domain_mode)] = metrics(yt, pr)["accuracy"]

    yt_std = [int(r["ground_truth_label"]) for r in cards]
    sham_pred = [engine_sham(r["card_text"], str(r["TransactionID"])) for r in cards]
    sham_std = metrics(yt_std, sham_pred)
    per_rule = [metrics(yt_std,
                        [int(sham_rules(r["card_text"], str(r["TransactionID"]))[k])
                         for r in cards])["accuracy"]
                for k in range(4)]
    yt_cd = [int(r["ground_truth_label"]) for r in carddisjoint]
    sham_cd = metrics(yt_cd,
                      [engine_sham(r["card_text"], str(r["TransactionID"]))
                       for r in carddisjoint])
    computed["engine_sham"] = {"standard": sham_std, "card_disjoint": sham_cd,
                              "standard_per_rule_accuracy": per_rule,
                              "by_reading": {"|".join(k): v for k, v in sham_acc.items()}}

    C.check("Sec 4.1  E_sham accuracy (standard)", sham_std["accuracy"], 0.5025)
    C.check("Sec 4.1  E_sham flags (standard)", float(sham_std["flags"]), 343)
    C.check("Sec 4.1  E_sham accuracy (card-disjoint)", sham_cd["accuracy"], 0.5100)
    C.check("Sec 4.1  E_sham flags (card-disjoint)", float(sham_cd["flags"]), 360)
    for k, want in zip(range(4), (0.5175, 0.5100, 0.5025, 0.5025)):
        C.check("Sec 4.1  E_sham rule %d accuracy" % (k + 1), per_rule[k], want)
    # The manuscript quotes the four card-derived values as a spread rather
    # than singling out one reading, so all four are checked individually.
    C.check("Sec 4.1  E_sham, first-label domain reading",
            sham_acc[("standard", "rendered", "firstlabel")], 0.5350)
    C.check("Sec 4.1  E_sham, two-decimal amount reading",
            sham_acc[("standard", "2dp", "firstlabel")], 0.5375)
    C.check("Sec 4.1  E_sham, two-decimal amount + full domain",
            sham_acc[("standard", "2dp", "full")], 0.4825)
    C.check("Sec 4.1  E_sham, largest value over readings and sets",
            max(sham_acc.values()), 0.5700)

    # ---------------------------------------------------------- McNemar b/c pairs
    eng_r2_cd = {str(r["TransactionID"]): engine_r2(parse_card(r["card_text"]))
                 for r in carddisjoint}
    r = mc_vs_engine(ctl["C-crisp__recs"], eng_r2)
    C.check("Result 4  C-crisp vs E_R2  delta", r["delta"], -0.0775)
    C.check("Result 4  C-crisp vs E_R2  b", float(r["b"]), 49)
    C.check("Result 4  C-crisp vs E_R2  c", float(r["c"]), 80)
    C.check("Result 4  C-crisp vs E_R2  p", r["p"], 0.0080, tol=0.002)

    gt_cd = {str(r["TransactionID"]): int(r["ground_truth_label"]) for r in carddisjoint}
    r = mc_vs_engine(f53_recs["C"], eng_r2_cd, gt_cd)
    C.check("Abstract  glm-5.3-flash C vs E_R2  delta", r["delta"], -0.0775)
    C.check("Abstract  glm-5.3-flash C vs E_R2  b", float(r["b"]), 50)
    C.check("Abstract  glm-5.3-flash C vs E_R2  c", float(r["c"]), 81)
    C.check("Abstract  glm-5.3-flash C vs E_R2  p", r["p"], 0.0085, tol=0.002)

    # ------------------------------------------------- Sec 4.1  sham->crisp interaction
    def dd(recs_hi, recs_lo):
        sa = {str(x["transaction_id"]): x for x in scored(recs_hi)}
        sb = {str(x["transaction_id"]): x for x in scored(recs_lo)}
        common = sorted(set(sa) & set(sb), key=lambda x: int(x) if x.isdigit() else 0)
        return common, ([int(sa[t]["is_fraud"] == sa[t]["ground_truth_label"])
                         - int(sb[t]["is_fraud"] == sb[t]["ground_truth_label"])
                         for t in common])

    ids_ds, d_ds = dd(ctl["C-crisp__recs"], ctl["C-sham__recs"])
    ids_gl, d_gl = dd(f53_recs["C-crisp"], f53_recs["C-sham"])
    gl = dict(zip(ids_gl, d_gl))
    common = [t for t in ids_ds if t in gl]
    D = [(gl[t] - d) for t, d in zip(ids_ds, d_ds) if t in gl]
    observed = sum(D) / len(D)

    rng_i = random.Random(BOOTSTRAP_SEED)
    boots = []
    for _ in range(BOOTSTRAP_N):
        boots.append(sum(D[rng_i.randrange(len(D))] for _ in range(len(D))) / len(D))
    boots.sort()
    lo_i = boots[int(round(0.025 * (len(boots) - 1)))]
    hi_i = boots[int(round(0.975 * (len(boots) - 1)))]
    p_i = signflip_permutation_p(D)
    computed["interaction"] = {"n": len(D), "interaction": observed,
                               "lo": lo_i, "hi": hi_i, "p_signflip": p_i,
                               "delta_ds": sum(d_ds) / len(d_ds),
                               "delta_glm": sum(d_gl) / len(d_gl)}

    C.check("Sec 4.1  sham->crisp interaction", observed, 0.0975)
    C.check("Sec 4.1  sham->crisp interaction CI low", lo_i, 0.0350, tol=0.0065)
    C.check("Sec 4.1  sham->crisp interaction CI high", hi_i, 0.1625, tol=0.0065)
    C.check("Sec 4.1  sham->crisp interaction sign-flip p", p_i, 0.0048, tol=0.0015)
    C.check("Sec 4.1  simple effect, deepseek", sum(d_ds) / len(d_ds), -0.0325)
    C.check("Sec 4.1  simple effect, glm-5.3-flash", sum(d_gl) / len(d_gl), 0.0650)
    C.flag("Sec 4.1  interaction CI excludes zero", lo_i > 0)

    # grok-4.3 interaction against deepseek (frontier-model row of the same
    # paragraph). Both the point estimate and the 95% CI are checked against
    # the corrected manuscript values; the interval the pre-correction draft
    # quoted ([+0.0300, +0.2625]) came from an unarchived inline script that
    # double-subtracted the deepseek mean, and is superseded.
    ids_gk, d_gk_int = dd(gk["C-crisp"], gk["C-sham"])
    gl_gk = dict(zip(ids_gk, d_gk_int))
    D_gk = [gl_gk[t] - d for t, d in zip(ids_ds, d_ds) if t in gl_gk]
    obs_gk = sum(D_gk) / len(D_gk)
    rng_gk_i = random.Random(BOOTSTRAP_SEED)
    boots_gk_i = sorted(sum(D_gk[rng_gk_i.randrange(len(D_gk))]
                            for _ in range(len(D_gk))) / len(D_gk)
                        for _ in range(BOOTSTRAP_N))
    lo_gk = boots_gk_i[int(round(0.025 * (BOOTSTRAP_N - 1)))]
    hi_gk = boots_gk_i[int(round(0.975 * (BOOTSTRAP_N - 1)))]
    computed["interaction_grok"] = {"n": len(D_gk), "interaction": obs_gk,
                                    "lo": lo_gk, "hi": hi_gk}
    C.check("Sec 4.1  sham->crisp interaction, grok-4.3", obs_gk, 0.1175)
    C.check("Sec 4.1  grok-4.3 interaction CI low", lo_gk, 0.0550, tol=0.0065)
    C.check("Sec 4.1  grok-4.3 interaction CI high", hi_gk, 0.1775, tol=0.0065)
    C.flag("Sec 4.1  grok-4.3 interaction CI excludes zero", lo_gk > 0)

    # ------------------------------------------------------- entity-level analyses
    if clusters is not None:
        c1 = clusters["card1_by_transaction"]
        pilot_c1 = set(clusters["pilot_card1"])

        overlap = [r for r in cards if c1.get(str(r["TransactionID"])) in pilot_c1]
        C.check("SI Statistical protocol  pilot-overlapping instances",
                float(len(overlap)), 93)

        # fresh card-disjoint benchmark, deepseek C vs A
        cd_ids = [str(r["TransactionID"]) for r in carddisjoint]
        cd_y = [int(r["ground_truth_label"]) for r in carddisjoint]
        cd_c = {str(x["transaction_id"]): x for x in scored(dsc["C"])}
        cd_a = {str(x["transaction_id"]): x for x in scored(dsc["A"])}
        keep = [i for i, t in enumerate(cd_ids) if t in cd_c and t in cd_a]
        y_cd = [cd_y[i] for i in keep]
        p_c = [int(cd_c[cd_ids[i]]["is_fraud"]) for i in keep]
        p_a = [int(cd_a[cd_ids[i]]["is_fraud"]) for i in keep]
        g_cd: Dict[Any, List[int]] = {}
        for j, i in enumerate(keep):
            g_cd.setdefault(c1.get(cd_ids[i], "?"), []).append(j)
        ci_cd = cluster_bootstrap_ci(y_cd, p_c, p_a, g_cd)
        computed["carddisjoint_cluster_ci"] = {**ci_cd, "clusters": len(g_cd)}
        C.check("SI Robustness (a) fresh card-disjoint C-A delta", ci_cd["delta"], 0.0925)
        C.check("SI Robustness (a) fresh cluster CI low", ci_cd["lo"], 0.0266, tol=0.0065)
        C.check("SI Robustness (a) fresh cluster CI high", ci_cd["hi"], 0.1617, tol=0.0065)
        C.flag("SI Robustness (a) fresh cluster CI excludes zero", ci_cd["lo"] > 0)

        # 307-row non-overlapping subsample of the original 400
        sub = [i for i, r in enumerate(cards)
               if c1.get(str(r["TransactionID"])) not in pilot_c1]
        ds_c_map = {str(x["transaction_id"]): x for x in scored(ds["C"])}
        ds_a_map = {str(x["transaction_id"]): x for x in scored(ds["A"])}
        sub = [i for i in sub if str(cards[i]["TransactionID"]) in ds_c_map
               and str(cards[i]["TransactionID"]) in ds_a_map]
        y_s = [int(cards[i]["ground_truth_label"]) for i in sub]
        p_sc = [int(ds_c_map[str(cards[i]["TransactionID"])]["is_fraud"]) for i in sub]
        p_sa = [int(ds_a_map[str(cards[i]["TransactionID"])]["is_fraud"]) for i in sub]
        g_s: Dict[Any, List[int]] = {}
        for j, i in enumerate(sub):
            g_s.setdefault(c1.get(str(cards[i]["TransactionID"]), "?"), []).append(j)
        ci_s = cluster_bootstrap_ci(y_s, p_sc, p_sa, g_s)
        computed["nonoverlap_subset_cluster_ci"] = {**ci_s, "n": len(sub),
                                                    "clusters": len(g_s)}
        C.check("SI Robustness (a) non-overlapping subsample n", float(len(sub)), 307)
        C.check("SI Robustness (a) non-overlapping subsample C-A delta", ci_s["delta"], 0.0489)
        C.flag("SI Robustness (a) subsample cluster CI includes zero",
               ci_s["lo"] < 0 < ci_s["hi"],
               note="endpoints are Monte-Carlo; the paper reports "
                    "[-0.0252, +0.1173] as primary and [-0.0204, +0.1180] "
                    "for a second draw order")

    # ---------------------------------------------------------------- report
    print("=" * 78)
    print("RECOMPUTATION FROM ARCHIVED LOGS")
    print("=" * 78)

    if not args.quiet:
        print("%-46s %10s %10s   %s" % ("value", "in paper", "recomputed", ""))
        print("-" * 78)
    for row in C.rows:
        if args.quiet and row["ok"]:
            continue
        print("%-46s %10.4f %10s   %s" % (
            row["label"], row["expected"], row["shown"],
            "ok" if row["ok"] else "*** MISMATCH ***"))

    print("-" * 78)
    print("%d / %d checked values match the manuscript." % (C.n_ok, C.n_total))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "verification.json").write_text(
        json.dumps({"checks": C.rows, "computed": computed},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("Wrote %s" % (OUT / "verification.json"))

    if args.json:
        print(json.dumps(computed, ensure_ascii=False, indent=2))

    return 0 if C.n_ok == C.n_total else 1


if __name__ == "__main__":
    sys.exit(main())
