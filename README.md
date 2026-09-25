# Prompted Rule Execution Adds Nothing over a Rule Engine

Reproducibility package for the manuscript:

> **Prompted Rule Execution Adds Nothing over a Rule Engine on Machine-Checkable Rules**
> Yuyang Lei, *Expert Systems* (submitted)

---

## What this study asks

Supplying a language model with a written rulebook in its prompt is now common
practice in fraud detection. The assumption behind it — that *prompting* a model
with rules differs materially from *executing* those same rules in code — is
rarely stated and, to our knowledge, had not been tested directly.

This repository contains everything needed to reproduce the study: the
experiment harness, the evaluation instances, the frozen prompt templates, the
per-instance response logs for every reported run, and the analysis code.

**Headline result.** In a specification-matched comparison, in which both arms
implement an identical rule specification, the design excludes any LLM advantage
above half a percentage point (Δ = −0.0250, *p* = 0.133, 95% CI
[−0.0550, +0.0050]). Decomposing the rulebook, one of its four rules carries
essentially all of the discriminative signal.

---

## Quick start

```bash
# 1. No dependencies to install — everything uses the Python standard library.
python --version          # requires Python 3.8+

# 2. Recompute every number reported in the paper from the archived logs.
python recompute.py

# 3. (Optional) Re-run the experiment live against an API endpoint.
cp config.example.json config.json   # then fill in your endpoint and key
python src/run_experiment.py --config config.json
```

`recompute.py` needs no network access and no API key: it reads the archived
logs in `logs/` and regenerates the paper's tables. **This is the fastest way to
verify the reported numbers.**

---

## Repository layout

```
.
├── recompute.py            # regenerates every reported number from logs/
│                           #   (134 checks: accuracies, McNemar b/c and exact p,
│                           #    deterministic engines incl. the zero-call sham
│                           #    control, the sham->crisp interaction, and the
│                           #    card-level cluster-robust intervals)
├── config.example.json     # template; copy to config.json and fill in
├── run_supplementary.py    # runner: single-rule, sham, de-hedged and A-SC controls
├── run_single_crisp.py     # runner: C-single-crisp (rule execution required)
├── run_neutral.py          # runner: C-neutral (base-rate statement only)
├── src/                    # experiment harness (8 scripts)
│   ├── run_experiment.py       # main runner: conditions A/B/C/D
│   ├── sample_formal_test.py   # builds the 400-instance evaluation sets
│   ├── eval_metrics.py         # metrics + paired bootstrap CIs
│   ├── eval_corrected_B.py     # evaluation of the corrected Condition B
│   ├── variance_probe.py       # run-to-run consistency probe
│   ├── check_determinism.py    # decoding-determinism probe
│   └── verify_prompt_integrity.py  # SHA-256 verification of prompt constants
├── work/                   # analysis and audit scripts used during the study
│   └── build_card_clusters.py   # exports data/card_clusters.json from the raw data
├── data/
│   ├── test_cards_400.json             # standard 400 instances
│   ├── test_cards_400_carddisjoint.json # card-disjoint 400 instances
│   └── card_clusters.json              # card1 per instance + pilot card1 list
├── logs/                   # per-instance response logs (see logs/README.md)
│   ├── deepseek/
│   ├── glm-4.5-air/
│   ├── glm-5.3-flash/
│   └── variance/
├── results/                # metric tables and confidence intervals
└── docs/
    └── PROMPTS.md          # verbatim prompt templates + SHA-256 fingerprints
```

---

## Experimental design

A 2×2 manipulation of **analytical role structure** (single agent vs. three
analytical roles + arbiter) and **rulebook injection** (none vs. four explicit
rules) gives four LLM conditions:

| Condition | Role structure | Rules | Contrast |
|---|---|---|---|
| A | single agent | none | reasoning floor |
| B | three roles | none | ΔS = B − A |
| C | single agent | four rules | ΔK = C − A |
| D | three roles | four rules | ΔInt = (D−B) − (C−A) |

Two deterministic, non-LLM classifiers implement the same rulebook over the
identical serialized card text:

- **E_R2** — applies the single best rule (C₂ ≥ 5)
- **E_OR** — applies the disjunction of all four rules

Six single-prompt controls isolate *what about* a rulebook matters:

- **C-R2only** — one rule only
- **C-sham** — four rules with no fraud content, of identical structure
- **C-crisp** — the four real rules restated as hard thresholds equivalent to
  the engine's conditions
- **C-single-crisp** — one de-hedged rule plus a requirement to execute it and
  ignore every other field or signal
- **C-neutral** — a base-rate statement only: no rules, no field references

**Models.** Five models drawn from four origins, reached through three
platforms.

| Model | Origin | Role |
|---|---|---|
| `deepseek-chat`¹ | DeepSeek | primary; carries the main 2×2 |
| `glm-4.5-air` | Zhipu | replication; also carries the main 2×2 |
| `glm-5.3-flash` | Zhipu | supplementary controls |
| `qwen3-max` | Alibaba | supplementary controls |
| `kimi-k2.6` | Moonshot | supplementary controls |

¹ The manuscript names this model `deepseek-v4.1-Flash`; the vendor's endpoint
is reached under the API identifier `deepseek-chat`, which is what the runs
used.

