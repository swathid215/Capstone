#!/usr/bin/env python3
"""Heatmap validation helper — compares FCD-derived congestion to tripinfo."""
import sys
from pathlib import Path
import tempfile
from pprint import pprint

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from api import simulation_service


def write_text(path: Path, text: str):
    path.write_text(text, encoding="utf-8")


def run_demo_validation():
    # Use the same tiny example as the unit test to demonstrate the check
    fcd_before = '<fcd><timestep time="10"><vehicle id="v1" lane="edge_a_0" speed="5" />'
    fcd_before += '</timestep></fcd>'
    fcd_after = '<fcd><timestep time="10"><vehicle id="v1" lane="edge_a_0" speed="2.5" />'
    fcd_after += '</timestep></fcd>'

    # Build a minimal mock net by reusing the test's mocking approach inside this script
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

    with tempfile.TemporaryDirectory() as tmp:
        before_path = Path(tmp) / "before.fcd.xml"
        after_path = Path(tmp) / "after.fcd.xml"
        write_text(before_path, fcd_before)
        write_text(after_path, fcd_after)

        heatmap = simulation_service._build_simulation_heatmap(
            before_path,
            after_path,
            net,
            net,
            begin_time=0,
            end_time=20,
        )

        print("Heatmap summary (difference):")
        pprint(heatmap["difference"]["summary"])

        # Create synthetic tripinfo files matching the FCD congestion
        tripinfo_before = (
            '<tripinfos>'
            '<tripinfo id="v1" duration="100" waitingTime="50" routeLength="1000" arrival="200" />'
            '</tripinfos>'
        )
        tripinfo_after = (
            '<tripinfos>'
            '<tripinfo id="v1" duration="100" waitingTime="75" routeLength="1000" arrival="200" />'
            '</tripinfos>'
        )

        before_trip_path = Path(tmp) / "tripinfo_before.xml"
        after_trip_path = Path(tmp) / "tripinfo_after.xml"
        write_text(before_trip_path, tripinfo_before)
        write_text(after_trip_path, tripinfo_after)

        trip_before = simulation_service._parse_tripinfo(before_trip_path)
        trip_after = simulation_service._parse_tripinfo(after_trip_path)

        print("Tripinfo metrics before:")
        pprint(trip_before)
        print("Tripinfo metrics after:")
        pprint(trip_after)

        # Compare FCD-derived average congestion (weighted) to tripinfo avg_congestion_ratio
        diff = heatmap["difference"]
        features = diff.get("features", [])
        # weighted average delta_congestion by sample_count if available
        total_samples = sum(f.get("properties", {}).get("sample_count", 1) for f in features) or 1
        weighted_delta = sum(
            f.get("properties", {}).get("delta_congestion", 0) * f.get("properties", {}).get("sample_count", 1)
            for f in features
        ) / total_samples

        print(f"Weighted mean delta_congestion (FCD): {weighted_delta:.4f}")
        print(f"Tripinfo congestion before: {trip_before.get('avg_congestion_ratio')}")
        print(f"Tripinfo congestion after: {trip_after.get('avg_congestion_ratio')}")


if __name__ == "__main__":
    run_demo_validation()
