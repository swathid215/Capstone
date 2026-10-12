"""Convert frontend wizard payloads to/from backend pipeline formats."""

from typing import Optional
from pathlib import Path

LANE_WIDTH_M = 3.5
NEW_ALIGNMENT_TYPES = {"flyover", "tunnel"}
SUPPORTED_INFRA_TYPES = NEW_ALIGNMENT_TYPES | {"road_block", "widening"}


def wizard_to_phase34_payload(request: dict) -> dict:
    route = request.get("route", {})
    start = route.get("start", {})
    end = route.get("end", {})
    default_intervention = request.get("intervention", {}) or {}
    interventions = request.get("interventions") or []
    if not interventions and default_intervention:
        interventions = [default_intervention]
    scenario = request.get("scenario", {})
    options = request.get("options", {})

    if not interventions:
        interventions = [default_intervention]

    proposals = []
    for intervention in interventions:
        coords = intervention.get("geometry", {}).get("coordinates")
        if not coords and start and end:
            coords = [
                [float(start["lon"]), float(start["lat"])],
                [float(end["lon"]), float(end["lat"])],
            ]

        mid_lat = (float(start.get("lat", 0)) + float(end.get("lat", 0))) / 2
        mid_lon = (float(start.get("lon", 0)) + float(end.get("lon", 0))) / 2

        infra_labels = {
            "flyover": "Flyover",
            "tunnel": "Tunnel",
            "road": "New Road",
            "widening": "Widening",
            "road_block": "Road Block",
        }
        infra_key = intervention.get("infra_type", "flyover")
        if intervention.get("type") == "optimize":
            infra_label = "Route Optimization"
        else:
            infra_label = infra_labels.get(infra_key, "New Road")

        road_width = float(intervention.get("road_width_m", 8))
        if options.get("widening"):
            road_width = min(24.0, road_width + 4.0)

        proposals.append({
            "place_name": scenario.get("place_name", "Kathriguppe, Bangalore, India"),
            "point": [mid_lat, mid_lon],
            "dist_m": int(scenario.get("dist_m", 5000)),
            "infra_type": infra_label,
            "road_width_m": road_width,
            "is_oneway": bool(intervention.get("is_oneway", False)),
            "geometry_type": intervention.get("geometry", {}).get("type", "LineString"),
            "smart_analysis": bool(options.get("smart_analysis", True)),
            "coordinates": coords or [],
            "item_id": intervention.get("id", ""),
            "name": intervention.get("name") or infra_label,
            "infra_key": infra_key,
            "is_main": bool(intervention.get("is_main", False)),
            # Widening adds lanes to existing roads instead of new edges; one
            # extra lane per ~3.5 m of added width.
            "extra_lanes": max(1, round(road_width / LANE_WIDTH_M))
            if infra_key == "widening"
            else 0,
        })

    # In a package, Route 4 is built through the main structure and the other
    # items act as connectors or widening around it. The user can mark the
    # main item; otherwise the first flyover/tunnel wins, then the first road.
    def pick_main():
        for predicate in (
            lambda p: p["is_main"] and p["infra_key"] in NEW_ALIGNMENT_TYPES,
            lambda p: p["infra_key"] in NEW_ALIGNMENT_TYPES,
            lambda p: p["infra_key"] in NEW_ALIGNMENT_TYPES,
        ):
            for index, proposal in enumerate(proposals):
                if predicate(proposal):
                    return index
        return 0

    main_index = pick_main()
    if main_index:
        proposals.insert(0, proposals.pop(main_index))
    for index, proposal in enumerate(proposals):
        if proposal["infra_key"] == "widening":
            proposal["role"] = "widening"
        elif proposal["infra_key"] == "road_block":
            proposal["role"] = "road_block"
        else:
            proposal["role"] = "main" if index == 0 else "connector"

    return {
        "scenario_metadata": {
            "name": request.get("report_name")
            or f"{start.get('name', 'Start')} to {end.get('name', 'End')}",
            "description": "Generated from UIDT Traffic Wizard",
        },
        "proposals": proposals,
        "proposal": proposals[0] if proposals else {},
    }


