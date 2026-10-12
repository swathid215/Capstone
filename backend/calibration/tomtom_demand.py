from __future__ import annotations

from typing import Any


def estimate_tomtom_demand(states: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert observed TomTom corridor states into a calibrated demand profile.

    This is intentionally simple and defensible: it uses observed speed/travel-time
    ratios to estimate a demand multiplier and splits the total demand across
    candidate routes. It does not treat TomTom values as direct SUMO inputs.
    """
    if not states:
        raise ValueError("states must not be empty")

    latest = states[-1]
    current_speed = float(latest.get("current_speed", 0.0) or 0.0)
    free_flow_speed = float(latest.get("free_flow_speed", max(current_speed, 1.0)) or max(current_speed, 1.0))
    current_travel_time = float(latest.get("current_travel_time", 0.0) or 0.0)
    free_flow_travel_time = float(latest.get("free_flow_travel_time", max(current_travel_time, 1.0)) or max(current_travel_time, 1.0))

    speed_ratio = current_speed / free_flow_speed if free_flow_speed > 0 else 0.0
    travel_time_ratio = current_travel_time / free_flow_travel_time if free_flow_travel_time > 0 else 0.0
    delay_ratio = max(0.0, travel_time_ratio - 1.0)

    congestion_state = (latest.get("congestion_state") or "FREE_FLOW").upper()
    if congestion_state not in {"FREE_FLOW", "MODERATE", "CONGESTED", "SEVERE"}:
        if speed_ratio < 0.6:
            congestion_state = "SEVERE"
        elif speed_ratio < 0.8:
            congestion_state = "CONGESTED"
        elif speed_ratio < 0.92:
            congestion_state = "MODERATE"
        else:
            congestion_state = "FREE_FLOW"

    demand_multiplier = 1.0 + max(0.0, 1.0 - speed_ratio) * 2.2 + delay_ratio * 0.9
    vehicles_per_hour = max(200.0, min(2500.0, 900.0 * demand_multiplier))

    route_split = {
        "route_1": 0.42,
        "route_2": 0.33,
        "route_3": 0.17,
        "route_4": 0.08,
    }

    if congestion_state == "FREE_FLOW":
        route_split = {"route_1": 0.48, "route_2": 0.30, "route_3": 0.15, "route_4": 0.07}
    elif congestion_state == "MODERATE":
        route_split = {"route_1": 0.42, "route_2": 0.33, "route_3": 0.17, "route_4": 0.08}
    elif congestion_state == "CONGESTED":
        route_split = {"route_1": 0.33, "route_2": 0.31, "route_3": 0.22, "route_4": 0.14}
    elif congestion_state == "SEVERE":
        route_split = {"route_1": 0.25, "route_2": 0.28, "route_3": 0.27, "route_4": 0.20}

    total = sum(route_split.values())
    normalized = {key: value / total for key, value in route_split.items()}

    return {
        "source_state": latest,
        "estimated_demand": {
            "congestion_level": congestion_state,
            "speed_ratio": round(speed_ratio, 3),
            "travel_time_ratio": round(travel_time_ratio, 3),
            "delay_ratio": round(delay_ratio, 3),
            "demand_multiplier": round(demand_multiplier, 3),
            "vehicles_per_hour": round(vehicles_per_hour, 1),
        },
        "route_split": {key: round(value, 4) for key, value in normalized.items()},
    }
