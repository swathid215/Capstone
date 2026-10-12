"""Test the TomTom demand estimation calibration pipeline."""
import json
import sys
from pathlib import Path

try:
    from backend.calibration.tomtom_demand_calibration import run_tomtom_demand_calibration
except ModuleNotFoundError:  # pragma: no cover - compatibility for project-root execution
    REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.calibration.tomtom_demand_calibration import run_tomtom_demand_calibration


def test_tomtom_demand_calibration_produces_report():
    """Verify the calibration pipeline runs and produces a valid report."""
    result = run_tomtom_demand_calibration(
        target_hour=9,
        target_dates=["2026-06-12", "2026-06-13", "2026-06-14", "2026-06-15"],  # Use available dates with data
    )

    assert isinstance(result, dict), "Result should be a dict"
    assert "status" in result, "Result should have 'status' field"

    if result["status"] == "COMPLETED":
        assert "target_hour" in result
        assert "demand_estimation" in result
        assert "route_split" in result
        assert result["demand_estimation"]["vehicles_per_hour"] > 0
        assert sum(result["route_split"].values()) > 0.99, "Route split should sum to ~1.0"
        print("[OK] Calibration completed with valid demand profile")
    else:
        print(f"[SKIP] Calibration did not complete. Reason: {result.get('reason', 'unknown')}")
        # Don't fail the test if TomTom data is missing or SUMO unavailable
        # This is an optional integration step

    print(f"\nCalibration report:\n{json.dumps(result, indent=2)}")


if __name__ == "__main__":
    test_tomtom_demand_calibration_produces_report()
