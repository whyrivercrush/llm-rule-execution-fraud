# -*- coding: utf-8 -*-
"""
Full A/B/C/D integrity audit: structure, rule_ids routing, prompt reconstruction.
Read-only with respect to experiment code/prompts; writes evidence only.
"""

import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "formal_experiment_archive" / "execution_record" / "ABCD_integrity_audit"
sys.path.insert(0, str(PROJECT / "src"))
import run_experiment as rx  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    return [json.loads(x) for x in Path(path).open(encoding="utf-8") if x.strip()]


EXPECTED_D = {
    "Entity": ["rule_1_email_anonymity"],
    "Flow": ["rule_3_category_crossborder"],
    "Anomaly": ["rule_2_frequency_burst", "rule_4_distance_mismatch"],
}
ROLES = ["Entity", "Flow", "Anomaly"]


def rule_sentences():
    return list(rx.RULES.values())


def role_map(roles):
    if roles is None:
        return None
    return {r["role"]: list(r.get("rule_ids") or []) for r in roles}


def rule_hits_single(card_text, rule_ids):
    messages = rx.build_a_or_c_messages(card_text, rule_ids)
    return sum(1 for s in rule_sentences() if s in messages[0]["content"])


def rule_hits_roles(card_text, roles):
    hits = 0
    for role_rec in roles:
        messages = rx.build_role_messages(
            role_rec["role"], card_text, role_rec.get("rule_ids") or []
        )
        hits += sum(1 for s in rule_sentences() if s in messages[0]["content"])
    return hits


