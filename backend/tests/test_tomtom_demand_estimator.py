try:
    from backend.calibration.tomtom_demand import estimate_tomtom_demand
except ModuleNotFoundError:  # pragma: no cover - compatibility for backend-local execution
    from calibration.tomtom_demand import estimate_tomtom_demand


def test_estimate_tomtom_demand_returns_reasonable_baseline_and_route_split():
    states = [
        {
            "timestamp": "2026-06-18T08:00:00",
            "congestion_state": "MODERATE",
            "current_speed": 30,
            "free_flow_speed": 36,
            "current_travel_time": 2320,
            "free_flow_travel_time": 1933,
        },
        {
            "timestamp": "2026-06-18T09:00:00",
            "congestion_state": "CONGESTED",
            "current_speed": 22,
            "free_flow_speed": 36,
            "current_travel_time": 2680,
            "free_flow_travel_time": 1933,
        },
    ]

    result = estimate_tomtom_demand(states)

    assert "estimated_demand" in result
    assert "route_split" in result
    assert result["estimated_demand"]["vehicles_per_hour"] > 0
    assert sum(result["route_split"].values()) > 0.99
    assert result["estimated_demand"]["congestion_level"].upper() in {"MODERATE", "CONGESTED"}
