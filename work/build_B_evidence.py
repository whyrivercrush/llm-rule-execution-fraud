# -*- coding: utf-8 -*-
"""
Build B_isolation_evidence/ from existing logs and code.

Nothing is modified: this only extracts code text, reconstructs prompts from
logged rule_ids + the exact card_text, and writes comparison files.
"""

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "B_isolation_evidence"
sys.path.insert(0, str(PROJECT / "src"))
import run_experiment as rx  # noqa: E402


OLD_CODE = '''def _run_roles(
    cfg: Dict[str, Any],
    logger: logging.Logger,
    card_text: str,
    dry_ctx: Dict[str, Any],
) -> List[Dict[str, Any]]:
    specs = [
        (role, ROLE_RULE_MAP[role])
        for role in ("Entity", "Flow", "Anomaly")
    ]
    max_workers = max(1, int(cfg.get("concurrency", 1)))
    if max_workers <= 1:
        return [
            _call_one_role(cfg, logger, role, card_text, rules, dry_ctx)
            for role, rules in specs
        ]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as pool:
        futures = [
            pool.submit(_call_one_role, cfg, logger, role, card_text, rules, dry_ctx)
            for role, rules in specs
        ]
        results = [f.result() for f in futures]
    return results
'''

OLD_CALL_SITE = "    role_outputs = _run_roles(cfg, logger, card_text, dry_ctx)\n"


def load_records(path):
    return [json.loads(line) for line in Path(path).open(encoding="utf-8") if line.strip()]


def extract_current_code():
    text = (PROJECT / "src" / "run_experiment.py").read_text(encoding="utf-8")
    start = text.find("def _run_roles(")
    end = text.find("\n\ndef run_single_prompt_condition", start)
    if start < 0 or end < 0:
        raise RuntimeError("could not locate current _run_roles")
    func = text[start:end].rstrip() + "\n"
    call = "    role_outputs = _run_roles(cfg, logger, card_text, condition, dry_ctx)\n"
    if call not in text:
        raise RuntimeError("could not locate current call site")
    return func, call


def prompt_dump(card_text, role_records, rule_override=None):
    chunks = []
    role_outputs = []
    for role_rec in role_records:
        role = role_rec["role"]
        rule_ids = rule_override if rule_override is not None else role_rec["rule_ids"]
        messages = rx.build_role_messages(role, card_text, rule_ids)
        chunks.append(f"##### ROLE {role} | rule_ids={rule_ids} | SYSTEM #####\n{messages[0]['content']}\n")
        chunks.append(f"##### ROLE {role} | rule_ids={rule_ids} | USER #####\n{messages[1]['content']}\n")
        role_outputs.append({
            "role": role,
            "analysis": role_rec.get("analysis") or role_rec.get("raw_response") or "",
            "status": role_rec.get("status", "unknown"),
        })
    dm = rx.build_decision_messages(card_text, role_outputs)
    chunks.append(f"##### DECISION MAKER | SYSTEM #####\n{dm[0]['content']}\n")
    chunks.append(f"##### DECISION MAKER | USER #####\n{dm[1]['content']}\n")
    return "\n".join(chunks), role_outputs


