"""Building-risk app: map, building 360 and inspector queue.

Run: .venv/Scripts/streamlit run app/app.py
"""

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

import db

st.set_page_config(page_title="Building Risk · Manhattan", page_icon="🏢", layout="wide")

AS_OF = db.latest_as_of()
LOTS = db.scored_lots(AS_OF)
KIND_LABELS = {"hpd_violation": "HPD violation", "dob_violation": "DOB violation",
               "dob_complaint": "DOB complaint", "facade_filing": "Facade filing"}
OUTCOME_LABELS = {"hazard_found": "Hazard found", "minor_issues": "Minor issues",
                  "no_issue": "No issue", "no_access": "No access"}


def as_list(drivers) -> list[str]:
    """Drivers come back from DuckDB as a numpy array (or None)."""
    return [] if drivers is None else list(drivers)


def show(value) -> str:
    # "none", not "-": st.table renders a lone "-" as a markdown bullet
    return "none" if value is None or pd.isna(value) or value == "" else str(value)


def open_building(bbl: str) -> None:
    st.session_state.bbl = bbl
    st.switch_page(BUILDING_PAGE)


def color(score: float) -> list[int]:
    if score >= 99:
        return [200, 30, 45, 220]
    if score >= 95:
        return [235, 120, 30, 200]
    if score >= 80:
        return [230, 190, 40, 170]
    return [140, 150, 160, 110]


# ---- Map ------------------------------------------------------------------------------------

def map_page() -> None:
    st.title("Risk map")
    st.caption(f"{len(LOTS):,} Manhattan lots scored as of {AS_OF}. "
               "Score = percentile of the lot's worse risk.")
    c1, c2 = st.columns([2, 3])
    min_score = c1.slider("Minimum score", 0, 100, 98)
    risks = c2.multiselect("Main risk", list(db.RISK_LABELS.values()),
                           default=list(db.RISK_LABELS.values()))
    df = LOTS[(LOTS.score >= min_score) & LOTS.risk.isin(risks)].dropna(subset=["lat", "lon"])
    df = df.assign(color=df.score.map(color), score_txt=df.score.round(1))

    layer = pdk.Layer(
        "ScatterplotLayer", id="lots",
        data=df[["bbl", "address", "lat", "lon", "color", "score_txt", "risk", "rank", "why"]],
        get_position=["lon", "lat"], get_fill_color="color", get_radius=28,
        radius_min_pixels=3, radius_max_pixels=12, pickable=True, auto_highlight=True)
    deck = pdk.Deck(
        layers=[layer],
        map_style="https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        initial_view_state=pdk.ViewState(latitude=40.79, longitude=-73.96, zoom=11.3),
        tooltip={"html": "<b>{address}</b><br/>#{rank} · score {score_txt}<br/>{risk}<br/>{why}"})
    event = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object",
                            height=560, key="map")
    st.caption(f"{len(df):,} lots shown. Red: top 1% · orange: top 5% · yellow: top 20%. "
               "Click a lot to open it.")

    picked = (event.selection.get("objects", {}).get("lots") or [None])[0] if event else None
    if picked:
        st.info(f"**{picked['address']}** · #{picked['rank']} · {picked['risk']}  \n"
                f"{picked['why']}")
        if st.button("Open building 360", type="primary"):
            open_building(picked["bbl"])


# ---- Building 360 ---------------------------------------------------------------------------

