import json
import os
import re
from pathlib import Path

T = Path("glm_run_2026-09-12")
EXT = Path(r"C:\Users\Niconiconi\sci\数据训练集")
TEXT = {".py", ".md", ".txt", ".json", ".csv", ".jsonl"}

print("--- raw data ---")
for p in sorted((T / "raw_data").iterdir()):
    src = EXT / p.name
    same = False
    try:
        same = os.stat(src).st_ino == os.stat(p).st_ino
    except Exception:
        pass
    print(p.name, round(p.stat().st_size / 1024 / 1024, 2), "MB",
          "hardlink" if same else "copy")

print("--- handoff trace scan ---")
hits = []
for p in (T / "glm_handoff_clean").rglob("*"):
    if p.is_file() and p.suffix.lower() in TEXT:
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"deepseek|api\.deepseek\.com", text, re.I):
            hits.append(str(p.relative_to(T)))
print("handoff deepseek hits:", len(hits))

print("--- credential-like files in whole target (names only) ---")
cred = []
for p in T.rglob("*"):
    if p.is_file() and p.suffix.lower() in TEXT:
        text = p.read_text(encoding="utf-8", errors="ignore")
        has_token = bool(re.search(r"sk-[A-Za-z0-9]{16,}", text))
        has_plain_key = (
            '"api_key"' in text
            and '"REDACTED"' not in text
            and '"REPLACE' not in text
        )
        if has_token or has_plain_key:
            cred.append(str(p.relative_to(T)))
print("files potentially containing credentials:", len(cred))
for c in cred[:20]:
    print("  ", c)

print("--- existing glm run config (no key value) ---")
cfg_path = T / "config.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
print({k: cfg.get(k) for k in ("base_url", "model", "temperature", "json_mode")})

print("--- existing run summary ---")
rs_path = T / "logs" / "run_summary.json"
rs = json.loads(rs_path.read_text(encoding="utf-8")) if rs_path.exists() else {}
print({k: rs.get(k) for k in ("log_prefix", "samples", "expected_calls_total", "actual_calls_total")})
