import sys
if hasattr(sys.stdout, 'reconfigure') and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from utils.osm_loader import load_road_network, load_buildings
from feasibility_engine.feasibility import FeasibilityEngine
from graph_modification.modify_graph import GraphModificationEngine
from visualization.plot_map import plot_results
import random

def main():
    # ----------------------------------------------------
    # Configuration
    # ----------------------------------------------------
    PLACE_NAME = "Bangalore, India"
    
    # ====================================================
    # Phase 1: Data Ingestion (Shared Setup)
    # ====================================================
    print(f"\n=======================================================")
    print(f"INFRASTRUCTURE INTERVENTION ENGINE")
    print(f"=======================================================\n")
    
    G = load_road_network(PLACE_NAME)
    buildings = load_buildings(PLACE_NAME)
    
    # ----------------------------------------------------
    # Simulated User Input: Propose New Infrastructure
    # In a fully integrated system, this comes from a front-end map UI.
    # Here, we pick a valid starting junction and extend a 400m road.
    # ----------------------------------------------------
    random_node = list(G.nodes(data=True))[200]
    x_base, y_base = random_node[1]['x'], random_node[1]['y']
    
    # Simulating drawing a straight ~400 meter road diagonally
    proposed_road_coords = [
        (x_base, y_base), 
        (x_base + 200, y_base + 200),
        (x_base + 400, y_base + 100)
    ]
    
    print(f"\n[System] User proposed a new infrastructure (length ≈ 400m).")
    print(f"   ↳ Area: {PLACE_NAME}")
    
    # ====================================================
    # Phase 2: Feasibility Engine (Swathi H Rao)
    # ====================================================
    print(f"\n=======================================================")
    print(f"PHASE 2: FEASIBILITY ENGINE")
    print(f"=======================================================")
    feasibility_engine = FeasibilityEngine(buildings)
    is_feasible, colliding_buildings = feasibility_engine.check_feasibility(proposed_road_coords)
    
    # ====================================================
    # Phase 3: Graph Modification (Swathi D)
    # ====================================================
    if is_feasible:
        print(f"\n=======================================================")
        print(f"PHASE 3: GRAPH MODIFICATION ENGINE")
        print(f"=======================================================")
        mod_engine = GraphModificationEngine(G)
        G_updated = mod_engine.add_infrastructure(proposed_road_coords)
        
        # Calculate impacts
        impact_report = mod_engine.calculate_impact()
        for key, val in impact_report.items():
            print(f"  > {key}: {val}")
            
        # ====================================================
        # Phase 4: Viz & Report (Shared output)
        # ====================================================
        print(f"\n=======================================================")
        print(f"PHASE 4: REPORT & VISUALIZATION")
        print(f"=======================================================")
        plot_results(G_updated, buildings, proposed_road_coords)
        
    else:
        print("\nSYSTEM HALTED: Cannot modify graph due to physical constraints.")
        print("Please propose a different geographic route that does not intersect buildings.")
        
        # Plot anyway to show the collision!
        print(f"\n=======================================================")
        print(f"PHASE 4: REPORT & VISUALIZATION (SHOWING COLLISION)")
        print(f"=======================================================")
        plot_results(G, buildings, proposed_road_coords)

if __name__ == "__main__":
    main()