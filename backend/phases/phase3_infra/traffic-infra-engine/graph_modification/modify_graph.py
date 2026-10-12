import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, Point


class GraphModificationEngine:
    """
    Responsibilities: Modify the mathematical road graph, splice new edges, calculate network metrics.
    """
    def __init__(self, graph):
        # Work on a copy of the graph to easily compare impact later without corrupting original
        self.original_G = graph
        self.G = graph.copy()

    def add_infrastructure(
        self,
        proposed_coords,
        is_oneway=False,
        infra_type="New Road",
        flyover_height_m=5.0,
        proposal_segment_offset=0,
        proposal_index=0,
    ):
        """
        Add the proposal to the graph using the nearest existing network nodes.

        Auto-connect proposals contain exactly two points, so the resulting
        proposed segment is always a single straight segment between the
        snapped endpoints. Manually drawn proposals retain every supplied
        waypoint and are represented as ordered consecutive segments.
        Flyovers and tunnels carry 3D SUMO shapes that pass above and below
        the existing ground-level network respectively.
        """
        print("\n[Graph Mod Engine] Modifying Network Graph with Waypoint Snapping...")

        if len(proposed_coords) < 2:
            raise ValueError("At least two proposal coordinates are required.")

        normalized_infra_type = str(infra_type).strip().lower()
        is_flyover = normalized_infra_type == "flyover"
        is_tunnel = normalized_infra_type == "tunnel"
        # A two-point auto-connected proposal is already straight. Preserve
        # all waypoints for manually drawn paths, including flyovers, so the
        # route builder can follow the complete intervention alignment.
        coords = proposed_coords

        total_added_length = 0
        spliced_segments = 0

        snapped_nodes = [
            ox.distance.nearest_nodes(self.G, X=coord[0], Y=coord[1])
            for coord in coords
        ]

        proposal_segment_index = int(proposal_segment_offset)
        for index, (start_node, end_node) in enumerate(
            zip(snapped_nodes[:-1], snapped_nodes[1:])
        ):
            if start_node == end_node:
                continue

            start_coord = coords[index]
            end_coord = coords[index + 1]
            segment_length = Point(start_coord).distance(Point(end_coord))
            edge_data = {
                "length": segment_length,
                "name": (
                    "AI Flyover"
                    if is_flyover
                    else "AI Tunnel" if is_tunnel else "Proposed Infra"
                ),
                "highway": (
                    "proposed_flyover"
                    if is_flyover
                    else "proposed_tunnel" if is_tunnel else "proposed_road"
                ),
                "infra_type": str(infra_type),
                "proposal_segment": True,
                "proposal_segment_index": proposal_segment_index,
                # Distinguishes the items of a multi-infra package; 0 is the
                # main structure that Route 4 is built through.
                "proposal_index": int(proposal_index),
                "is_flyover": is_flyover,
                "is_tunnel": is_tunnel,
                "flyover_height_m": float(flyover_height_m) if is_flyover else 0.0,
                "tunnel_depth_m": float(flyover_height_m) if is_tunnel else 0.0,
                "proposal_direction": "forward",
            }

            if is_flyover or is_tunnel:
                start_x = float(self.G.nodes[start_node].get("x"))
                start_y = float(self.G.nodes[start_node].get("y"))
                end_x = float(self.G.nodes[end_node].get("x"))
                end_y = float(self.G.nodes[end_node].get("y"))
                mid_x = (start_x + end_x) / 2.0
                mid_y = (start_y + end_y) / 2.0
                edge_data["sumo_shape"] = [
                    (start_x, start_y, 0.0),
                    (
                        mid_x,
                        mid_y,
                        float(flyover_height_m) if is_flyover else -float(flyover_height_m),
                    ),
                    (end_x, end_y, 0.0),
                ]

            self.G.add_edge(start_node, end_node, **edge_data)

            if not is_oneway:
                reverse_data = dict(edge_data)
                reverse_data["proposal_direction"] = "reverse"
                if is_flyover or is_tunnel:
                    reverse_data["sumo_shape"] = list(reversed(edge_data["sumo_shape"]))
                self.G.add_edge(end_node, start_node, **reverse_data)

            total_added_length += segment_length
            spliced_segments += 1
            proposal_segment_index += 1

        print(f"GRAPH UPDATED: Spliced {spliced_segments} consecutive segments via Waypoint Snapping.")
        print(f"   ↳ Total added road length: {total_added_length:.2f} meters")

        return self.G

    def add_widening(self, proposed_coords, extra_lanes=1, name="Widening"):
        """
        Record a widening of the existing roads along ``proposed_coords``.

        Widening does not add graph edges. The alignment is stored on the
        graph and applied when the SUMO network is built, by adding lanes to
        the existing SUMO edges that run along it.
        """
        if len(proposed_coords) < 2:
            raise ValueError("At least two widening coordinates are required.")
        self.G.graph.setdefault("uidt_widenings", []).append(
            {
                "coords": [(float(x), float(y)) for x, y in proposed_coords],
                "extra_lanes": max(1, int(extra_lanes)),
                "name": str(name),
            }
        )
        print(f"[Graph Mod Engine] Recorded widening '{name}' (+{int(extra_lanes)} lane(s)).")
        return self.G

    def add_road_block(self, proposed_coords, name="Road Block", radius_m=12.0):
        """Close existing graph edges that overlap the selected road alignment.

        Road blocks do not create replacement infrastructure. The selected
        alignment is retained as metadata so the SUMO exporter can remove the
        corresponding baseline edges from the generated network.
        """
        if len(proposed_coords) < 2:
            raise ValueError("At least two road-block coordinates are required.")

        block_line = LineString(proposed_coords)
        block_area = block_line.buffer(float(radius_m))
        blocked_edges = []
        for source, target, key, data in list(self.G.edges(keys=True, data=True)):
            geometry = data.get("geometry")
            if geometry is None:
                start = (self.G.nodes[source].get("x"), self.G.nodes[source].get("y"))
                end = (self.G.nodes[target].get("x"), self.G.nodes[target].get("y"))
                if None in (*start, *end):
                    continue
                geometry = LineString([start, end])
            if geometry.intersects(block_area):
                blocked_edges.append((source, target, key))

        for source, target, key in blocked_edges:
            self.G.remove_edge(source, target, key)

        self.G.graph.setdefault("uidt_road_blocks", []).append(
            {
                "coords": [(float(x), float(y)) for x, y in proposed_coords],
                "name": str(name),
                "radius_m": float(radius_m),
                "blocked_graph_edges": len(blocked_edges),
            }
        )
        print(f"[Graph Mod Engine] Closed {len(blocked_edges)} graph edge(s) for '{name}'.")
        return self.G

    def calculate_impact(self):
        """
        Calculates the impact of the new infrastructure on the network.
        """
        print("\n[Graph Mod Engine] Calculating Impact Metrics...")
        
        orig_nodes, orig_edges = len(self.original_G.nodes), len(self.original_G.edges)
        new_nodes, new_edges = len(self.G.nodes), len(self.G.edges)
        
        # Basic topological metrics
        report = {
            "Original Network Edges": orig_edges,
            "Updated Network Edges": new_edges,
            "New Connections Spliced": new_edges - orig_edges,
        }
        
        return report
