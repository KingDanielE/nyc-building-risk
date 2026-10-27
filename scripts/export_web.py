"""Export the scored ontology as static, gzipped JSON for the browser version of the app.

Writes site/app/data/ (git-ignored; regenerate before deploying):
  meta.json.gz        as-of date, model version, 2024 backtest precision
  lots.json.gz        every scored lot (map + queue columns), portfolios, managing agents
  lot/<block>.json.gz per tax block: lot facts, buildings and events for each lot in it

The browser fetches one block file when a building page opens, so the 1.28M events never
load at once. Descriptions are cut to DESC_CHARS to keep the files small.
"""

import gzip
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "building_risk.duckdb"
OUT = ROOT / "site" / "app" / "data"
METRICS_CSV = ROOT / "reports" / "backtest_metrics.csv"
MODEL_VERSION = "learned_v3"
DESC_CHARS = 160
KINDS = ["hpd_violation", "dob_violation", "dob_complaint", "facade_filing"]
RISKS = ["serious_hazard", "housing_distress"]


def clean(v):
    """JSON-safe scalar: NaN/NaT -> None, numpy -> python, dates -> ISO."""
    if v is None or (not isinstance(v, (list, tuple)) and pd.isna(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    if hasattr(v, "item"):
        return v.item()
    return v


def write(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(data, 9))
    return path.stat().st_size


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    as_of = con.execute("select max(as_of) from risk_assessment where model_version = ?",
                        [MODEL_VERSION]).fetchone()[0]
    if OUT.exists():
        shutil.rmtree(OUT)

    m = pd.read_csv(METRICS_CSV)
    m = m[m.ranker.str.startswith(MODEL_VERSION)]
    total = write(OUT / "meta.json.gz", {
        "as_of": as_of.isoformat(), "model_version": MODEL_VERSION,
        "p_at_100": dict(zip(m.label, m["p@100"].round(3))),
        "kinds": KINDS, "risks": RISKS})

    lots = con.execute("""
    select r.bbl, r.rank, round(r.score, 2) score, r.primary_risk, r.drivers,
           round(r.p_serious_hazard, 4) ph, round(r.p_housing_distress, 4) pd,
           l.address, round(l.lat, 6) lat, round(l.lon, 6) lon, l.units_res,
           o.name owner, l.portfolio_id, l.agent_id
    from risk_assessment r join lot l using (bbl) left join owner o using (owner_id)
    where r.as_of = ? and r.model_version = ? order by r.rank
    """, [as_of, MODEL_VERSION]).df()
    cols = ["bbl", "rank", "score", "risk", "drivers", "ph", "pd", "address", "lat", "lon",
            "units", "owner", "portfolio", "agent"]
    rows = [[clean(r.bbl), clean(r.rank), clean(r.score), RISKS.index(r.primary_risk),
             [] if r.drivers is None else list(r.drivers), clean(r.ph), clean(r.pd),
             clean(r.address), clean(r.lat), clean(r.lon), clean(r.units_res), clean(r.owner),
             clean(r.portfolio_id), clean(r.agent_id)] for r in lots.itertuples()]
    portfolios = {r[0]: [r[1], list(r[2] or [])[:3], r[3], clean(r[4])] for r in con.execute(
        "select portfolio_id, name, people, num_lots, units_res from landlord_portfolio")
        .fetchall()}
    agents = {r[0]: [r[1], r[2]] for r in con.execute(
        "select agent_id, name, num_lots from managing_agent").fetchall()}
    total += write(OUT / "lots.json.gz", {"cols": cols, "rows": rows,
                                          "portfolios": portfolios, "agents": agents})

    scored = set(lots.bbl)
    blocks: dict[str, dict] = defaultdict(dict)
    facts = con.execute("""
    select l.bbl, l.bldgclass, l.year_built, l.num_floors, l.units_res, l.num_buildings,
           l.is_condo, l.landmark, l.historic_district, o.owner_type
    from lot l left join owner o using (owner_id)
    """).fetchall()
    for bbl, *f in facts:
        if bbl in scored:
            blocks[bbl[:6]][bbl] = {"facts": [clean(v) for v in f], "buildings": [],
                                    "events": []}
    for bbl, *b in con.execute("""select bbl, bin, construction_year, height_roof_ft,
                                  footprint_sqft, status from building order by bin""").fetchall():
        if bbl in scored:
            blocks[bbl[:6]][bbl]["buildings"].append([clean(v) for v in b])
    ev = con.execute(f"""
    select bbl, event_date, kind, detail, severity, is_open, left(description, {DESC_CHARS}),
           bin
    from event where bbl is not null order by bbl, event_date desc nulls last
    """)
    while batch := ev.fetchmany(200_000):
        for bbl, d, kind, detail, sev, is_open, desc, bin_ in batch:
            if bbl in scored:
                blocks[bbl[:6]][bbl]["events"].append(
                    [clean(d), KINDS.index(kind), detail, sev, is_open, desc, bin_])

    for block, data in blocks.items():
        total += write(OUT / "lot" / f"{block}.json.gz", data)
    print(f"{len(rows):,} lots, {len(blocks):,} block files, {total / 1e6:.1f} MB in {OUT}")


if __name__ == "__main__":
    main()