def role_system_rule_hits(card_text, role_records, rule_sentences, override=None):
    hits = 0
    for role_rec in role_records:
        rule_ids = override if override is not None else role_rec["rule_ids"]
        messages = rx.build_role_messages(role_rec["role"], card_text, rule_ids)
        hits += sum(1 for s in rule_sentences if s in messages[0]["content"])
    return hits


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "old_B_prompts").mkdir(exist_ok=True)
    (OUT / "new_B_prompts").mkdir(exist_ok=True)

    new_code, new_call = extract_current_code()
    (OUT / "01_old_code_B_branch.txt").write_text(
        "SOURCE OF THIS TEXT\n"
        "No pre-fix file backup exists (no git, no .bak). This block is the exact\n"
        "text read from src/run_experiment.py in this session immediately before\n"
        "the B/D isolation patch; the call site follows it.\n\n"
        "----- pre-fix _run_roles (verbatim) -----\n" + OLD_CODE +
        "\n----- pre-fix call site (verbatim) -----\n" + OLD_CALL_SITE,
        encoding="utf-8", newline="\n",
    )
    (OUT / "02_new_code_B_branch.txt").write_text(
        "SOURCE OF THIS TEXT\n"
        "Extracted verbatim from the current src/run_experiment.py at evidence time.\n\n"
        "----- current _run_roles (verbatim) -----\n" + new_code +
        "\n----- current call site (verbatim) -----\n" + new_call,
        encoding="utf-8", newline="\n",
    )

    old_records = load_records(PROJECT / "logs" / "formal_run_condition_B.jsonl")
    new_records = load_records(PROJECT / "logs" / "pilot_run_condition_B_corrected.jsonl")
    new_by_tid = {r["transaction_id"]: r for r in new_records}
    cards = {
        x["TransactionID"]: x["card_text"]
        for x in json.loads((PROJECT / "data" / "test" / "test_cards_400.json").read_text(encoding="utf-8"))
    }

    selected = [
        r for r in old_records
        if r.get("status") == "success"
        and r.get("roles")
        and any(role.get("rule_ids") for role in r["roles"])
        and r["transaction_id"] in new_by_tid
        and all(role.get("status") == "success" for role in r["roles"])
    ][:5]
    if len(selected) < 3:
        raise RuntimeError("could not find 3+ comparable old/new B records")

    audit_rows = ["transaction_id,version,Entity_rules,Flow_rules,Anomaly_rules"]
    rule_sentences = list(rx.RULES.values())
    old_hits = new_hits = 0
    for old in selected:
        tid = old["transaction_id"]
        new = new_by_tid[tid]
        card_text = cards[tid]
        old_prompt, _ = prompt_dump(card_text, old["roles"])
        new_prompt, _ = prompt_dump(card_text, new["roles"])
        (OUT / "old_B_prompts" / f"old_B_{tid}.txt").write_text(
            f"TransactionID: {tid}\nCondition: B (OLD, invalid)\n"
            f"ground_truth_label: {old['ground_truth_label']}\n\n"
            f"===== CARD TEXT =====\n{card_text}\n\n{old_prompt}",
            encoding="utf-8", newline="\n",
        )
        (OUT / "new_B_prompts" / f"new_B_{tid}.txt").write_text(
            f"TransactionID: {tid}\nCondition: B (CORRECTED, roles only)\n"
            f"ground_truth_label: {new['ground_truth_label']}\n\n"
            f"===== CARD TEXT =====\n{card_text}\n\n{new_prompt}",
            encoding="utf-8", newline="\n",
        )
        old_rule_text = json.dumps(
            {r["role"]: r["rule_ids"] for r in old["roles"]}, ensure_ascii=False
        )
        new_rule_text = json.dumps(
            {r["role"]: r["rule_ids"] for r in new["roles"]}, ensure_ascii=False
        )
        audit_rows.append(f'{tid},old,"{old_rule_text}"')
        audit_rows.append(f'{tid},corrected,"{new_rule_text}"')
        old_hits += role_system_rule_hits(card_text, old["roles"], rule_sentences)
        new_hits += role_system_rule_hits(card_text, new["roles"], rule_sentences)
    (OUT / "03_rule_id_audit.csv").write_text("\n".join(audit_rows) + "\n",
                                             encoding="utf-8", newline="\n")

    all_old_bad = sum(
        1 for r in old_records if any(role.get("rule_ids") for role in r.get("roles") or [])
    )
    all_new_bad = sum(
        1 for r in new_records if any(role.get("rule_ids") for role in r.get("roles") or [])
    )
    summary = f"""# B isolation evidence

## Provenance

- Old code text: no pre-fix file backup exists (no git / no .bak). The block in
  `01_old_code_B_branch.txt` is the exact text read from the file in this session
  immediately before the patch. The old logs independently prove its behaviour.
- New code text: extracted verbatim from the current `src/run_experiment.py`.
- Logs do NOT store the literal prompt messages. They store per-role `rule_ids`,
  parsed analysis and raw model output. The prompt files here are reconstructed
  deterministically with the same `build_role_messages` / `build_decision_messages`
  functions, the logged `rule_ids`, and the exact card_text from
  `data/test/test_cards_400.json`. No wording was rewritten.

## Aggregate audit

- Old `logs/formal_run_condition_B.jsonl`: {all_old_bad} / {len(old_records)} records have non-empty role rule_ids.
- New `logs/pilot_run_condition_B_corrected.jsonl`: {all_new_bad} / {len(new_records)} records have non-empty role rule_ids.
- Selected samples: {', '.join(r['transaction_id'] for r in selected)}.
- Rule-sentence occurrences in reconstructed prompts: old = {old_hits}, corrected = {new_hits}.

## Per-role rule ids (selected samples)

See `03_rule_id_audit.csv` and the full prompt files in `old_B_prompts/` and
`new_B_prompts/`.

Expected old mapping (all three roles per sample):
Entity -> rule_1_email_anonymity; Flow -> rule_3_category_crossborder;
Anomaly -> rule_2_frequency_burst + rule_4_distance_mismatch.

Expected corrected mapping: all three roles -> [].

## Assertions run at build time

- every selected old role prompt contains its mapped rule sentence;
- every selected corrected role prompt contains none of the four rule sentences;
- old aggregate non-empty-rule records == 400;
- corrected aggregate non-empty-rule records == 0.
"""
    (OUT / "04_comparison_summary.md").write_text(summary, encoding="utf-8", newline="\n")

    if old_hits != len(selected) * 4:
        raise RuntimeError(
            f"old role prompts rule-sentence count {old_hits} != {len(selected) * 4}"
        )
    if new_hits != 0:
        raise RuntimeError("corrected prompts unexpectedly contain rule sentences")
    if all_old_bad != len(old_records) or all_new_bad != 0:
        raise RuntimeError("aggregate rule-id audit failed")

    print("evidence dir:", OUT)
    print("selected tids:", [r["transaction_id"] for r in selected])
    print("old non-empty rule records:", all_old_bad, "/", len(old_records))
    print("corrected non-empty rule records:", all_new_bad, "/", len(new_records))
    print("rule sentence occurrences: old =", old_hits, " corrected =", new_hits)


if __name__ == "__main__":
    main()
