"""SUMO-based traffic simulation with NetworkX fallback.

All simulation demand comes from the committed route file in ``auto_tool``.
The older demand-scaling helpers remain below as disconnected compatibility
code, but are not part of any API execution path.
"""
from __future__ import annotations

import networkx as nx
from collections import defaultdict
from itertools import combinations, islice

import json
import logging
import math
import os
import random
import re
import statistics
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTO_TOOL_DIR = PROJECT_ROOT / "auto_tool"
BASELINE_NET = AUTO_TOOL_DIR / "cleaned_network.net.xml"
BASELINE_ROU = AUTO_TOOL_DIR / "simulation.rou.xml"

# Day-of-week calibrated demand. Each file covers the same 07:00-23:00 window
# as BASELINE_ROU (absolute seconds-of-day departs), shaped from real hourly
# incident/delay data per weekday. Falls back to BASELINE_ROU when a day is
# not specified or not recognized, so existing callers are unaffected.
WEEKLY_ROUTES_DIR = AUTO_TOOL_DIR / "weekly_routes"
DAY_ROUTE_FILES: dict[str, Path] = {
    "monday": WEEKLY_ROUTES_DIR / "Monday_routes.rou.xml",
    "tuesday": WEEKLY_ROUTES_DIR / "Tuesday_routes.rou.xml",
    "wednesday": WEEKLY_ROUTES_DIR / "Wednesday_routes.rou.xml",
    "thursday": WEEKLY_ROUTES_DIR / "Thursday_routes.rou.xml",
    "friday": WEEKLY_ROUTES_DIR / "Friday_routes.rou.xml",
    "saturday": WEEKLY_ROUTES_DIR / "Saturday_routes.rou.xml",
    "sunday": WEEKLY_ROUTES_DIR / "Sunday_routes.rou.xml",
}


def _resolve_baseline_route(day_of_week: Optional[str] = None) -> Path:
    """Pick the calibrated route file for a given day of week.

    ``day_of_week`` is matched case-insensitively (e.g. "Monday", "monday").
    Falls back to the static BASELINE_ROU when not provided, unrecognized,
    or the calibrated file is missing on disk -- this keeps every existing
    call site working exactly as before if it doesn't pass a day.
    """
    if day_of_week:
        candidate = DAY_ROUTE_FILES.get(day_of_week.strip().lower())
        if candidate is not None and candidate.exists():
            return candidate
        logger.warning(
            "day_of_week=%r not recognized or file missing; using default baseline route.",
            day_of_week,
        )
    return BASELINE_ROU


PHASE1_DIR = Path(__file__).resolve().parents[1] / "phases" / "phase1_demand"
UBER_CSV = PHASE1_DIR / "bangalore-wards-2019-3-OnlyWeekdays-HourlyAggregate.csv"
WARDS_GEOJSON = PHASE1_DIR / "bangalore_wards.json"

HOURLY_LABELS = [
    "6 AM", "7 AM", "8 AM", "9 AM", "10 AM", "11 AM",
    "12 PM", "5 PM", "6 PM", "7 PM",
]
HOURLY_FACTORS = [0.45, 0.72, 0.92, 1.0, 0.78, 0.55, 0.58, 0.88, 0.96, 0.8]

SIMULATION_SECONDS = 10000
REFERENCE_TRIP_VOLUME = 390.0
MIN_TARGET_VEHICLES = 100
MAX_TARGET_VEHICLES = 6000
MIN_UBER_ROUTE_SUCCESS_RATE = 0.5
STRICT_MIN_ROUTE_EDGE_DIFF = 7
RELAXED_MIN_ROUTE_EDGE_DIFF = 4
MAX_ROUTE_EDGE_DIFF = 20
MIN_ROUTE_SIMILARITY_THRESHOLD = 0.9
# Search a broader route pool so we do not get stuck with only the shortest alley routes.
ROUTE_SEARCH_LIMIT = 60
HEATMAP_CONGESTION_THRESHOLD = 0.05
HEATMAP_MIN_HOTSPOT_LENGTH_M = 50.0
HEATMAP_MIN_SAMPLE_COUNT = 5

# SUMO teleports a vehicle that has been stuck for this many seconds (SUMO's
# default is 300). Set UIDT_SUMO_TIME_TO_TELEPORT=-1 to disable teleporting
# for sensitivity runs; teleports are always counted in the results.
_time_to_teleport_env = os.environ.get("UIDT_SUMO_TIME_TO_TELEPORT", "").strip()
try:
    SUMO_TIME_TO_TELEPORT: Optional[int] = int(_time_to_teleport_env) if _time_to_teleport_env else None
except ValueError:
    SUMO_TIME_TO_TELEPORT = None

# Seed for bootstrap confidence intervals so reports are reproducible.
STATISTICS_RANDOM_SEED = 20240601
BOOTSTRAP_RESAMPLES = 2000


def _arrival_rate_from_demand(demand_data: dict) -> float:
    vehicle_count = demand_data.get("vehicle_count_in_target_hour")
    if vehicle_count is None:
        vehicle_count = demand_data.get("vehicle_count", 0)
    try:
        return round(max(0.0, float(vehicle_count)), 2)
    except (TypeError, ValueError):
        return 0.0

def _find_candidate_routes(
    net,
    start_edge,
    end_edge,
    route_count: int = 3,
):
    """
    Returns up to `route_count` candidate routes.

    Each returned route is a list of SUMO Edge objects.
    """

    start_node = start_edge.getToNode().getID()
    end_node = end_edge.getFromNode().getID()

    # Build lookup from edge id -> edge object
    edge_map = {
        edge.getID(): edge
        for edge in net.getEdges()
        if not edge.getID().startswith(":")
    }

    graph = _build_networkx_graph(net)

    candidate_routes = []

    try:

        node_paths = islice(
            nx.shortest_simple_paths(
                graph,
                start_node,
                end_node,
                # Prefer realistic driving time and road hierarchy over
                # simply counting metres through residential side streets.
                weight="routing_weight",
            ),
            route_count,
        )

        for node_path in node_paths:

            route_edges = [edge_map[start_edge.getID()]]

            for u, v in zip(node_path[:-1], node_path[1:]):

                edge_data = graph.get_edge_data(u, v)

                if edge_data is None:
                    route_edges = []
                    break

                edge_id = edge_data["edge_id"]
                route_edges.append(edge_map[edge_id])

            if route_edges[-1].getID() != end_edge.getID():
                route_edges.append(edge_map[end_edge.getID()])

            if route_edges:
                candidate_routes.append(route_edges)

    except nx.NetworkXNoPath:
        return []

    return _deduplicate_routes(candidate_routes)


def _route_edge_count(route: list) -> int:
    return len(route)


def _route_similarity(route_a: list, route_b: list) -> float:
    ids_a = {edge.getID() for edge in route_a}
    ids_b = {edge.getID() for edge in route_b}

    if not ids_a or not ids_b:
        return 0.0

    intersection = ids_a.intersection(ids_b)
    return len(intersection) / min(len(ids_a), len(ids_b))


def _deduplicate_routes(raw_routes: list[list], similarity_threshold: float = MIN_ROUTE_SIMILARITY_THRESHOLD) -> list[list]:
    unique_routes: list[list] = []
    for route in raw_routes:
        if any(_route_similarity(route, existing) >= similarity_threshold for existing in unique_routes):
            continue
        unique_routes.append(route)
    return unique_routes


def _pairwise_edge_diffs(routes: list[list]) -> list[int]:
    lengths = [_route_edge_count(route) for route in routes]
    diffs = []
    for index, left_length in enumerate(lengths):
        for right_length in lengths[index + 1 :]:
            diffs.append(abs(left_length - right_length))
    return diffs


def _select_routes_with_edge_span(
    raw_routes: list[list],
    route_count: int = 3,
    min_edge_diff: int = STRICT_MIN_ROUTE_EDGE_DIFF,
    max_edge_diff: int = MAX_ROUTE_EDGE_DIFF,
) -> list[list]:
    """Choose a 3-route set with edge-count separation.

    Priority:
    1. All pairwise differences between 7 and 20.
    2. Otherwise return the 3 shortest routes.
    """
    if len(raw_routes) < route_count:
        return raw_routes[:route_count]

    # ``raw_routes`` is already ordered by the routing cost used to discover
    # the paths. Keep that first route as the primary/natural route instead of
    # reordering by edge count, which can promote an alley-heavy detour.
    route_pool = list(raw_routes)

    def matches_exactly(routes: tuple[list, ...], lower_bound: int) -> bool:
        diffs = _pairwise_edge_diffs(list(routes))
        return all(lower_bound <= diff <= max_edge_diff for diff in diffs)

    if route_count == 1:
        return route_pool[:1]

    primary_route = route_pool[0]
    for alternatives in combinations(route_pool[1:], route_count - 1):
        combo = (primary_route, *alternatives)
        if matches_exactly(combo, min_edge_diff):
            return list(combo)

    return route_pool[:route_count]


def _select_routes_with_fallbacks(raw_routes: list[list], route_count: int = 3) -> list[list]:
    strict_routes = _select_routes_with_edge_span(
        raw_routes,
        route_count=route_count,
        min_edge_diff=STRICT_MIN_ROUTE_EDGE_DIFF,
        max_edge_diff=MAX_ROUTE_EDGE_DIFF,
    )
    if strict_routes:
        return strict_routes

    relaxed_routes = _select_routes_with_edge_span(
        raw_routes,
        route_count=route_count,
        min_edge_diff=RELAXED_MIN_ROUTE_EDGE_DIFF,
        max_edge_diff=MAX_ROUTE_EDGE_DIFF,
    )
    if relaxed_routes:
        return relaxed_routes

    return raw_routes[:route_count]


def _build_hourly_traffic(demand_data: dict, improvement_ratio: float) -> list:
    matrix = demand_data.get("demand_matrix", [])
    peak_volume = sum(float(row.get("synthetic_trip_count", 0)) for row in matrix)
    base_peak = max(12, int(peak_volume * 8))
    improvement_ratio = max(0.0, min(0.65, improvement_ratio))

    return [
        {
            "time": label,
            "before": int(base_peak * factor),
            "after": int(base_peak * factor * (1 - improvement_ratio)),
        }
        for label, factor in zip(HOURLY_LABELS, HOURLY_FACTORS)
    ]


def _total_trip_count(demand_data: dict) -> float:
    matrix = demand_data.get("demand_matrix", [])
    return sum(float(row.get("synthetic_trip_count", 0)) for row in matrix)


def _target_vehicle_count(demand_data: dict) -> int:
    total_trips = _total_trip_count(demand_data)
    baseline_count = _baseline_vehicle_count()
    if total_trips <= 0:
        return baseline_count
    scaled = round(baseline_count * total_trips / REFERENCE_TRIP_VOLUME)
    return max(MIN_TARGET_VEHICLES, min(MAX_TARGET_VEHICLES, scaled))


def _baseline_vehicle_count(route_file: Path = BASELINE_ROU) -> int:
    if not route_file.exists():
        return MAX_TARGET_VEHICLES
    text = route_file.read_text(encoding="utf-8", errors="ignore")
    count = len(re.findall(r"<vehicle\b", text))
    return count or MAX_TARGET_VEHICLES


def _route_file_vehicle_count(
    route_file: Path = BASELINE_ROU,
    begin_time: float | None = None,
    end_time: float | None = None,
) -> int:
    """Count vehicles declared in a route file, optionally by departure window."""
    if not route_file.exists():
        return 0

    try:
        vehicles = ET.parse(route_file).getroot().findall("vehicle")
    except (ET.ParseError, OSError):
        return 0

    if begin_time is None and end_time is None:
        return len(vehicles)

    start = float(begin_time or 0)
    end = float(end_time if end_time is not None else float("inf"))
    count = 0
    for vehicle in vehicles:
        try:
            depart = float(vehicle.get("depart", 0) or 0)
        except (TypeError, ValueError):
            continue
        if start <= depart < end:
            count += 1
    return count


def _sumo_binary(gui: bool = False):
    if gui:
        return shutil.which("sumo-gui")
    return shutil.which("sumo") or shutil.which("sumo-gui")


def _netconvert_binary() -> Optional[str]:
    return shutil.which("netconvert")


def _duarouter_binary() -> Optional[str]:
    return shutil.which("duarouter")


def _sumo_net_offset(net_path: Path) -> tuple[float, float]:
    """Return the coordinate offset declared by a SUMO network."""
    if not net_path.exists():
        return 0.0, 0.0

    try:
        location = ET.parse(net_path).getroot().find("location")
        raw_offset = location.get("netOffset", "0,0") if location is not None else "0,0"
        x_offset, y_offset = (float(value) for value in raw_offset.split(",", 1))
        return x_offset, y_offset
    except (ET.ParseError, OSError, TypeError, ValueError):
        return 0.0, 0.0


