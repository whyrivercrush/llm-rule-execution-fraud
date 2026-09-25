# -*- coding: utf-8 -*-
"""
Build formal_experiment_archive/ from existing, already-used artifacts.

This script only COPIES and DOCUMENTS. It does not modify prompts, rules,
conditions, model parameters, or experiment code.
"""

import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ARCHIVE = PROJECT / "formal_experiment_archive"
SRC = PROJECT / "src"

sys.path.insert(0, str(SRC))
import run_experiment as rx  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dirs():
    for rel in [
        "original_prompts",
        "experiment_code/other_required_scripts",
        "rules",
        "experiment_definition",
        "execution_record/previous_results/results",
        "execution_record/previous_results/logs",
        "execution_record/previous_results/data",
        "execution_record/metrics",
    ]:
        (ARCHIVE / rel).mkdir(parents=True, exist_ok=True)


def copy_verbatim(src: Path, dst: Path, inventory: list, label: str):
    if not src.exists():
        raise FileNotFoundError(f"missing source file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    src_hash, dst_hash = sha256(src), sha256(dst)
    if src_hash != dst_hash:
        raise RuntimeError(f"copy mismatch: {src} -> {dst}")
    inventory.append({
        "label": label,
        "archive_path": str(dst.relative_to(ARCHIVE)).replace("\\", "/"),
        "source_path": str(src),
        "size_bytes": dst.stat().st_size,
        "sha256": dst_hash,
    })


def write_text(rel: str, text: str, inventory: list, label: str, source: str):
    path = ARCHIVE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    inventory.append({
        "label": label,
        "archive_path": rel,
        "source_path": source,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    })


def section(title: str, text: str) -> str:
    return f"===== {title} =====\n{text}\n"


def main():
    ensure_dirs()
    inventory = []

    # ---------------- experiment_code ----------------
    code_files = [
        ("experiment_code/run_experiment.py", SRC / "run_experiment.py"),
        ("experiment_code/eval_metrics.py", SRC / "eval_metrics.py"),
        ("experiment_code/sample_formal_test.py", SRC / "sample_formal_test.py"),
        ("experiment_code/other_required_scripts/variance_probe.py", SRC / "variance_probe.py"),
        ("experiment_code/other_required_scripts/check_determinism.py", SRC / "check_determinism.py"),
        ("experiment_code/other_required_scripts/eval_corrected_B.py", SRC / "eval_corrected_B.py"),
        ("experiment_code/other_required_scripts/verify_prompt_integrity.py", SRC / "verify_prompt_integrity.py"),
        ("experiment_code/other_required_scripts/config.example.json", PROJECT / "config.example.json"),
        ("experiment_code/other_required_scripts/README_project.md", PROJECT / "README.md"),
    ]
    for rel, src in code_files:
        copy_verbatim(src, ARCHIVE / rel, inventory, "code")

    # ---------------- original_prompts ----------------
    write_text(
        "original_prompts/final_system_prompt.txt",
        section("FINAL_SYSTEM_TEMPLATE (exact source constant)",
                rx.FINAL_SYSTEM_TEMPLATE)
        + "\n"
        + section("OUTPUT_SCHEMA_PROMPT (exact source constant, inserted as {output_schema})",
                  rx.OUTPUT_SCHEMA_PROMPT),
        inventory, "prompt", "src/run_experiment.py constants",
    )
    for role, filename in [
        ("Entity", "entity_prompt.txt"),
        ("Flow", "flow_prompt.txt"),
        ("Anomaly", "anomaly_prompt.txt"),
    ]:
        write_text(
            f"original_prompts/{filename}",
            section(f'ROLE_DEFINITIONS["{role}"] (exact source constant)',
                    rx.ROLE_DEFINITIONS[role])
            + "\n"
            + section("ROLE_OUTPUT_PROMPT (exact source constant, appended by code)",
                      rx.ROLE_OUTPUT_PROMPT),
            inventory, "prompt", "src/run_experiment.py constants",
        )

    dm_messages = rx.build_decision_messages(
        "<<<CARD_TEXT>>>",
        [
            {"role": "Entity", "analysis": "<<<ENTITY_ANALYSIS>>>", "status": "success"},
            {"role": "Flow", "analysis": "<<<FLOW_ANALYSIS>>>", "status": "success"},
            {"role": "Anomaly", "analysis": "<<<ANOMALY_ANALYSIS>>>", "status": "success"},
        ],
    )
    write_text(
        "original_prompts/decision_prompt.txt",
        section("DECISION MAKER SYSTEM MESSAGE (exact output of build_final_system())",
                dm_messages[0]["content"])
        + "\n"
        + section("DECISION MAKER USER MESSAGE TEMPLATE (exact output of build_decision_messages with archive placeholders)",
                  dm_messages[1]["content"])
        + "\n"
        + section("ARCHIVE PLACEHOLDERS", "<<<CARD_TEXT>>> / <<<ENTITY_ANALYSIS>>> / "
                  "<<<FLOW_ANALYSIS>>> / <<<ANOMALY_ANALYSIS>>>"),
        inventory, "prompt", "src/run_experiment.py build_decision_messages",
    )

    # ---------------- rules ----------------
    source_text = (SRC / "run_experiment.py").read_text(encoding="utf-8")
    start_marker = "# ================= 实验协议正式锁定的 4 条电商反欺诈规则 ================="
    start = source_text.find(start_marker)
    end = source_text.find("\n\n\ndef utc_now_iso", start)
    if start < 0 or end < 0:
        raise RuntimeError("could not locate exact RULES / ROLE_RULE_MAP source block")
    rules_source = source_text[start:end].rstrip() + "\n"
    rendered = "\n".join(
        f"{rid}\t{rx.RULES[rid]}" for rid in rx.ALL_RULE_IDS
    )
    role_map = "\n".join(
        f"{role}\t{json.dumps(rx.ROLE_RULE_MAP[role], ensure_ascii=False)}"
        for role in ("Entity", "Flow", "Anomaly")
    )
    write_text(
        "rules/rule_base.txt",
        section("EXACT SOURCE BLOCK: RULES + ALL_RULE_IDS + ROLE_RULE_MAP", rules_source)
        + "\n"
        + section("RENDERED RULE TEXT (exact output of render_rules, one rule per line)",
                  rendered)
        + "\n"
        + section("ROLE_RULE_MAP (rendered)", role_map),
        inventory, "rules", "src/run_experiment.py",
    )

    # ---------------- experiment definition ----------------
    abcd = """A/B/C/D CONDITIONS - ACTUAL CODE BEHAVIOUR (post B/D isolation fix)

Source of truth: formal_experiment_archive/experiment_code/run_experiment.py

Call-count constants (CALLS_PER_SAMPLE): A=1, B=4, C=1, D=4.

Condition A
- Function: run_single_prompt_condition
- Messages: build_a_or_c_messages(card_text, rule_ids=None)
- System prompt: FINAL_SYSTEM_TEMPLATE + OUTPUT_SCHEMA_PROMPT
- Rules injected: none
- API calls per sample: 1 (single final JSON decision)

Condition B
- Function: run_role_condition
- Roles: Entity, Flow, Anomaly
- _run_roles(condition="B") -> rule_ids = [] for ALL three roles
- Role prompt = ROLE_DEFINITIONS[role] + "" + ROLE_OUTPUT_PROMPT
- Then build_decision_messages(card_text, role_outputs)
- API calls per sample: 3 role calls + 1 Decision Maker call = 4

Condition C
- Function: run_single_prompt_condition
- Messages: build_a_or_c_messages(card_text, rule_ids=ALL_RULE_IDS)
- System prompt: FINAL_SYSTEM_TEMPLATE with all four rules inserted as {extra}
  + OUTPUT_SCHEMA_PROMPT
- Rules injected: rule_1, rule_2, rule_3, rule_4
- API calls per sample: 1 (single final JSON decision)

Condition D
- Function: run_role_condition
- Roles: Entity, Flow, Anomaly
- _run_roles(condition="D") -> rule_ids = ROLE_RULE_MAP[role]
  Entity    -> [rule_1_email_anonymity]
  Flow      -> [rule_3_category_crossborder]
  Anomaly   -> [rule_2_frequency_burst, rule_4_distance_mismatch]
- Role prompt = ROLE_DEFINITIONS[role] + render_rules(rule_ids) + ROLE_OUTPUT_PROMPT
- Then build_decision_messages(card_text, role_outputs)
- API calls per sample: 3 role calls + 1 Decision Maker call = 4

Run-level audit
- 400 samples: A 400 + B 1600 + C 400 + D 1600 = 4,000 nominal API calls.
- --resume skips TransactionIDs already logged with status=success.
- 402/401/403/404/422 abort immediately without retry; partial progress is kept.

Historical note
- Before 2026-09-12, _run_roles did not receive `condition` and always injected
  ROLE_RULE_MAP into BOTH B and D. Old B results are therefore invalid and have
  been replaced by the corrected B run. The archived code already contains the
  fix; GLM must verify rather than re-apply it.
"""
    write_text("experiment_definition/ABCD_conditions.txt", abcd,
               inventory, "definition", "derived from src/run_experiment.py behaviour")

    # ---------------- execution_record: copy results / logs / data ----------------
    for path in sorted((PROJECT / "results").glob("*")):
        if path.is_file():
            copy_verbatim(
                path,
                ARCHIVE / "execution_record/previous_results/results" / path.name,
                inventory, "result",
            )
    for path in sorted((PROJECT / "logs").glob("*")):
        if path.is_file():
            copy_verbatim(
                path,
                ARCHIVE / "execution_record/previous_results/logs" / path.name,
                inventory, "log",
            )
    for name in ("test_cards_400.json", "test_cards_400_meta.json"):
        path = PROJECT / "data" / "test" / name
        copy_verbatim(
            path,
            ARCHIVE / "execution_record/previous_results/data" / name,
            inventory, "data",
        )

    metric_names = [
        "formal_metrics_summary.csv",
        "formal_metrics_bootstrap.json",
        "formal_pairwise_tests.csv",
        "formal_predictions_detail.csv",
        "corrected_B_metrics.csv",
        "corrected_B_pairwise_tests.csv",
        "corrected_B_results.md",
        "model_variance_50samples.json",
        "variance_run_metrics.csv",
        "formal_call_audit.csv",
        "formal_execution_report.md",
        "determinism_summary.md",
        "determinism_report.json",
        "determinism_report_seed_varied.json",
        "determinism_report_fixed_seed.json",
    ]
    for name in metric_names:
        src = PROJECT / "results" / name
        if src.exists():
            copy_verbatim(
                src,
                ARCHIVE / "execution_record/metrics" / name,
                inventory, "metrics",
            )

    known_issues = """KNOWN ISSUES FOUND IN EXISTING ARTIFACTS

Status at archive time: 2026-09-12 (Asia/Shanghai).
Do NOT change prompts, roles, rules, or A/B/C/D definitions while addressing these.

1. B/D isolation bug (historical, already fixed in archived code)
   - Old _run_roles() had no `condition` parameter and always used
     ROLE_RULE_MAP[role], so Condition B also received rules and became
     design-equivalent to D.
   - Fix already applied in the archived experiment_code/run_experiment.py:
     condition == "D" -> ROLE_RULE_MAP[role]; otherwise [].
   - GLM action: verify the fix and the log line
     "Condition B | Role ... | Rules []" before any rerun. Do not rewrite prompts.

2. Old Condition B results are invalid
   - logs/formal_run_condition_B.jsonl (old) has rule_ids on all 400 records.
   - Corrected B run: logs/pilot_run_condition_B_corrected.jsonl (400 records, all
     rule_ids empty, 1600/1600 calls).
   - Existing paper/report rows for old B, A-B, B-C, B-D must be replaced.

3. Variance probe B column (former issue, now corrected)
   - Old logs/variance_r*_condition_B.jsonl were produced under the buggy code.
   - A fixed-code variance rerun exists:
     rerun_2026-09-12/logs/variance_fixed_r1..r5_condition_B.jsonl
     (250 records, 0 rule_ids violations; same 50-sample subset, seed=200).
   - The canonical results/model_variance_50samples.json now contains corrected B
     (A/C/D kept from the original report); pre-fix copies are preserved as
     results/*_prerefix.json / *_prerefix.csv.
   - No additional B-only API calls are required unless a fresh repeat is wanted.

4. logs/run_summary.json was overwritten by the corrected-B-only run
   - Original 4,000-call main-run audit is preserved in
     results/formal_call_audit.csv and results/formal_execution_report.md.

5. Executor-model determinism (historical finding)
   - temperature=0.0 does NOT guarantee identical output; some endpoints do not
     document or honor a seed parameter. Fixed-seed repeats still differed.
   - Single-run bootstrap covers evaluation-set sampling uncertainty only.
     Run-to-run model variance must come from the repeated-run probe.

6. config.json is intentionally NOT archived
   - It contains a live API key. Use config.example.json and provide a new key.

7. Dataset split type
   - data/test/test_cards_400.json is a stratified random split (200/200,
     seed=100, pilot IDs excluded), not a temporal split.

8. Corrected-B result changes the substantive story
   - Roles-only B does not beat A on accuracy/MCC; it shifts toward more
     fraud-positive answers (recall/F1 up, precision/MCC/AUC slightly down).
   - C and D significantly beat corrected B, so on this dataset the four rules,
     not the role scaffold alone, carry the gain.

9. Legacy endpoint misconfiguration (execution-critical)
   - An earlier rerun under `rerun_2026-09-12/` was executed with a legacy
     endpoint/model configuration instead of the intended GLM/zcode endpoint.
     Those artifacts must NOT be labelled as a GLM run.
   - All new executions must use the GLM template
     `experiment_code/other_required_scripts/config.example.json`
     (base_url `https://open.bigmodel.cn/api/paas/v4/`) and a GLM/zcode model.
   - Do not copy endpoint or model values from any historical result file.
"""
    write_text("execution_record/known_issues.txt", known_issues,
               inventory, "known_issues", "audit of existing artifacts")

    # ---------------- README + GLM instruction ----------------
    readme = """# formal_experiment_archive

> **HANDOFF WARNING**: for the zcode/GLM workspace use
> `../formal_experiment_archive_for_zcode` (sanitized). This raw archive keeps
> historical model metadata for audit and must NOT be handed to zcode.

This directory is a faithful archive of the experiment files that already
exist in the project. Nothing here is a redesign.

## What is copied verbatim

- `experiment_code/` - the exact code versions used to run the experiments
  (`run_experiment.py`, `eval_metrics.py`, `sample_formal_test.py`), plus
  supporting scripts under `other_required_scripts/`.
- `original_prompts/` - prompts extracted directly from the code constants.
  Lines like `===== ... =====` are archive labels, not part of the prompt.
  The runtime role prompt is `definition + rendered rules (D only) + ROLE_OUTPUT_PROMPT`.
- `rules/rule_base.txt` - the exact `RULES` / `ALL_RULE_IDS` / `ROLE_RULE_MAP`
  source block, plus the exact rendered rule text.
- `execution_record/previous_results/` - all existing results, logs and the
  exact 400-sample test set used in the formal runs.
- `execution_record/metrics/` - the metric/test files grouped for convenience.
- `execution_record/known_issues.txt` - issues already discovered and their
  current status.

## Important exclusions

- `config.json` is NOT copied because it contains a live API key. Use
  `experiment_code/other_required_scripts/config.example.json`.
- The large raw Kaggle files are not copied; the exact sampled test set used
  by the experiments is included under `previous_results/data/`.

## Provenance

- Archived at: {timestamp}
- Source project: {project}
- B/D isolation fix: present in the archived `run_experiment.py`.
- Old B results: invalid, superseded by `pilot_run_condition_B_corrected.jsonl`.

See `execution_record/file_inventory.csv` for per-file size and SHA-256.
"""
    readme = readme.format(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        project=str(PROJECT),
    )
    write_text("README.md", readme, inventory, "readme", "generated archive overview")

    glm = """# GLM execution instruction

You are an EXECUTOR, not the experiment designer.

## Hard constraints

1. Do not rewrite, reword, or "improve" any prompt under `original_prompts/`.
2. Do not change role definitions or add/remove roles.
3. Do not modify the rules in `rules/rule_base.txt`.
4. Do not change the A/B/C/D condition definitions in
   `experiment_definition/ABCD_conditions.txt`.
5. Use the GLM/zcode endpoint configured for this executor:
   base_url `https://open.bigmodel.cn/api/paas/v4/`.
   Keep temperature=0.0, the JSON-mode setting and max_tokens consistent with
   the protocol; do not substitute any other API endpoint.

## Starting procedure

1. Read every file in this archive first, especially:
   - `experiment_definition/ABCD_conditions.txt`
   - `execution_record/known_issues.txt`
   - `experiment_code/run_experiment.py`
2. Verify the B/D isolation fix in the archived code:
   - `_run_roles(..., condition, ...)` takes `condition`;
   - `condition == "D"` uses `ROLE_RULE_MAP`; otherwise `[]`.
   A dry run must print `Condition B | Role ... | Rules []` and
   `Condition D | Role ... | Rules ['rule_...']`.
3. Create `config.json` from
   `experiment_code/other_required_scripts/config.example.json`
   (GLM template) with your own zcode/GLM API key and model name.
   Never commit or share the API key. Do not reuse any historical endpoint.
4. Rerun the FULL formal experiment with the fixed code so that A/B/C/D all
   come from the same code version:
   `python src/run_experiment.py --config config.json --concurrency 3
    --sample-concurrency 4 --log-prefix formal_run_fixed --resume`
   - 400 samples -> A 400 + B 1600 + C 400 + D 1600 = 4,000 calls.
   - If it stops (e.g. HTTP 402 insufficient balance), top up and rerun the
     SAME command; `--resume` skips already-successful TransactionIDs.
5. Recompute metrics and tests:
   `python src/eval_metrics.py --log-prefix formal_run_fixed --bootstrap 1000`
6. Rerun the variance probe with the fixed code (this fixes the stale B column):
   `python src/variance_probe.py --config config.json --log-dir logs
    --results-dir results --repeats 5 --sample-concurrency 4`
   - 50 samples x 5 repeats x 10 calls = 2,500 calls.
7. Replace all old Condition-B results (and old B-involving McNemar rows) in
   any report with the new fixed-code numbers. Old B files are invalid.
8. Verify that request logs and configs point only to the GLM/zcode endpoint;
   if a historical artifact is reused for comparison, relabel it as a
   non-GLM historical run rather than mixing it into GLM results.

## What NOT to do

- Do not rename A/B/C/D or reinterpret them.
- Do not add new conditions, roles, rules, or prompt modules.
- Do not change temperature, model, or dataset to chase better numbers.
- Do not delete old logs; keep them for audit and write new logs alongside.

## Success criteria

- All four conditions run from the same fixed code version.
- Call audit matches the nominal counts (4,000 main + 2,500 variance).
- B logs show empty rule lists; D logs show the role-rule mapping.
- Metrics contain no `n_invalid` records and the corrected B replaces the old B.
"""
    write_text("GLM_execution_instruction.md", glm,
               inventory, "instruction", "generated executor instruction")

    # ---------------- inventory ----------------
    inventory_path = ARCHIVE / "execution_record/file_inventory.csv"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    with inventory_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["label", "archive_path", "source_path", "size_bytes", "sha256"]
        )
        writer.writeheader()
        writer.writerows(inventory)

    # ---------------- safety checks ----------------
    secret_pattern = re.compile(r"sk-[A-Za-z0-9]{16,}")
    leaked = []
    for path in ARCHIVE.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".md", ".txt", ".json", ".csv"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if secret_pattern.search(text):
                leaked.append(str(path))
    if leaked:
        raise RuntimeError(f"API key pattern found in archive: {leaked}")

    print(f"archive root: {ARCHIVE}")
    print(f"inventory rows: {len(inventory)}")
    total = sum(x["size_bytes"] for x in inventory)
    print(f"archived bytes (inventory files): {total} ({total / 1024 / 1024:.2f} MB)")
    print("security check: no sk- API key pattern found")
    for rel in [
        "README.md",
        "GLM_execution_instruction.md",
        "original_prompts/final_system_prompt.txt",
        "rules/rule_base.txt",
        "experiment_definition/ABCD_conditions.txt",
        "execution_record/known_issues.txt",
    ]:
        print(" ", rel)


if __name__ == "__main__":
    main()
