"""Build the ontology layer on top of data/building_risk.duckdb (run scripts/build_db.py first).

Adds: owner, lot.owner_id, dob_violation.dob_class, event (all sources in one shape),
link_check (orphan counts per link), the empty risk_assessment table (filled by score.py),
and two point-in-time table macros:

    select * from lot_features_at(DATE '2024-01-01')
    select * from building_features_at(DATE '2024-01-01')

They only count events before as_of, so the same features feed a backtest and today's score.
The spec lives in ontology/ontology.yaml.

Usage: .venv/Scripts/python scripts/build_ontology.py
"""

import hashlib
import sys
from collections import Counter
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "building_risk.duckdb"
RAW = ROOT / "data" / "raw"

# HPD registration contacts that identify who really controls a building. Managing agents are
# kept separately: one agent runs buildings for many unrelated owners.
PERSON_TYPES = ["HeadOfficer", "IndividualOwner", "JointOwner"]

# DOB violation type codes -> what kind of risk they say something about
DOB_CLASSES = {
    "hazard": ["FISPHAZ", "FISPFCS", "IMEGNCY", "EGNCY", "COMPBLD", "UB", "CLOS"],
    "equipment": ["LBLVIO", "HBLVIO", "B", "E", "AEUHAZ1", "EVCAT5", "HVCAT5", "JVCAT5",
                  "LL6291", "LL1081"],
    "construction": ["C", "P"],
    # Paperwork: benchmarking, energy grade, landmark, zoning, reports not filed
    "admin": ["BENCH", "EGRADE", "EARCX", "LANDMK", "Z", "FISPNRF", "FISP", "RWNRF"],
}
OWNER_PLACEHOLDERS = ["UNAVAILABLE OWNER", "NAME NOT ON FILE", "OWNER NOT ON FILE", "UNKNOWN"]


