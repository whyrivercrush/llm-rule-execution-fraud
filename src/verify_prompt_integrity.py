# -*- coding: utf-8 -*-
"""
verify_prompt_integrity.py

Machine-check that the executor is using the FROZEN prompt templates from this
archive, byte-for-byte. Run this BEFORE any experiment:

    python experiment_code/other_required_scripts/verify_prompt_integrity.py

It verifies:
1. prompt constants match original_prompts/template_fingerprint.json;
2. assembled A/C/B/D templates match the fingerprint;
3. original_prompts/*.txt contain the exact constants;
4. B maps to empty rule lists and D maps to the frozen per-role rules.

Exit code 0 = PASS; any mismatch = non-zero, and the run must be stopped.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict


_HERE = Path(__file__).resolve().parent


def _find_archive_root(start: Path) -> Path:
    """Locate the directory holding original_prompts/template_fingerprint.json.

    This script historically lived at <archive>/experiment_code/other_required_scripts/,
    so it could hard-code `parents[2]`. In the released repository it sits in `src/`,
    where that depth is wrong, so we search upward instead of assuming a layout.
    """
    for base in (start, *start.parents):
        if (base / "original_prompts" / "template_fingerprint.json").is_file():
            return base
    return start.parents[2] if len(start.parents) > 2 else start


def _find_code_dir(archive_root: Path) -> Path:
    """Directory containing run_experiment.py (the frozen executor)."""
    for cand in (_HERE, archive_root / "experiment_code", archive_root / "src"):
        if (cand / "run_experiment.py").is_file():
            return cand
    return archive_root / "experiment_code"


ARCHIVE_ROOT = _find_archive_root(_HERE)
CODE_DIR = _find_code_dir(ARCHIVE_ROOT)
sys.path.insert(0, str(CODE_DIR))
import run_experiment as rx  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    fingerprint_path = ARCHIVE_ROOT / "original_prompts" / "template_fingerprint.json"
    if not fingerprint_path.is_file():
        print("Prompt integrity verification")
        print("  archive root:", ARCHIVE_ROOT)
        print("  RESULT: CANNOT RUN")
        print("   - fingerprint file not found:", fingerprint_path)
        print("     Ship original_prompts/ alongside the executor, or run this script")
        print("     from inside the handoff archive.")
        return 2
    fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))

    card = "<<<CARD_TEXT>>>"
    analyses = [
        {"role": "Entity", "analysis": "<<<ENTITY_ANALYSIS>>>", "status": "success"},
        {"role": "Flow", "analysis": "<<<FLOW_ANALYSIS>>>", "status": "success"},
        {"role": "Anomaly", "analysis": "<<<ANOMALY_ANALYSIS>>>", "status": "success"},
    ]
    a_msgs = rx.build_a_or_c_messages(card, None)
    c_msgs = rx.build_a_or_c_messages(card, rx.ALL_RULE_IDS)
    dm = rx.build_decision_messages(card, analyses)

    constants: Dict[str, Any] = {
        "FINAL_SYSTEM_TEMPLATE": sha(rx.FINAL_SYSTEM_TEMPLATE),
        "OUTPUT_SCHEMA_PROMPT": sha(rx.OUTPUT_SCHEMA_PROMPT),
        "ROLE_OUTPUT_PROMPT": sha(rx.ROLE_OUTPUT_PROMPT),
        "ROLE_DEFINITIONS_Entity": sha(rx.ROLE_DEFINITIONS["Entity"]),
        "ROLE_DEFINITIONS_Flow": sha(rx.ROLE_DEFINITIONS["Flow"]),
        "ROLE_DEFINITIONS_Anomaly": sha(rx.ROLE_DEFINITIONS["Anomaly"]),
        "RULES_json": sha(json.dumps(rx.RULES, ensure_ascii=False, sort_keys=True)),
        "ROLE_RULE_MAP_json": sha(
            json.dumps(rx.ROLE_RULE_MAP, ensure_ascii=False, sort_keys=True)
        ),
    }
    assembled: Dict[str, Any] = {
        "A_system": sha(a_msgs[0]["content"]),
        "A_user": sha(a_msgs[1]["content"]),
        "C_system": sha(c_msgs[0]["content"]),
        "C_user": sha(c_msgs[1]["content"]),
        "decision_system": sha(dm[0]["content"]),
        "decision_user": sha(dm[1]["content"]),
    }
    for condition, mapping in (
        ("B", {"Entity": [], "Flow": [], "Anomaly": []}),
        ("D", rx.ROLE_RULE_MAP),
    ):
        for role in ("Entity", "Flow", "Anomaly"):
            msgs = rx.build_role_messages(role, card, mapping[role])
            assembled[f"{condition}_{role}_system"] = sha(msgs[0]["content"])
            assembled[f"{condition}_{role}_user"] = sha(msgs[1]["content"])

    errors = []
    for key, expected in fingerprint["constants"].items():
        if constants.get(key) != expected:
            errors.append(f"constant mismatch: {key}")
    for key, expected in fingerprint["assembled_with_placeholders"].items():
        if assembled.get(key) != expected:
            errors.append(f"assembled template mismatch: {key}")

    prompt_files = {
        "final_system_prompt.txt": [
            rx.FINAL_SYSTEM_TEMPLATE,
            rx.OUTPUT_SCHEMA_PROMPT,
        ],
        "entity_prompt.txt": [
            rx.ROLE_DEFINITIONS["Entity"],
            rx.ROLE_OUTPUT_PROMPT,
        ],
        "flow_prompt.txt": [
            rx.ROLE_DEFINITIONS["Flow"],
            rx.ROLE_OUTPUT_PROMPT,
        ],
        "anomaly_prompt.txt": [
            rx.ROLE_DEFINITIONS["Anomaly"],
            rx.ROLE_OUTPUT_PROMPT,
        ],
    }
    for name, texts in prompt_files.items():
        content = (ARCHIVE_ROOT / "original_prompts" / name).read_text(encoding="utf-8")
        for index, text in enumerate(texts):
            if text not in content:
                errors.append(f"prompt file missing exact text: {name} part {index}")

    expected_map = {
        "B": {"Entity": [], "Flow": [], "Anomaly": []},
        "D": rx.ROLE_RULE_MAP,
    }
    if fingerprint["rule_ids_by_condition"]["B"] != expected_map["B"]:
        errors.append("fingerprint B rule map mismatch")
    if fingerprint["rule_ids_by_condition"]["D"] != expected_map["D"]:
        errors.append("fingerprint D rule map mismatch")
    if rx.ROLE_RULE_MAP != expected_map["D"]:
        errors.append("runtime D rule map mismatch")

    print("Prompt integrity verification")
    print("  archive root:", ARCHIVE_ROOT)
    print("  fingerprint :", fingerprint_path)
    if errors:
        print("  RESULT: FAIL")
        for error in errors:
            print("   -", error)
        print("STOP: restore prompt templates from this archive; do not regenerate them.")
        return 1
    print("  RESULT: PASS")
    print("  prompt templates are byte-identical to the frozen fingerprint.")
    print("  B rule lists are empty; D uses the frozen per-role mapping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
