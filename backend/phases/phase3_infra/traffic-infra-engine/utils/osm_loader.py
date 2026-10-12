import math
import re
import sys
import time
import math
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd

_backend_root = Path(__file__).resolve().parents[4]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))
from paths import CACHE_DIR

# Enable OSMnx's built-in HTTP request cache
ox.settings.use_cache = True
ox.settings.requests_timeout = 3000  # Increase timeout to 50 minutes for massive queries

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
)


def _overpass_request(operation, description):
    """Run an OSMnx Overpass request with endpoint failover."""
    original_endpoint = ox.settings.overpass_url
    errors = []
    try:
        for endpoint in OVERPASS_ENDPOINTS:
            ox.settings.overpass_url = endpoint
            for attempt in range(2):
                try:
                    print(f"[OSM Loader] Trying {description} via {endpoint} (attempt {attempt + 1}/2)...")
                    return operation()
                except Exception as exc:
                    errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
                    if attempt == 0:
                        time.sleep(1)
    finally:
        ox.settings.overpass_url = original_endpoint

    raise RuntimeError(
        f"Unable to download {description} from the Overpass mirrors. "
        "The OSM service may be unavailable or the network may be blocking it. "
        f"Last error: {errors[-1] if errors else 'unknown error'}"
    )


def _nearest_point_cache(point, dist, suffix):
    """Find a cached point-area dataset that covers the same corridor area."""
    if not point:
        return None
    candidates = []
    marker = f"_{dist}m_{suffix}"
    for path in CACHE_DIR.glob(f"point_*{marker}"):
        stem = path.name
        if not stem.startswith("point_") or marker not in stem:
            continue
        coordinates = stem[len("point_"):].split(marker, 1)[0].split("_")
        if len(coordinates) != 2:
            continue
        try:
            cached_point = (float(coordinates[0]), float(coordinates[1]))
        except ValueError:
            continue
        distance = math.hypot(cached_point[0] - point[0], cached_point[1] - point[1])
        candidates.append((distance, path))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None

# A cached area is reused for a new request when the request's centre lies
# this far inside it, so everything drawn around the corridor stays covered.
COVERAGE_MARGIN_M = 1500

_CACHE_NAME = re.compile(r"^point_(-?\d+\.\d+)_(-?\d+\.\d+)_(\d+)m_(drive\.graphml|buildings\.geojson)$")

# The cached files are large (the building footprints are hundreds of MB),
# so keep the most recently loaded ones in memory between analyses.
_memory_cache = {}


def _distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _covering_cache(point, dist, suffix):
    """Nearest cached file of this kind whose area contains the requested one."""
    best = None
    for path in CACHE_DIR.glob(f"point_*_{suffix}"):
        match = _CACHE_NAME.match(path.name)
        if not match:
            continue
        lat, lon, cached_dist = float(match.group(1)), float(match.group(2)), int(match.group(3))
        # Analyses only touch the corridor around the request centre, so the
        # centre sitting well inside the cached circle is enough.
        offset = _distance_m(point[0], point[1], lat, lon)
        if cached_dist < dist - COVERAGE_MARGIN_M or offset > cached_dist - COVERAGE_MARGIN_M:
            continue
        if best is None or offset < best[0]:
            best = (offset, path)
    return best[1] if best else None


def _cached_path(point, dist, safe_name, suffix):
    exact = CACHE_DIR / f"{safe_name}_{suffix}"
    if exact.exists() or not point:
        return exact
    covering = _covering_cache(point, dist, suffix)
    if covering is not None:
        print(f"[OSM Loader] Reusing cached area {covering.name}, which covers {point}.")
        return covering
    return exact


def _download_failed(kind, point, place_name, exc):
    where = point if point else place_name
    return RuntimeError(
        f"Could not download the {kind} around {where} from OpenStreetMap (Overpass API): {exc}. "
        "No cached map covers this area yet. Check that overpass-api.de is reachable from this "
        "network, or pick a corridor inside an area that has already been analysed."
    )


