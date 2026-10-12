import matplotlib.pyplot as plt
import osmnx as ox

def plot_results(G_updated, buildings, proposed_coords):
    """
    Visualizes the existing graph, buildings, and the proposed infrastructure.
    """
    print("\n[Visualization] Generating Map Rendering...")
    
    fig, ax = plt.subplots(figsize=(10, 10))
    
    # 1. Plot building footprints (Grey)
    print("   ↳ Rendering Buildings...")
    buildings.plot(ax=ax, facecolor='lightgray', edgecolor='dimgray', alpha=0.6)
    
    # 2. Plot the original road network (Black/White depending on background)
    print("   ↳ Rendering Road Network Graph...")
    ox.plot_graph(
        G_updated, 
        ax=ax, 
        node_size=0, 
        edge_color="#333333", 
        edge_linewidth=0.5, 
        show=False, 
        close=False,
        bgcolor="white"
    )
    
    # 3. Overlay the proposed infrastructure (Red Line)
    print("   ↳ Rendering Proposed Infrastructure...")
    x_coords = [p[0] for p in proposed_coords]
    y_coords = [p[1] for p in proposed_coords]
    ax.plot(x_coords, y_coords, color='red', linewidth=4, linestyle='--', label='Proposed Infrastructure')
    
    # Formatting
    plt.title("Traffic Digital Twin - Infrastructure Intervention", fontsize=16, fontweight='bold')
    plt.legend(loc='upper right')
    plt.axis('off')
    plt.tight_layout()
    
    # Save diagram
    plt.savefig("map_report_output.png", dpi=300)
    print("IMAGE SAVED: map_report_output.png")
    
    # Show display 
    plt.show()