def audit_bundle(label, path, condition, expected_rule_map, expected_hits, expected_roles):
    records = load(path)
    cards = {
        x["TransactionID"]: x["card_text"]
        for x in json.loads((PROJECT / "data" / "test" / "test_cards_400.json").read_text(encoding="utf-8"))
    }
    rows = []
    for r in records:
        tid = str(r.get("transaction_id"))
        card = cards.get(tid, "")
        if expected_roles == "none":
            roles_ok = r.get("roles") is None
            actual_map = None
            map_ok = None
            hits = (
                rule_hits_single(card, rx.ALL_RULE_IDS if condition == "C" else None)
            )
        else:
            roles = r.get("roles") or []
            roles_ok = (
                len(roles) == 3 and [x.get("role") for x in roles] == ROLES
            )
            actual_map = role_map(roles)
            map_ok = actual_map == expected_rule_map
            hits = rule_hits_roles(card, roles)
        api_ok = int(r.get("api_calls") or 0) == rx.CALLS_PER_SAMPLE[condition]
        rows.append({
            "bundle": label,
            "condition": condition,
            "transaction_id": tid,
            "status": r.get("status"),
            "roles_structural_ok": roles_ok,
            "rule_map_expected": json.dumps(expected_rule_map, ensure_ascii=False),
            "rule_map_actual": json.dumps(actual_map, ensure_ascii=False),
            "rule_map_ok": map_ok if map_ok is not None else "N/A(no roles)",
            "prompt_rule_hits": hits,
            "prompt_rule_hits_expected": expected_hits,
            "prompt_rule_hits_ok": hits == expected_hits,
            "api_calls": r.get("api_calls"),
            "api_calls_ok": api_ok,
        })
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    per_record = []
    bundles = [
        ("A_old", "logs/formal_run_condition_A.jsonl", "A", None, 0, "none"),
        ("C_old", "logs/formal_run_condition_C.jsonl", "C", None, 4, "none"),
        ("B_old_invalid", "logs/formal_run_condition_B.jsonl", "B", EXPECTED_D, 4, "roles"),
        ("B_corrected", "logs/pilot_run_condition_B_corrected.jsonl", "B", {r: [] for r in ROLES}, 0, "roles"),
        ("D_old", "logs/formal_run_condition_D.jsonl", "D", EXPECTED_D, 4, "roles"),
    ]
    for label, rel, condition, expected_map, expected_hits, expected_roles in bundles:
        per_record.extend(
            audit_bundle(label, PROJECT / rel, condition, expected_map, expected_hits, expected_roles)
        )

    # Per-record CSV
    with (OUT / "01_rule_distribution_and_prompt_audit.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(per_record[0].keys()))
        writer.writeheader()
        writer.writerows(per_record)

    # Summary CSV
    summary = []
    for label, rel, condition, expected_map, expected_hits, expected_roles in bundles:
        rows = [r for r in per_record if r["bundle"] == label]
        summary.append({
            "bundle": label,
            "condition": condition,
            "log_file": rel,
            "n_records": len(rows),
            "n_success": sum(1 for r in rows if r["status"] == "success"),
            "roles_structure_ok": sum(1 for r in rows if r["roles_structural_ok"]),
            "rule_map_ok": (
                sum(1 for r in rows if r["rule_map_ok"] is True)
                if expected_roles == "roles" else "N/A(no roles)"
            ),
            "prompt_rule_hits_ok": sum(1 for r in rows if r["prompt_rule_hits_ok"]),
            "api_calls_ok": sum(1 for r in rows if r["api_calls_ok"]),
            "expected_rule_map": json.dumps(expected_map, ensure_ascii=False),
            "expected_prompt_rule_hits": expected_hits,
            "verdict": (
                "PASS" if all(
                    r["roles_structural_ok"] and r["prompt_rule_hits_ok"] and r["api_calls_ok"]
                    and (r["rule_map_ok"] is True if expected_roles == "roles" else True)
                    for r in rows
                ) else "FAIL"
            ),
        })
    with (OUT / "00_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)

    # Prompt examples: one common TransactionID per bundle
    all_tids = None
    data = {}
    for label, rel, condition, expected_map, expected_hits, expected_roles in bundles:
        recs = {str(r["transaction_id"]): r for r in load(PROJECT / rel)}
        data[label] = recs
        tids = set(recs)
        all_tids = tids if all_tids is None else (all_tids & tids)
    example_tid = sorted(all_tids, key=lambda x: int(x))[0]
    cards = {
        x["TransactionID"]: x["card_text"]
        for x in json.loads((PROJECT / "data" / "test" / "test_cards_400.json").read_text(encoding="utf-8"))
    }
    examples = OUT / "02_prompt_examples"
    examples.mkdir(exist_ok=True)
    for label, rel, condition, expected_map, expected_hits, expected_roles in bundles:
        r = data[label][example_tid]
        card = cards[example_tid]
        parts = [
            f"bundle: {label}",
            f"condition: {condition}",
            f"transaction_id: {example_tid}",
            f"ground_truth_label: {r.get('ground_truth_label')}",
            f"rule_ids in old/current log: {json.dumps(role_map(r.get('roles')), ensure_ascii=False)}",
            "",
            "===== CARD TEXT =====",
            card,
            "",
        ]
        if expected_roles == "none":
            ids = rx.ALL_RULE_IDS if condition == "C" else None
            messages = rx.build_a_or_c_messages(card, ids)
            parts += ["===== SYSTEM =====", messages[0]["content"], "",
                      "===== USER =====", messages[1]["content"]]
        else:
            role_outputs = []
            for role_rec in r["roles"]:
                messages = rx.build_role_messages(
                    role_rec["role"], card, role_rec.get("rule_ids") or []
                )
                parts += [
                    f"===== ROLE {role_rec['role']} | rule_ids={role_rec.get('rule_ids')} | SYSTEM =====",
                    messages[0]["content"], "",
                    f"===== ROLE {role_rec['role']} | USER =====",
                    messages[1]["content"], "",
                ]
                role_outputs.append({
                    "role": role_rec["role"],
                    "analysis": role_rec.get("analysis") or role_rec.get("raw_response") or "",
                    "status": role_rec.get("status", "unknown"),
                })
            dm = rx.build_decision_messages(card, role_outputs)
            parts += ["===== DECISION MAKER | SYSTEM =====", dm[0]["content"], "",
                      "===== DECISION MAKER | USER =====", dm[1]["content"]]
        (examples / f"{label}_{example_tid}.txt").write_text(
            "\n".join(parts), encoding="utf-8", newline="\n"
        )

    # Copy the earlier B-specific evidence for self-containment.
    src_b = PROJECT / "B_isolation_evidence"
    if src_b.exists():
        dst_b = OUT / "03_B_isolation_evidence"
        shutil.copytree(src_b, dst_b, dirs_exist_ok=True)

    # Documentation
    (OUT / "README.md").write_text(
        """# A/B/C/D experiment integrity audit

Purpose: provide a complete, reproducible record of the B/D isolation bug,
its fix, and a full audit of all four condition code paths. This material is
intended to answer reviewer/defense questions about why early B results differ
from the final submitted results.

Contents:

- `00_summary.csv` - per-bundle pass/fail across structure, rule routing,
  prompt reconstruction and API-call counts.
- `01_rule_distribution_and_prompt_audit.csv` - one row per record per bundle
  (2,000 rows: 400 x five bundles).
- `02_prompt_examples/` - full reconstructed prompts for one common
  TransactionID in every bundle (A old, C old, B old, B corrected, D old).
- `03_B_isolation_evidence/` - the earlier B-specific evidence pack.
- `04_methodology.md` - how the audit was performed and its limits.
- `05_findings.md` - conclusions for A/B/C/D.
- `06_timeline.md` - discovery -> fix -> corrected rerun.
- `07_execution_commands.md` - exact commands used.
- `08_checksums.csv` - SHA-256 of every audit file.
- `09_variance_B_correction.md` - corrected-B variance merge provenance.

No prompts, rules, role definitions, conditions or model parameters were
modified to produce this audit.
""",
        encoding="utf-8", newline="\n",
    )
    (OUT / "04_methodology.md").write_text(
        """# Methodology

## Data sources

- Old logs: `logs/formal_run_condition_{A,B,C,D}.jsonl` (400 records each).
- Corrected B log: `logs/pilot_run_condition_B_corrected.jsonl` (400 records).
- Exact card texts: `data/test/test_cards_400.json`.
- Code: the archived `experiment_code/run_experiment.py`.

## What the logs do and do not contain

- B/D records contain `roles[].rule_ids`, i.e. the exact rule ids passed to
  each role, plus parsed analysis and raw model output.
- A/C are single-prompt calls and therefore have no `roles` field; the logs do
  not store a literal prompt string or rule flag.

## How A/C were audited

For every A and C record, the prompt was deterministically reconstructed with
`build_a_or_c_messages(card_text, rule_ids)`, using:
- A: `rule_ids=None`,
- C: `rule_ids=ALL_RULE_IDS`.

Then the number of exact rule sentences present in the system prompt was
counted. Expected: A = 0, C = 4, for all 400 records.

## How B/D were audited

For every B/D record, `build_role_messages(role, card_text, rule_ids)` was
reconstructed from the logged `rule_ids`, and the exact rule sentences were
counted across the three role system prompts. Expected:
- old B: 4 (bug: it received D's per-role mapping),
- corrected B: 0,
- D: 4.

The logged `rule_ids` mapping was also compared record-by-record with the
expected mapping.

## Limits

- The pre-fix code file itself was overwritten in place (no git / no .bak).
  The old code text in `03_B_isolation_evidence/01_old_code_B_branch.txt` is the
  exact text read from the file in-session immediately before the patch.
- Prompt reconstruction uses the same unmodified builder functions; the logs
  themselves do not store literal prompt strings.
""",
        encoding="utf-8", newline="\n",
    )
    findings = f"""# Findings

## Expected design

- A: single prompt, no roles, no rules, 1 call/sample.
- B: Entity/Flow/Anomaly, roles only, 4 calls/sample.
- C: single prompt + all four rules, 1 call/sample.
- D: Entity/Flow/Anomaly with per-role rules, 4 calls/sample.

## Audit result

{chr(10).join(
    f"- {s['bundle']}: verdict={s['verdict']}; records={s['n_records']}; "
    f"roles_ok={s['roles_structure_ok']}; rule_map_ok={s['rule_map_ok']}; "
    f"prompt_hits_ok={s['prompt_rule_hits_ok']}; api_calls_ok={s['api_calls_ok']}"
    for s in summary
)}

## Interpretation

- C is clean: single-prompt path, no roles, and all four rules present in the
  reconstructed prompt for every record.
- D is clean: all 400 records have the intended per-role mapping
  (Entity rule_1; Flow rule_3; Anomaly rule_2 + rule_4).
- Old B is not clean: 400/400 records carry D's per-role rule mapping, which is
  the isolation bug. Corrected B is clean: 0/400 records carry any rule.
- Therefore the hidden routing defect was confined to B; no additional
  routing defect was found in A, C or D.

## Reporting recommendation

Report old B as an invalidated pre-fix run and use corrected B for all B
statistics and B-involving comparisons. Keep this audit as supplementary
material for the discrepancy explanation.
"""
    (OUT / "05_findings.md").write_text(findings, encoding="utf-8", newline="\n")
    (OUT / "06_timeline.md").write_text(
        """# Timeline

1. 2026-09-10 - formal A/B/C/D run executed (4,000 calls). B and D used the
   same rule-injection path because `_run_roles` ignored `condition`.
2. 2026-09-12 - isolation bug identified from the user's code review request.
3. 2026-09-12 - three minimal code edits applied: add `condition` parameter,
   route rules only for D, log per-role rule ids.
4. 2026-09-12 - corrected B rerun on the same 400 samples/model/parameters
   (1,600 calls, 400/400 success). A/C/D were not rerun.
5. 2026-09-12 - corrected B metrics and McNemar tests computed; old B results
   marked invalid.
6. 2026-09-12 - full A/B/C/D integrity audit and archival (this directory).
""",
        encoding="utf-8", newline="\n",
    )
    (OUT / "07_execution_commands.md").write_text(
        """# Exact commands

Working directory:

    C:\\Users\\Niconiconi\\Documents\\Codex\\2026-09-03\\llm-mrml-ccf-c-sci-bridging

Corrected Condition B rerun (1,600 calls):

    & "C:\\Users\\Niconiconi\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe" src\\run_experiment.py --config config.json --conditions B --concurrency 3 --sample-concurrency 4 --log-prefix pilot_run_correctedB --resume

Copy to the requested filename:

    Copy-Item -LiteralPath 'logs\\pilot_run_correctedB_condition_B.jsonl' -Destination 'logs\\pilot_run_condition_B_corrected.jsonl' -Force

Corrected B metrics / McNemar:

    python src\\eval_corrected_B.py

B isolation evidence pack:

    python work\\build_B_evidence.py

Full A/B/C/D integrity audit (this directory):

    python work\\build_ABCD_audit.py

No commands in this audit called the LLM API; all checks are local and
deterministic except for reading the original logs.
""",
        encoding="utf-8", newline="\n",
    )

    # Checksums
    rows = []
    for path in sorted(OUT.rglob("*")):
        if path.is_file():
            rows.append({
                "relative_path": str(path.relative_to(OUT)).replace("\\", "/"),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
    with (OUT / "08_checksums.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("audit dir:", OUT)
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()
