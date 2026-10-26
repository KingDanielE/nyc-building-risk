"""Risk score + backtest with precision@k.

Backtest: score every lot as of 2024-01-01 using only earlier events, then check what happened
in 2024. Rankers compared:
  - rules_v1:   hand-set weights over 0..1 components (kept to show why it was replaced)
  - learned_v2: same kind of components, one logistic regression per outcome, fit on 2023
                (features as of 2023-01-01, outcomes during 2023). Coefficients are the
                weights, so every score still splits into named drivers.
  - learned_v3: v2 plus, per outcome, the components that won on a validation split
                (fit 2022, validate 2023): repeat-offender flags for serious hazards,
                landlord-portfolio and managing-agent records for housing distress. The product.
  - gradient boosting on the raw features (fit on 2023), as the accuracy ceiling
  - naive baselines and random

Writes: reports/backtest.md, reports/backtest_metrics.csv, reports/validation_metrics.csv and
risk_assessment rows (learned_v3) for the backtest date and today.

Usage: .venv/Scripts/python scripts/score.py
"""

import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "building_risk.duckdb"
REPORTS = ROOT / "reports"

MODEL_VERSION = "learned_v3"
TRAIN_AS_OF, TEST_AS_OF = date(2023, 1, 1), date(2024, 1, 1)
# Component choice is made on this split only (train 2022 -> validate 2023), never on 2024
VAL_TRAIN_AS_OF, VAL_AS_OF = date(2022, 1, 1), date(2023, 1, 1)
KS = [100, 250, 500, 1000]

# Outcomes in the 12 months after as_of
LABELS = {
    "serious_hazard": "DOB hazard violation or UNSAFE facade filing",
    "housing_distress": "10+ new HPD class C violations",
}

# rules_v1: hand-set weights, chosen before looking at any outcomes
V1_WEIGHTS = {
    "open_c": 0.20, "dob_hazard": 0.20, "facade": 0.15, "c_rate": 0.15, "dob_open": 0.10,
    "equipment": 0.05, "trend": 0.05, "complaints": 0.05, "age": 0.05,
}
# learned_v2 inputs, the same for both outcomes
V2_COMPONENTS = [
    "open_c", "c_count", "c_rate", "dob_hazard", "facade_unsafe", "facade_swarmp",
    "dob_open", "equipment", "trend", "age",
]
REPEAT = ["hazard_any", "unsafe_and_hazard"]
LANDLORD = ["portfolio_c_rate", "portfolio_hazards", "portfolio_unsafe", "agent_c_rate"]
# Candidate additions compared on the validation split (see validate())
CANDIDATES = {
    "v2": [], "+complaints": ["complaints"], "+repeat offender": REPEAT,
    "+landlord & agent": LANDLORD, "+all": ["complaints"] + REPEAT + LANDLORD,
}
# learned_v3: per outcome, the validation winner
V3_COMPONENTS = {
    "serious_hazard": V2_COMPONENTS + REPEAT,
    "housing_distress": V2_COMPONENTS + LANDLORD,
}
ML_FEATURES = [
    "building_age", "num_floors", "units_res", "units_total", "num_buildings", "is_condo",
    "is_landmark", "in_hist_district", "hpd_c_12m", "hpd_b_12m", "hpd_a_12m", "hpd_open_c",
    "hpd_rent_impairing_12m", "hpd_bc_last_6m", "hpd_bc_prior_6m", "dob_hazard_36m",
    "dob_equipment_12m", "dob_admin_12m", "dob_open", "complaints_12m", "facade_severity",
    "facade_filing_age_years", "owner_lots", "owner_c_per_100_units", "portfolio_lots",
    "portfolio_c_per_100_units", "portfolio_other_hazards_36m",
    "portfolio_other_unsafe_facades", "agent_lots", "agent_c_per_100_units",
]


