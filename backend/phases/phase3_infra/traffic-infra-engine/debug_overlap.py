import geopandas as gpd
from shapely.geometry import Point, LineString
from utils.osm_loader import load_buildings
from feasibility_engine.feasibility import FeasibilityEngine

def debug_feasibility():
    buildings = load_buildings("Malleshwaram, Bangalore, India")
    target_crs = buildings.crs
    
    # CASE: Folium gives [lng, lat] exactly as written in the app
    # Looking at the map, Sankey road and 6th temple street.
    # Coordinates for roughly that line the user drew:
    lonlat_coords = [
        [77.57076, 13.00334], # bottom of user's line (approx 5th Temple St)
        [77.57342, 13.01185]  # top of user's line near Sankey Road
    ]
    
    projected = []
    for pt in lonlat_coords:
        lon, lat = pt[0], pt[1]
        pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
        pt_gdf_proj = pt_gdf.to_crs(target_crs)
        proj_x = pt_gdf_proj.geometry.iloc[0].x
        proj_y = pt_gdf_proj.geometry.iloc[0].y
        projected.append((proj_x, proj_y))
        
    feas_eng = FeasibilityEngine(buildings)
    
    is_feas, coll = feas_eng.check_feasibility(projected)
    print(f"Feasible? {is_feas}, Collisions: {len(coll) if coll is not None else 0}")
    
    if coll is not None and not coll.empty:
        print("Buildings intersected:")
        print(coll.head())
    else:
        # Check if the line is ANYWHERE near the buildings.
        line = LineString(projected)
        print("Line bounds:", line.bounds)
        print("Buildings bounds:", buildings.total_bounds)
        # Find closest building
        distances = buildings.geometry.distance(line)
        print("Closest building distance:", distances.min(), "meters")

if __name__ == "__main__":
    debug_feasibility()