def _primary_simulation(simulation: dict) -> dict:
    scenarios = simulation.get("scenarios") or []
    if scenarios:
        primary_rate = simulation.get(
            "primary_adoption_rate_pct",
            simulation.get("adoption_rate_pct"),
        )
        try:
            primary_rate = float(primary_rate)
        except (TypeError, ValueError):
            primary_rate = None
        return next(
            (
                scenario
                for scenario in scenarios
                if primary_rate is not None
                and float(scenario.get("adoption_rate_pct", -1)) == primary_rate
            ),
            scenarios[-1],
        )
    return simulation


def _simulation_vehicle_count(simulation: dict, demand_data: dict) -> int:
    before = simulation.get("before") or {}
    candidates = (
        simulation.get("route_file_window_vehicle_count"),
        simulation.get("route_file_vehicle_count"),
        before.get("matched_vehicle_count"),
        before.get("throughput"),
        demand_data.get("vehicle_count_in_target_hour"),
        demand_data.get("vehicle_count"),
    )
    for value in candidates:
        try:
            if value is not None and float(value) > 0:
                return int(float(value))
        except (TypeError, ValueError):
            continue
    return 0


def _simulation_route_rows(simulation: dict) -> list[dict]:
    rows = []
    for route in _primary_simulation(simulation).get("routes", []) or []:
        metrics = route.get("metrics") or {}
        assigned = route.get("assigned_vehicle_count")
        if assigned is None:
            assigned = metrics.get("matched_vehicle_count", 0)
        travel_time_sec = metrics.get("avg_travel_time_sec")
        rows.append(
            {
                "name": f"SUMO Route {route.get('id', '?')}",
                "vehicles": int(float(assigned or 0)),
                "throughput": int(float(metrics.get("throughput", 0) or 0)),
                "time": (
                    f"{float(travel_time_sec) / 60:.2f} min"
                    if travel_time_sec is not None and float(travel_time_sec) > 0
                    else "—"
                ),
                "speed": metrics.get("avg_speed_kmh"),
                "congestion": metrics.get("avg_congestion_ratio"),
            }
        )
    return rows


