import sys
from pathlib import Path

sys.path.insert(0, "src")
import run_experiment as rx

A = Path("formal_experiment_archive")
final = (A / "original_prompts/final_system_prompt.txt").read_text(encoding="utf-8")
entity = (A / "original_prompts/entity_prompt.txt").read_text(encoding="utf-8")
flow = (A / "original_prompts/flow_prompt.txt").read_text(encoding="utf-8")
anomaly = (A / "original_prompts/anomaly_prompt.txt").read_text(encoding="utf-8")
decision = (A / "original_prompts/decision_prompt.txt").read_text(encoding="utf-8")
rules = (A / "rules/rule_base.txt").read_text(encoding="utf-8")

checks = {
    "FINAL_SYSTEM_TEMPLATE": rx.FINAL_SYSTEM_TEMPLATE in final,
    "OUTPUT_SCHEMA_PROMPT": rx.OUTPUT_SCHEMA_PROMPT in final,
    "Entity definition": rx.ROLE_DEFINITIONS["Entity"] in entity,
    "Flow definition": rx.ROLE_DEFINITIONS["Flow"] in flow,
    "Anomaly definition": rx.ROLE_DEFINITIONS["Anomaly"] in anomaly,
    "ROLE_OUTPUT_PROMPT (entity)": rx.ROLE_OUTPUT_PROMPT in entity,
    "ROLE_RULE_MAP Entity": 'Entity\t["rule_1_email_anonymity"]' in rules,
    "ROLE_RULE_MAP Flow": 'Flow\t["rule_3_category_crossborder"]' in rules,
    "ROLE_RULE_MAP Anomaly": (
        'Anomaly\t["rule_2_frequency_burst", "rule_4_distance_mismatch"]' in rules
    ),
    "decision placeholders": all(
        x in decision
        for x in ["<<<CARD_TEXT>>>", "<<<ENTITY_ANALYSIS>>>",
                  "<<<FLOW_ANALYSIS>>>", "<<<ANOMALY_ANALYSIS>>>"]
    ),
}
for rid, text in rx.RULES.items():
    checks[f"rule {rid}"] = text in rules

for name, ok in checks.items():
    print(("OK  " if ok else "FAIL"), name)

if not all(checks.values()):
    raise SystemExit(1)
