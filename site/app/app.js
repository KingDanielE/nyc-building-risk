// Building risk: browser version of the Streamlit app (app/app.py).
// Data comes from scripts/export_web.py as gzipped JSON; tasks live in localStorage.

const KIND_LABELS = ["HPD violation", "DOB violation", "DOB complaint", "Facade filing"];
const RISK_LABELS = ["Serious hazard", "Housing distress"];
const OUTCOMES = { hazard_found: "Hazard found", minor_issues: "Minor issues", no_issue: "No issue", no_access: "No access" };
const OPEN_STATUSES = ["open", "assigned"];
const TASKS_KEY = "nbr.tasks.v1";
const EVENT_PAGE = 200;

const S = { meta: null, lots: [], byBbl: new Map(), portfolios: {}, agents: {}, blocks: new Map(),
            bbl: null, map: null, qSel: new Set(), evShown: EVENT_PAGE };

// ---- Helpers -------------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const show = (v) => (v === null || v === undefined || v === "" ? "none" : v);
const fmt = (n) => (n === null || n === undefined ? "none" : Number(n).toLocaleString("en-US"));
const pct = (p) => `${Math.round(p * 100)}%`;
const priorityFor = (rank) => (rank <= 100 ? 1 : rank <= 500 ? 2 : 3);
const PRIORITY = { 1: "1 · high", 2: "2 · medium", 3: "3 · low" };

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 2600);
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  const buf = await res.arrayBuffer();
  const head = new Uint8Array(buf, 0, 2);
  // Gzip magic bytes: decompress here (unless the server already sent it decoded)
  if (head[0] === 0x1f && head[1] === 0x8b) {
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    return JSON.parse(await new Response(stream).text());
  }
  return JSON.parse(new TextDecoder().decode(buf));
}

async function block(bbl) {
  const key = bbl.slice(0, 6);
  if (!S.blocks.has(key)) S.blocks.set(key, getJson(`data/lot/${key}.json.gz`));
  return (await S.blocks.get(key))[bbl];
}

// ---- Tasks (writeback; ontology.yaml actions) -----------------------------------------------

function loadTasks() {
  try { return JSON.parse(localStorage.getItem(TASKS_KEY)) || []; } catch { return []; }
}
function saveTasks(tasks) {
  try { localStorage.setItem(TASKS_KEY, JSON.stringify(tasks)); }
  catch { toast("Couldn't save: browser storage is blocked"); }
}
function createTask(bbl, reason, priority, bin) {
  const tasks = loadTasks();
  const id = "T-" + crypto.getRandomValues(new Uint32Array(1))[0].toString(16).toUpperCase().padStart(8, "0");
  tasks.unshift({ task_id: id, bbl, bin: bin || null, as_of: S.meta.as_of, model_version: S.meta.model_version,
                  reason, priority, status: "open", assigned_to: null, outcome: null, notes: "",
                  created_at: new Date().toISOString(), updated_at: null });
  saveTasks(tasks);
  return id;
}
function updateTask(id, fields) {
  const tasks = loadTasks();
  const t = tasks.find((x) => x.task_id === id);
  if (t) Object.assign(t, fields, { updated_at: new Date().toISOString() });
  saveTasks(tasks);
}
const when = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "");

// ---- Routing ---------------------------------------------------------------------------------

