import geopandas as gpd
from shapely.geometry import Point, LineString
import osmnx as ox
import traceback

def test():
    try:
        print("Loading buildings...")
        place_name = "Malleshwaram, Bangalore, India"
        buildings = ox.features_from_place(place_name, {'building': True})
        buildings_proj = buildings.to_crs(buildings.estimate_utm_crs())
        target_crs = buildings_proj.crs
        print("Loaded buildings. CRS:", target_crs)

        # Let's take a line we KNOW is in Malleshwaram.
        # Pick 2 building centroids.
        b1 = buildings.iloc[0].geometry.centroid
        b2 = buildings.iloc[10].geometry.centroid
        
        lon1, lat1 = b1.x, b1.y # EPSG:4326 originally
        lon2, lat2 = b2.x, b2.y
        
        print(f"Test Points (lon, lat): ({lon1}, {lat1}) to ({lon2}, {lat2})")

        # Scenario A: Correctly passing [lon, lat]
        coords_A = [[lon1, lat1], [lon2, lat2]]
        
        # Scenario B: Incorrectly passing [lat, lon]
        coords_B = [[lat1, lon1], [lat2, lon2]]

        def check_coords(name, coords):
            projected = []
            for pt in coords:
                lon, lat = pt
                # Simulate app.py logic
                pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
                pt_gdf_proj = pt_gdf.to_crs(target_crs)
                proj_x = pt_gdf_proj.geometry.iloc[0].x
                proj_y = pt_gdf_proj.geometry.iloc[0].y
                projected.append((proj_x, proj_y))
            
            # Simulate feasibility.py
            proposed_road = LineString(projected)
            road_buffer = proposed_road.buffer(4.0)
            possible_indexes = list(buildings_proj.sindex.intersection(road_buffer.bounds))
            possible = buildings_proj.iloc[possible_indexes]
            precise = possible[possible.intersects(road_buffer)]
            print(f"[{name}] Intersecting Buildings: {len(precise)}")
            
        check_coords("Scenario A [lon, lat]", coords_A)
        check_coords("Scenario B [lat, lon]", coords_B)

    except Exception as e:
        traceback.print_exc()

if __name__ == "__main__":
    test()