def load(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """Features as of as_of + outcome labels over the following 12 months, one row per lot."""
    return con.execute("""
    with y as (
      select bbl,
             count(*) filter (where (kind = 'dob_violation' and detail = 'hazard')
                                 or (kind = 'facade_filing' and detail = 'UNSAFE')) > 0
               as serious_hazard,
             count(*) filter (where kind = 'hpd_violation' and detail = 'C') >= 10
               as housing_distress
      from event
      where event_date >= $1 and event_date < $1 + interval 12 month and bbl is not null
      group by bbl)
    select f.*, l.address, coalesce(y.serious_hazard, false) as serious_hazard,
           coalesce(y.housing_distress, false) as housing_distress
    from lot_features_at($1) f join lot l using (bbl) left join y using (bbl)
    where f.num_buildings > 0
    """, [as_of]).df()


def saturate(x: pd.Series) -> pd.Series:
    """log-scale 0..1, where the 99th percentile of the non-zero values maps to 1."""
    x = x.clip(lower=0).fillna(0)
    cap = np.log1p(x[x > 0].quantile(0.99)) if (x > 0).any() else 1.0
    return (np.log1p(x) / cap).clip(0, 1)


def components(df: pd.DataFrame) -> pd.DataFrame:
    """Score components, each on 0..1, scaled against that date's own lots."""
    return pd.DataFrame({
        "open_c": saturate(df.hpd_open_c),
        "c_count": saturate(df.hpd_c_12m),
        # class C per unit, shrunk toward 0 for small buildings (+10 units)
        "c_rate": saturate(100 * df.hpd_c_12m / (df.units_res.fillna(0) + 10)),
        "dob_hazard": saturate(df.dob_hazard_36m),
        "facade": df.facade_severity / 3,
        "facade_unsafe": (df.facade_severity == 3).astype(float),
        "facade_swarmp": (df.facade_severity == 2).astype(float),  # SWARMP or not filed
        "dob_open": saturate(df.dob_open),
        "equipment": saturate(df.dob_equipment_12m),
        "trend": saturate(df.hpd_bc_last_6m - df.hpd_bc_prior_6m),
        "complaints": saturate(df.complaints_12m),
        "age": ((df.building_age - 20) / 100).clip(0, 1).fillna(0),
        # Repeat offender: any DOB hazard in 3 years, and the same with an UNSAFE facade
        "hazard_any": (df.dob_hazard_36m > 0).astype(float),
        "unsafe_and_hazard": ((df.facade_severity == 3) & (df.dob_hazard_36m > 0)).astype(float),
        # Landlord (HPD registration portfolio) and managing agent track records
        "portfolio_c_rate": saturate(df.portfolio_c_per_100_units.astype(float)),
        "portfolio_hazards": saturate(df.portfolio_other_hazards_36m.astype(float)),
        "portfolio_unsafe": saturate(df.portfolio_other_unsafe_facades.astype(float)),
        "agent_c_rate": saturate(df.agent_c_per_100_units.astype(float)),
    }, index=df.index).astype(float)  # nullable ints from DuckDB would make these object


DRIVER_TEXT = {
    "open_c": lambda r: f"{r.hpd_open_c} open class C violations",
    "c_count": lambda r: f"{r.hpd_c_12m} new class C in 12m",
    "c_rate": lambda r: f"{r.hpd_c_12m} new class C in 12m ({r.units_res or 0} units)",
    "dob_hazard": lambda r: f"{r.dob_hazard_36m} DOB hazard violations (3y)",
    "facade": lambda r: f"facade {r.facade_status}",
    "facade_unsafe": lambda r: "facade UNSAFE",
    "facade_swarmp": lambda r: f"facade {r.facade_status}",
    "dob_open": lambda r: f"{r.dob_open} open DOB violations",
    "equipment": lambda r: f"{r.dob_equipment_12m} boiler/elevator violations (12m)",
    "trend": lambda r: f"HPD B+C rising ({r.hpd_bc_prior_6m} -> {r.hpd_bc_last_6m} per 6m)",
    "complaints": lambda r: f"{r.complaints_12m} DOB complaints (12m)",
    "age": lambda r: f"built {r.building_age} years ago",
    "hazard_any": lambda r: f"repeat offender: {r.dob_hazard_36m} DOB hazard violations (3y)",
    "unsafe_and_hazard": lambda r: "UNSAFE facade with past hazard violations",
    "portfolio_c_rate": lambda r: (f"landlord's {r.portfolio_lots} lots: "
                                   f"{r.portfolio_c_per_100_units} class C per 100 units"),
    "portfolio_hazards": lambda r: (f"landlord has {r.portfolio_other_hazards_36m} hazard "
                                    "violations on other buildings (3y)"),
    "portfolio_unsafe": lambda r: (f"landlord has {r.portfolio_other_unsafe_facades} other "
                                   "UNSAFE facades"),
    "agent_c_rate": lambda r: f"managing agent: {r.agent_c_per_100_units} class C per 100 units",
}


def top_drivers(df: pd.DataFrame, contrib: pd.DataFrame, n: int = 3) -> pd.Series:
    """Plain-text names of the n largest positive contributions per lot."""
    names = np.array(contrib.columns)
    vals = contrib.to_numpy()
    top = np.argsort(-vals, axis=1)[:, :n]
    return pd.Series([[DRIVER_TEXT[names[j]](r) for j in idx if v[j] > 0.005]
                      for r, v, idx in zip(df.itertuples(index=False), vals, top)],
                     index=df.index, dtype=object)


def v1_score(comp: pd.DataFrame) -> pd.Series:
    return 100 * (comp[list(V1_WEIGHTS)] * pd.Series(V1_WEIGHTS)).sum(axis=1)


class LearnedScore:
    """One logistic regression per outcome, each over its own list of 0..1 components."""

    def __init__(self, cols: dict[str, list[str]]):
        self.cols = cols

    def fit(self, train: pd.DataFrame) -> "LearnedScore":
        X = components(train)
        self.models = {lab: LogisticRegression(C=1.0, max_iter=3000)
                       .fit(X[self.cols[lab]], train[lab]) for lab in LABELS}
        return self

    def coefs(self) -> pd.DataFrame:
        """Component x outcome; blank where an outcome's model doesn't use the component."""
        return pd.DataFrame({lab: pd.Series(m.coef_[0], index=self.cols[lab])
                             for lab, m in self.models.items()})

    def score(self, df: pd.DataFrame, drivers: bool = True) -> pd.DataFrame:
        """Per-outcome probability, overall score (0..100 percentile of the worse outcome),
        the outcome driving it, and (if drivers) its top drivers."""
        X = components(df)
        out = pd.DataFrame(index=df.index)
        pct = {}
        for lab, m in self.models.items():
            out[f"p_{lab}"] = m.predict_proba(X[self.cols[lab]])[:, 1]
            pct[lab] = out[f"p_{lab}"].rank(pct=True)
        pct = pd.DataFrame(pct)
        out["score"] = 100 * pct.max(axis=1)
        out["primary_risk"] = pct.idxmax(axis=1)
        # Many lots tie near 100; order them by the other outcome's percentile
        out["rank"] = (out.score + pct.min(axis=1) / 1000).rank(
            ascending=False, method="first").astype(int)
        if drivers:  # text only for each lot's main outcome (row-by-row, so it's the slow part)
            out["drivers"] = pd.Series(dtype=object)
            for lab, m in self.models.items():
                rows = out.index[out.primary_risk == lab]
                out.loc[rows, "drivers"] = top_drivers(
                    df.loc[rows], X.loc[rows, self.cols[lab]] * m.coef_[0])
        return out


def at_k(y: pd.Series, score: pd.Series, k: int) -> tuple[float, float]:
    """(precision@k, recall@k); ties broken by a fixed random order, not by row order."""
    order = np.lexsort((np.random.default_rng(0).random(len(score)), -score.to_numpy()))[:k]
    hits = y.to_numpy()[order].sum()
    return hits / k, hits / max(y.sum(), 1)


def validate(train: pd.DataFrame, val: pd.DataFrame) -> pd.DataFrame:
    """Average precision and P@100 of each CANDIDATES set on the validation year."""
    rows = []
    for name, extra in CANDIDATES.items():
        s = LearnedScore({lab: V2_COMPONENTS + extra for lab in LABELS}).fit(train).score(
            val, drivers=False)
        for lab in LABELS:
            rows.append({"set": name, "label": lab,
                         "p@100": at_k(val[lab], s[f"p_{lab}"], 100)[0],
                         "avg_precision": average_precision_score(val[lab], s[f"p_{lab}"])})
    return pd.DataFrame(rows)


def backtest(train: pd.DataFrame, test: pd.DataFrame,
             learned: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    comp = components(test)
    for label in LABELS:
        y = test[label]
        gbm = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0)
        gbm.fit(train[ML_FEATURES].astype(float), train[label])
        rankers = {
            **{name: s[f"p_{label}"] for name, s in learned.items()},
            "gradient boosting (ceiling)": pd.Series(
                gbm.predict_proba(test[ML_FEATURES].astype(float))[:, 1], index=test.index),
            "rules_v1 (hand weights)": v1_score(comp),
            "baseline: most violations last 12m": (
                test.hpd_c_12m + test.hpd_b_12m + test.hpd_a_12m + test.dob_equipment_12m
                + test.dob_admin_12m + test.complaints_12m).astype(float),
            "baseline: same signal last year": (
                test.dob_hazard_36m + 10 * test.facade_severity if label == "serious_hazard"
                else test.hpd_c_12m).astype(float),
            "baseline: oldest building": test.building_age.fillna(0).astype(float),
            "random": pd.Series(np.random.default_rng(1).random(len(test)), index=test.index),
        }
        for name, s in rankers.items():
            row = {"label": label, "ranker": name,
                   "avg_precision": average_precision_score(y, s)}
            for k in KS:
                row[f"p@{k}"], row[f"recall@{k}"] = at_k(y, s, k)
            rows.append(row)
    m = pd.DataFrame(rows)
    m["base_rate"] = m.label.map({lab: test[lab].mean() for lab in LABELS})
    return m


def save_assessments(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, s: pd.DataFrame,
                     as_of: date) -> None:
    for col, typ in [("p_serious_hazard", "double"), ("p_housing_distress", "double"),
                     ("primary_risk", "varchar")]:
        con.execute(f"alter table risk_assessment add column if not exists {col} {typ}")
    out = pd.DataFrame({
        "bbl": df.bbl, "as_of": as_of, "model_version": MODEL_VERSION,
        "score": s.score.round(2),
        "rank": s["rank"],
        "drivers": s.drivers, "p_serious_hazard": s.p_serious_hazard.round(4),
        "p_housing_distress": s.p_housing_distress.round(4), "primary_risk": s.primary_risk,
    })
    con.execute("delete from risk_assessment where as_of = ? and model_version = ?",
                [as_of, MODEL_VERSION])
    con.register("out_df", out)
    con.execute("insert into risk_assessment by name select * from out_df")
    con.unregister("out_df")


def report(m: pd.DataFrame, v: pd.DataFrame, test: pd.DataFrame, coefs: pd.DataFrame,
           today: pd.DataFrame, today_s: pd.DataFrame) -> str:
    lines = [
        "# Backtest: building risk score", "",
        f"Scored {len(test):,} Manhattan lots as of {TEST_AS_OF} using only earlier events; "
        f"outcomes are the 12 months after. Learned models were fit on features as of "
        f"{TRAIN_AS_OF} and 2023 outcomes, so 2024 is never seen in training. Which "
        f"components each model uses was decided on a separate validation split (fit "
        f"{VAL_TRAIN_AS_OF.year}, validate {VAL_AS_OF.year}), before the 2024 test was run.", "",
        "## Component choice (validation year, not the test)", "",
        "| Added to v2 | " + " | ".join(f"{lab} AP / P@100" for lab in LABELS) + " |",
        "|---|" + "---|" * len(LABELS)]
    for name, g in v.groupby("set", sort=False):
        g = g.set_index("label")
        lines.append(f"| {name} | " + " | ".join(
            f"{g.avg_precision[lab]:.3f} / {g['p@100'][lab]:.0%}" for lab in LABELS) + " |")
    lines += ["", "learned_v3 = v2 + repeat offender for serious_hazard, v2 + landlord & agent "
              "for housing_distress.", ""]
    for label, desc in LABELS.items():
        t = m[m.label == label]
        base = t.base_rate.iloc[0]
        lines += [f"## {label}: {desc}", "",
                  f"Base rate {base:.2%} ({int(test[label].sum())} lots).", "",
                  "| Ranker | " + " | ".join(f"P@{k}" for k in KS)
                  + " | Recall@500 | Lift@100 | Avg precision |",
                  "|---|" + "---|" * (len(KS) + 3)]
        for _, r in t.iterrows():
            lines.append(f"| {r.ranker} | " + " | ".join(f"{r[f'p@{k}']:.0%}" for k in KS)
                         + f" | {r['recall@500']:.0%} | {r['p@100'] / base:.1f}× "
                         f"| {r.avg_precision:.3f} |")
        lines.append("")
    lines += [f"## {MODEL_VERSION} weights (logistic coefficients on 0..1 components)", "",
              "| Component | " + " | ".join(LABELS) + " |", "|---|" + "---|" * len(LABELS)]
    for comp, r in coefs.iterrows():
        lines.append(f"| `{comp}` | " + " | ".join(
            "" if pd.isna(r[lab]) else f"{r[lab]:+.2f}" for lab in LABELS) + " |")
    top = today_s.nsmallest(10, "rank")
    lines += ["", f"## Today's top 10 ({date.today()})", "",
              "| Rank | BBL | Address | Score | Main risk | Drivers |", "|---|---|---|---|---|---|"]
    for i, (idx, r) in enumerate(top.iterrows(), 1):
        lines.append(f"| {i} | {today.bbl[idx]} | {today.address[idx]} | {r.score:.1f} | "
                     f"{r.primary_risk} | " + "; ".join(r.drivers) + " |")
    lines += ["", "## Caveats", "",
              "- rules_v1's hand weights were set before seeing outcomes and lost to simple "
              "baselines; learned_v2 keeps the explainable form but fits the weights on 2023.",
              "- Static attributes (owner, floors, units) and landlord portfolios / agents are "
              "today's PLUTO and HPD registrations, also in the backtest.",
              "- Outcomes are recorded violations, so they reflect where agencies inspect: the "
              "score predicts 'will get a serious violation', not 'is unsafe'.",
              "- Validation features as of 2022-01-01 see only 24 of the 36 months for "
              "3-year windows (the data starts in 2020).",
              "- HPD close dates are approximated by the current-status date."]
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    REPORTS.mkdir(exist_ok=True)
    with duckdb.connect(str(DB)) as con:
        val_train, val, train, test, today = (
            load(con, d) for d in (VAL_TRAIN_AS_OF, VAL_AS_OF, TRAIN_AS_OF, TEST_AS_OF,
                                   date.today()))
        v = validate(val_train, val)
        model = LearnedScore(V3_COMPONENTS).fit(train)
        v2 = LearnedScore({lab: V2_COMPONENTS for lab in LABELS}).fit(train)
        test_s, today_s = model.score(test), model.score(today)
        m = backtest(train, test, {f"{MODEL_VERSION} (this score)": test_s,
                                   "learned_v2 (previous)": v2.score(test, drivers=False)})
        save_assessments(con, test, test_s, TEST_AS_OF)
        save_assessments(con, today, today_s, date.today())

    m.to_csv(REPORTS / "backtest_metrics.csv", index=False)
    v.to_csv(REPORTS / "validation_metrics.csv", index=False)
    md = report(m, v, test, model.coefs(), today, today_s)
    (REPORTS / "backtest.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
