"""Generate a summary of available TomTom data coverage and time slots."""
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))

from api.available_hours_service import get_available_hours_for_corridor

# TomTom coverage area
TOMTOM_CENTER_LAT = 12.924135
TOMTOM_CENTER_LON = 77.55614
TOMTOM_RADIUS_KM = 2.0

# Test with coordinates in the TomTom area
result = get_available_hours_for_corridor(
    start_lat=TOMTOM_CENTER_LAT - 0.01,
    start_lon=TOMTOM_CENTER_LON - 0.01,
    end_lat=TOMTOM_CENTER_LAT + 0.01,
    end_lon=TOMTOM_CENTER_LON + 0.01,
)

summary = {
    "tomtom_coverage": {
        "center_latitude": TOMTOM_CENTER_LAT,
        "center_longitude": TOMTOM_CENTER_LON,
        "search_radius_km": TOMTOM_RADIUS_KM,
        "location": "Kathriguppe, Bangalore, India",
        "note": "Only corridors within this area will show TomTom traffic data"
    },
    "available_data": {
        "data_available": result["in_tomtom_area"],
        "total_hours": len(result["hours"]),
        "message": result["message"]
    },
    "time_slots": [
        {
            "hour": h["hour"],
            "hour_label": h["hour_label"],
            "congestion_level": h["congestion_level"],
            "mean_speed_kmh": h["mean_speed_kmh"],
            "mean_travel_time_s": h["mean_travel_time_s"],
            "speed_ratio": h["speed_ratio"],
            "sample_count": h["sample_count"],
            "dates_available": h["dates_available"],
        }
        for h in result["hours"]
    ],
    "instructions": {
        "step_1": "Select a corridor within the TomTom coverage area (see coordinates above)",
        "step_2": "Choose a time slot from the list below",
        "step_3": "Simulation will run with demand calibrated to observed traffic for that hour",
        "example_coordinates": {
            "start": {"lat": 12.914, "lon": 77.546, "description": "Kathriguppe area - Southwest"},
            "end": {"lat": 12.934, "lon": 77.567, "description": "Kathriguppe area - Northeast"},
        },
    }
}

# Write summary
output_file = Path(__file__).parent / "tomtom_data_summary.json"
output_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(f"✓ Summary written to: {output_file}")
print(f"\nTomTom Coverage Area:")
print(f"  Center: {TOMTOM_CENTER_LAT}, {TOMTOM_CENTER_LON}")
print(f"  Radius: {TOMTOM_RADIUS_KM} km")
print(f"  Available hours: {len(result['hours'])}")
print(f"\nTime Slots (Peak Hours):")
for h in result["hours"]:
    if h["sample_count"] > 0:
        print(f"  {h['hour_label']}: {h['congestion_level']:10s} - Speed: {h['mean_speed_kmh']:5.1f} km/h - Samples: {h['sample_count']}")
