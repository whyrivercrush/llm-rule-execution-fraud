# Per-instance response logs

One JSON object per line; one file per condition per model. Every file here is
a **complete** run: 400 instances, all with `status: "success"` — six conditions
were added on 2026-09-21 (C-single-crisp on three models, C-neutral on two, and
the path-equivalence probe) and are complete on the same terms. All figures
reported in the manuscript can be regenerated from these files alone — run
`python recompute.py` to check.

## `deepseek/` — primary model

The manuscript names this model `deepseek-v4.1-Flash`; the vendor's endpoint is
reached under the API identifier `deepseek-chat`, which is what the runs here
used. `config.deepseek.json` is where that identifier is supplied (see the
repository `.gitignore` — no credential is ever committed).

The manuscript reports **two independent full executions**. Table 7 uses Run 2;
the robustness section compares the two.

| File | Condition | Accuracy |
|---|---|---|
| `main_run2_condition_A.jsonl` | A — no rules, single agent | 0.4875 |
| `main_run2_condition_B.jsonl` | B — no rules, three roles | 0.4425 |
| `main_run2_condition_C.jsonl` | C — four rules, single agent | 0.5825 |
| `main_run2_condition_D.jsonl` | D — four rules, three roles | 0.5375 |
| `main_run1_condition_A.jsonl` | A, first execution | 0.4875 |
| `main_run1_condition_B_corrected.jsonl` | B, first execution (corrected routing) | 0.4475 |
| `main_run1_condition_C.jsonl` | C, first execution | 0.5950 |
| `main_run1_condition_D.jsonl` | D, first execution | 0.5500 |

Supplementary controls and replications:

| File | Condition | Accuracy |
|---|---|---|
| `supp_C-R2only.jsonl` | C-R2only — single-rule control | 0.6075 |
| `supp_C-sham.jsonl` | C-sham — four rules with no fraud content | 0.5725 |
| `supp_C-crisp.jsonl` | C-crisp — four de-hedged hard thresholds | 0.5400 |
| `supp_A-SC.jsonl` | A-SC — compute-matched self-consistency control | 0.4825 |
| `carddisjoint_A.jsonl` | A on the card-disjoint instance set | 0.4950 |
| `carddisjoint_C.jsonl` | C on the card-disjoint instance set | 0.5875 |
| `selfcons_Ax4.jsonl` | four sampled Condition A prompts per instance | — |
| `supp_C-single-crisp.jsonl` | C-single-crisp — the de-hedged rule plus a requirement to execute it and ignore everything else | 0.6175 |
| `supp_C-neutral.jsonl` | C-neutral — a base-rate statement only: no rules, no field references | 0.4575 |

## `glm-4.5-air/` — replication model

Carries the main $2\times2$ together with `deepseek-chat`. The supplementary
controls were added on 2026-09-20.

| File | Condition | Accuracy |
|---|---|---|
| `main_condition_A.jsonl` | A | 0.4300 |
| `main_condition_B.jsonl` | B | 0.4925 |
| `main_condition_C.jsonl` | C | 0.5950 |
| `main_condition_D.jsonl` | D | 0.5625 |
| `supp_C-sham.jsonl` | C-sham | 0.4925 |
| `supp_C-crisp.jsonl` | C-crisp | 0.5600 |
| `supp_C-R2only.jsonl` | C-R2only | 0.6225 |
| `supp_C-single-crisp.jsonl` | C-single-crisp | 0.6175 |
| `supp_C-neutral.jsonl` | C-neutral — base-rate statement only | 0.4525 |

## `qwen3-max/` and `kimi-k2.6/` — two further origins

Both were served through Alibaba's DashScope endpoint rather than through their
own vendors' APIs; see the specification-matched contrast in the Supplementary
Information. Only the sham and de-hedged
controls were run on these models, on the standard $400$ instances.

| File | Condition | Accuracy |
|---|---|---|
| `qwen3-max/supp_C-sham.jsonl` | C-sham | 0.5100 |
| `qwen3-max/supp_C-crisp.jsonl` | C-crisp | 0.6025 |
| `kimi-k2.6/supp_C-sham.jsonl` | C-sham | 0.5300 |
| `kimi-k2.6/supp_C-crisp.jsonl` | C-crisp | 0.5675 |

Together with `deepseek-chat` and the two `glm` models these give the five-model
matched contrast of Table 3 in the manuscript: C-sham and C-crisp are both
four-rule single-prompt conditions that differ only in whether the rules carry
fraud semantics.

## `glm-5.3-flash/` — supplementary controls on a third model

| File | Condition | Accuracy |
|---|---|---|
| `main_A.jsonl` | A on the card-disjoint set | 0.5075 |
| `main_C.jsonl` | C on the card-disjoint set | 0.5550 |
| `supp_C-R2only.jsonl` | C-R2only on the standard set — **the reported run** | 0.6100 |
| `supp_C-sham.jsonl` | C-sham on the standard set | 0.5050 |
| `supp_C-crisp.jsonl` | C-crisp on the standard set | 0.5700 |
| `supp_C-single-crisp.jsonl` | C-single-crisp on the standard set | 0.6175 |
| `supp_C-R2only-prior-run.jsonl` | C-R2only, an earlier run of the identical frozen prompt and instances, archived as a run-to-run disclosure | 0.6000 |

**Two runs of C-R2only.** C-R2only was executed twice on this model: an
earlier run, and a second run performed to verify that the model is reachable
through the same serving path as the archived data. The two runs differ on 14
of 400 instances at temperature 0 (provider-side decoding variability; see the
run-to-run consistency probe under `variance/`). The **second run is the
reported one** — the manuscript's 0.6100, Δ = −0.0075, b/c = 5/8, p = 0.581,
and the 13/0 over-flagging test all come from `supp_C-R2only.jsonl`. The
earlier run gives 0.6000, Δ = −0.0175, b/c = 4/11, p = 0.118, and the same
one-directional over-flagging (15 flags the engine does not, 0 the other way);
both runs are checked by `recompute.py`.

## `variance/` — run-to-run consistency probe

`variance_r{1..5}_condition_{A,B,C,D}.jsonl` — 50 instances × 5 replicates ×
4 conditions, used for the agreement analysis in the robustness section.
Agreement (majority label across the five replicates): A 0.9760, C 0.9400,
B 0.8400, D 0.8600.

---

## A note on what is *not* here

Two classes of file are deliberately excluded, because including them would
misrepresent the study:

1. **Aborted runs.** Development runs that stopped part-way — for example when
   the API account ran out of credit, leaving every record at
   `status: "api_error"` — are not part of the reported results.
2. **Superseded Condition B.** An early Condition B execution contained a
   routing defect (sub-agents received rule text they should not have). That
   run is superseded by `main_run1_condition_B_corrected.jsonl` and
   `main_run2_condition_B.jsonl`, which is what the manuscript reports.

## Field reference

| Field | Meaning |
|---|---|
| `condition` | `A`, `B`, `C`, `D`, or a control name |
| `transaction_id` | instance identifier |
| `ground_truth_label` | 1 = fraud, 0 = legitimate |
| `status` | `success` / `parse_error` / `api_error` |
| `is_fraud` | parsed model decision (null unless success) |
| `confidence` | model self-reported confidence, where supplied |
| `api_calls` | HTTP calls this record consumed |
| `timestamp` | UTC timestamp |
