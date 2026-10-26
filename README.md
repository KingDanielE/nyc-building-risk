# Building-risk ontology (NYC open data, Manhattan)

Links NYC's building datasets (DOB violations and complaints, HPD violations, facade
inspections, PLUTO, footprints) into one ontology keyed on the tax lot (BBL) and building
(BIN), then derives point-in-time risk features.

## Run

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements.txt
bash scripts/download.sh                      # ~470 MB into data/raw (Manhattan, 2020+)
.venv/Scripts/python scripts/build_db.py      # clean + resolve to BIN/BBL -> data/building_risk.duckdb
.venv/Scripts/python scripts/build_ontology.py  # owner, event, links, feature macros
.venv/Scripts/python scripts/score.py         # risk score + 2024 backtest -> reports/backtest.md
```

## App

```bash
.venv/Scripts/streamlit run app/app.py        # http://localhost:8501
```

- **Risk map**: lots coloured by score; click one to open it.
- **Building 360**: score, drivers, event timeline and table, lot facts, buildings, owner
  portfolio, tasks, "Create inspection task".
- **Inspector queue**: ranked list (hides lots with open tasks), multi-select → create
  tasks; assign inspector / record outcome / flag false positive; hit rate of closed tasks.

Tasks are stored in `data/actions.duckdb`, separate from the rebuildable risk database.
Stop the app before re-running the build scripts (it holds a lock on the database).

## Risk score

`learned_v2`: one logistic regression per outcome (serious hazard; housing distress) over
0..1 components (open class C, facade status, open DOB violations, ...), fit on 2023 and
tested on 2024. Coefficients are the weights, so each score lists its top drivers. Scores
land in `risk_assessment` (score = percentile of the worse outcome). Results:
[reports/backtest.md](reports/backtest.md).

## Ontology

Spec: [ontology/ontology.yaml](ontology/ontology.yaml).

`Owner` 1→N `Lot` 1→N `Building`; `Lot` and `Building` 1→N `Event` (HPD/DOB violation,
DOB complaint, facade filing); `Lot` 1→N `RiskAssessment` 1→N `InspectionTask` (writeback).

```sql
select * from lot_features_at(DATE '2024-01-01');   -- only events before that date
select * from building_features_at(current_date);
select * from match_report;  select * from link_check;
```

## Known limits

- DOB complaints (BIS) end 2024-04-15; later complaints live in DOB NOW (not loaded).
- Owners are PLUTO owner-of-record names; most Manhattan buildings sit in their own LLC, so
  portfolios are undercounted (HPD registrations would link them via managing agent).
- HPD violations only carry block/lot: BIN is filled only when the lot has one building.
- Static attributes (owner, floors) are today's PLUTO values, also in backtests.
