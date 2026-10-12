import geopandas as gpd
from shapely.geometry import Point, LineString
import osmnx as ox

def test_collision():
    print("Loading data...")
    tags = {'building': True}
    buildings = ox.features_from_place("Malleshwaram, Bangalore, India", tags)
    buildings_proj = buildings.to_crs(buildings.estimate_utm_crs())
    
    # Get a building and create a line through it
    bldg = buildings_proj.iloc[0]
    centroid = bldg.geometry.centroid
    x, y = centroid.x, centroid.y
    # create a line that definitely intersects
    line = LineString([(x-10, y-10), (x+10, y+10)])
    
    buffer = line.buffer(4)
    intersects = buildings_proj.geometry.intersects(buffer)
    count = intersects.sum()
    print(f"Intersection count: {count}")

if __name__ == "__main__":
    test_collision()
