# UIDT Integration Guide

## Architecture

```
frontend/ (React wizard)
    ↓ POST /api/analyze
backend/api/server.py
    ├── Phase 1 — demand (tanisha_pipeline)
    ├── Phase 2 — optimization estimates (placeholder/heuristics)
    ├── Phase 3 — feasibility + graph modification (phase34_pipeline)
    └── Phase 4 — NetworkX traffic simulation
    ↓
backend/data/shared/  (JSON, HTML, GeoJSON outputs)
```

## Run locally

```bash
# Terminal 1
cd backend && uvicorn api.server:app --reload --port 8000

# Terminal 2
cd frontend && npm run dev
```

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Service check |
| `GET /demand?hour=9&ward_no=163` | Phase 1 corridor demand |
| `POST /analyze` | Full pipeline |
| `GET /artifacts/{filename}` | Shared HTML/GeoJSON outputs |

## Phase status

| Phase | Owner | Status |
|-------|-------|--------|
| 1 | Tanisha | Integrated — demand JSON + map |
| 2 | TBD | Heuristic preview (widening/signals) |
| 3 | Swathi H | Integrated — feasibility + graph mod |
| 4 | Shared | Integrated — NetworkX simulator |

## Legacy folders

`UI V1/` and `Capstone_Project/` were moved to `_archive/` after integration.
Use `frontend/` and `backend/` only.