The two that carry the main 2×2 were run on all four conditions A–D, plus the
single-rule, de-hedged, forced-execution and base-rate controls. The other
three received the sham and de-hedged controls, and `glm-5.3-flash`
additionally the single-rule and forced-execution controls. Note that `qwen3-max` and `kimi-k2.6` were both served
through **Alibaba's DashScope endpoint** rather than through their own vendors'
APIs, and `glm-5.3-flash` through a forwarding layer; see the
specification-matched contrast in the Supplementary Information.

---

## Evaluation data

The instances are derived from the public **IEEE-CIS Fraud Detection**
benchmark (Kaggle, 2019). The raw competition files are **not redistributed
here** — they are 1.3 GB and carry their own licence. To rebuild the evaluation
sets from scratch:

1. Download `train_transaction.csv` and `train_identity.csv` from
   <https://www.kaggle.com/c/ieee-fraud-detection>
2. Place them in `data/raw/`
3. Run `python src/sample_formal_test.py`

The two ready-to-use evaluation sets (`data/test_cards_400.json` and
`data/test_cards_400_carddisjoint.json`) **are** included, so reproduction from
the archived logs does not require the raw dataset.

Both sets contain 400 instances (200 fraudulent, 200 legitimate), sampled by
within-class uniform reservoir sampling (`seed=100`). The card-disjoint set
additionally excludes every `card1` present in the pilot development set.

`card1` is deliberately **not** part of the serialized card text the model saw
(manuscript §4.6(a): no classifier in the study can condition on card identity
directly), so it is not inside `card_text`. It is nevertheless needed by the
card-level analyses — the cluster-robust confidence intervals and the
non-overlapping subsample — and is therefore exported separately into
`data/card_clusters.json`:

```json
{
  "pilot_card1": ["<distinct card1 values in the 100-record pilot set>"],
  "card1_by_transaction": {"<TransactionID>": "<card1>", "...": "..."}
}
```

To rebuild that file from the raw data (only needed if the evaluation sets are
re-drawn):

```bash
python work/build_card_clusters.py --raw-dir <dir with train_transaction.csv
                                               and pilot_sample_100.csv>
```

It reproduces the entity-overlap figure quoted in the manuscript: 93 of the 400
standard instances (23.25%) carry a `card1` that also occurs in the pilot set.

---

## Reproducing the results

### From the archived logs (no API key needed)

```bash
python recompute.py
```

This regenerates the accuracy, precision, recall, macro-F1 and PR-AUC figures,
the exact McNemar tests, the paired bootstrap confidence intervals, the
deterministic engines (including the zero-call sham control), the
sham-versus-crisp interaction, the five-model crisp-versus-engine contrast and
the card-level cluster-robust intervals
reported in the manuscript, and writes them to `results/recomputed/`. It
prints **134 / 134 checked values match the manuscript** and exits non-zero if
any value disagrees.

Two notes on the Monte-Carlo quantities. The cluster-robust interval endpoints
move by up to ~0.005 with the order in which clusters are drawn, so the check
asserts what the manuscript claims about them (which interval excludes zero)
rather than a specific endpoint; for the non-overlapping subsample the
manuscript itself reports both draw orders. The sign-flip permutation p-value
is likewise reported to Monte-Carlo precision.

### Running the experiment live

```bash
cp config.example.json config.json
# edit config.json: set base_url, api_key, model
python src/run_experiment.py --config config.json --log-prefix myrun
```

Useful flags: `--conditions A,C`, `--limit 10`, `--concurrency 3`,
`--sample-concurrency 4`, `--resume`, `--dry-run`.

`--resume` skips instances already logged with `status: success`, so an
interrupted run continues from the breakpoint. `--dry-run` uses deterministic
fake responses so the full pipeline can be exercised without spending quota.

### Verifying prompt integrity

The frozen prompt constants are fingerprinted with SHA-256. To verify that the
prompts you are about to send match the ones used in the reported runs:

```bash
python src/verify_prompt_integrity.py
```

---

## Log format

Each `.jsonl` file has one JSON object per line, one line per instance per
condition. The fields used in the analysis are:

| Field | Meaning |
|---|---|
| `condition` | `A`, `B`, `C`, `D`, or a control name (`C-sham`, `C-crisp`, …) |
| `transaction_id` | instance identifier |
| `ground_truth_label` | 1 = fraud, 0 = legitimate |
| `status` | `success`, `parse_error`, or `api_error` |
| `is_fraud` | the model's parsed decision (null unless `status == "success"`) |
| `confidence` | the model's self-reported confidence, where supplied |
| `api_calls` | number of HTTP calls this record consumed |
| `timestamp` | UTC timestamp |

Only records with `status == "success"` and a non-null `is_fraud` are scored.
Retried instances appear as additional lines; the analysis takes the last
successful record per `transaction_id`.

---

## Requirements

**None beyond the Python standard library.** The API client is implemented
directly against the OpenAI-compatible `/chat/completions` protocol using
`urllib`, so no `pip install` is needed to run the harness or the analysis.

`requirements.txt` is provided for completeness and lists no third-party
packages.

---

## Licence

Code released under the MIT Licence — see `LICENSE`.

The IEEE-CIS dataset is governed by its own terms on Kaggle and is not
redistributed here.

---

## Citation

If you use this code or these logs, please cite the manuscript — see
`CITATION.cff`.
