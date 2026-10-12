# UIDT Change Request — Projects + Multi-Infrastructure Editing

## 0. How to use this document
You are being handed a working full-stack app (React/Leaflet frontend + FastAPI/Python
backend + SUMO/OSM-based simulation pipeline). This document describes a set of changes
to make to it. Read the "Current implementation" subsection under each change first —
it is a factual description of what exists today, verified against the actual source

files — then implement the "Required changes." Do not redesign parts of the app that
aren't mentioned here. If something is ambiguous, make the most reasonable engineering
choice, state the assumption in your response, and keep it easy to revise later — don't
block on it.

More change requests will follow this one in later prompts, building on top of what you
implement here, so favor clean, extensible data models over quick hacks (e.g. don't
hardcode "exactly one project" or "exactly one infrastructure" anywhere new you write).

---

## 1. App overview (for context)

**UIDT** ("Urban Infrastructure Digital Twin") is a tool where a user picks a start and
end point on a map around a corridor in Kathriguppe, Bangalore, draws a proposed piece
of infrastructure (a new road, flyover, or tunnel) connecting/near them, and the backend:
1. checks feasibility (building collisions etc.) via a phase3 "infra engine",
2. injects the proposed geometry into the OSM-derived road graph,
3. runs a SUMO microsimulation comparing before/after traffic at several adoption rates,
4. returns a report with metrics, a route comparison, and a congestion heatmap.

Frontend: `frontend/src/App.jsx` (shell + screen switcher) and
`frontend/src/components/MapCommandCenter.jsx` (the actual Leaflet map + 3-step wizard:
1 = pick route, 2 = define intervention, 3 = simulate/results). Uses `react-leaflet` +
`react-leaflet-draw` for drawing, `localStorage` only for theme and last report — **there
is currently no backend persistence at all** (no database, no saved-project concept).

