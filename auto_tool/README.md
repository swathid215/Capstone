# SUMO Network & Route Generation Tools

This directory contains two Python utilities for working with SUMO (Simulation of Urban MObility) network and route files.

---

## 1. **network_repair.py**

A network repair and validation tool for SUMO network files (`.net.xml`).

### Purpose
Detects and fixes connectivity issues in road networks, ensuring edges and nodes are properly connected for realistic traffic simulations.

### Key Features
- **Network Validation**: Checks XML structure and file integrity
- **Connectivity Analysis**: Identifies disconnected components and isolated nodes
- **Automatic Repairs**:
  - Adds missing edges to connect separate network components
  - Creates missing connections between edges
  - Removes invalid or broken edges
- **Network Statistics**: Computes metrics like:
  - Total nodes and edges
  - Connected components
  - Average node degree
  - Network fragmentation percentage

### Inputs
- A SUMO network file (`.net.xml`)

### Outputs
- Repaired network file
- Detailed repair report with statistics (before/after comparison)

### Usage
```bash
python network_repair.py <input_network.net.xml>
```

---

## 2. **route_generator.py**

A route generation tool that creates realistic vehicle routes for SUMO traffic simulations.

### Purpose
Generates diverse, realistic traffic routes by analyzing network topology and edge characteristics.

### Key Features
- **Edge Classification**:
  - Filters valid motorized routes (excludes pedestrian-only and internal edges)
  - Optional support for rail networks
  - Categorizes edges by type (motorway, trunk, primary, secondary, tertiary, residential)
- **Intelligent Route Selection**:
  - Weights edges based on speed, lane count, road type, and location
  - Favors major roads and boundary crossings for realistic traffic patterns
  - Generates origin-destination (OD) pairs across the network
- **Vehicle Variety**:
  - Supports multiple vehicle classes (passenger, taxi, bus, truck, motorcycle, etc.)
  - Assigns random colors for visual distinction in simulations
  - Distributes departures over a specified time period

### Inputs
- A SUMO network file (`.net.xml`)

### Outputs
- A route file (`.rou.xml`) containing vehicle definitions and their routes
- Generation statistics and route metrics

### Usage
```bash
python route_generator.py <network.net.xml> [--output <output.rou.xml>] [--num-vehicles N]
```

---

## Typical Workflow

1. **Start with a raw SUMO network** → Use `network_repair.py` to fix connectivity issues
2. **Generate realistic routes** → Use `route_generator.py` on the repaired network to create traffic
3. **Run simulations** → Use SUMO with the cleaned network and generated routes

---

## Dependencies
- `networkx` - Network graph analysis
- `sumolib` - SUMO library for reading network files
- Python 3.7+

## Notes
- Both tools work with SUMO XML files
- The network repair tool is especially useful for networks generated programmatically or from external sources
- The route generator can be configured to include or exclude rail networks
