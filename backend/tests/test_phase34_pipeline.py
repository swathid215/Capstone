import importlib.util
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import networkx as nx

ENGINE_DIR = Path(__file__).resolve().parents[1] / "phases" / "phase3_infra" / "traffic-infra-engine"
import sys
sys.path.insert(0, str(ENGINE_DIR))

MODULE_PATH = ENGINE_DIR / "phase34_pipeline.py"
SPEC = importlib.util.spec_from_file_location("phase34_pipeline", MODULE_PATH)
phase34_pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(phase34_pipeline)
Phase34Pipeline = phase34_pipeline.Phase34Pipeline

from phase34_visualizer import Phase34Visualizer

from api import simulation_service
from api.alternative_service import (
    generate_alternative_candidates,
    generate_same_endpoint_candidates,
    evaluate_candidate,
    rank_candidate_evaluations,
    validate_candidate_constraints,
)


def vtype_ids(route_path):
    return [vtype.get("id") for vtype in ET.parse(route_path).getroot().findall("vType")]


class Phase34PipelineRoadOverlayTests(unittest.TestCase):
    def test_road_block_reroutes_affected_vehicles_without_dropping_them(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            route_path = tmp_path / "baseline.rou.xml"
            route_path.write_text(
                '<routes><vType id="car" maxSpeed="20" />'
                '<vehicle id="untouched" type="car" depart="11">'
                '<route edges="a b" /></vehicle>'
                '<vehicle id="affected" type="car" depart="12">'
                '<route edges="start blocked end" /></vehicle>'
                '<vehicle id="endpoint" depart="13" departPos="40" departLane="1">'
                '<route edges="blocked mid end" /></vehicle>'
                '<vehicle id="isolated" depart="14">'
                '<route edges="left blocked right" /></vehicle></routes>',
                encoding="utf-8",
            )
            after_net = tmp_path / "modified.net.xml"
            after_net.write_text("<net />", encoding="utf-8")

            def duarouter_run(command, **_kwargs):
                trips_path = Path(command[command.index("--route-files") + 1])
                trips = {trip.get("id"): trip.attrib for trip in ET.parse(trips_path).getroot().findall("trip")}
                # Only vehicles touching the closure are submitted for rerouting.
                self.assertEqual(set(trips), {"affected", "endpoint", "isolated"})
                self.assertEqual(trips["affected"], {
                    "id": "affected", "type": "car", "depart": "12", "from": "start", "to": "end",
                })
                # A closed origin is relocated, and its position pin released.
                self.assertEqual(trips["endpoint"]["from"], "mid")
                self.assertNotIn("departPos", trips["endpoint"])
                self.assertEqual(trips["endpoint"]["departLane"], "best")
                output_path = Path(command[command.index("--output-file") + 1])
                output_path.write_text(
                    '<routes><vehicle id="affected" depart="12"><route edges="start detour end" /></vehicle>'
                    '<vehicle id="endpoint" depart="13"><route edges="mid end" /></vehicle></routes>',
                    encoding="utf-8",
                )
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.object(
                simulation_service, "_duarouter_binary", return_value="duarouter"
            ), mock.patch.object(
                simulation_service.subprocess, "run", side_effect=duarouter_run
            ):
                with redirect_stdout(io.StringIO()):
                    result_path, stats = simulation_service._reroute_around_blocked_edges(
                        route_path, after_net, {"blocked"}, tmp_path
                    )

            vehicles = {
                vehicle.get("id"): vehicle
                for vehicle in ET.parse(result_path).getroot().findall("vehicle")
            }
            self.assertEqual(vehicles["untouched"].find("route").get("edges"), "a b")
            self.assertEqual(vehicles["affected"].find("route").get("edges"), "start detour end")
            self.assertEqual(vehicles["endpoint"].find("route").get("edges"), "mid end")
            self.assertNotIn("departPos", vehicles["endpoint"].attrib)
            # "isolated" has no open path on the (empty) network: reported, not silently lost.
            self.assertNotIn("isolated", vehicles)
            self.assertEqual(stats["affected_vehicle_count"], 3)
            self.assertEqual(stats["rerouted_vehicle_count"], 2)
            self.assertEqual(stats["relocated_origin_count"], 1)
            self.assertEqual(stats["unroutable_vehicle_ids"], ["isolated"])
            self.assertEqual(vtype_ids(result_path), ["car"])

    def test_road_block_rerouting_is_noop_without_blocked_edges(self):
        route_path = Path("baseline.rou.xml")
        with mock.patch.object(simulation_service, "_duarouter_binary") as binary:
            result, stats = simulation_service._reroute_around_blocked_edges(
                route_path, Path("modified.net.xml"), set(), Path("temp")
            )
        self.assertEqual(result, route_path)
        self.assertEqual(stats["rerouted_vehicle_count"], 0)
        binary.assert_not_called()

    def test_closed_origin_relocates_to_upstream_open_edge(self):
        class Node:
            def __init__(self):
                self.incoming, self.outgoing = [], []

            def getIncoming(self):
                return self.incoming

            def getOutgoing(self):
                return self.outgoing

        class Edge:
            def __init__(self, edge_id, source, target):
                self.edge_id, self.source, self.target = edge_id, source, target
                source.outgoing.append(self)
                target.incoming.append(self)

            def getID(self):
                return self.edge_id

            def getFromNode(self):
                return self.source

            def getToNode(self):
                return self.target

            def allows(self, _vehicle_class):
                return True

        n0, n1, n2, n3 = Node(), Node(), Node(), Node()
        edges = {
            edge.getID(): edge
            for edge in (Edge("approach", n0, n1), Edge("closed_a", n1, n2), Edge("closed_b", n2, n3), Edge("exit", n3, n0))
        }
        net = mock.Mock()
        net.getEdge.side_effect = edges.__getitem__
        blocked = {"closed_a", "closed_b"}

        # Origin in the middle of a two-edge closure walks back past it.
        self.assertEqual(
            simulation_service._endpoint_candidates(["closed_b", "exit"], blocked, net, origin=True)[0],
            "approach",
        )
        # Destination on the closure exits downstream of it.
        self.assertEqual(
            simulation_service._endpoint_candidates(["approach", "closed_a"], blocked, net, origin=False)[0],
            "exit",
        )

    def test_cohort_metrics_count_unfinished_vehicles_and_pair_by_vehicle(self):
        before = {
            "v1": {"completed": True, "duration_sec": 100.0, "route_length_m": 1000.0, "waiting_time_sec": 10.0},
            "v2": {"completed": True, "duration_sec": 200.0, "route_length_m": 1000.0, "waiting_time_sec": 50.0},
            "v3": {"completed": True, "duration_sec": 300.0, "route_length_m": 1000.0, "waiting_time_sec": 0.0},
        }
        after = {
            "v1": {"completed": True, "duration_sec": 80.0, "route_length_m": 1000.0, "waiting_time_sec": 0.0},
            "v2": {"completed": True, "duration_sec": 150.0, "route_length_m": 1000.0, "waiting_time_sec": 10.0},
            "v3": {"completed": False, "duration_sec": 900.0, "route_length_m": 400.0, "waiting_time_sec": 0.0},
        }
        comparison = simulation_service._scope_comparison("test", before, after, ["v1", "v2", "v3", "never_inserted"])

        self.assertEqual(comparison["after"]["completed_vehicle_count"], 2)
        self.assertEqual(comparison["after"]["active_vehicles"], 2)
        self.assertEqual(comparison["before"]["completion_rate_pct"], 75.0)
        paired = comparison["paired"]
        # v3 did not finish after the change, so it is excluded from the pairing.
        self.assertEqual(paired["paired_vehicle_count"], 2)
        self.assertEqual(paired["mean_before_sec"], 150.0)
        self.assertEqual(paired["mean_after_sec"], 115.0)
        self.assertEqual(paired["mean_delta_sec"], -35.0)
        self.assertEqual(paired["improved_vehicle_count"], 2)
        self.assertEqual(comparison["time_reduction_pct"], 23.3)
        low, high = paired["ci95_mean_delta_sec"]
        self.assertLessEqual(low, -35.0)
        self.assertGreaterEqual(high, -35.0)

    def test_sumo_statistics_parse_teleports(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "stats.xml"
            path.write_text(
                '<statistics><vehicles loaded="10" inserted="9" running="1" waiting="1"/>'
                '<teleports total="3" jam="2" yield="1" wrongLane="0"/></statistics>',
                encoding="utf-8",
            )
            parsed = simulation_service._parse_sumo_statistics(path)
        self.assertEqual(parsed["inserted_vehicle_count"], 9)
        self.assertEqual(parsed["teleport_count"], 3)
        self.assertEqual(parsed["teleport_jam_count"], 2)

    def test_build_modified_network_geojson_creates_line_features(self):
        pipeline = Phase34Pipeline(input_json_path="")
        graph = nx.Graph()
        graph.add_node(1, x=12.92, y=77.55)
        graph.add_node(2, x=12.921, y=77.551)
        graph.add_edge(1, 2, length=120, name="Proposed Infra", highway="proposed_road")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "modified_roads.geojson"
            geojson_path = pipeline._write_modified_roads_geojson(graph, output_path)

            with open(geojson_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

            self.assertEqual(payload["type"], "FeatureCollection")
            self.assertEqual(len(payload["features"]), 1)
            self.assertEqual(payload["features"][0]["properties"]["highway"], "proposed_road")
            self.assertEqual(payload["features"][0]["geometry"]["type"], "LineString")

    def test_resolve_after_net_path_builds_preview_when_infeasible(self):
        graph = nx.Graph()
        graph.add_node("n1", x=12.92, y=77.55)
        graph.add_node("n2", x=12.921, y=77.551)
        graph.add_edge("n1", "n2", length=120, name="Proposed Infra", highway="proposed_road")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            baseline_net = tmp_path / "cleaned_network.net.xml"
            baseline_net.write_text("<net />", encoding="utf-8")

            def mock_run(*args, **kwargs):
                out_file = tmp_path / "cleaned_modified_network.net.xml"
                out_file.write_text("<net />", encoding="utf-8")
                res = mock.MagicMock()
                res.returncode = 0
                return res

            with mock.patch.object(simulation_service, "AUTO_TOOL_DIR", tmp_path), \
                 mock.patch.object(simulation_service, "BASELINE_NET", baseline_net), \
                 mock.patch("subprocess.run", side_effect=mock_run):
                after_net = simulation_service._resolve_after_net_path(graph, False)

            self.assertTrue(after_net.exists())
            self.assertEqual(after_net.name, "cleaned_modified_network.net.xml")

    def test_proposed_sumo_geometry_uses_baseline_network_offset(self):
        graph = nx.DiGraph()
        graph.add_node("n1", x=110.0, y=220.0)
        graph.add_node("n2", x=130.0, y=240.0)
        graph.add_edge(
            "n1",
            "n2",
            highway="proposed_flyover",
            sumo_shape=[(110.0, 220.0, 0.0), (120.0, 230.0, 5.0), (130.0, 240.0, 0.0)],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            baseline_net = tmp_path / "cleaned_network.net.xml"
            baseline_net.write_text(
                '<net><location netOffset="-100,-200" /></net>',
                encoding="utf-8",
            )
            nodes_path = tmp_path / "proposed_nodes.nod.xml"
            edges_path = tmp_path / "proposed_edges.edg.xml"
            offset = simulation_service._sumo_net_offset(baseline_net)

            simulation_service._write_proposed_node_file(
                graph,
                nodes_path,
                coordinate_offset=offset,
            )
            simulation_service._write_proposed_edge_file(
                graph,
                edges_path,
                coordinate_offset=offset,
            )

            nodes = {
                node.get("id"): (node.get("x"), node.get("y"))
                for node in ET.parse(nodes_path).getroot()
            }
            edge = ET.parse(edges_path).getroot()[0]

            self.assertEqual(nodes["n1"], ("10.0", "20.0"))
            self.assertEqual(nodes["n2"], ("30.0", "40.0"))
            self.assertEqual(
                edge.get("shape"),
                "10.0,20.0,0.0 20.0,30.0,5.0 30.0,40.0,0.0",
            )

    def test_proposed_tunnel_is_exported_as_below_ground_sumotype(self):
        graph = nx.DiGraph()
        graph.add_node("n1", x=110.0, y=220.0)
        graph.add_node("n2", x=130.0, y=240.0)
        graph.add_edge(
            "n1",
            "n2",
            highway="proposed_tunnel",
            sumo_shape=[(110.0, 220.0, 0.0), (120.0, 230.0, -5.0), (130.0, 240.0, 0.0)],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            edges_path = Path(tmp_dir) / "proposed_edges.edg.xml"
            simulation_service._write_proposed_edge_file(graph, edges_path)
            edge = ET.parse(edges_path).getroot()[0]

            self.assertEqual(edge.get("type"), "uidt.tunnel")
            self.assertIn("-5.0", edge.get("shape"))

    def test_proposed_connection_file_adds_incoming_road_lanes(self):
        graph = nx.DiGraph()
        graph.add_node("n1", x=110.0, y=220.0)
        graph.add_node("n2", x=130.0, y=240.0)
        graph.add_edge("n1", "n2", highway="proposed_road")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            baseline_net = tmp_path / "cleaned_network.net.xml"
            baseline_net.write_text(
                """
                <net>
                  <edge id="incoming" from="before" to="n1">
                    <lane id="incoming_0" />
                    <lane id="incoming_1" />
                  </edge>
                  <edge id="outgoing" from="n1" to="after">
                    <lane id="outgoing_0" />
                  </edge>
                </net>
                """,
                encoding="utf-8",
            )
            connections_path = tmp_path / "proposed_connections.con.xml"

            simulation_service._write_proposed_connection_file(
                graph,
                baseline_net,
                connections_path,
            )

            connections = [
                (connection.get("from"), connection.get("to"), connection.get("fromLane"), connection.get("toLane"))
                for connection in ET.parse(connections_path).getroot()
            ]
            self.assertEqual(
                connections,
                [
                    ("incoming", "proposed_edge_0", "0", "0"),
                    ("incoming", "proposed_edge_0", "1", "1"),
                ],
            )

    def test_proposed_edge_export_preserves_drawn_segment_order(self):
        graph = nx.MultiDiGraph()
        for node_id in ("n0", "n1", "n2"):
            graph.add_node(node_id, x=0.0, y=0.0)

        # Insert the second segment first to ensure ordering comes from the
        # proposal metadata rather than NetworkX's node iteration order.
        graph.add_edge(
            "n1",
            "n2",
            highway="proposed_road",
            proposal_segment_index=1,
            proposal_direction="forward",
        )
        graph.add_edge(
            "n2",
            "n1",
            highway="proposed_road",
            proposal_segment_index=1,
            proposal_direction="reverse",
        )
        graph.add_edge(
            "n0",
            "n1",
            highway="proposed_road",
            proposal_segment_index=0,
            proposal_direction="forward",
        )
        graph.add_edge(
            "n1",
            "n0",
            highway="proposed_road",
            proposal_segment_index=0,
            proposal_direction="reverse",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            edges_path = Path(tmp_dir) / "proposed_edges.edg.xml"
            simulation_service._write_proposed_edge_file(graph, edges_path)
            edge_ids = [
                edge.get("id")
                for edge in ET.parse(edges_path).getroot()
            ]

        self.assertEqual(
            edge_ids,
            [
                "proposed_edge_0_forward",
                "proposed_edge_0_reverse",
                "proposed_edge_1_forward",
                "proposed_edge_1_reverse",
            ],
        )

    def test_route_four_follows_all_manual_proposal_segments(self):
        class MockNode:
            def __init__(self, node_id, coord):
                self.node_id = node_id
                self.coord = coord

            def getID(self):
                return self.node_id

            def getCoord(self):
                return self.coord

        class MockEdge:
            def __init__(self, edge_id, from_node, to_node, name="Ground"):
                self.edge_id = edge_id
                self.from_node = from_node
                self.to_node = to_node
                self.name = name

            def getID(self):
                return self.edge_id

            def getFromNode(self):
                return self.from_node

            def getToNode(self):
                return self.to_node

            def getName(self):
                return self.name

            def getShape(self):
                return [self.from_node.getCoord(), self.to_node.getCoord()]

            def allows(self, _vehicle_class):
                return True

        class MockNet:
            def __init__(self, edges):
                self.edges = edges

            def getEdges(self):
                return self.edges

            def convertLonLat2XY(self, lon, lat):
                return lon, lat

            def convertXY2LonLat(self, x, y):
                return x, y

            def convertLonLat2XY(self, lon, lat):
                return lon, lat

        nodes = {
            node_id: MockNode(node_id, coord)
            for node_id, coord in (
                ("start_node", (0.0, 0.0)),
                ("p0", (1.0, 0.0)),
                ("p1", (2.0, 1.0)),
                ("p2", (3.0, 0.0)),
                ("end_node", (4.0, 0.0)),
            )
        }
        start_edge = MockEdge("start", nodes["start_node"], nodes["p0"])
        end_edge = MockEdge("end", nodes["p2"], nodes["end_node"])
        forward_edges = [
            MockEdge(
                f"proposed_edge_{index}_forward",
                nodes[from_id],
                nodes[to_id],
                name="Proposed Infra",
            )
            for index, (from_id, to_id) in enumerate(
                (("p0", "p1"), ("p1", "p2"))
            )
        ]
        reverse_edges = [
            MockEdge(
                f"proposed_edge_{index}_reverse",
                nodes[to_id],
                nodes[from_id],
                name="Proposed Infra",
            )
            for index, (from_id, to_id) in enumerate(
                (("p0", "p1"), ("p1", "p2"))
            )
        ]
        net = MockNet([start_edge, end_edge, *forward_edges, *reverse_edges])

        route_edges, proposed_route = simulation_service._find_direct_proposed_route(
            net,
            start_edge,
            end_edge,
            [*forward_edges, *reverse_edges],
            {"lon": 0.0, "lat": 0.0},
            {"lon": 4.0, "lat": 0.0},
        )

        self.assertEqual(
            [edge.getID() for edge in proposed_route],
            ["proposed_edge_0_forward", "proposed_edge_1_forward"],
        )
        self.assertEqual(
            [edge.getID() for edge in route_edges],
            [
                "start",
                "proposed_edge_0_forward",
                "proposed_edge_1_forward",
                "end",
            ],
        )
        self.assertEqual(
            simulation_service._route_to_geojson(net, route_edges)["coordinates"],
            [[0.0, 0.0], [1.0, 0.0], [2.0, 1.0], [3.0, 0.0], [4.0, 0.0]],
        )
        anchored_geometry = simulation_service._route_to_geojson(
            net,
            route_edges,
            {"lon": 0.25, "lat": 0.0},
            {"lon": 3.75, "lat": 0.0},
        )
        self.assertEqual(
            anchored_geometry["coordinates"],
            [[0.25, 0.0], [1.0, 0.0], [2.0, 1.0], [3.0, 0.0], [3.75, 0.0]],
        )

    def test_visualizer_styles_collisions_as_strong_red(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            shared_dir = Path(tmp_dir)
            output_json = shared_dir / "phase34_output.json"
            collisions_geojson = shared_dir / "phase34_collisions.geojson"
            html_path = shared_dir / "phase34_map.html"

            collisions_geojson.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {
                                    "type": "Polygon",
                                    "coordinates": [[[77.55, 12.92], [77.551, 12.92], [77.551, 12.921], [77.55, 12.921], [77.55, 12.92]]],
                                },
                                "properties": {"id": 1},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output_json.write_text(
                json.dumps(
                    {
                        "input_summary": {"coordinates_lonlat": [[77.55, 12.92], [77.56, 12.93]]},
                        "feasibility": {"is_feasible": False, "collision_count": 1},
                        "smart_recommendation": None,
                        "artifacts": {"collisions_geojson": str(collisions_geojson)},
                    }
                ),
                encoding="utf-8",
            )

            visualizer = Phase34Visualizer(output_json_path=str(output_json), output_html_path=str(html_path))
            visualizer.generate_map()

            html = html_path.read_text(encoding="utf-8")
            self.assertIn('"fillColor": "#ff0000"', html)
            self.assertIn('"color": "#cc0000"', html)

    def test_find_via_point_route_creates_path_using_proposed_edge(self):
        class MockNode:
            def __init__(self, node_id):
                self.node_id = node_id
            def getID(self):
                return self.node_id
                
        class MockEdge:
            def __init__(self, edge_id, from_node, to_node):
                self.edge_id = edge_id
                self.from_node = MockNode(from_node)
                self.to_node = MockNode(to_node)
            def getID(self):
                return self.edge_id
            def getFromNode(self):
                return self.from_node
            def getToNode(self):
                return self.to_node
                
        class MockNet:
            def __init__(self, edges):
                self.edges = edges
            def getEdges(self):
                return self.edges
                
        start_edge = MockEdge("start", "n0", "n1")
        road_1 = MockEdge("road_1", "n1", "n2")
        via_edge = MockEdge("proposed_edge_0", "n2", "n3")
        road_2 = MockEdge("road_2", "n3", "n4")
        end_edge = MockEdge("end", "n4", "n5")
        
        net = MockNet([start_edge, road_1, via_edge, road_2, end_edge])
        
        with mock.patch("api.simulation_service.nx.shortest_path") as mock_shortest_path, \
             mock.patch("api.simulation_service._build_networkx_graph") as mock_build_graph:
             
             mock_graph = mock.MagicMock()
             # Mock the graph edge attributes
             mock_graph.__getitem__.side_effect = lambda u: {
                 "n1": {"n2": {"edge_id": "road_1"}},
                 "n3": {"n4": {"edge_id": "road_2"}},
             }[u]
             mock_build_graph.return_value = mock_graph
             
             mock_shortest_path.side_effect = [
                 ["n1", "n2"],
                 ["n3", "n4"],
             ]
             
             route_edges = simulation_service._find_via_point_route(net, start_edge, end_edge, via_edge)
             
             edge_ids = [e.getID() for e in route_edges]
             self.assertIn("proposed_edge_0", edge_ids)
             self.assertEqual(edge_ids, ["start", "road_1", "proposed_edge_0", "road_2", "end"])

    def test_determine_simulation_time_window(self):
        begin_t, end_t = simulation_service._determine_simulation_time_window("09:00", "10:00")
        self.assertEqual(begin_t, 32100)
        self.assertEqual(end_t, 36900)

        begin_t2, end_t2 = simulation_service._determine_simulation_time_window("09:00", "09:15")
        self.assertEqual(begin_t2, 32100)
        self.assertEqual(end_t2, 35100)

    def test_builds_edge_heatmap_from_before_and_after_fcd(self):
        class MockNode:
            def __init__(self, coord):
                self.coord = coord

            def getCoord(self):
                return self.coord

        class MockEdge:
            def __init__(self, edge_id, shape, speed=10.0):
                self.edge_id = edge_id
                self.shape = shape
                self.speed = speed
                self.node = MockNode(shape[0])
                self.target = MockNode(shape[-1])

            def getID(self):
                return self.edge_id

            def allows(self, _vehicle_class):
                return True

            def getShape(self):
                return self.shape

            def getSpeed(self):
                return self.speed

            def getName(self):
                return "Test Road"

            def getFromNode(self):
                return self.node

            def getToNode(self):
                return self.target

        class MockNet:
            def __init__(self, edges):
                self.edges = edges

            def getEdges(self):
                return self.edges

            def convertXY2LonLat(self, x, y):
                return x, y

        edge = MockEdge("edge_a", [(77.55, 12.92), (77.551, 12.921)])
        net = MockNet([edge])
        fcd_before = (
            '<fcd><timestep time="10"><vehicle id="v1" lane="edge_a_0" speed="5" />'
            '</timestep></fcd>'
        )
        fcd_after = (
            '<fcd><timestep time="10"><vehicle id="v1" lane="edge_a_0" speed="2.5" />'
            '</timestep></fcd>'
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            before_path = Path(tmp_dir) / "before.fcd.xml"
            after_path = Path(tmp_dir) / "after.fcd.xml"
            before_path.write_text(fcd_before, encoding="utf-8")
            after_path.write_text(fcd_after, encoding="utf-8")

            heatmap = simulation_service._build_simulation_heatmap(
                before_path,
                after_path,
                net,
                net,
                begin_time=0,
                end_time=20,
            )

        self.assertEqual(heatmap["before"]["observed_edge_count"], 1)
        self.assertEqual(heatmap["after"]["observed_edge_count"], 1)
        difference = heatmap["difference"]
        self.assertEqual(difference["summary"]["worsened_edge_count"], 1)
        self.assertAlmostEqual(
            difference["features"][0]["properties"]["delta_congestion"],
            0.25,
        )
        self.assertEqual(difference["hotspots"][0]["edge_id"], "edge_a")

    def test_route_graph_prefers_faster_higher_priority_arterial(self):
        class MockNode:
            def __init__(self, node_id):
                self.node_id = node_id

            def getID(self):
                return self.node_id

        class MockEdge:
            def __init__(self, edge_id, source, target, length, speed, priority):
                self.edge_id = edge_id
                self.source = MockNode(source)
                self.target = MockNode(target)
                self.length = length
                self.speed = speed
                self.priority = priority

            def getID(self):
                return self.edge_id

            def getFromNode(self):
                return self.source

            def getToNode(self):
                return self.target

            def getLength(self):
                return self.length

            def getSpeed(self):
                return self.speed

            def getPriority(self):
                return self.priority

            def allows(self, _vehicle_class):
                return True

        class MockNet:
            def __init__(self, edges):
                self.edges = edges

            def getEdges(self):
                return self.edges

        edges = [
            MockEdge("local_1", "s", "local", 100.0, 10.0, 3),
            MockEdge("local_2", "local", "d", 100.0, 10.0, 3),
            MockEdge("arterial_1", "s", "arterial", 130.0, 30.0, 13),
            MockEdge("arterial_2", "arterial", "d", 130.0, 30.0, 13),
        ]
        graph = simulation_service._build_networkx_graph(MockNet(edges))

        self.assertEqual(
            nx.shortest_path(graph, "s", "d", weight="routing_weight"),
            ["s", "arterial", "d"],
        )

    def test_route_selection_keeps_primary_routing_result_first(self):
        raw_routes = [
            list(range(20)),
            list(range(10)),
            list(range(30)),
        ]

        selected = simulation_service._select_routes_with_edge_span(
            raw_routes,
            route_count=3,
            min_edge_diff=7,
            max_edge_diff=20,
        )

        self.assertIs(selected[0], raw_routes[0])

    def test_adoption_rate_moves_only_selected_vehicles_to_route_four(self):
        vehicle_ids = [f"veh_{index:02d}" for index in range(8)]
        baseline_assignments = {
            vehicle_id: index % 3 for index, vehicle_id in enumerate(vehicle_ids)
        }

        for adoption_rate, expected_adopters in ((25, 2), (50, 4), (75, 6)):
            assignments, adopted_count = simulation_service._assign_intervention_routes(
                vehicle_ids,
                baseline_assignments,
                base_route_count=3,
                adoption_rate_pct=adoption_rate,
            )

            self.assertEqual(adopted_count, expected_adopters)
            self.assertEqual(
                sum(route_index == 3 for route_index in assignments.values()),
                expected_adopters,
            )
            for vehicle_id in vehicle_ids[expected_adopters:]:
                self.assertEqual(assignments[vehicle_id], baseline_assignments[vehicle_id])

    def test_batch_simulation_returns_all_requested_adoption_rates(self):
        def fake_simulation(**kwargs):
            rate = kwargs["adoption_rate_pct"]
            return {
                "status": "complete",
                "adoption_rate_pct": rate,
                "actual_adoption_rate_pct": rate,
                "routes": [],
            }

        with mock.patch.object(
            simulation_service,
            "run_corridor_intervention_simulation",
            side_effect=fake_simulation,
        ):
            batch = simulation_service.run_corridor_intervention_simulation_batch(
                origin={"lat": 12.92, "lon": 77.55},
                destination={"lat": 12.93, "lon": 77.56},
                demand_data={},
                adoption_rates_pct=[25, 50, 75],
            )

        self.assertEqual(batch["status"], "batch_complete")
        self.assertEqual(batch["adoption_rates_pct"], [25.0, 50.0, 75.0])
        self.assertEqual(
            [scenario["adoption_rate_pct"] for scenario in batch["scenarios"]],
            [25, 50, 75],
        )
        self.assertEqual(batch["adoption_rate_pct"], 75)

    def test_generates_same_endpoint_infrastructure_candidates(self):
        origin = {"lat": 12.9200, "lon": 77.5500}
        destination = {"lat": 12.9300, "lon": 77.5600}

        candidates = generate_same_endpoint_candidates(origin, destination)

        self.assertEqual(
            [candidate["infrastructure"]["type"] for candidate in candidates],
            ["road", "flyover", "tunnel"],
        )
        for candidate in candidates:
            self.assertEqual(candidate["geometry"]["coordinates"], [[77.55, 12.92], [77.56, 12.93]])
            validate_candidate_constraints(candidate)

    def test_generates_nearby_candidates_within_endpoint_shift_limit(self):
        candidates = generate_alternative_candidates(
            {"lat": 12.9200, "lon": 77.5500},
            {"lat": 12.9300, "lon": 77.5600},
            infrastructure_types=["flyover"],
            nearby_radii_m=(250.0, 500.0),
        )

        self.assertEqual(len(candidates), 5)
        self.assertEqual(candidates[0]["candidate_type"], "same_endpoints")
        self.assertEqual(
            {candidate["metadata"]["endpoint_shift_m"] for candidate in candidates[1:]},
            {250.0, 500.0},
        )
        for candidate in candidates:
            validate_candidate_constraints(candidate)

    def test_ranks_only_feasible_route_four_candidates_first(self):
        evaluations = [
            {
                "candidate": {"infrastructure": {"type": "tunnel"}},
                "feasibility": {"is_feasible": False, "collision_count": 2},
                "route_validation": {"valid": False},
            },
            {
                "candidate": {"infrastructure": {"type": "road"}},
                "feasibility": {"is_feasible": True, "collision_count": 0},
                "route_validation": {"valid": True, "route4_length_m": 1800},
                "simulation": {"time_reduction_pct": 4, "congestion_reduction_pct": 6},
            },
            {
                "candidate": {"infrastructure": {"type": "flyover"}},
                "feasibility": {"is_feasible": True, "collision_count": 0},
                "route_validation": {"valid": True, "route4_length_m": 1200},
                "simulation": {"time_reduction_pct": 12, "congestion_reduction_pct": 10},
            },
        ]

        ranked = rank_candidate_evaluations(evaluations)

        self.assertEqual(ranked[0]["candidate"]["infrastructure"]["type"], "flyover")
        self.assertTrue(ranked[0]["ranking_eligible"])
        self.assertFalse(ranked[-1]["ranking_eligible"])

    def test_candidate_evaluation_requires_feasibility_before_route_validation(self):
        candidate = generate_same_endpoint_candidates(
            {"lat": 12.9200, "lon": 77.5500},
            {"lat": 12.9300, "lon": 77.5600},
            ["road"],
        )[0]
        route_validator_called = False

        def feasibility_runner(_candidate):
            return {"feasibility": {"is_feasible": False, "collision_count": 1}}, None

        def route_validator(_candidate, _graph):
            nonlocal route_validator_called
            route_validator_called = True
            return {"status": "valid", "route4": {"length_m": 100}}

        evaluation = evaluate_candidate(candidate, feasibility_runner, route_validator)

        self.assertEqual(evaluation["status"], "infeasible")
        self.assertFalse(evaluation["route_validation"]["valid"])
        self.assertFalse(route_validator_called)

    def test_candidate_evaluation_accepts_feasible_route_four_without_running_sumo(self):
        candidate = generate_same_endpoint_candidates(
            {"lat": 12.9200, "lon": 77.5500},
            {"lat": 12.9300, "lon": 77.5600},
            ["flyover"],
        )[0]

        def feasibility_runner(_candidate):
            return {"feasibility": {"is_feasible": True, "collision_count": 0}}, object()

        def route_validator(_candidate, _graph):
            return {
                "status": "valid",
                "baseline_route_count": 3,
                "modified_route_count": 4,
                "route4": {"length_m": 1250.0},
                "routes": [{"id": 4, "length_m": 1250.0}],
            }

        evaluation = evaluate_candidate(candidate, feasibility_runner, route_validator)

        self.assertEqual(evaluation["status"], "feasible")
        self.assertTrue(evaluation["route_validation"]["valid"])
        self.assertEqual(evaluation["route_validation"]["route4_length_m"], 1250.0)
        self.assertEqual(evaluation["simulation"]["status"], "deferred")


if __name__ == "__main__":
    unittest.main()
