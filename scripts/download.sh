#!/usr/bin/env bash
# Download the project's raw data from NYC Open Data (Socrata API).
# Scope: Manhattan only, 2020 onward. Re-run to refresh.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw
B=https://data.cityofnewyork.us/resource

get() {  # name, dataset id, where, [select]
  local out="data/raw/$1.csv"
  echo "-> $1 ($2)"
  curl -sf -G --retry 3 --retry-all-errors --max-time 1800 "$B/$2.csv" \
    --data-urlencode "\$where=$3" \
    ${4:+--data-urlencode "\$select=$4"} \
    --data-urlencode "\$limit=5000000" -o "$out"
  echo "   $(($(wc -l < "$out") - 1)) rows, $(du -h "$out" | cut -f1)"
}

# Backbone: one row per tax lot (BBL) with building attributes
get pluto 64uk-42ks "borough='MN'"
# DOB violations (construction / equipment code), keyed on BIN
get dob_violations 3h2n-5cm9 "boro='1' AND issue_date >= '20200101'"
# HPD housing-maintenance violations (class A/B/C), keyed on BuildingID / BBL
get hpd_violations wvxf-dwi5 "boroid='1' AND inspectiondate >= '2020-01-01'" \
  "violationid,buildingid,boroid,block,lot,housenumber,streetname,zip,class,inspectiondate,novdescription,currentstatus,currentstatusdate,violationstatus,rentimpairing"
# DOB complaints, keyed on BIN (community board 1xx = Manhattan)
get dob_complaints eabe-havv "starts_with(community_board,'1') AND date_entered like '%/202%'"
# Facade inspections (FISP / Local Law 11), keyed on BIN
get facade_fisp xubg-57si "borough='Manhattan'"
# Building footprints: the BIN <-> BBL bridge (PLUTO has no BIN). Manhattan BINs start with 1.
get footprints 5zhs-2jue "bin >= 1000000 AND bin < 2000000" \
  "bin,base_bbl,mappluto_bbl,construction_year,height_roof,shape_area,last_status_type,feature_code"