function route() {
  const [page, arg] = location.hash.replace(/^#/, "").split("/");
  const name = ["map", "lot", "queue"].includes(page) ? page : "map";
  $$(".page").forEach((p) => (p.hidden = p.id !== `page-${name}`));
  $$("nav a[data-page]").forEach((a) => a.classList.toggle("on", a.dataset.page === name));
  if (name === "map") renderMap();
  if (name === "lot") renderLot(arg && S.byBbl.has(arg) ? arg : S.bbl || S.lots[0].bbl);
  if (name === "queue") renderQueue();
}
const openLot = (bbl) => (location.hash = `#lot/${bbl}`);

// ---- Map -------------------------------------------------------------------------------------

function color(score) {
  return score >= 99 ? "#e76a6e" : score >= 95 ? "#ec9a3c" : score >= 80 ? "#fbd065" : "#5f6b7c";
}

function mapFilter() {
  const min = Number($("#min-score").value);
  const risks = $$("input[name=map-risk]:checked").map((c) => Number(c.value));
  $("#min-score-v").textContent = min;
  const n = S.lots.filter((l) => l.score >= min && risks.includes(l.risk) && l.lat).length;
  $("#map-count").textContent = `${n.toLocaleString("en-US")} lots shown. Click a lot to open it.`;
  return ["all", [">=", ["get", "s"], min], ["in", ["get", "r"], ["literal", risks]]];
}

function renderMap() {
  $("#map-caption").textContent =
    `${S.lots.length.toLocaleString("en-US")} Manhattan lots scored as of ${S.meta.as_of}. ` +
    "Score = percentile of the lot's worse risk.";
  if (S.map) { S.map.resize(); return; }
  const features = S.lots.filter((l) => l.lat).map((l) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [l.lon, l.lat] },
    properties: { b: l.bbl, s: l.score, r: l.risk, c: color(l.score) } }));
  const map = new maplibregl.Map({
    container: "map", style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    center: [-73.965, 40.785], zoom: 11.4, attributionControl: { compact: true } });
  S.map = map;
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
  map.on("load", () => {
    map.addSource("lots", { type: "geojson", data: { type: "FeatureCollection", features } });
    map.addLayer({ id: "lots", type: "circle", source: "lots", filter: mapFilter(), paint: {
      "circle-color": ["get", "c"],
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 13, 5, 16, 10],
      "circle-opacity": 0.9, "circle-stroke-color": "#000", "circle-stroke-width": 0.5 },
      layout: { "circle-sort-key": ["get", "s"] } });
    map.on("mouseenter", "lots", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "lots", () => { map.getCanvas().style.cursor = ""; popup.remove(); });
    map.on("mousemove", "lots", (e) => {
      const l = S.byBbl.get(e.features[0].properties.b);
      popup.setLngLat(e.lngLat).setHTML(
        `<b>${esc(l.address || l.bbl)}</b><br>#${l.rank} · score ${l.score.toFixed(1)}<br>` +
        `${RISK_LABELS[l.risk]}<br><span class="faint">${esc(l.drivers.join("; "))}</span>`).addTo(map);
    });
    map.on("click", "lots", (e) => pickLot(e.features[0].properties.b));
  });
  const refilter = () => map.getLayer("lots") && map.setFilter("lots", mapFilter());
  $("#min-score").addEventListener("input", refilter);
  $$("input[name=map-risk]").forEach((c) => c.addEventListener("change", refilter));
  mapFilter();
}

function pickLot(bbl) {
  const l = S.byBbl.get(bbl);
  const el = $("#picked");
  el.hidden = false;
  el.innerHTML = `<b>${esc(l.address || l.bbl)}</b>
    <span class="faint">#${l.rank} · score ${l.score.toFixed(1)}</span><br>
    <span class="risk-tag risk-${l.risk}">${RISK_LABELS[l.risk]}</span>
    <ul>${l.drivers.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
    <button class="btn primary" id="picked-open">Open building 360</button>`;
  $("#picked-open").onclick = () => openLot(bbl);
}

// ---- Building 360 ----------------------------------------------------------------------------

function setupSearch() {
  const input = $("#lot-search");
  const list = $("#lot-results");
  let matches = [];
  let active = 0;
  const draw = () => {
    list.hidden = !matches.length;
    list.innerHTML = matches.map((l, i) => `<li data-bbl="${l.bbl}" class="${i === active ? "on" : ""}">
      <span>${esc(l.address || "?")}</span><small>#${l.rank} · ${l.bbl}</small></li>`).join("");
  };
  input.addEventListener("input", () => {
    const q = input.value.trim().toUpperCase();
    matches = q.length < 2 ? [] : S.lots.filter((l) =>
      l.bbl.startsWith(q) || (l.address && l.address.includes(q))).slice(0, 10);
    active = 0;
    draw();
  });
  input.addEventListener("keydown", (e) => {
    if (!matches.length) return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, matches.length - 1); draw(); e.preventDefault(); }
    if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); draw(); e.preventDefault(); }
    if (e.key === "Enter") { choose(matches[active].bbl); }
    if (e.key === "Escape") { matches = []; draw(); }
  });
  const choose = (bbl) => { matches = []; draw(); input.value = ""; openLot(bbl); };
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li");
    if (li) choose(li.dataset.bbl);
  });
  input.addEventListener("blur", () => setTimeout(() => { matches = []; draw(); }, 150));
}