def sql_list(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


def build_portfolios(con: duckdb.DuckDBPyConnection) -> None:
    """Landlord portfolios and managing agents from HPD registrations.

    Two registered lots are in the same portfolio when their registrations share a head
    officer / individual / joint owner (by name) or a corporate-owner name; portfolios are the
    connected components of that lot-contact graph. Each lot also gets the managing agent from
    its latest registration.
    """
    for name in ["hpd_registrations", "hpd_contacts"]:
        con.sql(f"""create or replace temp view raw_{name} as
                    select * from read_csv('{(RAW / name).as_posix()}.csv',
                                           all_varchar=true, header=true)""")
    # Registration -> PLUTO lot (condo unit lots mapped to the billing lot)
    con.sql("""
    create or replace temp table reg_lot as
    with r as (
      select registrationid, try_cast(lastregistrationdate as timestamp) as registered_at,
             case when try_cast(block as int) > 0 and try_cast(lot as int) > 0
                  then '1' || lpad(cast(cast(block as int) as varchar), 5, '0')
                       || lpad(cast(cast(lot as int) as varchar), 4, '0') end as src_bbl
      from raw_hpd_registrations)
    select r.registrationid, r.registered_at, coalesce(l.bbl, cu.bbl) as bbl
    from r left join lot l on l.bbl = r.src_bbl
    left join (select distinct base_bbl, bbl from building where base_bbl <> bbl) cu
      on cu.base_bbl = r.src_bbl and l.bbl is null
    where coalesce(l.bbl, cu.bbl) is not null
    """)
    clean = "trim(regexp_replace(upper({}), '[^A-Z0-9 ]', ' ', 'g'))"
    person = (f"{clean.format('k.firstname')} || ' ' || {clean.format('k.lastname')}")
    edges = con.sql(f"""
    select distinct r.bbl,
           case when k.type in ({sql_list(PERSON_TYPES)}) then 'P:' || {person}
                else 'C:' || {clean.format('k.corporationname')} end as key
    from reg_lot r join raw_hpd_contacts k using (registrationid)
    where (k.type in ({sql_list(PERSON_TYPES)}) and length(trim(k.lastname)) > 1
           and length(trim(coalesce(k.firstname, ''))) > 0)
       or (k.type = 'CorporateOwner' and length(trim(coalesce(k.corporationname, ''))) > 3)
    """).fetchall()

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for bbl, key in edges:
        parent[find(bbl)] = find(key)
    # Name each portfolio after its most-shared person (else corporation)
    keys_by_root: dict[str, Counter] = {}
    for bbl, key in edges:
        keys_by_root.setdefault(find(bbl), Counter())[key] += 1
    rows = []
    for bbl in {b for b, _ in edges}:
        root = find(bbl)
        ranked = sorted(keys_by_root[root].items(), key=lambda kv: (not kv[0].startswith("P:"),
                                                                    -kv[1], kv[0]))
        rows.append({"bbl": bbl, "root": root, "name": ranked[0][0][2:],
                     "people": [k[2:] for k, _ in ranked if k.startswith("P:")][:5]})
    lot_pf = pd.DataFrame(rows)
    lot_pf["portfolio_id"] = "pf_" + lot_pf.root.map(
        lambda r: hashlib.md5(r.encode()).hexdigest()[:12])
    con.register("lot_pf", lot_pf)
    con.sql("""
    create or replace table landlord_portfolio as
    select p.portfolio_id, any_value(p.name) as name, any_value(p.people) as people,
           count(*) as num_lots, sum(coalesce(l.units_res, 0)) as units_res
    from lot_pf p join lot l using (bbl) group by p.portfolio_id
    """)
    con.sql("alter table lot add column if not exists portfolio_id varchar")
    con.sql("update lot set portfolio_id = p.portfolio_id from lot_pf p where p.bbl = lot.bbl")
    con.unregister("lot_pf")

    # Managing agent from each lot's latest registration
    con.sql(f"""
    create or replace temp table lot_agent as
    select r.bbl,
           arg_max(coalesce(nullif({clean.format('k.corporationname')}, ''), {person}),
                   r.registered_at) as agent_name
    from reg_lot r join raw_hpd_contacts k using (registrationid)
    where k.type = 'Agent' group by r.bbl
    """)
    con.sql("""
    create or replace table managing_agent as
    select 'agt_' || substr(md5(agent_name), 1, 12) as agent_id, agent_name as name,
           count(*) as num_lots, sum(coalesce(l.units_res, 0)) as units_res
    from lot_agent a join lot l using (bbl)
    where length(trim(agent_name)) > 1 group by agent_name
    """)
    con.sql("alter table lot add column if not exists agent_id varchar")
    con.sql("""
    update lot set agent_id = 'agt_' || substr(md5(a.agent_name), 1, 12)
    from lot_agent a where a.bbl = lot.bbl and length(trim(a.agent_name)) > 1
    """)


def build(con: duckdb.DuckDBPyConnection) -> None:
    # ---- Owner ------------------------------------------------------------------------------
    # Normalize: uppercase, punctuation -> space, collapse spaces, drop entity suffixes.
    con.sql(f"""
    create or replace temp table lot_owner as
    with n as (
      select bbl, ownertype,
             trim(regexp_replace(regexp_replace(upper(ownername), '[^A-Z0-9 ]', ' ', 'g'),
                                 ' +', ' ', 'g')) as n0
      from lot)
    select bbl, ownertype,
           case when n0 is null or n0 = '' or n0 in ({sql_list(OWNER_PLACEHOLDERS)}) then null
                else trim(regexp_replace(n0,
                  '( (LLC|L L C|INC|CORP|CORPORATION|CO|LP|L P|LTD|LLP|COMPANY))+$', '')) end
             as owner_name
    from n
    """)
    con.sql("""
    create or replace table owner as
    select 'own_' || substr(md5(o.owner_name), 1, 12) as owner_id,
           o.owner_name as name,
           -- PLUTO ownertype: C city, M mixed city/private, O other public, X tax-exempt,
           -- P private; blank is overwhelmingly private
           case mode(o.ownertype) when 'C' then 'city' when 'M' then 'mixed'
                when 'O' then 'other_public' when 'X' then 'tax_exempt'
                else 'private' end as owner_type,
           count(*) as num_lots,
           sum(coalesce(l.num_buildings, 0)) as num_buildings,
           sum(coalesce(l.units_res, 0)) as units_res
    from lot_owner o join lot l using (bbl)
    where o.owner_name is not null
    group by o.owner_name
    """)
    con.sql("alter table lot add column if not exists owner_id varchar")
    con.sql("""
    update lot set owner_id = 'own_' || substr(md5(o.owner_name), 1, 12)
    from lot_owner o where o.bbl = lot.bbl and o.owner_name is not null
    """)
    build_portfolios(con)

    # ---- DOB violation class ----------------------------------------------------------------
    cases = " ".join(f"when split_part(type, '-', 1) in ({sql_list(codes)}) then '{cls}'"
                     for cls, codes in DOB_CLASSES.items())
    con.sql("alter table dob_violation add column if not exists dob_class varchar")
    con.sql(f"update dob_violation set dob_class = case {cases} else 'other' end")

    # ---- Event: every source in one shape (timeline + features) ------------------------------
    con.sql("""
    create or replace table event as
    select 'hpd:' || id as event_id, 'hpd_violation' as kind, id as source_id,
           bin, bbl, inspected_on as event_date, hpd_class as detail,
           case hpd_class when 'C' then 3 when 'B' then 2 else 1 end as severity,
           is_open, case when not is_open then status_on end as closed_on,
           rent_impairing, description
    from hpd_violation
    union all
    select 'dob:' || id, 'dob_violation', id, bin, bbl, issued_on, dob_class,
           case dob_class when 'hazard' then 3 when 'admin' then 1 else 2 end,
           is_open, disposed_on, null, description
    from dob_violation
    union all
    select 'cmp:' || id, 'dob_complaint', id, bin, bbl, entered_on, category, 1,
           status <> 'CLOSED', disposed_on, null, null
    from dob_complaint
    union all
    select 'fsp:' || id, 'facade_filing', id, bin, bbl, filed_on, current_status,
           case current_status when 'UNSAFE' then 3 when 'SWARMP' then 2
                when 'No Report Filed' then 2 else 0 end,
           null, null, null, coalesce(wall_material, '')
    from facade_filing
    """)

    # ---- Writeback (actions fill these; scoring fills risk_assessment) ----------------------
    con.sql("""
    create table if not exists risk_assessment (
      bbl varchar, as_of date, model_version varchar, score double, rank integer,
      drivers varchar[], created_at timestamp default current_timestamp,
      primary key (bbl, as_of, model_version))
    """)
    # inspection_task lives in data/actions.duckdb (created by the app), so rebuilding this
    # database never wipes what inspectors recorded.
    con.sql("drop table if exists inspection_task")

    # ---- Point-in-time derived features -----------------------------------------------------
    # An event counts as open at as_of if it closed on/after as_of, or (close date unknown) it
    # is open now. HPD close dates are approximated by the current-status date.
    con.sql("""
    create or replace macro lot_features_at(as_of) as table
    with e as (
      select *, coalesce(closed_on >= as_of::date, is_open, false) as open_at
      from event where bbl is not null and event_date < as_of::date),
    ev as (
      select bbl,
        count(*) filter (where kind = 'hpd_violation' and detail = 'C'
                           and event_date >= as_of::date - interval 12 month) as hpd_c_12m,
        count(*) filter (where kind = 'hpd_violation' and detail = 'B'
                           and event_date >= as_of::date - interval 12 month) as hpd_b_12m,
        count(*) filter (where kind = 'hpd_violation' and detail in ('A', 'I')
                           and event_date >= as_of::date - interval 12 month) as hpd_a_12m,
        count(*) filter (where kind = 'hpd_violation' and detail = 'C' and open_at)
          as hpd_open_c,
        count(*) filter (where kind = 'hpd_violation' and rent_impairing
                           and event_date >= as_of::date - interval 12 month)
          as hpd_rent_impairing_12m,
        count(*) filter (where kind = 'hpd_violation' and detail in ('B', 'C')
                           and event_date >= as_of::date - interval 6 month) as hpd_bc_last_6m,
        count(*) filter (where kind = 'hpd_violation' and detail in ('B', 'C')
                           and event_date >= as_of::date - interval 12 month
                           and event_date < as_of::date - interval 6 month) as hpd_bc_prior_6m,
        count(*) filter (where kind = 'dob_violation' and detail = 'hazard'
                           and event_date >= as_of::date - interval 36 month) as dob_hazard_36m,
        count(*) filter (where kind = 'dob_violation' and detail = 'equipment'
                           and event_date >= as_of::date - interval 12 month)
          as dob_equipment_12m,
        count(*) filter (where kind = 'dob_violation' and detail = 'admin'
                           and event_date >= as_of::date - interval 12 month) as dob_admin_12m,
        count(*) filter (where kind = 'dob_violation' and detail <> 'admin' and open_at)
          as dob_open,
        count(*) filter (where kind = 'dob_complaint'
                           and event_date >= as_of::date - interval 12 month) as complaints_12m
      from e group by bbl),
    facade_bin as (  -- latest filing per building, then the worst building on the lot
      select bbl, bin, arg_max(severity, event_date) as sev, arg_max(detail, event_date) as status,
             max(event_date) as filed_on
      from e where kind = 'facade_filing' group by bbl, bin),
    facade as (
      select bbl, arg_max(status, sev) as facade_status, max(sev) as facade_severity,
             min(filed_on) as facade_oldest_latest_filing
      from facade_bin group by bbl),
    f as (
      select l.bbl, l.owner_id, l.portfolio_id, l.agent_id,
             year(as_of::date) - l.year_built as building_age,
             l.num_floors, l.units_res, l.units_total, l.num_buildings, l.is_condo,
             l.landmark is not null as is_landmark, l.historic_district is not null as in_hist_district,
             coalesce(ev.hpd_c_12m, 0) as hpd_c_12m,
             coalesce(ev.hpd_b_12m, 0) as hpd_b_12m,
             coalesce(ev.hpd_a_12m, 0) as hpd_a_12m,
             coalesce(ev.hpd_open_c, 0) as hpd_open_c,
             coalesce(ev.hpd_rent_impairing_12m, 0) as hpd_rent_impairing_12m,
             coalesce(ev.hpd_bc_last_6m, 0) as hpd_bc_last_6m,
             coalesce(ev.hpd_bc_prior_6m, 0) as hpd_bc_prior_6m,
             coalesce(ev.dob_hazard_36m, 0) as dob_hazard_36m,
             coalesce(ev.dob_equipment_12m, 0) as dob_equipment_12m,
             coalesce(ev.dob_admin_12m, 0) as dob_admin_12m,
             coalesce(ev.dob_open, 0) as dob_open,
             coalesce(ev.complaints_12m, 0) as complaints_12m,
             fa.facade_status, coalesce(fa.facade_severity, 0) as facade_severity,
             date_diff('day', fa.facade_oldest_latest_filing, as_of::date) / 365.25
               as facade_filing_age_years
      from lot l left join ev using (bbl) left join facade fa using (bbl))
    select f.*,
           round(100.0 * f.hpd_c_12m / nullif(f.units_res, 0), 2) as hpd_c_per_100_units,
           -- Owner / portfolio / agent: class C per 100 units across all their lots (incl.
           -- this one). Null when the lot has no such link (not one big "unknown" group).
           o.num_lots as owner_lots,
           case when f.owner_id is not null then round(100.0 * sum(f.hpd_c_12m) over w_own
             / nullif(sum(f.units_res) over w_own, 0), 2) end as owner_c_per_100_units,
           p.num_lots as portfolio_lots,
           case when f.portfolio_id is not null then round(100.0 * sum(f.hpd_c_12m) over w_pf
             / nullif(sum(f.units_res) over w_pf, 0), 2) end as portfolio_c_per_100_units,
           -- Landlord's hazard record on their *other* buildings
           case when f.portfolio_id is not null
                then sum(f.dob_hazard_36m) over w_pf - f.dob_hazard_36m end
             as portfolio_other_hazards_36m,
           case when f.portfolio_id is not null
                then sum((f.facade_severity = 3)::int) over w_pf - (f.facade_severity = 3)::int end
             as portfolio_other_unsafe_facades,
           a.num_lots as agent_lots,
           case when f.agent_id is not null then round(100.0 * sum(f.hpd_c_12m) over w_agt
             / nullif(sum(f.units_res) over w_agt, 0), 2) end as agent_c_per_100_units,
           as_of::date as as_of
    from f left join owner o using (owner_id)
    left join landlord_portfolio p using (portfolio_id)
    left join managing_agent a using (agent_id)
    window w_own as (partition by f.owner_id), w_pf as (partition by f.portfolio_id),
           w_agt as (partition by f.agent_id)
    """)

    con.sql("""
    create or replace macro building_features_at(as_of) as table
    with e as (
      select *, coalesce(closed_on >= as_of::date, is_open, false) as open_at
      from event where bin is not null and event_date < as_of::date),
    ev as (
      select bin,
        count(*) filter (where kind = 'hpd_violation' and detail = 'C'
                           and event_date >= as_of::date - interval 12 month) as hpd_c_12m,
        count(*) filter (where kind = 'dob_violation' and detail = 'hazard'
                           and event_date >= as_of::date - interval 36 month) as dob_hazard_36m,
        count(*) filter (where kind = 'dob_violation' and detail = 'equipment'
                           and event_date >= as_of::date - interval 12 month)
          as dob_equipment_12m,
        count(*) filter (where kind = 'dob_violation' and detail <> 'admin' and open_at)
          as dob_open,
        count(*) filter (where kind = 'dob_complaint'
                           and event_date >= as_of::date - interval 12 month) as complaints_12m,
        arg_max(detail, event_date) filter (where kind = 'facade_filing') as facade_status
      from e group by bin)
    select b.bin, b.bbl, year(as_of::date) - b.construction_year as building_age,
           b.height_roof_ft,
           coalesce(ev.hpd_c_12m, 0) as hpd_c_12m,
           coalesce(ev.dob_hazard_36m, 0) as dob_hazard_36m,
           coalesce(ev.dob_equipment_12m, 0) as dob_equipment_12m,
           coalesce(ev.dob_open, 0) as dob_open,
           coalesce(ev.complaints_12m, 0) as complaints_12m,
           ev.facade_status, as_of::date as as_of
    from building b left join ev using (bin)
    """)

    # ---- Link integrity: how many rows point at an object that isn't there ------------------
    con.sql("""
    create or replace table link_check as
    select 'owner_owns_lot' as link, count(*) as rows,
           count(*) filter (where owner_id is null) as no_key,
           count(*) filter (where owner_id is not null
                              and owner_id not in (select owner_id from owner)) as orphans
    from lot
    union all
    select 'portfolio_controls_lot', count(*), count(*) filter (where portfolio_id is null),
           count(*) filter (where portfolio_id is not null and portfolio_id not in
                              (select portfolio_id from landlord_portfolio)) from lot
    union all
    select 'agent_manages_lot', count(*), count(*) filter (where agent_id is null),
           count(*) filter (where agent_id is not null and agent_id not in
                              (select agent_id from managing_agent)) from lot
    union all
    select 'lot_has_building', count(*), count(*) filter (where bbl is null),
           count(*) filter (where bbl not in (select bbl from lot)) from building
    union all
    select 'lot_has_event', count(*), count(*) filter (where bbl is null),
           count(*) filter (where bbl not in (select bbl from lot)) from event
    union all
    select 'building_has_event', count(*), count(*) filter (where bin is null),
           count(*) filter (where bin not in (select bin from building)) from event
    """)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with duckdb.connect(str(DB)) as con:
        build(con)
        con.sql("select owner_type, count(*) owners, sum(num_lots) lots from owner "
                "group by 1 order by 2 desc").show()
        con.sql("select kind, count(*) events, count(bbl) with_lot from event "
                "group by 1 order by 1").show()
        con.sql("select * from link_check").show()


if __name__ == "__main__":
    main()
