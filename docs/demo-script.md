# Demo video script (about 2:30)

Record with Loom, OBS or the Windows Snipping Tool (Win+Shift+R). Use a browser window at
about 1440×900, zoomed to 110%. Start with the app running and no open tasks.

**Before recording**
- Start the app: `.venv/Scripts/streamlit run app/app.py`
- Open the three pages once so they're cached (the first load takes a few seconds).
- Have reports/backtest.md open in a second tab.

---

### 1. The problem (0:00–0:20) · Risk map page

**Show:** the map at the default filter (score ≥ 98).

**Say:** "NYC publishes building data from several agencies (DOB, HPD, city planning), each
keyed differently. I linked six of these datasets, 1.2 million records for Manhattan, into one
ontology, scored every lot's risk, and built the tool an inspection planner would use to
decide where to go first."

### 2. The map (0:20–0:40)

**Do:** drag the slider down to 95, then back up to 99. Hover over two or three red dots.

**Say:** "Each dot is a tax lot. Red is the top 1%. The score is the percentile of the lot's
worse risk: either a serious hazard, meaning an unsafe facade or an emergency DOB violation,
or housing distress, meaning ten or more new immediately-hazardous HPD violations in a year.
The tooltip already says why."

### 3. Building 360 (0:40–1:15)

**Do:** click a red dot, then "Open building 360". If it's hard to hit one, pick
*151 West 140 Street (#1)* from the dropdown instead.

**Say:** "This is the building 360: one object with everything linked to it. Here are the
score and probabilities, and the drivers in plain words: an unsafe facade, 21 open DOB
violations, 35 open class C. Below is every violation, complaint and facade filing since
2020, from four agencies, all resolved to this one lot. That entity resolution was most of
the work: 95 to 99 percent of records matched."

**Do:** scroll to the Owner section.

**Say:** "And the owner, linked through the ontology to their other lots."

### 4. Taking action (1:15–1:50) · Inspector queue

**Do:** go to the Inspector queue, tick three rows, click "Create 3 task(s)". Show that they
disappear from the queue. Scroll to Tasks, pick one, choose "Record outcome" → "Hazard
found" → Apply. Point at the hit-rate number.

**Say:** "A planner works from the ranked queue: select buildings and create inspection
tasks. They're written back to the ontology and drop out of the queue. When the inspector
reports back, the outcome is recorded, and that becomes the feedback for the next version of
the model."

### 5. Does it work? (1:50–2:20) · reports/backtest.md tab

**Show:** the two results tables.

**Say:** "I tested it honestly. I scored every lot as of January 2024 using only earlier
data, trained on 2023, and checked what happened in 2024. For housing distress, 92 percent of
the top 100 had ten or more new class C violations, 25 times the base rate. My first
version, with hand-picked weights, lost to simple baselines, so I replaced it with learned
weights that are still explainable. For hazards, a naive repeat-offender rule still beats
the model in the top 100. I'd rather show that than hide it."

### 6. Close (2:20–2:30)

**Say:** "Next I'd add HPD registrations to link owners through their managing agent, and
the newer DOB complaints feed. The code and write-up are on GitHub."

---

**After recording:** trim the start and end, then upload as unlisted (YouTube or Loom).
Put the link at the top of README.md, under the summary table:

```markdown
▶️ **[2-minute demo](https://...)**
```
