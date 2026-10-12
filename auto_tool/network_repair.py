import argparse
import math
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx
from sumolib.net import readNet


DEFAULT_SPEED = 13.89
DEFAULT_LANES = 1
DEFAULT_PRIORITY = -1


@dataclass
class EdgeRecord:
    edge_id: str
    from_node: str
    to_node: str
    length: float
    speed: float
    lanes: int
    edge_type: str
    priority: int


class NetworkRepairTool:
    def __init__(self, input_path: str):
        self.input_path = input_path
        self._validate_input()

        self.tree = ET.parse(self.input_path)
        self.root = self.tree.getroot()
        self.net = readNet(self.input_path, withInternal=False)

        self.nodes: Dict[str, Tuple[float, float]] = {}
        self.node_types: Dict[str, str] = {}
        self.edges: Dict[str, EdgeRecord] = {}
        self.connections: Set[Tuple[str, str]] = set()
        self.graph = nx.DiGraph()
        self._edge_counter = 0
        self._existing_edge_pairs: Set[Tuple[str, str]] = set()
        self._existing_connection_pairs: Set[Tuple[str, str]] = set()

        self.repairs = {
            "new_edges": 0,
            "new_connections": 0,
            "removed_edges": 0,
            "warnings": [],
        }

        self._load_network_data()
        self.before_stats = self._compute_stats()
        self.after_stats = dict(self.before_stats)

    def _validate_input(self) -> None:
        if not os.path.exists(self.input_path):
            raise FileNotFoundError(f"Input file does not exist: {self.input_path}")
        if not self.input_path.lower().endswith(".xml"):
            raise ValueError("Input must be an XML file.")
        try:
            ET.parse(self.input_path)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid XML in {self.input_path}: {exc}") from exc

    def _load_network_data(self) -> None:
        for node in self.net.getNodes():
            node_id = node.getID()
            self.nodes[node_id] = (float(node.getCoord()[0]), float(node.getCoord()[1]))
            node_type = ""
            get_type = getattr(node, "getType", None)
            if callable(get_type):
                node_type = str(get_type() or "")
            self.node_types[node_id] = node_type
            self.graph.add_node(node_id)

        for edge in self.net.getEdges():
            edge_id = edge.getID()
            if edge_id.startswith(":"):
                continue
            from_node = edge.getFromNode().getID()
            to_node = edge.getToNode().getID()
            length = float(edge.getLength() or 0.0)
            speed = float(edge.getSpeed() or DEFAULT_SPEED)
            lanes = int(edge.getLaneNumber() or DEFAULT_LANES)
            edge_type = edge.getType() or ""
            priority = int(edge.getPriority() if edge.getPriority() is not None else DEFAULT_PRIORITY)

            self.edges[edge_id] = EdgeRecord(
                edge_id=edge_id,
                from_node=from_node,
                to_node=to_node,
                length=length,
                speed=speed,
                lanes=lanes,
                edge_type=edge_type,
                priority=priority,
            )
            self.graph.add_edge(from_node, to_node, edge_id=edge_id, length=length)
            self._existing_edge_pairs.add((from_node, to_node))
            self._edge_counter += 1

        # SUMO/sumolib API differs across versions: some expose net.getConnections(),
        # others do not. We normalize by preferring API, then falling back to XML.
        get_connections = getattr(self.net, "getConnections", None)
        if callable(get_connections):
            for conn in get_connections():
                from_edge = conn.getFrom().getID()
                to_edge = conn.getTo().getID()
                self.connections.add((from_edge, to_edge))
                self._existing_connection_pairs.add((from_edge, to_edge))
        else:
            for conn_el in self.root.findall("./connection"):
                from_edge = conn_el.get("from")
                to_edge = conn_el.get("to")
                if not from_edge or not to_edge:
                    continue
                self.connections.add((from_edge, to_edge))
                self._existing_connection_pairs.add((from_edge, to_edge))

    def _is_tls_controlled_node(self, node_id: str) -> bool:
        node_type = self.node_types.get(node_id, "").lower()
        return "traffic_light" in node_type

    def _compute_stats(self) -> Dict[str, float]:
        node_count = self.graph.number_of_nodes()
        edge_count = self.graph.number_of_edges()

        if node_count == 0:
            return {
                "total_nodes": 0,
                "total_edges": 0,
                "components": 0,
                "component_sizes": [],
                "largest_component_edges": 0,
                "isolated_nodes": 0,
                "avg_node_degree": 0.0,
                "largest_component_pct_edges": 0.0,
            }

        components = list(nx.weakly_connected_components(self.graph))
        component_sizes = []
        largest_component_edges = 0
        for comp in components:
            sub = self.graph.subgraph(comp)
            e_count = sub.number_of_edges()
            component_sizes.append(e_count)
            largest_component_edges = max(largest_component_edges, e_count)

        isolated_nodes = sum(1 for n in self.graph.nodes if self.graph.degree(n) == 0)
        avg_degree = (sum(dict(self.graph.degree()).values()) / node_count) if node_count else 0.0
        largest_pct = (largest_component_edges / edge_count * 100.0) if edge_count else 0.0

        return {
            "total_nodes": node_count,
            "total_edges": edge_count,
            "components": len(components),
            "component_sizes": sorted(component_sizes, reverse=True),
            "largest_component_edges": largest_component_edges,
            "isolated_nodes": isolated_nodes,
            "avg_node_degree": avg_degree,
            "largest_component_pct_edges": largest_pct,
        }

    def _distance(self, a: str, b: str) -> float:
        ax, ay = self.nodes[a]
        bx, by = self.nodes[b]
        return math.hypot(ax - bx, ay - by)

    def _unit_vector(self, x: float, y: float) -> Tuple[float, float]:
        mag = math.hypot(x, y)
        if mag == 0:
            return 0.0, 0.0
        return x / mag, y / mag

    def _dot(self, u: Tuple[float, float], v: Tuple[float, float]) -> float:
        return u[0] * v[0] + u[1] * v[1]

    def _avg_out_vector(self, node_id: str) -> Tuple[float, float]:
        x0, y0 = self.nodes[node_id]
        vecs = []
        for _, to_node in self.graph.out_edges(node_id):
            x1, y1 = self.nodes[to_node]
            vecs.append(self._unit_vector(x1 - x0, y1 - y0))
        if not vecs:
            return 0.0, 0.0
        return self._unit_vector(sum(v[0] for v in vecs), sum(v[1] for v in vecs))

    def _avg_in_vector(self, node_id: str) -> Tuple[float, float]:
        x0, y0 = self.nodes[node_id]
        vecs = []
        for from_node, _ in self.graph.in_edges(node_id):
            x1, y1 = self.nodes[from_node]
            vecs.append(self._unit_vector(x0 - x1, y0 - y1))
        if not vecs:
            return 0.0, 0.0
        return self._unit_vector(sum(v[0] for v in vecs), sum(v[1] for v in vecs))

    def _has_outgoing(self, node_id: str) -> bool:
        return self.graph.out_degree(node_id) > 0

    def _has_incoming(self, node_id: str) -> bool:
        return self.graph.in_degree(node_id) > 0

    def _closest_edge_template(self, a: str, b: str) -> Optional[EdgeRecord]:
        ax, ay = self.nodes[a]
        bx, by = self.nodes[b]
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0

        best_edge = None
        best_dist = float("inf")
        for edge in self.edges.values():
            ex1, ey1 = self.nodes[edge.from_node]
            ex2, ey2 = self.nodes[edge.to_node]
            d = min(math.hypot(mx - ex1, my - ey1), math.hypot(mx - ex2, my - ey2))
            if d < best_dist:
                best_dist = d
                best_edge = edge
        return best_edge

    def _create_xml_edge(self, from_node: str, to_node: str) -> str:
        template = self._closest_edge_template(from_node, to_node)
        speed = template.speed if template else DEFAULT_SPEED
        lanes = template.lanes if template else DEFAULT_LANES
        edge_type = template.edge_type if template else ""
        priority = template.priority if template else DEFAULT_PRIORITY

        edge_id = f"repair_e_{self._edge_counter}"
        self._edge_counter += 1

        x1, y1 = self.nodes[from_node]
        x2, y2 = self.nodes[to_node]
        length = math.hypot(x2 - x1, y2 - y1)

        edge_attrs = {
            "id": edge_id,
            "from": from_node,
            "to": to_node,
            "priority": str(priority),
            "numLanes": str(max(1, int(lanes))),
            "speed": f"{speed:.2f}",
        }
        if edge_type:
            edge_attrs["type"] = edge_type

        edge_el = ET.Element("edge", edge_attrs)
        shape = f"{x1:.2f},{y1:.2f} {x2:.2f},{y2:.2f}"
        lane_el = ET.SubElement(
            edge_el,
            "lane",
            {
                "id": f"{edge_id}_0",
                "index": "0",
                "speed": f"{speed:.2f}",
                "length": f"{length:.2f}",
                "shape": shape,
            },
        )
        _ = lane_el

        self.root.append(edge_el)

        self.edges[edge_id] = EdgeRecord(
            edge_id=edge_id,
            from_node=from_node,
            to_node=to_node,
            length=length,
            speed=speed,
            lanes=max(1, int(lanes)),
            edge_type=edge_type,
            priority=priority,
        )
        self.graph.add_edge(from_node, to_node, edge_id=edge_id, length=length)
        self._existing_edge_pairs.add((from_node, to_node))
        self.repairs["new_edges"] += 1
        return edge_id

    def _create_xml_connection(self, from_edge: str, to_edge: str) -> None:
        if (from_edge, to_edge) in self._existing_connection_pairs:
            return
        conn_el = ET.Element(
            "connection",
            {
                "from": from_edge,
                "to": to_edge,
                "fromLane": "0",
                "toLane": "0",
                "dir": "s",
                "state": "M",
            },
        )
        self.root.append(conn_el)
        self.connections.add((from_edge, to_edge))
        self._existing_connection_pairs.add((from_edge, to_edge))
        self.repairs["new_connections"] += 1

    def _sanitize_connection_attributes(self) -> None:
        for conn_el in self.root.findall("./connection"):
            if "from" not in conn_el.attrib or "to" not in conn_el.attrib:
                continue
            if not conn_el.get("dir"):
                conn_el.set("dir", "s")
            if not conn_el.get("state"):
                conn_el.set("state", "M")

    def _is_direction_compatible(self, from_node: str, to_node: str) -> bool:
        x1, y1 = self.nodes[from_node]
        x2, y2 = self.nodes[to_node]
        ab = self._unit_vector(x2 - x1, y2 - y1)
        out_v = self._avg_out_vector(from_node)
        in_v = self._avg_in_vector(to_node)
        if out_v == (0.0, 0.0) or in_v == (0.0, 0.0):
            return True
        return self._dot(ab, out_v) > 0.3 and self._dot(ab, in_v) > 0.3

    def snap_nearby_nodes(self, threshold_m: float = 7.0, max_new_edges: int = 1000) -> int:
        if threshold_m <= 0:
            raise ValueError("threshold_m must be positive.")
        if self.graph.number_of_edges() == 0:
            print("Network has zero edges. Skipping node snapping.")
            return 0

        bucket_size = threshold_m
        buckets: Dict[Tuple[int, int], List[str]] = defaultdict(list)
        for node_id, (x, y) in self.nodes.items():
            key = (int(x // bucket_size), int(y // bucket_size))
            buckets[key].append(node_id)

        candidate_pairs: Set[Tuple[str, str]] = set()
        neighbor_offsets = [-1, 0, 1]
        for (bx, by), node_list in buckets.items():
            nearby_nodes = []
            for ox in neighbor_offsets:
                for oy in neighbor_offsets:
                    nearby_nodes.extend(buckets.get((bx + ox, by + oy), []))
            for a in node_list:
                for b in nearby_nodes:
                    if a == b:
                        continue
                    pair = (a, b) if a < b else (b, a)
                    candidate_pairs.add(pair)

        print(f"Checking {len(candidate_pairs)} node pairs for snapping...")
        created = 0
        for a, b in candidate_pairs:
            if created >= max_new_edges:
                self.repairs["warnings"].append(
                    f"Reached max_new_edges={max_new_edges}. Additional candidate links skipped."
                )
                break
            if self._distance(a, b) >= threshold_m:
                continue

            if self._has_outgoing(a) and self._has_incoming(b):
                if (a, b) not in self._existing_edge_pairs and self._is_direction_compatible(a, b):
                    self._create_xml_edge(a, b)
                    created += 1

            if created >= max_new_edges:
                break

            if self._has_outgoing(b) and self._has_incoming(a):
                if (b, a) not in self._existing_edge_pairs and self._is_direction_compatible(b, a):
                    self._create_xml_edge(b, a)
                    created += 1

        if self.repairs["new_edges"] > 100:
            self.repairs["warnings"].append(
                f"Suspicious operation: {self.repairs['new_edges']} new edges created."
            )
        self.after_stats = self._compute_stats()
        return created

    def rebuild_connections(
        self, min_alignment_cos: float = -0.2, allow_unsafe_xml_connections: bool = False
    ) -> int:
        if not allow_unsafe_xml_connections:
            self.repairs["warnings"].append(
                "Skipped rebuilding missing turn connections because direct .net.xml connection injection "
                "can corrupt junction logic indices. Enable allow_unsafe_xml_connections=True only if you "
                "will rebuild the net with netconvert afterwards."
            )
            return 0
        print("Rebuilding missing turn connections...")
        edge_ids_by_from: Dict[str, List[str]] = defaultdict(list)
        edge_ids_by_to: Dict[str, List[str]] = defaultdict(list)
        for edge_id, edge in self.edges.items():
            edge_ids_by_from[edge.from_node].append(edge_id)
            edge_ids_by_to[edge.to_node].append(edge_id)

        created = 0
        for node_id in self.nodes:
            # Adding new connections at TLS-controlled junctions requires
            # synchronizing tlLogic link indices, which this repair tool
            # does not recompute. Skip to keep the net SUMO-valid.
            if self._is_tls_controlled_node(node_id):
                continue
            incoming = edge_ids_by_to.get(node_id, [])
            outgoing = edge_ids_by_from.get(node_id, [])
            if not incoming or not outgoing:
                continue

            for in_eid in incoming:
                in_edge = self.edges[in_eid]
                in_from = in_edge.from_node
                x1, y1 = self.nodes[in_from]
                x2, y2 = self.nodes[node_id]
                vin = self._unit_vector(x2 - x1, y2 - y1)

                for out_eid in outgoing:
                    if in_eid == out_eid:
                        continue
                    if (in_eid, out_eid) in self._existing_connection_pairs:
                        continue

                    out_edge = self.edges[out_eid]
                    out_to = out_edge.to_node
                    x3, y3 = self.nodes[out_to]
                    vout = self._unit_vector(x3 - x2, y3 - y2)

                    alignment = self._dot(vin, vout)
                    if alignment < min_alignment_cos:
                        continue

                    self._create_xml_connection(in_eid, out_eid)
                    created += 1

        self.after_stats = self._compute_stats()
        return created

    def remove_problematic_edges(
        self, short_edge_threshold_m: float = 5.0, remove_isolated_dead_ends: bool = False
    ) -> int:
        to_remove = []
        for edge_id, edge in self.edges.items():
            if edge.length < short_edge_threshold_m:
                to_remove.append(edge_id)
                continue
            if remove_isolated_dead_ends:
                deg_from = self.graph.degree(edge.from_node)
                deg_to = self.graph.degree(edge.to_node)
                if deg_from <= 1 and deg_to <= 1:
                    to_remove.append(edge_id)

        if not to_remove:
            return 0

        parent_map = {child: parent for parent in self.root.iter() for child in parent}
        removed = 0
        for edge_id in to_remove:
            if edge_id not in self.edges:
                continue
            edge_rec = self.edges[edge_id]

            edge_el = self.root.find(f"./edge[@id='{edge_id}']")
            if edge_el is not None and edge_el in parent_map:
                parent_map[edge_el].remove(edge_el)

            for conn in list(self.root.findall(f"./connection[@from='{edge_id}']")):
                if conn in parent_map:
                    parent_map[conn].remove(conn)
            for conn in list(self.root.findall(f"./connection[@to='{edge_id}']")):
                if conn in parent_map:
                    parent_map[conn].remove(conn)

            if self.graph.has_edge(edge_rec.from_node, edge_rec.to_node):
                self.graph.remove_edge(edge_rec.from_node, edge_rec.to_node)
            self._existing_edge_pairs.discard((edge_rec.from_node, edge_rec.to_node))
            del self.edges[edge_id]
            removed += 1

        self.repairs["removed_edges"] += removed
        self.after_stats = self._compute_stats()
        return removed

    def validate_connectivity(self) -> Dict[str, float]:
        self.after_stats = self._compute_stats()
        return self.after_stats

    def _health_label(self, largest_component_pct_edges: float) -> str:
        if largest_component_pct_edges > 80.0:
            return "highly connected"
        if largest_component_pct_edges >= 40.0:
            return "partially connected"
        return "still fragmented"

    def get_repair_stats(self) -> Dict[str, float]:
        before = self.before_stats
        after = self.after_stats
        new_edges = self.repairs["new_edges"]
        return {
            "before_components": before["components"],
            "after_components": after["components"],
            "before_largest_component_edges": before["largest_component_edges"],
            "after_largest_component_edges": after["largest_component_edges"],
            "new_edges": new_edges,
            "new_connections": self.repairs["new_connections"],
            "removed_edges": self.repairs["removed_edges"],
            "before_isolated_nodes": before["isolated_nodes"],
            "after_isolated_nodes": after["isolated_nodes"],
            "warnings": list(self.repairs["warnings"]),
            "network_health": self._health_label(after["largest_component_pct_edges"]),
        }

    def print_connection_report(self) -> None:
        before = self.before_stats
        after = self.after_stats
        stats = self.get_repair_stats()

        print("\n=== Connection Analysis Report ===")
        print("Before Repair:")
        print(f"  Total nodes: {before['total_nodes']}")
        print(f"  Total edges: {before['total_edges']}")
        print(f"  Connected components: {before['components']}")
        print(f"  Largest component size: {before['largest_component_edges']} edges")
        print(f"  Isolated nodes: {before['isolated_nodes']}")
        print(f"  Average node degree: {before['avg_node_degree']:.2f}")
        print(f"  Component sizes (edges): {before['component_sizes']}")

        print("\nAfter Repair:")
        print(f"  Total nodes: {after['total_nodes']}")
        print(f"  Total edges: {after['total_edges']} (↑ by {after['total_edges'] - before['total_edges']})")
        print(f"  Connected components: {after['components']} (↓ from {before['components']})")
        print(
            f"  Largest component size: {after['largest_component_edges']} edges "
            f"(↑ by {after['largest_component_edges'] - before['largest_component_edges']})"
        )
        print(f"  Isolated nodes: {after['isolated_nodes']} (↓ from {before['isolated_nodes']})")
        print(f"  Average node degree: {after['avg_node_degree']:.2f} (from {before['avg_node_degree']:.2f})")
        print(f"  Component sizes (edges): {after['component_sizes']}")

        print("\nSummary:")
        print(f"  New edges created: {stats['new_edges']}")
        print(f"  New turn connections created: {stats['new_connections']}")
        print(f"  Removed problematic edges: {stats['removed_edges']}")
        print(f"  Nodes connected: {before['isolated_nodes'] - after['isolated_nodes']}")
        print(f"  Connectivity improvement: {before['components'] - after['components']} fewer components")
        print(f"  Network is now: {stats['network_health']}")

        if stats["warnings"]:
            print("\nWarnings:")
            for w in stats["warnings"]:
                print(f"  - {w}")

    def save(self, output_path: str = "cleaned_network.net.xml") -> str:
        self.after_stats = self._compute_stats()
        self._sanitize_connection_attributes()
        self.tree.write(output_path, encoding="utf-8", xml_declaration=True)
        return output_path


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Repair connectivity in SUMO .net.xml files.")
    parser.add_argument("input", help="Input SUMO .net.xml file")
    parser.add_argument("--output", default="cleaned_network.net.xml", help="Output .net.xml file")
    parser.add_argument("--snap-threshold", type=float, default=7.0, help="Node snap threshold in meters")
    parser.add_argument(
        "--short-edge-threshold",
        type=float,
        default=5.0,
        help="Remove edges shorter than this value in meters",
    )
    parser.add_argument(
        "--remove-problematic-edges",
        action="store_true",
        help="Enable removal of short/isolated problematic edges",
    )
    parser.add_argument(
        "--remove-isolated-dead-ends",
        action="store_true",
        help="When removing problematic edges, also remove isolated dead-end edges",
    )
    parser.add_argument(
        "--unsafe-rebuild-connections",
        action="store_true",
        help=(
            "Allow direct insertion of missing <connection> elements. "
            "Can produce invalid junction logic unless followed by netconvert rebuild."
        ),
    )
    args = parser.parse_args()

    tool = NetworkRepairTool(args.input)
    tool.snap_nearby_nodes(threshold_m=args.snap_threshold)
    tool.rebuild_connections(allow_unsafe_xml_connections=args.unsafe_rebuild_connections)

    if args.remove_problematic_edges:
        tool.remove_problematic_edges(
            short_edge_threshold_m=args.short_edge_threshold,
            remove_isolated_dead_ends=args.remove_isolated_dead_ends,
        )

    tool.validate_connectivity()
    out = tool.save(args.output)
    tool.print_connection_report()
    print(f"\nSaved cleaned network to: {out}")


if __name__ == "__main__":
    _cli()
