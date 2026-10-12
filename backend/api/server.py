import sys
import re
import shutil
import threading
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
import os

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

load_dotenv(BACKEND_ROOT / ".env")

from paths import SHARED_DIR
from api.alternative_service import (
    evaluate_and_rank_candidates,
    generate_alternative_candidates,
)
from api.adapters import build_analyze_response, wizard_to_phase34_payload
from api.demand_service import get_or_generate_demand
from api.logging_utils import create_run_log_file, write_log_event
from api.project_store import ProjectStore
from api.auth import hash_password, new_session_token, public_user, session_digest, verify_password
from api.btp_validation import validate_btp_records
from api.simulation_service import (
    run_corridor_intervention_simulation_batch,
    run_corridor_simulation,
    run_traffic_simulation,
    inspect_intervention_routes,
)
import traceback

PHASE3_DIR = BACKEND_ROOT / "phases" / "phase3_infra" / "traffic-infra-engine"
PHASE2_DIR = BACKEND_ROOT / "phases" / "phase2_placeholder"
PHASE1_DIR = BACKEND_ROOT / "phases" / "phase1_demand"
for directory in (PHASE3_DIR, PHASE2_DIR, PHASE1_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from phase34_pipeline import Phase34Pipeline
from phase34_visualizer import Phase34Visualizer
from runner import run_phase2

app = FastAPI(title="UIDT API", version="0.1.0")

# Analyses write the modified SUMO network and the pipeline outputs to fixed
# shared files, so two runs at once corrupt each other. Run them one at a time.
SIMULATION_LOCK = threading.RLock()

PROJECT_DB = os.getenv("UIDT_PROJECT_DB", str(BACKEND_ROOT / "uidt_projects.db"))
project_store = ProjectStore(PROJECT_DB)

create_run_log_file()

allowed_origins = [
    origin.strip()
    for origin in os.getenv('UIDT_ALLOWED_ORIGINS', 'http://localhost:5173').split(',')
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Location(BaseModel):
    lat: float
    lon: float
    name: str = "Unknown"


class Route(BaseModel):
    start: Location
    end: Location


class InterventionGeometry(BaseModel):
    type: str = "LineString"
    coordinates: list[list[float]] = Field(default_factory=list)


class Intervention(BaseModel):
    id: str = ""
    name: str = "Infra"
    serial_number: int = 1
    type: str = "infrastructure"
    infra_type: str = "flyover"
    geometry: InterventionGeometry = Field(default_factory=InterventionGeometry)
    road_width_m: float = 8
    is_oneway: bool = False
    adoption_rate_pct: float = Field(default=75.0, ge=0, le=100)
    # Package mode: the structure Route 4 is built through.
    is_main: bool = False


class Scenario(BaseModel):
    start_time: str = "09:00"
    end_time: str = "10:00"
    target_hour: int = 9
    ward_no: int = 163
    # Optional: "Monday".."Sunday" (case-insensitive). Selects the calibrated
    # per-weekday demand file; falls back to the static baseline when omitted
    # or not recognized.
    day_of_week: Optional[str] = None
    place_name: str = "Kathriguppe, Bangalore, India"
    dist_m: int = 5000
    congestion_multiplier: float = 1.0
    adoption_rate_pct: float = Field(default=75.0, ge=0, le=100)
    adoption_rates_pct: list[float] = Field(default_factory=lambda: [25.0, 50.0, 75.0])
    # "package": every item is built together as one project (main structure
    # plus connectors / widening). "compare": each item is simulated on its
    # own and the results are ranked against each other.
    project_mode: str = "package"


class AnalyzeOptions(BaseModel):
    widening: bool = False
    smart_signals: bool = False
    smart_analysis: bool = True


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    phone: str = Field(min_length=7, max_length=20)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    password: str = Field(min_length=1, max_length=128)


class ProjectPatchRequest(BaseModel):
    name: Optional[str] = None
    route: Optional[dict] = None
    interventions: Optional[list[dict]] = None
    scenario: Optional[dict] = None
    options: Optional[dict] = None
    report: Optional[dict] = None
    active_infra: Optional[list[str]] = None


class AnalyzeRequest(BaseModel):
    route: Route
    intervention: Intervention = Field(default_factory=Intervention)
    interventions: list[Intervention] = Field(default_factory=list)
    scenario: Scenario = Field(default_factory=Scenario)
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)
    report_name: Optional[str] = None
    simulation_result: Optional[dict] = None
    project_id: Optional[str] = None


class CorridorSimulationRequest(BaseModel):
    origin: Location
    destination: Location
    start_time: str = "09:00"
    end_time: str = "10:00"
    target_hour: int = Field(default=9, ge=0, le=23)
    ward_no: int = Field(default=163, ge=1)
    congestion_multiplier: float = Field(default=1.0, gt=0)
    day_of_week: Optional[str] = None


class AlternativesRequest(BaseModel):
    origin: Location
    destination: Location
    place_name: str = "Kathriguppe, Bangalore, India"
    dist_m: int = Field(default=5000, ge=500)
    infrastructure_types: list[str] = Field(
        default_factory=lambda: ["road", "flyover", "tunnel"]
    )
    nearby_radii_m: list[float] = Field(default_factory=lambda: [250.0, 500.0])


class BTPValidationRequest(BaseModel):
    records: list[dict] = Field(default_factory=list, max_length=100000)


@app.get("/health")
def health():
    return {"status": "ok", "service": "uidt-api"}


@app.post("/api/validation/btp")
def validate_btp(request: BTPValidationRequest):
    return validate_btp_records(request.records)


@app.get("/api/health")
def api_health_alias():
    return health()


SESSION_COOKIE = "uidt_session"
MAX_PROJECTS = 5


def current_user(session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = project_store.get_user_by_session(session_digest(session_token))
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return user


def validate_registration(request: RegisterRequest) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]{3,40}", request.username):
        raise HTTPException(status_code=422, detail="Username must be 3-40 letters, numbers, or underscores")
    if not re.fullmatch(r"\+?[0-9][0-9 .()-]{6,18}[0-9]", request.phone):
        raise HTTPException(status_code=422, detail="Enter a valid phone number")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", request.email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    if request.password != request.confirm_password:
        raise HTTPException(status_code=422, detail="Passwords do not match")


def set_session_cookie(response: Response, user_id: str) -> dict:
    token = new_session_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
    project_store.save_session(session_digest(token), user_id, expires_at)
    response.set_cookie(SESSION_COOKIE, token, max_age=7 * 24 * 60 * 60, httponly=True, samesite="lax")
    return {"status": "success", "user": public_user(project_store.get_user_by_id(user_id))}


@app.post("/api/auth/register")
def register(request: RegisterRequest, response: Response):
    validate_registration(request)
    username = request.username.strip()
    phone = re.sub(r"[^0-9+]", "", request.phone)
    email = request.email.strip().lower()
    duplicate = project_store.user_exists(username, phone, email)
    if duplicate:
        raise HTTPException(status_code=409, detail=duplicate)
    try:
        user = project_store.create_user(username, phone, email, hash_password(request.password))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username, phone, or email is already registered") from exc
    return set_session_cookie(response, user["id"])


@app.post("/api/auth/login")
def login(request: LoginRequest, response: Response):
    phone = re.sub(r"[^0-9+]", "", request.phone)
    user = project_store.get_user_by_login(phone)
    if user is None or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid phone number or password")
    return set_session_cookie(response, user["id"])


@app.get("/api/auth/me")
def auth_me(user=Depends(current_user)):
    return {"status": "success", "user": public_user(user)}


@app.post("/api/auth/logout")
def logout(response: Response, session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    if session_token:
        project_store.delete_session(session_digest(session_token))
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "success"}


@app.get("/demand")
def demand(
    hour: int = Query(9, ge=0, le=23),
    ward_no: int = Query(163, ge=1),
    multiplier: float = Query(1.0, gt=0),
    refresh: bool = Query(False),
):
    try:
        data = get_or_generate_demand(
            target_hour=hour,
            ward_no=ward_no,
            congestion_multiplier=multiplier,
            refresh=refresh,
        )
        return {"status": "success", "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/simulate")
def simulate(request: CorridorSimulationRequest):
    with SIMULATION_LOCK:
        return _simulate(request)


def _simulate(request: CorridorSimulationRequest):
    try:
        # Determine target hour from start_time (e.g. "09:00" -> 9)
        hour = 9
        try:
            hour = int(request.start_time.split(":")[0])
        except Exception:
            hour = request.target_hour

        demand_data = get_or_generate_demand(
            target_hour=hour,
            ward_no=request.ward_no,
            congestion_multiplier=request.congestion_multiplier,
        )
        return run_corridor_simulation(
            origin=request.origin.model_dump(),
            destination=request.destination.model_dump(),
            demand_data=demand_data,
            start_time=request.start_time,
            end_time=request.end_time,
            day_of_week=request.day_of_week,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/simulate")
def simulate_api_alias(request: CorridorSimulationRequest):
    return simulate(request)


@app.post("/alternatives")
def alternatives(request: AlternativesRequest):
    with SIMULATION_LOCK:
        return _alternatives(request)


def _alternatives(request: AlternativesRequest):
    try:
        candidates = generate_alternative_candidates(
            origin=request.origin.model_dump(),
            destination=request.destination.model_dump(),
            infrastructure_types=request.infrastructure_types,
            nearby_radii_m=request.nearby_radii_m,
        )

        def run_feasibility(candidate):
            candidate_payload = {
                "route": {
                    "start": {
                        **candidate["start"],
                        "name": request.origin.name,
                    },
                    "end": {
                        **candidate["end"],
                        "name": request.destination.name,
                    },
                },
                "intervention": {
                    "type": "infrastructure",
                    "infra_type": candidate["infrastructure"]["type"],
                    "geometry": candidate["geometry"],
                    "road_width_m": candidate["infrastructure"]["road_width_m"],
                    "is_oneway": candidate["infrastructure"]["is_oneway"],
                },
                "scenario": {
                    "place_name": request.place_name,
                    "dist_m": request.dist_m,
                },
                "options": {"smart_analysis": False},
            }
            pipeline = Phase34Pipeline()
            phase34_payload = wizard_to_phase34_payload(candidate_payload)
            phase34_output = pipeline.run_from_dict(phase34_payload)
            return phase34_output, pipeline.modified_graph

        def validate_route(candidate, modified_graph):
            return inspect_intervention_routes(
                origin=candidate["start"],
                destination=candidate["end"],
                modified_graph=modified_graph,
                proposal_coordinates=candidate["geometry"].get("coordinates"),
            )

        ranked_candidates = evaluate_and_rank_candidates(
            candidates,
            feasibility_runner=run_feasibility,
            route_validator=validate_route,
        )
        return {
            "status": "success",
            "candidate_count": len(ranked_candidates),
            "simulation_policy": "SUMO runs only for the selected candidate.",
            "feasible_count": sum(
                candidate["ranking_eligible"] for candidate in ranked_candidates
            ),
            "candidates": ranked_candidates,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        print(f"[API] Alternative evaluation failed: {exc}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/alternatives")
def alternatives_api_alias(request: AlternativesRequest):
    return alternatives(request)


@app.post("/api/projects")
def create_project(request: ProjectCreateRequest, user=Depends(current_user)):
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Project name is required")
    if project_store.count_projects(user["id"]) >= MAX_PROJECTS:
        raise HTTPException(status_code=409, detail="You can have a maximum of 5 projects. Delete an existing project to create a new one.")
    project = project_store.create_project(name, user["id"])
    return {"status": "success", "project": project}


@app.get("/api/projects")
def list_projects(user=Depends(current_user)):
    projects = project_store.list_projects(user["id"])
    return {"status": "success", "projects": projects, "limit": MAX_PROJECTS, "count": len(projects)}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user=Depends(current_user)):
    project = project_store.get_project(project_id, user["id"])
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "success", "project": project}


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, request: ProjectPatchRequest, user=Depends(current_user)):
    existing = project_store.get_project(project_id, user["id"])
    if existing is None:
        raise HTTPException(status_code=404, detail="Project not found")
    payload = {}
    if request.name is not None:
        name = request.name.strip()
        if not name or len(name) > 100:
            raise HTTPException(status_code=422, detail="Project name must be 1-100 characters")
        payload["name"] = name
    if request.route is not None:
        payload["route"] = request.route
    if request.interventions is not None:
        payload["interventions"] = request.interventions
    if request.scenario is not None:
        payload["scenario"] = request.scenario
    if request.options is not None:
        payload["options"] = request.options
    if request.report is not None:
        payload["report"] = request.report
    if request.active_infra is not None:
        payload["active_infra"] = request.active_infra

    updated = project_store.update_project(project_id, payload, user["id"])
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "success", "project": updated}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, user=Depends(current_user)):
    deleted = project_store.delete_project(project_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "success", "deleted": True}