def building_page() -> None:
    options = LOTS.bbl.tolist()
    labels = dict(zip(LOTS.bbl, LOTS.address.fillna("?") + "  ·  #" + LOTS["rank"].astype(str)))
    current = st.session_state.get("bbl", options[0])
    bbl = st.selectbox("Lot", options, index=options.index(current) if current in options else 0,
                       format_func=lambda b: labels.get(b, b))
    st.session_state.bbl = bbl
    lot = db.lot(bbl)
    risk = LOTS.set_index("bbl").loc[bbl]

    st.title(lot.address or bbl)
    st.caption(f"BBL {bbl} · building class {show(lot.bldgclass)} · scored {AS_OF}")
    m = st.columns(4)
    m[0].metric("Score", f"{risk.score:.1f}")
    m[1].metric(f"Rank of {len(LOTS):,}", f"#{risk['rank']:,}")
    m[2].metric("P(serious hazard, 12m)", f"{risk.p_serious_hazard:.0%}")
    m[3].metric("P(housing distress, 12m)", f"{risk.p_housing_distress:.0%}")
    drivers = as_list(risk.drivers)
    st.markdown(f"**Main risk: {risk.risk}.** " + (("Why: " + " · ".join(drivers))
                                                   if drivers else ""))

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Event timeline")
        ev = db.events(bbl)
        if ev.empty:
            st.write("No recorded events since 2020.")
        else:
            ev["type"] = ev.kind.map(KIND_LABELS)
            # Violations and complaints start in 2020 (older facade filings would stretch it)
            monthly = (ev[pd.to_datetime(ev.event_date) >= "2020-01-01"]
                       .assign(month=lambda d: pd.to_datetime(d.event_date).dt.to_period("M")
                               .dt.to_timestamp())
                       .groupby(["month", "type"]).size().rename("events").reset_index())
            st.altair_chart(
                alt.Chart(monthly).mark_bar().encode(
                    x=alt.X("month:T", title=None), y=alt.Y("events:Q", title="Events"),
                    color=alt.Color("type:N", title=None, legend=alt.Legend(orient="top")),
                    tooltip=["month:T", "type:N", "events:Q"]).properties(height=240),
                use_container_width=True)
            f1, f2 = st.columns(2)
            kinds = f1.multiselect("Type", sorted(ev.type.unique()), key="ev_kinds")
            only_open = f2.checkbox("Open only")
            shown = ev[ev.type.isin(kinds)] if kinds else ev
            if only_open:
                shown = shown[shown.is_open.fillna(False)]
            st.dataframe(
                shown[["event_date", "type", "detail", "severity", "is_open", "description",
                       "bin"]],
                hide_index=True, use_container_width=True, height=300,
                column_config={"event_date": st.column_config.DateColumn("Date"),
                               "detail": "Class / status",
                               "is_open": "Open", "description": st.column_config.TextColumn(
                                   "Description", width="large")})
    with right:
        st.subheader("Lot")
        facts = {
            "Year built": lot.year_built, "Floors": lot.num_floors,
            "Residential units": lot.units_res, "Buildings": lot.num_buildings,
            "Condo": "yes" if lot.is_condo else "no",
            "Landmark": lot.landmark, "Historic district": lot.historic_district,
        }
        st.table(pd.DataFrame({"": [show(v) for v in facts.values()]}, index=facts.keys()))
        st.dataframe(db.buildings(bbl), hide_index=True, use_container_width=True)

        st.subheader("Owner")
        if lot.owner_id is None or pd.isna(lot.owner_id):
            st.write("No owner on record.")
        else:
            st.write(f"**{lot.owner}** ({lot.owner_type}), {lot.owner_lots} lot(s) in Manhattan")
            pf = db.portfolio(lot.owner_id, AS_OF)
            if len(pf) > 1:
                st.dataframe(pf[pf.bbl != bbl], hide_index=True, use_container_width=True,
                             height=min(250, 40 + 35 * len(pf)))

    st.subheader("Inspection tasks")
    tk = db.tasks(bbl)
    if not tk.empty:
        st.dataframe(tk[["task_id", "status", "priority", "assigned_to", "outcome", "notes",
                         "created_at"]], hide_index=True, use_container_width=True)
    with st.form("create_task"):
        reason = st.text_input("Reason", "; ".join(drivers))
        priority = st.selectbox("Priority", [1, 2, 3], index=db.priority_for(risk["rank"]) - 1,
                                format_func=lambda p: {1: "1 · high", 2: "2 · medium",
                                                       3: "3 · low"}[p])
        if st.form_submit_button("Create inspection task", type="primary"):
            task_id = db.create_task(bbl, reason, priority, AS_OF)
            st.toast(f"Created {task_id}")
            st.rerun()


# ---- Inspector queue ------------------------------------------------------------------------

