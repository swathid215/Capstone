import geopandas as gpd
from shapely.geometry import Point, LineString
from utils.osm_loader import load_buildings
from feasibility_engine.feasibility import FeasibilityEngine

def debug_feasibility():
    buildings = load_buildings("Malleshwaram, Bangalore, India")
    target_crs = buildings.crs
    
    # CASE A: Folium gives [lng, lat]
    lonlat_coords = [
        [77.5714, 13.003], # bottom of user's line
        [77.5724, 13.006]  # top of user's line near Sankey
    ]
    
    projected_A = []
    for pt in lonlat_coords:
        lon, lat = pt[0], pt[1]
        pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
        pt_gdf_proj = pt_gdf.to_crs(target_crs)
        projected_A.append((pt_gdf_proj.geometry.iloc[0].x, pt_gdf_proj.geometry.iloc[0].y))
        
    feas_eng = FeasibilityEngine(buildings)
    
    print("--- CASE A (Folium gives lng, lat AND App reads lng, lat) ---")
    is_feas, coll = feas_eng.check_feasibility(projected_A)
    print(f"Feasible? {is_feas}, Collisions: {len(coll) if coll is not None else 0}")

if __name__ == "__main__":
    debug_feasibility()
