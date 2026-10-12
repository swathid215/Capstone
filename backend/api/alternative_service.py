"""Generate deterministic infrastructure alternatives for a selected corridor."""

from __future__ import annotations

from math import cos, hypot, radians
from typing import Callable, Iterable

EARTH_METERS_PER_DEGREE = 111_320.0
MAX_ENDPOINT_SHIFT_M = 750.0
SUPPORTED_INFRASTRUCTURE = {
    "road": {
        "label": "New Road",
        "road_width_m": 8.0,
        "is_oneway": False,
        "construction_cost_index": 1.0,
    },
    "flyover": {
        "label": "Flyover",
        "road_width_m": 8.0,
        "is_oneway": False,
        "construction_cost_index": 2.0,
    },
    "tunnel": {
        "label": "Tunnel",
        "road_width_m": 8.0,
        "is_oneway": False,
        "construction_cost_index": 3.0,
    },
}


def _location_coordinates(location: dict) -> tuple[float, float]:
    latitude = float(location["lat"])
    longitude = float(location["lon"])
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    return latitude, longitude


def _normalise_infrastructure_types(
    infrastructure_types: Iterable[str] | None,
) -> list[str]:
    requested = list(infrastructure_types or SUPPORTED_INFRASTRUCTURE)
    normalised = []
    for infrastructure_type in requested:
        key = str(infrastructure_type).strip().lower()
        if key not in SUPPORTED_INFRASTRUCTURE:
            raise ValueError(
                f"Unsupported infrastructure type '{infrastructure_type}'. "
                f"Choose from {sorted(SUPPORTED_INFRASTRUCTURE)}."
            )
        if key not in normalised:
            normalised.append(key)
    if not normalised:
        raise ValueError("At least one infrastructure type is required.")
    return normalised


def _offset_location(
    latitude: float,
    longitude: float,
    east_m: float,
    north_m: float,
) -> dict:
    latitude_delta = north_m / EARTH_METERS_PER_DEGREE
    longitude_scale = EARTH_METERS_PER_DEGREE * max(cos(radians(latitude)), 0.01)
    longitude_delta = east_m / longitude_scale
    return {
        "lat": round(latitude + latitude_delta, 7),
        "lon": round(longitude + longitude_delta, 7),
    }


def _candidate(
    candidate_id: str,
    candidate_type: str,
    infrastructure_type: str,
    start: dict,
    end: dict,
    metadata: dict | None = None,
) -> dict:
    specification = SUPPORTED_INFRASTRUCTURE[infrastructure_type]
    start_point = {"lat": start["lat"], "lon": start["lon"]}
    end_point = {"lat": end["lat"], "lon": end["lon"]}
    return {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "infrastructure": {
            "type": infrastructure_type,
            **specification,
        },
        "start": start_point,
        "end": end_point,
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [start_point["lon"], start_point["lat"]],
                [end_point["lon"], end_point["lat"]],
            ],
        },
        "metadata": metadata or {},
    }


def generate_same_endpoint_candidates(
    origin: dict,
    destination: dict,
    infrastructure_types: Iterable[str] | None = None,
) -> list[dict]:
    origin_lat, origin_lon = _location_coordinates(origin)
    destination_lat, destination_lon = _location_coordinates(destination)
    selected_types = _normalise_infrastructure_types(infrastructure_types)

    start = {"lat": origin_lat, "lon": origin_lon}
    end = {"lat": destination_lat, "lon": destination_lon}
    return [
        _candidate(
            candidate_id=f"same_endpoints_{infrastructure_type}",
            candidate_type="same_endpoints",
            infrastructure_type=infrastructure_type,
            start=start,
            end=end,
            metadata={"endpoint_shift_m": 0.0},
        )
        for infrastructure_type in selected_types
    ]


def generate_nearby_connection_candidates(
    origin: dict,
    destination: dict,
    infrastructure_types: Iterable[str] | None = None,
    radii_m: Iterable[float] = (250.0, 500.0),
) -> list[dict]:
    origin_lat, origin_lon = _location_coordinates(origin)
    destination_lat, destination_lon = _location_coordinates(destination)
    selected_types = _normalise_infrastructure_types(infrastructure_types)

    latitude_for_scale = (origin_lat + destination_lat) / 2.0
    east_delta_m = (
        (destination_lon - origin_lon)
        * EARTH_METERS_PER_DEGREE
        * max(cos(radians(latitude_for_scale)), 0.01)
    )
    north_delta_m = (destination_lat - origin_lat) * EARTH_METERS_PER_DEGREE
    corridor_length_m = hypot(east_delta_m, north_delta_m)
    if corridor_length_m == 0:
        raise ValueError("Origin and destination must be different points.")

    perpendicular_east = -north_delta_m / corridor_length_m
    perpendicular_north = east_delta_m / corridor_length_m
    candidates = []

    for radius in radii_m:
        shift_m = float(radius)
        if shift_m <= 0 or shift_m > MAX_ENDPOINT_SHIFT_M:
            raise ValueError(
                f"Nearby endpoint shifts must be greater than 0 and at most "
                f"{MAX_ENDPOINT_SHIFT_M:g} metres."
            )
        for side_label, side in (("left", -1.0), ("right", 1.0)):
            start = _offset_location(
                origin_lat,
                origin_lon,
                perpendicular_east * shift_m * side,
                perpendicular_north * shift_m * side,
            )
            end = _offset_location(
                destination_lat,
                destination_lon,
                perpendicular_east * shift_m * side,
                perpendicular_north * shift_m * side,
            )
            for infrastructure_type in selected_types:
                candidates.append(
                    _candidate(
                        candidate_id=(
                            f"nearby_{int(shift_m)}m_{side_label}_"
                            f"{infrastructure_type}"
                        ),
                        candidate_type="nearby_endpoints",
                        infrastructure_type=infrastructure_type,
                        start=start,
                        end=end,
                        metadata={
                            "endpoint_shift_m": shift_m,
                            "side": side_label,
                        },
                    )
                )

    return candidates