def load_road_network(place_name="Kathriguppe, Bangalore, India", point=None, dist=5000):
    """
    Downloads the drivable road network, caching it to disk to prevent massive API loads on restart.
    Supports either place_name or (lat, lon) point.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if point:
        safe_name = f"point_{point[0]:.4f}_{point[1]:.4f}_{dist}m"
        print(f"[OSM Loader] ℹ️ Processing request for coordinates: {point} with {dist}m radius")
    else:
        safe_name = f"{place_name.replace(', ', '_').replace(' ', '_').lower()}_{dist}m"
        print(f"[OSM Loader] ℹ️ Processing request for place: {place_name} with {dist}m radius")

    graph_path = _cached_path(point, dist, safe_name, "drive.graphml")

    if graph_path in _memory_cache:
        print(f"[OSM Loader] Using in-memory road network: {graph_path.name}")
        return _memory_cache[graph_path]

    if graph_path.exists():
        print(f"[OSM Loader] ⚡ Loading road network from local disk cache: {graph_path}")
        G_proj = ox.load_graphml(graph_path)
        print(f"Loaded Graph: {len(G_proj.nodes)} nodes, {len(G_proj.edges)} edges.")
        _remember(graph_path, G_proj, "drive.graphml")
        return G_proj

    nearby_graph_path = _nearest_point_cache(point, dist, "drive.graphml")
    if nearby_graph_path:
        print(f"[OSM Loader] ⚡ Exact cache unavailable; using nearby cached road network: {nearby_graph_path}")
        G_proj = ox.load_graphml(nearby_graph_path)
        print(f"Loaded Graph: {len(G_proj.nodes)} nodes, {len(G_proj.edges)} edges.")
        return G_proj

    if point:
        print(f"[OSM Loader] 🌐 Downloading road network around {point} from Overpass API...")
        try:
            G = _overpass_request(
                lambda: ox.graph_from_point(point, dist=dist, network_type='drive'),
                f"the road network around {point}",
            )
        except Exception as exc:
            raise _download_failed("road network", point, place_name, exc) from exc
    else:
        print(f"[OSM Loader] 🌐 Downloading road network around {place_name} from Overpass API...")
        try:
            G = _overpass_request(
                lambda: ox.graph_from_address(place_name, dist=dist, network_type='drive'),
                f"the road network around {place_name}",
            )
        except Exception as exc:
            raise _download_failed("road network", point, place_name, exc) from exc
    # Project graph to UTM local CRS
    G_proj = ox.project_graph(G)

    print(f"[OSM Loader] 💾 Saving road network to local disk cache...")
    ox.save_graphml(G_proj, graph_path)
    print(f"Loaded Graph: {len(G_proj.nodes)} nodes, {len(G_proj.edges)} edges.")
    _remember(graph_path, G_proj, "drive.graphml")

    return G_proj


def _remember(path, value, kind):
    """Keep only the latest loaded file of each kind in memory."""
    for cached in [key for key in _memory_cache if key.name.endswith(kind)]:
        del _memory_cache[cached]
    _memory_cache[path] = value

def load_buildings(place_name="Kathriguppe, Bangalore, India", point=None, dist=5000):
    """
    Downloads building footprints for the specified radius.
    Supports either place_name or (lat, lon) point.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if point:
        safe_name = f"point_{point[0]:.4f}_{point[1]:.4f}_{dist}m"
    else:
        safe_name = f"{place_name.replace(', ', '_').replace(' ', '_').lower()}_{dist}m"

    buildings_path = _cached_path(point, dist, safe_name, "buildings.geojson")

    if buildings_path in _memory_cache:
        print(f"[OSM Loader] Using in-memory building footprints: {buildings_path.name}")
        return _memory_cache[buildings_path]

    if buildings_path.exists():
        print(f"[OSM Loader] ⚡ Loading building footprints from local disk cache: {buildings_path}")
        buildings_proj = gpd.read_file(buildings_path, engine="fiona")
        print(f"Loaded {len(buildings_proj)} building footprints.")
        _remember(buildings_path, buildings_proj, "buildings.geojson")
        return buildings_proj

    nearby_buildings_path = _nearest_point_cache(point, dist, "buildings.geojson")
    if nearby_buildings_path:
        print(f"[OSM Loader] ⚡ Exact cache unavailable; using nearby cached building footprints: {nearby_buildings_path}")
        buildings_proj = gpd.read_file(nearby_buildings_path, engine="fiona")
        print(f"Loaded {len(buildings_proj)} building footprints.")
        return buildings_proj

    tags = {'building': True}
    if point:
        print(f"[OSM Loader] 🌐 Downloading building footprints around {point} from Overpass API...")
        try:
            buildings = _overpass_request(
                lambda: ox.features_from_point(point, tags=tags, dist=dist),
                f"building footprints around {point}",
            )
        except Exception as exc:
            raise _download_failed("building footprints", point, place_name, exc) from exc
    else:
        print(f"[OSM Loader] 🌐 Downloading building footprints around {place_name} from Overpass API...")
        try:
            buildings = _overpass_request(
                lambda: ox.features_from_address(place_name, tags=tags, dist=dist),
                f"building footprints around {place_name}",
            )
        except Exception as exc:
            raise _download_failed("building footprints", point, place_name, exc) from exc
    # Project to UTM CRS
    buildings_proj = buildings.to_crs(buildings.estimate_utm_crs())

    print(f"[OSM Loader] 💾 Saving building footprints to local disk cache (GeoJSON)...")
    buildings_proj.to_file(buildings_path, driver="GeoJSON", engine="fiona")
    print(f"Loaded {len(buildings_proj)} building footprints.")
    _remember(buildings_path, buildings_proj, "buildings.geojson")

    return buildings_proj
