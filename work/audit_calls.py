import csv
import glob
import json
from pathlib import Path


def audit(path: Path, calls_per_record: int):
    records = 0
    success = 0
    calls = 0
    retries = 0
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        records += 1
        sample_calls = int(r.get("api_calls") or 0)
        calls += sample_calls
        retries += max(0, sample_calls - calls_per_record)
        success += 1 if r.get("status") == "success" else 0
    return records, success, calls, retries


rows = []
for condition in ("A", "B", "C", "D"):
    path = Path(f"logs/formal_run_condition_{condition}.jsonl")
    if path.exists():
        records, success, calls, retries = audit(path, 4 if condition in ("B", "D") else 1)
        rows.append({
            "track": "formal_main",
            "condition": condition,
            "records": records,
            "success": success,
            "api_calls": calls,
            "retries": retries,
            "note": "B old invalid (rules injected by bug)" if condition == "B" else "",
        })

path = Path("logs/pilot_run_correctedB_condition_B.jsonl")
records, success, calls, retries = audit(path, 4)
rows.append({
    "track": "corrected_B",
    "condition": "B",
    "records": records,
    "success": success,
    "api_calls": calls,
    "retries": retries,
    "note": "roles only, empty rule lists",
})

for condition in ("A", "B", "C", "D"):
    files = sorted(glob.glob(f"logs/variance_r*_condition_{condition}.jsonl"))
    records = success = calls = retries = 0
    for f in files:
        r, s, c, rt = audit(Path(f), 4 if condition in ("B", "D") else 1)
        records += r
        success += s
        calls += c
        retries += rt
    rows.append({
        "track": "variance_50x5",
        "condition": condition,
        "records": records,
        "success": success,
        "api_calls": calls,
        "retries": retries,
        "note": "B invalid (old bug); D/A/C valid" if condition == "B" else "",
    })

out = Path("results/formal_call_audit.csv")
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
print("saved", out)
for row in rows:
    print(row)