Backend: `backend/api/server.py` (FastAPI app, Pydantic request models, `/api/analyze`
endpoint), `backend/api/adapters.py` (`wizard_to_phase34_payload` — converts the
frontend's wizard JSON into the shape the simulation pipeline expects),
`backend/infrastructure_feasibility/phase3_infra/traffic-infra-engine/phase34_pipeline.py`
(`Phase34Pipeline.run_from_dict` — does the actual graph modification + feasibility
check for **one** proposal dict), `backend/api/simulation_service.py` (SUMO batch runs).

---

## 2. Change 1 — Project creation & naming

### Current implementation
There is no notion of a "project" anywhere in the codebase. The app is a single
stateless wizard: open it, pick a route, define one intervention, run one analysis. The
only client-side persistence is `localStorage['reportData']` (the last generated report)
and `localStorage['uidt-theme']`. The backend has no database, no ORM, and no
`project_id` concept in any request/response model (`server.py`, `adapters.py`).

### Required changes
Add a real "project" concept that a user creates and names, and that persists their
work across sessions.

**Backend**
- Add a lightweight persistence layer (SQLite is fine — this is a single-user/local-first
  tool today, don't over-engineer with a heavier DB unless one is already configured).
  Use SQLAlchemy or a similarly simple ORM/query layer, consistent with the existing
  FastAPI + Pydantic style in `server.py`.
- New `Project` entity: `id`, `name`, `created_at`, `updated_at`, plus whatever fields
  are needed to hold the project's map state (see Change 2 — a project owns a list of
  infrastructure proposals, plus the route/scenario settings currently in
  `AnalyzeRequest`).
- New endpoints (naming can follow FastAPI conventions already used in `server.py`):
  - `POST /api/projects` — create a project, body includes at least `name`.
  - `GET /api/projects` — list projects (id, name, updated_at — enough for a picker UI).
  - `GET /api/projects/{id}` — full project state.
  - `PATCH /api/projects/{id}` — rename / update project state (used as the project is
    edited — see Change 2).
  - `DELETE /api/projects/{id}`.
  - Existing `/api/analyze` should be scoped to a project — either
    `POST /api/projects/{id}/analyze`, or accept `project_id` in the existing request
    body. Pick whichever fits the existing router style better, but the analysis result
    should still be saved back onto the project (so re-opening a project shows its last
    result), not just returned and forgotten like today.

**Frontend**
- Add a "Projects" landing view: list of existing projects (name + last-updated),
  a "New project" action that prompts for a name and creates it, and clicking a project
  opens it in the Command Center (`MapCommandCenter.jsx`).
- `App.jsx` currently switches between `'map'` and `'report'` screens with a local
  `screen` state and no routing. Extend this (a `'projects'` screen, or introduce real
  client-side routing with a `/projects/:id` pattern — your call) so a project's ID is
  part of the app's navigation state, and `MapCommandCenter` knows which project it's
  currently editing.
- Show the active project's name somewhere persistent in the header/nav area next to
  the existing `UIDT` / "Urban Infrastructure Digital Twin" branding in `App.jsx`, with
  a way to rename it and a way to go back to the projects list.
- Replace the current `localStorage['reportData']`-only persistence with loading/saving
  against the project on the backend. Keep using `localStorage` only for pure UI prefs
  (theme) — actual project data should live server-side now.

### Acceptance criteria
- A user can create a named project, leave, come back, and see it listed with their
  prior work still there.
- Multiple projects can exist independently with no cross-contamination.

---

## 3. Change 2 — Multiple infrastructure proposals per project (side panel)

### Motivation (as given)
A single road/infra change often requires accompanying changes to surrounding
infrastructure to make sense (e.g. adding a new road also needs a widening on the road
it connects to, or a block on a redundant one). The tool currently forces one
infrastructure edit per analysis; it needs to support several within one project.

### Current implementation (verified in code)
The entire stack is hardwired to exactly one intervention:
- **Drawing**: `MapCommandCenter.jsx` keeps a single `drawnFeature` state
  (`useState(null)`) and a single Leaflet `FeatureGroup` ref (`drawnGroupRef`). Its
  `onCreated` handler explicitly clears any previous drawing before adding the new one:
  ```js
  const onCreated = (e) => {
    const layer = e.layer;
    // Keep exactly one proposal line...
    drawnGroupRef.current?.clearLayers?.();
    drawnGroupRef.current?.addLayer?.(layer);
    ...
  }
  ```
- **Infra attributes**: only `infraType` (`useState('flyover')`, one of
  `'road' | 'flyover' | 'tunnel'`, chosen via three buttons around line 1591-1593) and a
  hardcoded `road_width_m: 8` and `is_oneway: false` are sent — there is no per-drawing
  name, no serial number, no side-panel list, no adoption-rate-per-infra, and no
  direction field beyond the unused `is_oneway` flag.
- **Payload shape**: `handleGenerate()` builds a single `intervention: {...}` object
  inside the POST body to `/api/analyze`.
- **Backend model** (`server.py`): `Intervention` is a single Pydantic object
  (`infra_type`, `geometry`, `road_width_m`, `is_oneway`), and `AnalyzeRequest.intervention`
  is one `Intervention`, not a list.
- **Adapter** (`adapters.py`): `wizard_to_phase34_payload` reads `request["intervention"]`
  (singular) and builds one `proposal` dict.
- **Pipeline** (`phase34_pipeline.py`): `Phase34Pipeline.run_from_dict(payload)` takes
  one `payload["proposal"]` dict, builds one geometry, and calls the graph-modification
  engine once, storing the result in `self.modified_graph`.
- **Adoption rate**: currently a *scenario-level*, not infra-level, concept —
  `Scenario.adoption_rates_pct` (default `[25, 50, 75]`) is a fixed list applied to
  whatever the single intervention is, used for the SUMO scenario comparison shown in
  the results panel (`analysisMetrics.simulation_scenarios`).

### Required changes

**Data model — one infrastructure item**
Each drawn line becomes a distinct infrastructure-change record with:
- `id` (stable client/server identifier)
- `serial_number` (1, 2, 3… in creation order, shown in the side panel; don't reuse
  numbers after a delete — or do, your call, but be consistent and say which)
- `name` (auto-generated default like `Infra 1`, user-editable free text)
- `infra_type`: one of `road`, `tunnel`, `widening`, `road_block`, **plus the existing
  `flyover`** — confirmed: `flyover` stays as a 5th type, it is not folded into the
  other four.
- `road_width_m` (numeric, user-editable — currently hardcoded to `8`)
- `direction`: confirmed to just be the existing `is_oneway` boolean, exposed in the UI
  (currently sent but never actually surfaced as a control) — a simple
  one-way / two-way toggle per infra item, not a forward/reverse/heading concept. Keep
  the field name `is_oneway` (or `direction: 'oneway' | 'twoway'` if you prefer an enum
  — your call, just be consistent across frontend/backend).
- `adoption_rate_pct`: confirmed — the existing scenario-level `25/50/75` sweep stays
  as-is (still runs, still shown in the multi-scenario comparison), **and** each
  infra item additionally gets its own `adoption_rate_pct` the user can type/choose.
  On top of that, add the ability to adjust/re-run the simulation for different
  **combinations** of the drawn infra items when several exist together — see "Infra
  combination toggle" under Required changes below; this is a new, separate piece of
  functionality, not just a per-item field.
- `geometry` (the drawn GeoJSON LineString, as today — see Change 3 for a
  coordinate-typing alternative to freehand drawing).

Some fields may not make sense for every `infra_type` (e.g. a `road_block` likely
doesn't need a `road_width_m` or `direction`) — disable/hide irrelevant fields in the UI
per type rather than forcing every field on every item.

**Frontend**
- Change `drawnFeature` (singular) into a list, e.g. `infraItems: []`, one entry per
  drawn line as defined above.
- Change the draw handler so each new polyline drawn via `EditControl` is **appended**
  to `infraItems` (with an auto-generated name/serial) instead of clearing the map and
  replacing the one existing drawing. The map should be able to show multiple drawn
  lines simultaneously, each visually distinguishable (e.g. by color/label), similar to
  how existing `ROUTE_STYLES` differentiate Route 1-4.
- Add a side panel (in step 2 "INTERVENE" of the existing wizard, where the current
  Road/Flyover/Tunnel buttons live around line ~1580-1640) listing every item in
  `infraItems`: serial number + name (name editable inline), and per-item controls for
  `infra_type` (dropdown/segmented control), `road_width_m` (number input),
  `direction` (select), `adoption_rate_pct` (number input or slider). Include a way to
  select an item (highlights its line on the map) and delete an item (removes it from
  both the list and the map).
- Support editing an existing item's geometry (re-drawing/dragging it) via
  `react-leaflet-draw`'s edit mode, keyed to the right `infraItems` entry — don't only
  support edit-via-delete-and-redraw unless that's clearly simpler and you say so.
- Update `handleGenerate()`'s payload construction: instead of a single `intervention`
  object, send `interventions: [...]` (array of the infra items above, in the shape the
  backend expects — see below).
- This project-level infra list is part of what gets persisted via the `Project`
  endpoints from Change 1 — i.e. reopening a project should restore all previously
  drawn infra items, not just the last report.
- **Infra combination toggle**: when a project has 2+ infra items drawn, give the user
  a way to select/deselect which of them are "active" for a given analysis run (e.g. a
  checkbox per row in the side panel). This lets someone drawn 4 changes but run the
  simulation on just 2 of them at a time, compare combinations, then flip others on —
  rather than always simulating every drawn item together. The last-run combination
  (which items were active) should be stored alongside that run's result so past
  results stay interpretable.

**Backend**
- `server.py`: change `AnalyzeRequest.intervention: Intervention` to
  `AnalyzeRequest.interventions: list[Intervention]`, and extend the `Intervention`
  model with `id`, `name`, `serial_number`, `adoption_rate_pct` per the data model above
  (keep `infra_type`, `geometry`, `road_width_m`, `is_oneway`). `interventions` sent in
  a given `/analyze` call represents only the currently-active/toggled-on subset from
  the project's full infra list — the frontend filters before sending, the backend just
  applies whatever list it receives.
- `adapters.py`: change `wizard_to_phase34_payload` to build a **list** of `proposal`
  dicts (one per intervention) instead of a single `proposal` dict, or otherwise adapt
  its output shape so the pipeline (next bullet) can consume multiple proposals. Keep
  the existing single-proposal shape per item so the pipeline changes stay minimal.
- `phase34_pipeline.py`: `Phase34Pipeline.run_from_dict` currently mutates
  `self.modified_graph` once, for one proposal. Change it (or add a new method) to
  accept a **list** of proposals and apply them to the graph sequentially, so the
  resulting `modified_graph` reflects all of them combined. Watch for two things
  explicitly: (1) proposals that spatially/topologically interact (e.g. a `widening` on
  a road that a later `road` proposal connects to) need to be applied in a sensible
  order — probably the order the user drew them in, i.e. `serial_number` order — and
  (2) the feasibility/collision check and the "modified roads" GeoJSON export
  (`_write_modified_roads_geojson`) should now report per-item results (which of the N
  proposals passed/failed feasibility), not just one pass/fail for the whole batch.
- `simulation_service.py` (`run_corridor_intervention_simulation_batch`) and anything
  in `server.py`'s `/analyze` handler that assumes a single intervention (e.g. the
  `is_infrastructure_intervention` check and everything downstream of it) needs to
  handle the list. The SUMO comparison ("before" vs "after") should reflect the graph
  with **all** proposed changes applied together, since that's the point of allowing
  several — the frontend results panel (`analysisMetrics`, `simulation_scenarios`) can
  stay structured similarly, but should make clear it's evaluating the combined effect
  of the whole set of changes, and ideally still break out attribution per infra item
  where feasible (e.g. per-item feasibility pass/fail) even if the traffic simulation
  itself is necessarily evaluated jointly.

### Acceptance criteria
- Within one project, a user can draw 2+ separate infrastructure lines in a single
  session, each showing up as its own row in the side panel with a serial number and
  editable name.
- Each row lets the user set adoption rate, road width, direction (one-way/two-way),
  and infra type (road / tunnel / widening / road block / flyover) independently.
- The user can toggle individual infra items on/off and re-run the analysis against
  just the active subset, in addition to running all of them together.
- Running the analysis sends the active set together and the backend applies all of
  them to the same road graph before simulating, rather than only ever looking at one.
- Reopening the project later shows all previously drawn infra items, not just one.

---

## 4. Change 3 — Type coordinates instead of only drawing

### Current implementation
Every point/geometry the app needs is currently only obtainable by interacting with the
map — there is no numeric text-entry path anywhere:
- **Route start/end**: `startLoc`/`endLoc` are only set via `CorridorPointPicker`
  (clicking the map, `handleMapPick`) or a place-name text search that geocodes through
  Nominatim (`searchQuery` → `https://nominatim.openstreetmap.org/search...`). There is
  no field to type a raw `lat, lon` pair directly.
- **Infra geometry**: as covered in Change 2, the only way to produce a `geometry` for
  an infra item today is drawing a polyline on the map with `react-leaflet-draw`
  (`EditControl` → `onCreated`). There's no way to type/paste a list of coordinates.

### Required changes
Add a coordinate text-entry alternative alongside (not replacing) map-drawing, in two
places:

1. **Route start/destination** (step 1 of the wizard): let the user choose between
   "Select on map" (today's behavior) and "Enter coordinates" (new — two numeric
   inputs, latitude and longitude, per point), for both the start point and the
   destination. Whichever method is used, it should populate the same `startLoc`/
   `endLoc` state so the rest of the flow (steps 2-3) doesn't need to know which
   method was used.
2. **Infra item geometry** (Change 2's side panel, per infra item — "Add infra/change
   N"): let the user type a coordinate list instead of/in addition to drawing —
   a start point, one or more mid-point waypoints, and a last point (i.e. an ordered
   list of `lat, lon` pairs, `[start, mid_1, mid_2, ..., last]`), which builds the same
   `LineString` geometry that drawing on the map produces today. A reasonable UI is a
   small repeatable "waypoint" list with add/remove-row controls per infra item, though
   a single multi-line paste-a-list-of-coordinates textarea is also acceptable — your
   call, but it must support an arbitrary number of intermediate points, not just
   start+end.
   - Whichever entry method is used for a given infra item, the map should still render
     the resulting line (so typed coordinates are visually confirmed, not a blind
     text-only input), and it should remain editable — e.g. typing new coordinates
     updates the drawn line, and dragging the drawn line (if you kept map-editing from
     Change 2) should be reflected back if you show the coordinates as editable text too.
     Keeping both perfectly in sync bidirectionally is a nice-to-have, not a hard
     requirement — at minimum, typed coordinates must produce a correct, usable geometry.

### Acceptance criteria
- A user can set the route's start and destination purely by typing latitude/longitude,
  with no map click required.
- A user can define an infra item's full line (start, any number of mid-points, end)
  purely by typing coordinates, with no freehand drawing required, and see it rendered
  on the map.

---

## 5. Design reference — target UI flow (from provided mockups)

Two reference mockup sets were provided showing the intended direction for the app's
overall flow and visual design (dark hero/light panel login, a "Project Hub" landing
page, a multi-step "Create New Project" wizard, and a results dashboard). These are
**directional UI/UX reference for this and future change requests, not a spec to fully
implement in this pass** — only the pieces that overlap with Changes 1-3 above are in
scope right now. Treat the rest as context for what's coming next so you don't build
something that will need to be thrown away.

Screens shown (second, more detailed mockup set):
1. **Login / Sign up** — split layout, dark hero panel ("Smarter Cities. Better
   Decisions.") on the left with email/password form on the right ("Welcome back").
   *Not yet requested as a functional requirement — flag back whether real
   authentication is in scope, or if this is purely a visual/landing treatment; don't
   build an auth backend speculatively.*
2. **Project Hub** — landing page after login: greets the user by name, two big
   entry tiles ("Create New Project" / "View / Modify Existing Project"), and a "Recent
   Projects" row showing project thumbnails with name, last-modified, and a status
   label (e.g. "In Progress" / "Completed" / "Draft"). A left sidebar nav appears in
   this and all subsequent screens: **Home, Projects, Map, Data, Settings**. *This maps
   directly onto Change 1's "Projects" landing view — use it as the layout target: the
   project list should show status and thumbnails where practical, and the "create" and
   "open existing" actions should be visually distinct entry points rather than a bare
   list.*
3. **Create New Project → map (coordinate selection)** — a stepper (Select Route →
   Configure → Review) with a left panel to define Start Point / Destination, each with
   a **"Click on map" vs "Enter coordinates" choice** and a Latitude/Longitude pair of
   inputs. *This is exactly Change 3, item 1 above — implement this screen's coordinate-
   entry pattern.*
4. **Coordinate selection in progress** — visual feedback state showing which point
   (start/destination) is currently being selected, plus a running distance readout.
5. **Coordinate entry + route preview** — after both points are set, shows a small
   route summary card (start/destination coordinates, distance, estimated travel time)
   with a live preview line on the map, before continuing.
6. **Review & Configure** — a "Simulation Configuration" panel (time period e.g.
   "Morning Peak", simulation type e.g. "Traffic Flow" / "Infrastructure Change", and
   toggles for things like public transport, lane changes, real-time data) plus a
   "Project Summary" card, before a final "Create Project" action. *The simulation-type
   and time-period controls roughly correspond to the existing `scenario` fields
   (`start_time`/`end_time`/`target_hour`) already in `AnalyzeRequest` — no need to
   invent new backend concepts here, just note this as the target layout for
   configuring them once Change 1's project flow exists.*
7. **Simulation / Loading** — full-screen dark loading state with a progress bar and
   staged labels ("Loading map data" → "Running traffic simulation" → "Generating
   analytics" → "Almost there"), similar in spirit to the existing `analysisRunning`
   overlay in `MapCommandCenter.jsx` (which already has staged status messages) but as
   a more polished full-screen treatment.
8. **Results / Analytics Dashboard** — tabbed view (Overview / Traffic Flow / Speed &
   Congestion / Emissions / Map View) with headline stat cards (avg speed, total
   vehicles, congestion level, CO2 emissions, each with a before/after delta), a
   traffic-flow-over-time chart, and an embedded map. This is a redesign of the
   existing `TrafficReport.jsx` / `analysisMetrics` results panel — **out of scope for
   this change request**, listed here for future reference only.
9. **Project saved / loading** — confirmation screen ("Project Created Successfully!")
   with "View Project" / "Create Another" actions.

Nothing in this section requires backend work beyond what Changes 1-3 already specify;
it's primarily a frontend layout/IA target. Feel free to adopt the sidebar nav
(Home/Projects/Map/Data/Settings) now if it's a natural place to put the Change-1
"Projects" list and Change-2 side panel, since restructuring navigation later is more
disruptive than getting it close to right now — but don't build out the Data/Settings
sections themselves, they're not defined yet.

---

## 6. Open questions worth flagging back to the user (don't block on these — pick a
default, implement it, and just state your choice)
- Is real login/authentication in scope now (per mockup screen 1), or is that purely a
  future/visual reference? Default to **not** building auth yet — everything so far has
  been single-user/local — and just leave a clean seam (e.g. a `user_id` column you
  don't populate yet) if it's cheap to do so.
- For the infra combination toggle (Change 2): should toggling items on/off be a
  per-run choice only, or should it also support saving named combinations (e.g. "Combo
  A: items 1+2", "Combo B: items 1+3") for quick re-comparison? Default to per-run
  toggling only for now — named/saved combinations can be a later change if wanted.
- For coordinate entry on infra items (Change 3): repeatable waypoint rows vs. a
  paste-able multi-line textarea — pick whichever is faster to build well; note your
  choice.
