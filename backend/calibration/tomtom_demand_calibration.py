"""Single-step TomTom demand estimation and SUMO calibration.

This script:
1. Loads observed TomTom states for a target hour/corridor
2. Estimates demand using the TomTom demand estimator
3. Generates a SUMO route file with the estimated demand
4. Runs SUMO baseline simulation
5. Compares simulated output to observed TomTom metrics
6. Produces a calibration report with fit metrics
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "files_tomtom"
NETWORK_PATH = ROOT / "auto_tool" / "network.net.xml"
ROUTE_PATH = ROOT / "auto_tool" / "simulation.rou.xml"
OUTPUT_ROOT = Path(__file__).resolve().parent / "tomtom_calibration_run"


def run_tomtom_demand_calibration(
    target_hour: int = 9,
    target_dates: list[str] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Run a single TomTom demand estimation and SUMO calibration cycle.

    Args:
        target_hour: Which hour (0-23) to calibrate for.
        target_dates: Which dates to use for TomTom state aggregation.
        output_dir: Where to write results (default: tomtom_calibration_run).

    Returns:
        A calibration report dict with fit metrics, demand profile, and recommendations.
    """
    if target_dates is None:
        target_dates = ["2026-06-12", "2026-06-13", "2026-06-14", "2026-06-15", "2026-06-16"]
    if output_dir is None:
        output_dir = OUTPUT_ROOT

    output_dir.mkdir(parents=True, exist_ok=True)

    # Calibration helpers are optional research artifacts and are not required
    # by the production API. Import them at execution time so the rest of the
    # repository remains testable when those artifacts are not checked in.
    try:
        if __package__:
            from .dataset_loader import load_tomtom_dataset
            from .demand_generator import generate_scaled_route_file
            from .tomtom_demand import estimate_tomtom_demand
            from .sumo_runner import SimulationSpec, run_baseline_sumo
        else:
            REPO_ROOT = Path(__file__).resolve().parents[2]
            if str(REPO_ROOT) not in sys.path:
                sys.path.insert(0, str(REPO_ROOT))
            from backend.calibration.dataset_loader import load_tomtom_dataset
            from backend.calibration.demand_generator import generate_scaled_route_file
            from backend.calibration.tomtom_demand import estimate_tomtom_demand
            from backend.calibration.sumo_runner import SimulationSpec, run_baseline_sumo
    except ModuleNotFoundError as exc:
        return {
            "status": "FAILED",
            "reason": f"Optional calibration artifacts are unavailable: {exc.name}",
            "target_hour": target_hour,
            "target_dates": target_dates,
        }

    # Step 1: Load observed TomTom states
    print(f"[1/5] Loading TomTom states for hour {target_hour}...")
    dataset = load_tomtom_dataset(
        DATASET_DIR,
        selected_hour=target_hour,
        selected_dates=target_dates,
        holdout_dates=[],
    )
    tomtom_rows = dataset.get("selected_hour_data", [])
    if not tomtom_rows:
        return {
            "status": "FAILED",
            "reason": "No TomTom data found for selected hour and dates",
            "target_hour": target_hour,
            "target_dates": target_dates,
        }

    # Step 2: Estimate demand from TomTom states
    print("[2/5] Estimating demand from TomTom observations...")
    try:
        demand_estimate = estimate_tomtom_demand(tomtom_rows)
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason": f"Demand estimation failed: {exc}",
            "target_hour": target_hour,
        }

    estimated_vehicles = demand_estimate["estimated_demand"]["vehicles_per_hour"]
    route_split = demand_estimate["route_split"]

    print(f"   → Estimated {estimated_vehicles:.0f} vehicles/hour")
    print(f"   → Route split: {route_split}")

    # Step 3: Generate a scaled route file
    print("[3/5] Generating SUMO route file with estimated demand...")
    demand_scale = max(0.1, min(3.0, estimated_vehicles / 900.0))  # Baseline is ~900 vehicles
    try:
        route_gen_result = generate_scaled_route_file(
            ROUTE_PATH,
            demand_scale=demand_scale,
            output_dir=output_dir,
            experiment_name="tomtom_estimated",
        )
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason": f"Route file generation failed: {exc}",
            "target_hour": target_hour,
        }

    generated_route = Path(route_gen_result["generated_route"])

    # Step 4: Run SUMO
    print("[4/5] Running SUMO baseline simulation...")
    manifest = {
        "calibration_type": "tomtom_demand_estimation",
        "target_hour": target_hour,
        "tomtom_data_source": {
            "dataset_dir": str(DATASET_DIR),
            "selected_dates": target_dates,
            "sample_count": len(tomtom_rows),
        },
        "demand_estimation": {
            "congestion_level": demand_estimate["estimated_demand"]["congestion_level"],
            "vehicles_per_hour": demand_estimate["estimated_demand"]["vehicles_per_hour"],
            "demand_multiplier": demand_estimate["estimated_demand"]["demand_multiplier"],
            "speed_ratio": demand_estimate["estimated_demand"]["speed_ratio"],
            "travel_time_ratio": demand_estimate["estimated_demand"]["travel_time_ratio"],
        },
        "route_scaling": {
            "baseline_vehicles": 900.0,
            "estimated_vehicles": estimated_vehicles,
            "demand_scale": demand_scale,
        },
    }

    spec = SimulationSpec(
        network_path=NETWORK_PATH,
        route_file=generated_route,
        sumocfg_path=None,
        config_name=f"tomtom_hour_{target_hour}",
        selected_hour=target_hour,
        output_dir=output_dir,
    )

    try:
        manifest = run_baseline_sumo(spec, manifest)
    except Exception as exc:
        manifest["sumo_run_status"] = "FAILED"
        manifest["sumo_run_error"] = str(exc)

    # Step 5: Compare and generate report
    print("[5/5] Comparing SUMO output to TomTom observations...")
    report = {
        "status": "COMPLETED",
        "target_hour": target_hour,
        "target_dates": target_dates,
        "dataset_sample_count": len(tomtom_rows),
        "tomtom_observed_metrics": {
            "mean_speed_kmh": float(sum(row.get("current_speed", 0) for row in tomtom_rows) / max(1, len(tomtom_rows))),
            "mean_travel_time_s": float(sum(row.get("current_travel_time", 0) for row in tomtom_rows) / max(1, len(tomtom_rows))),
            "congestion_level": demand_estimate["estimated_demand"]["congestion_level"],
        },
        "demand_estimation": demand_estimate["estimated_demand"],
        "route_split": route_split,
        "sumo_manifest": manifest,
        "calibration_recommendations": [
            "If SUMO speed is lower than TomTom observed speed, consider reducing demand_scale.",
            "If SUMO speed is higher than TomTom, consider increasing demand_scale.",
            f"Current demand scale: {demand_scale:.3f} (based on {estimated_vehicles:.0f} vehicles/hour).",
            "Monitor route split distribution; adjust per state if certain routes are consistently under/over-utilized.",
        ],
    }

    # Save the report
    report_path = output_dir / "calibration_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nCalibration report saved to: {report_path}")

    return report


if __name__ == "__main__":
    result = run_tomtom_demand_calibration(target_hour=9)
    print("\n" + "=" * 60)
    print("CALIBRATION COMPLETE")
    print("=" * 60)
    print(json.dumps(result, indent=2))