@app.post("/api/projects/{project_id}/analyze")
def analyze_project(project_id: str, request: AnalyzeRequest, user=Depends(current_user)):
    # Ensure the project exists; report can be saved back to project on completion.
    if project_store.get_project(project_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    request.project_id = project_id
    return analyze(request, user=user, user_id=user["id"])


def _analyze_once(request: AnalyzeRequest) -> dict:
    """Run the full pipeline for one set of interventions built together."""
    try:
        payload = request.model_dump()
        interventions = list(request.interventions or [])
        if not interventions and request.intervention:
            interventions = [request.intervention]
        if interventions:
            payload["interventions"] = [item.model_dump() if hasattr(item, 'model_dump') else item for item in interventions]
        else:
            payload["interventions"] = []

        write_log_event(
            "REQUEST_ANALYZE",
            {
                "route": payload.get("route"),
                "intervention": payload.get("intervention"),
                "interventions": payload.get("interventions"),
                "scenario": payload.get("scenario"),
                "options": payload.get("options"),
                "report_name": payload.get("report_name"),
                "project_id": payload.get("project_id"),
            },
        )
        phase34_payload = wizard_to_phase34_payload(payload)

        if not phase34_payload.get("proposals"):
            raise HTTPException(status_code=400, detail="At least one infrastructure intervention is required.")
        if len(phase34_payload["proposals"][0]["coordinates"]) < 2:
            raise HTTPException(status_code=400, detail="At least two route coordinates are required.")

        # Determine target hour from start_time
        hour = 9
        try:
            hour = int(request.scenario.start_time.split(":")[0])
        except Exception:
            hour = request.scenario.target_hour

        demand_data = get_or_generate_demand(
            target_hour=hour,
            ward_no=request.scenario.ward_no,
            congestion_multiplier=request.scenario.congestion_multiplier,
        )

        pipeline = Phase34Pipeline()
        phase34_output = pipeline.run_from_dict(phase34_payload)

        is_feasible = phase34_output.get("feasibility", {}).get("is_feasible", False)
        route4_valid = None
        route4_error = None
        is_infrastructure_intervention = bool(interventions)
        is_widening_only = bool(phase34_payload.get("proposals")) and all(
            proposal.get("infra_key") == "widening"
            for proposal in phase34_payload["proposals"]
        )
        is_road_modification_only = bool(phase34_payload.get("proposals")) and all(
            proposal.get("infra_key") in {"widening", "road_block"}
            for proposal in phase34_payload["proposals"]
        )
        if is_road_modification_only:
            route4_valid = None
            route4_error = None
            try:
                simulation_result = run_traffic_simulation(
                    pipeline.original_graph,
                    pipeline.modified_graph,
                    demand_data,
                    is_feasible,
                    start_time=request.scenario.start_time,
                    end_time=request.scenario.end_time,
                    day_of_week=request.scenario.day_of_week,
                    origin=request.route.start.model_dump(),
                    destination=request.route.end.model_dump(),
                )
            except Exception as exc:
                route4_error = f"Road-modification simulation fallback: {exc}"
                print(f"[API] {route4_error}")
                write_log_event(
                    "ROAD_MODIFICATION_SIMULATION_FALLBACK",
                    {"error": str(exc), "traceback": traceback.format_exc()},
                )
                simulation_result = run_corridor_simulation(
                    origin=request.route.start.model_dump(),
                    destination=request.route.end.model_dump(),
                    demand_data=demand_data,
                    start_time=request.scenario.start_time,
                    end_time=request.scenario.end_time,
                    day_of_week=request.scenario.day_of_week,
                )
        else:
            if is_infrastructure_intervention and pipeline.modified_graph is not None:
                route4_valid = False
                try:
                    inspect_intervention_routes(
                        origin=request.route.start.model_dump(),
                        destination=request.route.end.model_dump(),
                        modified_graph=pipeline.modified_graph,
                        proposal_coordinates=phase34_payload["proposals"][0].get("coordinates"),
                    )
                    route4_valid = True
                except Exception as exc:
                    route4_error = str(exc)
                    print(f"[API] Route 4 validation failed: {exc}")
                    print(traceback.format_exc())

                try:
                    simulation_result = run_corridor_intervention_simulation_batch(
                        origin=request.route.start.model_dump(),
                        destination=request.route.end.model_dump(),
                        demand_data=demand_data,
                        start_time=request.scenario.start_time,
                        end_time=request.scenario.end_time,
                        modified_graph=pipeline.modified_graph,
                        adoption_rates_pct=request.scenario.adoption_rates_pct,
                        primary_adoption_rate_pct=request.scenario.adoption_rate_pct,
                        proposal_coordinates=phase34_payload["proposals"][0].get("coordinates"),
                        day_of_week=request.scenario.day_of_week,
                    )
                except Exception as exc:
                    route4_error = route4_error or str(exc)
                    print(f"[API] Corridor intervention simulation failed: {exc}")
                    write_log_event(
                        "SIMULATION_FAILED",
                        {"error": str(exc), "traceback": traceback.format_exc()},
                    )
                    print(traceback.format_exc())
                    try:
                        simulation_result = run_traffic_simulation(
                            pipeline.original_graph,
                            pipeline.modified_graph,
                            demand_data,
                            is_feasible,
                            start_time=request.scenario.start_time,
                            end_time=request.scenario.end_time,
                            day_of_week=request.scenario.day_of_week,
                        )
                    except Exception as traffic_fallback_exc:
                        print(
                            "[API] Network-level traffic simulation fallback failed: "
                            f"{traffic_fallback_exc}"
                        )
                        simulation_result = request.simulation_result
                        if simulation_result is None:
                            try:
                                simulation_result = run_corridor_simulation(
                                    origin=request.route.start.model_dump(),
                                    destination=request.route.end.model_dump(),
                                    demand_data=demand_data,
                                    start_time=request.scenario.start_time,
                                    end_time=request.scenario.end_time,
                                    day_of_week=request.scenario.day_of_week,
                                )
                            except Exception as fallback_exc:
                                print(f"[API] Standard corridor simulation fallback failed: {fallback_exc}")
                                simulation_result = None
            else:
                simulation_result = request.simulation_result
                if simulation_result is None:
                    try:
                        simulation_result = run_traffic_simulation(
                            pipeline.original_graph,
                            pipeline.modified_graph,
                            demand_data,
                            is_feasible,
                            start_time=request.scenario.start_time,
                            end_time=request.scenario.end_time,
                            day_of_week=request.scenario.day_of_week,
                        )
                    except Exception as exc:
                        print(f"[API] Full traffic simulation failed: {exc}")
                        print(traceback.format_exc())
                        raise

        phase2_result = run_phase2(
            request=payload,
            graph=pipeline.modified_graph or pipeline.original_graph,
            feasibility_result=phase34_output.get("feasibility"),
        )

        response = build_analyze_response(
            payload, phase34_output, demand_data, phase2_result, simulation_result
        )
        response["route4_valid"] = route4_valid
        response["route4_error"] = route4_error
        response["report"]["route4Valid"] = route4_valid
        response["report"]["route4Error"] = route4_error
        widened_edges = {}
        widened_base_lanes = {}
        blocked_edges = set()
        if pipeline.modified_graph is not None:
            widened_edges = pipeline.modified_graph.graph.get("uidt_widened_edges") or {}
            widened_base_lanes = pipeline.modified_graph.graph.get("uidt_widened_base_lanes") or {}
            blocked_edges = pipeline.modified_graph.graph.get("uidt_blocked_edges") or set()
        response["report"]["widenedEdgeCount"] = len(widened_edges)
        response["report"]["lanesAddedCount"] = sum(
            max(0, lanes - widened_base_lanes.get(edge_id, lanes - 1))
            for edge_id, lanes in widened_edges.items()
        )
        response["report"]["blockedEdgeCount"] = len(blocked_edges)
        response["report"]["roadModification"] = (simulation_result or {}).get("road_modification")

        write_log_event(
            "RESPONSE_ANALYZE",
            {
                "status": response.get("status"),
                "feasibility": response.get("feasibility"),
                "demand": response.get("demand"),
                "phase2": response.get("phase2"),
                "simulation": response.get("simulation"),
                "report": response.get("report"),
                "artifacts": response.get("artifacts"),
                "route4_valid": route4_valid,
                "route4_error": route4_error,
            },
        )

        try:
            Phase34Visualizer().generate_map()
            response["artifacts"]["map_html"] = "/api/artifacts/phase34_map.html"
            response["report"]["artifacts"] = response["artifacts"]
        except Exception as exc:
            print(f"[API] Map visualization skipped: {exc}")

        try:
            from tanisha_visualizer import generate_map as generate_demand_map
            generate_demand_map()
            response["artifacts"]["demand_map_html"] = "/api/artifacts/traffic_map.html"
        except Exception as exc:
            print(f"[API] Demand map skipped: {exc}")

        return response
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


NEW_ALIGNMENT_TYPES = {"flyover", "tunnel"}
SUPPORTED_INFRA_TYPES = NEW_ALIGNMENT_TYPES | {"road_block", "widening"}
MAX_COMPARE_ITEMS = 3
BASE_ADOPTION_SWEEP = (25.0, 50.0, 75.0)


def _request_interventions(request: AnalyzeRequest) -> list[Intervention]:
    interventions = list(request.interventions or [])
    if not interventions and request.intervention:
        interventions = [request.intervention]
    return interventions


def _validate_project_mode(mode: str, interventions: list[Intervention]) -> None:
    infra = [item for item in interventions if item.type != "optimize"]
    if not infra:
        return
    kinds = [item.infra_type for item in infra]
    unsupported = sorted(set(kinds) - SUPPORTED_INFRA_TYPES)
    if unsupported:
        raise HTTPException(status_code=400, detail=f"Unsupported infrastructure type(s): {', '.join(unsupported)}")
    if mode == "compare":
        if len(infra) > MAX_COMPARE_ITEMS:
            raise HTTPException(
                status_code=400,
                detail=f"Compare mode supports up to {MAX_COMPARE_ITEMS} infrastructure options.",
            )


def _suffix_artifacts(response: dict, suffix: str) -> None:
    """Copy this run's artifacts to suffixed names so later runs don't overwrite them."""
    artifacts = response.get("artifacts") or {}
    for key, url in list(artifacts.items()):
        if not url:
            continue
        source = SHARED_DIR / Path(url).name
        if not source.exists():
            continue
        target = SHARED_DIR / f"{source.stem}__{suffix}{source.suffix}"
        shutil.copyfile(source, target)
        artifacts[key] = f"/api/artifacts/{target.name}"
    response.setdefault("report", {})["artifacts"] = artifacts


def _primary_scenario(simulation: dict) -> dict:
    scenarios = (simulation or {}).get("scenarios") or []
    primary_rate = (simulation or {}).get("primary_adoption_rate_pct")
    for scenario in scenarios:
        if primary_rate is not None and float(scenario.get("adoption_rate_pct", -1)) == float(primary_rate):
            return scenario
    return scenarios[-1] if scenarios else (simulation or {})


def _variant_report(key: str, item: Intervention, response: dict) -> dict:
    simulation = response.get("simulation") or {}
    return {
        **(response.get("report") or {}),
        "variantKey": key,
        "reportName": item.name,
        "feasibility": response.get("feasibility"),
        "impact": response.get("impact"),
        "phase2": response.get("phase2"),
        "smartRecommendation": response.get("smart_recommendation"),
        "simulation": simulation,
        "simulationScenarios": simulation.get("scenarios", []),
        "demand": response.get("demand"),
        "artifacts": response.get("artifacts"),
    }


def _compare_summary(request: AnalyzeRequest, variants: list[dict]) -> dict:
    rows = []
    for variant in variants:
        response = variant["response"]
        simulation = response.get("simulation") or {}
        primary = _primary_scenario(simulation)
        feasibility = response.get("feasibility") or {}
        items = (response.get("report") or {}).get("packageItems") or []
        rows.append(
            {
                "key": variant["key"],
                "label": variant["item"].name,
                "infraType": variant["item"].infra_type,
                "feasible": bool(feasibility.get("is_feasible")),
                "collisionCount": feasibility.get("collision_count", 0),
                "route4Valid": response.get("route4_valid") is not False,
                "adoptionRatePct": primary.get("adoption_rate_pct"),
                "timeSavedPct": primary.get("time_reduction_pct"),
                "congestionReductionPct": primary.get("congestion_reduction_pct"),
                "travelTimeBeforeMin": primary.get("avg_travel_time_before_min"),
                "travelTimeAfterMin": primary.get("avg_travel_time_after_min"),
                "lengthM": items[0].get("length_m") if items else None,
                "error": variant.get("error"),
            }
        )

    def score(row):
        # Buildable options with a valid new route first, then most time saved,
        # then most congestion relief.
        return (
            row["error"] is None,
            row["feasible"] and row["route4Valid"],
            float(row["timeSavedPct"] if row["timeSavedPct"] is not None else float("-inf")),
            float(row["congestionReductionPct"] if row["congestionReductionPct"] is not None else float("-inf")),
        )

    ranked = sorted(rows, key=score, reverse=True)
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank

    best = ranked[0] if ranked else None
    verdict = "No option could be simulated."
    if best and best["error"] is None:
        others = [row for row in ranked[1:] if row["error"] is None and row["timeSavedPct"] is not None]

        def time_effect(pct):
            pct = float(pct)
            return f"saves {pct:.1f}%" if pct >= 0 else f"adds {abs(pct):.1f}%"

        verdict = (
            f"{best['label']} performs best"
            + (f": it {time_effect(best['timeSavedPct'])} travel time" if best["timeSavedPct"] is not None else "")
            + (
                "; "
                + ", ".join(f"{row['label']} {time_effect(row['timeSavedPct'])}" for row in others)
                if others
                else ""
            )
            + "."
        )
        if best["timeSavedPct"] is not None and float(best["timeSavedPct"]) < 0:
            verdict += " No option reduces travel time at the chosen adoption rates."
        if not (best["feasible"] and best["route4Valid"]):
            verdict += " No option is fully buildable yet; results are previews."

    start = request.route.start
    end = request.route.end
    return {
        "route": f"{start.name} to {end.name}",
        "scenarioCount": len(rows),
        "winnerKey": best["key"] if best else None,
        "verdict": verdict,
        "scenarios": rows,
    }


def _analyze_compare(request: AnalyzeRequest, interventions: list[Intervention]) -> dict:
    variants = []
    for index, item in enumerate(interventions, start=1):
        key = f"option{index}"
        rate = float(item.adoption_rate_pct)
        sub_request = request.model_copy(deep=True)
        sub_request.intervention = item
        sub_request.interventions = [item]
        sub_request.simulation_result = None
        sub_request.report_name = item.name
        sub_request.scenario.adoption_rate_pct = rate
        sub_request.scenario.adoption_rates_pct = sorted(set(BASE_ADOPTION_SWEEP) | {rate})
        print(f"[API] Compare mode: running {item.name} ({index}/{len(interventions)})")
        try:
            response = _analyze_once(sub_request)
            _suffix_artifacts(response, key)
            error = None
            simulation_status = (response.get("simulation") or {}).get("status")
            is_completed_widening = (
                item.infra_type in {"widening", "road_block"}
                and simulation_status == "complete"
            )
            if simulation_status != "batch_complete" and not is_completed_widening:
                error = "Simulation failed: " + (
                    response.get("route4_error") or "no before/after results were produced"
                )
            variants.append({"key": key, "item": item, "response": response, "error": error})
        except HTTPException as exc:
            variants.append({"key": key, "item": item, "response": {}, "error": str(exc.detail)})
        except Exception as exc:
            print(traceback.format_exc())
            variants.append({"key": key, "item": item, "response": {}, "error": str(exc)})

    summary = _compare_summary(request, variants)
    succeeded = [variant for variant in variants if variant["error"] is None] or [
        variant for variant in variants if variant["response"]
    ]
    if not succeeded:
        raise HTTPException(
            status_code=500,
            detail="; ".join(f"{v['item'].name}: {v['error']}" for v in variants),
        )
    winner = next(
        (variant for variant in succeeded if variant["key"] == summary["winnerKey"]),
        succeeded[0],
    )

    start = request.route.start
    end = request.route.end
    report = {
        "projectMode": "compare",
        "reportName": request.report_name or "Infrastructure comparison",
        "fromPlace": start.name,
        "toPlace": end.name,
        "overallSummary": summary,
        "variants": [
            {
                "key": variant["key"],
                "label": variant["item"].name,
                "error": variant["error"],
                "report": _variant_report(variant["key"], variant["item"], variant["response"])
                if variant["response"]
                else None,
            }
            for variant in variants
        ],
    }
    # Top-level fields mirror the best option so the map view can show it.
    return {
        **winner["response"],
        "mode": "compare",
        "comparison": summary,
        "report": report,
    }


def _analysis_simulation_completed(response: dict) -> bool:
    simulation = response.get("simulation") or {}
    status = simulation.get("status")
    if status == "batch_complete":
        return True
    if status != "complete":
        return False

    if response.get("route4_error") or response.get("route4Error"):
        return (
            simulation.get("avg_travel_time_before_min") is not None
            and simulation.get("avg_travel_time_after_min") is not None
        )
    return True


@app.post("/analyze")
def analyze(request: AnalyzeRequest, user=Depends(current_user), user_id: Optional[str] = None):
    user_id = user_id or user["id"]
    interventions = _request_interventions(request)
    mode = (request.scenario.project_mode or "package").strip().lower()
    if mode not in {"package", "compare"}:
        raise HTTPException(status_code=400, detail="project_mode must be 'package' or 'compare'.")
    request.scenario.project_mode = mode
    _validate_project_mode(mode, interventions)

    with SIMULATION_LOCK:
        if mode == "compare" and len(interventions) > 1:
            response = _analyze_compare(request, interventions)
        else:
            response = _analyze_once(request)
            response["mode"] = mode

    if request.project_id:
        if not _analysis_simulation_completed(response):
            raise HTTPException(
                status_code=503,
                detail="The new simulation did not complete; the previous project report was kept.",
            )
        project_store.update_project(
            request.project_id,
            {
                "route": request.route.model_dump(),
                "interventions": [item.model_dump() for item in interventions],
                "scenario": request.scenario.model_dump(),
                "options": request.options.model_dump(),
                "report": response.get("report", {}),
            },
            user_id=user_id,
        )
    return response


@app.get("/artifacts/{filename}")
@app.get("/api/artifacts/{filename}")
def get_artifact(filename: str, download: bool = False):
    safe_name = Path(filename).name
    file_path = SHARED_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    if file_path.suffix.lower() == ".md":
        return FileResponse(file_path, filename=safe_name, media_type="text/markdown")
    if download:
        return FileResponse(file_path, filename=safe_name)
    return FileResponse(file_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="127.0.0.1", port=8000, reload=True)
