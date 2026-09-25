import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import run_experiment as r

cases = [
    'Here is the JSON:\n```json\n{"reasoning_summary":"x","is_fraud":1,"confidence":0.92}\n```\nDone',
    '{"reasoning_summary":"trailing text","is_fraud":"yes","confidence":"95"} after',
    'prefix {"reasoning_summary":"nested { brace ok","is_fraud":false,"confidence":"0.12"}',
]
for c in cases:
    obj = r.extract_json_object(c)
    print("EXTRACT", obj is not None)
    if obj is not None:
        print(r.parse_final_response(c))

print(
    r.normalize_label("Fraudulent"),
    r.normalize_label(True),
    r.normalize_label("0"),
    r.normalize_confidence("95"),
    r.normalize_confidence(0.5),
)

try:
    r.parse_role_response("The role analysis: no structured json")
except Exception as exc:
    print("role fallback expected:", type(exc).__name__)
