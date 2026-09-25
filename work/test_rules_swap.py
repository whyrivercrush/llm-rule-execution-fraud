import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import run_experiment as r

card = "dummy card text"

assert set(r.ALL_RULE_IDS) == set(r.RULES.keys())
assert set(r.ROLE_RULE_MAP.keys()) == {"Entity", "Flow", "Anomaly"}
for role, rule_ids in r.ROLE_RULE_MAP.items():
    assert all(rid in r.RULES for rid in rule_ids), (role, rule_ids)

c_msgs = r.build_a_or_c_messages(card, r.ALL_RULE_IDS)
c_sys = c_msgs[0]["content"]
for rid in r.ALL_RULE_IDS:
    assert r.RULES[rid].split(":")[0] in c_sys, rid

b_msgs = r.build_role_messages("Entity", card, [])
d_msgs = r.build_role_messages("Entity", card, r.ROLE_RULE_MAP["Entity"])
assert "Email Anonymity & Absence Rule" not in b_msgs[0]["content"]
assert "Email Anonymity & Absence Rule" in d_msgs[0]["content"]

d_flow = r.build_role_messages("Flow", card, r.ROLE_RULE_MAP["Flow"])
d_anom = r.build_role_messages("Anomaly", card, r.ROLE_RULE_MAP["Anomaly"])
assert "High-Risk Category & Cross-Border Rule" in d_flow[0]["content"]
assert "Velocity & Frequency Burst Rule" in d_anom[0]["content"]
assert "Geographical & Identity Inconsistency Rule" in d_anom[0]["content"]

print("rules swap OK")
print("role->rule map:", r.ROLE_RULE_MAP)
