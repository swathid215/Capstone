import networkx as nx
import numpy as np
import random

class Vehicle:
    def __init__(self, v_id, source, destination, path, base_speed):
        self.id = v_id
        self.type = random.choice(['car', 'car', 'car', 'bike'])
        self.source = source
        self.destination = destination
        self.path = path  # List of node IDs
        self.base_speed = base_speed  # Starting speed
        self.current_speed = base_speed
        
        # State tracking
        self.edge_index = 0  # Which edge in the path the vehicle is on
        self.progress_on_edge = 0.0  # Distance traveled on current edge
        self.active = True
        self.travel_time = 0
        self.history = []  # List of (time_step, x, y)

class TrafficSimulator:
    """
    Module Owner: Integrated Digital Twin
    Responsibilities: Simulate stochastic traffic and measure performance metrics.
    """
    def __init__(self, graph, arrival_rate=5.0, mean_speed=40.0, speed_std=5.0):
        self.G = graph.copy()
        self.arrival_rate = arrival_rate
        self.mean_speed = mean_speed  # km/h
        self.speed_std = speed_std
        
        self.vehicles = []
        self.completed_vehicles = []
        self.time_step = 0
        self.vehicle_counter = 0
        
        # Initialize edge dynamic attributes
        for u, v, data in self.G.edges(data=True):
            if 'capacity' not in data:
                lanes = data.get('lanes', 1)
                if isinstance(lanes, list): lanes = int(lanes[0])
                elif isinstance(lanes, str): lanes = int(lanes)
                
                data['capacity'] = lanes * 10  # capacity per lane
            data['current_load'] = 0
            if 'length' not in data:
                data['length'] = 100.0

    def generate_traffic(self):
        """Stochastic Traffic Generator using Poisson and Normal distributions."""
        num_new_vehicles = np.random.poisson(self.arrival_rate)
        
        nodes = list(self.G.nodes)
        for _ in range(num_new_vehicles):
            src = random.choice(nodes)
            dst = random.choice(nodes)
            if dst == src: continue
                
            try:
                # Assign Route: Shortest path based on length
                path = nx.shortest_path(self.G, source=src, target=dst, weight='length')
                
                # Assign Speed: Normal Distribution (Gaussian) -> convert km/h to m/s
                speed_kmh = max(10, np.random.normal(self.mean_speed, self.speed_std))
                speed_ms = speed_kmh * (1000 / 3600)
                
                v = Vehicle(self.vehicle_counter, src, dst, path, speed_ms)
                self.vehicles.append(v)
                self.vehicle_counter += 1
                
                # Add to initial edge load
                if len(path) > 1:
                    u, v_node = path[0], path[1]
                    if self.G.has_edge(u, v_node):
                        # Some edges are multigraph, key 0
                        if 0 in self.G[u][v_node]:
                            self.G[u][v_node][0]['current_load'] += 1
                        else:
                            self.G[u][v_node]['current_load'] = self.G[u][v_node].get('current_load', 0) + 1
                        
            except nx.NetworkXNoPath:
                # Disconnected components, skip
                pass

    def update_congestion_and_speeds(self):
        """Congestion Model: Decreases speed if load > capacity."""
        # Check if the graph is a MultiGraph or normal Graph
        is_multigraph = self.G.is_multigraph()

        if is_multigraph:
            for u, v, key, data in self.G.edges(keys=True, data=True):
                self._apply_congestion(data)
        else:
            for u, v, data in self.G.edges(data=True):
                self._apply_congestion(data)

    def _apply_congestion(self, data):
        current_load = data.get('current_load', 0)
        capacity = data.get('capacity', 10)
        
        if current_load > capacity:
            penalty = capacity / current_load
        else:
            penalty = 1.0
            
        data['congestion_factor'] = penalty

    def move_vehicles(self):
        """Moves vehicles along their assigned routes based on physics."""
        is_multigraph = self.G.is_multigraph()

        for v in self.vehicles:
            if not v.active:
                continue
                
            v.travel_time += 1  # 1 discrete step (e.g., 1 second)
            
            if v.edge_index >= len(v.path) - 1:
                v.active = False
                self.completed_vehicles.append(v)
                continue
                
            u = v.path[v.edge_index]
            nxt = v.path[v.edge_index + 1]
            
            if self.G.has_edge(u, nxt):
                if is_multigraph and 0 in self.G[u][nxt]:
                    edge_data = self.G[u][nxt][0]
                else:
                    edge_data = self.G[u][nxt]
                    
                edge_length = edge_data.get('length', 100)
                congestion_fac = edge_data.get('congestion_factor', 1.0)
                
                v.current_speed = v.base_speed * congestion_fac
                v.progress_on_edge += v.current_speed
                
                # Record position
                if 'geometry' in edge_data:
                    pt = edge_data['geometry'].interpolate(v.progress_on_edge)
                    v.history.append((self.time_step, pt.x, pt.y))
                else:
                    u_x, u_y = self.G.nodes[u]['x'], self.G.nodes[u]['y']
                    nxt_x, nxt_y = self.G.nodes[nxt]['x'], self.G.nodes[nxt]['y']
                    ratio = min(1.0, v.progress_on_edge / edge_length)
                    curr_x = u_x + (nxt_x - u_x) * ratio
                    curr_y = u_y + (nxt_y - u_y) * ratio
                    v.history.append((self.time_step, curr_x, curr_y))
                
                if v.progress_on_edge >= edge_length:
                    edge_data['current_load'] = max(0, edge_data.get('current_load', 1) - 1)
                    v.edge_index += 1
                    v.progress_on_edge = 0.0
                    
                    if v.edge_index < len(v.path) - 1:
                        new_u = v.path[v.edge_index]
                        new_nxt = v.path[v.edge_index + 1]
                        if self.G.has_edge(new_u, new_nxt):
                            if is_multigraph and 0 in self.G[new_u][new_nxt]:
                                self.G[new_u][new_nxt][0]['current_load'] += 1
                            else:
                                self.G[new_u][new_nxt]['current_load'] = self.G[new_u][new_nxt].get('current_load', 0) + 1
                    else:
                        v.active = False
                        self.completed_vehicles.append(v)
            else:
                v.active = False

    def run_simulation(self, steps=100):
        """Main Simulation Loop"""
        print(f"\n🚀 [Simulation Engine] Starting {steps}-step stochastic simulation...")
        
        for t in range(steps):
            self.time_step += 1
            self.generate_traffic()
            self.update_congestion_and_speeds()
            self.move_vehicles()
            self.vehicles = [v for v in self.vehicles if v.active]
            
        return self.compute_metrics()

    def compute_metrics(self):
        """Outputs performance metrics of the network."""
        avg_travel_time = 0
        if self.completed_vehicles:
            avg_travel_time = sum(v.travel_time for v in self.completed_vehicles) / len(self.completed_vehicles)
            
        throughput = len(self.completed_vehicles)
        
        total_congestion = []
        is_multigraph = self.G.is_multigraph()

        if is_multigraph:
            for u, v, key, data in self.G.edges(keys=True, data=True):
                cap = data.get('capacity', 10)
                load = data.get('current_load', 0)
                total_congestion.append(load / max(1, cap))
        else:
            for u, v, data in self.G.edges(data=True):
                cap = data.get('capacity', 10)
                load = data.get('current_load', 0)
                total_congestion.append(load / max(1, cap))
            
        avg_congestion = sum(total_congestion) / max(1, len(total_congestion)) if total_congestion else 0
            
        return {
            "throughput": throughput,
            "avg_travel_time": avg_travel_time,
            "active_vehicles": len(self.vehicles),
            "avg_congestion_ratio": avg_congestion,
            "all_vehicles": self.completed_vehicles + self.vehicles
        }
