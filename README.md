# UIDT — Urban Integrated Digital Twin

UIDT is a traffic-infrastructure planning tool for Bangalore. It lets a user draw a proposed intervention (a road, flyover, or tunnel) on a map, then runs it through a pipeline that checks feasibility, modifies the road-network graph, and simulates the before/after traffic impact — returning a report the user can share.

The project has two parts:

- **`frontend/`** — a React "Command Center" map wizard where interventions are drawn and results are reviewed.
- **`backend/`** — a FastAPI service that orchestrates four analysis phases and exposes them over HTTP.

## How it works

1. The user draws a route/intervention on the map in the frontend (`MapCommandCenter.jsx`) and submits it.
2. The backend (`api/server.py`) converts the request into a **Phase 3/4 payload**, then:
   - **Phase 1 — Demand**: pulls ward-level hourly traffic demand for Bangalore from a pre-processed BMTC/ward CSV (`phase1_demand/tanisha_pipeline.py`), cached to `backend/data/shared/`.
   - **Phase 2 — Optimize (heuristic preview)**: an OpenStreetMap-graph betweenness-centrality pass that flags bottleneck junctions, plus optional widening / smart-signal impact estimates (`phase2_placeholder/runner.py`). Not a full optimization engine yet.
   - **Phase 3 — Infrastructure**: downloads/caches the OSM road network around the target corridor, checks the proposed intervention for feasibility (clearances, overlaps, collisions), and produces a modified graph (`phase3_infra/traffic-infra-engine/`: `feasibility_engine/`, `graph_modification/`, `smart_suggestion_engine/`). The "smart architect" step can optionally call the Google Gemini API to suggest/refine interventions.
   - **Phase 4 — Simulation**: runs a NetworkX-based before/after traffic simulation over the original vs. modified graph, including corridor-adoption-rate sweeps (`simulation_engine/simulator.py`, `api/simulation_service.py`).
3. Results (feasibility, demand, simulation, bottlenecks, and generated Folium maps/GeoJSON) are merged into one JSON response and rendered as a report in the frontend.

The backend also supports proposing and ranking **alternative** intervention candidates (road / flyover / tunnel) near a given origin–destination pair before committing to a full simulation (`api/alternative_service.py`, `POST /alternatives`).

## Project layout

```
UIDT/
├── frontend/                     # React + Vite + Tailwind map wizard (Leaflet, Turf, Recharts)
│   └── src/
│       ├── App.jsx               # Screen router (map wizard vs. report view), theme toggle
│       └── components/
│           ├── MapCommandCenter.jsx  # Draw routes/interventions, call the API
│           └── TrafficReport.jsx     # Renders the analysis report
├── backend/
│   ├── api/
│   │   ├── server.py             # FastAPI app + routes
│   │   ├── adapters.py           # Wizard payload <-> Phase 3/4 payload conversion
│   │   ├── demand_service.py     # Phase 1 demand lookup/caching
│   │   ├── simulation_service.py # Phase 4 simulation orchestration
│   │   └── alternative_service.py# Candidate generation + ranking for /alternatives
│   ├── paths.py                  # Central data-directory config
│   ├── data/
│   │   ├── shared/                # Pipeline outputs: JSON, GeoJSON, HTML maps, impact reports
│   │   └── raw/                   # Raw input data
│   └── phases/
│       ├── phase1_demand/         # Ward-level Bangalore traffic demand (Tanisha)
│       ├── phase2_placeholder/    # Bottleneck/heuristic optimization preview
│       └── phase3_infra/
│           └── traffic-infra-engine/  # Feasibility, graph modification, smart suggestions, simulation (Swathi H)
├── auto_tool/                    # SUMO network repair + route generation scripts/files
├── files2/                       # Additional SUMO network/route/config files
├── docs/INTEGRATION.md           # Architecture notes
└── package.json                  # Root convenience scripts (dev:backend / dev:frontend)
```

## Quick start

### 1. Backend

On Linux or macOS:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional: add GEMINI_API_KEY for the Smart Architect
uvicorn api.server:app --reload --port 8000
```

On Windows (native):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env        # optional: add GEMINI_API_KEY for the Smart Architect
python -m uvicorn api.server:app --port 8000
```

