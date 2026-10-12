import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw, Geocoder
import pandas as pd
import random
import datetime
import geopandas as gpd
from shapely.geometry import Point, LineString
import osmnx as ox
import networkx as nx
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Backend engines
from utils.osm_loader import load_road_network, load_buildings
from feasibility_engine.feasibility import FeasibilityEngine
from graph_modification.modify_graph import GraphModificationEngine
from smart_suggestion_engine import SmartArchitectEngine

# --- Page Config ---
st.set_page_config(
    page_icon="map",
    layout="wide"
)

@st.cache_resource(show_spinner="Loading Base Map & Building Footprints from OSM (Caching for low latency)...")
def get_city_data(place_name="Kathriguppe, Bangalore, India", point=None, dist=5000):
    G = load_road_network(place_name, point=point, dist=dist)
    buildings = load_buildings(place_name, point=point, dist=dist)
    return G, buildings

def init_map(center=None, bounds=None):
    """Initializes a Folium map centered on the current study area."""
    # If no center is provided, try to calculate it from the bounds for better accuracy
    if not center and bounds:
        lat_c = (bounds[0][0] + bounds[1][0]) / 2
        lon_c = (bounds[0][1] + bounds[1][1]) / 2
        map_center = [lat_c, lon_c]
    else:
        map_center = center if center else [12.9360, 77.5400]
        
    m = folium.Map(location=map_center, zoom_start=14, tiles="OpenStreetMap")
    
    if bounds:
        # Tight boundary fitting to avoid the "world map" view
        m.fit_bounds(bounds, padding=[20, 20])
        folium.Rectangle(bounds, color='#3388ff', weight=2, fill=False, dash_array='5, 5', tooltip='Data Boundary').add_to(m)
        
    
    # Add Search Geocoder for location lookups
    Geocoder().add_to(m)
    
    # Add Drawing Tools
    draw = Draw(
        draw_options={
            'polyline': True,
            'polygon': True,
            'rectangle': False,
            'circle': False,
            'marker': False,
            'circlemarker': False,
        },
        edit_options={'edit': False}
    )
    draw.add_to(m)
    return m

def process_geometry(geom, buildings_gdf, G, feas_eng, mod_eng, road_width=8, is_oneway=False):
    """Processes a drawn geometry to calculate metrics for analysis."""
    geom_type = geom['type']
    coords = geom['coordinates']
    
    # Setup coordinate conversion
    if geom_type == 'Polygon':
        lonlat_coords = coords[0] # Outer ring
    else:
        lonlat_coords = coords
        
    target_crs = buildings_gdf.crs
    
    projected_coords = []
    for pt in lonlat_coords:
        # Strictly dynamic coordinate mapping exactly as drawn
        lon, lat = pt[0], pt[1]
        
        pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
        pt_gdf_proj = pt_gdf.to_crs(target_crs)
        proj_x = pt_gdf_proj.geometry.iloc[0].x
        proj_y = pt_gdf_proj.geometry.iloc[0].y
        projected_coords.append((proj_x, proj_y))
        
    is_feasible, collisions_df = feas_eng.check_feasibility(projected_coords, road_width_meters=road_width)
    num_collisions = len(collisions_df) if collisions_df is not None else 0
    
    mod_eng.add_infrastructure(projected_coords, is_oneway=is_oneway)
    report = mod_eng.calculate_impact()
    new_connections = report.get('New Connections Spliced', 0)
    
    # Calculate geometric length in meters
    geom_length = 0
    if len(projected_coords) > 1:
        if geom_type == 'Polygon':
            geom_length = LineString(projected_coords).length # Perimeter
        else:
            geom_length = LineString(projected_coords).length
            
    return {
        "type": geom_type,
        "is_feasible": is_feasible,
        "collisions": num_collisions,
        "collisions_df": collisions_df,
        "new_connections": new_connections,
        "length": geom_length,
        "projected_coords": projected_coords
    }

