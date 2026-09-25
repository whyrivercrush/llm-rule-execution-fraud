import hashlib
import json
from pathlib import Path


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
print("config.json input_path:", cfg.get("input_path"))
print("config.json model:", cfg.get("model"))
print("config.json temperature:", cfg.get("temperature"))
print("config.json json_mode:", cfg.get("json_mode"))
print("config.json keys:", sorted(cfg.keys()))

manifest = json.loads(Path("logs/run_manifest.json").read_text(encoding="utf-8"))
print("run_manifest.json:", json.dumps(manifest, ensure_ascii=False))

summary = json.loads(Path("logs/run_summary_corrected_B.json").read_text(encoding="utf-8"))
print(
    "corrected-B summary:",
    {
        "input_path": summary.get("input_path"),
        "samples": summary.get("samples"),
        "log_prefix": summary.get("log_prefix"),
        "expected_calls_total": summary.get("expected_calls_total"),
        "actual_calls_total": summary.get("actual_calls_total"),
        "resume": summary.get("resume"),
    },
)

test = json.loads(Path("data/test/test_cards_400.json").read_text(encoding="utf-8"))
test_ids = {str(x["TransactionID"]) for x in test}
log_a = Path("logs/pilot_run_correctedB_condition_B.jsonl")
log_b = Path("logs/pilot_run_condition_B_corrected.jsonl")
records = [json.loads(line) for line in log_a.open(encoding="utf-8") if line.strip()]
log_ids = {str(r["transaction_id"]) for r in records}
print("test set ids:", len(test_ids))
print("corrected B log records:", len(records))
print("id set equal:", test_ids == log_ids)
print("missing from log:", len(test_ids - log_ids), "extra in log:", len(log_ids - test_ids))
print("test sha256:", sha256("data/test/test_cards_400.json"))
print("manifest sha256:", manifest.get("input_sha256"))
print("log copy identical:", sha256(log_a) == sha256(log_b))
