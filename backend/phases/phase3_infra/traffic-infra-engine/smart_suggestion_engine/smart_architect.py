"""
Smart Architect Engine
Intelligent infrastructure recommendation system using LLM analysis
"""

import os
import json
from typing import Dict, Any, Optional
import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon
import networkx as nx


class SmartArchitectEngine:
    """
    AI-powered infrastructure recommendation engine.
    Analyzes multiple parameters and provides intelligent suggestions.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Smart Architect Engine.
        
        Args:
            api_key: Groq API key. If not provided, reads from GROQ_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.use_llm = bool(self.api_key)
        
        # Debug output
        print(f"[SmartArchitectEngine] API Key present: {bool(self.api_key)}")
        if self.api_key:
            print(f"[SmartArchitectEngine] API Key starts with: {self.api_key[:10]}...")
        
        if self.use_llm:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self.gemini_model = genai.GenerativeModel('gemini-pro')
                print("[SmartArchitectEngine] Gemini client initialized successfully")
            except ImportError as e:
                print(f"Warning: google-generativeai library not installed. {e}. Using fallback heuristic mode.")
                self.use_llm = False
            except Exception as e:
                print(f"Warning: Failed to initialize Gemini client: {e}. Using fallback heuristic mode.")
                self.use_llm = False
        else:
            print("[SmartArchitectEngine] No API key found. Using heuristic mode.")
    
    def analyze_infrastructure(
        self,
        geometry_type: str,
        length: float,
        collisions: int,
        new_connections: int,
        collision_df: Optional[gpd.GeoDataFrame] = None,
        network_graph: Optional[nx.MultiDiGraph] = None,
        buildings_gdf: Optional[gpd.GeoDataFrame] = None,
        coordinates: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Analyze infrastructure parameters and provide recommendation.
        
        Args:
            geometry_type: 'LineString' or 'Polygon'
            length: Length/perimeter in meters
            collisions: Number of building collisions
            new_connections: Number of new network connections created
            collision_df: GeoDataFrame of collided buildings
            network_graph: NetworkX graph object
            buildings_gdf: GeoDataFrame of all buildings
            coordinates: Projected coordinates of the route
            
        Returns:
            Dictionary with infrastructure recommendation and detailed reasoning.
        """
        
        # Extract additional metrics
        metrics = self._calculate_metrics(
            geometry_type, length, collisions, new_connections,
            collision_df, buildings_gdf, coordinates, network_graph
        )
        
        # Get recommendation based on LLM or heuristics
        if self.use_llm:
            recommendation = self._get_llm_recommendation(metrics)
        else:
            recommendation = self._get_heuristic_recommendation(metrics)
        
        return {
            "metrics": metrics,
            "recommendation": recommendation["infrastructure_type"],
            "reasoning": recommendation["reasoning"],
            "confidence": recommendation.get("confidence", 0.85),
            "cost_estimate": recommendation.get("cost_estimate", "N/A"),
            "implementation_time": recommendation.get("implementation_time", "N/A"),
            "risk_factors": recommendation.get("risk_factors", []),
            "environmental_impact": recommendation.get("environmental_impact", "Moderate")
        }
    
    def _calculate_metrics(
        self,
        geometry_type: str,
        length: float,
        collisions: int,
        new_connections: int,
        collision_df: Optional[gpd.GeoDataFrame],
        buildings_gdf: Optional[gpd.GeoDataFrame],
        coordinates: Optional[list],
        network_graph: Optional[nx.MultiDiGraph]
    ) -> Dict[str, Any]:
        """Calculate detailed metrics for analysis."""
        
        metrics = {
            "geometry_type": geometry_type,
            "length_meters": round(length, 2),
            "building_collisions": collisions,
            "network_connections": new_connections,
            "collision_density": round(collisions / max(length / 100, 1), 2),  # collisions per 100m
            "connectivity_score": self._calculate_connectivity_score(new_connections, length),
            "land_acquisition_complexity": "High" if collisions > 5 else ("Medium" if collisions > 0 else "Low"),
            "urban_density": self._assess_urban_density(collision_df, buildings_gdf),
            "route_efficiency": self._calculate_route_efficiency(coordinates, network_graph)
        }
        
        return metrics
    
    def _calculate_connectivity_score(self, new_connections: int, length: float) -> float:
        """Calculate connectivity efficiency score (0-100)."""
        connections_per_km = (new_connections / (length / 1000)) if length > 0 else 0
        score = min(100, connections_per_km * 10)  # Scale to 0-100
        return round(score, 2)
    
    def _assess_urban_density(
        self,
        collision_df: Optional[gpd.GeoDataFrame],
        buildings_gdf: Optional[gpd.GeoDataFrame]
    ) -> str:
        """Assess urban density based on collision data."""
        if collision_df is None or len(collision_df) == 0:
            return "Low (Rural/Open)"
        
        if len(collision_df) > 20:
            return "Very High (Dense Urban)"
        elif len(collision_df) > 10:
            return "High (Urban)"
        elif len(collision_df) > 3:
            return "Medium (Mixed Urban-Rural)"
        else:
            return "Low-Medium (Emerging Area)"
    
    def _calculate_route_efficiency(
        self,
        coordinates: Optional[list],
        network_graph: Optional[nx.MultiDiGraph]
    ) -> float:
        """Calculate route efficiency based on network topology."""
        if not coordinates or len(coordinates) < 2:
            return 0.5
        
        # Simple efficiency: straight line vs actual path
        start = coordinates[0]
        end = coordinates[-1]
        straight_distance = ((end[0] - start[0])**2 + (end[1] - start[1])**2)**0.5
        actual_distance = sum([
            ((coordinates[i+1][0] - coordinates[i][0])**2 + 
             (coordinates[i+1][1] - coordinates[i][1])**2)**0.5
            for i in range(len(coordinates) - 1)
        ])
        
        efficiency = min(1.0, straight_distance / actual_distance) if actual_distance > 0 else 0.5
        return round(efficiency, 2)
    
    def _get_llm_recommendation(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Get recommendation from Gemini LLM."""
        try:
            print("[SmartArchitectEngine] Making Gemini API call...")
            prompt = self._build_analysis_prompt(metrics)
            
            response = self.gemini_model.generate_content(prompt)
            
            print("[SmartArchitectEngine] Gemini API call successful!")
            response_text = response.text
            recommendation = self._parse_llm_response(response_text)
            return recommendation
            
        except Exception as e:
            print(f"[SmartArchitectEngine] LLM Error: {e}. Falling back to heuristic mode.")
            return self._get_heuristic_recommendation(metrics)
    
    def _build_analysis_prompt(self, metrics: Dict[str, Any]) -> str:
        """Build the analysis prompt for the LLM."""
        prompt = f"""
Analyze the following infrastructure proposal metrics and recommend the optimal infrastructure type.

PROPOSED ROUTE METRICS:
- Geometry Type: {metrics['geometry_type']}
- Length/Perimeter: {metrics['length_meters']} meters
- Building Collisions: {metrics['building_collisions']}
- Collision Density: {metrics['collision_density']} per 100m
- New Network Connections: {metrics['network_connections']}
- Connectivity Score: {metrics['connectivity_score']}/100
- Urban Density: {metrics['urban_density']}
- Land Acquisition Complexity: {metrics['land_acquisition_complexity']}
- Route Efficiency: {metrics['route_efficiency']}

INFRASTRUCTURE OPTIONS TO CONSIDER:
1. Surface Road (Ground Level)
2. Flyover (Elevated Corridor)
3. Tunnel (Underground)
4. Mixed Solution (Combination)

ANALYSIS REQUIREMENTS:
For the recommended infrastructure type, provide:
1. Specific infrastructure type recommendation
2. Detailed reasoning (3-4 sentences) explaining WHY this type is optimal given the metrics
3. Estimated cost level (Low/Medium/High/Very High)
4. Estimated implementation time (Months: range like 6-12)
5. Key risk factors (list 2-3 main risks)
6. Environmental impact assessment (Low/Moderate/High)
7. Confidence level (0.0-1.0) in this recommendation

Format your response as JSON with keys: infrastructure_type, reasoning, cost_estimate, implementation_time, risk_factors, environmental_impact, confidence
"""
        return prompt
    
    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """Parse LLM response into structured format."""
        try:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                return json.loads(json_str)
        except:
            pass
        
        # Fallback: extract key information from text
        return {
            "infrastructure_type": self._extract_infrastructure_type(response_text),
            "reasoning": response_text[:500],
            "confidence": 0.7,
            "cost_estimate": "Medium",
            "implementation_time": "12-18",
            "risk_factors": ["Design complexity", "Weather delays"],
            "environmental_impact": "Moderate"
        }
    
    def _extract_infrastructure_type(self, text: str) -> str:
        """Extract infrastructure type from text."""
        text_lower = text.lower()
        if "tunnel" in text_lower:
            return "Tunnel / Underground"
        elif "flyover" in text_lower or "elevated" in text_lower:
            return "Flyover / Elevated Corridor"
        elif "mixed" in text_lower or "combination" in text_lower:
            return "Mixed Solution"
        else:
            return "Surface Road"
    
    def _get_heuristic_recommendation(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Get recommendation using rule-based heuristics."""
        
        length = metrics['length_meters']
        collisions = metrics['building_collisions']
        collision_density = metrics['collision_density']
        connectivity = metrics['connectivity_score']
        geometry = metrics['geometry_type']
        urban_density = metrics['urban_density']
        
        recommendation = {
            "infrastructure_type": "Surface Road",
            "reasoning": "",
            "confidence": 0.75,
            "cost_estimate": "Medium",
            "implementation_time": "6-9",
            "risk_factors": [],
            "environmental_impact": "Low"
        }
        
        # Decision logic based on metrics
        if geometry == 'LineString':
            
            if collisions > 10:
                # Very high collision density - underground necessary
                recommendation["infrastructure_type"] = "Tunnel / Underground Bypass"
                recommendation["confidence"] = 0.90
                recommendation["reasoning"] = (
                    f"High-density urban area with {collisions} building collisions detected. "
                    f"A surface road would require extensive demolition and land acquisition costing 500+ crores. "
                    f"An underground tunnel minimizes property disruption and is strategically optimal for long-term urban development. "
                    f"The {connectivity:.1f} connectivity score indicates significant traffic redistribution potential."
                )
                recommendation["cost_estimate"] = "Very High"
                recommendation["implementation_time"] = "24-36"
                recommendation["risk_factors"] = ["Geological surveys needed", "Deep excavation complexity", "Traffic diversions during construction", "Underground utility conflicts"]
                recommendation["environmental_impact"] = "High (Construction phase)"
                
            elif collisions > 4:
                # Medium-high collisions - elevated option
                if length > 1500:
                    recommendation["infrastructure_type"] = "Tunnel / Underground Bypass"
                    recommendation["confidence"] = 0.82
                    recommendation["reasoning"] = (
                        f"Extended route ({length:.0f}m) through moderate-high density urban area with {collisions} collisions. "
                        f"The cumulative displacement impact and land costs favor underground infrastructure. "
                        f"Network connectivity score of {connectivity:.1f} justifies the investment. Underground preserves street-level dynamics."
                    )
                    recommendation["cost_estimate"] = "Very High"
                    recommendation["implementation_time"] = "18-30"
                    recommendation["risk_factors"] = ["Long excavation timeline", "Environmental baseline surveys", "Underground utilities relocation"]
                    recommendation["environmental_impact"] = "High (Construction)"
                else:
                    recommendation["infrastructure_type"] = "Flyover / Elevated Corridor"
                    recommendation["confidence"] = 0.85
                    recommendation["reasoning"] = (
                        f"Route length {length:.0f}m with {collisions} building obstacles in moderately dense area. "
                        f"Elevated grade separation is cost-effective, avoids mass demolition, and provides highway-speed connectivity. "
                        f"Connectivity gain of {connectivity:.1f} points justifies elevated infrastructure. Ground-level urban vitality is preserved."
                    )
                    recommendation["cost_estimate"] = "High"
                    recommendation["implementation_time"] = "12-18"
                    recommendation["risk_factors"] = ["Structural design for span", "Foundation soil analysis", "Aesthetic integration"]
                    recommendation["environmental_impact"] = "Moderate"
                    
            elif collisions > 0:
                # Low-medium collisions
                recommendation["infrastructure_type"] = "Flyover / Elevated Corridor"
                recommendation["confidence"] = 0.80
                recommendation["reasoning"] = (
                    f"Route encounters {collisions} scattered buildings over {length:.0f}m in emerging/mixed urban area. "
                    f"An elevated corridor strategically bypasses obstacles while maintaining connectivity ({connectivity:.1f}/100). "
                    f"More economical than tunneling while avoiding demolition costs. Future-proof for vertical urban expansion."
                )
                recommendation["cost_estimate"] = "Medium-High"
                recommendation["implementation_time"] = "10-15"
                recommendation["risk_factors"] = ["Visual impact concerns", "Structural foundation design", "Noise mitigation"]
                recommendation["environmental_impact"] = "Moderate"
                
            else:
                # No collisions - surface road
                if length > 2000:
                    recommendation["infrastructure_type"] = "Arterial Road / Highway Expansion"
                    recommendation["cost_estimate"] = "Medium"
                    recommendation["implementation_time"] = "8-12"
                    recommendation["reasoning"] = (
                        f"Clear path of {length:.0f}m with zero building interference through rural or low-density area. "
                        f"Standard multi-lane arterial road is most cost-effective. Excellent for inter-city connectivity "
                        f"with connectivity potential of {connectivity:.1f}/100. Asset-light solution with rapid deployment."
                    )
                    recommendation["environmental_impact"] = "Low"
                else:
                    recommendation["infrastructure_type"] = "Local Road Widening / New Link Road"
                    recommendation["cost_estimate"] = "Low"
                    recommendation["implementation_time"] = "3-6"
                    recommendation["reasoning"] = (
                        f"Short, unobstructed route ({length:.0f}m) in low-density area. "
                        f"Standard surface road construction is optimal. Quick, economical solution for local connectivity gaps "
                        f"with {connectivity:.1f} network efficiency score. Supports gradual urban infill development."
                    )
                    recommendation["environmental_impact"] = "Low"
                recommendation["confidence"] = 0.95
                recommendation["risk_factors"] = ["Land rights acquisition", "Drainage design"]
                
        elif geometry == 'Polygon':
            if collisions > 8:
                recommendation["infrastructure_type"] = "Area Redevelopment / Mixed-Use Complex"
                recommendation["confidence"] = 0.80
                recommendation["reasoning"] = (
                    f"Large area ({length:.0f}m perimeter) with {collisions} existing structures presents significant development opportunity. "
                    f"Phased land assembly and strategic demolition can enable modern mixed-use development. "
                    f"Infrastructure integration ({connectivity:.1f} connections) supports multi-modal transportation hub. High long-term value creation."
                )
                recommendation["cost_estimate"] = "Very High"
                recommendation["implementation_time"] = "36-60"
                recommendation["risk_factors"] = ["Complex land acquisition", "Stakeholder negotiations", "Phasing risks"]
                recommendation["environmental_impact"] = "High (Long-term positive)"
            else:
                recommendation["infrastructure_type"] = "New Layout / Transportation Hub"
                recommendation["confidence"] = 0.90
                recommendation["reasoning"] = (
                    f"Clear, greenfield polygon area ({length:.0f}m) with minimal existing structures is ideal for planned development. "
                    f"Can accommodate integrated transit hub, parking facilities, or mixed-use development. "
                    f"Connectivity score of {connectivity:.1f} suggests strong network benefits. Enables modern urban design principles."
                )
                recommendation["cost_estimate"] = "High"
                recommendation["implementation_time"] = "18-30"
                recommendation["risk_factors"] = ["Zoning approvals", "Environmental clearances", "Utility infrastructure"]
                recommendation["environmental_impact"] = "Moderate"
        
        recommendation["confidence"] = round(recommendation["confidence"], 2)
        
        return recommendation


def create_smart_architect_engine(api_key: Optional[str] = None) -> SmartArchitectEngine:
    """Factory function to create Smart Architect Engine."""
    return SmartArchitectEngine(api_key=api_key)
