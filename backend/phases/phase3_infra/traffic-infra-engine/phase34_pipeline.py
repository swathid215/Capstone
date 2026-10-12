import datetime
import json
import os
import sys
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, mapping

_backend_root = Path(__file__).resolve().parents[3]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))
from paths import SHARED_DIR

from api.simulation_service import _resolve_after_net_path
from feasibility_engine.feasibility import FeasibilityEngine
from graph_modification.modify_graph import GraphModificationEngine
from smart_suggestion_engine import SmartArchitectEngine
from utils.osm_loader import load_buildings, load_road_network


# Assumed width of the existing road when checking clearance for a widening.
EXISTING_CARRIAGEWAY_M = 7.0


class Phase34Pipeline:
    def __init__(self, input_json_path=None):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_shared_dir = SHARED_DIR
        self.data_shared_dir.mkdir(parents=True, exist_ok=True)
        self.input_json_path = input_json_path or str(
            self.data_shared_dir / "phase34_input.json"
        )
        self.original_graph = None
        self.modified_graph = None

    def _load_input(self):
        if not os.path.exists(self.input_json_path):
            raise FileNotFoundError(f"Input JSON not found at {self.input_json_path}")

        with open(self.input_json_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _project_coords(self, lonlat_coords, target_crs):
        projected_coords = []
        for lon, lat in lonlat_coords:
            point_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
            point_proj = point_gdf.to_crs(target_crs)
            projected_coords.append(
                (point_proj.geometry.iloc[0].x, point_proj.geometry.iloc[0].y)
            )
        return projected_coords

    def _build_geometry(self, projected_coords, geometry_type):
        if geometry_type == "Polygon":
            return Polygon(projected_coords)
        return LineString(projected_coords)

    def _sanitize_graph_value(self, value):
        if value is None:
            return ""

        if isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, dict):
            return json.dumps(value, default=str, sort_keys=True)

        if isinstance(value, (list, tuple, set)):
            return json.dumps(list(value), default=str)

        if hasattr(value, "wkt"):
            return value.wkt

        return str(value)

    def _prepare_graph_for_export(self, graph):
        export_graph = graph.copy()

        for key, value in list(export_graph.graph.items()):
            export_graph.graph[key] = self._sanitize_graph_value(value)

        if export_graph.is_multigraph():
            edge_iter = export_graph.edges(data=True, keys=True)
            for _, _, _, data in edge_iter:
                for attr, value in list(data.items()):
                    data[attr] = self._sanitize_graph_value(value)
        else:
            edge_iter = export_graph.edges(data=True)
            for _, _, data in edge_iter:
                for attr, value in list(data.items()):
                    data[attr] = self._sanitize_graph_value(value)

        for _, data in export_graph.nodes(data=True):
            for attr, value in list(data.items()):
                data[attr] = self._sanitize_graph_value(value)

        return export_graph

    def _write_geojson(self, geometry, output_path, properties=None):
        feature = {
            "type": "Feature",
            "geometry": mapping(geometry),
            "properties": properties or {},
        }
        collection = {"type": "FeatureCollection", "features": [feature]}

        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(collection, file, indent=2)

    def _write_modified_roads_geojson(self, graph, output_path):
        features = []
        transformer = None
        graph_crs = graph.graph.get("crs")
        if graph_crs:
            try:
                transformer = Transformer.from_crs(
                    graph_crs,
                    "EPSG:4326",
                    always_xy=True,
                )
            except Exception:
                transformer = None

        for source, target, data in graph.edges(data=True):
            if (
                data.get("highway") not in ("proposed_road", "proposed_flyover", "proposed_tunnel")
                and data.get("name") not in ("Proposed Infra", "AI Flyover", "AI Tunnel")
            ):
                continue
            if data.get("proposal_direction") == "reverse":
                continue

            node_u = graph.nodes.get(source, {})
            node_v = graph.nodes.get(target, {})
            coords = []
            for node in (node_u, node_v):
                x = node.get("x")
                y = node.get("y")
                if x is None and node.get("lon") is not None:
                    x = node.get("lon")
                if y is None and node.get("lat") is not None:
                    y = node.get("lat")
                if x is None or y is None:
                    continue
                if transformer:
                    lon, lat = transformer.transform(float(x), float(y))
                    coords.append((float(lon), float(lat)))
                else:
                    coords.append((float(x), float(y)))

            if len(coords) != 2:
                continue

            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    },
                    "properties": {
                        "name": data.get("name", "Proposed Infra"),
                        "highway": data.get("highway", "proposed_road"),
                        "length_m": data.get("length"),
                        "infra_type": data.get("infra_type", "New Road"),
                        "is_flyover": bool(data.get("is_flyover", False)),
                        "is_tunnel": bool(data.get("is_tunnel", False)),
                        "elevation_m": data.get("flyover_height_m", 0.0),
                    },
                }
            )

        collection = {"type": "FeatureCollection", "features": features}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(collection, file, indent=2)

        return str(output_path)

    def run_from_dict(self, payload: dict) -> dict:
        # A package holds several items built together (main structure first,
        # then connectors and widenings); a single-item payload is the same
        # flow with one proposal.
        proposals = [p for p in (payload.get("proposals") or []) if p] or [
            payload.get("proposal", {})
        ]
        proposal = proposals[0]
        scenario_metadata = payload.get("scenario_metadata", {})

        place_name = proposal.get("place_name", "Kathriguppe, Bangalore, India")
        point = proposal.get("point")
        dist_m = int(proposal.get("dist_m", 5000))
        infra_type = proposal.get("infra_type", "New Road")
        road_width_m = float(proposal.get("road_width_m", 8))
        is_oneway = bool(proposal.get("is_oneway", False))
        geometry_type = proposal.get("geometry_type", "LineString")
        smart_analysis = bool(proposal.get("smart_analysis", True))
        lonlat_coords = proposal.get("coordinates", [])

        for item in proposals:
            if len(item.get("coordinates", [])) < 2:
                raise ValueError(
                    f"{item.get('name', 'Infrastructure item')} needs at least two [lon, lat] coordinates."
                )

        print(f"[Phase34 Pipeline] Loading base data for {place_name}...")
        graph = load_road_network(place_name=place_name, point=point, dist=dist_m)
        self.original_graph = graph
        self.modified_graph = None
        buildings = load_buildings(place_name=place_name, point=point, dist=dist_m)

        feasibility_engine = FeasibilityEngine(buildings)
        item_results = []
        collision_frames = []
        for index, item in enumerate(proposals):
            item_coords = self._project_coords(item["coordinates"], buildings.crs)
            item_is_widening = item.get("infra_key") == "widening"
            item_width = float(item.get("road_width_m", 8))
            # Widening clears the existing carriageway plus the added width.
            check_width = (
                EXISTING_CARRIAGEWAY_M + item_width if item_is_widening else item_width
            )
            item_feasible, item_collisions = feasibility_engine.check_feasibility(
                item_coords, road_width_meters=check_width
            )
            item_collision_count = (
                0 if item_collisions is None else int(len(item_collisions))
            )
            if item_collisions is not None and not item_collisions.empty:
                collision_frames.append(item_collisions)
            item_geom = self._build_geometry(
                item_coords, item.get("geometry_type", "LineString")
            )
            item_results.append(
                {
                    "index": index,
                    "item_id": item.get("item_id", ""),
                    "name": item.get("name") or item.get("infra_type", "Infra"),
                    "infra_type": item.get("infra_type", "New Road"),
                    "infra_key": item.get("infra_key", ""),
                    "role": item.get("role", "main" if index == 0 else "connector"),
                    "road_width_m": item_width,
                    "extra_lanes": int(item.get("extra_lanes") or 0),
                    "is_oneway": bool(item.get("is_oneway", False)),
                    "flyover_height_m": float(item.get("flyover_height_m", 5.0)),
                    "length_m": round(float(item_geom.length), 1),
                    "is_feasible": bool(item_feasible),
                    "collision_count": item_collision_count,
                    "coordinates_lonlat": item["coordinates"],
                    "projected_coords": item_coords,
                }
            )

        projected_coords = item_results[0]["projected_coords"]
        proposal_geom = self._build_geometry(projected_coords, geometry_type)
        is_feasible = all(item["is_feasible"] for item in item_results)
        collision_count = sum(item["collision_count"] for item in item_results)
        collisions = None
        if collision_frames:
            collisions = gpd.GeoDataFrame(
                pd.concat(collision_frames, ignore_index=True),
                crs=collision_frames[0].crs,
            )
        prepared_proposals = [
            {
                "coordinates": item["coordinates_lonlat"],
                "projected_coords": item["projected_coords"],
                "geometry_type": proposals[item["index"]].get("geometry_type", "LineString"),
                "infra_type": item["infra_type"],
                "road_width_m": item["road_width_m"],
                "is_oneway": item["is_oneway"],
                "flyover_height_m": item["flyover_height_m"],
            }
            for item in item_results
        ]
        projected_coords = item_results[0]["projected_coords"]
        proposal_geom = self._build_geometry(projected_coords, geometry_type)

        graph_output_path = self.data_shared_dir / "phase34_modified_network.graphml"
        route_geojson_path = self.data_shared_dir / "phase34_route.geojson"
        modified_roads_geojson_path = self.data_shared_dir / "phase34_modified_roads.geojson"
        collisions_geojson_path = self.data_shared_dir / "phase34_collisions.geojson"
        report_md_path = self.data_shared_dir / "phase34_impact_report.md"
        output_json_path = self.data_shared_dir / "phase34_output.json"

        # Feasibility uses projected coordinates, but the public artifact is
        # consumed by web maps and must remain in WGS84 lon/lat coordinates.
        # The main structure is always the first feature.
        route_features = []
        for item_result in item_results:
            route_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(
                        self._build_geometry(
                            item_result["coordinates_lonlat"],
                            proposals[item_result["index"]].get("geometry_type", "LineString"),
                        )
                    ),
                    "properties": {
                        "name": item_result["name"],
                        "role": item_result["role"],
                        "infra_type": item_result["infra_type"],
                        "road_width_m": item_result["road_width_m"],
                        "extra_lanes": item_result["extra_lanes"],
                        "is_oneway": item_result["is_oneway"],
                        "is_feasible": item_result["is_feasible"],
                        "flyover_height_m": item_result["flyover_height_m"]
                        if str(item_result["infra_type"]).strip().lower() == "flyover"
                        else 0.0,
                    },
                }
            )
        with open(route_geojson_path, "w", encoding="utf-8") as file:
            json.dump({"type": "FeatureCollection", "features": route_features}, file, indent=2)

        if collisions is not None and not collisions.empty:
            collisions_geojson = collisions
            if collisions_geojson.crs:
                collisions_geojson = collisions_geojson.to_crs("EPSG:4326")
            collisions_geojson.to_file(
                collisions_geojson_path, driver="GeoJSON", engine="fiona"
            )
        elif collisions_geojson_path.exists():
            collisions_geojson_path.unlink()

        # Build the proposed network even when feasibility fails. Feasibility is
        # reported separately, while the generated network is used for a
        # clearly-labelled simulation preview of the selected proposal.
        impact_report = None
        modified_net_path = None
        modification_engine = GraphModificationEngine(graph)
        proposal_segment_offset = 0
        for index, (prepared, item_result) in enumerate(zip(prepared_proposals, item_results)):
            if item_result["infra_key"] == "road_block":
                modification_engine.add_road_block(
                    item_result["projected_coords"],
                    name=item_result["name"],
                )
                continue
            if item_result["infra_key"] == "widening":
                modification_engine.add_widening(
                    item_result["projected_coords"],
                    extra_lanes=item_result["extra_lanes"],
                    name=item_result["name"],
                )
                continue
            modification_engine.add_infrastructure(
                prepared["projected_coords"],
                is_oneway=prepared["is_oneway"],
                infra_type=prepared["infra_type"],
                flyover_height_m=prepared["flyover_height_m"],
                proposal_segment_offset=proposal_segment_offset,
                proposal_index=index,
            )
            proposal_segment_offset += max(1, len(prepared["projected_coords"]) - 1)
        self.modified_graph = modification_engine.G
        impact_report = modification_engine.calculate_impact()

        export_graph = self._prepare_graph_for_export(modification_engine.G)
        nx.write_graphml(export_graph, graph_output_path)
        self._write_modified_roads_geojson(modification_engine.G, modified_roads_geojson_path)
        modified_net_path = _resolve_after_net_path(modification_engine.G, True)

        public_items = [
            {key: value for key, value in item.items() if key != "projected_coords"}
            for item in item_results
        ]

        smart_recommendation = None
        if smart_analysis:
            api_key = os.getenv("GEMINI_API_KEY")
            smart_architect = SmartArchitectEngine(api_key=api_key)

            new_connections = 0
            if impact_report:
                new_connections = int(impact_report.get("New Connections Spliced", 0))

            smart_recommendation = smart_architect.analyze_infrastructure(
                geometry_type=geometry_type,
                length=proposal_geom.length,
                collisions=collision_count,
                new_connections=new_connections,
                collision_df=collisions,
                network_graph=graph,
                buildings_gdf=buildings,
                coordinates=projected_coords,
            )

        output_payload = {
            "scenario_metadata": {
                "name": scenario_metadata.get("name", "Phase 3/4 Standalone Run"),
                "description": scenario_metadata.get(
                    "description",
                    "Standalone feasibility and graph-modification analysis for n8n.",
                ),
                "created_at": datetime.datetime.now().isoformat(),
            },
            "input_summary": {
                "place_name": place_name,
                "point": point,
                "dist_m": dist_m,
                "infra_type": infra_type,
                "road_width_m": road_width_m,
                "is_oneway": is_oneway,
                "geometry_type": geometry_type,
                "coordinate_count": len(lonlat_coords),
                "coordinates_lonlat": lonlat_coords,
                "intervention_count": len(prepared_proposals),
                "interventions": [
                    {
                        "infra_type": item["infra_type"],
                        "road_width_m": item["road_width_m"],
                        "is_oneway": item["is_oneway"],
                        "coordinate_count": len(item["coordinates"]),
                    }
                    for item in prepared_proposals
                ],
            },
            "items": public_items,
            "feasibility": {
                "is_feasible": is_feasible,
                "collision_count": collision_count,
                "items": [
                    {
                        "name": item["name"],
                        "role": item["role"],
                        "infra_type": item["infra_type"],
                        "is_feasible": item["is_feasible"],
                        "collision_count": item["collision_count"],
                    }
                    for item in public_items
                ],
            },
            "impact_report": impact_report,
            "smart_recommendation": smart_recommendation,
            "artifacts": {
                "route_geojson": str(route_geojson_path),
                "modified_roads_geojson": str(modified_roads_geojson_path) if impact_report else None,
                "collisions_geojson": str(collisions_geojson_path) if collision_count else None,
                "graphml": str(graph_output_path) if impact_report else None,
                "modified_network_netxml": str(modified_net_path) if modified_net_path else None,
                "report_markdown": str(report_md_path),
            },
        }

        report_lines = [
            "# Phase 3/4 Impact Report",
            f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Place: {place_name}",
            f"Type: {infra_type}",
            f"Geometry: {geometry_type}",
            f"Road Width: {road_width_m} m",
            f"One Way: {'Yes' if is_oneway else 'No'}",
            "",
            "## Feasibility",
            f"- Status: {'Approved' if is_feasible else 'Failed'}",
            f"- Building Collisions: {collision_count}",
        ]

        if len(public_items) > 1:
            report_lines.extend(["", "## Package Items"])
            for item in public_items:
                detail = (
                    f"+{item['extra_lanes']} lane(s)"
                    if item["infra_key"] == "widening"
                    else f"{item['road_width_m']} m wide"
                )
                report_lines.append(
                    f"- {item['name']} ({item['role']}, {item['infra_type']}, "
                    f"{item['length_m']} m, {detail}): "
                    f"{'Approved' if item['is_feasible'] else 'Failed'}, "
                    f"{item['collision_count']} collision(s)"
                )

        if impact_report:
            report_lines.extend(
                [
                    "",
                    "## Graph Impact",
                    f"- Original Network Edges: {impact_report.get('Original Network Edges', 0)}",
                    f"- Updated Network Edges: {impact_report.get('Updated Network Edges', 0)}",
                    f"- New Connections Spliced: {impact_report.get('New Connections Spliced', 0)}",
                ]
            )

        if smart_recommendation:
            report_lines.extend(
                [
                    "",
                    "## Smart Recommendation",
                    f"- Recommendation: {smart_recommendation.get('recommendation')}",
                    f"- Confidence: {smart_recommendation.get('confidence')}",
                    f"- Cost Estimate: {smart_recommendation.get('cost_estimate')}",
                    f"- Implementation Time: {smart_recommendation.get('implementation_time')}",
                    f"- Environmental Impact: {smart_recommendation.get('environmental_impact')}",
                    "",
                    smart_recommendation.get("reasoning", ""),
                ]
            )

        with open(report_md_path, "w", encoding="utf-8") as file:
            file.write("\n".join(report_lines) + "\n")

        with open(output_json_path, "w", encoding="utf-8") as file:
            json.dump(output_payload, file, indent=4)

        print(f"[Phase34 Pipeline] Output saved to: {output_json_path}")
        return output_payload

    def run(self):
        payload = self._load_input()
        self.run_from_dict(payload)
        return str(self.data_shared_dir / "phase34_output.json")


if __name__ == "__main__":
    try:
        input_arg = sys.argv[1] if len(sys.argv) > 1 else None
        pipeline = Phase34Pipeline(input_json_path=input_arg)
        saved_file = pipeline.run()
        print(f"Success! Phase 3/4 output saved at: {saved_file}")
    except Exception as exc:
        print(f"Pipeline failed: {exc}")
        sys.exit(1)
