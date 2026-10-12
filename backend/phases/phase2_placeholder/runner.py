"""
Phase 2 — optimization estimates (widening, smart signals).

Full engine TBD; returns heuristic preview multipliers when options are selected.
"""


import networkx as nx


def run_phase2(request, graph=None, feasibility_result=None):
    options = request.get("options", {})
    intervention = request.get("intervention", {})

    widening = bool(options.get("widening", False))
    smart_signals = bool(options.get("smart_signals", False))
    is_optimize = intervention.get("type") == "optimize"

    bottlenecks = []
    if graph is not None and hasattr(graph, "nodes") and len(graph) > 0:
        try:
            # Sample k nodes for fast betweenness calculation on large OSM graphs
            k_sample = min(len(graph), 50)
            centralities = nx.betweenness_centrality(graph, k=k_sample, normalized=True)
            sorted_nodes = sorted(centralities.items(), key=lambda item: item[1], reverse=True)[:5]
            for node_id, score in sorted_nodes:
                if score <= 0:
                    continue
                node_data = graph.nodes[node_id]
                lat = node_data.get("y") or node_data.get("lat")
                lon = node_data.get("x") or node_data.get("lon")
                bottlenecks.append({
                    "node_id": str(node_id),
                    "centrality_score": round(float(score), 4),
                    "lat": lat,
                    "lon": lon,
                })
        except Exception as exc:
            print(f"[Phase 2] Bottleneck calculation skipped: {exc}")

    if not widening and not smart_signals and not bottlenecks:
        return {
            "phase": 2,
            "status": "skipped",
            "widening_impact": None,
            "signal_optimization": None,
            "bottlenecks": [],
        }

    result = {
        "phase": 2,
        "status": "heuristic_preview" if (widening or smart_signals) else "graph_analyzed",
        "note": "Phase 2 optimization preview with NetworkX graph bottleneck centrality.",
        "widening_impact": None,
        "signal_optimization": None,
        "bottlenecks": bottlenecks,
    }

    if widening:
        result["widening_impact"] = {
            "estimated_congestion_reduction_pct": 12,
            "lane_capacity_multiplier": 1.25,
            "summary": "Road widening could add ~25% lane capacity on the corridor.",
        }

    if smart_signals:
        result["signal_optimization"] = {
            "estimated_time_reduction_pct": 8,
            "junction_delay_reduction_pct": 15,
            "summary": "Adaptive signal timing could cut junction delays on the route.",
        }

    if is_optimize and not widening and not smart_signals:
        result["status"] = "graph_analyzed"
        result["note"] = f"Identified {len(bottlenecks)} high-centrality bottleneck junctions on the corridor graph."

    return result

