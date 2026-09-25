import csv
import json
from pathlib import Path

PILOT_CSV = Path(r"C:\Users\Niconiconi\sci\数据训练集\pilot_sample_100.csv")
TRAIN_CSV = Path(r"C:\Users\Niconiconi\sci\数据训练集\train_transaction.csv")
TEST_JSON = Path("data/test/test_cards_400.json")

pilot_card1 = set()
with PILOT_CSV.open(encoding="utf-8", errors="ignore", newline="") as f:
    for row in csv.DictReader(f):
        c = (row.get("card1") or "").strip()
        if c:
            pilot_card1.add(c)
print("pilot rows:", sum(1 for _ in PILOT_CSV.open(encoding="utf-8", errors="ignore")) - 1)
print("pilot distinct card1:", len(pilot_card1))

test = json.loads(TEST_JSON.read_text(encoding="utf-8"))
test_ids = {str(x["TransactionID"]) for x in test}
print("formal test rows:", len(test), "distinct ids:", len(test_ids))

test_card1 = {}
with TRAIN_CSV.open(encoding="utf-8", errors="ignore", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        tid = (row.get("TransactionID") or "").strip()
        if tid in test_ids:
            test_card1[tid] = (row.get("card1") or "").strip()
            if len(test_card1) == len(test_ids):
                break
print("resolved test card1:", len(test_card1), "/", len(test_ids))

overlap_rows = [tid for tid, c in test_card1.items() if c and c in pilot_card1]
test_cards = {c for c in test_card1.values() if c}
overlap_cards = test_cards & pilot_card1
print("test rows whose card1 in pilot card1:", len(overlap_rows),
      f"({len(overlap_rows)/len(test_ids)*100:.2f}%)")
print("test distinct card1:", len(test_cards),
      "overlapping distinct card1:", len(overlap_cards))
print("duplicate card1 groups inside test:",
      sum(1 for c in test_cards if list(test_card1.values()).count(c) > 1))