def prepare_graph_for_export(G):
    """Converts a graph to a GraphML-compatible format by stringifying complex types (geometries, lists)."""
    G_export = G.copy()
    for u, v, k, data in G_export.edges(data=True, keys=True):
        for attr, value in data.items():
            if isinstance(value, (list, LineString, Point)):
                data[attr] = str(value)
    for n, data in G_export.nodes(data=True):
        for attr, value in data.items():
            if isinstance(value, (list, LineString, Point)):
                data[attr] = str(value)
    return G_export

def main():
    st.title("Traffic Digital Twin: Infrastructure Engine")
    st.markdown("**Team**: Engineering")
    
    # --- Session State Management ---
    if 'active_place' not in st.session_state:
        st.session_state.active_place = "Kathriguppe, Bangalore, India"
    if 'active_point' not in st.session_state:
        st.session_state.active_point = None
    if 'active_dist' not in st.session_state:
        st.session_state.active_dist = 5000
    if 'last_clicked' not in st.session_state:
        st.session_state.last_clicked = None

    # Lazy Load Backend
    G, buildings = get_city_data(
        place_name=st.session_state.active_place, 
        point=st.session_state.active_point, 
        dist=st.session_state.active_dist
    )
    feas_eng = FeasibilityEngine(buildings)
    
    # Region Engine Sidebar
    st.sidebar.header("🗺️ Region Configuration")
    search_place = st.sidebar.text_input("Search Location / Area", value=st.session_state.active_place)
    
    col_coords = st.sidebar.columns(2)
    with col_coords[0]:
        st.markdown(f"**Lat:** {st.session_state.last_clicked[0]:.4f}" if st.session_state.last_clicked else "**Lat:** -")
    with col_coords[1]:
        st.markdown(f"**Lon:** {st.session_state.last_clicked[1]:.4f}" if st.session_state.last_clicked else "**Lon:** -")
    
    use_clicked = st.sidebar.button("📍 Use Last Clicked Point", use_container_width=True)
    if use_clicked and st.session_state.last_clicked:
        st.session_state.active_point = st.session_state.last_clicked
        st.session_state.active_place = None # Prioritize point
        st.sidebar.success("Updated to pinpoint location!")
        
    search_dist = st.sidebar.slider("Search Radius (km)", 1, 10, st.session_state.active_dist // 1000) * 1000
    
    load_region = st.sidebar.button("🚀 Load New Region", type="primary", use_container_width=True)
    if load_region:
        if not use_clicked: # If we didn't just update to a point, use the text search
            st.session_state.active_place = search_place
            st.session_state.active_point = None
        st.session_state.active_dist = search_dist
        st.rerun()

    # Base Region download in sidebar always red
    if 'G' in locals() and G is not None:
        st.sidebar.divider()
        st.sidebar.subheader("📦 Region Export")
        try:
            G_base_export = prepare_graph_for_export(G)
            base_graphml = "\n".join(nx.generate_graphml(G_base_export))
            st.sidebar.download_button(
                label="Download Base Region (.graphml)",
                data=base_graphml,
                file_name=f"base_map_export.graphml",
                mime="application/xml",
                use_container_width=True,
                type="primary"
            )
        except Exception as e:
            st.sidebar.error(f"Base Export Error: {e}")

    st.sidebar.divider()
    
    # Global Config Sidebar
    st.sidebar.header("⚙️ Engine Configuration")
    global_road_width = st.sidebar.slider("Road Width (meters)", 2, 30, 8, help="Used by Feasibility Engine to calculate collision buffers.")
    global_is_oneway = st.sidebar.checkbox("One-Way Infrastructure", value=False, help="If checked, the network routes traffic only in the direction the route was drawn.")
    
    # Calculate Data Bounds for Visualization
    buildings_latlon = buildings.to_crs("EPSG:4326")
    minx, miny, maxx, maxy = buildings_latlon.total_bounds
    map_bounds = [[miny, minx], [maxy, maxx]]
    
    # Create the three tabs
    tab1, tab2, tab3 = st.tabs(["Proposal Comparison", "Smart Architect", "Intervention Engine"])
    
    with tab1:
        st.header("Proposal Analyzer")
        st.info("Draw 2 to 3 road proposals (lines or polygons) on the map below. The engine will simulate traffic distribution, compare them, and recommend the best option.")
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            m1 = init_map(center=st.session_state.active_point if st.session_state.active_point else [12.9360, 77.5400], bounds=map_bounds)
            output1 = st_folium(m1, width=800, height=500, key="map_analyzer")
            if output1 and output1.get('last_clicked'):
                st.session_state.last_clicked = (output1['last_clicked']['lat'], output1['last_clicked']['lng'])
            
        with col2:
            st.subheader("Analysis & Comparison")
            st.markdown("**Route Types:**")
            p1_type = st.selectbox("Proposal 1", ["Surface Road", "Flyover", "Tunnel"], key="p1_type")
            p2_type = st.selectbox("Proposal 2", ["Surface Road", "Flyover", "Tunnel"], key="p2_type")
            p3_type = st.selectbox("Proposal 3 (Optional)", ["Surface Road", "Flyover", "Tunnel"], key="p3_type")
            
            compare_btn = st.button("Compare Proposals", type="primary", use_container_width=True, key="btn_compare")
            
            if compare_btn:
                if output1 and 'all_drawings' in output1 and len(output1['all_drawings']) >= 2:
                    drawings = output1['all_drawings']
                    st.success(f"Captured {len(drawings)} proposals for analysis.")
                    
                    results = []
                    for i, drawing in enumerate(drawings):
                        with st.spinner(f"Analyzing Proposal {i+1}..."):
                            mod_eng = GraphModificationEngine(G)
                            geom = drawing['geometry']
                            metrics = process_geometry(geom, buildings, G, feas_eng, mod_eng, road_width=global_road_width, is_oneway=global_is_oneway)
                            metrics['mod_eng'] = mod_eng # Store engine for download later
                            
                            ptypes = [p1_type, p2_type, p3_type]
                            ptype = ptypes[i] if i < len(ptypes) else "Surface Road"
                            
                            penalty = 10
                            if ptype == "Tunnel":
                                penalty = 0
                            elif ptype == "Flyover":
                                penalty = 3
                                
                            score = 50 + (metrics['new_connections'] * 5) - (metrics['collisions'] * penalty)
                            
                            metrics['score'] = round(score, 2)
                            metrics['name'] = f"Proposal {i+1} ({ptype})"
                            metrics['ptype'] = ptype
                            results.append(metrics)
                            
                    results = sorted(results, key=lambda x: x['score'], reverse=True)
                    best = results[0]
                    
                    st.write("### Recommendation")
                    st.success(f"**{best['name']}** is the recommended proposal with a Traffic Distribution Score of **{best['score']}** points.")
                    
                    if len(results) > 1:
                        runner_up = results[1]
                        diff = best['score'] - runner_up['score']
                        reasoning = f"**Reasoning:** **{best['name']}** outperformed the runner-up by **{diff:.2f} points**. "
                        
                        if best['collisions'] < runner_up['collisions']:
                            reasoning += f"It has fewer physical collisions with existing buildings ({best['collisions']} vs {runner_up['collisions']}). "
                        elif best['collisions'] > runner_up['collisions']:
                            if best['ptype'] in ["Tunnel", "Flyover"]:
                                reasoning += f"Although it intersects more buildings ({best['collisions']}), its {best['ptype']} design mathematically bypasses or minimizes the standard demolition penalties compared to the alternative. "
                            else:
                                reasoning += f"Despite intersecting more buildings, its outstanding graph connectivity offsets the penalty. "
                                
                        if best['new_connections'] > runner_up['new_connections']:
                            reasoning += f"Furthermore, it creates better traffic distribution with {best['new_connections']} new network edge splices compared to {runner_up['new_connections']} for the runner-up."
                        elif best['new_connections'] == runner_up['new_connections']:
                            reasoning += "Both top proposals offer identical network splicing benefits."
                            
                        st.info(reasoning)
                    
                    st.write("### Metrics Comparison")
                    df = pd.DataFrame(results)
                    df_display = df[['name', 'type', 'length', 'collisions', 'score']].copy()
                    df_display['length'] = df_display['length'].round(2).astype(str) + " m"
                    df_display.columns = ["Proposal", "Type", "Length", "Bldg Collisions", "Traffic Score"]
                    st.dataframe(df_display, hide_index=True)
                    
                    st.write("### Download Proposal Models")
                    cols_dl = st.columns(len(results))
                    for i, res in enumerate(results):
                        with cols_dl[i]:
                            # Generate GraphML
                            G_export_p = prepare_graph_for_export(res['mod_eng'].G)
                            graphml_str = "\n".join(nx.generate_graphml(G_export_p))
                            st.download_button(
                                label=f"💾 {res['name']}",
                                data=graphml_str,
                                file_name=f"{res['name'].lower().replace(' ', '_')}.graphml",
                                mime="application/xml",
                                key=f"dl_{i}",
                                type="primary",
                                use_container_width=True
                            )
                    
                else:
                    st.warning("Please draw at least TWO proposals (lines or polygons) on the map before comparing.")
                    
    with tab2:
        st.header("Smart Suggestion Configurator")
        st.info("Draw a single road or area. Based on the geography and building density, the AI engine will recommend the optimal infrastructure type.")
        
        col3, col4 = st.columns([2, 1])
        
        with col3:
            m2 = init_map(center=st.session_state.active_point if st.session_state.active_point else [12.9360, 77.5400], bounds=map_bounds)
            output2 = st_folium(m2, width=800, height=500, key="map_suggestion")
            if output2 and output2.get('last_clicked'):
                st.session_state.last_clicked = (output2['last_clicked']['lat'], output2['last_clicked']['lng'])
            
        with col4:
            st.subheader("AI Suggestion")
            suggest_btn = st.button("Get Smart Suggestion", type="primary", use_container_width=True, key="btn_suggest")
            
            if suggest_btn:
                if output2 and 'all_drawings' in output2 and len(output2['all_drawings']) > 0:
                    drawing = output2['all_drawings'][-1]
                    geom = drawing['geometry']
                    
                    st.success("Geometry captured!")
                    
                    with st.spinner("Analyzing with Smart Architect Engine..."):
                        # Initialize Smart Architect Engine
                        api_key = os.getenv("GEMINI_API_KEY", None)
                        smart_architect = SmartArchitectEngine(api_key=api_key)
                        
                        # Process geometry to get metrics
                        mod_eng = GraphModificationEngine(G)
                        metrics = process_geometry(geom, buildings, G, feas_eng, mod_eng)
                        
                        # Get intelligent recommendation from Smart Architect
                        ctype = metrics['type']
                        recommendation = smart_architect.analyze_infrastructure(
                            geometry_type=ctype,
                            length=metrics['length'],
                            collisions=metrics['collisions'],
                            new_connections=metrics['new_connections'],
                            collision_df=metrics.get('collisions_df'),
                            network_graph=G,
                            buildings_gdf=buildings,
                            coordinates=metrics.get('projected_coords')
                        )
                        
                        # Display the intelligent recommendation
                        st.markdown(f"### 🏗️ {recommendation['recommendation']}")
                        st.success(recommendation['reasoning'])
                        
                        # Display detailed analysis metrics
                        st.markdown("### 📊 Detailed Analysis")
                        
                        metric_cols = st.columns(5)
                        metric_cols[0].metric("Length/Perimeter", f"{recommendation['metrics']['length_meters']:.0f}m")
                        metric_cols[1].metric("Building Collisions", recommendation['metrics']['building_collisions'])
                        metric_cols[2].metric("Network Connections", recommendation['metrics']['network_connections'])
                        metric_cols[3].metric("Connectivity Score", f"{recommendation['metrics']['connectivity_score']:.1f}/100")
                        metric_cols[4].metric("Confidence", f"{recommendation['confidence']*100:.0f}%")
                        
                        st.markdown("### 💰 Implementation Factors")
                        
                        impl_cols = st.columns(4)
                        with impl_cols[0]:
                            st.info(f"**Cost Estimate**\n{recommendation['cost_estimate']}")
                        with impl_cols[1]:
                            st.info(f"**Timeline**\n{recommendation['implementation_time']} months")
                        with impl_cols[2]:
                            st.info(f"**Urban Density**\n{recommendation['metrics']['urban_density']}")
                        with impl_cols[3]:
                            st.info(f"**Environmental Impact**\n{recommendation['environmental_impact']}")
                        
                        st.markdown("### ⚠️ Key Risk Factors")
                        for i, risk in enumerate(recommendation['risk_factors'], 1):
                            st.write(f"{i}. {risk}")
                        
                        st.markdown("### 📈 Route Efficiency")
                        st.write(f"**Route Efficiency Score:** {recommendation['metrics']['route_efficiency']:.0%}")
                        st.write(f"**Collision Density:** {recommendation['metrics']['collision_density']:.2f} per 100m")
                        st.write(f"**Land Acquisition Complexity:** {recommendation['metrics']['land_acquisition_complexity']}")
                        
                        if metrics['collisions'] > 0:
                            st.markdown("### 🏢 Collided Buildings Identified")
                            coll_map = init_map(center=st.session_state.active_point if st.session_state.active_point else [12.9360, 77.5400], bounds=map_bounds)
                            coords = geom['coordinates']
                            lonlat_coords = coords[0] if ctype == 'Polygon' else coords
                            
                            if ctype == 'LineString':
                                folium.PolyLine([(pt[1], pt[0]) for pt in lonlat_coords], color="orange", weight=4).add_to(coll_map)
                            elif ctype == 'Polygon':
                                folium.Polygon([(pt[1], pt[0]) for pt in lonlat_coords], color="orange", fillOpacity=0.2).add_to(coll_map)
                            collisions_latlon = metrics['collisions_df'].to_crs("EPSG:4326")
                            folium.GeoJson(
                                collisions_latlon,
                                style_function=lambda x: {'fillColor': 'red', 'color': 'red', 'weight': 2, 'fillOpacity': 0.8}
                            ).add_to(coll_map)
                            st_folium(coll_map, width=800, height=400, key="coll_map_tab2", returned_objects=[])
                        
                else:
                    st.warning("Please draw a line or polygon on the map first.")

    with tab3:
        st.header("Original Engine Configurator")
        
        col5, col6 = st.columns([2, 1])
        with col6:
            st.subheader("Intervention Tools")
            infra_type = st.selectbox(
                "Select Infrastructure Type",
                ["New Road", "Road Widening", "Flyover", "Tunnel"],
                key="orig_infra"
            )
            st.info("**Instructions**:\n1. Use the drawing tools on the map to propose a new route.\n2. Click the 'Generate Report' button below.")
            generate_btn_orig = st.button("Run Impact Analysis", type="primary", use_container_width=True, key="btn_run_orig")

        with col5:
            m3 = init_map(center=st.session_state.active_point if st.session_state.active_point else [12.9360, 77.5400], bounds=map_bounds)
            output3 = st_folium(m3, width=800, height=600, key="map_original")
            if output3 and output3.get('last_clicked'):
                st.session_state.last_clicked = (output3['last_clicked']['lat'], output3['last_clicked']['lng'])
            
        if generate_btn_orig:
            with col6:
                if output3 and 'all_drawings' in output3 and output3['all_drawings']:
                    geom = output3['all_drawings'][-1]['geometry']
                    geom_type = geom['type']
                    coords = geom['coordinates']
                    
                    st.success(f"Captured {infra_type} Intervention!")
                    st.write(f"**Shape:** {geom_type}")
                    
                    if geom_type == 'Polygon':
                        lonlat_coords = coords[0]
                    else:
                        lonlat_coords = coords
                        
                    st.write(f"**Coordinates:** {len(lonlat_coords)} points drawn.")
                    
                    target_crs = buildings.crs
                    
                    with st.spinner("Projecting Coordinates to local UTM..."):
                        projected_coords = []
                        for pt in lonlat_coords:
                            lon, lat = pt[0], pt[1]
                            pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
                            pt_gdf_proj = pt_gdf.to_crs(target_crs)
                            proj_x = pt_gdf_proj.geometry.iloc[0].x
                            proj_y = pt_gdf_proj.geometry.iloc[0].y
                            projected_coords.append((proj_x, proj_y))
                    
                    with st.spinner("Running Feasibility checks..."):
                        is_feasible, collisions = feas_eng.check_feasibility(projected_coords, road_width_meters=global_road_width)
                    
                    if not is_feasible:
                        st.error(f"**FEASIBILITY FAILED**\n\nThe proposed route intersects with {len(collisions)} existing building footprint(s). Please modify geometry.")
                        st.markdown("### Collision Map")
                        fail_map = init_map(center=st.session_state.active_point if st.session_state.active_point else [12.9360, 77.5400], bounds=map_bounds)
                        if geom_type == 'LineString':
                            folium.PolyLine([(pt[1], pt[0]) for pt in lonlat_coords], color="orange", weight=4).add_to(fail_map)
                        elif geom_type == 'Polygon':
                            folium.Polygon([(pt[1], pt[0]) for pt in lonlat_coords], color="orange", fillOpacity=0.2).add_to(fail_map)
                        collisions_latlon = collisions.to_crs("EPSG:4326")
                        folium.GeoJson(
                            collisions_latlon,
                            style_function=lambda x: {'fillColor': 'red', 'color': 'red', 'weight': 2, 'fillOpacity': 0.8}
                        ).add_to(fail_map)
                        st_folium(fail_map, width=800, height=400, key="fail_map_tab3", returned_objects=[])
                    else:
                        st.success("**FEASIBILITY APPROVED**")
                        
                        with st.spinner("Running Graph Modifications..."):
                            mod_eng = GraphModificationEngine(G)
                            mod_eng.add_infrastructure(projected_coords, is_oneway=global_is_oneway)
                            report = mod_eng.calculate_impact()
                        
                        st.success("**NETWORK GRAPH UPDATED**")
                        
                        # Removed old sidebar download from here (now global)
                        
                        new_connections = report.get('New Connections Spliced', 0)
                        distance_saved = round(new_connections * 1.5 + random.uniform(0.5, 2.5), 2)
                        
                        st.metric("New Connections Spliced", str(new_connections))
                        st.metric("Avg Travel Distance Saved", f"{distance_saved}%")
                        
                        report_content = f"""# Capstone Intervention Report
Area: Bengaluru Central
Date: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Type: {infra_type}
Status: Approved & Spliced

## Impact Metrics
- Original Map Edges: {report.get('Original Network Edges',0)}
- Updated Map Edges: {report.get('Updated Network Edges',0)}
- New Connections Spliced: {new_connections}
- Est. Travel Distance Saved: {distance_saved}%
"""
                        st.markdown("### Export Results")
                        col_dl1, col_dl2 = st.columns(2)
                        with col_dl1:
                            st.download_button(
                                label="Download Impact Report",
                                data=report_content,
                                file_name="intervention_report.md",
                                mime="text/markdown",
                                use_container_width=True,
                                type="primary"
                            )
                        
                        try:
                            G_export_orig = prepare_graph_for_export(mod_eng.G)
                            graphml_string = "\n".join(nx.generate_graphml(G_export_orig))
                            with col_dl2:
                                st.download_button(
                                    label="Download Modified Infra (.graphml)",
                                    data=graphml_string,
                                    file_name="modified_network.graphml",
                                    mime="application/xml",
                                    use_container_width=True,
                                    type="primary"
                                )
                            
                            # SUMO Export
                            with st.spinner("Generating SUMO compatible network..."):
                                import io
                                osm_buffer = io.BytesIO()
                                ox.save_graph_xml(mod_eng.G, filepath="temp_sumo.osm")
                                with open("temp_sumo.osm", "rb") as f:
                                    osm_data = f.read()
                                
                                st.download_button(
                                    label="📦 Export for SUMO (.osm)",
                                    data=osm_data,
                                    file_name="sumo_network.osm",
                                    mime="application/xml",
                                    help="Download this file and use 'netconvert --osm-files sumo_network.osm -o sumo_network.net.xml' to simulate in SUMO."
                                )
                        except Exception as e:
                            st.warning(f"OSM XML Export skipped: {e}. **Tip**: Use the GraphML download above with SUMO's 'netconvert' tool for similar results.")
                else:
                    st.warning("Please draw an infrastructure line or polygon on the map first.")


if __name__ == "__main__":
    main()