def build_analyze_response(
    request: dict,
    phase34_output: dict,
    demand_data: dict,
    phase2_result: dict,
    simulation_result: Optional[dict] = None,
) -> dict:
    route = request.get("route", {})
    start = route.get("start", {})
    end = route.get("end", {})
    intervention = request.get("intervention", {})
    options = request.get("options", {})

    impact_report = phase34_output.get("impact_report") or {}
    feasibility = phase34_output.get("feasibility", {})
    smart = phase34_output.get("smart_recommendation") or {}
    artifacts = phase34_output.get("artifacts") or {}
    phase34_items = phase34_output.get("items") or []
    is_widening_only = bool(phase34_items) and all(
        item.get("infra_key") == "widening" for item in phase34_items
    )
    is_road_block_only = bool(phase34_items) and all(
        item.get("infra_key") == "road_block" for item in phase34_items
    )
    is_road_modification_only = bool(phase34_items) and all(
        item.get("infra_key") in {"widening", "road_block"}
        for item in phase34_items
    )

    artifact_urls = {}
    for key, path in artifacts.items():
        if path:
            # Artifacts are stored using Windows paths in this environment.
            # Path.name correctly extracts the filename from either slash
            # style before building the API URL.
            filename = Path(path).name
            artifact_urls[key] = f"/api/artifacts/{filename}"

    simulation = dict(simulation_result or {})
    time_reduction = simulation.get("time_reduction_pct")
    congestion_reduction = simulation.get("congestion_reduction_pct")
    if time_reduction is not None:
        simulation["time_reduction_pct"] = round(float(time_reduction), 1)
    if congestion_reduction is not None:
        simulation["congestion_reduction_pct"] = round(float(congestion_reduction), 1)

    total_vehicle_count = _simulation_vehicle_count(simulation, demand_data)
    route_rows = _simulation_route_rows(simulation)
    route_file = simulation.get("route_file") or demand_data.get("route_file")

    cautions = []
    if not feasibility.get("is_feasible"):
        cautions.append(
            f"Building collisions detected ({feasibility.get('collision_count', 0)}). "
            "Route revision required before construction."
        )
    if smart.get("risk_factors"):
        cautions.extend(smart["risk_factors"])
    if phase2_result.get("status") == "heuristic_preview":
        cautions.append(phase2_result.get("note", "Phase 2 estimates applied."))

    # Derived Environmental & Economic Metrics
    primary_sim = _primary_simulation(simulation)
    before_time = primary_sim.get("avg_travel_time_before_min")
    after_time = primary_sim.get("avg_travel_time_after_min")
    
    time_saved_min = 0.0
    if before_time is not None and after_time is not None:
        time_saved_min = max(0.0, float(before_time) - float(after_time))
    
    daily_vehicle_count = max(total_vehicle_count, primary_sim.get("eligible_vehicle_count") or 0)
    total_hours_saved_daily = (time_saved_min / 60.0) * daily_vehicle_count
    if primary_sim.get("network_vehicle_hours_delta") is not None:
        # Road modifications measure every vehicle in the window directly, so
        # use the paired network-wide change instead of extrapolating the
        # corridor saving to all vehicles.
        total_hours_saved_daily = max(0.0, -float(primary_sim["network_vehicle_hours_delta"]))

    co2_saved_kg_daily = round(total_hours_saved_daily * 1.2, 1)
    fuel_saved_liters_daily = round(total_hours_saved_daily * 0.8, 1)
    economic_value_inr_daily = round(total_hours_saved_daily * 150)

    environmental_impact = {
        "time_saved_per_vehicle_min": round(time_saved_min, 1),
        "total_hours_saved_daily": round(total_hours_saved_daily, 1),
        "co2_saved_kg_daily": co2_saved_kg_daily,
        "fuel_saved_liters_daily": fuel_saved_liters_daily,
        "economic_value_inr_daily": economic_value_inr_daily,
    }

    return {
        "status": "success",
        "feasibility": feasibility,
        "impact": {
            "original_edges": impact_report.get("Original Network Edges"),
            "updated_edges": impact_report.get("Updated Network Edges"),
            "new_connections": impact_report.get("New Connections Spliced"),
            "environmental": environmental_impact,
        },
        "smart_recommendation": smart,
        "demand": {
            "scenario_metadata": demand_data.get("scenario_metadata", {}),
            "source": "SUMO route file",
            "route_file": route_file,
            "total_vehicle_count": total_vehicle_count,
            "target_hour_vehicle_count": demand_data.get("vehicle_count_in_target_hour", 0),
            "top_corridors": [],
        },
        "phase2": phase2_result,
        "simulation": simulation,
        "artifacts": artifact_urls,
        "report": {
            "reportName": f"Intervention: {start.get('name', 'Start')} - {end.get('name', 'End')}",
            "collaborators": ["UIDT System"],
            "fromPlace": start.get("name", "Unknown"),
            "toPlace": end.get("name", "Unknown"),
            "totalVehicles": total_vehicle_count,
            "vehicleCountLabel": "Vehicles departing in the selected SUMO window",
            "routeFileSource": simulation.get("route_file_source") or f"Source: {route_file}",
            "routeFile": route_file,
            "solutions": {
                "type": intervention.get("type", "infrastructure"),
                "infraType": intervention.get("infra_type"),
                "widening": options.get("widening", False),
                "signal": options.get("smart_signals", False),
            },
            "projectMode": (request.get("scenario") or {}).get("project_mode", "package"),
            "isWideningOnly": is_widening_only,
            "isRoadBlockOnly": is_road_block_only,
            "isRoadModificationOnly": is_road_modification_only,
            "packageItems": phase34_output.get("items", []),
            "feasibility": feasibility,
            "impact": {
                "original_edges": impact_report.get("Original Network Edges"),
                "updated_edges": impact_report.get("Updated Network Edges"),
                "new_connections": impact_report.get("New Connections Spliced"),
                "environmental": environmental_impact,
            },
            "simulation": simulation,
            "simulationScenarios": simulation.get("scenarios", []),
            "smartRecommendation": smart,
            "hourlyTraffic": simulation.get("hourly_traffic", [])
            if simulation.get("hourly_traffic_source") == "simulation"
            else [],
            "adoptionRate": simulation.get(
                "actual_adoption_rate_pct",
                simulation.get("adoption_rate_pct"),
            ),
            "metrics": {
                "trafficReduction": round(float(congestion_reduction), 1)
                if congestion_reduction is not None
                else None,
                "timeSaved": round(float(time_reduction), 1)
                if time_reduction is not None
                else None,
                "diversion": simulation.get(
                    "actual_adoption_rate_pct",
                    simulation.get("adoption_rate_pct"),
                ),
            },
            "environmental": environmental_impact,
            "cautions": cautions,
            "phase2": phase2_result,
            "routes": route_rows,
        },
        "pipeline_output": phase34_output,
    }
