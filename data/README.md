# Evaluation instances

| File | Contents |
|---|---|
| `test_cards_400.json` | standard evaluation set: 400 instances, 200 fraudulent / 200 legitimate |
| `test_cards_400_carddisjoint.json` | card-disjoint evaluation set: 400 instances, excluding every `card1` present in the pilot development set |
| `card_clusters.json` | `card1` for all 800 evaluation instances, plus the distinct `card1` values in the pilot set — needed only by the card-level analyses |

Both are derived from the public **IEEE-CIS Fraud Detection** benchmark
(Kaggle, 2019) by within-class uniform reservoir sampling with `seed=100`,
after excluding the 100 pilot development records at the level of
`TransactionID`.

Each record is the *serialized card text* as presented to the model, together
with its ground-truth label. The serialization projects raw numeric and
categorical attributes into neutral descriptive slots, without evaluative
adjectives, qualitative risk classifications or derived statistics.

## The raw dataset is not redistributed

`train_transaction.csv` and `train_identity.csv` are **not** included: they are
approximately 1.3 GB and are governed by their own terms on Kaggle.

To rebuild these evaluation sets from scratch:

1. Download the competition files from
   <https://www.kaggle.com/c/ieee-fraud-detection>
2. Place them under `data/raw/`
3. Run `python src/sample_formal_test.py`

Reproduction from the archived logs does **not** require the raw dataset — the
serialized instances and `card_clusters.json` are sufficient.

`card_clusters.json` exists because `card1` is deliberately absent from the
serialized card text (manuscript §4.6(a)), yet the cluster-robust intervals and
the non-overlapping subsample both need the entity grouping. It is committed so
that `recompute.py` can verify those results without the 1.3 GB source files.
Rebuild it with `python work/build_card_clusters.py --raw-dir <dir>`.