def _to_sumo_coordinates(
    x: float,
    y: float,
    coordinate_offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    """Convert projected graph coordinates into the network's local frame."""
    return x + coordinate_offset[0], y + coordinate_offset[1]


def _proposed_graph_edges(modified_graph):
    if modified_graph is None:
        return []

    return [
        (str(source), str(target), data)
        for source, target, data in modified_graph.edges(data=True)
        if (
            data.get("highway") in ("proposed_road", "proposed_flyover", "proposed_tunnel")
            or data.get("name") in ("Proposed Infra", "AI Flyover", "AI Tunnel")
        )
    ]


def _proposal_index(data) -> int:
    """Package item index of a proposal edge; 0 is the main structure."""
    try:
        return int(data.get("proposal_index") or 0)
    except (TypeError, ValueError):
        return 0


def _ordered_proposed_graph_edges(modified_graph):
    """Return proposal edges in drawn-segment order and direction order."""
    proposed_edges = _proposed_graph_edges(modified_graph)

    def sort_key(item):
        source, target, data = item
        proposal_index = _proposal_index(data)
        try:
            segment_index = int(data.get("proposal_segment_index"))
        except (TypeError, ValueError):
            segment_index = 10**9
        direction_order = 0 if data.get("proposal_direction") == "forward" else 1
        return proposal_index, segment_index, direction_order, str(source), str(target)

    return sorted(proposed_edges, key=sort_key)


def _proposed_edge_records(modified_graph):
    """Return exported proposal edges with stable SUMO IDs."""
    records = []
    for export_index, (source, target, data) in enumerate(
        _ordered_proposed_graph_edges(modified_graph)
    ):
        try:
            segment_index = int(data.get("proposal_segment_index"))
        except (TypeError, ValueError):
            segment_index = None

        direction = data.get("proposal_direction")
        proposal_index = _proposal_index(data)
        if segment_index is not None and direction in {"forward", "reverse"}:
            if proposal_index:
                edge_id = f"proposed_edge_p{proposal_index}_{segment_index}_{direction}"
            else:
                edge_id = f"proposed_edge_{segment_index}_{direction}"
        else:
            # Preserve the legacy ID format for graphs created before ordered
            # proposal metadata was introduced.
            edge_id = f"proposed_edge_{export_index}"

        records.append((edge_id, source, target, data))
    return records


def _write_proposed_edge_file(
    modified_graph,
    output_path: Path,
    coordinate_offset: tuple[float, float] = (0.0, 0.0),
) -> Optional[Path]:
    proposed_edges = _proposed_edge_records(modified_graph)

    if not proposed_edges:
        return None

    root = ET.Element("edges")
    for edge_id, source, target, data in proposed_edges:
        edge_attributes = {
            "id": edge_id,
            "from": source,
            "to": target,
            "priority": "1",
            "numLanes": "2",
            "speed": "13.89",
        }

        # SUMO accepts a 3D edge shape. The flyover uses a straight XY
        # segment with a raised midpoint, and the tunnel uses a lowered
        # midpoint. Both endpoints remain attached to ground-level nodes.
        sumo_shape = data.get("sumo_shape")
        if sumo_shape:
            shape_points = []
            for x, y, z in sumo_shape:
                sumo_x, sumo_y = _to_sumo_coordinates(
                    float(x), float(y), coordinate_offset
                )
                shape_points.append(f"{sumo_x},{sumo_y},{float(z)}")
            edge_attributes["shape"] = " ".join(shape_points)
        highway = str(data.get("highway", "proposed_road"))
        if highway == "proposed_flyover":
            edge_attributes["type"] = "uidt.flyover"
        elif highway == "proposed_tunnel":
            edge_attributes["type"] = "uidt.tunnel"
        else:
            edge_attributes["type"] = "uidt.proposed_road"
        if data.get("name"):
            edge_attributes["name"] = str(data["name"])

        ET.SubElement(root, "edge", edge_attributes)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


WIDENING_MATCH_RADIUS_M = 12.0
WIDENING_MIN_OVERLAP = 0.6


def _widened_edge_lanes(
    modified_graph,
    baseline_net_path: Path,
    coordinate_offset: tuple[float, float] = (0.0, 0.0),
) -> dict[str, int]:
    """Map existing SUMO edge IDs to their lane count after widening.

    An edge is widened when most of its length lies within a few metres of a
    drawn widening alignment, which picks up both directions of the road.
    """
    widenings = (modified_graph.graph.get("uidt_widenings") or []) if modified_graph is not None else []
    if not widenings or not baseline_net_path.exists():
        return {}

    from shapely.geometry import LineString as ShapelyLineString
    from sumolib.net import readNet

    net = readNet(str(baseline_net_path), withInternal=False)
    widened: dict[str, int] = {}
    base_lanes: dict[str, int] = {}
    for widening in widenings:
        points = [
            _to_sumo_coordinates(float(x), float(y), coordinate_offset)
            for x, y in widening.get("coords", [])
        ]
        if len(points) < 2:
            continue
        alignment = ShapelyLineString(points)
        corridor = alignment.buffer(WIDENING_MATCH_RADIUS_M)
        extra_lanes = max(1, int(widening.get("extra_lanes", 1)))

        candidates = {}
        step = 10.0
        distance = 0.0
        while distance <= alignment.length:
            sample = alignment.interpolate(distance)
            for edge, _dist in net.getNeighboringEdges(
                sample.x, sample.y, WIDENING_MATCH_RADIUS_M
            ):
                candidates[edge.getID()] = edge
            distance += step

        for edge_id, edge in candidates.items():
            if edge_id.startswith(":") or not edge.allows("passenger"):
                continue
            shape = edge.getShape()
            if len(shape) < 2:
                continue
            edge_line = ShapelyLineString([(x, y) for x, y, *_ in shape])
            if edge_line.length <= 0:
                continue
            overlap = edge_line.intersection(corridor).length / edge_line.length
            if overlap >= WIDENING_MIN_OVERLAP:
                base_lanes[edge_id] = edge.getLaneNumber()
                widened[edge_id] = max(
                    widened.get(edge_id, 0), edge.getLaneNumber() + extra_lanes
                )

    if widened:
        print(f"[Widening] matched {len(widened)} edge(s) via overlap")
        modified_graph.graph["uidt_widened_base_lanes"] = base_lanes
        return widened

    fallback_radius = WIDENING_MATCH_RADIUS_M * 3
    for widening in widenings:
        points = [
            _to_sumo_coordinates(float(x), float(y), coordinate_offset)
            for x, y in widening.get("coords", [])
        ]
        if len(points) < 2:
            continue
        alignment = ShapelyLineString(points)
        extra_lanes = max(1, int(widening.get("extra_lanes", 1)))
        sample_distances = []
        distance = 0.0
        while distance < alignment.length:
            sample_distances.append(distance)
            distance += 10.0
        if not sample_distances or alignment.length - sample_distances[-1] > 1e-6:
            sample_distances.append(alignment.length)

        for distance in sample_distances:
            sample = alignment.interpolate(distance)
            neighbors = net.getNeighboringEdges(
                sample.x, sample.y, fallback_radius
            )
            drivable_neighbors = [
                (edge, edge_distance)
                for edge, edge_distance in neighbors
                if not edge.getID().startswith(":") and edge.allows("passenger")
            ]
            if not drivable_neighbors:
                continue
            edge, _edge_distance = min(
                drivable_neighbors, key=lambda candidate: candidate[1]
            )
            base_lanes[edge.getID()] = edge.getLaneNumber()
            widened[edge.getID()] = max(
                widened.get(edge.getID(), 0), edge.getLaneNumber() + extra_lanes
            )

    print(
        f"[Widening] matched {len(widened)} edge(s) via nearest-edge fallback"
    )
    modified_graph.graph["uidt_widened_base_lanes"] = base_lanes
    return widened


def _write_widening_edge_file(widened_lanes: dict[str, int], output_path: Path) -> Optional[Path]:
    if not widened_lanes:
        return None
    root = ET.Element("edges")
    for edge_id, lane_count in sorted(widened_lanes.items()):
        ET.SubElement(root, "edge", {"id": edge_id, "numLanes": str(lane_count)})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


ROAD_BLOCK_MATCH_RADIUS_M = 14.0


def _blocked_edge_ids(
    modified_graph,
    baseline_net_path: Path,
    coordinate_offset: tuple[float, float] = (0.0, 0.0),
) -> set[str]:
    """Find baseline SUMO edges covered by the configured road blocks."""
    blocks = (modified_graph.graph.get("uidt_road_blocks") or []) if modified_graph is not None else []
    if not blocks or not baseline_net_path.exists():
        return set()

    from shapely.geometry import LineString
    from sumolib.net import readNet

    net = readNet(str(baseline_net_path), withInternal=False)
    blocked: set[str] = set()
    for block in blocks:
        coords = [
            _to_sumo_coordinates(float(x), float(y), coordinate_offset)
            for x, y in block.get("coords", [])
        ]
        if len(coords) < 2:
            continue
        alignment = LineString(coords)
        radius = max(ROAD_BLOCK_MATCH_RADIUS_M, float(block.get("radius_m", 0) or 0))
        candidates = {}
        step = max(5.0, min(15.0, alignment.length / 10.0))
        distance = 0.0
        while distance <= alignment.length:
            sample = alignment.interpolate(distance)
            for edge, _distance in net.getNeighboringEdges(sample.x, sample.y, radius):
                if edge.getID().startswith(":") or not edge.allows("passenger"):
                    continue
                candidates[edge.getID()] = edge
            distance += step
        for edge_id, edge in candidates.items():
            shape = edge.getShape()
            if len(shape) < 2:
                continue
            edge_line = LineString([(x, y) for x, y, *_ in shape])
            if edge_line.distance(alignment) <= radius:
                blocked.add(edge_id)

    if blocked:
        print(f"[Road Block] matched {len(blocked)} edge(s) via radius overlap")
        return blocked

    for block in blocks:
        coords = [
            _to_sumo_coordinates(float(x), float(y), coordinate_offset)
            for x, y in block.get("coords", [])
        ]
        if len(coords) < 2:
            continue
        alignment = LineString(coords)
        radius = max(ROAD_BLOCK_MATCH_RADIUS_M, float(block.get("radius_m", 0) or 0))
        fallback_radius = radius * 3
        sample_distances = []
        distance = 0.0
        while distance < alignment.length:
            sample_distances.append(distance)
            distance += 10.0
        if not sample_distances or alignment.length - sample_distances[-1] > 1e-6:
            sample_distances.append(alignment.length)

        for distance in sample_distances:
            sample = alignment.interpolate(distance)
            neighbors = net.getNeighboringEdges(
                sample.x, sample.y, fallback_radius
            )
            drivable_neighbors = [
                (edge, edge_distance)
                for edge, edge_distance in neighbors
                if not edge.getID().startswith(":") and edge.allows("passenger")
            ]
            if drivable_neighbors:
                edge, _edge_distance = min(
                    drivable_neighbors, key=lambda candidate: candidate[1]
                )
                blocked.add(edge.getID())

    print(f"[Road Block] matched {len(blocked)} edge(s) via nearest-edge fallback")
    return blocked


def _write_remove_edge_file(edge_ids: set[str], output_path: Path) -> Optional[Path]:
    if not edge_ids:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(sorted(edge_ids)) + "\n", encoding="utf-8")
    return output_path


def _write_proposed_connection_file(
    modified_graph,
    baseline_net_path: Path,
    output_path: Path,
) -> Optional[Path]:
    """Add explicit incoming connections for newly introduced edges.

    netconvert can omit the road-to-proposed-edge connection when a new edge
    is the reverse direction of a bidirectional proposal. Without this record,
    SUMO accepts the route but vehicles wait at the junction until they are
    teleported.
    """
    proposed_edges = _proposed_edge_records(modified_graph)
    if not proposed_edges or not baseline_net_path.exists():
        return None

    try:
        network_root = ET.parse(baseline_net_path).getroot()
    except (ET.ParseError, OSError):
        return None

    incoming_by_node: dict[str, list[tuple[str, int]]] = {}
    for edge in network_root.findall("edge"):
        edge_id = edge.get("id")
        target_node = edge.get("to")
        if (
            not edge_id
            or not target_node
            or edge.get("function") == "internal"
            or edge_id.startswith(":")
        ):
            continue
        incoming_by_node.setdefault(target_node, []).append(
            (edge_id, max(1, len(edge.findall("lane"))))
        )

    # Only existing roads get explicit connections here. Connections between
    # package items (e.g. connector -> flyover) are left to netconvert: an
    # explicit one would stop it computing the item's links to ground roads.
    root = ET.Element("connections")
    for proposed_edge_id, source, _target, _data in proposed_edges:
        for incoming_edge_id, lane_count in incoming_by_node.get(source, []):
            for lane_index in range(lane_count):
                ET.SubElement(
                    root,
                    "connection",
                    {
                        "from": incoming_edge_id,
                        "to": proposed_edge_id,
                        "fromLane": str(lane_index),
                        "toLane": str(min(lane_index, 1)),
                    },
                )

    if len(root) == 0:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def _write_proposed_node_file(
    modified_graph,
    output_path: Path,
    coordinate_offset: tuple[float, float] = (0.0, 0.0),
) -> Optional[Path]:
    if modified_graph is None:
        return None

    # Keep the graph's native node IDs for lookup. OSMnx/NetworkX commonly
    # stores these IDs as integers, while the SUMO XML representation uses
    # strings. Converting before the lookup makes every node appear missing.
    proposed_nodes = set()
    for source, target, data in modified_graph.edges(data=True):
        if (
            data.get("highway") not in ("proposed_road", "proposed_flyover", "proposed_tunnel")
            and data.get("name") not in ("Proposed Infra", "AI Flyover", "AI Tunnel")
        ):
            continue
        proposed_nodes.update((source, target))

    if not proposed_nodes:
        return None

    root = ET.Element("nodes")
    for node_id in sorted(proposed_nodes, key=str):
        node_data = modified_graph.nodes.get(node_id, {})
        x = node_data.get("x") if node_data.get("x") is not None else node_data.get("lon")
        y = node_data.get("y") if node_data.get("y") is not None else node_data.get("lat")
        if x is None or y is None:
            continue
        sumo_x, sumo_y = _to_sumo_coordinates(float(x), float(y), coordinate_offset)
        ET.SubElement(
            root,
            "node",
            {"id": str(node_id), "x": str(sumo_x), "y": str(sumo_y)},
        )

    if len(root) == 0:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def _geojson_centroid(geometry: dict) -> Optional[tuple[float, float]]:
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not coordinates:
        return None

    if geom_type == "Polygon":
        ring = coordinates[0]
    elif geom_type == "MultiPolygon":
        ring = coordinates[0][0]
    else:
        return None

    if not ring:
        return None
    lon = sum(point[0] for point in ring) / len(ring)
    lat = sum(point[1] for point in ring) / len(ring)
    return lon, lat


def _load_ward_centroids() -> dict[str, tuple[float, float]]:
    if not WARDS_GEOJSON.exists():
        return {}

    with open(WARDS_GEOJSON, encoding="utf-8") as file:
        payload = json.load(file)

    centroids: dict[str, tuple[float, float]] = {}
    for feature in payload.get("features", []):
        movement_id = feature.get("properties", {}).get("MOVEMENT_ID")
        if movement_id is None:
            continue
        centroid = _geojson_centroid(feature.get("geometry", {}))
        if centroid:
            centroids[str(movement_id)] = centroid
    return centroids


def _load_demand_rows_from_uber_csv(demand_data: dict) -> list[dict]:
    """Supplement demand_matrix with Uber Movement CSV rows when available."""
    if not UBER_CSV.exists():
        return []

    try:
        import pandas as pd
    except ImportError:
        return []

    scenario = demand_data.get("scenario_metadata", {})
    target_hour = int(scenario.get("target_hour", 9))
    movement_id = scenario.get("movement_id")

    try:
        frame = pd.read_csv(UBER_CSV)
    except Exception:
        return []

    if "hod" not in frame.columns:
        return []

    frame = frame[frame["hod"] == target_hour]
    if movement_id is not None:
        movement_id = int(movement_id)
        frame = frame[(frame["sourceid"] == movement_id) | (frame["dstid"] == movement_id)]

    rows = []
    for _, row in frame.head(50).iterrows():
        travel_sec = float(row.get("mean_travel_time", 0) or 0)
        travel_min = max(0.5, travel_sec / 60.0)
        rows.append(
            {
                "sourceid": int(row["sourceid"]),
                "dstid": int(row["dstid"]),
                "synthetic_trip_count": max(1.0, 100.0 / travel_min),
                "travel_time_min": round(travel_min, 2),
            }
        )
    return rows


def _demand_rows_for_routes(demand_data: dict) -> list[dict]:
    matrix = list(demand_data.get("demand_matrix", []) or [])
    if matrix:
        return matrix

    uber_rows = _load_demand_rows_from_uber_csv(demand_data)
    return uber_rows


def _nearest_edge(net, lon: float, lat: float, radius: float):
    x, y = net.convertLonLat2XY(lon, lat)
    neighbors = net.getNeighboringEdges(x, y, radius)
    if not neighbors:
        return None
    return neighbors[0][0]


def _route_edges_for_od(net, centroids: dict[str, tuple[float, float]], source_id, dest_id):
    source = centroids.get(str(source_id))
    dest = centroids.get(str(dest_id))
    if not source or not dest:
        return None

    from_edge = None
    to_edge = None
    for radius in (500, 1200, 2500):
        from_edge = _nearest_edge(net, source[0], source[1], radius)
        to_edge = _nearest_edge(net, dest[0], dest[1], radius)
        if from_edge and to_edge:
            break

    if not from_edge or not to_edge or from_edge.getID() == to_edge.getID():
        return None

    path, _ = net.getShortestPath(from_edge, to_edge)
    if not path:
        return None

    edge_ids = [edge.getID() for edge in path if not edge.getID().startswith(":")]
    if len(edge_ids) < 2:
        return None
    return edge_ids


def _write_route_file(output_path: Path, routes: list[tuple[float, list[str]]]) -> None:
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "car",
            "accel": "2.6",
            "decel": "4.5",
            "sigma": "0.5",
            "length": "5.0",
            "maxSpeed": "50.0",
        },
    )

    for index, (depart, edge_ids) in enumerate(routes):
        vehicle = ET.SubElement(
            root,
            "vehicle",
            {
                "id": f"demand_vehicle_{index}",
                "type": "car",
                "depart": f"{depart:.2f}",
            },
        )
        ET.SubElement(vehicle, "route", {"edges": " ".join(edge_ids)})

    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)


def _try_build_uber_demand_routes(
    net_path: Path,
    demand_data: dict,
    target_vehicles: int,
    simulation_time: int,
) -> Optional[Path]:
    """Build a demand-scaled .rou.xml from Uber Movement OD mapped onto the SUMO network."""
    if not net_path.exists() or not WARDS_GEOJSON.exists():
        return None

    demand_rows = _demand_rows_for_routes(demand_data)
    if not demand_rows:
        return None

    try:
        from sumolib.net import readNet
    except ImportError:
        try:
            import sumolib

            readNet = sumolib.net.readNet
        except ImportError:
            return None

    try:
        import pyproj  # noqa: F401 — required by sumolib geo conversion
    except ImportError:
        return None

    try:
        net = readNet(str(net_path), withInternal=False)
    except Exception:
        return None

    try:
        centroids = _load_ward_centroids()
        if not centroids:
            return None

        weighted_routes: list[tuple[float, list[str]]] = []
        total_weight = sum(float(row.get("synthetic_trip_count", 0) or 0) for row in demand_rows)
        if total_weight <= 0:
            return None

        for row in demand_rows:
            edge_ids = _route_edges_for_od(
                net,
                centroids,
                row.get("sourceid"),
                row.get("dstid"),
            )
            if not edge_ids:
                continue
            weight = float(row.get("synthetic_trip_count", 0) or 0)
            count = max(1, round(target_vehicles * weight / total_weight))
            weighted_routes.extend([(0.0, edge_ids)] * count)

        if not weighted_routes:
            return None

        if len(weighted_routes) > target_vehicles:
            stride = len(weighted_routes) / target_vehicles
            weighted_routes = [weighted_routes[int(i * stride)] for i in range(target_vehicles)]
        elif len(weighted_routes) < target_vehicles:
            extra = target_vehicles - len(weighted_routes)
            for i in range(extra):
                weighted_routes.append(weighted_routes[i % len(weighted_routes)])

        step = simulation_time / max(1, len(weighted_routes) - 1) if len(weighted_routes) > 1 else simulation_time
        scheduled = [(round(i * step, 2), edges) for i, (_, edges) in enumerate(weighted_routes)]

        temp_dir = Path(tempfile.mkdtemp(prefix="uidt_sumo_routes_"))
        route_path = temp_dir / "demand.rou.xml"
        _write_route_file(route_path, scheduled)

        success_rate = len(scheduled) / max(1, target_vehicles)
        if success_rate < MIN_UBER_ROUTE_SUCCESS_RATE:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None

        return route_path
    except Exception:
        return None


def _scale_baseline_routes(
    source_rou: Path,
    target_vehicles: int,
    simulation_time: int,
    output_path: Path,
) -> None:
    tree = ET.parse(source_rou)
    root = tree.getroot()

    vtype = root.find("vType")
    vehicles = root.findall("vehicle")
    if not vehicles:
        raise ValueError("Baseline route file contains no vehicles.")

    if len(vehicles) >= target_vehicles:
        indices = [
            int(i * (len(vehicles) - 1) / max(1, target_vehicles - 1))
            for i in range(target_vehicles)
        ]
        selected = [vehicles[index] for index in indices]
    else:
        selected = []
        for i in range(target_vehicles):
            selected.append(vehicles[i % len(vehicles)])

    new_root = ET.Element("routes")
    if vtype is not None:
        new_root.append(ET.Element("vType", vtype.attrib))

    step = simulation_time / max(1, len(selected) - 1) if len(selected) > 1 else 0.0
    for index, vehicle in enumerate(selected):
        attrs = dict(vehicle.attrib)
        attrs["id"] = f"scaled_vehicle_{index}"
        attrs["depart"] = f"{index * step:.2f}"
        new_vehicle = ET.SubElement(new_root, "vehicle", attrs)
        route = vehicle.find("route")
        if route is not None:
            ET.SubElement(new_vehicle, "route", dict(route.attrib))

    ET.ElementTree(new_root).write(output_path, encoding="utf-8", xml_declaration=True)


def _prepare_route_file(
    net_path: Path,
    demand_data: dict,
    target_vehicles: int,
    simulation_time: int,
    temp_dir: Path,
    route_file: Path = BASELINE_ROU,
) -> Path:
    del net_path, demand_data, target_vehicles, simulation_time, temp_dir
    if not route_file.exists():
        raise FileNotFoundError(f"SUMO route file is missing: {route_file}")
    return route_file


