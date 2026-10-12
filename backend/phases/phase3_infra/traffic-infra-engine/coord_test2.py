import sys
import geopandas as gpd
from shapely.geometry import Point, LineString
from utils.osm_loader import load_buildings
from feasibility_engine.feasibility import FeasibilityEngine

def main():
    place_name = "Malleshwaram, Bangalore, India"
    print("Loading data...")
    buildings = load_buildings(place_name)
    target_crs = buildings.crs
    
    # Take a building from the dataset so we KNOW it's a building
    b1 = buildings.iloc[50].geometry
    b1_centroid = b1.centroid
    print(f"Sample Building 50 UTM: {b1_centroid.x}, {b1_centroid.y}")
    
    # Project back to EPSG:4326 to get [lon, lat] like the frontend
    b1_gdf = gpd.GeoDataFrame(geometry=[b1_centroid], crs=target_crs)
    b1_latlon = b1_gdf.to_crs("EPSG:4326")
    b1_lon = b1_latlon.geometry.iloc[0].x
    b1_lat = b1_latlon.geometry.iloc[0].y
    print(f"Sample Building 50 EPSG:4326 (lon={b1_lon}, lat={b1_lat})")

    b2 = buildings.iloc[60].geometry
    b2_centroid = b2.centroid
    b2_gdf = gpd.GeoDataFrame(geometry=[b2_centroid], crs=target_crs)
    b2_latlon = b2_gdf.to_crs("EPSG:4326")
    b2_lon = b2_latlon.geometry.iloc[0].x
    b2_lat = b2_latlon.geometry.iloc[0].y

    # Simulated correctly drawn coords [lon, lat]
    lonlat_coords = [
        [b1_lon, b1_lat], 
        [b2_lon, b2_lat]
    ]

    print("\n[Test 1] Correct Mapping (lon, lat = pt)")
    projected_coords = []
    for pt in lonlat_coords:
        lon, lat = pt
        pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
        pt_gdf_proj = pt_gdf.to_crs(target_crs)
        proj_x = pt_gdf_proj.geometry.iloc[0].x
        proj_y = pt_gdf_proj.geometry.iloc[0].y
        projected_coords.append((proj_x, proj_y))
    
    eng = FeasibilityEngine(buildings)
    is_feas, coll = eng.check_feasibility(projected_coords)
    print(f"Correct Mapping Result: is_feasible={is_feas}")

    # Simulated swapped drawn coords [lat, lon]
    latlon_coords = [
        [b1_lat, b1_lon], 
        [b2_lat, b2_lon]
    ]
    print("\n[Test 2] Swapped Mapping (lat, lon = pt)")
    projected_coords_swapped = []
    for pt in latlon_coords:
        lon, lat = pt
        pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
        pt_gdf_proj = pt_gdf.to_crs(target_crs)
        proj_x = pt_gdf_proj.geometry.iloc[0].x
        proj_y = pt_gdf_proj.geometry.iloc[0].y
        projected_coords_swapped.append((proj_x, proj_y))
        
    is_feas_swap, coll_swap = eng.check_feasibility(projected_coords_swapped)
    print(f"Swapped Mapping Result: is_feasible={is_feas_swap}")

if __name__ == "__main__":
    main()
