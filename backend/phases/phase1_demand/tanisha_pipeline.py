import json
import os
import sys
from pathlib import Path

import pandas as pd

_backend_root = Path(__file__).resolve().parents[2]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))
from paths import SHARED_DIR


class TanishaTrafficPipeline:
    def __init__(self, uber_csv):
        self.uber_csv = uber_csv
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.geojson_path = os.path.join(self.base_dir, 'bangalore_wards.json')

    def get_ward_lookups(self):
        """Build movement-id and ward-number lookups from the GeoJSON file."""
        movement_to_ward = {}
        ward_to_movement = {}

        if not os.path.exists(self.geojson_path):
            return movement_to_ward, ward_to_movement

        try:
            with open(self.geojson_path, "r", encoding="utf-8") as file:
                geo_data = json.load(file)
            for feature in geo_data["features"]:
                props = feature["properties"]
                ward_no = props.get("WARD_NO")
                movement_id = props.get("MOVEMENT_ID")
                if ward_no is None or movement_id is None:
                    continue
                ward_no = int(ward_no)
                movement_id = int(movement_id)
                ward_name = props.get("WARD_NAME", "Unknown")
                movement_to_ward[movement_id] = {
                    "ward_no": ward_no,
                    "ward_name": ward_name,
                }
                ward_to_movement[ward_no] = movement_id
        except Exception as exc:
            print(f"Warning: Could not parse GeoJSON for ward lookups: {exc}")

        return movement_to_ward, ward_to_movement

    def generate_demand(self, congestion_multiplier=1.0, target_hour=9, ward_no=163):
        if not os.path.exists(self.uber_csv):
            print(f"Error: CSV not found at {self.uber_csv}")
            return None

        movement_to_ward, ward_to_movement = self.get_ward_lookups()
        target_movement_id = ward_to_movement.get(int(ward_no))
        if target_movement_id is None:
            print(f"Error: Ward {ward_no} not found in GeoJSON.")
            return None

        df = pd.read_csv(self.uber_csv)
        rush_hour = df[df["hod"] == target_hour].copy()

        corridor_filter = (rush_hour["sourceid"] == target_movement_id) | (
            rush_hour["dstid"] == target_movement_id
        )
        rush_hour = rush_hour[corridor_filter]

        if rush_hour.empty:
            print(
                f"Warning: No data found for ward {ward_no} "
                f"(movement id {target_movement_id})."
            )
            return None

        rush_hour['mean_travel_min'] = rush_hour['mean_travel_time'] / 60
        
        od_matrix = rush_hour.groupby(['sourceid', 'dstid']).agg({
            'mean_travel_min': 'mean',
            'standard_deviation_travel_time': 'mean'
        }).reset_index()

        od_matrix['synthetic_trip_count'] = (100 / od_matrix['mean_travel_min']) * congestion_multiplier
        hotspots = od_matrix.sort_values(by='synthetic_trip_count', ascending=False).head(10)

        final_matrix = []
        for _, row in hotspots.iterrows():
            source_movement_id = int(row["sourceid"])
            dest_movement_id = int(row["dstid"])
            source_ward = movement_to_ward.get(source_movement_id, {})
            dest_ward = movement_to_ward.get(dest_movement_id, {})
            final_matrix.append({
                "sourceid": source_movement_id,
                "source_ward_no": source_ward.get("ward_no"),
                "sourcename": source_ward.get("ward_name", "Unknown Ward"),
                "dstid": dest_movement_id,
                "dst_ward_no": dest_ward.get("ward_no"),
                "dstname": dest_ward.get("ward_name", "Unknown Ward"),
                "synthetic_trip_count": round(float(row["synthetic_trip_count"]), 2),
                "travel_time_min": round(float(row["mean_travel_min"]), 2),
            })

        ward_label = movement_to_ward.get(target_movement_id, {}).get("ward_name", f"Ward {ward_no}")
        output = {
            "scenario_metadata": {
                "multiplier": congestion_multiplier,
                "ward_no": int(ward_no),
                "movement_id": target_movement_id,
                "description": f"{ward_label} Corridor - {'Heavy' if congestion_multiplier > 1.2 else 'Normal'}",
                "target_hour": target_hour,
            },
            "demand_matrix": final_matrix
        }

        output_path = SHARED_DIR / "integration_demand.json"
        SHARED_DIR.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=4)

        return str(output_path)

if __name__ == "__main__":
    try:
        mult = float(sys.argv[1])
    except (IndexError, ValueError):
        mult = 1.0

    base_dir = Path(__file__).resolve().parent
    csv_file = base_dir / "bangalore-wards-2019-3-OnlyWeekdays-HourlyAggregate.csv"

    pipeline = TanishaTrafficPipeline(str(csv_file))
    saved_file = pipeline.generate_demand(congestion_multiplier=mult)

    if saved_file:
        print(f"Success! Output saved to {saved_file}")