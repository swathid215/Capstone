import json
import re
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = BACKEND_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

CURRENT_LOG_PATH: Path | None = None


def _metric_summary(metrics: dict | None) -> dict:
    metrics = metrics or {}
    return {
        "mean_travel_time_s": metrics.get("avg_travel_time_sec", 0.0),
        "mean_speed_kmh": metrics.get("avg_speed_kmh", 0.0),
        "completed_vehicles": metrics.get(
            "completed_vehicle_count", metrics.get("throughput", 0)
        ),
        "congestion_ratio": metrics.get("avg_congestion_ratio", 0.0),
    }


def _scenario_summary(scenario: dict) -> dict:
    before = scenario.get("before") or {}
    after = scenario.get("after") or {}
    baseline_time = float(before.get("avg_travel_time_sec", 0) or 0)
    scenario_time = float(after.get("avg_travel_time_sec", 0) or 0)
    time_change = scenario.get("time_reduction_pct")
    if time_change is None and baseline_time:
        time_change = ((baseline_time - scenario_time) / baseline_time) * 100

    heatmap = scenario.get("heatmap") or {}
    difference = heatmap.get("difference") or {}
    return {
        "adoption_rate_pct": scenario.get("adoption_rate_pct"),
        "metrics": _metric_summary(after),
        "travel_time_change_vs_baseline_pct": round(float(time_change or 0), 1),
        "spatial": {
            "total_edge_count": (difference.get("summary") or {}).get(
                "total_edge_count", 0
            ),
            "improved_edges": (difference.get("summary") or {}).get(
                "improved_edge_count", 0
            ),
            "worsened_edges": (difference.get("summary") or {}).get(
                "worsened_edge_count", 0
            ),
            "improved_hotspots": difference.get("improved_hotspots", []),
            "worsened_hotspots": difference.get("hotspots", []),
        },
    }


def _compact_payload(event_name: str, payload: dict) -> dict:
    if event_name == "REQUEST_ANALYZE":
        route = payload.get("route") or {}
        intervention = payload.get("intervention") or {}
        scenario = payload.get("scenario") or {}
        return {
            "route": {
                "start": route.get("start") or {},
                "end": route.get("end") or {},
            },
            "infrastructure": {
                "type": intervention.get("type"),
                "infra_type": intervention.get("infra_type"),
                "road_width_m": intervention.get("road_width_m"),
                "is_oneway": intervention.get("is_oneway"),
            },
            "scenario": {
                "start_time": scenario.get("start_time"),
                "end_time": scenario.get("end_time"),
                "target_hour": scenario.get("target_hour"),
                "ward_no": scenario.get("ward_no"),
                "adoption_rates_pct": scenario.get("adoption_rates_pct", [25, 50, 75]),
            },
            "options": payload.get("options") or {},
            "report_name": payload.get("report_name"),
        }

    simulation = payload.get("simulation") or {}
    scenarios = simulation.get("scenarios") or []
    primary = _scenario_summary(simulation) if simulation.get("after") else None
    report = payload.get("report") or {}
    solutions = report.get("solutions") or {}
    return {
        "status": payload.get("status"),
        "infrastructure": {
            "type": solutions.get("type"),
            "infra_type": solutions.get("infraType"),
            "feasibility": payload.get("feasibility") or {},
        },
        "metrics": {
            "baseline": _metric_summary(simulation.get("before")),
            "scenarios": [_scenario_summary(item) for item in scenarios],
        },
        "spatial": {
            "primary_adoption_rate_pct": simulation.get("adoption_rate_pct"),
            "total_edge_count": (primary.get("spatial") or {}).get(
                "total_edge_count", 0
            ) if primary else 0,
            "primary": primary.get("spatial") if primary else {},
        },
        "report": {
            "report_name": report.get("reportName"),
            "artifact_keys": sorted((payload.get("artifacts") or {}).keys()),
            "route4_valid": payload.get("route4_valid"),
        },
    }


def _next_log_number() -> int:
    existing = list(LOGS_DIR.glob("log*.txt"))
    if not existing:
        return 1

    numbers = []
    for path in existing:
        match = re.search(r"log(\d+)\.txt$", path.name)
        if match:
            numbers.append(int(match.group(1)))

    return max(numbers, default=0) + 1


def create_run_log_file() -> Path:
    global CURRENT_LOG_PATH
    next_number = _next_log_number()
    CURRENT_LOG_PATH = LOGS_DIR / f"log{next_number}.txt"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    CURRENT_LOG_PATH.write_text(
        "UIDT run log\n"
        f"created_at={timestamp}\n"
        f"log_file={CURRENT_LOG_PATH.name}\n\n",
        encoding="utf-8",
    )
    return CURRENT_LOG_PATH


def write_log_event(event_name: str, payload: dict | None = None) -> Path:
    global CURRENT_LOG_PATH
    if CURRENT_LOG_PATH is None:
        create_run_log_file()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    record = {
        "timestamp": timestamp,
        "event": event_name,
        "payload": _compact_payload(event_name, payload or {}),
    }

    with CURRENT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str, indent=2))
        handle.write("\n\n")

    return CURRENT_LOG_PATH
