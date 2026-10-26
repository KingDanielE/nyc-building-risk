# Building-risk ontology: write-up

## Problem

An inspection planner at a NYC agency has far more buildings than inspectors. The evidence
about which buildings are deteriorating already exists in public data, but it's split across
agencies with different keys. HPD housing violations use block/lot. DOB violations,
complaints and facade filings use a building number (BIN). Ownership is in a tax-lot file
(PLUTO) with no BIN at all. **The decision this supports: which buildings to inspect this
week, and why.**

## Data

Six NYC Open Data sources, Manhattan, events from 2020 onward: PLUTO (42.5k lots), Building
Footprints (45k buildings, the BIN↔BBL bridge), HPD violations (955k), DOB violations (101k),
DOB complaints (111k) and facade (FISP) filings (51k). That's 1.22M events in total, loaded
into DuckDB.

## Entity resolution

Every record is matched to a building (BIN) and a tax lot (BBL), trying in order:

1. the record's BIN, if it's a real building in footprints;
2. its block/lot, if that's a PLUTO lot;
3. a condo unit lot mapped to its billing lot;
4. otherwise, a real-looking BIN is kept without a lot (demolished or new buildings).

Placeholder BINs (`1000000`) and junk dates (`Y9990120`) are dropped along the way.

| Source | Matched to a lot | Matched to a building |
|---|---|---|
| DOB violations | 99.5% | 100% |
| HPD violations | 99.7% | 89.2% (lot-only data; BIN known when the lot has one building) |
| DOB complaints | 95.2% | 100% |
| Facade filings | 99.6% | 99.9% |

## Ontology

The object types are **Owner → Lot → Building → Event**, plus **RiskAssessment** and
**InspectionTask**. Events are one table holding every violation, complaint and filing, each
with a severity from 0 to 3. Two modelling choices mattered:

- **Paperwork isn't risk.** About a fifth of DOB violations are administrative: failure to
  benchmark energy use, to post an energy grade, or to file a report. I classified every DOB
  code as hazard, equipment, construction or admin, so paperwork doesn't look like a
  dangerous building.
- **Features are point-in-time.** `lot_features_at(date)` counts only events before the date.
  The backtest and today's live score use the same code, so nothing from the future leaks
  into training.

The actions (create task, assign inspector, record outcome, flag false positive) write to a
separate store. Rebuilding the data never loses recorded work.

## Model and backtest

There are two outcomes, each measured over the 12 months after the score date:

- **Serious hazard:** a DOB hazard violation or an UNSAFE facade filing (1.6% of lots).
- **Housing distress:** 10 or more new class C (immediately hazardous) HPD violations (3.6%).

All lots were scored as of 2024-01-01; learned models were fit on 2023.

| Precision in the top 100 | Housing distress | Serious hazard |
|---|---|---|
| **Final score** (logistic per outcome, explainable) | **92%** | **34%** |
| Gradient boosting (accuracy ceiling, not explainable) | 91% | 42% |
| Naive: same signal last year | 92% | 46% |
| My first version (hand-set weights) | 59% | 21% |
| Random | 5% | 0% |

**What I learned.**

- My first score used weights I set by hand before looking at any outcomes, and it lost to
  naive baselines. Blending two different risks into one number diluted both. Fitting one
  model per outcome on 0–1 components fixed it, and the model stays explainable: the
  coefficients are the weights, and each lot's top three contributions become its drivers.
- For housing distress, the explainable model matches gradient boosting, so explainability
  costs nothing there.
- For hazards, a naive repeat-offender rule ("had an unsafe facade or hazard violation
  before") beats every model in the top 100, because the same buildings keep getting cited.
  The learned score is better across the full ranking (average precision 0.143 vs 0.138), but
  a real deployment should put known repeat offenders at the top explicitly.

## Product

A Streamlit app with three pages:

- **Map:** lots coloured by score; click one to open it.
- **Building 360:** score, drivers, a timeline of every linked event, lot facts and the
  owner's portfolio.
- **Inspector queue:** ranked, hides lots that already have an open task, multi-select to
  create tasks, then assign an inspector and record the outcome. The hit rate of closed tasks
  is shown. Recorded outcomes are the labels a next model version would learn from.

## Limits

- Outcomes are *recorded* violations, so they partly reflect where agencies choose to
  inspect.
- DOB complaints stop in April 2024 (the newer DOB NOW feed isn't loaded), so the model
  doesn't use them.
- Ownership is the owner-of-record name; most Manhattan buildings sit in their own LLC, so
  portfolios are undercounted.
- Building attributes are today's values, even in the backtest.

## With more time

- Add HPD registrations to link LLCs through their managing agent and head officer.
- Load DOB NOW complaints.
- Add an explicit repeat-offender component to the hazard score.
- Extend to all five boroughs.
- Rebuild the ontology and actions in Palantir Foundry (Ontology Manager, Workshop, Actions).
