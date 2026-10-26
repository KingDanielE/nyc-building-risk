"""Data access and actions for the app.

Reads come from data/building_risk.duckdb, attached read-only as `br`. Writeback (the
InspectionTask actions) goes to data/actions.duckdb, so rebuilding the risk database never
wipes recorded work. Stop the app before re-running the build scripts: DuckDB lets only one
process write a file, and the app holds a read lock on it.
"""

import uuid
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
MAIN_DB = ROOT / "data" / "building_risk.duckdb"
ACTIONS_DB = ROOT / "data" / "actions.duckdb"
METRICS_CSV = ROOT / "reports" / "backtest_metrics.csv"
MODEL_VERSION = "learned_v3"

OPEN_STATUSES = ("open", "assigned")
OUTCOMES = ["hazard_found", "minor_issues", "no_issue", "no_access"]
RISK_LABELS = {"serious_hazard": "Serious hazard", "housing_distress": "Housing distress"}


@st.cache_resource
def connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(ACTIONS_DB))
    con.execute("""
    create table if not exists inspection_task (
      task_id varchar primary key, bbl varchar, bin varchar, as_of date, model_version varchar,
      reason varchar, priority integer, status varchar default 'open', assigned_to varchar,
      outcome varchar, notes varchar, created_at timestamp default current_timestamp,
      updated_at timestamp)
    """)
    con.execute(f"attach '{MAIN_DB.as_posix()}' as br (read_only)")
    return con


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run on a fresh cursor (one per call; Streamlit sessions run on separate threads)."""
    return connection().cursor().execute(sql, params or []).df()


def execute(sql: str, params: list | None = None) -> None:
    connection().cursor().execute(sql, params or [])


# ---- Reads ----------------------------------------------------------------------------------

@st.cache_data
def latest_as_of() -> date:
    return pd.Timestamp(query("select max(as_of) d from br.risk_assessment "
                              "where model_version = ?", [MODEL_VERSION]).d[0]).date()


@st.cache_data
def scored_lots(as_of: date) -> pd.DataFrame:
    """Every scored lot with what the map and queue show."""
    df = query("""
    select r.bbl, r.rank, r.score, r.primary_risk, r.drivers,
           r.p_serious_hazard, r.p_housing_distress,
           l.address, l.lat, l.lon, l.units_res, l.year_built, l.num_floors,
           o.name as owner, o.num_lots as owner_lots
    from br.risk_assessment r join br.lot l using (bbl) left join br.owner o using (owner_id)
    where r.as_of = ? and r.model_version = ?
    order by r.rank
    """, [as_of, MODEL_VERSION])
    df["risk"] = df.primary_risk.map(RISK_LABELS)
    df["why"] = df.drivers.map(lambda d: "" if d is None else "; ".join(d))
    return df


@st.cache_data
def backtest_precision() -> dict[str, float]:
    """Top-100 precision of the live model in the 2024 backtest, per outcome."""
    if not METRICS_CSV.exists():
        return {}
    m = pd.read_csv(METRICS_CSV)
    m = m[m.ranker.str.startswith(MODEL_VERSION)]
    return dict(zip(m.label, m["p@100"]))


def lot(bbl: str) -> pd.Series:
    return query("""
    select l.*, o.name as owner, o.owner_type, o.num_lots as owner_lots,
           p.name as portfolio, p.people as portfolio_people, p.num_lots as portfolio_lots,
           p.units_res as portfolio_units, a.name as agent, a.num_lots as agent_lots
    from br.lot l left join br.owner o using (owner_id)
    left join br.landlord_portfolio p using (portfolio_id)
    left join br.managing_agent a using (agent_id)
    where l.bbl = ?
    """, [bbl]).iloc[0]


def buildings(bbl: str) -> pd.DataFrame:
    return query("""
    select bin, construction_year, height_roof_ft, footprint_sqft, status
    from br.building where bbl = ? order by bin
    """, [bbl])


def events(bbl: str) -> pd.DataFrame:
    return query("""
    select event_date, kind, detail, severity, is_open, closed_on, description, bin
    from br.event where bbl = ? order by event_date desc nulls last
    """, [bbl])


def linked_lots(link: str, link_id: str, as_of: date) -> pd.DataFrame:
    """Lots sharing this lot's owner_id / portfolio_id / agent_id, with their scores."""
    if link not in ("owner_id", "portfolio_id", "agent_id"):
        raise ValueError(link)
    return query(f"""
    select l.bbl, l.address, l.units_res, r.score, r.rank, r.primary_risk
    from br.lot l left join br.risk_assessment r
      on r.bbl = l.bbl and r.as_of = ? and r.model_version = ?
    where l.{link} = ? order by r.rank nulls last
    """, [as_of, MODEL_VERSION, link_id])


def tasks(bbl: str | None = None) -> pd.DataFrame:
    where, params = ("where t.bbl = ?", [bbl]) if bbl else ("", [])
    return query(f"""
    select t.*, l.address from inspection_task t left join br.lot l using (bbl)
    {where} order by t.created_at desc
    """, params)


# ---- Actions (ontology.yaml: create_inspection_task, assign_inspector, record_outcome,
# flag_false_positive) -----------------------------------------------------------------------

def priority_for(rank: int) -> int:
    return 1 if rank <= 100 else 2 if rank <= 500 else 3


def create_task(bbl: str, reason: str, priority: int, as_of: date) -> str:
    """Create an open task; BIN is filled when the lot has exactly one building."""
    task_id = "T-" + uuid.uuid4().hex[:8].upper()
    bins = buildings(bbl).bin
    execute("""
    insert into inspection_task (task_id, bbl, bin, as_of, model_version, reason, priority)
    values (?, ?, ?, ?, ?, ?, ?)
    """, [task_id, bbl, bins[0] if len(bins) == 1 else None, as_of, MODEL_VERSION, reason,
          priority])
    return task_id


def assign_inspector(task_id: str, assigned_to: str) -> None:
    execute("""update inspection_task set assigned_to = ?, status = 'assigned',
               updated_at = current_timestamp where task_id = ?""", [assigned_to, task_id])


def record_outcome(task_id: str, outcome: str, notes: str) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    execute("""update inspection_task set outcome = ?, notes = ?, status = 'closed',
               updated_at = current_timestamp where task_id = ?""", [outcome, notes, task_id])


def flag_false_positive(task_id: str, notes: str) -> None:
    execute("""update inspection_task set outcome = 'no_issue', notes = ?,
               status = 'false_positive', updated_at = current_timestamp
               where task_id = ?""", [notes, task_id])