# How far (in edges) to search outward from a closure when a trip's origin or
# destination lies on a blocked edge, or when no path exists from it.
ROAD_BLOCK_RELOCATION_MAX_HOPS = 6

def _release_endpoint_position(attributes: dict, origin: bool) -> None:
    """Drop positions that pin a vehicle to an endpoint edge it no longer uses.

    Numeric lane indexes may not exist on the replacement edge, so they fall
    back to SUMO's lane choice; symbolic values such as ``best`` are kept.
    """
    position_key, lane_key = ("departPos", "departLane") if origin else ("arrivalPos", "arrivalLane")
    attributes.pop(position_key, None)
    if str(attributes.get(lane_key, "")).isdigit():
        attributes[lane_key] = "best" if origin else "current"


def _load_sumo_net(net_path: Path):
    """Read a SUMO network, returning None when it cannot be parsed."""
    try:
        from sumolib.net import readNet

        return readNet(str(net_path), withInternal=False)
    except Exception as exc:  # noqa: BLE001 - sumolib raises many types
        logger.warning("Could not read SUMO network %s: %s", net_path, exc)
        return None


def _nearby_open_edges(
    net,
    edge_id: str,
    blocked_edge_ids: set[str],
    upstream: bool,
    max_hops: int = ROAD_BLOCK_RELOCATION_MAX_HOPS,
) -> list[str]:
    """Open passenger edges reachable from ``edge_id`` in breadth-first order.

    ``upstream`` walks against the direction of travel (incoming edges), which
    is where a vehicle waits when its origin is closed. Downstream walks with
    the direction of travel, which is where a vehicle exits when its
    destination is closed. Blocked edges are traversed but never returned.
    """
    if net is None:
        return []
    try:
        start = net.getEdge(edge_id)
    except (KeyError, AttributeError):
        return []

    found: list[str] = []
    seen = {edge_id}
    frontier = [start]
    for _hop in range(max_hops):
        next_frontier = []
        for edge in frontier:
            node = edge.getFromNode() if upstream else edge.getToNode()
            neighbours = node.getIncoming() if upstream else node.getOutgoing()
            for neighbour in neighbours:
                neighbour_id = neighbour.getID()
                if neighbour_id in seen or neighbour_id.startswith(":"):
                    continue
                seen.add(neighbour_id)
                if not neighbour.allows("passenger"):
                    continue
                next_frontier.append(neighbour)
                if neighbour_id not in blocked_edge_ids:
                    found.append(neighbour_id)
        if not next_frontier:
            break
        frontier = next_frontier
    return found


def _route_walk_endpoint(edge_ids: list[str], blocked_edge_ids: set[str], from_start: bool) -> Optional[str]:
    """First (or last) open edge on the vehicle's own route."""
    ordered = edge_ids if from_start else list(reversed(edge_ids))
    return next((edge_id for edge_id in ordered if edge_id not in blocked_edge_ids), None)


def _endpoint_candidates(
    edge_ids: list[str],
    blocked_edge_ids: set[str],
    baseline_net,
    origin: bool,
) -> list[str]:
    """Ordered origin/destination candidates for a trip on the blocked network.

    The vehicle's own endpoint comes first when it is open. When it is closed
    the vehicle is relocated to the closure boundary: the upstream approach for
    an origin and the downstream exit for a destination. The vehicle's own
    route is the final fallback so a candidate is always available when any
    part of the route is open.
    """
    endpoint = edge_ids[0] if origin else edge_ids[-1]
    candidates: list[str] = []
    if endpoint not in blocked_edge_ids:
        candidates.append(endpoint)
    candidates.extend(
        _nearby_open_edges(baseline_net, endpoint, blocked_edge_ids, upstream=origin)
    )
    walked = _route_walk_endpoint(edge_ids, blocked_edge_ids, from_start=origin)
    if walked is not None:
        candidates.append(walked)

    unique: list[str] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _shortest_open_path(after_net, origin_ids: list[str], destination_ids: list[str]) -> Optional[list[str]]:
    """Try origin/destination candidate pairs until one is connected."""
    if after_net is None:
        return None
    for origin_id in origin_ids[:4]:
        for destination_id in destination_ids[:4]:
            if origin_id == destination_id:
                continue
            try:
                path, _cost = after_net.getShortestPath(
                    after_net.getEdge(origin_id),
                    after_net.getEdge(destination_id),
                    vClass="passenger",
                )
            except (KeyError, AttributeError, TypeError):
                continue
            if path:
                return [edge.getID() for edge in path if not edge.getID().startswith(":")]
    return None


