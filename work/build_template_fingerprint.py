# -*- coding: utf-8 -*-
"""Create SHA-256 fingerprints for the fixed prompt templates used by A/B/C/D."""

import hashlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
import run_experiment as rx  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    card = "<<<CARD_TEXT>>>"
    analyses = [
        {"role": "Entity", "analysis": "<<<ENTITY_ANALYSIS>>>", "status": "success"},
        {"role": "Flow", "analysis": "<<<FLOW_ANALYSIS>>>", "status": "success"},
        {"role": "Anomaly", "analysis": "<<<ANOMALY_ANALYSIS>>>", "status": "success"},
    ]
    a_msgs = rx.build_a_or_c_messages(card, None)
    c_msgs = rx.build_a_or_c_messages(card, rx.ALL_RULE_IDS)
    dm = rx.build_decision_messages(card, analyses)
    role_systems = {}
    for condition, mapping in (
        ("B", {"Entity": [], "Flow": [], "Anomaly": []}),
        ("D", rx.ROLE_RULE_MAP),
    ):
        for role in ("Entity", "Flow", "Anomaly"):
            msgs = rx.build_role_messages(role, card, mapping[role])
            role_systems[f"{condition}_{role}_system"] = msgs[0]["content"]
            role_systems[f"{condition}_{role}_user"] = msgs[1]["content"]

    report = {
        "source": "src/run_experiment.py (post B/D isolation fix)",
        "note": (
            "These hashes identify the fixed prompt templates. The formal A/C/D "
            "run and the corrected B run used these same templates; the only "
            "difference across runs was the rule_ids variable passed into B "
            "(old B wrongly received D's mapping; corrected B receives [])."
        ),
        "constants": {
            "FINAL_SYSTEM_TEMPLATE": sha(rx.FINAL_SYSTEM_TEMPLATE),
            "OUTPUT_SCHEMA_PROMPT": sha(rx.OUTPUT_SCHEMA_PROMPT),
            "ROLE_OUTPUT_PROMPT": sha(rx.ROLE_OUTPUT_PROMPT),
            "ROLE_DEFINITIONS_Entity": sha(rx.ROLE_DEFINITIONS["Entity"]),
            "ROLE_DEFINITIONS_Flow": sha(rx.ROLE_DEFINITIONS["Flow"]),
            "ROLE_DEFINITIONS_Anomaly": sha(rx.ROLE_DEFINITIONS["Anomaly"]),
            "RULES_json": sha(json.dumps(rx.RULES, ensure_ascii=False, sort_keys=True)),
            "ROLE_RULE_MAP_json": sha(json.dumps(rx.ROLE_RULE_MAP, ensure_ascii=False, sort_keys=True)),
        },
        "assembled_with_placeholders": {
            "A_system": sha(a_msgs[0]["content"]),
            "A_user": sha(a_msgs[1]["content"]),
            "C_system": sha(c_msgs[0]["content"]),
            "C_user": sha(c_msgs[1]["content"]),
            "decision_system": sha(dm[0]["content"]),
            "decision_user": sha(dm[1]["content"]),
            **{k: sha(v) for k, v in role_systems.items()},
        },
        "rule_ids_by_condition": {
            "A": "none (single prompt, no roles)",
            "B": {role: [] for role in ("Entity", "Flow", "Anomaly")},
            "C": list(rx.ALL_RULE_IDS),
            "D": rx.ROLE_RULE_MAP,
        },
    }
    out = PROJECT / "formal_experiment_archive" / "original_prompts" / "template_fingerprint.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", out)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
