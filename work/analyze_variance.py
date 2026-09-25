import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import eval_metrics as em

log_dir = Path("logs")
rows = []
for repeat in range(1, 6):
    for condition in ("A", "B", "C", "D"):
        records = em.load_condition_records(log_dir, f"variance_r{repeat}", condition)
        counts, metrics = em.compute_metrics(records)
        rows.append({
            "repeat": repeat,
            "condition": condition,
            "n_valid": counts["n_valid"],
            "accuracy": metrics["accuracy"],
            "macro_f1": metrics["macro_f1"],
            "auc_roc": metrics["auc_roc"],
        })

out = Path("results/variance_run_metrics.csv")
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

for condition in ("A", "B", "C", "D"):
    sub = [r for r in rows if r["condition"] == condition]
    accs = [r["accuracy"] for r in sub]
    f1s = [r["macro_f1"] for r in sub if r["macro_f1"] is not None]
    aucs = [r["auc_roc"] for r in sub]
    print(
        condition,
        "acc", f"{min(accs):.4f}-{max(accs):.4f}",
        "macroF1", f"{min(f1s):.4f}-{max(f1s):.4f}",
        "auc", f"{min(aucs):.4f}-{max(aucs):.4f}",
    )
print("saved", out)
