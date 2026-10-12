import argparse
import os
import random
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sumolib.net import readNet


PEDESTRIAN_CLASSES = {"pedestrian", "bicycle"}
MOTORIZED_CLASSES = {"passenger", "delivery", "bus", "taxi", "truck", "motorcycle", "evehicle"}
RAIL_CLASSES = {
    "rail",
    "rail_urban",
    "rail_electric",
    "rail_fast",
    "tram",
    "subway",
}
COLOR_PALETTE = [
    "1,0,0",
    "0,0,1",
    "0,0.6,0",
    "1,0.5,0",
    "0.7,0,0.8",
    "0,0.7,0.7",
]


@dataclass
class RouteResult:
    vehicle_id: str
    depart: float
    edges: List[str]
    color: str


class RouteGenerator:
    def __init__(self, net_file: str, include_rail: bool = False):
        self.net_file = net_file
        self.include_rail = include_rail
        self._validate_input()

        self.net = self._load_net_with_fallback()
        self.valid_edges = self._extract_valid_edges()
        self.edge_features: Dict[str, Dict[str, float]] = {}
        self.boundary_edges: List = []
        self.valid_successor_pairs: Set[Tuple[str, str]] = set()
        self._compute_edge_features()
        self._build_valid_successor_pairs()
        self.entry_edges, self.exit_edges = self._identify_entry_exit_points()
        self.generated_routes: List[RouteResult] = []
        self.statistics: Dict[str, float] = {}

        if not self.valid_edges:
            raise ValueError("No valid road edges found in network.")

    def _validate_input(self) -> None:
        if not os.path.exists(self.net_file):
            raise FileNotFoundError(f"Network file not found: {self.net_file}")
        try:
            ET.parse(self.net_file)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid SUMO XML file: {self.net_file}") from exc

    def _load_net_with_fallback(self):
        try:
            return readNet(self.net_file, withInternal=False)
        except Exception as exc:
            # Some generated/repaired files can contain connection tags that miss
            # optional fields older/newer SUMO parsers expect (for example "dir").
            # Normalize those fields and retry once.
            tree = ET.parse(self.net_file)
            root = tree.getroot()
            changed = False
            for conn in root.findall("./connection"):
                if "from" not in conn.attrib or "to" not in conn.attrib:
                    continue
                if "dir" not in conn.attrib:
                    conn.set("dir", "s")
                    changed = True
                if "state" not in conn.attrib:
                    conn.set("state", "M")
                    changed = True

            if not changed:
                raise exc

            fd, tmp_path = tempfile.mkstemp(prefix="sumo_net_sanitized_", suffix=".net.xml")
            os.close(fd)
            try:
                tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
                return readNet(tmp_path, withInternal=False)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _is_rail_edge(self, edge) -> bool:
        edge_type = (edge.getType() or "").lower()
        if any(token in edge_type for token in ("rail", "tram", "subway")):
            return True
        for lane in edge.getLanes():
            if any(self._lane_allows(lane, vclass) for vclass in RAIL_CLASSES):
                return True
        return False

    def _lane_allows(self, lane, vclass: str) -> bool:
        allows_fn = getattr(lane, "allows", None)
        if callable(allows_fn):
            try:
                return bool(allows_fn(vclass))
            except Exception:
                return False
        return False

    def _is_pedestrian_only(self, edge) -> bool:
        lanes = edge.getLanes()
        if not lanes:
            return True
        for lane in lanes:
            if any(self._lane_allows(lane, vclass) for vclass in MOTORIZED_CLASSES):
                return False
            if any(self._lane_allows(lane, vclass) for vclass in RAIL_CLASSES):
                return False
        return True

    def _extract_valid_edges(self) -> List:
        valid = []
        for edge in self.net.getEdges():
            edge_id = edge.getID()
            if edge_id.startswith(":"):
                continue
            if edge.getFunction() == "internal":
                continue
            if self._is_pedestrian_only(edge):
                continue
            if not self.include_rail and self._is_rail_edge(edge):
                continue
            valid.append(edge)
        return valid

    def _identify_entry_exit_points(self) -> Tuple[List, List]:
        if not self.valid_edges:
            return [], []
        # Use all valid edges as OD candidates; boundary edges are still tracked
        # separately and used as weighted targets for center->outward flows.
        return list(self.valid_edges), list(self.valid_edges)

    def _edge_midpoint(self, edge) -> Tuple[float, float]:
        fx, fy = edge.getFromNode().getCoord()
        tx, ty = edge.getToNode().getCoord()
        return (float(fx) + float(tx)) / 2.0, (float(fy) + float(ty)) / 2.0

    def _major_type_bonus(self, edge) -> float:
        edge_type = (edge.getType() or "").lower()
        if "motorway" in edge_type or "trunk" in edge_type:
            return 3.0
        if "primary" in edge_type:
            return 2.4
        if "secondary" in edge_type:
            return 1.8
        if "tertiary" in edge_type:
            return 1.2
        if "residential" in edge_type:
            return 0.5
        return 0.0

    def _compute_edge_features(self) -> None:
        if not self.valid_edges:
            self.edge_features = {}
            self.boundary_edges = []
            return

        mids = [self._edge_midpoint(edge) for edge in self.valid_edges]
        xs = [m[0] for m in mids]
        ys = [m[1] for m in mids]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
        max_dist = max((((x - cx) ** 2 + (y - cy) ** 2) ** 0.5) for x, y in mids) or 1.0

        margin_x = max((max_x - min_x) * 0.06, 35.0)
        margin_y = max((max_y - min_y) * 0.06, 35.0)
        features: Dict[str, Dict[str, float]] = {}
        boundary_edges = []

        for edge in self.valid_edges:
            edge_id = edge.getID()
            mx, my = self._edge_midpoint(edge)
            dist_norm = min((((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5) / max_dist, 1.0)
            speed = float(edge.getSpeed() or 13.89)
            lanes = int(edge.getLaneNumber() or 1)
            major = 1.0 + (speed / 13.89) * 0.7 + max(0, lanes - 1) * 0.9 + self._major_type_bonus(edge)

            near_boundary = (
                mx <= min_x + margin_x
                or mx >= max_x - margin_x
                or my <= min_y + margin_y
                or my >= max_y - margin_y
            )
            if near_boundary:
                boundary_edges.append(edge)

            center_weight = 1.0 + (1.0 - dist_norm) * 3.5 + major * 0.8
            outward_weight = 1.0 + dist_norm * 3.2 + major * 0.5 + (2.0 if near_boundary else 0.0)
            major_weight = 1.0 + major * 1.3

            features[edge_id] = {
                "dist_norm": dist_norm,
                "center_weight": center_weight,
                "outward_weight": outward_weight,
                "major_weight": major_weight,
            }

        self.edge_features = features
        self.boundary_edges = boundary_edges

    def _build_valid_successor_pairs(self) -> None:
        pairs: Set[Tuple[str, str]] = set()
        for edge in self.valid_edges:
            outgoing = edge.getOutgoing()
            if not outgoing:
                continue
            from_id = edge.getID()
            for to_edge in outgoing.keys():
                to_id = to_edge.getID()
                if to_id.startswith(":"):
                    continue
                pairs.add((from_id, to_id))
        self.valid_successor_pairs = pairs

    def _weighted_choice(self, edges: Sequence, weight_key: str):
        if not edges:
            return None
        weights = [self.edge_features.get(e.getID(), {}).get(weight_key, 1.0) for e in edges]
        return random.choices(edges, weights=weights, k=1)[0]

    def _pick_od_pair(self):
        # 60% center -> outer, 25% outer -> center, 15% cross-city major roads.
        mode = random.random()
        if mode < 0.60:
            from_edge = self._weighted_choice(self.entry_edges, "center_weight")
            to_pool = self.boundary_edges if self.boundary_edges else self.exit_edges
            to_edge = self._weighted_choice(to_pool, "outward_weight")
            return from_edge, to_edge
        if mode < 0.85:
            from_pool = self.boundary_edges if self.boundary_edges else self.entry_edges
            from_edge = self._weighted_choice(from_pool, "outward_weight")
            to_edge = self._weighted_choice(self.exit_edges, "center_weight")
            return from_edge, to_edge

        from_edge = self._weighted_choice(self.entry_edges, "major_weight")
        to_edge = self._weighted_choice(self.exit_edges, "major_weight")
        return from_edge, to_edge

    def _depart_times_even(self, num_vehicles: int, simulation_time: int) -> List[float]:
        if num_vehicles <= 0:
            return []
        if num_vehicles == 1:
            return [0.0]
        step = simulation_time / float(num_vehicles - 1)
        return [round(i * step, 2) for i in range(num_vehicles)]

    def _pick_route(self, min_route_length: int, max_route_length: Optional[int]) -> Optional[List[str]]:
        from_edge, to_edge = self._pick_od_pair()
        if from_edge is None or to_edge is None:
            return None
        if from_edge.getID() == to_edge.getID():
            return None

        path, _ = self.net.getShortestPath(from_edge, to_edge)
        if not path:
            return None

        route_ids = [e.getID() for e in path if not e.getID().startswith(":")]
        route_len = len(route_ids)
        if route_len < min_route_length:
            return None
        if max_route_length is not None and route_len > max_route_length:
            return None
        if not self._is_route_connection_valid(route_ids):
            return None
        return route_ids

    def _is_route_connection_valid(self, route_ids: Sequence[str]) -> bool:
        if len(route_ids) < 2:
            return False
        for i in range(len(route_ids) - 1):
            pair = (route_ids[i], route_ids[i + 1])
            if pair not in self.valid_successor_pairs:
                return False
        return True

    def generate_routes(
        self,
        num_vehicles: int = 1000,
        output_file: str = "simulation.rou.xml",
        simulation_time: int = 3600,
        min_route_length: int = 3,
        max_route_length: Optional[int] = None,
        vehicle_type: str = "car",
        random_seed: Optional[int] = None,
        max_attempt_factor: int = 30,
    ) -> Dict[str, float]:
        if random_seed is not None:
            random.seed(random_seed)
        if num_vehicles <= 0:
            raise ValueError("num_vehicles must be > 0")
        if simulation_time <= 0:
            raise ValueError("simulation_time must be > 0")
        if min_route_length < 1:
            raise ValueError("min_route_length must be >= 1")
        if max_route_length is not None and max_route_length < min_route_length:
            raise ValueError("max_route_length must be >= min_route_length")

        print(f"Valid edges available: {len(self.valid_edges)}")
        print(f"Entry edges: {len(self.entry_edges)}, Exit edges: {len(self.exit_edges)}")
        print(f"Boundary edges (outer targets): {len(self.boundary_edges)}")
        print(f"Generating up to {num_vehicles} valid routes...")

        depart_times = self._depart_times_even(num_vehicles, simulation_time)
        routes: List[RouteResult] = []
        used_edges: Set[str] = set()
        failed = 0
        attempts = 0
        max_attempts = max(num_vehicles * max_attempt_factor, num_vehicles + 100)

        while len(routes) < num_vehicles and attempts < max_attempts:
            attempts += 1
            route = self._pick_route(min_route_length=min_route_length, max_route_length=max_route_length)
            if not route:
                failed += 1
                continue
            depart = depart_times[len(routes)]
            vehicle_id = f"vehicle_{len(routes)}"
            color = random.choice(COLOR_PALETTE)
            routes.append(RouteResult(vehicle_id=vehicle_id, depart=depart, edges=route, color=color))
            used_edges.update(route)

            if len(routes) % 100 == 0:
                print(f"Generated {len(routes)} routes...")

        self.generated_routes = routes
        self._write_routes_xml(output_file=output_file, vehicle_type=vehicle_type, routes=routes)
        self.statistics = self._build_statistics(
            requested=num_vehicles,
            valid=len(routes),
            failed=failed,
            simulation_time=simulation_time,
            used_edges=used_edges,
        )
        self._print_summary()
        return self.statistics

    def _write_routes_xml(self, output_file: str, vehicle_type: str, routes: Sequence[RouteResult]) -> None:
        root = ET.Element("routes")
        ET.SubElement(
            root,
            "vType",
            {
                "id": vehicle_type,
                "accel": "2.6",
                "decel": "4.5",
                "sigma": "0.5",
                "length": "5.0",
                "maxSpeed": "50.0",
            },
        )

        for route_data in routes:
            veh_el = ET.SubElement(
                root,
                "vehicle",
                {
                    "id": route_data.vehicle_id,
                    "type": vehicle_type,
                    "depart": f"{route_data.depart:.2f}",
                    "color": route_data.color,
                },
            )
            ET.SubElement(veh_el, "route", {"edges": " ".join(route_data.edges)})

        tree = ET.ElementTree(root)
        tree.write(output_file, encoding="utf-8", xml_declaration=True)

        # Validate written XML is well-formed.
        ET.parse(output_file)

    def _build_statistics(
        self,
        requested: int,
        valid: int,
        failed: int,
        simulation_time: int,
        used_edges: Set[str],
    ) -> Dict[str, float]:
        route_lengths = [len(r.edges) for r in self.generated_routes]
        avg_route_length = (sum(route_lengths) / len(route_lengths)) if route_lengths else 0.0
        total_valid_edges = len(self.valid_edges) if self.valid_edges else 1
        coverage_pct = (len(used_edges) / total_valid_edges) * 100.0
        success_rate = (valid / requested * 100.0) if requested else 0.0
        vehicles_per_min = valid / (simulation_time / 60.0)

        warnings = []
        if success_rate < 50.0:
            warnings.append("Route success rate below 50%.")
        if success_rate < 70.0:
            warnings.append("Route success rate below target 70%.")

        return {
            "requested_routes": requested,
            "total_routes": valid,
            "total_vehicles": valid,
            "valid_routes": valid,
            "failed_routes": failed,
            "success_rate_percent": round(success_rate, 2),
            "avg_route_length": round(avg_route_length, 2),
            "edge_coverage_percent": round(coverage_pct, 2),
            "used_edges_count": len(used_edges),
            "total_valid_edges": len(self.valid_edges),
            "vehicles_per_minute": round(vehicles_per_min, 2),
            "simulation_time": simulation_time,
            "warnings": warnings,
        }

    def _print_summary(self) -> None:
        s = self.statistics
        print("\n=== Route Generation Report ===")
        print(f"Total routes generated: {s['total_routes']}")
        print(f"Total vehicles: {s['total_vehicles']}")
        print(f"Valid routes: {s['valid_routes']} ({s['success_rate_percent']}% success)")
        print(f"Average route length: {s['avg_route_length']} edges")
        print(
            f"Routes covering network: {s['edge_coverage_percent']}% "
            f"({s['used_edges_count']} / {s['total_valid_edges']} edges)"
        )
        print(f"Vehicles per minute: {s['vehicles_per_minute']}")
        print(f"Simulation duration: {s['simulation_time']} seconds")
        if s["warnings"]:
            print("Warnings:")
            for w in s["warnings"]:
                print(f"  - {w}")

    def get_statistics(self) -> Dict[str, float]:
        return dict(self.statistics)


def _parse_max_route_length(raw_value: str) -> Optional[int]:
    if raw_value.lower() in {"none", "unlimited", "null"}:
        return None
    value = int(raw_value)
    if value <= 0:
        return None
    return value


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Random route generator for SUMO networks.")
    parser.add_argument("net_file", help="Input SUMO network .net.xml")
    parser.add_argument("--output", default="simulation.rou.xml", help="Output route file (.rou.xml)")
    parser.add_argument("--num-vehicles", type=int, default=1000, help="Number of vehicles to generate")
    parser.add_argument("--simulation-time", type=int, default=3600, help="Simulation duration in seconds")
    parser.add_argument("--min-route-length", type=int, default=3, help="Minimum route length in edges")
    parser.add_argument(
        "--max-route-length",
        type=_parse_max_route_length,
        default=None,
        help="Maximum route length in edges (or 'none')",
    )
    parser.add_argument("--vehicle-type", default="car", help="Vehicle type id")
    parser.add_argument("--random-seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--include-rail", action="store_true", help="Include rail/tram edges")
    args = parser.parse_args()

    generator = RouteGenerator(args.net_file, include_rail=args.include_rail)
    generator.generate_routes(
        num_vehicles=args.num_vehicles,
        output_file=args.output,
        simulation_time=args.simulation_time,
        min_route_length=args.min_route_length,
        max_route_length=args.max_route_length,
        vehicle_type=args.vehicle_type,
        random_seed=args.random_seed,
    )
    print(f"\nSaved routes to: {args.output}")


if __name__ == "__main__":
    _cli()