def queue_page() -> None:
    st.title("Inspector queue")
    prec = db.backtest_precision()
    if prec:
        st.caption(
            "In the 2024 backtest, of the top 100 lots: "
            f"{prec.get('serious_hazard', 0):.0%} had a serious hazard, "
            f"{prec.get('housing_distress', 0):.0%} had 10+ new class C violations, "
            "within 12 months.")

    all_tasks = db.tasks()
    open_bbls = set(all_tasks[all_tasks.status.isin(db.OPEN_STATUSES)].bbl)

    c1, c2, c3 = st.columns([2, 1, 1])
    risks = c1.multiselect("Main risk", list(db.RISK_LABELS.values()),
                           default=list(db.RISK_LABELS.values()), key="q_risks")
    top_n = c2.number_input("Show top", 25, 2000, 100, step=25)
    hide_tasked = c3.checkbox("Hide lots with an open task", True)
    q = LOTS[LOTS.risk.isin(risks)]
    if hide_tasked:
        q = q[~q.bbl.isin(open_bbls)]
    q = q.head(int(top_n))

    sel = st.dataframe(
        q[["rank", "address", "score", "risk", "why", "units_res", "owner"]],
        hide_index=True, use_container_width=True, height=420,
        on_select="rerun", selection_mode="multi-row",
        # New key after creating tasks: the old row positions would point at other lots
        key=f"queue_{st.session_state.get('queue_gen', 0)}",
        column_config={"rank": "#", "score": st.column_config.NumberColumn(format="%.1f"),
                       "risk": "Main risk", "why": st.column_config.TextColumn(
                           "Drivers", width="large"), "units_res": "Units"})
    rows = q.iloc[sel.selection.rows] if sel else q.iloc[[]]
    b1, b2 = st.columns([1, 4])
    if b1.button(f"Create {len(rows)} task(s)", type="primary", disabled=rows.empty):
        for r in rows.itertuples():
            db.create_task(r.bbl, r.why, db.priority_for(r.rank), AS_OF)
        st.session_state.queue_gen = st.session_state.get("queue_gen", 0) + 1
        st.toast(f"Created {len(rows)} task(s)")
        st.rerun()
    if len(rows) == 1 and b2.button("Open building 360"):
        open_building(rows.bbl.iloc[0])

    st.divider()
    st.subheader("Tasks")
    open_t = all_tasks[all_tasks.status.isin(db.OPEN_STATUSES)]
    done_t = all_tasks[~all_tasks.status.isin(db.OPEN_STATUSES)]
    k = st.columns(4)
    k[0].metric("Open", len(open_t))
    k[1].metric("Closed", len(done_t))
    found = (done_t.outcome == "hazard_found").sum()
    k[2].metric("Hazard found", found)
    k[3].metric("Hit rate", f"{found / len(done_t):.0%}" if len(done_t) else "-")

    tab_open, tab_done = st.tabs([f"Open ({len(open_t)})", f"Closed ({len(done_t)})"])
    with tab_open:
        if open_t.empty:
            st.write("No open tasks. Select lots in the queue above to create some.")
        else:
            st.dataframe(open_t[["task_id", "address", "priority", "status", "assigned_to",
                                 "reason", "created_at"]],
                         hide_index=True, use_container_width=True)
            with st.form("task_action"):
                labels = dict(zip(open_t.task_id, open_t.task_id + " · " + open_t.address))
                task_id = st.selectbox("Task", open_t.task_id, format_func=labels.get)
                action = st.radio("Action", ["Assign inspector", "Record outcome",
                                             "Flag false positive"], horizontal=True)
                inspector = st.text_input("Inspector (for Assign)")
                outcome = st.selectbox("Outcome (for Record outcome)", db.OUTCOMES,
                                       format_func=OUTCOME_LABELS.get)
                notes = st.text_area("Notes")
                if st.form_submit_button("Apply", type="primary"):
                    if action == "Assign inspector":
                        if not inspector.strip():
                            st.error("Enter the inspector's name.")
                            st.stop()
                        db.assign_inspector(task_id, inspector.strip())
                    elif action == "Record outcome":
                        db.record_outcome(task_id, outcome, notes)
                    else:
                        db.flag_false_positive(task_id, notes)
                    st.toast(f"{action}: {task_id}")
                    st.rerun()
    with tab_done:
        if not done_t.empty:
            st.dataframe(done_t[["task_id", "address", "status", "outcome", "assigned_to",
                                 "notes", "updated_at"]],
                         hide_index=True, use_container_width=True)


MAP_PAGE = st.Page(map_page, title="Risk map", icon="🗺️", default=True)
BUILDING_PAGE = st.Page(building_page, title="Building 360", icon="🏢", url_path="building")
QUEUE_PAGE = st.Page(queue_page, title="Inspector queue", icon="📋", url_path="queue")
st.navigation([MAP_PAGE, BUILDING_PAGE, QUEUE_PAGE]).run()
