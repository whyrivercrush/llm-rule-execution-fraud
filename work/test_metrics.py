import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import eval_metrics as m

y = [1, 1, 0, 0]
p = [1, 0, 1, 0]
s = [0.9, 0.4, 0.7, 0.2]
tp, fp, tn, fn = m.binary_confusion(y, p)
print(tp, fp, tn, fn)
print("acc", m.safe_div(tp + tn, 4))
print("bacc", (m.safe_div(tp, 2) + m.safe_div(tn, 2)) / 2)
print("prec/rec", m.safe_div(tp, tp + fp), m.safe_div(tp, tp + fn))
print("macro_f1", m.macro_f1_score(tp, fp, tn, fn))
print("auc", m.auc_roc(y, s))
