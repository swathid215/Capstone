import json
import os
import sys
from pathlib import Path

import folium

_backend_root = Path(__file__).resolve().parents[3]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))
from paths import SHARED_DIR


class Phase34Visualizer:
    def __init__(self, output_json_path=None, output_html_path=None):
        self.data_shared_dir = SHARED_DIR
        self.output_json_path = output_json_path or str(
            self.data_shared_dir / "phase34_output.json"
        )
        self.output_html_path = output_html_path or str(self.data_shared_dir / "phase34_map.html")

    def generate_map(self):
        if not os.path.exists(self.output_json_path):
            raise FileNotFoundError(f"Output JSON not found at {self.output_json_path}")

        with open(self.output_json_path, "r", encoding="utf-8") as file:
            payload = json.load(file)

        input_summary = payload.get("input_summary", {})
        feasibility = payload.get("feasibility", {})
        smart = payload.get("smart_recommendation")
        artifacts = payload.get("artifacts", {})

        coords = input_summary.get("coordinates_lonlat", [])
        if not coords:
            raise ValueError("No route coordinates found in output JSON.")

        map_center = [coords[0][1], coords[0][0]]
        phase_map = folium.Map(location=map_center, zoom_start=14, tiles="OpenStreetMap")

        route_points = [(lat, lon) for lon, lat in coords]
        route_color = "green" if feasibility.get("is_feasible") else "red"

        folium.PolyLine(
            locations=route_points,
            color=route_color,
            weight=5,
            opacity=0.8,
            tooltip=(
                f"{input_summary.get('infra_type', 'Route')} | "
                f"Feasible: {feasibility.get('is_feasible')} | "
                f"Collisions: {feasibility.get('collision_count', 0)}"
            ),
        ).add_to(phase_map)

        # Package widenings don't add edges, so draw their alignments directly.
        for item in payload.get("items", []):
            if item.get("infra_key") != "widening":
                continue
            folium.PolyLine(
                locations=[(lat, lon) for lon, lat in item.get("coordinates_lonlat", [])],
                color="orange",
                weight=7,
                opacity=0.8,
                dash_array="8 6",
                tooltip=(
                    f"{item.get('name', 'Widening')} | +{item.get('extra_lanes', 1)} lane(s) | "
                    f"Feasible: {item.get('is_feasible')}"
                ),
            ).add_to(phase_map)

        folium.Marker(
            location=route_points[0],
            tooltip="Start",
            icon=folium.Icon(color="blue", icon="play"),
        ).add_to(phase_map)

        folium.Marker(
            location=route_points[-1],
            tooltip="End",
            icon=folium.Icon(color="darkred", icon="stop"),
        ).add_to(phase_map)

        modified_roads_geojson = artifacts.get("modified_roads_geojson")
        if modified_roads_geojson and os.path.exists(modified_roads_geojson):
            with open(modified_roads_geojson, "r", encoding="utf-8") as file:
                modified_roads_data = json.load(file)

            folium.GeoJson(
                modified_roads_data,
                name="Modified Road",
                style_function=lambda _: {
                    "color": "#ff9800",
                    "weight": 6,
                    "opacity": 0.95,
                },
                tooltip=folium.GeoJsonTooltip(fields=["name", "highway", "length_m"]),
            ).add_to(phase_map)

        collisions_geojson = artifacts.get("collisions_geojson")
        if collisions_geojson and os.path.exists(collisions_geojson):
            with open(collisions_geojson, "r", encoding="utf-8") as file:
                collision_data = json.load(file)

            folium.GeoJson(
                collision_data,
                name="Collided Buildings",
                style_function=lambda feature: {
                    "fillColor": "#ff0000",
                    "color": "#cc0000",
                    "weight": 3,
                    "fillOpacity": 0.8,
                },
                highlight_function=lambda feature: {
                    "fillColor": "#ff4d4d",
                    "color": "#990000",
                    "weight": 4,
                    "fillOpacity": 0.95,
                },
            ).add_to(phase_map)

        summary_html = [
            "<div style=\"position: fixed; top: 12px; right: 12px; z-index: 9999; ",
            "background: white; padding: 12px 14px; border: 1px solid #ccc; ",
            "border-radius: 8px; width: 300px; font-family: sans-serif; font-size: 13px;\">",
            "<b>Phase 3/4 Summary</b><br>",
            f"Place: {input_summary.get('place_name', 'Unknown')}<br>",
            f"Type: {input_summary.get('infra_type', 'Unknown')}<br>",
            f"Feasible: {feasibility.get('is_feasible')}<br>",
            f"Collisions: {feasibility.get('collision_count', 0)}<br>",
        ]

        if smart:
            summary_html.extend(
                [
                    f"Recommendation: {smart.get('recommendation', 'N/A')}<br>",
                    f"Confidence: {smart.get('confidence', 'N/A')}<br>",
                ]
            )

        summary_html.append("</div>")
        phase_map.get_root().html.add_child(folium.Element("".join(summary_html)))

        phase_map.save(self.output_html_path)
        print(f"Success! Phase 3/4 map generated at: {self.output_html_path}")
        return self.output_html_path


if __name__ == "__main__":
    try:
        output_arg = sys.argv[1] if len(sys.argv) > 1 else None
        visualizer = Phase34Visualizer(output_json_path=output_arg)
        html_path = visualizer.generate_map()
        print(f"Visualization ready at: {html_path}")
    except Exception as exc:
        print(f"Visualizer failed: {exc}")
        sys.exit(1)
