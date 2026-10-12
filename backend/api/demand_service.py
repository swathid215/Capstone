import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

AUTO_TOOL_DIR = Path(__file__).resolve().parents[2] / "auto_tool"
ROUTE_FILE = AUTO_TOOL_DIR / "simulation.rou.xml"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEMAND_CACHE = BACKEND_ROOT / "data" / "shared" / "integration_demand.json"
PHASE1_DIR = BACKEND_ROOT / "phases" / "phase1_demand"

if str(PHASE1_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE1_DIR))

from tanisha_pipeline import TanishaTrafficPipeline


def _vehicle_count_for_hour(vehicles: list, target_hour: int) -> int:
    start = int(target_hour) * 3600
    end = start + 3600
    count = 0
    for vehicle in vehicles:
        try:
            depart = float(vehicle.get("depart", 0) or 0)
        except (TypeError, ValueError):
            continue
        if start <= depart < end:
            count += 1
    return count


def get_or_generate_demand(
    target_hour: int = 9,
    ward_no: int = 163,
    congestion_multiplier: float = 1.0,
    refresh: bool = False,
) -> dict:
    """Return the Phase 1 ward demand plus committed route-file provenance."""
    if not ROUTE_FILE.exists():
        raise FileNotFoundError(f"SUMO route file is missing: {ROUTE_FILE}")

    cached = None
    if DEMAND_CACHE.exists() and not refresh:
        try:
            candidate = json.loads(DEMAND_CACHE.read_text(encoding="utf-8"))
            metadata = candidate.get("scenario_metadata", {})
            if (
                metadata.get("ward_no") == int(ward_no)
                and metadata.get("target_hour") == int(target_hour)
                and float(metadata.get("multiplier", -1)) == float(congestion_multiplier)
            ):
                cached = candidate
        except (OSError, ValueError, TypeError):
            cached = None

    if cached is None:
        csv_path = PHASE1_DIR / "bangalore-wards-2019-3-OnlyWeekdays-HourlyAggregate.csv"
        pipeline = TanishaTrafficPipeline(str(csv_path))
        generated_path = pipeline.generate_demand(
            congestion_multiplier=congestion_multiplier,
            target_hour=target_hour,
            ward_no=ward_no,
        )
        if not generated_path:
            raise FileNotFoundError(
                f"Phase 1 could not generate demand for ward {ward_no} at hour {target_hour}"
            )
        cached = json.loads(Path(generated_path).read_text(encoding="utf-8"))
        DEMAND_CACHE.parent.mkdir(parents=True, exist_ok=True)
        DEMAND_CACHE.write_text(json.dumps(cached, indent=2), encoding="utf-8")

    root = ET.parse(ROUTE_FILE).getroot()
    vehicles = root.findall("vehicle")
    total_vehicle_count = len(vehicles)
    target_hour_vehicle_count = _vehicle_count_for_hour(vehicles, target_hour)

    result = {
        **cached,
        "scenario_metadata": {
            **cached.get("scenario_metadata", {}),
            "source": "Phase 1 ward CSV + committed SUMO route file",
            "route_file": str(ROUTE_FILE),
            "requested_ward_no": int(ward_no),
            "requested_congestion_multiplier": float(congestion_multiplier),
            "filters_applied": ["ward_csv", "target_hour_summary", "committed_route_file"],
        },
        "route_file": str(ROUTE_FILE),
        "vehicle_count": total_vehicle_count,
        "vehicle_count_in_target_hour": target_hour_vehicle_count,
    }
    return result
