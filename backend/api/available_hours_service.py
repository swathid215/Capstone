"""Service to query available hours from TomTom data with traffic metrics."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any

from api.traffic_twin_service import load_states

# Known TomTom corridor area (Kathriguppe, Bangalore)
TOMTOM_CORRIDOR_CENTER = (12.924135, 77.55614)
TOMTOM_SEARCH_RADIUS_KM = 2.0  # Corridor data is relevant within ~2km


def _haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two lat/lon points."""
    R = 6371  # Earth radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def _corridor_in_tomtom_area(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> bool:
    """Check if a corridor (start→end) is close to the TomTom data area."""
    center_lat, center_lon = TOMTOM_CORRIDOR_CENTER
    
    # Check distance from corridor midpoint to TomTom center
    mid_lat = (start_lat + end_lat) / 2
    mid_lon = (start_lon + end_lon) / 2
    
    dist = _haversine_distance_km(mid_lat, mid_lon, center_lat, center_lon)
    return dist <= TOMTOM_SEARCH_RADIUS_KM


def get_available_hours_for_corridor(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
) -> dict[str, Any]:
    """
    Get available TomTom hours for a specific corridor.

    Args:
        start_lat, start_lon: Origin coordinates
        end_lat, end_lon: Destination coordinates

    Returns:
        Dict with:
        - in_tomtom_area: bool - whether corridor is in TomTom data area
        - hours: list of hour dicts (empty if not in area)
        - message: str - explanation
    """
    if not _corridor_in_tomtom_area(start_lat, start_lon, end_lat, end_lon):
        return {
            "in_tomtom_area": False,
            "hours": [],
            "message": f"Corridor is {_haversine_distance_km((start_lat+end_lat)/2, (start_lon+end_lon)/2, *TOMTOM_CORRIDOR_CENTER):.1f} km from TomTom data area. Using simulation defaults.",
            "start": {"lat": start_lat, "lon": start_lon},
            "end": {"lat": end_lat, "lon": end_lon},
        }

    states = load_states()
    if not states:
        return {
            "in_tomtom_area": True,
            "hours": [],
            "message": "TomTom data available for this area, but no observations found.",
        }

    # Group by hour
    hours_data: dict[int, list[dict[str, Any]]] = defaultdict(list)
    dates_by_hour: dict[int, set[str]] = defaultdict(set)

    for state in states:
        try:
            dt = datetime.fromisoformat(state["timestamp"].replace("Z", "+00:00"))
            hour = dt.hour
            date_str = dt.date().isoformat()

            hours_data[hour].append(state)
            dates_by_hour[hour].add(date_str)
        except (ValueError, TypeError):
            continue

    # Compute aggregate metrics per hour
    result = []
    for hour in range(24):
        if hour not in hours_data or not hours_data[hour]:
            continue

        hour_states = hours_data[hour]
        speeds = [
            state.get("current_speed", 0.0)
            for state in hour_states
            if isinstance(state.get("current_speed"), (int, float))
        ]
        travel_times = [
            state.get("current_travel_time", 0.0)
            for state in hour_states
            if isinstance(state.get("current_travel_time"), (int, float))
        ]
        speed_ratios = [
            state.get("speed_ratio", 0.0)
            for state in hour_states
            if isinstance(state.get("speed_ratio"), (int, float))
        ]
        congestion_states = [
            state.get("congestion_state", "MODERATE")
            for state in hour_states
        ]

        mean_speed = sum(speeds) / len(speeds) if speeds else 0.0
        mean_travel_time = sum(travel_times) / len(travel_times) if travel_times else 0.0
        mean_speed_ratio = sum(speed_ratios) / len(speed_ratios) if speed_ratios else 0.0

        # Most common congestion state
        state_counts = {}
        for cs in congestion_states:
            state_counts[cs] = state_counts.get(cs, 0) + 1
        most_common_state = max(state_counts, key=state_counts.get) if state_counts else "MODERATE"

        result.append({
            "hour": hour,
            "hour_label": f"{hour:02d}:00",
            "congestion_level": most_common_state,
            "mean_speed_kmh": round(mean_speed, 1),
            "mean_travel_time_s": round(mean_travel_time, 1),
            "speed_ratio": round(mean_speed_ratio, 3),
            "sample_count": len(hour_states),
            "dates_available": sorted(list(dates_by_hour.get(hour, []))),
        })

    return {
        "in_tomtom_area": True,
        "hours": sorted(result, key=lambda x: x["hour"]),
        "message": f"TomTom data available for this corridor: {len(result)} hours with observations",
        "corridor": {
            "start": {"lat": start_lat, "lon": start_lon},
            "end": {"lat": end_lat, "lon": end_lon},
        },
    }