def _run_duarouter_trips(
    duarouter: str,
    after_net: Path,
    trips_root: ET.Element,
    temp_dir: Path,
) -> dict[str, list[str]]:
    """Route trips on the modified network; returns vehicle id -> edge ids."""
    trips_path = temp_dir / "road_block_trips.xml"
    output_path = temp_dir / "road_block_duarouter.rou.xml"
    ET.ElementTree(trips_root).write(trips_path, encoding="utf-8", xml_declaration=True)
    result = subprocess.run(
        [
            duarouter,
            "--net-file",
            str(after_net),
            "--route-files",
            str(trips_path),
            "--output-file",
            str(output_path),
            # Unroutable trips are recovered below instead of being lost.
            "--ignore-errors",
            "true",
            "--no-warnings",
            "true",
            "--no-step-log",
            "true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists():
        detail = (result.stderr or result.stdout or "").strip()
        logger.warning("duarouter failed on the road-blocked network: %s", detail)
        return {}

    routed: dict[str, list[str]] = {}
    for vehicle in ET.parse(output_path).getroot().iter("vehicle"):
        route = vehicle.find("route")
        if route is None:
            route_distribution = vehicle.find("routeDistribution")
            route = route_distribution.find("route") if route_distribution is not None else None
        edges = (route.get("edges", "") if route is not None else "").split()
        if vehicle.get("id") and edges:
            routed[vehicle.get("id")] = edges
    return routed


def _empty_reroute_stats() -> dict:
    return {
        "affected_vehicle_count": 0,
        "rerouted_vehicle_count": 0,
        "relocated_origin_count": 0,
        "relocated_destination_count": 0,
        "python_fallback_count": 0,
        "unroutable_vehicle_count": 0,
        "unroutable_vehicle_ids": [],
        "router": None,
    }


def _reroute_around_blocked_edges(
    route_path: Path,
    after_net: Path,
    blocked_edge_ids: set[str],
    temp_dir: Path,
    baseline_net_path: Optional[Path] = None,
) -> tuple[Path, dict]:
    """Reroute every vehicle whose route uses a blocked edge; drop none.

    Vehicles that never touch a blocked edge keep their baseline route
    unchanged, so the before/after comparison isolates the closure. Affected
    vehicles get a new route on the modified network:

    1. If the origin or destination edge is closed, the endpoint is moved to
       the nearest open edge at the closure boundary.
    2. duarouter computes the replacement route.
    3. Any trip duarouter could not route is retried in Python with
       alternative nearby endpoints.

    Only a vehicle whose entire neighbourhood is disconnected stays
    unroutable. It is reported, never silently discarded.

    Returns ``(route_file_path, stats)``.
    """
    stats = _empty_reroute_stats()
    if not blocked_edge_ids:
        return route_path, stats

    tree = ET.parse(route_path)
    source_root = tree.getroot()
    baseline_net = _load_sumo_net(baseline_net_path) if baseline_net_path else None

    trips_root = ET.Element("trips")
    for vtype in source_root.findall("vType"):
        trips_root.append(ET.Element("vType", dict(vtype.attrib)))

    affected: dict[str, dict] = {}
    for vehicle in source_root.findall("vehicle"):
        route = vehicle.find("route")
        edge_ids = route.get("edges", "").split() if route is not None else []
        vehicle_id = vehicle.get("id")
        if not vehicle_id or not edge_ids or not blocked_edge_ids.intersection(edge_ids):
            continue

        origin_ids = _endpoint_candidates(edge_ids, blocked_edge_ids, baseline_net, origin=True)
        destination_ids = _endpoint_candidates(edge_ids, blocked_edge_ids, baseline_net, origin=False)
        affected[vehicle_id] = {
            "element": vehicle,
            "original_edges": edge_ids,
            "origin_ids": origin_ids,
            "destination_ids": destination_ids,
        }
        if not origin_ids or not destination_ids:
            continue

        trip_attributes = {
            key: value for key, value in vehicle.attrib.items() if key != "route"
        }
        if origin_ids[0] != edge_ids[0]:
            _release_endpoint_position(trip_attributes, origin=True)
        if destination_ids[0] != edge_ids[-1]:
            _release_endpoint_position(trip_attributes, origin=False)
        trip_attributes["from"] = origin_ids[0]
        trip_attributes["to"] = destination_ids[0]
        if trip_attributes["from"] == trip_attributes["to"] and len(destination_ids) > 1:
            trip_attributes["to"] = destination_ids[1]
        ET.SubElement(trips_root, "trip", trip_attributes)

    stats["affected_vehicle_count"] = len(affected)

    routed: dict[str, list[str]] = {}
    duarouter = _duarouter_binary()
    if duarouter and len(trips_root.findall("trip")):
        routed = _run_duarouter_trips(duarouter, after_net, trips_root, temp_dir)
        stats["router"] = "duarouter"

    missing = [vehicle_id for vehicle_id in affected if vehicle_id not in routed]
    if missing:
        after_sumo_net = _load_sumo_net(after_net)
        for vehicle_id in missing:
            info = affected[vehicle_id]
            path = _shortest_open_path(after_sumo_net, info["origin_ids"], info["destination_ids"])
            if path:
                routed[vehicle_id] = path
                stats["python_fallback_count"] += 1
        stats["router"] = stats["router"] or "sumolib"

    for vehicle_id, info in affected.items():
        vehicle = info["element"]
        new_edges = routed.get(vehicle_id)
        if not new_edges:
            stats["unroutable_vehicle_ids"].append(vehicle_id)
            continue
        original_edges = info["original_edges"]
        if new_edges[0] != original_edges[0]:
            _release_endpoint_position(vehicle.attrib, origin=True)
            stats["relocated_origin_count"] += 1
        if new_edges[-1] != original_edges[-1]:
            _release_endpoint_position(vehicle.attrib, origin=False)
            stats["relocated_destination_count"] += 1
        vehicle.find("route").set("edges", " ".join(new_edges))
        stats["rerouted_vehicle_count"] += 1

    # A vehicle with no open path at all would make SUMO abort or teleport it,
    # so it is held out of the after-run and reported explicitly.
    unroutable = set(stats["unroutable_vehicle_ids"])
    for vehicle in list(source_root.findall("vehicle")):
        if vehicle.get("id") in unroutable:
            source_root.remove(vehicle)
    stats["unroutable_vehicle_count"] = len(unroutable)
    stats["unroutable_vehicle_ids"] = sorted(unroutable)[:50]

    new_route_path = temp_dir / "road_block_after.rou.xml"
    tree.write(new_route_path, encoding="utf-8", xml_declaration=True)
    print(
        f"[Road Block] rerouted {stats['rerouted_vehicle_count']} of "
        f"{stats['affected_vehicle_count']} affected vehicle(s) around "
        f"{len(blocked_edge_ids)} blocked edge(s); relocated "
        f"{stats['relocated_origin_count']} origin(s) and "
        f"{stats['relocated_destination_count']} destination(s); "
        f"{stats['unroutable_vehicle_count']} vehicle(s) have no open path"
    )
    return new_route_path, stats


def _run_sumo(
    net_path: Path,
    route_path: Path,
    simulation_time: int,
    tripinfo_path: Path,
    summary_path: Path,
    gui: bool = False,
    fcd_path: Path | None = None,
    begin_time: int = 0,
    end_time: int = 10000,
    statistics_path: Path | None = None,
) -> None:
    sumo = _sumo_binary(gui=gui)
    if not sumo:
        raise FileNotFoundError("SUMO binary not found on PATH")

    command = [
        sumo,
        "-n",
        str(net_path),
        "-r",
        str(route_path),
        "--begin",
        str(begin_time),
        "--end",
        str(end_time),
        "--no-step-log",
        "--ignore-route-errors",
        "true",
        "--collision.action",
        "warn",
        f"--tripinfo-output={tripinfo_path}",
        f"--summary-output={summary_path}",
        "--tripinfo-output.write-unfinished",
        "true",
    ]

    if fcd_path is not None:
        command += [
            f"--fcd-output={fcd_path}",
        ]

    if statistics_path is not None:
        command += [f"--statistic-output={statistics_path}"]

    if SUMO_TIME_TO_TELEPORT is not None:
        command += ["--time-to-teleport", str(SUMO_TIME_TO_TELEPORT)]

    if gui:
        command += [
            "--start",
            "--quit-on-end",
            "--delay",
            "10",
        ]

    logger.info("Executing SUMO command: %s", " ".join(command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if stdout:
        logger.info("[SUMO stdout]\n%s", stdout)
    if stderr:
        logger.warning("[SUMO stderr]\n%s", stderr)

    if result.returncode != 0:
        signed_returncode = result.returncode
        if result.returncode > 0x7FFFFFFF:
            signed_returncode = result.returncode - 0x100000000
        details = stderr or stdout or "no diagnostic output"
        raise RuntimeError(
            f"SUMO failed (executable={sumo}, exit_code={result.returncode}, "
            f"signed_exit_code={signed_returncode}): {details}"
        )
    
def _find_corridor_vehicle_ids(
    route_file: Path,
    start_edge_id: str,
    end_edge_id: str,
) -> dict[str, dict]:
    """
    Return vehicles whose declared route contains start_edge_id and later end_edge_id.
    """
    if not route_file.exists():
        return {}

    root = ET.parse(route_file).getroot()
    matched: dict[str, dict] = {}

    for vehicle in root.findall("vehicle"):
        vehicle_id = vehicle.get("id")
        if not vehicle_id:
            continue

        route_elem = vehicle.find("route")
        if route_elem is None:
            continue

        route_edges = route_elem.get("edges", "").split()
        if not route_edges:
            continue

        if start_edge_id not in route_edges or end_edge_id not in route_edges:
            continue

        start_idx = route_edges.index(start_edge_id)
        end_idx = route_edges.index(end_edge_id)

        if start_idx >= end_idx:
            continue

        depart = float(vehicle.get("depart", 0) or 0)

        matched[vehicle_id] = {
            "depart": depart,
            "route_edges": route_edges,
        }

    return matched

def _contiguous_overlap_score(vehicle_edges: list[str], candidate_edges: list[str]) -> float:
    """
    Score how well a vehicle's route matches a candidate route.
    Uses longest contiguous sequence of candidate edges that appear
    consecutively in the vehicle's route, divided by candidate length.
    This is order-sensitive: scattered edge matches don't inflate the score.
    """
    if not candidate_edges or not vehicle_edges:
        return 0.0

    # Build a position index for fast lookup: edge_id -> list of positions in vehicle route
    vehicle_index: dict[str, list[int]] = {}
    for pos, edge_id in enumerate(vehicle_edges):
        vehicle_index.setdefault(edge_id, []).append(pos)

    best_run = 0
    current_run = 0
    last_vehicle_pos = -1

    for candidate_edge in candidate_edges:
        positions = vehicle_index.get(candidate_edge)
        if positions is None:
            # Edge not in vehicle route at all — break the run
            current_run = 0
            last_vehicle_pos = -1
            continue

        # Find the earliest vehicle position that continues the sequence
        advanced = False
        for vpos in positions:
            if vpos > last_vehicle_pos:
                current_run += 1
                last_vehicle_pos = vpos
                advanced = True
                break

        if not advanced:
            # No valid continuation — restart run from best available position
            current_run = 1
            last_vehicle_pos = positions[-1]

        best_run = max(best_run, current_run)

    return best_run / len(candidate_edges)

def _classify_vehicles_to_routes(
    route_file: Path,
    candidate_routes: list[dict],
    min_overlap_threshold: float = 0.05,
    begin_time: float = 0.0,
    end_time: float = 999999.0,
) -> dict[str, int]:
    """
    Read the route file and assign each vehicle to the single best-matching
    candidate route using contiguous edge overlap.

    Returns { vehicle_id: route_index } where route_index is 0-based.
    """
    if not route_file.exists():
        return {}

    candidate_edge_lists = [route["edge_ids"] for route in candidate_routes]

    root = ET.parse(route_file).getroot()
    assignments: dict[str, int] = {}

    for vehicle in root.findall("vehicle"):
        vehicle_id = vehicle.get("id")
        if not vehicle_id:
            continue

        depart = float(vehicle.get("depart", 0))
        if depart < begin_time or depart > end_time:
            continue

        route_elem = vehicle.find("route")
        if route_elem is None:
            continue

        vehicle_edges = route_elem.get("edges", "").split()
        if not vehicle_edges:
            continue

        # Score against each candidate route using contiguous overlap.
        best_route_index = -1
        best_score = -1.0

        for route_index, candidate_edges in enumerate(candidate_edge_lists):
            score = _contiguous_overlap_score(vehicle_edges, candidate_edges)
            if score > best_score:
                best_score = score
                best_route_index = route_index

        # Assign if there is any meaningful overlap with a candidate route.
        # A lower threshold improves distribution when routes share similar
        # segments or the corridor path is only partially covered.
        if best_score >= min_overlap_threshold:
            assignments[vehicle_id] = best_route_index

    return assignments


def _assign_intervention_routes(
    corridor_vehicle_ids: list[str],
    baseline_assignments: dict[str, int],
    base_route_count: int,
    adoption_rate_pct: float,
) -> tuple[dict[str, int], int]:
    eligible_vehicle_ids = sorted(corridor_vehicle_ids)
    bounded_adoption_rate = max(0.0, min(100.0, float(adoption_rate_pct)))
    adopted_vehicle_count = int(
        len(eligible_vehicle_ids) * bounded_adoption_rate / 100.0 + 0.5
    )
    adopter_ids = set(eligible_vehicle_ids[:adopted_vehicle_count])
    new_route_index = base_route_count

    assignments = {
        vehicle_id: (
            new_route_index
            if vehicle_id in adopter_ids
            else baseline_assignments[vehicle_id]
        )
        for vehicle_id in eligible_vehicle_ids
    }
    return assignments, adopted_vehicle_count

def _extract_corridor_vehicle_stats_from_fcd(
    fcd_path: Path,
    vehicle_assignments: dict[str, int],
    candidate_routes: list[dict],
    low_speed_threshold: float = 0.1,
) -> dict[int, list[dict]]:
    """
    Single-pass FCD parse. For each vehicle in vehicle_assignments,
    track it against the corridor edges of its assigned route.

    Returns { route_index: [vehicle_stat_rows] }
    """
    if not fcd_path.exists() or not vehicle_assignments:
        return {}

    # Use the first and last route edges as the origin/destination timing
    # boundaries. Measuring every edge in the route as one open-ended segment
    # incorrectly turns the observation window into the travel time.
    route_metadata = []
    for route in candidate_routes:
        edge_ids = list(route.get("edge_ids", []))
        route_metadata.append({
            "corridor_edges": set(edge_ids),
            "start_edge_id": edge_ids[0] if edge_ids else None,
            "end_edge_id": edge_ids[-1] if edge_ids else None,
            "route_length_m": float(route.get("length_m", 0) or 0),
        })

    # Initialise tracking state per vehicle
    vehicle_state: dict[str, dict] = {}
    for vehicle_id, route_index in vehicle_assignments.items():
        vehicle_state[vehicle_id] = {
            "vehicle_id": vehicle_id,
            "route_index": route_index,
            "corridor_edges": route_metadata[route_index]["corridor_edges"],
            "start_edge_id": route_metadata[route_index]["start_edge_id"],
            "end_edge_id": route_metadata[route_index]["end_edge_id"],
            "route_length_m": route_metadata[route_index]["route_length_m"],
            "start_time": None,
            "end_time": None,
            "segment_time_sec": None,
            "waiting_time_sec": 0.0,
            "completed": False,
            "last_time": None,
        }

    context = ET.iterparse(fcd_path, events=("end",))

    for event, elem in context:
        if elem.tag != "timestep":
            continue

        timestep_time = float(elem.get("time", 0) or 0)

        for veh in elem.findall("vehicle"):
            vehicle_id = veh.get("id")
            if vehicle_id not in vehicle_state:
                continue

            lane_id = veh.get("lane", "")
            if not lane_id:
                continue

            edge_id = lane_id.rsplit("_", 1)[0]
            state = vehicle_state[vehicle_id]

            if (
                state["start_time"] is None
                and edge_id == state["start_edge_id"]
            ):
                state["start_time"] = timestep_time

            if (
                state["start_time"] is not None
                and not state["completed"]
                and edge_id in state["corridor_edges"]
            ):
                speed = float(veh.get("speed", 0) or 0)
                if speed <= low_speed_threshold and state["last_time"] is not None:
                    dt = timestep_time - state["last_time"]
                    if dt > 0:
                        state["waiting_time_sec"] += dt

            if (
                state["start_time"] is not None
                and not state["completed"]
                and edge_id == state["end_edge_id"]
                and timestep_time >= state["start_time"]
                and state["start_edge_id"] != state["end_edge_id"]
            ):
                state["end_time"] = timestep_time
                state["completed"] = True
                state["segment_time_sec"] = timestep_time - state["start_time"]

            state["last_time"] = timestep_time

        elem.clear()

    # Group results by route_index
    results: dict[int, list[dict]] = {i: [] for i in range(len(candidate_routes))}

    for state in vehicle_state.values():
        route_index = state["route_index"]
        results[route_index].append({
            "vehicle_id": state["vehicle_id"],
            "start_time": state["start_time"],
            "end_time": state["end_time"],
            "segment_time_sec": state["segment_time_sec"],
            "route_length_m": state["route_length_m"],
            "waiting_time_sec": round(state["waiting_time_sec"], 2),
            "completed": state["completed"],
        })

    return results

def _aggregate_corridor_metrics(vehicle_rows: list[dict]) -> dict:
    matched_count = len(vehicle_rows)

    completed_rows = [
        row for row in vehicle_rows
        if row["completed"] and row["segment_time_sec"] is not None and row["segment_time_sec"] >= 0
    ]

    completed_count = len(completed_rows)

    travel_times = [row["segment_time_sec"] for row in completed_rows]
    speeds_kmh = [
        (float(row.get("route_length_m", 0) or 0) / row["segment_time_sec"]) * 3.6
        for row in completed_rows
        if row["segment_time_sec"] > 0 and float(row.get("route_length_m", 0) or 0) > 0
    ]
    congestion_ratios = []

    for row in completed_rows:
        seg_time = row["segment_time_sec"]
        wait_time = row.get("waiting_time_sec", 0.0) or 0.0
        if seg_time and seg_time > 0:
            congestion_ratios.append(wait_time / seg_time)

    return {
        "throughput": completed_count,
        "metric_source": "SUMO FCD fallback",
        "avg_travel_time_sec": round(sum(travel_times) / len(travel_times), 2)
        if travel_times else 0.0,
        "avg_speed_kmh": round(sum(speeds_kmh) / len(speeds_kmh), 2)
        if speeds_kmh else 0.0,
        "active_vehicles": max(0, matched_count - completed_count),
        "avg_congestion_ratio": round(sum(congestion_ratios) / len(congestion_ratios), 4)
        if congestion_ratios else 0.0,
        "matched_vehicle_count": matched_count,
        "completed_vehicle_count": completed_count,
        "debug_segment_rows": vehicle_rows,
    }


def _heatmap_edge_metadata(net) -> dict[str, dict]:
    """Return public GeoJSON metadata for passenger-drivable network edges."""
    metadata = {}
    for edge in net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":") or not edge.allows("passenger"):
            continue

        shape = list(edge.getShape() or [])
        if len(shape) < 2:
            shape = [
                edge.getFromNode().getCoord(),
                edge.getToNode().getCoord(),
            ]
        if len(shape) < 2:
            continue

        coordinates = []
        for point in shape:
            if len(point) < 2:
                continue
            lon, lat = net.convertXY2LonLat(float(point[0]), float(point[1]))
            coordinate = [round(float(lon), 7), round(float(lat), 7)]
            if not coordinates or coordinates[-1] != coordinate:
                coordinates.append(coordinate)
        if len(coordinates) < 2:
            continue

        try:
            speed_limit_kmh = max(1.0, float(edge.getSpeed()) * 3.6)
        except (AttributeError, TypeError, ValueError):
            speed_limit_kmh = 50.0

        try:
            road_name = edge.getName() or edge_id
        except AttributeError:
            road_name = edge_id

        metadata[edge_id] = {
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "road_name": road_name,
            "speed_limit_kmh": round(speed_limit_kmh, 1),
        }
    return metadata


def _parse_fcd_edge_metrics(
    fcd_path: Path,
    net,
    begin_time: int,
    end_time: int,
) -> tuple[dict[str, dict], int, int]:
    """Aggregate SUMO FCD samples by road edge for the selected time window."""
    if not fcd_path.exists():
        return {}, 0, 0

    metadata = _heatmap_edge_metadata(net)
    samples = defaultdict(
        lambda: {
            "speed_sum_mps": 0.0,
            "sample_count": 0,
            "stopped_sample_count": 0,
            "vehicle_ids": set(),
        }
    )
    observed_vehicle_ids = set()
    observed_timestep_count = 0

    try:
        context = ET.iterparse(fcd_path, events=("end",))
        for _event, elem in context:
            if elem.tag != "timestep":
                continue

            try:
                timestep_time = float(elem.get("time", 0) or 0)
            except (TypeError, ValueError):
                elem.clear()
                continue

            if timestep_time < begin_time or timestep_time > end_time:
                elem.clear()
                continue

            timestep_had_observation = False
            for vehicle in elem.findall("vehicle"):
                vehicle_id = vehicle.get("id")
                lane_id = vehicle.get("lane", "")
                if not vehicle_id or not lane_id:
                    continue

                edge_id = lane_id.rsplit("_", 1)[0]
                if edge_id not in metadata:
                    continue

                try:
                    speed_mps = max(0.0, float(vehicle.get("speed", 0) or 0))
                except (TypeError, ValueError):
                    continue

                edge_sample = samples[edge_id]
                edge_sample["speed_sum_mps"] += speed_mps
                edge_sample["sample_count"] += 1
                edge_sample["stopped_sample_count"] += speed_mps <= 0.1
                edge_sample["vehicle_ids"].add(vehicle_id)
                observed_vehicle_ids.add(vehicle_id)
                timestep_had_observation = True

            observed_timestep_count += int(timestep_had_observation)
            elem.clear()
    except (ET.ParseError, OSError):
        return {}, 0, 0

    edge_metrics = {}
    for edge_id, sample in samples.items():
        sample_count = sample["sample_count"]
        if not sample_count:
            continue
        edge_meta = metadata[edge_id]
        average_speed_kmh = (
            sample["speed_sum_mps"] / sample_count * 3.6
        )
        speed_congestion = max(
            0.0,
            min(
                1.0,
                1.0 - average_speed_kmh / max(1.0, edge_meta["speed_limit_kmh"]),
            ),
        )
        stopped_ratio = sample["stopped_sample_count"] / sample_count
        congestion = max(speed_congestion, stopped_ratio)
        edge_metrics[edge_id] = {
            **edge_meta,
            "average_speed_kmh": round(average_speed_kmh, 1),
            "congestion": round(congestion, 4),
            "vehicle_count": len(sample["vehicle_ids"]),
            "sample_count": sample_count,
        }

    return edge_metrics, observed_timestep_count, len(observed_vehicle_ids)


def _heatmap_bbox(features: list[dict]) -> Optional[list[float]]:
    coordinates = [
        coordinate
        for feature in features
        for coordinate in feature.get("geometry", {}).get("coordinates", [])
    ]
    if not coordinates:
        return None
    lons = [float(coordinate[0]) for coordinate in coordinates]
    lats = [float(coordinate[1]) for coordinate in coordinates]
    return [min(lons), min(lats), max(lons), max(lats)]


def _build_heatmap_feature_collection(
    edge_metrics: dict[str, dict],
    observed_timestep_count: int,
    mode: str,
    observed_vehicle_count: int,
    comparison_metrics: Optional[dict[str, dict]] = None,
) -> dict:
    """Build the GeoJSON shape consumed by the frontend network heatmap."""
    features = []
    comparison_metrics = comparison_metrics or {}

    for edge_id, metrics in edge_metrics.items():
        properties = {
            "edge_id": edge_id,
            "road_name": metrics["road_name"],
            "congestion": metrics["congestion"],
            "intensity": metrics["congestion"],
            "avg_speed_kmh": metrics["average_speed_kmh"],
                "vehicle_count": metrics["vehicle_count"],
                "sample_count": metrics["sample_count"],
                # coverage: fraction of observed timesteps with at least one sample
                "coverage": round(
                    (metrics.get("sample_count", 0) / observed_timestep_count) if observed_timestep_count else 0.0,
                    4,
                ),
                # confidence flag: true when sample_count meets minimum threshold
                "confidence": bool(metrics.get("sample_count", 0) >= HEATMAP_MIN_SAMPLE_COUNT),
            "speed_limit_kmh": metrics["speed_limit_kmh"],
        }

        if mode == "difference":
            before = comparison_metrics.get(edge_id)
            if before is None:
                continue
            properties.update({
                "before_congestion": before["congestion"],
                "after_congestion": metrics["congestion"],
                "delta_congestion": round(
                    metrics["congestion"] - before["congestion"], 4
                ),
                "before_speed_kmh": before["average_speed_kmh"],
                "after_speed_kmh": metrics["average_speed_kmh"],
                "before_vehicle_count": before["vehicle_count"],
                "after_vehicle_count": metrics["vehicle_count"],
            })

        features.append({
            "type": "Feature",
            "geometry": metrics["geometry"],
            "properties": properties,
        })

    summary = {}
    hotspots = []
    if mode == "difference":
        def edge_length_m(feature):
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            if len(coordinates) < 2:
                return 0.0
            total = 0.0
            for first, second in zip(coordinates, coordinates[1:]):
                lon1, lat1 = map(math.radians, first[:2])
                lon2, lat2 = map(math.radians, second[:2])
                dlon = lon2 - lon1
                dlat = lat2 - lat1
                a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
                total += 6371000.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))
            return total

        worsened = [
            feature for feature in features
            if feature["properties"].get("delta_congestion", 0)
            >= HEATMAP_CONGESTION_THRESHOLD
            and edge_length_m(feature) >= HEATMAP_MIN_HOTSPOT_LENGTH_M
        ]
        improved = [
            feature for feature in features
            if feature["properties"].get("delta_congestion", 0)
            <= -HEATMAP_CONGESTION_THRESHOLD
            and edge_length_m(feature) >= HEATMAP_MIN_HOTSPOT_LENGTH_M
        ]
        summary = {
            "total_edge_count": len(features),
            "worsened_edge_count": len(worsened),
            "improved_edge_count": len(improved),
            "stable_edge_count": len(features) - len(worsened) - len(improved),
        }
        def hotspot_summary(feature):
            properties = feature["properties"]
            return {
                "edge_id": properties["edge_id"],
                "road_name": properties["road_name"],
                "delta_congestion": properties["delta_congestion"],
                "after_speed_kmh": properties["after_speed_kmh"],
            }

        hotspots = [
            hotspot_summary(feature)
            for feature in sorted(
                worsened,
                key=lambda item: item["properties"].get("delta_congestion", 0),
                reverse=True,
            )[:10]
        ]
        improved_hotspots = [
            hotspot_summary(feature)
            for feature in sorted(
                improved,
                key=lambda item: item["properties"].get("delta_congestion", 0),
            )[:10]
        ]
    else:
        improved_hotspots = []

    return {
        "type": "FeatureCollection",
        "features": features,
        "observed_edge_count": len(features),
        "observed_vehicle_count": observed_vehicle_count,
        "observed_timestep_count": observed_timestep_count,
        "zone_bbox": _heatmap_bbox(features),
        "summary": summary,
        "hotspots": hotspots,
        "improved_hotspots": improved_hotspots,
    }


def _build_simulation_heatmap(
    before_fcd_path: Path,
    after_fcd_path: Path,
    baseline_net,
    modified_net,
    begin_time: int,
    end_time: int,
) -> dict:
    """Create before/after/difference network heatmaps from two FCD files."""
    before_metrics, before_timesteps, before_vehicle_count = _parse_fcd_edge_metrics(
        before_fcd_path, baseline_net, begin_time, end_time
    )
    after_metrics, after_timesteps, after_vehicle_count = _parse_fcd_edge_metrics(
        after_fcd_path, modified_net, begin_time, end_time
    )
    return {
        "schema_version": 1,
        "before": _build_heatmap_feature_collection(
            before_metrics,
            before_timesteps,
            "before",
            observed_vehicle_count=before_vehicle_count,
        ),
        "after": _build_heatmap_feature_collection(
            after_metrics,
            after_timesteps,
            "after",
            observed_vehicle_count=after_vehicle_count,
        ),
        "difference": _build_heatmap_feature_collection(
            after_metrics,
            after_timesteps,
            "difference",
            observed_vehicle_count=after_vehicle_count,
            comparison_metrics=before_metrics,
        ),
    }


def _parse_tripinfo_rows(tripinfo_path: Path) -> dict[str, dict]:
    """Read SUMO's per-vehicle trip metrics keyed by vehicle id."""
    if not tripinfo_path.exists():
        return {}

    try:
        trips = ET.parse(tripinfo_path).getroot().findall("tripinfo")
    except (ET.ParseError, OSError):
        return {}

    rows = {}
    for trip in trips:
        vehicle_id = trip.get("id")
        if not vehicle_id:
            continue
        try:
            arrival = float(trip.get("arrival", -1) or -1)
            duration = float(trip.get("duration", 0) or 0)
            route_length_m = float(trip.get("routeLength", 0) or 0)
            waiting_time_sec = float(trip.get("waitingTime", 0) or 0)
        except (TypeError, ValueError):
            continue
        rows[vehicle_id] = {
            "vehicle_id": vehicle_id,
            "arrival": arrival,
            "duration_sec": duration,
            "route_length_m": route_length_m,
            "waiting_time_sec": waiting_time_sec,
            "completed": arrival >= 0 and duration >= 0,
        }
    return rows


def _aggregate_tripinfo_metrics(
    tripinfo_path: Path,
    vehicle_assignments: dict[str, int],
) -> dict:
    """Aggregate actual SUMO tripinfo for vehicles assigned to each route."""
    tripinfo_rows = _parse_tripinfo_rows(tripinfo_path)
    assigned_rows = []
    for vehicle_id, route_index in vehicle_assignments.items():
        row = dict(tripinfo_rows.get(vehicle_id, {}))
        row["vehicle_id"] = vehicle_id
        row["route_index"] = route_index
        row.setdefault("completed", False)
        assigned_rows.append(row)

    observed_count = sum(
        1 for row in assigned_rows if row["vehicle_id"] in tripinfo_rows
    )
    completed_rows = [
        row for row in assigned_rows
        if row.get("completed") and float(row.get("duration_sec", 0) or 0) >= 0
    ]
    travel_times = [float(row.get("duration_sec", 0) or 0) for row in completed_rows]
    speeds_kmh = [
        (float(row.get("route_length_m", 0) or 0) / row["duration_sec"]) * 3.6
        for row in completed_rows
        if float(row.get("duration_sec", 0) or 0) > 0
        and float(row.get("route_length_m", 0) or 0) > 0
    ]
    congestion_ratios = [
        float(row.get("waiting_time_sec", 0) or 0) / float(row["duration_sec"])
        for row in completed_rows
        if float(row.get("duration_sec", 0) or 0) > 0
    ]

    return {
        "throughput": len(completed_rows),
        "metric_source": "SUMO tripinfo.xml",
        "avg_travel_time_sec": round(sum(travel_times) / len(travel_times), 2)
        if travel_times else 0.0,
        "avg_speed_kmh": round(sum(speeds_kmh) / len(speeds_kmh), 2)
        if speeds_kmh else 0.0,
        "active_vehicles": max(0, len(assigned_rows) - len(completed_rows)),
        "avg_congestion_ratio": round(sum(congestion_ratios) / len(congestion_ratios), 4)
        if congestion_ratios else 0.0,
        "matched_vehicle_count": len(assigned_rows),
        "completed_vehicle_count": len(completed_rows),
        "tripinfo_vehicle_count": observed_count,
        "debug_tripinfo_rows": assigned_rows,
    }


def _parse_tripinfo(tripinfo_path: Path) -> dict:
    if not tripinfo_path.exists():
        return {
            "throughput": 0,
            "avg_travel_time_sec": 0.0,
            "avg_speed_kmh": 0.0,
            "active_vehicles": 0,
            "avg_congestion_ratio": 0.0,
        }

    root = ET.parse(tripinfo_path).getroot()
    trips = list(root)
    durations = []
    waiting_times = []
    speeds_kmh = []

    for trip in trips:
        duration = float(trip.get("duration", 0) or 0)
        waiting = float(trip.get("waitingTime", 0) or 0)
        if duration > 0:
            durations.append(duration)
            waiting_times.append(waiting)
            route_length_m = float(trip.get("routeLength", 0) or 0)
            if route_length_m > 0:
                speeds_kmh.append((route_length_m / duration) * 3.6)

    throughput = len(durations)
    avg_travel = sum(durations) / throughput if throughput else 0.0
    avg_waiting = sum(waiting_times) / len(waiting_times) if waiting_times else 0.0
    congestion_ratio = (avg_waiting / avg_travel) if avg_travel > 0 else 0.0

    return {
        "throughput": throughput,
        "avg_travel_time_sec": round(avg_travel, 2),
        "avg_speed_kmh": round(sum(speeds_kmh) / len(speeds_kmh), 2)
        if speeds_kmh else 0.0,
        "active_vehicles": 0,
        "avg_congestion_ratio": round(min(1.0, congestion_ratio), 4),
    }


def _parse_summary(summary_path: Path) -> dict:
    if not summary_path.exists():
        return {"active_vehicles": 0, "avg_congestion_ratio": 0.0}

    root = ET.parse(summary_path).getroot()
    steps = list(root)
    if not steps:
        return {"active_vehicles": 0, "avg_congestion_ratio": 0.0}

    congestion_samples = []
    last_running = 0
    for step in steps:
        running = int(float(step.get("running", 0) or 0))
        waiting = int(float(step.get("waiting", 0) or 0))
        last_running = running
        if running > 0:
            congestion_samples.append(waiting / running)

    avg_congestion = (
        sum(congestion_samples) / len(congestion_samples) if congestion_samples else 0.0
    )
    return {
        "active_vehicles": last_running,
        "avg_congestion_ratio": round(min(1.0, avg_congestion), 4),
    }


def _merge_run_metrics(tripinfo_metrics: dict, summary_metrics: dict) -> dict:
    merged = dict(tripinfo_metrics)
    merged["active_vehicles"] = summary_metrics.get("active_vehicles", 0)
    if summary_metrics.get("avg_congestion_ratio", 0) > 0:
        merged["avg_congestion_ratio"] = summary_metrics["avg_congestion_ratio"]
    return merged


def _parse_sumo_statistics(statistics_path: Optional[Path]) -> dict:
    """Read SUMO --statistic-output: insertion and teleport counts."""
    empty = {
        "loaded_vehicle_count": None,
        "inserted_vehicle_count": None,
        "waiting_to_insert_count": None,
        "teleport_count": None,
        "teleport_jam_count": None,
        "teleport_yield_count": None,
        "teleport_wrong_lane_count": None,
    }
    if statistics_path is None or not statistics_path.exists():
        return empty
    try:
        root = ET.parse(statistics_path).getroot()
    except (ET.ParseError, OSError):
        return empty

    def as_int(element, attribute):
        try:
            return int(float(element.get(attribute))) if element is not None and element.get(attribute) is not None else None
        except (TypeError, ValueError):
            return None

    vehicles = root.find("vehicles")
    teleports = root.find("teleports")
    return {
        "loaded_vehicle_count": as_int(vehicles, "loaded"),
        "inserted_vehicle_count": as_int(vehicles, "inserted"),
        "waiting_to_insert_count": as_int(vehicles, "waiting"),
        "teleport_count": as_int(teleports, "total"),
        "teleport_jam_count": as_int(teleports, "jam"),
        "teleport_yield_count": as_int(teleports, "yield"),
        "teleport_wrong_lane_count": as_int(teleports, "wrongLane"),
    }


def _measurement_window(start_time: str, end_time: str) -> tuple[int, int]:
    """Departure window whose vehicles are measured (warm-up/cool-down excluded).

    Matches the core of ``_determine_simulation_time_window``; the SUMO run
    itself is padded on both sides so measured vehicles start in a loaded
    network and have time to finish.
    """
    begin_sec = int(_parse_time_to_seconds(start_time))
    end_sec = int(_parse_time_to_seconds(end_time))
    if end_sec - begin_sec < 1800:
        end_sec = begin_sec + 1800
    return begin_sec, end_sec


def _route_file_vehicles(route_file: Path) -> dict[str, dict]:
    """Vehicle id -> {depart, edges} for a route file with nested routes."""
    vehicles: dict[str, dict] = {}
    try:
        root = ET.parse(route_file).getroot()
    except (ET.ParseError, OSError):
        return vehicles
    for vehicle in root.findall("vehicle"):
        vehicle_id = vehicle.get("id")
        route = vehicle.find("route")
        if not vehicle_id or route is None:
            continue
        try:
            depart = float(vehicle.get("depart", 0) or 0)
        except (TypeError, ValueError):
            continue
        vehicles[vehicle_id] = {"depart": depart, "edges": route.get("edges", "").split()}
    return vehicles


def _cohort_trip_metrics(tripinfo_rows: dict[str, dict], vehicle_ids) -> dict:
    """Summarise SUMO tripinfo for a fixed vehicle cohort.

    Vehicles that never appear in tripinfo (never inserted) and vehicles that
    did not arrive count against completion instead of being ignored.
    """
    ids = list(vehicle_ids)
    completed = [
        tripinfo_rows[vehicle_id]
        for vehicle_id in ids
        if tripinfo_rows.get(vehicle_id, {}).get("completed")
    ]
    travel_times = [row["duration_sec"] for row in completed]
    speeds_kmh = [
        row["route_length_m"] / row["duration_sec"] * 3.6
        for row in completed
        if row["duration_sec"] > 0 and row["route_length_m"] > 0
    ]
    waiting_times = [row["waiting_time_sec"] for row in completed]
    congestion_ratios = [
        row["waiting_time_sec"] / row["duration_sec"]
        for row in completed
        if row["duration_sec"] > 0
    ]

    def mean(values):
        return round(sum(values) / len(values), 2) if values else 0.0

    return {
        "metric_source": "SUMO tripinfo.xml",
        "matched_vehicle_count": len(ids),
        "throughput": len(completed),
        "completed_vehicle_count": len(completed),
        "active_vehicles": max(0, len(ids) - len(completed)),
        "completion_rate_pct": round(len(completed) / len(ids) * 100, 1) if ids else 0.0,
        "avg_travel_time_sec": mean(travel_times),
        "median_travel_time_sec": round(statistics.median(travel_times), 2) if travel_times else 0.0,
        "avg_speed_kmh": mean(speeds_kmh),
        "avg_waiting_time_sec": mean(waiting_times),
        "avg_congestion_ratio": round(sum(congestion_ratios) / len(congestion_ratios), 4)
        if congestion_ratios else 0.0,
    }


def _bootstrap_mean_ci(values: list[float], resamples: int = BOOTSTRAP_RESAMPLES) -> Optional[list[float]]:
    """Percentile bootstrap 95% CI of the mean, seeded for reproducibility."""
    if len(values) < 2:
        return None
    try:
        import numpy as np

        rng = np.random.default_rng(STATISTICS_RANDOM_SEED)
        data = np.asarray(values, dtype=float)
        # Batch the resamples so large network-wide cohorts stay small in memory.
        batch = max(1, min(resamples, 2_000_000 // data.size))
        means = []
        remaining = resamples
        while remaining > 0:
            size = min(batch, remaining)
            means.append(rng.choice(data, size=(size, data.size), replace=True).mean(axis=1))
            remaining -= size
        low, high = np.percentile(np.concatenate(means), [2.5, 97.5])
        return [round(float(low), 2), round(float(high), 2)]
    except (ImportError, MemoryError):
        rng = random.Random(STATISTICS_RANDOM_SEED)
        count = min(resamples, 500)
        means = sorted(
            sum(rng.choices(values, k=len(values))) / len(values) for _ in range(count)
        )
        return [round(means[int(0.025 * count)], 2), round(means[int(0.975 * count) - 1], 2)]


def _paired_travel_time_statistics(
    before_rows: dict[str, dict],
    after_rows: dict[str, dict],
    vehicle_ids,
) -> dict:
    """Per-vehicle before/after travel-time differences (after - before).

    Only vehicles that completed their trip in both runs are paired, so the
    comparison is not biased by vehicles that did not finish.
    """
    before_times, after_times = [], []
    for vehicle_id in vehicle_ids:
        before = before_rows.get(vehicle_id)
        after = after_rows.get(vehicle_id)
        if before and after and before.get("completed") and after.get("completed"):
            before_times.append(before["duration_sec"])
            after_times.append(after["duration_sec"])

    deltas = [after - before for before, after in zip(before_times, after_times)]
    result = {
        "paired_vehicle_count": len(deltas),
        "mean_before_sec": None,
        "mean_after_sec": None,
        "mean_delta_sec": None,
        "median_delta_sec": None,
        "ci95_mean_delta_sec": None,
        "improved_vehicle_count": sum(1 for delta in deltas if delta < 0),
        "worsened_vehicle_count": sum(1 for delta in deltas if delta > 0),
        "wilcoxon_p_value": None,
        "time_reduction_pct": None,
    }
    if not deltas:
        return result

    mean_before = sum(before_times) / len(before_times)
    mean_after = sum(after_times) / len(after_times)
    result.update(
        {
            "mean_before_sec": round(mean_before, 2),
            "mean_after_sec": round(mean_after, 2),
            "mean_delta_sec": round(sum(deltas) / len(deltas), 2),
            "median_delta_sec": round(statistics.median(deltas), 2),
            "ci95_mean_delta_sec": _bootstrap_mean_ci(deltas),
            "time_reduction_pct": round((mean_before - mean_after) / mean_before * 100, 1)
            if mean_before > 0 else None,
        }
    )
    if len(deltas) >= 10 and any(deltas):
        try:
            from scipy.stats import wilcoxon

            result["wilcoxon_p_value"] = float(wilcoxon(deltas).pvalue)
        except Exception:  # noqa: BLE001 - optional dependency / degenerate data
            pass
    return result


def _percent_reduction(before: float, after: float) -> Optional[float]:
    return round((before - after) / before * 100, 1) if before and before > 0 else None


def _scope_comparison(
    label: str,
    before_rows: dict[str, dict],
    after_rows: dict[str, dict],
    vehicle_ids,
) -> dict:
    """Before/after comparison of one fixed vehicle cohort."""
    ids = sorted(vehicle_ids)
    before = _cohort_trip_metrics(before_rows, ids)
    after = _cohort_trip_metrics(after_rows, ids)
    paired = _paired_travel_time_statistics(before_rows, after_rows, ids)
    unpaired_time_reduction = _percent_reduction(before["avg_travel_time_sec"], after["avg_travel_time_sec"])
    return {
        "label": label,
        "vehicle_count": len(ids),
        "before": before,
        "after": after,
        "paired": paired,
        # Paired means are the headline: both runs cover the same vehicles.
        "time_reduction_pct": paired["time_reduction_pct"]
        if paired["time_reduction_pct"] is not None else unpaired_time_reduction,
        "congestion_reduction_pct": _percent_reduction(
            before["avg_congestion_ratio"], after["avg_congestion_ratio"]
        ),
        "speed_change_pct": round(
            (after["avg_speed_kmh"] - before["avg_speed_kmh"]) / before["avg_speed_kmh"] * 100, 1
        ) if before["avg_speed_kmh"] > 0 else None,
    }


def _corridor_candidate_routes(net, origin: dict, destination: dict) -> list[dict]:
    """The same three O-D corridor routes the corridor simulation reports."""
    start_edge = _nearest_drivable_edge(net, float(origin["lon"]), float(origin["lat"]))
    end_edge = _nearest_drivable_edge(net, float(destination["lon"]), float(destination["lat"]))
    raw_routes = _find_candidate_routes(net, start_edge, end_edge, route_count=ROUTE_SEARCH_LIMIT)
    selected = _select_routes_with_fallbacks(raw_routes, route_count=3) if raw_routes else []
    return [
        {
            "id": index + 1,
            "edge_ids": [edge.getID() for edge in route],
            "length_m": round(sum(edge.getLength() for edge in route), 1),
            "geometry": _route_to_geojson(net, route, origin, destination),
        }
        for index, route in enumerate(selected)
    ]


def _run_sumo_scenario(
    net_path: Path,
    route_path: Path,
    simulation_time: int,
    temp_dir: Path,
    label: str,
    gui: bool = False,
    begin_time: int = 0,
    end_time: int = 10000,
) -> dict:
    tripinfo_path = temp_dir / f"{label}_tripinfo.xml"
    summary_path = temp_dir / f"{label}_summary.xml"
    _run_sumo(
        net_path,
        route_path,
        simulation_time,
        tripinfo_path,
        summary_path,
        gui=gui,
        begin_time=begin_time,
        end_time=end_time,
    )

    tripinfo_metrics = _parse_tripinfo(tripinfo_path)
    summary_metrics = _parse_summary(summary_path)
    metrics = _merge_run_metrics(tripinfo_metrics, summary_metrics)

    for path in (tripinfo_path, summary_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return metrics


CONTROL_NET = AUTO_TOOL_DIR / "cleaned_network.control.net.xml"


def _control_baseline_net() -> Path:
    """Baseline network rebuilt by the same netconvert as the modified network.

    netconvert does not round-trip a network unchanged: re-deriving junction
    geometry, right-of-way and signal plans (and any version differences in
    netconvert) alters travel times even with no edits. The modified network
    always goes through netconvert, so the "before" run must use a network
    that went through the identical pass with no modifications; otherwise the
    rebuild itself is measured as part of the intervention effect.
    """
    netconvert = _netconvert_binary()
    if not netconvert or not BASELINE_NET.exists():
        return BASELINE_NET

    stamp_path = CONTROL_NET.with_name(CONTROL_NET.name + ".stamp")
    baseline_stat = BASELINE_NET.stat()
    netconvert_stat = Path(netconvert).stat()
    signature = (
        f"{baseline_stat.st_size}:{baseline_stat.st_mtime_ns}:"
        f"{netconvert}:{netconvert_stat.st_mtime_ns}"
    )
    try:
        if CONTROL_NET.exists() and stamp_path.read_text(encoding="utf-8") == signature:
            return CONTROL_NET
    except OSError:
        pass

    result = subprocess.run(
        [netconvert, "--sumo-net-file", str(BASELINE_NET), "--output-file", str(CONTROL_NET)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not CONTROL_NET.exists():
        logger.warning(
            "Control network rebuild failed; comparing against the original baseline: %s",
            (result.stderr or result.stdout or "unknown error").strip(),
        )
        return BASELINE_NET
    stamp_path.write_text(signature, encoding="utf-8")
    return CONTROL_NET


def _resolve_after_net_path(modified_graph, is_feasible: bool) -> Path:
    """Create the modified SUMO network for simulation or feasibility preview."""
    output_path = AUTO_TOOL_DIR / "cleaned_modified_network.net.xml"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if modified_graph is None:
        raise RuntimeError("Modified SUMO network was not generated because no modified graph was provided.")

    edge_file = AUTO_TOOL_DIR / "proposed_edges.edg.xml"
    node_file = AUTO_TOOL_DIR / "proposed_nodes.nod.xml"
    connection_file = AUTO_TOOL_DIR / "proposed_connections.con.xml"
    remove_edge_file = AUTO_TOOL_DIR / "road_block_edges.txt"
    coordinate_offset = _sumo_net_offset(BASELINE_NET)
    edge_file_path = _write_proposed_edge_file(
        modified_graph,
        edge_file,
        coordinate_offset=coordinate_offset,
    )
    node_file_path = _write_proposed_node_file(
        modified_graph,
        node_file,
        coordinate_offset=coordinate_offset,
    )
    connection_file_path = _write_proposed_connection_file(
        modified_graph,
        BASELINE_NET,
        connection_file,
    )
    widened_lanes = _widened_edge_lanes(modified_graph, BASELINE_NET, coordinate_offset)
    if modified_graph is not None:
        modified_graph.graph["uidt_widened_edges"] = widened_lanes
    widening_file_path = _write_widening_edge_file(
        widened_lanes, AUTO_TOOL_DIR / "widened_edges.edg.xml"
    )
    blocked_edges = _blocked_edge_ids(modified_graph, BASELINE_NET, coordinate_offset)
    modified_graph.graph["uidt_blocked_edges"] = blocked_edges
    remove_edge_file_path = _write_remove_edge_file(blocked_edges, remove_edge_file)
    edge_files = [path for path in (edge_file_path, widening_file_path) if path is not None]

    if BASELINE_NET.exists() and (edge_files or remove_edge_file_path is not None):
        netconvert = _netconvert_binary()
        if netconvert:
            command = [
                netconvert,
                "--sumo-net-file",
                str(BASELINE_NET),
                "--output-file",
                str(output_path),
            ]
            if edge_files:
                command[command.index("--output-file"):command.index("--output-file")] = [
                    "--edge-files",
                    ",".join(str(path) for path in edge_files),
                ]
            if remove_edge_file_path is not None:
                command.extend(["--remove-edges.input-file", str(remove_edge_file_path)])
            if node_file_path is not None:
                command.extend(["--node-files", str(node_file_path)])
            if connection_file_path is not None:
                command.extend(["--connection-files", str(connection_file_path)])

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and output_path.exists():
                for path in edge_files:
                    path.unlink(missing_ok=True)
                if remove_edge_file_path is not None:
                    remove_edge_file_path.unlink(missing_ok=True)
                if node_file_path is not None:
                    node_file_path.unlink(missing_ok=True)
                if connection_file_path is not None:
                    connection_file_path.unlink(missing_ok=True)
                return output_path

            logger.warning(
                "netconvert failed for modified network: %s",
                (result.stderr or result.stdout or "unknown error").strip(),
            )
            raise RuntimeError(
                "netconvert failed while building the modified SUMO network; see logs for details."
            )

        raise RuntimeError("netconvert was not found on PATH, so the modified SUMO network could not be generated.")

    if edge_file.exists():
        edge_file.unlink(missing_ok=True)
    if node_file.exists():
        node_file.unlink(missing_ok=True)
    if connection_file.exists():
        connection_file.unlink(missing_ok=True)
    if remove_edge_file.exists():
        remove_edge_file.unlink(missing_ok=True)

    raise RuntimeError("Modified SUMO network could not be generated.")


def _build_result_dict(
    before: dict,
    after: dict,
    arrival_rate: float,
    steps: int,
    route_window_vehicle_count: int = 0,
    route_file: Path = BASELINE_ROU,
    day_of_week: Optional[str] = None,
) -> dict:
    time_before = before["avg_travel_time_sec"]
    time_after = after["avg_travel_time_sec"]
    time_reduction_pct = 0.0
    if time_before > 0:
        time_reduction_pct = round(((time_before - time_after) / time_before) * 100, 1)

    congestion_before = before["avg_congestion_ratio"]
    congestion_after = after["avg_congestion_ratio"]
    congestion_reduction_pct = 0.0
    if congestion_before > 0:
        congestion_reduction_pct = round(
            ((congestion_before - congestion_after) / congestion_before) * 100, 1
        )

    return {
        "status": "complete",
        "steps": steps,
        "arrival_rate": round(arrival_rate, 2),
        "route_file": str(route_file),
        "route_file_source": (
            f"SUMO calibrated route file ({day_of_week})" if day_of_week
            else "SUMO baseline route file"
        ),
        "route_file_vehicle_count": _baseline_vehicle_count(route_file),
        "route_file_window_vehicle_count": int(route_window_vehicle_count),
        "before": before,
        "after": after,
        "avg_travel_time_before_sec": time_before,
        "avg_travel_time_after_sec": time_after,
        "avg_travel_time_before_min": round(time_before / 60, 2),
        "avg_travel_time_after_min": round(time_after / 60, 2),
        "avg_speed_before_kmh": before.get("avg_speed_kmh", 0.0),
        "avg_speed_after_kmh": after.get("avg_speed_kmh", 0.0),
        "time_reduction_pct": time_reduction_pct,
        "congestion_reduction_pct": congestion_reduction_pct,
        "hourly_traffic": [],
        "hourly_traffic_source": "sumo_route_file",
    }

def _route_to_geojson(
    net,
    edge_objects: list,
    origin: Optional[dict] = None,
    destination: Optional[dict] = None,
) -> dict:
    """
    Convert a list of SUMO edge objects to a GeoJSON LineString.
    Coordinates are in [lon, lat] order as required by GeoJSON spec.

    SUMO routes start at the beginning of the nearest edge and end at the end
    of the nearest edge. The UI markers represent the user's exact clicks,
    which are usually in the middle of those edges. Anchor the public geometry
    to those exact locations while leaving the underlying SUMO edge sequence
    unchanged for simulation.
    """
    route_points = []
    edge_segment_ranges = []
    for edge in edge_objects:
        shape = [(float(x), float(y)) for x, y in edge.getShape()]
        if not shape:
            continue

        if route_points and route_points[-1] == shape[0]:
            edge_start_index = len(route_points) - 1
        else:
            edge_start_index = len(route_points)

        for point in shape:
            if not route_points or route_points[-1] != point:
                route_points.append(point)

        edge_segment_ranges.append(
            range(edge_start_index, max(edge_start_index, len(route_points) - 1))
        )

    if not route_points:
        return {"type": "LineString", "coordinates": []}

    def nearest_segment_index(target, segment_range):
        best_index = None
        best_distance = float("inf")
        target_x, target_y = target
        for index in segment_range:
            start_x, start_y = route_points[index]
            end_x, end_y = route_points[index + 1]
            delta_x = end_x - start_x
            delta_y = end_y - start_y
            segment_length_sq = delta_x * delta_x + delta_y * delta_y
            if segment_length_sq:
                projection = (
                    (target_x - start_x) * delta_x
                    + (target_y - start_y) * delta_y
                ) / segment_length_sq
                projection = max(0.0, min(1.0, projection))
            else:
                projection = 0.0
            projected_x = start_x + projection * delta_x
            projected_y = start_y + projection * delta_y
            distance = (target_x - projected_x) ** 2 + (target_y - projected_y) ** 2
            if distance < best_distance:
                best_distance = distance
                best_index = index
        return best_index

    origin_xy = None
    destination_xy = None
    start_segment_index = None
    end_segment_index = None
    if origin and edge_segment_ranges:
        origin_xy = net.convertLonLat2XY(
            float(origin["lon"]), float(origin["lat"])
        )
        start_segment_index = nearest_segment_index(
            origin_xy, edge_segment_ranges[0]
        )
    if destination and edge_segment_ranges:
        destination_xy = net.convertLonLat2XY(
            float(destination["lon"]), float(destination["lat"])
        )
        end_segment_index = nearest_segment_index(
            destination_xy, edge_segment_ranges[-1]
        )

    display_points = route_points
    if origin_xy is not None and destination_xy is not None:
        if (
            start_segment_index is not None
            and end_segment_index is not None
            and start_segment_index <= end_segment_index
        ):
            display_points = (
                [origin_xy]
                + route_points[start_segment_index + 1 : end_segment_index + 1]
                + [destination_xy]
            )
        else:
            display_points = [origin_xy, destination_xy]
    elif origin_xy is not None and start_segment_index is not None:
        display_points = [origin_xy] + route_points[start_segment_index + 1 :]
    elif destination_xy is not None and end_segment_index is not None:
        display_points = route_points[: end_segment_index + 1] + [destination_xy]

    coords = []
    for x, y in display_points:
        lon, lat = net.convertXY2LonLat(x, y)
        point = [lon, lat]
        if not coords or coords[-1] != point:
            coords.append(point)

    if coords and origin:
        coords[0] = [float(origin["lon"]), float(origin["lat"])]
    if coords and destination:
        coords[-1] = [float(destination["lon"]), float(destination["lat"])]

    return {"type": "LineString", "coordinates": coords}


def _proposal_to_geojson(proposal_coordinates) -> Optional[dict]:
    """Return the exact user-drawn line for public Route 4 geometry.

    SUMO still needs snapped network edges and endpoint connectors internally,
    but those are implementation details and must not replace the alignment
    the user drew on the map.
    """
    if not isinstance(proposal_coordinates, (list, tuple)) or len(proposal_coordinates) < 2:
        return None

    coordinates = []
    for coordinate in proposal_coordinates:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            return None
        try:
            coordinates.append([float(coordinate[0]), float(coordinate[1])])
        except (TypeError, ValueError):
            return None

    return {"type": "LineString", "coordinates": coordinates}

def _run_sumo_simulation(
    demand_data: dict,
    modified_graph,
    is_feasible: bool,
    arrival_rate: float,
    start_time: str = "09:00",
    end_time: str = "10:00",
    day_of_week: Optional[str] = None,
    origin: Optional[dict] = None,
    destination: Optional[dict] = None,
) -> dict:
    """Before/after SUMO run for widening and road-block modifications.

    Both runs use the same demand. Only vehicles whose route crosses a blocked
    edge are rerouted; everyone else keeps their baseline route. Results are
    reported for fixed vehicle cohorts departing in the user's time window:

    * ``network``  - every measured vehicle,
    * ``affected`` - vehicles whose baseline route uses a modified segment,
    * ``corridor`` - vehicles matched to the selected O-D corridor routes.

    Headline travel times are paired per-vehicle means, so a vehicle that
    fails to finish in one run cannot bias the comparison.
    """
    baseline_route = _resolve_baseline_route(day_of_week)
    if not BASELINE_NET.exists() or not baseline_route.exists():
        raise FileNotFoundError("SUMO baseline network or route files are missing from auto_tool/")

    after_net = _resolve_after_net_path(modified_graph, is_feasible)
    graph_attributes = modified_graph.graph if modified_graph is not None else {}
    blocked_edge_ids = set(graph_attributes.get("uidt_blocked_edges") or ())
    widened_lanes = dict(graph_attributes.get("uidt_widened_edges") or {})
    widened_base_lanes = dict(graph_attributes.get("uidt_widened_base_lanes") or {})
    modified_edge_ids = blocked_edge_ids | set(widened_lanes)

    begin_time, end_time_seconds = _determine_simulation_time_window(start_time, end_time)
    measure_begin, measure_end = _measurement_window(start_time, end_time)
    route_window_vehicle_count = _route_file_vehicle_count(
        baseline_route,
        begin_time=begin_time,
        end_time=end_time_seconds,
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="uidt_sumo_run_"))
    warnings: list[str] = []
    heatmap = None
    corridor_routes: list[dict] = []
    corridor_assignments: dict[str, int] = {}
    reroute_stats = _empty_reroute_stats()
    runs: dict[str, dict] = {}

    try:
        route_path = _prepare_route_file(
            BASELINE_NET,
            demand_data,
            route_window_vehicle_count,
            SIMULATION_SECONDS,
            temp_dir,
            route_file=baseline_route,
        )
        vehicles = _route_file_vehicles(route_path)
        measured_ids = {
            vehicle_id
            for vehicle_id, vehicle in vehicles.items()
            if measure_begin <= vehicle["depart"] < measure_end
        }
        affected_ids = {
            vehicle_id
            for vehicle_id in measured_ids
            if modified_edge_ids.intersection(vehicles[vehicle_id]["edges"])
        }

        baseline_net = _load_sumo_net(BASELINE_NET)
        if origin and destination and baseline_net is not None:
            try:
                corridor_routes = _corridor_candidate_routes(baseline_net, origin, destination)
                assignments = _classify_vehicles_to_routes(
                    route_path,
                    corridor_routes,
                    begin_time=measure_begin,
                    end_time=measure_end,
                )
                corridor_assignments = {
                    vehicle_id: route_index
                    for vehicle_id, route_index in assignments.items()
                    if vehicle_id in measured_ids
                }
            except Exception as exc:  # noqa: BLE001 - corridor view is optional
                warnings.append(f"Corridor route matching unavailable: {exc}")

        after_route_path = route_path
        if blocked_edge_ids:
            after_route_path, reroute_stats = _reroute_around_blocked_edges(
                route_path,
                after_net,
                blocked_edge_ids,
                temp_dir,
                baseline_net_path=BASELINE_NET,
            )

        control_net = _control_baseline_net()
        for label, net_path, run_route_path in (
            ("before", control_net, route_path),
            ("after", after_net, after_route_path),
        ):
            outputs = {
                kind: temp_dir / f"{label}_{kind}.xml"
                for kind in ("tripinfo", "summary", "statistics", "fcd")
            }
            _run_sumo(
                net_path,
                run_route_path,
                SIMULATION_SECONDS,
                outputs["tripinfo"],
                outputs["summary"],
                fcd_path=outputs["fcd"],
                begin_time=begin_time,
                end_time=end_time_seconds,
                statistics_path=outputs["statistics"],
            )
            runs[label] = {
                "rows": _parse_tripinfo_rows(outputs["tripinfo"]),
                "summary": _parse_summary(outputs["summary"]),
                "sumo": _parse_sumo_statistics(outputs["statistics"]),
                "fcd": outputs["fcd"],
            }

        try:
            heatmap = _build_simulation_heatmap(
                before_fcd_path=runs["before"]["fcd"],
                after_fcd_path=runs["after"]["fcd"],
                baseline_net=baseline_net,
                modified_net=_load_sumo_net(after_net),
                begin_time=begin_time,
                end_time=end_time_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - heatmap is supplementary
            warnings.append(f"Congestion heatmap unavailable: {exc}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    before_rows = runs["before"]["rows"]
    after_rows = runs["after"]["rows"]
    scopes = {
        "network": _scope_comparison(
            "All vehicles departing in the analysis window",
            before_rows, after_rows, measured_ids,
        )
    }
    if affected_ids:
        scopes["affected"] = _scope_comparison(
            "Vehicles whose baseline route uses a modified road segment",
            before_rows, after_rows, affected_ids,
        )
    if corridor_assignments:
        scopes["corridor"] = _scope_comparison(
            "Vehicles travelling the selected origin-destination corridor",
            before_rows, after_rows, corridor_assignments.keys(),
        )
    primary_scope = next(
        (
            key for key in ("corridor", "affected", "network")
            if key in scopes and scopes[key]["paired"]["paired_vehicle_count"] > 0
        ),
        "network",
    )
    primary = scopes[primary_scope]
    paired = primary["paired"]

    route_results = []
    for route_index, route in enumerate(corridor_routes):
        route_vehicle_ids = [
            vehicle_id
            for vehicle_id, assigned_index in corridor_assignments.items()
            if assigned_index == route_index
        ]
        route_results.append({
            "id": route["id"],
            "geometry": route["geometry"],
            "length_m": route["length_m"],
            "metrics": _cohort_trip_metrics(after_rows, route_vehicle_ids),
            "before_metrics": _cohort_trip_metrics(before_rows, route_vehicle_ids),
            "assigned_vehicle_count": len(route_vehicle_ids),
            "blocked_edge_count": len(blocked_edge_ids.intersection(route["edge_ids"])),
            "widened_edge_count": len(set(widened_lanes).intersection(route["edge_ids"])),
        })

    result = _build_result_dict(
        primary["before"],
        primary["after"],
        arrival_rate,
        SIMULATION_SECONDS,
        route_window_vehicle_count=route_window_vehicle_count,
        route_file=baseline_route,
        day_of_week=day_of_week,
    )
    if paired["paired_vehicle_count"]:
        time_before, time_after = paired["mean_before_sec"], paired["mean_after_sec"]
    else:
        time_before = primary["before"]["avg_travel_time_sec"]
        time_after = primary["after"]["avg_travel_time_sec"]
    network_paired = scopes["network"]["paired"]
    result.update({
        "avg_travel_time_before_sec": time_before,
        "avg_travel_time_after_sec": time_after,
        "avg_travel_time_before_min": round(time_before / 60, 2),
        "avg_travel_time_after_min": round(time_after / 60, 2),
        "time_reduction_pct": primary["time_reduction_pct"] or 0.0,
        "congestion_reduction_pct": primary["congestion_reduction_pct"] or 0.0,
        "travel_time_basis": "paired_vehicle_mean" if paired["paired_vehicle_count"] else "cohort_mean",
        "primary_metric_scope": primary_scope,
        "metric_scopes": scopes,
        "network_vehicle_hours_delta": round(
            (network_paired["mean_delta_sec"] or 0.0) * network_paired["paired_vehicle_count"] / 3600, 2
        ),
        "measurement_window_sec": [measure_begin, measure_end],
        "simulation_window_sec": [begin_time, end_time_seconds],
        "before_network": control_net.name,
        "routes": route_results,
        "heatmap": heatmap,
        "sumo_statistics": {"before": runs["before"]["sumo"], "after": runs["after"]["sumo"]},
        "network_summary": {"before": runs["before"]["summary"], "after": runs["after"]["summary"]},
        "road_modification": {
            "blocked_edge_count": len(blocked_edge_ids),
            "widened_edge_count": len(widened_lanes),
            "lanes_added": sum(
                max(0, lanes - widened_base_lanes.get(edge_id, lanes - 1))
                for edge_id, lanes in widened_lanes.items()
            ),
            "measured_vehicle_count": len(measured_ids),
            "affected_vehicle_count": len(affected_ids),
            "corridor_vehicle_count": len(corridor_assignments),
            **reroute_stats,
        },
        "road_block_rerouted_vehicle_count": reroute_stats["rerouted_vehicle_count"],
        "road_block_unroutable_vehicle_count": reroute_stats["unroutable_vehicle_count"],
        "warnings": warnings,
    })
    return result


def _nearest_drivable_edge(net, lon: float, lat: float):
    x, y = net.convertLonLat2XY(lon, lat)
    for radius in (50, 150, 500, 1500):
        candidates = sorted(net.getNeighboringEdges(x, y, radius), key=lambda item: item[1])
        for edge, _distance in candidates:
            if not edge.getID().startswith(":") and edge.allows("passenger"):
                return edge
    raise ValueError("The selected point is not close to a drivable SUMO road.")

def _build_networkx_graph(net):
    graph = nx.DiGraph()

    for edge in net.getEdges():

        # Ignore internal junction edges
        if edge.getID().startswith(":"):
            continue

        if not edge.allows("passenger"):
            continue

        from_node = edge.getFromNode().getID()
        to_node = edge.getToNode().getID()
        length_m = max(1.0, float(edge.getLength()))
        speed_mps = max(1.0, float(edge.getSpeed()))
        try:
            priority = float(edge.getPriority())
        except (AttributeError, TypeError, ValueError):
            priority = 0.0

        # SUMO priorities encode the road hierarchy (e.g. residential=3,
        # tertiary=10, secondary=11, trunk=13). Use travel time as the base
        # cost and give higher-class roads a modest preference so a natural
        # arterial route is not displaced by a shorter residential shortcut.
        hierarchy_bonus = 1.0 + min(0.5, max(0.0, priority - 3.0) * 0.04)
        routing_weight = length_m / speed_mps / hierarchy_bonus

        graph.add_edge(
            from_node,
            to_node,
            weight=length_m,
            routing_weight=routing_weight,
            edge_id=edge.getID(),
        )

    return graph






def _build_corridor_route_file(
    output_path: Path,
    origin: dict,
    destination: dict,
    selected_vehicle_count: int,
) -> dict:
    try:
        from sumolib.net import readNet
    except ImportError as exc:
        raise RuntimeError("sumolib is required for map-to-SUMO routing.") from exc

    net = readNet(str(BASELINE_NET), withInternal=False)

    start_edge = _nearest_drivable_edge(
        net, float(origin["lon"]), float(origin["lat"])
    )
    end_edge = _nearest_drivable_edge(
        net, float(destination["lon"]), float(destination["lat"])
    )

    path, _cost = net.getShortestPath(start_edge, end_edge, vClass="passenger")
    if not path:
        raise ValueError("SUMO could not find a drivable route between the selected points.")

    route_edges = [
        edge.getID() for edge in path if not edge.getID().startswith(":")
    ]
    edge_ids = " ".join(route_edges)

    # DEBUG: create a fresh routes file with ONLY the selected vehicle
    root = ET.Element("routes")

    ET.SubElement(
        root,
        "vType",
        {
            "id": "car",
            "accel": "2.6",
            "decel": "4.5",
            "sigma": "0.5",
            "length": "5.0",
            "minGap": "2.5",
            "maxSpeed": "25",
        },
    )

    # Force exactly one selected vehicle for debugging
    selected_vehicle_count = 1

    vehicle = ET.SubElement(
        root,
        "vehicle",
        {
            "id": "selected_od_0",
            "type": "car",
            "depart": "10",
            "departLane": "best",
        },
    )
    ET.SubElement(vehicle, "route", {"edges": edge_ids})

    tree = ET.ElementTree(root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    return {
        "start_edge": start_edge.getID(),
        "end_edge": end_edge.getID(),
        "route_edges": route_edges,
        "selected_vehicle_count": selected_vehicle_count,
    } 



def _parse_selected_tripinfo(tripinfo_path: Path, selected_vehicle_count: int) -> dict:
    trips = [
        trip
        for trip in ET.parse(tripinfo_path).getroot().findall("tripinfo")
        if (trip.get("id") or "").startswith("selected_od_")
    ]
    completed = [trip for trip in trips if float(trip.get("arrival", -1)) >= 0]
    durations = [float(trip.get("duration", 0) or 0) for trip in completed]
    wait_ratios = [
        float(trip.get("waitingTime", 0) or 0) / max(1.0, float(trip.get("duration", 0) or 0))
        for trip in trips
    ]
    return {
        "throughput": len(completed),
        "avg_travel_time_sec": round(sum(durations) / len(durations), 2) if durations else 0.0,
        "active_vehicles": max(0, selected_vehicle_count - len(completed)),
        "avg_congestion_ratio": round(sum(wait_ratios) / len(wait_ratios), 4) if wait_ratios else 0.0,
    }


def _parse_time_to_seconds(t_str: str) -> float:
    try:
        parts = t_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        return h * 3600.0 + m * 60.0
    except Exception:
        return 0.0


def _determine_simulation_time_window(start_time: str, end_time: str) -> tuple[int, int]:
    begin_sec = int(_parse_time_to_seconds(start_time))
    end_sec = int(_parse_time_to_seconds(end_time))
    
    # Enforce minimum 30 minutes (1800 seconds)
    if end_sec - begin_sec < 1800:
        end_sec = begin_sec + 1800
        
    begin_time = max(0, begin_sec - 300)
    end_time = end_sec + 900
    return begin_time, end_time


def run_corridor_simulation(
    origin: dict,
    destination: dict,
    demand_data: dict,
    start_time: str = "09:00",
    end_time: str = "10:00",
    day_of_week: Optional[str] = None,
) -> dict:
    """
    Find 3 candidate routes, run SUMO once, classify background vehicles,
    extract per-route metrics from a single FCD pass.
    """
    sumo = _sumo_binary(gui=False)
    if not sumo:
        raise RuntimeError("SUMO binary was not found on PATH.")
    baseline_route = _resolve_baseline_route(day_of_week)
    if not BASELINE_NET.exists() or not baseline_route.exists():
        raise FileNotFoundError("SUMO network or baseline route file is missing.")

    try:
        from sumolib.net import readNet
    except ImportError as exc:
        raise RuntimeError("sumolib is required for map-to-SUMO routing.") from exc

    net = readNet(str(BASELINE_NET), withInternal=False)

    start_edge = _nearest_drivable_edge(net, float(origin["lon"]), float(origin["lat"]))
    end_edge = _nearest_drivable_edge(net, float(destination["lon"]), float(destination["lat"]))

    start_edge_id = start_edge.getID()
    end_edge_id = end_edge.getID()

    # Step 1: find candidate routes
    raw_routes = _find_candidate_routes(net, start_edge, end_edge, route_count=ROUTE_SEARCH_LIMIT)
    if not raw_routes:
        raise ValueError("No routes found between the selected points.")

    candidate_routes = [
        {
            "id": i + 1,
            "edge_ids": [edge.getID() for edge in route],
            "geometry": _route_to_geojson(net, route, origin, destination),
        }
        for i, route in enumerate(raw_routes)
    ]

    filtered_routes = _select_routes_with_fallbacks(raw_routes, route_count=3)
    if filtered_routes:
        candidate_routes = [
            {
                "id": i + 1,
                "edge_ids": [edge.getID() for edge in route],
                "geometry": _route_to_geojson(net, route, origin, destination),
            }
            for i, route in enumerate(filtered_routes)
        ]
        print("=== FILTERED ROUTES DEBUG ===")
        for r in candidate_routes:
            print(
                f"Route {r['id']}: {len(r['edge_ids'])} edges | first={r['edge_ids'][:3]} | last={r['edge_ids'][-3:]}"
            )
        print("=============================")


    print("=== CANDIDATE ROUTES DEBUG ===")
    for r in candidate_routes:
        print(f"Route {r['id']}: {len(r['edge_ids'])} edges | first={r['edge_ids'][:3]} | last={r['edge_ids'][-3:]}")
    print("==============================")

    # Step 2: reuse the baseline vehicle set and only measure vehicles that match the corridor.
    temp_dir = Path(tempfile.mkdtemp(prefix="uidt_sumo_run_"))

    try:
        begin_time, end_time = _determine_simulation_time_window(start_time, end_time)
        
        route_path = baseline_route
        vehicle_assignments = _classify_vehicles_to_routes(
            route_path,
            candidate_routes,
            begin_time=begin_time,
            end_time=end_time,
        )

        logger.info(
            "Matched %d vehicles across %d routes",
            len(vehicle_assignments),
            len(candidate_routes),
        )

        # Step 3: run SUMO once with FCD output
        fcd_path = temp_dir / "fcd.xml"
        tripinfo_path = temp_dir / "tripinfo.xml"
        summary_path = temp_dir / "summary.xml"

        _run_sumo(
            BASELINE_NET,
            route_path,
            SIMULATION_SECONDS,
            tripinfo_path,
            summary_path,
            fcd_path=fcd_path,
            begin_time=begin_time,
            end_time=end_time,
        )

        # Step 4: SUMO tripinfo is the source of throughput and travel time.
        # FCD remains useful as a segment-level fallback when tripinfo does not
        # contain a matched vehicle.
        tripinfo_by_route = {
            route_index: _aggregate_tripinfo_metrics(tripinfo_path, {
                vehicle_id: route_index
                for vehicle_id, assigned_route_index in vehicle_assignments.items()
                if assigned_route_index == route_index
            })
            for route_index in range(len(candidate_routes))
        }

        # Step 5: single FCD pass, results grouped by route index
        per_route_rows = _extract_corridor_vehicle_stats_from_fcd(
            fcd_path,
            vehicle_assignments,
            candidate_routes,
        )

        # Step 6: aggregate metrics per route
        route_results = []
        for i, route in enumerate(candidate_routes):
            rows = per_route_rows.get(i, [])
            fcd_metrics = _aggregate_corridor_metrics(rows)
            tripinfo_metrics = tripinfo_by_route[i]
            metrics = (
                tripinfo_metrics
                if tripinfo_metrics["tripinfo_vehicle_count"]
                else fcd_metrics
            )
            route_results.append({
                "id": route["id"],
                "geometry": route["geometry"],
                "metrics": metrics,
            })

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "status": "complete",
        "start_edge": start_edge_id,
        "end_edge": end_edge_id,
        "route_file": str(route_path),
        "route_file_source": (
            f"SUMO calibrated route file ({day_of_week})" if day_of_week
            else "SUMO baseline route file"
        ),
        "route_file_vehicle_count": _baseline_vehicle_count(route_path),
        "route_file_window_vehicle_count": _route_file_vehicle_count(
            route_path,
            begin_time=begin_time,
            end_time=end_time,
        ),
        "routes": route_results,
    }

def _edge_endpoint_xy(node):
    coord = node.getCoord()
    return float(coord[0]), float(coord[1])


def _edge_distance_to_point(edge, x: float, y: float) -> float:
    shape = edge.getShape()
    if not shape:
        shape = [edge.getFromNode().getCoord(), edge.getToNode().getCoord()]
    return min(
        math.hypot(float(point[0]) - x, float(point[1]) - y)
        for point in shape
    )


def _select_direct_proposed_edge(net, proposed_edges, origin, destination):
    """Select the proposed edge whose direction follows origin -> destination."""
    origin_x, origin_y = net.convertLonLat2XY(
        float(origin["lon"]), float(origin["lat"])
    )
    destination_x, destination_y = net.convertLonLat2XY(
        float(destination["lon"]), float(destination["lat"])
    )

    def directional_score(edge):
        from_x, from_y = _edge_endpoint_xy(edge.getFromNode())
        to_x, to_y = _edge_endpoint_xy(edge.getToNode())
        return math.hypot(from_x - origin_x, from_y - origin_y) + math.hypot(
            to_x - destination_x,
            to_y - destination_y,
        )

    return min(proposed_edges, key=directional_score)


def _ordered_proposed_edge_paths(proposed_edges):
    """Build forward and reverse SUMO edge paths for a drawn proposal.

    New proposal exports encode the source segment index and direction in the
    edge ID. A forward path follows the user's drawn order; a reverse path
    follows the same alignment in the opposite direction. Returning complete
    paths prevents Route 4 from silently using only one segment of a curved
    intervention.
    """
    segments = {}
    pattern = re.compile(r"^proposed_edge_(\d+)_(forward|reverse)$")
    for edge in proposed_edges:
        match = pattern.match(edge.getID())
        if not match:
            continue
        segment_index = int(match.group(1))
        direction = match.group(2)
        segments.setdefault(segment_index, {})[direction] = edge

    if not segments:
        return []

    segment_indexes = sorted(segments)
    forward_path = [segments[index].get("forward") for index in segment_indexes]
    reverse_path = [
        segments[index].get("reverse") for index in reversed(segment_indexes)
    ]

    paths = []
    if all(forward_path):
        paths.append(forward_path)
    if all(reverse_path):
        paths.append(reverse_path)
    return paths


def _proposed_path_directional_score(net, proposed_path, origin, destination):
    origin_x, origin_y = net.convertLonLat2XY(
        float(origin["lon"]), float(origin["lat"])
    )
    destination_x, destination_y = net.convertLonLat2XY(
        float(destination["lon"]), float(destination["lat"])
    )
    first_from_x, first_from_y = _edge_endpoint_xy(
        proposed_path[0].getFromNode()
    )
    last_to_x, last_to_y = _edge_endpoint_xy(
        proposed_path[-1].getToNode()
    )
    return math.hypot(first_from_x - origin_x, first_from_y - origin_y) + math.hypot(
        last_to_x - destination_x,
        last_to_y - destination_y,
    )


def _find_local_connector(net, target_node_id, lon: float, lat: float, incoming: bool):
    """Find one adjacent ground edge without traversing unrelated roads."""
    point_x, point_y = net.convertLonLat2XY(float(lon), float(lat))
    candidates = []

    for edge in net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":") or edge_id.startswith("proposed_edge_"):
            continue
        if not edge.allows("passenger"):
            continue

        node = edge.getToNode() if incoming else edge.getFromNode()
        if node.getID() != target_node_id:
            continue
        candidates.append((_edge_distance_to_point(edge, point_x, point_y), edge))

    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _find_direct_proposed_route(
    net,
    start_edge,
    end_edge,
    proposed_edges,
    origin,
    destination,
):
    """Build Route 4 through the complete proposed alignment.

    The proposed edges are ordered by the user's drawn segment sequence. Only
    one nearest adjacent ground edge at either endpoint is used when needed to
    enter or leave the intervention, so the route cannot wander through
    unrelated roads.
    """
    proposed_paths = _ordered_proposed_edge_paths(proposed_edges)
    if proposed_paths:
        proposed_path = min(
            proposed_paths,
            key=lambda path: _proposed_path_directional_score(
                net, path, origin, destination
            ),
        )
    else:
        # Compatibility fallback for older generated SUMO networks that do
        # not contain segment-order metadata.
        proposed_path = [
            _select_direct_proposed_edge(net, proposed_edges, origin, destination)
        ]

    has_package_connectors = any(
        edge.getID().startswith("proposed_edge_p") for edge in proposed_edges
    )
    if has_package_connectors:
        stitched = _stitch_route_through_path(net, start_edge, end_edge, proposed_path)
        if stitched:
            return stitched, proposed_path

    start_node_id = proposed_path[0].getFromNode().getID()
    end_node_id = proposed_path[-1].getToNode().getID()

    start_connector = None
    if start_edge.getToNode().getID() == start_node_id:
        start_connector = start_edge
    else:
        start_connector = _find_local_connector(
            net,
            start_node_id,
            origin["lon"],
            origin["lat"],
            incoming=True,
        )

    end_connector = None
    if end_edge.getFromNode().getID() == end_node_id:
        end_connector = end_edge
    else:
        end_connector = _find_local_connector(
            net,
            end_node_id,
            destination["lon"],
            destination["lat"],
            incoming=False,
        )

    route_edges = []
    if start_connector is not None:
        route_edges.append(start_connector)
    route_edges.extend(proposed_path)
    if end_connector is not None and (
        not route_edges or end_connector.getID() != route_edges[-1].getID()
    ):
        route_edges.append(end_connector)

    return route_edges, proposed_path


def _stitch_route_through_path(net, start_edge, end_edge, proposed_path):
    """Shortest drivable route origin -> main structure -> destination.

    Used for multi-item packages: the legs to and from the main structure
    may run over the package's connector roads.
    """
    if start_edge.getID() == proposed_path[0].getID():
        lead = [start_edge]
    else:
        lead, _cost = net.getShortestPath(start_edge, proposed_path[0])
    if proposed_path[-1].getID() == end_edge.getID():
        tail = [end_edge]
    else:
        tail, _cost = net.getShortestPath(proposed_path[-1], end_edge)
    if not lead or not tail:
        return []

    route_edges = list(lead[:-1]) + list(proposed_path) + list(tail[1:])
    deduped = []
    for edge in route_edges:
        if not deduped or deduped[-1].getID() != edge.getID():
            deduped.append(edge)
    return deduped


def _find_via_point_route(net, start_edge, end_edge, via_edge):
    """Legacy helper retained for callers outside the intervention flow.

    Route 4 no longer calls this function; it uses
    ``_find_direct_proposed_route`` so the selected proposal stays direct.
    """
    graph = _build_networkx_graph(net)
    start_node = start_edge.getToNode().getID()
    end_node = end_edge.getFromNode().getID()
    via_from = via_edge.getFromNode().getID()
    via_to = via_edge.getToNode().getID()

    try:
        path1 = nx.shortest_path(graph, start_node, via_from, weight="weight")
        path2 = nx.shortest_path(graph, via_to, end_node, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []

    edge_map = {
        edge.getID(): edge
        for edge in net.getEdges()
        if not edge.getID().startswith(":")
    }
    route_edges = [start_edge]
    for u, v in zip(path1[:-1], path1[1:]):
        route_edges.append(edge_map[graph[u][v]["edge_id"]])
    route_edges.append(via_edge)
    for u, v in zip(path2[:-1], path2[1:]):
        route_edges.append(edge_map[graph[u][v]["edge_id"]])
    route_edges.append(end_edge)

    dedup_edges = []
    for edge in route_edges:
        if not dedup_edges or dedup_edges[-1].getID() != edge.getID():
            dedup_edges.append(edge)
    return dedup_edges


def _build_intervention_route_context(
    origin: dict,
    destination: dict,
    modified_graph=None,
    proposal_coordinates=None,
) -> dict:
    sumo = _sumo_binary(gui=False)
    if not sumo:
        raise RuntimeError("SUMO binary was not found on PATH.")

    modified_net_path = AUTO_TOOL_DIR / "cleaned_modified_network.net.xml"
    if modified_graph is not None:
        modified_net_path = _resolve_after_net_path(modified_graph, True)
    if not BASELINE_NET.exists() or not BASELINE_ROU.exists() or not modified_net_path.exists():
        raise FileNotFoundError("SUMO networks or baseline route file is missing.")

    try:
        from sumolib.net import readNet
    except ImportError as exc:
        raise RuntimeError("sumolib is required for map-to-SUMO routing.") from exc

    net_base = readNet(str(BASELINE_NET), withInternal=False)
    start_edge_base = _nearest_drivable_edge(net_base, float(origin["lon"]), float(origin["lat"]))
    end_edge_base = _nearest_drivable_edge(net_base, float(destination["lon"]), float(destination["lat"]))

    raw_routes_base = _find_candidate_routes(
        net_base,
        start_edge_base,
        end_edge_base,
        route_count=ROUTE_SEARCH_LIMIT,
    )
    if not raw_routes_base:
        raise ValueError("No baseline routes found between the selected points.")

    filtered_routes_base = _select_routes_with_fallbacks(raw_routes_base, route_count=3)
    candidate_routes_base = [
        {
            "id": i + 1,
            "edge_ids": [edge.getID() for edge in route],
            "length_m": round(sum(edge.getLength() for edge in route), 1),
            "geometry": _route_to_geojson(net_base, route, origin, destination),
        }
        for i, route in enumerate(filtered_routes_base)
    ]

    net_mod = readNet(str(modified_net_path), withInternal=False)
    start_edge_mod = _nearest_drivable_edge(net_mod, float(origin["lon"]), float(origin["lat"]))
    end_edge_mod = _nearest_drivable_edge(net_mod, float(destination["lon"]), float(destination["lat"]))

    proposed_edges = [
        edge
        for edge in net_mod.getEdges()
        if edge.getID().startswith("proposed_edge_")
        or edge.getName() in {"Proposed Infra", "AI Flyover"}
    ]

    candidate_routes_mod = []
    for route in candidate_routes_base:
        edge_ids = route["edge_ids"]
        valid_edges = []
        for edge_id in edge_ids:
            try:
                valid_edges.append(net_mod.getEdge(edge_id))
            except KeyError:
                pass
        if len(valid_edges) == len(edge_ids):
            candidate_routes_mod.append(
                {
                    "id": route["id"],
                    "edge_ids": edge_ids,
                    "length_m": round(sum(edge.getLength() for edge in valid_edges), 1),
                    "geometry": _route_to_geojson(
                        net_mod, valid_edges, origin, destination
                    ),
                }
            )

    if proposed_edges:
        route_4_edges, proposed_route_edges = _find_direct_proposed_route(
            net_mod,
            start_edge_mod,
            end_edge_mod,
            proposed_edges,
            origin,
            destination,
        )
        if route_4_edges:
            simulation_geometry = _route_to_geojson(
                net_mod, route_4_edges, origin, destination
            )
            proposed_edge_ids = {edge.getID() for edge in proposed_route_edges}
            existing_route_geometries = [
                _route_to_geojson(
                    net_mod,
                    [edge],
                    origin=origin if index == 0 else None,
                    destination=destination
                    if index == len(route_4_edges) - 1
                    else None,
                )
                for index, edge in enumerate(route_4_edges)
                if edge.getID() not in proposed_edge_ids
            ]
            candidate_routes_mod.append(
                {
                    "id": 4,
                    "edge_ids": [edge.getID() for edge in route_4_edges],
                    "length_m": round(sum(edge.getLength() for edge in route_4_edges), 1),
                    "geometry": simulation_geometry,
                    "simulation_geometry": simulation_geometry,
                    "proposal_geometry": _proposal_to_geojson(proposal_coordinates),
                    "existing_route_geometries": existing_route_geometries,
                    "is_direct_proposed_route": len(proposed_route_edges) == 1,
                    "is_flyover": any(
                        edge.getName() == "AI Flyover"
                        for edge in proposed_route_edges
                    ),
                }
            )

    if len(candidate_routes_mod) != len(candidate_routes_base) + 1:
        raise RuntimeError(
            "The modified SUMO network did not produce a fourth route through the proposed infrastructure."
        )

    return {
        "modified_net_path": modified_net_path,
        "baseline_net": net_base,
        "modified_net": net_mod,
        "start_edge_base": start_edge_base,
        "end_edge_base": end_edge_base,
        "candidate_routes_base": candidate_routes_base,
        "candidate_routes_mod": candidate_routes_mod,
    }


def inspect_intervention_routes(
    origin: dict,
    destination: dict,
    modified_graph=None,
    proposal_coordinates=None,
) -> dict:
    context = _build_intervention_route_context(
        origin,
        destination,
        modified_graph,
        proposal_coordinates=proposal_coordinates,
    )
    return {
        "status": "valid",
        "start_edge": context["start_edge_base"].getID(),
        "end_edge": context["end_edge_base"].getID(),
        "baseline_route_count": len(context["candidate_routes_base"]),
        "modified_route_count": len(context["candidate_routes_mod"]),
        "baseline_routes": context["candidate_routes_base"],
        "routes": context["candidate_routes_mod"],
        "route4": context["candidate_routes_mod"][-1],
    }


def run_corridor_intervention_simulation(
    origin: dict,
    destination: dict,
    demand_data: dict,
    start_time: str = "09:00",
    end_time: str = "10:00",
    modified_graph=None,
    adoption_rate_pct: float = 75.0,
    proposal_coordinates=None,
    day_of_week: Optional[str] = None,
) -> dict:
    """
    Runs the corridor simulation with a configurable share of eligible vehicles
    adopting the new route while all other vehicles retain their baseline route.
    """
    baseline_route = _resolve_baseline_route(day_of_week)
    route_context = _build_intervention_route_context(
        origin,
        destination,
        modified_graph,
        proposal_coordinates=proposal_coordinates,
    )
    modified_net_path = route_context["modified_net_path"]
    baseline_net = route_context["baseline_net"]
    modified_net = route_context["modified_net"]
    start_edge_base = route_context["start_edge_base"]
    end_edge_base = route_context["end_edge_base"]
    candidate_routes_base = route_context["candidate_routes_base"]
    candidate_routes_mod = route_context["candidate_routes_mod"]
            
    # Step 2: Classify background vehicles to base routes
    begin_time, end_time = _determine_simulation_time_window(start_time, end_time)
    
    vehicle_assignments_base = _classify_vehicles_to_routes(
        baseline_route,
        candidate_routes_base,
        begin_time=begin_time,
        end_time=end_time,
    )
    corridor_veh_ids = list(vehicle_assignments_base.keys())
    corridor_veh_ids.sort()

    new_assignments_mod, adopted_vehicle_count = _assign_intervention_routes(
        corridor_vehicle_ids=corridor_veh_ids,
        baseline_assignments=vehicle_assignments_base,
        base_route_count=len(candidate_routes_base),
        adoption_rate_pct=adoption_rate_pct,
    )
    actual_adoption_rate_pct = (
        round(adopted_vehicle_count / len(corridor_veh_ids) * 100.0, 1)
        if corridor_veh_ids
        else 0.0
    )
        
    # Step 3: Run simulations
    temp_dir = Path(tempfile.mkdtemp(prefix="uidt_corridor_intervention_"))
    heatmap_result = None
    
    try:

        # Run Simulation A (Before) on the baseline network rebuilt by the
        # same netconvert pass as the modified network, with baseline routes.
        fcd_path_before = temp_dir / "fcd_before.xml"
        tripinfo_path_before = temp_dir / "tripinfo_before.xml"
        summary_path_before = temp_dir / "summary_before.xml"

        _run_sumo(
            _control_baseline_net(),
            baseline_route,
            SIMULATION_SECONDS,
            tripinfo_path_before,
            summary_path_before,
            fcd_path=fcd_path_before,
            begin_time=begin_time,
            end_time=end_time,
        )
        
        # Run Simulation B (After) using modified network and modified routes
        # Parse the resolved baseline route file and modify matching vehicles' routes
        tree = ET.parse(baseline_route)
        root = tree.getroot()
        for vehicle in root.findall("vehicle"):
            veh_id = vehicle.get("id")
            if veh_id in new_assignments_mod:
                route_idx = new_assignments_mod[veh_id]
                target_route = candidate_routes_mod[route_idx]
                route_elem = vehicle.find("route")
                if route_elem is not None:
                    route_elem.set("edges", " ".join(target_route["edge_ids"]))
                    
        modified_route_path = temp_dir / "modified_simulation.rou.xml"
        tree.write(modified_route_path, encoding="utf-8", xml_declaration=True)
        
        fcd_path_after = temp_dir / "fcd_after.xml"
        tripinfo_path_after = temp_dir / "tripinfo_after.xml"
        summary_path_after = temp_dir / "summary_after.xml"
        
        _run_sumo(
            modified_net_path,
            modified_route_path,
            SIMULATION_SECONDS,
            tripinfo_path_after,
            summary_path_after,
            fcd_path=fcd_path_after,
            begin_time=begin_time,
            end_time=end_time,
        )
        
        # Step 4: Extract stats.  SUMO tripinfo is authoritative for
        # throughput and travel time; FCD is retained as a fallback.
        tripinfo_summary_before = _aggregate_tripinfo_metrics(
            tripinfo_path_before,
            vehicle_assignments_base,
        )
        tripinfo_summary_after = _aggregate_tripinfo_metrics(
            tripinfo_path_after,
            new_assignments_mod,
        )

        per_route_rows_before = _extract_corridor_vehicle_stats_from_fcd(
            fcd_path_before,
            vehicle_assignments_base,
            candidate_routes_base,
        )
        
        per_route_rows_after = _extract_corridor_vehicle_stats_from_fcd(
            fcd_path_after,
            new_assignments_mod,
            candidate_routes_mod,
        )
        
        # Step 5: Aggregate metrics
        route_results = []
        all_before_rows = []
        all_after_rows = []
        
        for i, route in enumerate(candidate_routes_base):
            rows_before = per_route_rows_before.get(i, [])
            rows_after = per_route_rows_after.get(i, [])
            
            all_before_rows.extend(rows_before)
            all_after_rows.extend(rows_after)
            
            base_assignments = {
                vehicle_id: route_index
                for vehicle_id, route_index in vehicle_assignments_base.items()
                if route_index == i
            }
            after_assignments = {
                vehicle_id: route_index
                for vehicle_id, route_index in new_assignments_mod.items()
                if route_index == i
            }
            tripinfo_metrics_before = _aggregate_tripinfo_metrics(
                tripinfo_path_before,
                base_assignments,
            )
            tripinfo_metrics_after = _aggregate_tripinfo_metrics(
                tripinfo_path_after,
                after_assignments,
            )
            fcd_metrics_before = _aggregate_corridor_metrics(rows_before)
            fcd_metrics_after = _aggregate_corridor_metrics(rows_after)
            metrics_before = (
                tripinfo_metrics_before
                if tripinfo_metrics_before["tripinfo_vehicle_count"]
                else fcd_metrics_before
            )
            metrics_after = (
                tripinfo_metrics_after
                if tripinfo_metrics_after["tripinfo_vehicle_count"]
                else fcd_metrics_after
            )
            
            route_results.append({
                "id": route["id"],
                "geometry": route["geometry"],
                "metrics": metrics_after,
                "before_metrics": metrics_before,
                "assigned_vehicle_count": sum(
                    1 for route_index in new_assignments_mod.values() if route_index == i
                ),
            })
            
        # Add Route 4 if it exists
        if len(candidate_routes_mod) > len(candidate_routes_base):
            route_4 = candidate_routes_mod[-1]
            rows_after = per_route_rows_after.get(len(candidate_routes_base), [])
            all_after_rows.extend(rows_after)
            tripinfo_metrics_after = _aggregate_tripinfo_metrics(
                tripinfo_path_after,
                {
                    vehicle_id: route_index
                    for vehicle_id, route_index in new_assignments_mod.items()
                    if route_index == len(candidate_routes_base)
                },
            )
            metrics_after = (
                tripinfo_metrics_after
                if tripinfo_metrics_after["tripinfo_vehicle_count"]
                else _aggregate_corridor_metrics(rows_after)
            )
            
            route_results.append({
                "id": 4,
                "geometry": route_4["geometry"],
                "metrics": metrics_after,
                "before_metrics": {
                    "throughput": 0,
                    "avg_travel_time_sec": 0.0,
                    "avg_speed_kmh": 0.0,
                    "active_vehicles": 0,
                    "avg_congestion_ratio": 0.0,
                },
                "assigned_vehicle_count": sum(
                    1 for route_index in new_assignments_mod.values()
                    if route_index == len(candidate_routes_base)
                ),
            })
            
        fcd_before_summary = _aggregate_corridor_metrics(all_before_rows)
        fcd_after_summary = _aggregate_corridor_metrics(all_after_rows)
        before_summary = (
            tripinfo_summary_before
            if tripinfo_summary_before["tripinfo_vehicle_count"]
            else fcd_before_summary
        )
        after_summary = (
            tripinfo_summary_after
            if tripinfo_summary_after["tripinfo_vehicle_count"]
            else fcd_after_summary
        )
        
        time_before = before_summary["avg_travel_time_sec"]
        time_after = after_summary["avg_travel_time_sec"]
        speed_before = before_summary.get("avg_speed_kmh", 0.0)
        speed_after = after_summary.get("avg_speed_kmh", 0.0)
        time_reduction_pct = 0.0
        if time_before > 0:
            time_reduction_pct = round(((time_before - time_after) / time_before) * 100, 1)
            
        congestion_before = before_summary["avg_congestion_ratio"]
        congestion_after = after_summary["avg_congestion_ratio"]
        congestion_reduction_pct = 0.0
        if congestion_before > 0:
            congestion_reduction_pct = round(
                ((congestion_before - congestion_after) / congestion_before) * 100, 1
            )

        heatmap_result = _build_simulation_heatmap(
            before_fcd_path=fcd_path_before,
            after_fcd_path=fcd_path_after,
            baseline_net=baseline_net,
            modified_net=modified_net,
            begin_time=begin_time,
            end_time=end_time,
        )
            
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        
    return {
        "status": "complete",
        "start_edge": start_edge_base.getID(),
        "end_edge": end_edge_base.getID(),
        "route_file": str(baseline_route),
        "route_file_source": (
            f"SUMO calibrated route file ({day_of_week})" if day_of_week
            else "SUMO baseline route file"
        ),
        "route_file_vehicle_count": _baseline_vehicle_count(baseline_route),
        "route_file_window_vehicle_count": _route_file_vehicle_count(
            baseline_route,
            begin_time=begin_time,
            end_time=end_time,
        ),
        "routes": route_results,
        "avg_travel_time_before_sec": time_before,
        "avg_travel_time_after_sec": time_after,
        "avg_travel_time_before_min": round(time_before / 60, 2),
        "avg_travel_time_after_min": round(time_after / 60, 2),
        "avg_speed_before_kmh": speed_before,
        "avg_speed_after_kmh": speed_after,
        "time_reduction_pct": time_reduction_pct,
        "congestion_reduction_pct": congestion_reduction_pct,
        "adoption_rate_pct": round(float(adoption_rate_pct), 1),
        "actual_adoption_rate_pct": actual_adoption_rate_pct,
        "eligible_vehicle_count": len(corridor_veh_ids),
        "adopted_vehicle_count": adopted_vehicle_count,
        "before": before_summary,
        "after": after_summary,
        "heatmap": heatmap_result,
    }


def run_corridor_intervention_simulation_batch(
    origin: dict,
    destination: dict,
    demand_data: dict,
    start_time: str = "09:00",
    end_time: str = "10:00",
    modified_graph=None,
    adoption_rates_pct: list[float] | tuple[float, ...] = (25.0, 50.0, 75.0),
    proposal_coordinates=None,
    day_of_week: Optional[str] = None,
    primary_adoption_rate_pct: float = 75.0,
) -> dict:
    primary_rate = float(primary_adoption_rate_pct)
    rates = sorted({float(rate) for rate in adoption_rates_pct} | {primary_rate})
    if not rates:
        raise ValueError("At least one adoption rate is required.")
    if any(rate < 0 or rate > 100 for rate in rates):
        raise ValueError("Adoption rates must be between 0 and 100 percent.")

    scenarios = [
        run_corridor_intervention_simulation(
            origin=origin,
            destination=destination,
            demand_data=demand_data,
            start_time=start_time,
            end_time=end_time,
            modified_graph=modified_graph,
            adoption_rate_pct=rate,
            proposal_coordinates=proposal_coordinates,
            day_of_week=day_of_week,
        )
        for rate in rates
    ]
    primary = next(
        (
            scenario
            for scenario in scenarios
            if scenario["adoption_rate_pct"] == round(primary_rate, 1)
        ),
        scenarios[-1],
    )
    primary_heatmap = primary.get("heatmap")
    scenario_heatmaps = {}
    for scenario in scenarios:
        scenario_heatmap = scenario.get("heatmap")
        if not scenario_heatmap:
            continue
        rate = float(scenario["adoption_rate_pct"])
        rate_key = str(int(rate)) if rate.is_integer() else str(rate)
        scenario_heatmaps[rate_key] = {
            "after": scenario_heatmap.get("after"),
            "difference": scenario_heatmap.get("difference"),
        }

    heatmap = None
    if primary_heatmap:
        primary_rate = float(primary["adoption_rate_pct"])
        heatmap = {
            **primary_heatmap,
            "selected_adoption_rate_pct": (
                int(primary_rate) if primary_rate.is_integer() else primary_rate
            ),
            "scenarios": scenario_heatmaps,
        }
    return {
        **primary,
        "status": "batch_complete",
        "scenarios": scenarios,
        "adoption_rates_pct": rates,
        "primary_adoption_rate_pct": float(primary["adoption_rate_pct"]),
        "heatmap": heatmap,
    }


def run_traffic_simulation(
    original_graph,
    modified_graph,
    demand_data: dict,
    is_feasible: bool,
    start_time: str = "09:00",
    end_time: str = "10:00",
    day_of_week: Optional[str] = None,
    origin: Optional[dict] = None,
    destination: Optional[dict] = None,
) -> dict:
    arrival_rate = _arrival_rate_from_demand(demand_data)

    return _run_sumo_simulation(
        demand_data=demand_data,
        modified_graph=modified_graph,
        is_feasible=is_feasible,
        arrival_rate=arrival_rate,
        start_time=start_time,
        end_time=end_time,
        day_of_week=day_of_week,
        origin=origin,
        destination=destination,
    )
