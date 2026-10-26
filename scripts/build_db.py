"""Load the raw CSVs into DuckDB and resolve every record to a building (BIN) and tax lot (BBL).

Usage: .venv/Scripts/python scripts/build_db.py   ->  data/building_risk.duckdb

Resolution order, per record (the `match_method` column says which one won):
  1. bin         - the record's BIN is a real building in footprints; BBL = footprints.mappluto_bbl
  2. bbl         - the record's boro/block/lot is a PLUTO lot; BIN filled in if the lot has one building
  3. condo_unit  - the boro/block/lot is a condo unit lot (footprints.base_bbl); mapped to PLUTO's billing BBL
  4. bin_only    - real-looking BIN we can't place (demolished / new); kept, BBL null
  5. unresolved  - nothing usable
"""

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "building_risk.duckdb"


def bbl_expr(block: str, lot: str) -> str:
    """Manhattan BBL (10 digits) from block/lot text columns; null if either is missing/junk."""
    return (
        f"case when try_cast({block} as int) > 0 and try_cast({lot} as int) > 0 "
        f"then '1' || lpad(cast(cast({block} as int) as varchar), 5, '0') "
        f"|| lpad(cast(cast({lot} as int) as varchar), 4, '0') end"
    )


# A real BIN: 7 digits, Manhattan, not a "million BIN" placeholder (1000000, 2000000, ...)
VALID_BIN = "regexp_matches({0}, '^1[0-9]{{6}}$') and {0} not like '_000000'"