To start both services together on Windows PowerShell from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_uidt.ps1
```

This uses the backend virtual environment directly and starts the frontend with `npm.cmd`.

> Make sure the SUMO executable is installed and available on `PATH` if the backend needs to invoke SUMO. If your machine blocks native SUMO execution, you may still need WSL or another Linux environment.
>
> If you see `Module 'rtree' not available. Using brute-force fallback.` from SUMO, install the Python `rtree` dependency so SUMO can use spatial indexing instead of brute force:
>
> ```powershell
> pip install rtree
> ```
>
> On conda/WSL, install the native package first:
>
> ```bash
> conda install -c conda-forge rtree
> ```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**.

### Or, from the repo root

```bash
npm run install:all     # installs backend (pip) + frontend (npm) deps
npm run dev:backend     # terminal 1
npm run dev:frontend    # terminal 2
```

> First `/analyze` call is slow (30–90s) while the OSM road graph for the corridor is downloaded and cached; subsequent calls reuse the cache in `backend/data/cache/`.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/health` | Service check |
| `GET`  | `/demand?hour=9&ward_no=163&multiplier=1.0&refresh=false` | Phase 1 ward/corridor demand |
| `POST` | `/simulate` | Phase 4 corridor simulation for a given origin/destination and demand, without an intervention |
| `POST` | `/alternatives` | Generate and rank candidate interventions (road/flyover/tunnel) near an origin/destination |
| `POST` | `/analyze` | Full pipeline: feasibility → graph modification → Phase 2 heuristics → simulation → report |
| `POST` | `/api/validation/btp` | Compare paired BTP observations with simulated speed/travel-time metrics |
| `GET`  | `/artifacts/{filename}` | Serves generated maps/reports from `backend/data/shared/` (e.g. `phase34_map.html`, `traffic_map.html`) |

### `POST /analyze` request shape (abridged)

```json
{
  "route": { "start": {"lat": 12.92, "lon": 77.55, "name": "A"}, "end": {"lat": 12.93, "lon": 77.56, "name": "B"} },
  "intervention": { "type": "infrastructure", "infra_type": "flyover", "geometry": {"type": "LineString", "coordinates": []}, "road_width_m": 8, "is_oneway": false },
  "scenario": { "start_time": "09:00", "end_time": "10:00", "ward_no": 163, "place_name": "Kathriguppe, Bangalore, India", "dist_m": 5000, "adoption_rates_pct": [25, 50, 75] },
  "options": { "widening": false, "smart_signals": false, "smart_analysis": true }
}
```

The response includes feasibility results, the demand snapshot used, Phase 2 bottleneck/heuristic output, simulation results per adoption rate, and links to generated map/report artifacts.

### BTP external validation

The Traffic Report view accepts a BTP CSV or JSON file and reports paired speed/travel-time errors without imputing missing values. Use the schema and analysis procedure in [`docs/BTP_VALIDATION_PROTOCOL.md`](docs/BTP_VALIDATION_PROTOCOL.md). The project does not claim external validation until the BTP observations are mapped to the same corridor and simulation window and the paired results are frozen as a reproducible artifact.

## Phase status

| Phase | Area | Status |
|-------|------|--------|
| 1 | Demand | ✅ Integrated — ward-level CSV pipeline + cached JSON/map |
| 2 | Optimize | 🔶 Heuristic preview — graph bottleneck centrality, widening/smart-signal estimates; full optimization engine TBD |
| 3 | Infrastructure | ✅ Integrated — feasibility checks, OSM graph modification, optional Gemini-assisted suggestions |
| 4 | Simulation | ✅ Integrated — NetworkX-based before/after corridor simulation with adoption-rate sweeps |

## Other notable pieces

- **`auto_tool/`** and **`files2/`** contain standalone SUMO (`.net.xml` / `.rou.xml` / `.sumocfg`) network and route files plus a `network_repair.py` / `route_generator.py` pair, used for SUMO-based network prep/testing outside the main FastAPI pipeline.
- **Smart Architect** (`phase3_infra/traffic-infra-engine/smart_suggestion_engine/smart_architect.py`) can call the **Google Gemini API** for AI-assisted intervention suggestions — set `GEMINI_API_KEY` in `backend/.env` to enable it; the pipeline runs fine without it.
- A **Streamlit** debug UI is an optional dependency in `backend/requirements.txt`, separate from the main React frontend.

## Tech stack

- **Backend**: FastAPI, Pydantic, OSMnx, NetworkX, GeoPandas, Shapely, pandas, Folium, SciPy, python-dotenv, optionally `google-generativeai`.
- **Frontend**: React 18, Vite, Tailwind CSS, Leaflet / react-leaflet / leaflet-draw, Mapbox GL, Turf.js, Recharts, lucide-react.

See [`docs/INTEGRATION.md`](docs/INTEGRATION.md) for more on the request/response flow between the frontend and backend.