function timeline(events) {
  // Monthly bars from 2020-01 to the as-of month, stacked by kind
  const end = new Date(S.meta.as_of);
  const months = [];
  for (let y = 2020, m = 0; y < end.getUTCFullYear() || (y === end.getUTCFullYear() && m <= end.getUTCMonth()); m++) {
    if (m === 12) { m = 0; y++; }
    months.push(`${y}-${String(m + 1).padStart(2, "0")}`);
  }
  const idx = new Map(months.map((m, i) => [m, i]));
  const counts = months.map(() => [0, 0, 0, 0]);
  for (const e of events) {
    const i = e[0] ? idx.get(e[0].slice(0, 7)) : undefined;
    if (i !== undefined) counts[i][e[1]]++;
  }
  const max = Math.max(1, ...counts.map((c) => c[0] + c[1] + c[2] + c[3]));
  const W = 760, H = 200, L = 34, B = 22, T = 8, bw = (W - L) / months.length;
  let bars = "";
  counts.forEach((c, i) => {
    let y = H - B;
    c.forEach((n, k) => {
      if (!n) return;
      const h = ((H - B - T) * n) / max;
      y -= h;
      bars += `<rect x="${(L + i * bw + 0.5).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}"
        height="${h.toFixed(1)}" class="k${k}"><title>${months[i]} · ${KIND_LABELS[k]}: ${n}</title></rect>`;
    });
  });
  let axis = "";
  months.forEach((m, i) => {
    if (m.endsWith("-01")) axis += `<text x="${L + i * bw}" y="${H - 6}">${m.slice(0, 4)}</text>
      <line x1="${L + i * bw}" x2="${L + i * bw}" y1="${T}" y2="${H - B}" />`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Events per month since 2020">
    <style>text{fill:#738091;font:11px 'IBM Plex Mono',monospace} line{stroke:#2f343c}
      .k0{fill:#4c90f0}.k1{fill:#ec9a3c}.k2{fill:#8f99a8}.k3{fill:#e76a6e}</style>
    ${axis}<line x1="${L}" x2="${W}" y1="${H - B}" y2="${H - B}" />
    <text x="0" y="${T + 10}">${max}</text><text x="0" y="${H - B}">0</text>${bars}</svg>`;
}

function eventRows(events) {
  const kinds = $$("input[name=ev-kind]:checked").map((c) => Number(c.value));
  const openOnly = $("#ev-open")?.checked;
  const rows = events.filter((e) => (!kinds.length || kinds.includes(e[1])) && (!openOnly || e[4] === true));
  $("#ev-count").textContent = `${rows.length.toLocaleString("en-US")} events`;
  $("#ev-table").innerHTML = `<thead><tr><th>Date</th><th>Type</th><th>Class / status</th><th class="num">Severity</th>
    <th>Open</th><th>Description</th><th>BIN</th></tr></thead><tbody>` +
    rows.slice(0, S.evShown).map((e) => `<tr><td class="nowrap">${show(e[0])}</td><td class="nowrap">${KIND_LABELS[e[1]]}</td>
      <td>${esc(show(e[2]))}</td><td class="num">${show(e[3])}</td><td>${e[4] === true ? "yes" : e[4] === false ? "no" : ""}</td>
      <td class="desc" dir="auto">${esc(e[5] || "")}</td><td class="nowrap">${show(e[6])}</td></tr>`).join("") + "</tbody>";
  const more = $("#ev-more");
  more.hidden = rows.length <= S.evShown;
  more.textContent = `Show more (${(rows.length - S.evShown).toLocaleString("en-US")} left)`;
}

function linkedTable(lots, bbl) {
  const rows = lots.filter((l) => l.bbl !== bbl).sort((a, b) => a.rank - b.rank);
  if (!rows.length) return "";
  return `<div class="table-wrap"><table class="grid"><thead><tr><th>Address</th><th class="num">Units</th>
    <th class="num">Score</th><th class="num">#</th><th>Main risk</th></tr></thead><tbody>` +
    rows.map((l) => `<tr class="click" data-bbl="${l.bbl}"><td>${esc(l.address || l.bbl)}</td><td class="num">${fmt(l.units)}</td>
      <td class="num">${l.score.toFixed(1)}</td><td class="num">${l.rank}</td><td>${RISK_LABELS[l.risk]}</td></tr>`).join("") +
    "</tbody></table></div>";
}

async function renderLot(bbl) {
  S.bbl = bbl;
  if (location.hash !== `#lot/${bbl}`) history.replaceState(null, "", `#lot/${bbl}`);
  const l = S.byBbl.get(bbl);
  const body = $("#lot-body");
  body.innerHTML = `<p class="faint">Loading ${esc(l.address || bbl)}…</p>`;
  let d;
  try { d = await block(bbl); } catch (err) { body.innerHTML = `<p class="note">Couldn't load this lot: ${esc(err.message)}</p>`; return; }
  if (S.bbl !== bbl) return;
  const [bldgclass, yearBuilt, floors, units, nBuildings, isCondo, landmark, historic, ownerType] = d.facts;
  const pf = l.portfolio && S.portfolios[l.portfolio];
  const ag = l.agent && S.agents[l.agent];
  const n = S.lots.length.toLocaleString("en-US");
  const tasks = loadTasks().filter((t) => t.bbl === bbl);
  S.evShown = EVENT_PAGE;

  body.innerHTML = `
    <h1>${esc(l.address || bbl)}</h1>
    <p class="faint">BBL ${bbl} · building class ${esc(show(bldgclass))} · scored ${S.meta.as_of}</p>
    <div class="metrics">
      <div class="metric"><span class="label">Score</span><b>${l.score.toFixed(1)}</b></div>
      <div class="metric"><span class="label">Rank of ${n}</span><b>#${l.rank.toLocaleString("en-US")}</b></div>
      <div class="metric"><span class="label">P(serious hazard, 12m)</span><b>${pct(l.ph)}</b></div>
      <div class="metric"><span class="label">P(housing distress, 12m)</span><b>${pct(l.pd)}</b></div>
    </div>
    <p><span class="risk-tag risk-${l.risk}">Main risk: ${RISK_LABELS[l.risk]}</span>
      ${l.drivers.length ? "Why: " + l.drivers.map(esc).join(" · ") : ""}</p>
    <div class="cols">
      <div>
        <h3>Event timeline</h3>
        ${d.events.length ? `<div class="chart-legend">${KIND_LABELS.map((k, i) => `<span><i class="sw k${i}"></i>${k}</span>`).join("")}</div>
        <div class="chart">${timeline(d.events)}</div>
        <div class="ev-filters">${KIND_LABELS.map((k, i) => `<label class="check"><input type="checkbox" name="ev-kind" value="${i}"> ${k}</label>`).join("")}
          <label class="check"><input type="checkbox" id="ev-open"> Open only</label><span class="faint" id="ev-count"></span></div>
        <div class="table-wrap"><table class="grid" id="ev-table"></table></div>
        <button class="btn more" id="ev-more" hidden></button>` : "<p>No recorded events since 2020.</p>"}
      </div>
      <div>
        <h3>Lot</h3>
        <dl class="facts">
          <dt>Year built</dt><dd>${show(yearBuilt)}</dd><dt>Floors</dt><dd>${show(floors)}</dd>
          <dt>Residential units</dt><dd>${show(units)}</dd><dt>Buildings</dt><dd>${show(nBuildings)}</dd>
          <dt>Condo</dt><dd>${isCondo ? "yes" : "no"}</dd><dt>Landmark</dt><dd>${esc(show(landmark))}</dd>
          <dt>Historic district</dt><dd>${esc(show(historic))}</dd>
        </dl>
        ${d.buildings.length ? `<div class="table-wrap"><table class="grid"><thead><tr><th>BIN</th><th class="num">Built</th>
          <th class="num">Height ft</th><th class="num">Footprint ft²</th><th>Status</th></tr></thead><tbody>
          ${d.buildings.map((b) => `<tr><td>${b[0]}</td><td class="num">${show(b[1])}</td><td class="num">${b[2] ? b[2].toFixed(0) : ""}</td>
            <td class="num">${b[3] ? fmt(Math.round(b[3])) : ""}</td><td>${esc(show(b[4]))}</td></tr>`).join("")}</tbody></table></div>` : ""}
        <h3>Who controls it</h3>
        <p><b>Owner of record:</b> ${esc(show(l.owner))}${ownerType ? ` (${esc(ownerType)})` : ""}</p>
        ${pf ? `<p><b>Landlord portfolio:</b> ${esc(pf[0])}: ${pf[2]} lots, ${fmt(pf[3])} units
            (linked through HPD registrations: ${pf[1].map(esc).join(", ")})</p>
            ${linkedTable(S.lots.filter((x) => x.portfolio === l.portfolio), bbl)}`
          : "<p><b>Landlord portfolio:</b> not HPD-registered</p>"}
        ${ag ? `<p><b>Managing agent:</b> ${esc(ag[0])} (${ag[1]} lots in Manhattan)</p>` : ""}
      </div>
    </div>
    <h3>Inspection tasks</h3>
    ${tasks.length ? taskTable(tasks, ["task_id", "status", "priority", "assigned_to", "outcome", "notes", "created_at"]) : ""}
    <form class="card" id="lot-task">
      <label><span class="label">Reason</span><input type="text" name="reason" value="${esc(l.drivers.join("; "))}"></label>
      <label><span class="label">Priority</span><select name="priority">${[1, 2, 3].map((p) =>
        `<option value="${p}" ${p === priorityFor(l.rank) ? "selected" : ""}>${PRIORITY[p]}</option>`).join("")}</select></label>
      <div><button class="btn primary" type="submit">Create inspection task</button></div>
    </form>`;

  if (d.events.length) {
    eventRows(d.events);
    $$("input[name=ev-kind], #ev-open").forEach((c) => c.addEventListener("change", () => { S.evShown = EVENT_PAGE; eventRows(d.events); }));
    $("#ev-more").onclick = () => { S.evShown += EVENT_PAGE * 2; eventRows(d.events); };
  }
  body.querySelectorAll("tr.click").forEach((tr) => (tr.onclick = () => openLot(tr.dataset.bbl)));
  $("#lot-task").onsubmit = (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const id = createTask(bbl, f.get("reason"), Number(f.get("priority")), d.buildings.length === 1 ? d.buildings[0][0] : null);
    toast(`Created ${id}`);
    renderLot(bbl);
  };
  window.scrollTo(0, 0);
}

// ---- Inspector queue -------------------------------------------------------------------------

const TASK_COLS = { task_id: "Task", address: "Address", priority: "Priority", status: "Status", assigned_to: "Inspector",
                    outcome: "Outcome", reason: "Reason", notes: "Notes", created_at: "Created", updated_at: "Updated" };

function taskTable(tasks, cols) {
  const cell = (t, c) => {
    if (c === "address") return esc(S.byBbl.get(t.bbl)?.address || t.bbl);
    if (c === "outcome") return esc(OUTCOMES[t.outcome] || "");
    if (c.endsWith("_at")) return when(t[c]);
    return esc(t[c] ?? "");
  };
  return `<div class="table-wrap"><table class="grid"><thead><tr>${cols.map((c) => `<th>${TASK_COLS[c]}</th>`).join("")}</tr></thead>
    <tbody>${tasks.map((t) => `<tr class="click" data-bbl="${t.bbl}">${cols.map((c) =>
      `<td class="${["reason", "notes"].includes(c) ? "desc" : "nowrap"}" dir="auto">${cell(t, c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function queueRows() {
  const risks = $$("input[name=q-risk]:checked").map((c) => Number(c.value));
  const top = Number($("#q-top").value);
  const hide = $("#q-hide").checked;
  const tasked = new Set(loadTasks().filter((t) => OPEN_STATUSES.includes(t.status)).map((t) => t.bbl));
  const out = [];
  for (const l of S.lots) {
    if (!risks.includes(l.risk) || (hide && tasked.has(l.bbl))) continue;
    out.push(l);
    if (out.length >= top) break;
  }
  return out;
}

function drawQueue() {
  const rows = queueRows();
  const visible = new Set(rows.map((l) => l.bbl));
  S.qSel.forEach((b) => !visible.has(b) && S.qSel.delete(b));
  $("#q-table").innerHTML = `<thead><tr><th><input type="checkbox" id="q-all" aria-label="Select all"></th><th class="num">#</th>
    <th>Address</th><th class="num">Score</th><th>Main risk</th><th>Drivers</th><th class="num">Units</th><th>Owner</th></tr></thead><tbody>` +
    rows.map((l) => `<tr data-bbl="${l.bbl}" class="${S.qSel.has(l.bbl) ? "sel" : ""}">
      <td><input type="checkbox" ${S.qSel.has(l.bbl) ? "checked" : ""} aria-label="Select ${esc(l.address)}"></td>
      <td class="num">${l.rank}</td><td class="nowrap"><a href="#lot/${l.bbl}">${esc(l.address || l.bbl)}</a></td>
      <td class="num">${l.score.toFixed(1)}</td><td class="nowrap">${RISK_LABELS[l.risk]}</td>
      <td class="desc">${esc(l.drivers.join("; "))}</td><td class="num">${fmt(l.units)}</td><td>${esc(show(l.owner))}</td></tr>`).join("") + "</tbody>";
  $("#q-all").checked = rows.length > 0 && rows.every((l) => S.qSel.has(l.bbl));
  syncQueueButtons();
}

function syncQueueButtons() {
  const n = S.qSel.size;
  $("#q-create").textContent = `Create ${n} task(s)`;
  $("#q-create").disabled = n === 0;
  $("#q-open").hidden = n !== 1;
}

function drawTasks() {
  const all = loadTasks();
  const open = all.filter((t) => OPEN_STATUSES.includes(t.status));
  const done = all.filter((t) => !OPEN_STATUSES.includes(t.status));
  const found = done.filter((t) => t.outcome === "hazard_found").length;
  $("#t-kpis").innerHTML = [["Open", open.length], ["Closed", done.length], ["Hazard found", found],
    ["Hit rate", done.length ? pct(found / done.length) : "-"]]
    .map(([k, v]) => `<div class="metric"><span class="label">${k}</span><b>${v}</b></div>`).join("");
  $$(".tab")[0].textContent = `Open (${open.length})`;
  $$(".tab")[1].textContent = `Closed (${done.length})`;

  $("#t-open").innerHTML = !open.length ? "<p>No open tasks. Select lots in the queue above to create some.</p>" :
    taskTable(open, ["task_id", "address", "priority", "status", "assigned_to", "reason", "created_at"]) + `
    <form class="card" id="t-action">
      <label><span class="label">Task</span><select name="task">${open.map((t) =>
        `<option value="${t.task_id}">${t.task_id} · ${esc(S.byBbl.get(t.bbl)?.address || t.bbl)}</option>`).join("")}</select></label>
      <div class="radios">${["Assign inspector", "Record outcome", "Flag false positive"].map((a, i) =>
        `<label class="check"><input type="radio" name="action" value="${a}" ${i ? "" : "checked"}> ${a}</label>`).join("")}</div>
      <label><span class="label">Inspector (for Assign)</span><input type="text" name="inspector"></label>
      <label><span class="label">Outcome (for Record outcome)</span><select name="outcome">${Object.entries(OUTCOMES).map(([k, v]) =>
        `<option value="${k}">${v}</option>`).join("")}</select></label>
      <label><span class="label">Notes</span><textarea name="notes" rows="2"></textarea></label>
      <div><button class="btn primary" type="submit">Apply</button></div>
    </form>`;
  $("#t-done").innerHTML = !done.length ? "<p>No closed tasks yet.</p>" :
    taskTable(done, ["task_id", "address", "status", "outcome", "assigned_to", "notes", "updated_at"]);
  $$("#t-open tr.click, #t-done tr.click").forEach((tr) => (tr.ondblclick = () => openLot(tr.dataset.bbl)));

  const form = $("#t-action");
  if (form) form.onsubmit = (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const id = f.get("task");
    const action = f.get("action");
    const notes = f.get("notes").trim();
    if (action === "Assign inspector") {
      const who = f.get("inspector").trim();
      if (!who) { toast("Enter the inspector's name"); return; }
      updateTask(id, { assigned_to: who, status: "assigned" });
    } else if (action === "Record outcome") {
      updateTask(id, { outcome: f.get("outcome"), notes, status: "closed" });
    } else {
      updateTask(id, { outcome: "no_issue", notes, status: "false_positive" });
    }
    toast(`${action}: ${id}`);
    drawQueue();
    drawTasks();
  };
}

function renderQueue() {
  const p = S.meta.p_at_100;
  $("#queue-caption").textContent = `In the 2024 backtest, of the top 100 lots: ${pct(p.serious_hazard)} had a serious hazard, ` +
    `${pct(p.housing_distress)} had 10+ new class C violations, within 12 months.`;
  drawQueue();
  drawTasks();
}

function setupQueue() {
  $$("input[name=q-risk], #q-top, #q-hide").forEach((c) => c.addEventListener("change", drawQueue));
  $("#q-table").addEventListener("change", (e) => {
    if (e.target.id === "q-all") {
      queueRows().forEach((l) => (e.target.checked ? S.qSel.add(l.bbl) : S.qSel.delete(l.bbl)));
      drawQueue();
      return;
    }
    const tr = e.target.closest("tr[data-bbl]");
    if (!tr) return;
    e.target.checked ? S.qSel.add(tr.dataset.bbl) : S.qSel.delete(tr.dataset.bbl);
    tr.classList.toggle("sel", e.target.checked);
    syncQueueButtons();
  });
  $("#q-create").onclick = () => {
    const picked = [...S.qSel].map((b) => S.byBbl.get(b));
    picked.forEach((l) => createTask(l.bbl, l.drivers.join("; "), priorityFor(l.rank), null));
    S.qSel.clear();
    toast(`Created ${picked.length} task(s)`);
    drawQueue();
    drawTasks();
  };
  $("#q-open").onclick = () => openLot([...S.qSel][0]);
  $$(".tab").forEach((b) => (b.onclick = () => {
    $$(".tab").forEach((x) => x.classList.toggle("on", x === b));
    $("#t-open").hidden = b.dataset.tab !== "open";
    $("#t-done").hidden = b.dataset.tab !== "done";
  }));
}

// ---- Start -----------------------------------------------------------------------------------

async function start() {
  try {
    const [meta, data] = await Promise.all([getJson("data/meta.json.gz"), getJson("data/lots.json.gz")]);
    S.meta = meta;
    const c = Object.fromEntries(data.cols.map((k, i) => [k, i]));
    S.lots = data.rows.map((r) => ({ bbl: r[c.bbl], rank: r[c.rank], score: r[c.score], risk: r[c.risk], drivers: r[c.drivers],
      ph: r[c.ph], pd: r[c.pd], address: r[c.address], lat: r[c.lat], lon: r[c.lon], units: r[c.units], owner: r[c.owner],
      portfolio: r[c.portfolio], agent: r[c.agent] }));
    S.byBbl = new Map(S.lots.map((l) => [l.bbl, l]));
    S.portfolios = data.portfolios;
    S.agents = data.agents;
  } catch (err) {
    $("#loading").innerHTML = `<p class="note">Couldn't load the data: ${esc(err.message)}</p>`;
    return;
  }
  $("#loading").hidden = true;
  setupSearch();
  setupQueue();
  window.addEventListener("hashchange", route);
  route();
}

start();
