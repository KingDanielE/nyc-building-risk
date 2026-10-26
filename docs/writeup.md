# Building-risk ontology: write-up

## Problem

An inspection planner at a NYC agency has far more buildings than inspectors. The evidence
about which buildings are deteriorating already exists in public data, but it's split across
agencies with different keys. HPD housing violations use block/lot. DOB violations,
complaints and facade filings use a building number (BIN). Ownership is in a tax-lot file
(PLUTO) with no BIN, and it usually names a single-building LLC rather than the landlord
behind it. **The decision this supports: which buildings to inspect this week, and why.**

## Data

Eight NYC Open Data sources, Manhattan, events from 2020 onward:

- PLUTO (42.5k lots) and Building Footprints (45k buildings, the BIN↔BBL bridge)
- HPD violations (955k), DOB violations (101k), DOB complaints (175k) and facade (FISP)
  filings (51k): 1.28M events in total
- HPD registrations (30k Manhattan rental buildings) and their contacts (592k citywide),
  which name the owners, head officers and managing agents

Everything is loaded into DuckDB.

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

The object types are **LandlordPortfolio, ManagingAgent, Owner → Lot → Building → Event**,
plus **RiskAssessment** and **InspectionTask**. Events are one table holding every violation,
complaint and filing, each with a severity from 0 to 3. Three modelling choices mattered:

- **The landlord isn't the LLC.** Most Manhattan rentals are owned by a single-building LLC.
  Two registered lots are linked when their HPD registrations share a head officer, an
  individual or joint owner, or a corporate-owner name; portfolios are the connected
  components of that graph. That gives 27.8k lots in 13k portfolios, and the largest has 234
  lots. Managing agents are a separate object and aren't used for linking, because one
  management company runs buildings for many unrelated owners.
- **Paperwork isn't risk.** About a fifth of DOB violations are administrative: failure to
  benchmark energy use, to post an energy grade, or to file a report. I classified every DOB
  code as hazard, equipment, construction or admin, so paperwork doesn't look like a
  dangerous building.
- **Features are point-in-time.** `lot_features_at(date)` counts only events before the date.
  The backtest and today's live score use the same code, so nothing from the future leaks
  into training. The features include the landlord's and the agent's class C rate across all
  their lots, and the landlord's hazard record on their *other* buildings.

The actions (create task, assign inspector, record outcome, flag false positive) write to a
separate store. Rebuilding the data never loses recorded work.

## Model and evaluation

There are two outcomes, each measured over the 12 months after the score date:

- **Serious hazard:** a DOB hazard violation or an UNSAFE facade filing (1.6% of lots).
- **Housing distress:** 10 or more new class C (immediately hazardous) HPD violations (3.6%).

The evaluation has three stages, so the test year is never used for decisions:

1. Pick which components each outcome uses on a **validation** split: fit on 2022, score on
   2023.
2. Fit the chosen models on 2023.
3. Score every lot as of 2024-01-01 and **test once** against what happened in 2024.

| 2024 test: P@100 · average precision | Housing distress | Serious hazard |
|---|---|---|
| **v3** (logistic per outcome, explainable) | **91% · 0.572** | **32% · 0.146** |
| v2 (same, without landlord / repeat-offender inputs) | 92% · 0.563 | 34% · 0.143 |
| Gradient boosting (accuracy ceiling, not explainable) | 91% · 0.570 | 41% · 0.208 |
| Naive: same signal last year | 92% · 0.510 | 50% · 0.138 |
| My first version (hand-set weights) | 59% · 0.367 | 21% · 0.063 |
| Random | 0% · 0.034 | 1% · 0.016 |

**What I learned.**

- My first score used weights I set by hand before looking at any outcomes, and it lost to
  naive baselines. Blending two different risks into one number diluted both. Fitting one
  model per outcome on 0–1 components fixed it, and the model stays explainable: the
  coefficients are the weights, and each lot's top three contributions become its drivers.
- **Landlords matter.** The landlord's and managing agent's class C rates were picked on
  validation and held up on 2024. With them, the explainable housing model slightly beats
  gradient boosting on average precision (0.572 vs 0.570).
- **Validation gains don't always replicate.** For hazards, repeat-offender components lifted
  top-100 precision from 23% to 35% on the validation year, but on 2024 the score went from
  34% to 32%, while average precision ticked up. With about 660 positives, top-100 precision
  has roughly ±5 points of noise. I reported this instead of tuning against the test year.
- A naive repeat-offender rule ("had an unsafe facade or hazard violation before") still leads
  in the top 100 for hazards. A real deployment should put known repeat offenders first
  explicitly, and use the model to rank everything after them.

## Product

A Streamlit app with three pages:

- **Map:** lots coloured by score; click one to open it.
- **Building 360:** score, drivers, a timeline of every linked event, lot facts, and "who
  controls it": the landlord portfolio with the scores of their other buildings, and the
  managing agent.
- **Inspector queue:** ranked, hides lots that already have an open task, multi-select to
  create tasks, then assign an inspector and record the outcome. The hit rate of closed tasks
  is shown. Recorded outcomes are the labels a next model version would learn from.

## Limits

- Outcomes are *recorded* violations, so they partly reflect where agencies choose to
  inspect.
- Landlord linking uses exact names, so spelling variants across registrations stay split.
  Agent names aren't normalized. Only HPD-registered rentals have a portfolio.
- Building attributes and landlord links are today's values, even in the backtest.
- Validation features as of 2022 see only 24 of 36 months for 3-year windows (the data starts
  in 2020).

## With more time

- Fuzzy-match names and business addresses to merge landlord spelling variants.
- Add an explicit "repeat offenders first" rule to the hazard queue.
- Extend to all five boroughs.
- Rebuild the ontology and actions in Palantir Foundry (Ontology Manager, Workshop, Actions).
