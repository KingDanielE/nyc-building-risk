"""Risk score + backtest with precision@k.

Backtest: score every lot as of 2024-01-01 using only earlier events, then check what happened
in 2024. Rankers compared:
  - rules_v1:   hand-set weights over 0..1 components (kept to show why it was replaced)
  - learned_v2: same kind of components, one logistic regression per outcome, fit on 2023
                (features as of 2023-01-01, outcomes during 2023). Coefficients are the
                weights, so every score still splits into named drivers. This is the product.
  - gradient boosting on the raw features (fit on 2023), as the accuracy ceiling
  - naive baselines and random

DOB complaints are left out of learned_v2 and the boosting model: that feed stops in April
2024, so a model can't rely on it when scoring today.

Writes: reports/backtest.md, reports/backtest_metrics.csv, and risk_assessment rows
(learned_v2) for the backtest date and today.

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

MODEL_VERSION = "learned_v2"
TRAIN_AS_OF, TEST_AS_OF = date(2023, 1, 1), date(2024, 1, 1)
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
# learned_v2 inputs (no complaints, see module docstring)
V2_COMPONENTS = [
    "open_c", "c_count", "c_rate", "dob_hazard", "facade_unsafe", "facade_swarmp",
    "dob_open", "equipment", "trend", "age",
]
ML_FEATURES = [
    "building_age", "num_floors", "units_res", "units_total", "num_buildings", "is_condo",
    "is_landmark", "in_hist_district", "hpd_c_12m", "hpd_b_12m", "hpd_a_12m", "hpd_open_c",
    "hpd_rent_impairing_12m", "hpd_bc_last_6m", "hpd_bc_prior_6m", "dob_hazard_36m",
    "dob_equipment_12m", "dob_admin_12m", "dob_open", "facade_severity",
    "facade_filing_age_years", "owner_lots", "owner_c_per_100_units",
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
}


def top_drivers(df: pd.DataFrame, contrib: pd.DataFrame, n: int = 3) -> pd.Series:
    """Plain-text names of the n largest positive contributions per lot."""
    return pd.Series([[DRIVER_TEXT[c](df.loc[i]) for c, v in row.nlargest(n).items() if v > 0.005]
                      for i, row in contrib.iterrows()], index=df.index)


def v1_score(comp: pd.DataFrame) -> pd.Series:
    return 100 * (comp[list(V1_WEIGHTS)] * pd.Series(V1_WEIGHTS)).sum(axis=1)


class LearnedScore:
    """learned_v2: one logistic regression per outcome over V2_COMPONENTS."""

    def fit(self, train: pd.DataFrame) -> "LearnedScore":
        X = components(train)[V2_COMPONENTS]
        self.models = {lab: LogisticRegression(C=1.0, max_iter=2000).fit(X, train[lab])
                       for lab in LABELS}
        return self

    def coefs(self) -> pd.DataFrame:
        return pd.DataFrame({lab: m.coef_[0] for lab, m in self.models.items()},
                            index=V2_COMPONENTS)

    def score(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-outcome probability, overall score (0..100 percentile of the worse outcome),
        the outcome driving it, and its top drivers."""
        X = components(df)[V2_COMPONENTS]
        out = pd.DataFrame(index=df.index)
        pct = {}
        for lab, m in self.models.items():
            out[f"p_{lab}"] = m.predict_proba(X)[:, 1]
            pct[lab] = out[f"p_{lab}"].rank(pct=True)
        pct = pd.DataFrame(pct)
        out["score"] = 100 * pct.max(axis=1)
        out["primary_risk"] = pct.idxmax(axis=1)
        # Many lots tie near 100; order them by the other outcome's percentile
        out["rank"] = (out.score + pct.min(axis=1) / 1000).rank(
            ascending=False, method="first").astype(int)
        drivers = {lab: top_drivers(df, X * m.coef_[0]) for lab, m in self.models.items()}
        out["drivers"] = [drivers[lab][i] for i, lab in out.primary_risk.items()]
        return out


def at_k(y: pd.Series, score: pd.Series, k: int) -> tuple[float, float]:
    """(precision@k, recall@k); ties broken by a fixed random order, not by row order."""
    order = np.lexsort((np.random.default_rng(0).random(len(score)), -score.to_numpy()))[:k]
    hits = y.to_numpy()[order].sum()
    return hits / k, hits / max(y.sum(), 1)


def backtest(train: pd.DataFrame, test: pd.DataFrame, learned: pd.DataFrame) -> pd.DataFrame:
    rows = []
    comp = components(test)
    for label in LABELS:
        y = test[label]
        gbm = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0)
        gbm.fit(train[ML_FEATURES].astype(float), train[label])
        rankers = {
            "learned_v2 (this score)": learned[f"p_{label}"],
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


def report(m: pd.DataFrame, test: pd.DataFrame, coefs: pd.DataFrame, today: pd.DataFrame,
           today_s: pd.DataFrame) -> str:
    lines = [
        "# Backtest: building risk score", "",
        f"Scored {len(test):,} Manhattan lots as of {TEST_AS_OF} using only earlier events; "
        f"outcomes are the 12 months after. Learned models were fit on features as of "
        f"{TRAIN_AS_OF} and 2023 outcomes, so 2024 is never seen in training.", "",
    ]
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
    lines += ["## learned_v2 weights (logistic coefficients on 0..1 components)", "",
              "| Component | " + " | ".join(LABELS) + " |", "|---|" + "---|" * len(LABELS)]
    for comp, r in coefs.iterrows():
        lines.append(f"| `{comp}` | " + " | ".join(f"{r[lab]:+.2f}" for lab in LABELS) + " |")
    top = today_s.nsmallest(10, "rank")
    lines += ["", f"## Today's top 10 ({date.today()})", "",
              "| Rank | BBL | Address | Score | Main risk | Drivers |", "|---|---|---|---|---|---|"]
    for i, (idx, r) in enumerate(top.iterrows(), 1):
        lines.append(f"| {i} | {today.bbl[idx]} | {today.address[idx]} | {r.score:.1f} | "
                     f"{r.primary_risk} | " + "; ".join(r.drivers) + " |")
    lines += ["", "## Caveats", "",
              "- rules_v1's hand weights were set before seeing outcomes and lost to simple "
              "baselines; learned_v2 keeps the explainable form but fits the weights on 2023.",
              "- Static attributes (owner, floors, units) are today's PLUTO, also in the backtest.",
              "- Outcomes are recorded violations, so they reflect where agencies inspect: the "
              "score predicts 'will get a serious violation', not 'is unsafe'.",
              "- DOB complaints end 2024-04-15, so learned_v2 and boosting don't use them.",
              "- HPD close dates are approximated by the current-status date."]
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    REPORTS.mkdir(exist_ok=True)
    with duckdb.connect(str(DB)) as con:
        train, test, today = (load(con, d) for d in (TRAIN_AS_OF, TEST_AS_OF, date.today()))
        model = LearnedScore().fit(train)
        test_s, today_s = model.score(test), model.score(today)
        m = backtest(train, test, test_s)
        save_assessments(con, test, test_s, TEST_AS_OF)
        save_assessments(con, today, today_s, date.today())

    m.to_csv(REPORTS / "backtest_metrics.csv", index=False)
    md = report(m, test, model.coefs(), today, today_s)
    (REPORTS / "backtest.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