def generate_alternative_candidates(
    origin: dict,
    destination: dict,
    infrastructure_types: Iterable[str] | None = None,
    nearby_radii_m: Iterable[float] = (250.0, 500.0),
) -> list[dict]:
    selected_types = _normalise_infrastructure_types(infrastructure_types)
    return generate_same_endpoint_candidates(
        origin,
        destination,
        selected_types,
    ) + generate_nearby_connection_candidates(
        origin,
        destination,
        selected_types,
        nearby_radii_m,
    )


def validate_candidate_constraints(candidate: dict) -> None:
    infrastructure = candidate.get("infrastructure", {})
    infrastructure_type = infrastructure.get("type")
    if infrastructure_type not in SUPPORTED_INFRASTRUCTURE:
        raise ValueError("Candidate has an unsupported infrastructure type.")

    geometry = candidate.get("geometry", {})
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") != "LineString" or len(coordinates) < 2:
        raise ValueError("Candidate geometry must be a LineString with two points.")

    endpoint_shift_m = float(candidate.get("metadata", {}).get("endpoint_shift_m", 0))
    if endpoint_shift_m < 0 or endpoint_shift_m > MAX_ENDPOINT_SHIFT_M:
        raise ValueError("Candidate endpoint shift exceeds the allowed limit.")


def rank_candidate_evaluations(evaluations: list[dict]) -> list[dict]:
    def ranking_key(evaluation: dict) -> tuple:
        feasibility = evaluation.get("feasibility", {})
        route_validation = evaluation.get("route_validation", {})
        simulation = evaluation.get("simulation", {})
        candidate = evaluation.get("candidate", {})
        infrastructure_type = candidate.get("infrastructure", {}).get("type", "road")
        metadata = candidate.get("metadata", {})

        is_feasible = bool(feasibility.get("is_feasible"))
        has_route4 = bool(route_validation.get("valid"))
        collision_count = int(feasibility.get("collision_count", 0) or 0)
        time_reduction = float(simulation.get("time_reduction_pct", 0) or 0)
        congestion_reduction = float(
            simulation.get("congestion_reduction_pct", 0) or 0
        )
        impact_score = (time_reduction + congestion_reduction) / 2.0
        endpoint_shift_m = float(metadata.get("endpoint_shift_m", 0) or 0)
        route4_length_m = float(route_validation.get("route4_length_m", 0) or 0)
        cost_index = SUPPORTED_INFRASTRUCTURE.get(
            infrastructure_type,
            {"construction_cost_index": 99.0},
        ).get("construction_cost_index", 99.0)

        return (
            not is_feasible,
            not has_route4,
            collision_count,
            -impact_score,
            cost_index,
            endpoint_shift_m,
            route4_length_m,
        )

    ranked = []
    for rank, evaluation in enumerate(sorted(evaluations, key=ranking_key), start=1):
        item = dict(evaluation)
        feasibility = item.get("feasibility", {})
        route_validation = item.get("route_validation", {})
        item["rank"] = rank
        item["ranking_eligible"] = bool(
            feasibility.get("is_feasible") and route_validation.get("valid")
        )
        ranked.append(item)
    return ranked


def evaluate_candidate(
    candidate: dict,
    feasibility_runner: Callable[[dict], tuple[dict, object]],
    route_validator: Callable[[dict, object], dict],
) -> dict:
    validate_candidate_constraints(candidate)
    evaluation = {
        "candidate": candidate,
        "simulation": {
            "status": "deferred",
            "reason": "SUMO runs after a candidate is selected.",
        },
    }

    try:
        phase34_output, modified_graph = feasibility_runner(candidate)
    except Exception as exc:
        evaluation["status"] = "error"
        evaluation["error"] = str(exc)
        evaluation["feasibility"] = {"is_feasible": False, "collision_count": 0}
        evaluation["route_validation"] = {
            "valid": False,
            "reason": "Feasibility evaluation failed.",
        }
        return evaluation

    feasibility = phase34_output.get("feasibility", {})
    evaluation["feasibility"] = feasibility
    if not feasibility.get("is_feasible") or modified_graph is None:
        evaluation["status"] = "infeasible"
        evaluation["route_validation"] = {
            "valid": False,
            "reason": "Infrastructure feasibility failed.",
        }
        return evaluation

    try:
        route_info = route_validator(candidate, modified_graph)
        route4 = route_info.get("route4") or {}
        evaluation["route_validation"] = {
            "valid": bool(route_info.get("status") == "valid" and route4),
            "reason": "Route 4 generated successfully.",
            "baseline_route_count": route_info.get("baseline_route_count", 0),
            "modified_route_count": route_info.get("modified_route_count", 0),
            "route4_length_m": route4.get("length_m", 0),
            "routes": route_info.get("routes", []),
        }
        evaluation["status"] = (
            "feasible" if evaluation["route_validation"]["valid"] else "no_route4"
        )
    except Exception as exc:
        evaluation["status"] = "no_route4"
        evaluation["route_validation"] = {
            "valid": False,
            "reason": str(exc),
        }

    return evaluation


def evaluate_and_rank_candidates(
    candidates: list[dict],
    feasibility_runner: Callable[[dict], tuple[dict, object]],
    route_validator: Callable[[dict, object], dict],
) -> list[dict]:
    evaluations = [
        evaluate_candidate(candidate, feasibility_runner, route_validator)
        for candidate in candidates
    ]
    return rank_candidate_evaluations(evaluations)
