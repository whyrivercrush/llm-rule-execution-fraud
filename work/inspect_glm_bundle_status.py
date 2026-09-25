import json
import re
from pathlib import Path

T = Path("glm_run_2026-09-12")

print("--- credential check ---")
targets = [
    "config.json",
    "self_consistency_control.py",
    "wait_and_resume.py",
    "src/run_experiment.py",
]
for rel in targets:
    p = T / rel
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    print(rel, "sk_token:", bool(re.search(r"sk-[A-Za-z0-9]{16,}", text)),
          "api_key_field:", '"api_key"' in text)

print("--- GLM log record counts ---")
for pattern in ("logs/glm_air_run_condition_*.jsonl", "logs/glm_run_condition_*.jsonl",
                "logs/variance_r*_condition_*.jsonl", "logs_smoke/smoke_condition_*.jsonl"):
    files = sorted(T.glob(pattern))
    if not files:
        print(pattern, "none")
        continue
    counts = []
    for f in files:
        counts.append(sum(1 for _ in f.open(encoding="utf-8")))
    print(pattern, len(files), "files; records:", counts)

print("--- run summary detail ---")
rs = json.loads((T / "logs" / "run_summary.json").read_text(encoding="utf-8"))
print(json.dumps(rs, ensure_ascii=False, indent=1)[:1500])

print("--- raw_data total size ---")
total = sum(p.stat().st_size for p in (T / "raw_data").iterdir() if p.is_file())
print(round(total / 1024 / 1024 / 1024, 3), "GB")
