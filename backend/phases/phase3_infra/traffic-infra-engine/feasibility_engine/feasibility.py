import geopandas as gpd
from shapely.geometry import LineString

class FeasibilityEngine:
    """
    Responsibilities: Check physical constraints, building intersections, and spatial feasibility.
    """
    def __init__(self, buildings_gdf):
        self.buildings = buildings_gdf

    def check_feasibility(self, proposed_coords, road_width_meters=8):
        """
        Checks if the proposed road intersects with any existing buildings.
        proposed_coords: List of (x, y) coordinates in projected CRS.
        """
        print("\n[Feasibility Engine] Running Feasibility Check...")
        
        # 1. Create a Shapely geometry for the proposed road
        if len(proposed_coords) > 2 and proposed_coords[0] == proposed_coords[-1]:
            # It's a closed loop (Polygon perimeter)
            from shapely.geometry import Polygon
            proposed_road = Polygon(proposed_coords)
        else:
            proposed_road = LineString(proposed_coords)
        
        # 2. Add Buffer (simulate the physical width of the road or area)
        road_buffer = proposed_road.buffer(road_width_meters / 2.0)
        
        # 3. Robust Intersection Check using spatial indexing
        import geopandas as gpd
        road_gdf = gpd.GeoDataFrame({'geometry': [road_buffer]}, crs=self.buildings.crs)
        precise_matches = gpd.sjoin(self.buildings, road_gdf, how='inner', predicate='intersects')
        
        if not precise_matches.empty:
            print(f"FEASIBILITY FAILED: Proposed road intersects {len(precise_matches)} building(s).")
            return False, precise_matches
        
        print("FEASIBILITY APPROVED: No building collisions detected.")
        return True, None