def build(con: duckdb.DuckDBPyConnection) -> None:
    for name in ["pluto", "footprints", "dob_violations", "dob_complaints", "facade_fisp",
                 "hpd_violations"]:
        con.sql(f"""create or replace temp view raw_{name} as
                    select * from read_csv('{(RAW / name).as_posix()}.csv',
                                           all_varchar=true, header=true)""")

    # ---- Reference tables -------------------------------------------------------------------
    con.sql("""
    create or replace table lot as
    select cast(cast(bbl as double) as bigint)::varchar as bbl,
           address, zipcode, bldgclass, landuse, ownertype, ownername,
           try_cast(numbldgs as int) as num_buildings,
           try_cast(numfloors as double) as num_floors,
           try_cast(unitsres as int) as units_res,
           try_cast(unitstotal as int) as units_total,
           try_cast(bldgarea as bigint) as bldg_area_sqft,
           nullif(try_cast(yearbuilt as int), 0) as year_built,
           nullif(try_cast(yearalter1 as int), 0) as year_altered,
           try_cast(assesstot as double) as assessed_total,
           histdist as historic_district, landmark,
           try_cast(latitude as double) as lat, try_cast(longitude as double) as lon,
           nullif(condono, '') is not null as is_condo
    from raw_pluto
    """)

    con.sql("""
    create or replace table building as
    select bin, mappluto_bbl as bbl, base_bbl,
           nullif(try_cast(construction_year as int), 0) as construction_year,
           try_cast(height_roof as double) as height_roof_ft,
           try_cast(shape_area as double) as footprint_sqft,
           last_status_type as status
    from raw_footprints
    where """ + VALID_BIN.format("bin"))

    # Lots with exactly one building, to fill in BIN for lot-level records
    con.sql("""
    create or replace temp table single_bin_lot as
    select bbl, any_value(bin) as bin from building group by bbl having count(*) = 1
    """)
    con.sql("""
    create or replace temp table condo_unit as
    select distinct base_bbl, bbl from building where base_bbl <> bbl
    """)

    # ---- Staged event tables (source columns cleaned; src_bin / src_bbl still raw) ----------
    con.sql(f"""
    create or replace temp table stg_dob_violation as
    select isn_dob_bis_viol as id, bin as src_bin, {bbl_expr('block', 'lot')} as src_bbl,
           try_strptime(issue_date, '%Y%m%d')::date as issued_on,
           try_strptime(disposition_date, '%Y%m%d')::date as disposed_on,
           violation_type_code as type_code, trim(violation_type) as type,
           violation_category as category,
           violation_category like '%ACTIVE%' as is_open,
           description, ecb_number
    from raw_dob_violations
    """)
    con.sql(f"""
    create or replace temp table stg_hpd_violation as
    select violationid as id, null::varchar as src_bin, {bbl_expr('block', 'lot')} as src_bbl,
           buildingid as hpd_building_id,
           try_cast(inspectiondate as timestamp)::date as inspected_on,
           class as hpd_class, novdescription as description,
           currentstatus as current_status,
           try_cast(currentstatusdate as timestamp)::date as status_on,
           violationstatus = 'Open' as is_open,
           rentimpairing = 'Y' as rent_impairing
    from raw_hpd_violations
    """)
    con.sql("""
    create or replace temp table stg_dob_complaint as
    select complaint_number as id, bin as src_bin, null::varchar as src_bbl,
           try_strptime(date_entered, '%m/%d/%Y')::date as entered_on,
           try_strptime(inspection_date, '%m/%d/%Y')::date as inspected_on,
           try_strptime(disposition_date, '%m/%d/%Y')::date as disposed_on,
           complaint_category as category, disposition_code, status
    from raw_dob_complaints
    """)
    con.sql(f"""
    create or replace temp table stg_facade_filing as
    select tr6_no as id, bin as src_bin, {bbl_expr('block', 'lot')} as src_bbl,
           try_cast(cycle as int) as cycle, filing_type,
           coalesce(try_strptime(filing_date, '%m/%d/%Y'),
                    try_cast(filing_date as timestamp))::date as filed_on,
           current_status, filing_status, prior_status,
           try_cast(late_filing_amt as double) + try_cast(failure_to_file_amt as double)
             + try_cast(failure_to_correct_amt as double) as penalties_usd,
           exterior_wall_type_s_ as wall_type, exterior_wall_material_s_ as wall_material
    from raw_facade_fisp
    """)

    # ---- Resolve to BIN / BBL ---------------------------------------------------------------
    valid = VALID_BIN.format("s.src_bin")
    for name in ["dob_violation", "hpd_violation", "dob_complaint", "facade_filing"]:
        con.sql(f"""
        create or replace table {name} as
        select s.* exclude (src_bin, src_bbl),
               coalesce(b.bin, case when {valid} then s.src_bin end, sb.bin) as bin,
               coalesce(b.bbl, l.bbl, cu.bbl) as bbl,
               case when b.bin is not null then 'bin'
                    when l.bbl is not null then 'bbl'
                    when cu.bbl is not null then 'condo_unit'
                    when {valid} then 'bin_only'
                    else 'unresolved' end as match_method,
               s.src_bin, s.src_bbl
        from stg_{name} s
        left join building b on b.bin = s.src_bin and {valid}
        left join lot l on l.bbl = s.src_bbl
        left join condo_unit cu on cu.base_bbl = s.src_bbl and l.bbl is null
        left join single_bin_lot sb on sb.bbl = coalesce(l.bbl, cu.bbl) and b.bin is null
        """)

    con.sql("""
    create or replace table match_report as
    with all_rows as (
      select 'dob_violation' as source, match_method, bin, bbl from dob_violation union all
      select 'hpd_violation', match_method, bin, bbl from hpd_violation union all
      select 'dob_complaint', match_method, bin, bbl from dob_complaint union all
      select 'facade_filing', match_method, bin, bbl from facade_filing)
    select source, count(*) as rows,
           round(100.0 * count(bbl) / count(*), 1) as pct_bbl,
           round(100.0 * count(bin) / count(*), 1) as pct_bin,
           count(*) filter (where match_method = 'bin') as by_bin,
           count(*) filter (where match_method = 'bbl') as by_bbl,
           count(*) filter (where match_method = 'condo_unit') as by_condo_unit,
           count(*) filter (where match_method = 'bin_only') as bin_only,
           count(*) filter (where match_method = 'unresolved') as unresolved
    from all_rows group by source order by source
    """)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # DuckDB's table borders on the Windows console
    DB.unlink(missing_ok=True)
    with duckdb.connect(str(DB)) as con:
        build(con)
        for t in ["lot", "building", "dob_violation", "hpd_violation", "dob_complaint",
                  "facade_filing"]:
            print(f"{t:15} {con.execute(f'select count(*) from {t}').fetchall()[0][0]:>9,}")
        print()
        con.sql("select * from match_report").show()


if __name__ == "__main__":
    main()
