import json
import sys
from pathlib import Path

import folium
from shapely.geometry import shape

_backend_root = Path(__file__).resolve().parents[2]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))
from paths import SHARED_DIR


def get_ward_data(geojson_path):
    """Map movement IDs (and ward numbers) to centroid coords and names."""
    if not Path(geojson_path).exists():
        return {}

    with open(geojson_path, "r", encoding="utf-8") as file:
        gdf_data = json.load(file)

    lookup = {}
    for feature in gdf_data["features"]:
        props = feature["properties"]
        ward_no = props.get("WARD_NO")
        movement_id = props.get("MOVEMENT_ID")
        ward_name = props.get("WARD_NAME", "Unknown")

        if ward_no is None:
            continue

        try:
            poly = shape(feature["geometry"])
            center = poly.centroid
            entry = {"coords": [center.y, center.x], "name": ward_name}
            lookup[int(ward_no)] = entry
            if movement_id is not None:
                lookup[int(movement_id)] = entry
        except Exception:
            continue

    return lookup


def generate_map():
    base_dir = Path(__file__).resolve().parent
    input_json = SHARED_DIR / "integration_demand.json"
    geojson_path = base_dir / "bangalore_wards.json"
    output_html = SHARED_DIR / "traffic_map.html"

    ward_lookup = get_ward_data(geojson_path)

    if not input_json.exists():
        print(f"Demand JSON not found at {input_json}")
        return None

    with open(input_json, "r", encoding="utf-8") as file:
        demand_data = json.load(file)

    matrix = demand_data.get("demand_matrix", [])
    if not matrix:
        return None

    start_id = int(matrix[0]["sourceid"])
    map_center = ward_lookup.get(start_id, {"coords": [12.9716, 77.5946]})["coords"]
    m = folium.Map(location=map_center, zoom_start=12, tiles="OpenStreetMap")

    for trip in matrix:
        src, dst = int(trip["sourceid"]), int(trip["dstid"])
        count = float(trip["synthetic_trip_count"])
        src_name = trip.get("sourcename") or ward_lookup.get(src, {}).get("name", "Unknown")
        dst_name = trip.get("dstname") or ward_lookup.get(dst, {}).get("name", "Unknown")

        if src in ward_lookup and dst in ward_lookup:
            folium.PolyLine(
                locations=[ward_lookup[src]["coords"], ward_lookup[dst]["coords"]],
                weight=max(2, count / 5),
                color="#00FFCC",
                opacity=0.6,
                tooltip=f"<b>From:</b> {src_name}<br><b>To:</b> {dst_name}<br><b>Trips:</b> {count:.1f}",
            ).add_to(m)

            folium.Marker(
                location=ward_lookup[src]["coords"],
                icon=folium.DivIcon(
                    html=f'<div style="font-family:sans-serif;color:white;font-size:10px;width:100px;">{src_name}</div>'
                ),
            ).add_to(m)

    m.save(str(output_html))
    print(f"Success! Demand map generated at: {output_html}")
    return str(output_html)


if __name__ == "__main__":
    generate_map()
