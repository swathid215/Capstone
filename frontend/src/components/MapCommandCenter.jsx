import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { MapContainer, TileLayer, FeatureGroup, GeoJSON, Pane, useMap, useMapEvents, Marker, Polyline } from 'react-leaflet';
import { EditControl } from 'react-leaflet-draw';
import L from 'leaflet';
import * as turf from '@turf/turf';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw/dist/leaflet.draw.css';
import { apiUrl } from '../api';

// Custom Icons
const CORRIDOR_CENTER = [12.924135, 77.55614];
const CORRIDOR_BOUNDS = [[12.91782, 77.54425], [12.93045, 77.56803]];
const CORRIDOR_MIN_LAT = CORRIDOR_BOUNDS[0][0];
const CORRIDOR_MAX_LAT = CORRIDOR_BOUNDS[1][0];
const CORRIDOR_MIN_LON = CORRIDOR_BOUNDS[0][1];
const CORRIDOR_MAX_LON = CORRIDOR_BOUNDS[1][1];

function isWithinCorridorBounds(lat, lon) {
  return (
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    lat >= CORRIDOR_MIN_LAT &&
    lat <= CORRIDOR_MAX_LAT &&
    lon >= CORRIDOR_MIN_LON &&
    lon <= CORRIDOR_MAX_LON
  );
}

const ROUTE_STYLES = {
  1: { color: '#2563eb', label: 'Route 1' },
  2: { color: '#0f766e', label: 'Route 2' },
  3: { color: '#d97706', label: 'Route 3' },
  4: { color: '#ec4899', label: 'Route 4 · New path' },
};
const ADOPTION_RATES = [25, 50, 75];

const PROPOSED_INFRA_COLOR = '#7c3aed';
const PROPOSED_ROUTE_COLOR = '#ec4899';
const LANE_OPTIONS = [1, 2, 3, 4, 6];

function createPointIcon(label, variant) {
  const color = variant === 'start' ? '#2563eb' : '#ea580c';
  return L.divIcon({
    className: 'corridor-point-icon-wrapper',
    // The anchor is the bottom tip, so the pin lands exactly on the click.
    html: `<svg class="corridor-point-icon" width="40" height="48" viewBox="0 0 40 48" aria-hidden="true">
      <path d="M20 46C17.5 41.5 4 30.5 4 20A16 16 0 1 1 36 20C36 30.5 22.5 41.5 20 46Z" fill="${color}" stroke="white" stroke-width="3"/>
      <text x="20" y="25" text-anchor="middle" fill="white" font-size="13" font-weight="900" font-family="Arial, sans-serif">${label}</text>
    </svg>`,
    iconSize: [40, 48],
    iconAnchor: [20, 46],
  });
}

function createRouteArrowIcon(color, angle) {
  return L.divIcon({
    className: 'route-direction-icon-wrapper',
    html: `<span class="route-direction-arrow" style="color:${color}; transform: rotate(${angle}deg)">➜</span>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
}

function createInfrastructureLabelIcon(serialNumber, isSelected) {
  return L.divIcon({
    className: 'infrastructure-label-icon-wrapper',
    html: `<span class="infrastructure-label-icon ${isSelected ? 'is-selected' : ''}">#${serialNumber}</span>`,
    iconSize: [34, 22],
    iconAnchor: [17, 11],
  });
}

function getRouteDirectionCue(positions) {
  if (!positions || positions.length < 2) return null;
  const index = Math.max(0, Math.floor((positions.length - 1) / 2));
  const current = positions[index];
  const next = positions[Math.min(index + 1, positions.length - 1)];
  const angle = Math.atan2(-(next[0] - current[0]), next[1] - current[1]) * (180 / Math.PI);
  return { position: current, angle };
}

const START_ICON = createPointIcon('A', 'start');
const END_ICON = createPointIcon('B', 'end');

function heatmapColor(value, mode) {
  if (mode === 'difference') {
    if (value <= -0.2) return '#15803d';
    if (value <= -0.05) return '#22c55e';
    if (value < 0.05) return '#94a3b8';
    if (value < 0.2) return '#f97316';
    return '#b91c1c';
  }

  if (value < 0.05) return '#94a3b8';
  if (value < 0.2) return '#22c55e';
  if (value < 0.45) return '#facc15';
  if (value < 0.7) return '#f97316';
  return '#dc2626';
}

function heatmapNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function heatmapValue(properties, mode) {
  if (mode === 'difference') {
    return heatmapNumber(properties.delta_congestion);
  }
  return heatmapNumber(properties.congestion ?? properties.intensity);
}

function heatmapSpeed(properties, mode) {
  if (mode === 'before') {
    return heatmapNumber(properties.before_speed_kmh ?? properties.avg_speed_kmh);
  }
  return heatmapNumber(properties.after_speed_kmh ?? properties.avg_speed_kmh);
}

function escapeTooltipHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[character]));
}

function TrafficHeatmapLayer({ featureCollection, mode, scenarioKey, focusedEdgeId, onFocusEdge, hideLowConfidence = false }) {
  if (!featureCollection?.features?.length) return null;

  // Optionally filter out low-confidence edges
  const filtered = hideLowConfidence
    ? { ...featureCollection, features: featureCollection.features.filter((f) => f?.properties?.confidence) }
    : featureCollection;

  const style = (feature) => {
    const properties = feature?.properties || {};
    const value = heatmapValue(properties, mode);
    const magnitude = mode === 'difference' ? Math.min(1, Math.abs(value)) : Math.max(0.05, value);
    const isFocused = focusedEdgeId && properties.edge_id === focusedEdgeId;
    return {
      color: heatmapColor(value, mode),
      weight: isFocused ? 8 : 2 + magnitude * 5,
      opacity: isFocused ? 1 : 0.88,
      lineCap: 'round',
      lineJoin: 'round',
    };
  };

  const onEachFeature = (feature, layer) => {
    const properties = feature?.properties || {};
    const metric = heatmapValue(properties, mode);
    const value = mode === 'difference'
      ? `${(metric * 100).toFixed(1)} percentage points`
      : `${(metric * 100).toFixed(1)}% congestion`;
    const roadName = escapeTooltipHtml(properties.road_name || properties.edge_id || 'Road segment');
    const edgeId = escapeTooltipHtml(properties.edge_id || 'unknown');
    const coveragePct = (Number(properties.coverage || 0) * 100).toFixed(1);
    const confidenceText = properties.confidence ? 'High' : 'Low';
    layer.bindTooltip(
      `<strong>${roadName}</strong><br/>Edge: ${edgeId}<br/>${value}<br/>Speed: ${heatmapSpeed(properties, mode).toFixed(1)} km/h<br/>Coverage: ${coveragePct}%<br/>Confidence: ${confidenceText}`,
      { sticky: true },
    );
    layer.on('click', () => {
      const coordinates = feature?.geometry?.coordinates || [];
      const midpoint = coordinates[Math.floor(coordinates.length / 2)] || [];
      onFocusEdge?.({
        ...properties,
        lon: Number(properties.lon ?? midpoint[0]),
        lat: Number(properties.lat ?? midpoint[1]),
      });
    });
  };

  return (
    <GeoJSON
      key={`${scenarioKey || 'base'}-${mode}-${featureCollection.features.length}-${featureCollection.observed_timestep_count || 0}-${focusedEdgeId || 'none'}`}
      data={featureCollection}
      style={style}
      onEachFeature={onEachFeature}
    />
  );
}

function HeatmapFocusController({ focus }) {
  const map = useMap();

  useEffect(() => {
    if (!focus || !Number.isFinite(Number(focus.lat)) || !Number.isFinite(Number(focus.lon))) {
      return;
    }
    map.setView(
      [Number(focus.lat), Number(focus.lon)],
      Math.max(map.getZoom(), 17),
      { animate: true },
    );
  }, [focus, map]);

  return null;
}

function CollisionBoundsController({ featureCollection }) {
  const map = useMap();

  useEffect(() => {
    if (!featureCollection?.features?.length) return undefined;
    const fitCollisions = () => {
      if (!map.getSize().x || !map.getSize().y) return;
      const bounds = L.geoJSON(featureCollection).getBounds();
      if (bounds.isValid() && !map.getBounds().contains(bounds)) {
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 18 });
      }
    };
    fitCollisions();
    map.on('resize', fitCollisions);
    return () => map.off('resize', fitCollisions);
  }, [featureCollection, map]);

  return null;
}

function CollisionBuildingsLayer({ featureCollection, visible }) {
  if (!featureCollection?.features?.length) return null;

  const firstProperties = featureCollection.features[0]?.properties || {};
  const layerKey = `${featureCollection.features.length}-${firstProperties.id ?? ''}`;
  const onEachFeature = (feature, layer) => {
    const properties = feature.properties || {};
    const name = escapeTooltipHtml(properties.name || properties.building || 'Building');
    const element = escapeTooltipHtml(properties.element || 'way');
    const id = escapeTooltipHtml(properties.id ?? '');
    layer.bindTooltip(
      `<strong>Collides with proposed road</strong><br/>${name} (OSM ${element} ${id})`,
      { sticky: true },
    );
  };

  return (
    <>
      <CollisionBoundsController featureCollection={featureCollection} />
      {visible && (
        <Pane name="collision-buildings" style={{ zIndex: 450 }}>
          <GeoJSON
            key={layerKey}
            data={featureCollection}
            pane="collision-buildings"
            style={() => ({ color: '#b91c1c', weight: 2, fillColor: '#ef4444', fillOpacity: 0.65 })}
            onEachFeature={onEachFeature}
          />
        </Pane>
      )}
    </>
  );
}

function countUniqueCollisionBuildings(featureCollection) {
  const features = featureCollection?.features || [];
  const ids = new Set();
  features.forEach((feature, index) => {
    const properties = feature?.properties || {};
    const id = properties.id;
    ids.add(id === undefined || id === null ? `feature-${index}` : `${properties.element || 'way'}-${id}`);
  });
  return ids.size;
}

function HeatmapViewportController({ active, bbox }) {
  const map = useMap();

  useEffect(() => {
    if (active && Array.isArray(bbox) && bbox.length === 4) {
      const [west, south, east, north] = bbox;
      map.setMaxBounds(null);
      map.setMinZoom(11);
      map.fitBounds([[south, west], [north, east]], { padding: [20, 20] });
      return;
    }

    map.setMinZoom(15);
    map.setMaxBounds(CORRIDOR_BOUNDS);
    map.fitBounds(CORRIDOR_BOUNDS, { padding: [24, 24] });
  }, [active, bbox, map]);

  return null;
}

function MapResizeController({ trigger }) {
  const map = useMap();

  useEffect(() => {
    const container = map.getContainer();
    let firstFrame;
    let secondFrame;
    const resizeMap = () => map.invalidateSize({ animate: false });

    firstFrame = window.requestAnimationFrame(() => {
      resizeMap();
      secondFrame = window.requestAnimationFrame(resizeMap);
    });

    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(resizeMap);
    observer?.observe(container);

    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (secondFrame) window.cancelAnimationFrame(secondFrame);
      observer?.disconnect();
    };
  }, [map, trigger]);

  return null;
}

function MapSyncController({ onSync }) {
  const map = useMapEvents({
    moveend: () => {
      onSync?.({ center: map.getCenter(), zoom: map.getZoom() });
    },
    zoomend: () => {
      onSync?.({ center: map.getCenter(), zoom: map.getZoom() });
    },
  });
  return null;
}

function SyncedViewSetter({ syncState }) {
  const map = useMap();
  useEffect(() => {
    if (syncState?.center && syncState?.zoom) {
      map.setView(syncState.center, syncState.zoom, { animate: false });
    }
  }, [syncState, map]);
  return null;
}

function CorridorPointPicker({ onPick }) {
  useMapEvents({ click: (event) => onPick(event.latlng) });
  return null;
}

// Helper to move map view
function MapController({ center, zoom, bounds }) {
  const map = useMap();
  const hasAppliedInitialBounds = useRef(false);

  useEffect(() => {
    if (bounds && !hasAppliedInitialBounds.current) {
      map.fitBounds(bounds, { padding: [24, 24] });
      hasAppliedInitialBounds.current = true;
      return;
    }

    if (center) map.flyTo(center, zoom || 16);
  }, [bounds, center, zoom, map]);
  return null;
}

function TimeField({ label, value, onChange, minHour, maxHour }) {
  const [hour = '09'] = value.split(':');
  const hours = Array.from(
    { length: maxHour - minHour + 1 },
    (_, index) => String(minHour + index).padStart(2, '0'),
  );

  return (
    <div className="time-field">
      <label className="text-xs font-bold text-gray-500 uppercase mb-1 block">{label}</label>
      <div className="time-selects">
        <select
          className="time-select"
          value={hour}
          onChange={(event) => onChange(`${event.target.value}:00`)}
          aria-label={`${label} hour`}
        >
          {hours.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </div>
    </div>
  );
}

const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function DayOfWeekField({ label = 'Day of week', value, onChange }) {
  return (
    <div className="day-field">
      <label className="text-xs font-bold text-gray-500 uppercase mb-1 block">{label}</label>
      <select
        className="time-select w-full"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label={label}
      >
        {DAYS_OF_WEEK.map((day) => <option key={day} value={day}>{day}</option>)}
      </select>
    </div>
  );
}

function CoordinateEditor({ label, point, onChange, onPick }) {
  const [editing, setEditing] = useState(false);
  const [choiceMade, setChoiceMade] = useState(Boolean(point));
  const [draft, setDraft] = useState({ lat: point?.lat ?? '', lon: point?.lon ?? '' });
  const [error, setError] = useState('');

  useEffect(() => {
    setDraft({ lat: point?.lat ?? '', lon: point?.lon ?? '' });
    setChoiceMade(Boolean(point));
  }, [point?.lat, point?.lon]);

  function save() {
    const lat = Number(draft.lat);
    const lon = Number(draft.lon);

    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      setError('Enter valid latitude and longitude values.');
      return;
    }

    if (!isWithinCorridorBounds(lat, lon)) {
      setError(`Coordinates must stay within the corridor bounds: lat ${CORRIDOR_MIN_LAT.toFixed(5)}–${CORRIDOR_MAX_LAT.toFixed(5)}, lon ${CORRIDOR_MIN_LON.toFixed(5)}–${CORRIDOR_MAX_LON.toFixed(5)}.`);
      return;
    }

    setError('');
    onChange({ lat, lon, name: `${lat.toFixed(5)}, ${lon.toFixed(5)}` });
    setEditing(false);
    setChoiceMade(true);
  }

  const summary = point ? `${Number(point.lat).toFixed(6)}, ${Number(point.lon).toFixed(6)}` : 'No coordinates selected';

  return (
    <div className="coordinate-editor" onClick={(event) => event.stopPropagation()}>
      <div className="coordinate-editor-heading">
        <span>{label}</span>
        {!editing && point && <span className="coordinate-editor-hint">Choose an action below</span>}
      </div>
      {editing || !choiceMade ? (
        <div className="coordinate-editor-fields">
          {!editing && !choiceMade ? (
            <div className="coordinate-choice-row">
              <button type="button" onClick={onPick}>Pick on map</button>
              <button type="button" onClick={() => { setEditing(true); setChoiceMade(true); }}>Enter coordinates</button>
            </div>
          ) : (
            <>
              <label>Latitude<input type="number" step="any" value={draft.lat} onChange={(event) => setDraft((current) => ({ ...current, lat: event.target.value }))} /></label>
              <label>Longitude<input type="number" step="any" value={draft.lon} onChange={(event) => setDraft((current) => ({ ...current, lon: event.target.value }))} /></label>
              <button type="button" onClick={save}>Save</button>
              <button type="button" onClick={() => { setEditing(false); setError(''); setChoiceMade(Boolean(point)); }}>Cancel</button>
            </>
          )}
        </div>
      ) : (
        <>
          <div className="coordinate-editor-value">{summary}</div>
          <div className="coordinate-action-row">
            <button type="button" onClick={() => setEditing(true)}>Edit coordinates</button>
            <button type="button" onClick={onPick}>Pick on map</button>
          </div>
        </>
      )}
      {error && <div className="coordinate-editor-error">{error}</div>}
    </div>
  );
}

function InfrastructureCoordinates({ item, onChange }) {
  const [open, setOpen] = useState(false);
  const coordinates = item.geometry?.coordinates || [];

  function updatePoint(index, axis, value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return;
    const nextCoordinates = coordinates.map((coordinate, coordinateIndex) => {
      if (coordinateIndex !== index) return coordinate;
      const next = [...coordinate];
      next[axis] = number;
      return next;
    });
    onChange({ ...item.geometry, coordinates: nextCoordinates });
  }

  return (
    <div className="infrastructure-coordinates" onDoubleClick={() => setOpen(true)}>
      <button type="button" className="coordinate-toggle" onClick={() => setOpen((current) => !current)}>
        Coordinates ({coordinates.length}) {open ? '▴' : '▾'}
      </button>
      {open && (
        <div className="coordinate-list">
          {coordinates.length === 0 && <span className="text-xs text-gray-500">No vertices recorded.</span>}
          {coordinates.map((coordinate, index) => (
            <div className="coordinate-row" key={`${item.id}-coordinate-${index}`}>
              <span>Point {index + 1}</span>
              <label>Lat<input type="number" step="any" value={coordinate[1] ?? ''} onChange={(event) => updatePoint(index, 1, event.target.value)} /></label>
              <label>Lon<input type="number" step="any" value={coordinate[0] ?? ''} onChange={(event) => updatePoint(index, 0, event.target.value)} /></label>
            </div>
          ))}
          <span className="coordinate-editor-hint">Double-click this section to open coordinate editing.</span>
        </div>
      )}
    </div>
  );
}

const NEW_ALIGNMENT_TYPES = ['flyover', 'tunnel'];
const SUPPORTED_INFRA_TYPES = ['flyover', 'tunnel', 'road_block', 'widening'];
const MAX_COMPARE_ITEMS = 3;
const LANE_WIDTH_M = 3.5;

function widthToLaneCount(width, fallback = 2) {
  const value = Number(width);
  return Number.isFinite(value) && value > 0 ? Math.max(1, Math.round(value / LANE_WIDTH_M)) : fallback;
}

function laneFieldLabel(infraType) {
  if (infraType === 'widening') return 'Additional lanes';
  if (infraType === 'road_block') return 'Affected lanes';
  return 'Total lanes';
}

function laneOptionsForDirection(isOneway) {
  return isOneway ? LANE_OPTIONS : LANE_OPTIONS.filter((lanes) => lanes >= 2);
}
const INFRA_TYPE_LABELS = { flyover: 'Flyover', tunnel: 'Tunnel', road_block: 'Road Block', widening: 'Road Widening' };

function infraTypesForMode(mode) {
  return SUPPORTED_INFRA_TYPES;
}

function clampPercent(value) {
  return Math.min(100, Math.max(0, Number(value) || 0));
}

export default function MapCommandCenter({ onGenerate, onAnalyzingChange, onReset, onViewReport, projectId = null, activeProject = null, userEmail = '', compareVariantArtifacts = null }) {
  // Helpers
  const editRef = useRef(null);
  const drawnGroupRef = useRef(null);

  // --- STATE ---
  const [step, setStep] = useState(1); // 1: Route, 2: Intervention, 3: Simulate
  const [mapCenter, setMapCenter] = useState(CORRIDOR_CENTER);
  const [statusMsg, setStatusMsg] = useState('Click the corridor map to select an origin.');

  // Step 1: Route
  const [startLoc, setStartLoc] = useState(null); // { lat, lon, name }
  const [endLoc, setEndLoc] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchingStart, setIsSearchingStart] = useState(true); // true=Start, false=End
  const [searchResults, setSearchResults] = useState([]);

  // Step 2: Intervention
  const [interventionType, setInterventionType] = useState('infrastructure'); // 'infrastructure' | 'optimize'
  const [infraType, setInfraType] = useState('flyover'); // 'flyover' | 'tunnel'
  const [drawMethod, setDrawMethod] = useState(null); // 'auto' | 'manual'
  const [drawnFeature, setDrawnFeature] = useState(null); // GeoJSON
  const [infraItems, setInfraItems] = useState([]);
  const [selectedEdges, setSelectedEdges] = useState([]);
  const [selectedInfraId, setSelectedInfraId] = useState(null);
  const [activeInfraIds, setActiveInfraIds] = useState([]);
  const [isAddingInfrastructure, setIsAddingInfrastructure] = useState(false);
  const [draftInfrastructure, setDraftInfrastructure] = useState(null);
  // 'package': items are one project built together; 'compare': up to 3
  // independent options ranked against each other.
  const [projectMode, setProjectMode] = useState('package');
  const [packageAdoption, setPackageAdoption] = useState(75);

  const [optimizeOptions, setOptimizeOptions] = useState({
    widening: false,
    signal: false
  });

  // Step 3: Simulation Config
  const [email, setEmail] = useState(userEmail);
  const [startTime, setStartTime] = useState('09:00');
  const [endTime, setEndTime] = useState('10:00');
  const [dayOfWeek, setDayOfWeek] = useState('Monday');
  const [editingEmail, setEditingEmail] = useState(false);
  const [backendOnline, setBackendOnline] = useState(null);
  const [simulationResult, setSimulationResult] = useState(null);

  const [routes, setRoutes] = useState([]);
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [selectedRouteKey, setSelectedRouteKey] = useState(null);
  const [focusedRouteKey, setFocusedRouteKey] = useState(null);
  const [heatmap, setHeatmap] = useState(null);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [isHeatmapCollapsed, setIsHeatmapCollapsed] = useState(false);
  const [heatmapMode, setHeatmapMode] = useState('difference');
  const [heatmapAdoption, setHeatmapAdoption] = useState(null);
  const [hotspotView, setHotspotView] = useState('worsened');
  const [hideLowConfidenceEdges, setHideLowConfidenceEdges] = useState(false);
  const [focusedHeatmapEdgeId, setFocusedHeatmapEdgeId] = useState(null);
  const [focusedHeatmapLocation, setFocusedHeatmapLocation] = useState(null);
  const [alternativeCandidates, setAlternativeCandidates] = useState([]);
  const [selectedAlternative, setSelectedAlternative] = useState(null);
  const [alternativesLoading, setAlternativesLoading] = useState(false);
  const [showAlternatives, setShowAlternatives] = useState(false);
  const [simulationRunning, setSimulationRunning] = useState(false);
  const [analysisRunning, setAnalysisRunning] = useState(false);
  const [analysisProgress, setAnalysisProgress] = useState(0);
  const [analysisComplete, setAnalysisComplete] = useState(false);
  const [analysisMetrics, setAnalysisMetrics] = useState(null);
  const [editReturnSignature, setEditReturnSignature] = useState(null);
  const [collisionGeoJSON, setCollisionGeoJSON] = useState(null);
  const collisionRequestRef = useRef(0);
  const [isMapFocusMode, setIsMapFocusMode] = useState(false);
  const [isSplitView, setIsSplitView] = useState(false);
  const [syncedMapState, setSyncedMapState] = useState(null);
  const [showLayerControls, setShowLayerControls] = useState(false);
  const [visibleLayers, setVisibleLayers] = useState({
    existingRoutes: true,
    proposedRoute: true,
    endpoints: true,
    collisions: true,
  });

  const loadCollisions = useCallback(async (artifacts) => {
    const requestId = ++collisionRequestRef.current;
    const url = artifacts?.collisions_geojson;
    if (!url) {
      setCollisionGeoJSON(null);
      return;
    }

    try {
      const response = await fetch(apiUrl(url));
      if (!response.ok) throw new Error(`Collision GeoJSON request failed (${response.status})`);
      const data = await response.json();
      if (requestId === collisionRequestRef.current) setCollisionGeoJSON(data);
    } catch (error) {
      if (requestId === collisionRequestRef.current) setCollisionGeoJSON(null);
      console.warn('Unable to load collision GeoJSON:', error);
    }
  }, []);

  useEffect(() => {
    loadCollisions(compareVariantArtifacts);
  }, [compareVariantArtifacts, loadCollisions]);

  useEffect(() => {
    setEmail(userEmail || '');
  }, [userEmail]);

  useEffect(() => {
    collisionRequestRef.current += 1;
    setCollisionGeoJSON(null);
    const savedRoute = activeProject?.route;
    const savedInterventions = activeProject?.interventions || [];
    const savedScenario = activeProject?.scenario || {};
    const savedReport = activeProject?.report;
    const hasStart = savedRoute?.start?.lat !== undefined && savedRoute?.start?.lon !== undefined;
    const hasEnd = savedRoute?.end?.lat !== undefined && savedRoute?.end?.lon !== undefined;

    if (hasStart) setStartLoc(savedRoute.start);
    if (hasEnd) setEndLoc(savedRoute.end);
    if (hasStart && hasEnd) setMapCenter([
      (Number(savedRoute.start.lat) + Number(savedRoute.end.lat)) / 2,
      (Number(savedRoute.start.lon) + Number(savedRoute.end.lon)) / 2,
    ]);
    if (savedScenario.start_time) setStartTime(savedScenario.start_time);
    if (savedScenario.end_time) setEndTime(savedScenario.end_time);
    if (savedScenario.day_of_week) setDayOfWeek(savedScenario.day_of_week);
    setProjectMode(savedScenario.project_mode === 'compare' ? 'compare' : 'package');
    if (savedScenario.adoption_rate_pct !== undefined) setPackageAdoption(savedScenario.adoption_rate_pct);
    if (savedInterventions.length > 0) {
      const restoredItems = savedInterventions.map((item, index) => ({
        ...item,
        id: item.id || `restored-${index}`,
        serial_number: item.serial_number || index + 1,
        infra_type: item.infra_type || 'flyover',
        geometry: item.geometry || { type: 'LineString', coordinates: [] },
        active: item.active !== false,
      }));
      setInfraItems(restoredItems);
      setActiveInfraIds(restoredItems.filter((item) => item.active).map((item) => item.id));
      const primary = restoredItems[0];
      setSelectedInfraId(primary.id);
      setInfraType(primary.infra_type);
      setDrawMethod('manual');
      setDrawnFeature({ type: 'Feature', geometry: primary.geometry, properties: {} });
    }
    if (savedReport && Object.keys(savedReport).length > 0) {
      // A comparison report keeps one report per option; the map shows the best one.
      const baseReport = savedReport.projectMode === 'compare'
        ? (savedReport.variants || []).find((variant) => variant.key === savedReport.overallSummary?.winnerKey)?.report
          || (savedReport.variants || []).find((variant) => variant.report)?.report
          || {}
        : savedReport;
      loadCollisions(baseReport.artifacts);
      const simulation = baseReport.simulation || {};
      const feasibility = baseReport.feasibility || {};
      const impact = baseReport.impact || {};
      const metrics = baseReport.metrics || {};
      const restoredRoutes = simulation.routes || baseReport.routes || [];
      setRoutes(restoredRoutes.filter((route) => route?.geometry?.coordinates?.length >= 2));
      const restoredHeatmap = simulation.heatmap || null;
      const restoredAdoptions = Object.keys(restoredHeatmap?.scenarios || {});
      setHeatmap(restoredHeatmap);
      setHeatmapAdoption(String(
        restoredHeatmap?.selected_adoption_rate_pct ?? restoredAdoptions[restoredAdoptions.length - 1] ?? '75',
      ));
      setAnalysisMetrics({
        is_feasible: feasibility.is_feasible,
        collision_count: feasibility.collision_count || 0,
        route4_valid: baseReport.route4Valid !== false,
        avg_travel_time_before_min: simulation.avg_travel_time_before_min,
        avg_travel_time_after_min: simulation.avg_travel_time_after_min,
        avg_speed_before_kmh: simulation.avg_speed_before_kmh,
        avg_speed_after_kmh: simulation.avg_speed_after_kmh,
        before_throughput: simulation.before?.throughput,
        after_throughput: simulation.after?.throughput,
        time_saved_pct: impact.time_saved_pct ?? metrics.timeSaved,
        congestion_reduction_pct: impact.congestion_reduction_pct ?? metrics.trafficReduction,
        diversion_pct: impact.diversion_pct ?? metrics.diversion,
        adoption_rate_pct: simulation.actual_adoption_rate_pct ?? simulation.adoption_rate_pct,
        adopted_vehicle_count: simulation.adopted_vehicle_count,
        eligible_vehicle_count: simulation.eligible_vehicle_count,
        simulation_scenarios: simulation.scenarios || baseReport.simulationScenarios || [],
        comparison: savedReport.projectMode === 'compare' ? savedReport.overallSummary : null,
      });
      setAnalysisComplete(true);
      setStep(3);
      setStatusMsg('Completed analysis restored for this project.');
    }
    if (hasStart && hasEnd && savedInterventions.length > 0) setStep(2);
    else if (hasStart && hasEnd) setStep(1);
    if (savedReport && Object.keys(savedReport).length > 0) setStep(3);
  }, [activeProject?.id]);

  function handleStartTimeChange(value) {
    setStartTime(value);
    const hour = Number(value.split(':')[0]);
    setEndTime(`${String((hour + 1) % 24).padStart(2, '0')}:00`);
  }

  useEffect(() => {
    fetch(apiUrl('/api/health'))
      .then((res) => res.ok ? res.json() : Promise.reject())
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false));
  }, []);

  function toggleRoute(route, index) {
    if (!route) return;
    if (step === 2 && drawMethod === 'edge' && ['road_block', 'widening'].includes(infraType)) {
      setStatusMsg('Choose individual dashed road edges to build one continuous stretch, then press Done.');
      return;
    }
    const routeKey = `${route.id}-${index}`;

    if (focusedRouteKey === routeKey) {
      setSelectedRoute(null);
      setSelectedRouteKey(null);
      setFocusedRouteKey(null);
      setStatusMsg('Showing all simulated routes.');
      return;
    }

    setSelectedRoute(route);
    setSelectedRouteKey(routeKey);
    setFocusedRouteKey(routeKey);
    setStatusMsg(`Showing Route ${route.id} only. Click it again to unselect.`);
  }

  function showAllRoutes() {
    setSelectedRoute(null);
    setSelectedRouteKey(null);
    setFocusedRouteKey(null);
    setStatusMsg('Showing all simulated routes.');
  }

  function beginAddInfrastructure() {
    loadCollisions(null);
    setIsAddingInfrastructure(true);
    setDraftInfrastructure(null);
    setSelectedEdges([]);
    setSelectedInfraId(null);
    setDrawnFeature(null);
    setDrawMethod(null);
    drawnGroupRef.current?.clearLayers?.();
    setStatusMsg('New intervention started. Choose its type and geometry.');
  }

  function finishAddInfrastructure() {
    if (!draftInfrastructure?.geometry?.coordinates || draftInfrastructure.geometry.coordinates.length < 2) {
      setStatusMsg('Choose or draw the intervention geometry before finishing.');
      return;
    }
    const item = { ...draftInfrastructure, serial_number: infraItems.length + 1 };
    setInfraItems((current) => [...current, item]);
    setActiveInfraIds((current) => [...current, item.id]);
    setSelectedInfraId(item.id);
    setIsAddingInfrastructure(false);
    setDraftInfrastructure(null);
    setSelectedEdges([]);
    setDrawMethod(null);
    setStatusMsg(`${item.name} registered. Add another intervention if needed.`);
  }

  function handleReset() {
    if (simulationRunning || analysisRunning || alternativesLoading) return;

    drawnGroupRef.current?.clearLayers?.();
    loadCollisions(null);
    setStep(1);
    setMapCenter(CORRIDOR_CENTER);
    setStatusMsg('Click the corridor map to select an origin.');
    setStartLoc(null);
    setEndLoc(null);
    setSearchQuery('');
    setIsSearchingStart(true);
    setSearchResults([]);
    setInterventionType('infrastructure');
    setInfraType('flyover');
    setDrawMethod(null);
    setDrawnFeature(null);
    setInfraItems([]);
    setSelectedEdges([]);
    setSelectedInfraId(null);
    setActiveInfraIds([]);
    setProjectMode('package');
    setPackageAdoption(75);
    setIsAddingInfrastructure(false);
    setDraftInfrastructure(null);
    setOptimizeOptions({ widening: false, signal: false });
    setEmail(userEmail || '');
    setEditingEmail(false);
    setStartTime('09:00');
    setEndTime('10:00');
    setSimulationResult(null);
    setRoutes([]);
    setSelectedRoute(null);
    setSelectedRouteKey(null);
    setFocusedRouteKey(null);
    setHeatmap(null);
    setShowHeatmap(false);
    setHeatmapMode('difference');
    setHeatmapAdoption(null);
    setHotspotView('worsened');
    setFocusedHeatmapEdgeId(null);
    setFocusedHeatmapLocation(null);
    setAlternativeCandidates([]);
    setSelectedAlternative(null);
    setShowAlternatives(false);
    setAnalysisComplete(false);
    setAnalysisMetrics(null);
    setEditReturnSignature(null);
    setIsMapFocusMode(false);
    setShowLayerControls(false);
    setVisibleLayers({ existingRoutes: true, proposedRoute: true, endpoints: true, collisions: true });
    onAnalyzingChange?.(false);
    onReset?.();
  }

  function getEditableAnalysisSignature() {
    return JSON.stringify({
      startLoc,
      endLoc,
      interventionType,
      infraType,
      drawnGeometry: drawnFeature?.geometry || null,
      infraItems,
      activeInfraIds,
      projectMode,
      packageAdoption,
      optimizeOptions,
      startTime,
      endTime,
      dayOfWeek,
    });
  }

  const canReturnToPreviousSimulation = Boolean(
    editReturnSignature
    && analysisMetrics
    && getEditableAnalysisSignature() === editReturnSignature,
  );

  function beginEditingCompletedAnalysis() {
    setEditReturnSignature(getEditableAnalysisSignature());
    setAnalysisComplete(false);
    setStep(2);
    setStatusMsg('Review or update the interventions, then continue to run a new simulation.');
  }

  function returnToPreviousSimulation() {
    if (!canReturnToPreviousSimulation) return;
    setAnalysisComplete(true);
    setStep(3);
    setStatusMsg('Previous simulation restored.');
  }

  function handleMapPick(latlng) {
    if (step === 2 && drawMethod === 'edge') {
      const segment = getNearestNetworkSegment(latlng);
      if (!segment) {
        setStatusMsg('No corridor edge was found near that click. Try a point closer to the road network.');
        return;
      }
      handleEdgePick(segment.route, segment.coordinates, segment.segmentIndex, segment.routeIndex);
      return;
    }

    const point = {
      // Keep the actual Leaflet click coordinate for routing; only round the label.
      lat: latlng.lat,
      lon: latlng.lng,
      name: `${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`,
    };
    setSimulationResult(null);
    setRoutes([]);
    setSelectedRoute(null);
    setSelectedRouteKey(null);
    setFocusedRouteKey(null);
    setAlternativeCandidates([]);
    setSelectedAlternative(null);
    setShowAlternatives(false);

    if (isSearchingStart || !startLoc) {
      setStartLoc(point);
      if (!endLoc) {
        setIsSearchingStart(false);
      }
      setStatusMsg(endLoc
        ? 'Origin selected. Destination remains set; click the map to move destination B or select Destination (B) to toggle.'
        : 'Origin selected. Click the corridor map for the destination.');
    } else {
      setEndLoc(point);
      if (!startLoc) {
        setIsSearchingStart(true);
      }
      setStatusMsg(startLoc
        ? 'Destination selected. Click Start Point A to move the origin or select Destination (B) to toggle.'
        : 'Destination selected. Click the corridor map for the origin.');
    }
  }

  function updateRoutePoint(which, point) {
    if (which === 'start') setStartLoc(point);
    else setEndLoc(point);
    setSimulationResult(null);
    setRoutes([]);
    setSelectedRoute(null);
    setSelectedRouteKey(null);
    setFocusedRouteKey(null);
    setAlternativeCandidates([]);
    setSelectedAlternative(null);
    setShowAlternatives(false);
    setMapCenter([point.lat, point.lon]);
    setStatusMsg(`${which === 'start' ? 'Origin' : 'Destination'} coordinates updated.`);
  }

  async function handleRunSimulation() {
    if (!startLoc || !endLoc) return;
    
    const getDurationMinutes = (start, end) => {
      const sh = Number(start.split(':')[0]);
      const eh = Number(end.split(':')[0]);
      const duration = (eh - sh) * 60;
      return duration > 0 ? duration : duration + 24 * 60;
    };
    if (getDurationMinutes(startTime, endTime) < 30) {
      alert('Please select a simulation duration of at least 30 minutes.');
      return;
    }
    
    setSimulationRunning(true);
    onAnalyzingChange?.(true);
    setStatusMsg('SUMO GUI is running the selected corridor...');
    try {
      const startHourInt = parseInt(startTime.split(':')[0]) || 9;
      const res = await fetch(apiUrl('/api/simulate'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origin: startLoc,
          destination: endLoc,
          start_time: startTime,
          end_time: endTime,
          target_hour: startHourInt,
          ward_no: 163,
          congestion_multiplier: startHourInt >= 8 && startHourInt <= 10 ? 1.5 : 1.0,
          day_of_week: dayOfWeek,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'SUMO simulation failed');
      setSimulationResult(data);

      const simulationRoutes = data.routes || [];
      setRoutes(simulationRoutes);
      setSelectedRoute(simulationRoutes[0] || null);
      setSelectedRouteKey(simulationRoutes.length > 0 ? `${simulationRoutes[0].id}-0` : null);
      setFocusedRouteKey(null);

      setStatusMsg("SUMO simulation complete.");

      setStep(2);
    } catch (error) {
      setStatusMsg(`SUMO error: ${error.message}`);
    } finally {
      setSimulationRunning(false);
      onAnalyzingChange?.(false);
    }
  }

  const canProceedStep2 =
    interventionType === 'infrastructure'
      ? Boolean(showAlternatives ? selectedAlternative : (drawnFeature || selectedAlternative))
      : optimizeOptions.widening || optimizeOptions.signal;

  async function handleFindAlternatives() {
    if (!startLoc || !endLoc) return;

    loadCollisions(null);
    setAlternativesLoading(true);
    onAnalyzingChange?.(true);
    setStatusMsg('Evaluating alternative infrastructure candidates…');
    try {
      const res = await fetch(apiUrl('/api/alternatives'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origin: startLoc,
          destination: endLoc,
          place_name: 'Kathriguppe, Bangalore, India',
          dist_m: 5000,
          infrastructure_types: ['road', 'flyover', 'tunnel'],
          nearby_radii_m: [250, 500],
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Alternative evaluation failed');
      setAlternativeCandidates(data.candidates || []);
      setSelectedAlternative(null);
      setStatusMsg(
        `${data.feasible_count || 0} feasible alternative(s) found. Select one on the map or below.`
      );
    } catch (error) {
      setStatusMsg(`Alternative evaluation error: ${error.message}`);
    } finally {
      setAlternativesLoading(false);
      onAnalyzingChange?.(false);
    }
  }

  function handleSelectAlternative(evaluation) {
    const candidate = evaluation?.candidate;
    if (!candidate) return;
    if (!evaluation.ranking_eligible) {
      setStatusMsg(`${candidate.candidate_id} is not feasible and cannot be selected for simulation.`);
      return;
    }

    const candidateType = candidate.infrastructure?.type || 'flyover';
    loadCollisions(evaluation.artifacts || evaluation.phase34_output?.artifacts || null);
    setSelectedAlternative(evaluation);
    setInfraType(candidateType);
    setDrawMethod('auto');
    setDrawnFeature({
      type: 'Feature',
      geometry: candidate.geometry,
      properties: { candidate_id: candidate.candidate_id },
    });
    setStartLoc({
      ...candidate.start,
      name: startLoc?.name || 'Alternative origin',
    });
    setEndLoc({
      ...candidate.end,
      name: endLoc?.name || 'Alternative destination',
    });
    setStatusMsg(
      evaluation.ranking_eligible
        ? `Selected ${candidate.candidate_id}. Continue to run the full simulation.`
        : `Selected ${candidate.candidate_id}, but it is not currently feasible.`
    );
  }

  // --- LOGIC: Step 1 Search ---
  async function handleSearch() {
    if (!searchQuery) return;
    setStatusMsg('Searching OSM...');
    try {
      const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(searchQuery)}`;
      const res = await fetch(url);
      const data = await res.json();
      setSearchResults(data);
      if (data.length === 0) setStatusMsg('No results found.');
    } catch (e) {
      console.error(e);
    }
  }

  function selectLocation(loc) {
    const item = {
      lat: parseFloat(loc.lat),
      lon: parseFloat(loc.lon),
      name: loc.display_name.split(',')[0]
    };

    setMapCenter([item.lat, item.lon]);

    if (isSearchingStart) {
      setStartLoc(item);
      setSearchQuery('');
      setIsSearchingStart(false); // Auto-switch to End
      setStatusMsg(`Start set to ${item.name}. Now search for Destination.`);
    } else {
      setEndLoc(item);
      setSearchQuery('');
      setSearchResults([]);
      setStatusMsg(`Route Set: ${startLoc?.name} to ${item.name}.`);
    }
  }

  // --- LOGIC: Step 2 Intervention ---
  function addInfraItemFromGeometry(geojson, infraName = null, metadata = {}, { commitImmediately = false } = {}) {
    if (projectMode === 'compare' && infraItems.length >= MAX_COMPARE_ITEMS) {
      setStatusMsg(`Compare mode allows up to ${MAX_COMPARE_ITEMS} options. Delete one to draw another.`);
      return false;
    }
    const isFirstNewAlignment = NEW_ALIGNMENT_TYPES.includes(infraType)
      && !infraItems.some((entry) => NEW_ALIGNMENT_TYPES.includes(entry.infra_type));
    const item = {
      id: (crypto?.randomUUID ? crypto.randomUUID() : String(Date.now()) + '-' + Math.round(Math.random() * 10000)),
      name: infraName || `${infraType || 'Infra'} ${Math.max(1, infraItems.length + 1)}`,
      serial_number: Math.max(1, infraItems.length + 1),
      type: 'infrastructure',
      infra_type: infraType || 'flyover',
      geometry: geojson?.geometry || { type: 'LineString', coordinates: [] },
      road_width_m: infraType === 'widening' ? LANE_WIDTH_M : LANE_WIDTH_M * 2,
      is_oneway: false,
      adoption_rate_pct: 75,
      is_main: projectMode === 'package' && isFirstNewAlignment,
      active: true,
      edge_label: metadata.edge_label ?? null,
      edge_distance_km: metadata.edge_distance_km ?? null,
    };
    setDrawnFeature(geojson);
    if (commitImmediately) {
      setInfraItems((current) => [...current, item]);
      setActiveInfraIds((current) => [...new Set([...current, item.id])]);
      setSelectedInfraId(item.id);
      setIsAddingInfrastructure(false);
      setDraftInfrastructure(null);
      setDrawMethod(null);
      setStatusMsg(`${item.name} created as one intervention from ${metadata.edge_count} selected edges.`);
    } else {
      setDraftInfrastructure(item);
      setStatusMsg('Manual path captured.');
    }
    return true;
  }

  function changeProjectMode(mode) {
    if (mode === projectMode) return;
    if (mode === 'compare') {
      if (infraItems.length > MAX_COMPARE_ITEMS) {
        setStatusMsg(`Compare mode allows up to ${MAX_COMPARE_ITEMS} options. Delete some items first.`);
        return;
      }
    }
    if (!infraTypesForMode(mode).includes(infraType)) setInfraType('flyover');
    setProjectMode(mode);
    setAnalysisComplete(false);
    setStatusMsg(mode === 'compare'
      ? 'Compare mode: draw up to 3 options. Each is simulated on its own and ranked.'
      : 'Package mode: draw the main structure plus its connectors and widening. They are simulated together.');
  }

  function setMainInfra(itemId) {
    setInfraItems((current) => current.map((entry) => ({ ...entry, is_main: entry.id === itemId })));
  }

  function updateInfrastructureGeometry(itemId, geometry) {
    setInfraItems((current) => current.map((item) => item.id === itemId ? { ...item, geometry } : item));
    setSelectedInfraId(itemId);
    setDrawnFeature({ type: 'Feature', geometry, properties: {} });
    setSimulationResult(null);
    setRoutes([]);
    setStatusMsg('Infrastructure coordinates updated.');
  }

  function selectInfrastructureType(type) {
    setInfraType(type);
    if (!['road_block', 'widening'].includes(type)) setSelectedEdges([]);
    if (isAddingInfrastructure) {
      setDraftInfrastructure((current) => current ? {
        ...current,
        infra_type: type,
        road_width_m: type === 'widening' ? LANE_WIDTH_M : LANE_WIDTH_M * 2,
        name: `${type} ${Math.max(1, infraItems.length + 1)}`,
      } : current);
    } else if (selectedInfraId) {
      setInfraItems((current) => current.map((item) => item.id === selectedInfraId
        ? { ...item, infra_type: type, road_width_m: type === 'widening' ? LANE_WIDTH_M : LANE_WIDTH_M * 2, name: `${type} ${item.serial_number}` }
        : item));
    }
  }

  function getNearestNetworkSegment(latlng) {
    const candidates = [];

    routes.forEach((route, routeIndex) => {
      const geometry = route?.geometry?.coordinates || [];
      if (!Array.isArray(geometry) || geometry.length < 2) return;
      geometry.slice(0, -1).forEach((point, segmentIndex) => {
        const coordinates = [point, geometry[segmentIndex + 1]];
        const nearest = turf.nearestPointOnLine(turf.lineString(coordinates), [latlng.lng, latlng.lat]);
        candidates.push({
          distanceKm: nearest?.properties?.dist || 0,
          coordinates,
          route,
          routeIndex,
          segmentIndex,
          key: `${route.id ?? routeIndex}-${segmentIndex}`,
          label: `Route ${route.id || routeIndex + 1} · edge ${segmentIndex + 1}`,
        });
      });
    });

    if (!candidates.length) return null;
    const best = candidates.reduce((bestItem, item) => item.distanceKm < bestItem.distanceKm ? item : bestItem, candidates[0]);
    return best.distanceKm <= 0.08 ? best : null;
  }

  function handleAutoConnect() {
    if (!startLoc || !endLoc) return;
    const line = turf.lineString([
      [startLoc.lon, startLoc.lat],
      [endLoc.lon, endLoc.lat]
    ]);
    setDrawnFeature(line);
    setDrawMethod('auto');
    addInfraItemFromGeometry(line, `${infraType || 'Infra'} ${Math.max(1, infraItems.length + 1)}`);
    setStatusMsg('System auto-connected points with a new road.');

    const midLat = (startLoc.lat + endLoc.lat) / 2;
    const midLon = (startLoc.lon + endLoc.lon) / 2;
    setMapCenter([midLat, midLon]);
  }

  function startManualDraw() {
    loadCollisions(null);
    setDrawMethod('manual');
    setDrawnFeature(null);
    drawnGroupRef.current?.clearLayers?.();
    setStatusMsg('Select the POLYLINE tool (top-left) to draw your custom path.');
  }

  function startEdgeSelection() {
    if (!routes.length) {
      setStatusMsg('Run the route simulation first so the corridor network is visible for edge selection.');
      return;
    }
    loadCollisions(null);
    setSelectedEdges([]);
    setDrawMethod('edge');
    setDrawnFeature(null);
    drawnGroupRef.current?.clearLayers?.();
    setStatusMsg('Road-segment selection mode is active. The map is split into clickable edges.');
  }

  function handleEdgePick(route, coordinates, segmentIndex, routeIndex = 0) {
    if (!isAddingInfrastructure || !coordinates || coordinates.length < 2) return;
    const key = `${route.id ?? routeIndex}-${segmentIndex}`;
    const edge = {
      key,
      coordinates,
      label: `Route ${route.id || routeIndex + 1} · edge ${segmentIndex + 1}`,
    };
    const next = selectedEdges.some((item) => item.key === key)
      ? selectedEdges.filter((item) => item.key !== key)
      : [...selectedEdges, edge];
    setSelectedEdges(next);
    setStatusMsg(`${next.length} edge(s) selected. Select adjacent edges, then finish the intervention.`);
  }

  function buildSelectedEdgeCoordinates(edges) {
    if (!edges.length) return null;
    const samePoint = (first, second) => (
      Math.abs(first[0] - second[0]) < 1e-9 && Math.abs(first[1] - second[1]) < 1e-9
    );
    const remaining = edges.slice(1).map((edge) => edge.coordinates);
    let coordinates = [...edges[0].coordinates];

    while (remaining.length) {
      const index = remaining.findIndex((segment) => (
        samePoint(coordinates[coordinates.length - 1], segment[0])
        || samePoint(coordinates[coordinates.length - 1], segment[segment.length - 1])
        || samePoint(coordinates[0], segment[0])
        || samePoint(coordinates[0], segment[segment.length - 1])
      ));
      if (index < 0) return null;

      const [segment] = remaining.splice(index, 1);
      if (samePoint(coordinates[coordinates.length - 1], segment[0])) {
        coordinates = [...coordinates, ...segment.slice(1)];
      } else if (samePoint(coordinates[coordinates.length - 1], segment[segment.length - 1])) {
        coordinates = [...coordinates, ...segment.slice(0, -1).reverse()];
      } else if (samePoint(coordinates[0], segment[segment.length - 1])) {
        coordinates = [...segment.slice(0, -1), ...coordinates];
      } else {
        coordinates = [...segment.slice(1).reverse(), ...coordinates];
      }
    }

    return coordinates;
  }

  function finishEdgeSelection() {
    const coordinates = buildSelectedEdgeCoordinates(selectedEdges);
    if (!coordinates || coordinates.length < 2) {
      setStatusMsg('Selected edges must connect into one continuous stretch. Remove disconnected edges and try again.');
      return;
    }

    const itemName = `${infraType} ${Math.max(1, infraItems.length + 1)}`;
    const added = addInfraItemFromGeometry(
      { type: 'Feature', geometry: { type: 'LineString', coordinates }, properties: {} },
      itemName,
      { edge_label: `${selectedEdges.length} connected edges`, edge_distance_km: 0, edge_count: selectedEdges.length },
      { commitImmediately: true },
    );
    if (added) setSelectedEdges([]);
  }

  const onCreated = (e) => {
    const layer = e.layer;
    const geojson = layer.toGeoJSON();
    const added = addInfraItemFromGeometry(geojson, `${infraType || 'Infra'} ${Math.max(1, infraItems.length + 1)}`);
    if (added) drawnGroupRef.current?.addLayer?.(layer);
    else layer.remove?.();
  };

  // --- LOGIC: Step 3 Generate ---
  async function handleGenerate() {
    if (!startLoc || !endLoc) { setStatusMsg('Missing Route'); return; }
    if (email.trim() && !email.includes('@')) { setStatusMsg('Invalid Email'); return; }
    if (analysisRunning) return;
    
    const getDurationMinutes = (start, end) => {
      const sh = Number(start.split(':')[0]);
      const eh = Number(end.split(':')[0]);
      const duration = (eh - sh) * 60;
      return duration > 0 ? duration : duration + 24 * 60;
    };
    if (getDurationMinutes(startTime, endTime) < 30) {
      alert('Please select a simulation duration of at least 30 minutes.');
      return;
    }
    if (interventionType === 'infrastructure' && !drawnFeature) {
      setStatusMsg('Draw or auto-connect a path first.');
      return;
    }
    if (interventionType === 'optimize' && !optimizeOptions.widening && !optimizeOptions.signal) {
      setStatusMsg('Select at least one optimization option.');
      return;
    }
    const activeItems = infraItems.filter((item) => activeInfraIds.includes(item.id) || activeInfraIds.length === 0);
    if (interventionType === 'infrastructure' && projectMode === 'compare') {
      if (activeItems.length > MAX_COMPARE_ITEMS) {
        setStatusMsg(`Compare mode allows up to ${MAX_COMPARE_ITEMS} options.`);
        return;
      }
    }

    setAnalysisRunning(true);
    loadCollisions(null);
    setAnalysisProgress(10);
    onAnalyzingChange?.(true);
    setStatusMsg('[1/4] Validating spatial route geometry & OSM graph...');

    let currentProgress = 10;
    const progressInterval = setInterval(() => {
      currentProgress += 1;
      if (currentProgress <= 25) {
        setStatusMsg('[1/4] Validating spatial route geometry & OSM graph...');
      } else if (currentProgress <= 55) {
        setStatusMsg('[2/4] Injecting infrastructure proposal & running SUMO micro-simulation...');
      } else if (currentProgress <= 80) {
        setStatusMsg('[3/4] Processing Floating Car Data & computing edge heatmaps...');
      } else if (currentProgress < 95) {
        setStatusMsg('[4/4] Finalizing scenario comparison & rendering maps...');
      }
      setAnalysisProgress(Math.min(currentProgress, 95));
    }, 600);

    const minLon = Math.min(startLoc.lon, endLoc.lon);
    const maxLon = Math.max(startLoc.lon, endLoc.lon);
    const minLat = Math.min(startLoc.lat, endLoc.lat);
    const maxLat = Math.max(startLoc.lat, endLoc.lat);
    const PDING = 0.01;
    const bbox = [minLon - PDING, minLat - PDING, maxLon + PDING, maxLat + PDING];

    let coordinates = [
      [startLoc.lon, startLoc.lat],
      [endLoc.lon, endLoc.lat]
    ];
    if (interventionType === 'infrastructure' && drawnFeature?.geometry?.coordinates?.length) {
      coordinates = drawnFeature.geometry.coordinates;
    }

    const selectedInterventions = infraItems.filter((item) => activeInfraIds.includes(item.id) || activeInfraIds.length === 0).map((item) => ({
      id: item.id,
      name: item.name,
      serial_number: item.serial_number,
      type: interventionType,
      infra_type: item.infra_type || infraType,
      geometry: item.geometry || { type: 'LineString', coordinates },
      road_width_m: item.road_width_m ?? 8,
      is_oneway: item.is_oneway ?? false,
      adoption_rate_pct: clampPercent(projectMode === 'package' ? packageAdoption : (item.adoption_rate_pct ?? 75)),
      is_main: Boolean(item.is_main),
    }));
    // Package: one adoption % for the whole project. Compare: the backend
    // sweeps each option at its own %; this value only covers a single option.
    const userAdoptionRate = projectMode === 'package'
      ? clampPercent(packageAdoption)
      : clampPercent(selectedInterventions[0]?.adoption_rate_pct ?? 75);

    const payload = {
      route: {
        start: startLoc,
        end: endLoc,
      },
      intervention: selectedInterventions[0] || {
        id: (crypto?.randomUUID ? crypto.randomUUID() : String(Date.now())),
        name: `${infraType || 'Infra'} 1`,
        serial_number: 1,
        type: interventionType,
        infra_type: infraType,
        geometry: { type: 'LineString', coordinates },
        road_width_m: 8,
        is_oneway: false,
        adoption_rate_pct: ADOPTION_RATES[ADOPTION_RATES.length - 1],
      },
      interventions: selectedInterventions,
      scenario: {
        start_time: startTime,
        end_time: endTime,
        target_hour: parseInt(startTime.split(':')[0]) || 9,
        ward_no: 163,
        place_name: 'Kathriguppe, Bangalore, India',
        dist_m: 5000,
        day_of_week: dayOfWeek,
        congestion_multiplier: (parseInt(startTime.split(':')[0]) || 9) >= 8 && (parseInt(startTime.split(':')[0]) || 9) <= 10 ? 1.5 : 1.0,
        adoption_rate_pct: userAdoptionRate,
        adoption_rates_pct: [...new Set([25, 50, 75, userAdoptionRate])].sort((a, b) => a - b),
        project_mode: projectMode,
      },
      options: {
        widening: optimizeOptions.widening,
        smart_signals: optimizeOptions.signal,
        smart_analysis: true,
      },
      simulation_result: simulationResult,
      project_id: projectId || null,
      active_infra: activeInfraIds,
    };

    try {
      // Analyses run as background jobs: a single long request would be cut off
      // by the hosting proxy, so start the job and poll until it finishes.
      const url = projectId ? `/api/projects/${projectId}/analyze/jobs` : '/api/analyze/jobs';
      const startRes = await fetch(apiUrl(url), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...payload,
          report_name: projectMode === 'compare' && selectedInterventions.length > 1
            ? 'Infrastructure comparison'
            : projectMode === 'package' && selectedInterventions.length > 1
              ? 'Infrastructure package'
              : `${infraType} report`,
        }),
      });
      let res = { ok: startRes.ok };
      let data = await startRes.json();
      if (startRes.ok) {
        while (true) {
          await new Promise((resolve) => setTimeout(resolve, 3000));
          const pollRes = await fetch(apiUrl(`/api/analyze/jobs/${data.job_id}`));
          const job = await pollRes.json();
          if (!pollRes.ok) {
            res = { ok: false };
            data = job;
            break;
          }
          if (job.status === 'done') {
            res = { ok: job.status_code >= 200 && job.status_code < 300 };
            data = job.result;
            break;
          }
        }
      }
      clearInterval(progressInterval);

      if (!res.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map((item) => item.msg || item).join(', ')
          : (data.detail || data.message || 'Analysis failed');
        setStatusMsg('Error: ' + detail);
        return;
      }

      if (data.status === 'success') {
        setAnalysisProgress(100);
        const feasible = data.feasibility?.is_feasible;
        const route4Valid = data.route4_valid !== false;
        const nextHeatmap = data.simulation?.heatmap || data.report?.simulation?.heatmap || null;
        const availableAdoptions = Object.keys(nextHeatmap?.scenarios || {});
        setHeatmap(nextHeatmap);
        setShowHeatmap(false);
        setHeatmapMode('difference');
        setHeatmapAdoption(
          nextHeatmap?.selected_adoption_rate_pct == null
            ? (availableAdoptions[availableAdoptions.length - 1] || null)
            : String(nextHeatmap.selected_adoption_rate_pct),
        );
        setFocusedHeatmapEdgeId(null);
        setFocusedHeatmapLocation(null);
        setStatusMsg(
          !feasible
            ? `Analysis complete — feasibility failed (${data.feasibility?.collision_count || 0} building collision(s)). Simulation results are preview-only.`
            : !route4Valid
            ? 'Analysis complete — Route 4 could not be generated.'
            : 'Analysis complete — feasible proposal.'
        );

        const routesFromSim = data.simulation?.routes || data.report?.simulation?.routes;
        if (routesFromSim && routesFromSim.length > 0) {
          const visibleRoutes = routesFromSim.filter((route) => route?.geometry?.coordinates?.length >= 2);
          setRoutes(visibleRoutes);
          setSelectedRoute(visibleRoutes[0] || null);
          setSelectedRouteKey(visibleRoutes.length > 0 ? `${visibleRoutes[0].id}-0` : null);
          setFocusedRouteKey(null);
        }

        setAnalysisMetrics({
          is_feasible: feasible,
          collision_count: data.feasibility?.collision_count || 0,
          route4_valid: route4Valid,
          avg_travel_time_before_min: data.simulation?.avg_travel_time_before_min,
          avg_travel_time_after_min: data.simulation?.avg_travel_time_after_min,
          avg_speed_before_kmh: data.simulation?.avg_speed_before_kmh,
          avg_speed_after_kmh: data.simulation?.avg_speed_after_kmh,
          before_throughput: data.simulation?.before?.throughput,
          after_throughput: data.simulation?.after?.throughput,
          time_saved_pct: data.impact?.time_saved_pct,
          congestion_reduction_pct: data.impact?.congestion_reduction_pct,
          diversion_pct: data.impact?.diversion_pct,
          adoption_rate_pct: data.simulation?.actual_adoption_rate_pct ?? data.simulation?.adoption_rate_pct,
          adopted_vehicle_count: data.simulation?.adopted_vehicle_count,
          eligible_vehicle_count: data.simulation?.eligible_vehicle_count,
          simulation_scenarios: data.simulation?.scenarios || [],
          comparison: data.mode === 'compare' ? data.comparison : null,
        });
        loadCollisions(data.artifacts || data.report?.artifacts);
        setAnalysisComplete(true);
        setEditReturnSignature(null);

        if (data.mode === 'compare') {
          onGenerate({ ...data.report, selectionGeoJSON: turf.bboxPolygon(bbox) });
          setShowAlternatives(false);
          return;
        }

        onGenerate({
          ...data.report,
          reportName: data.report?.reportName || `${infraType} report`,
          isWideningOnly: data.report?.isWideningOnly,
          isRoadBlockOnly: data.report?.isRoadBlockOnly,
          isRoadModificationOnly: data.report?.isRoadModificationOnly,
          widenedEdgeCount: data.report?.widenedEdgeCount,
          blockedEdgeCount: data.report?.blockedEdgeCount,
          route4Error: data.report?.route4Error,
          feasibility: data.feasibility,
          impact: data.impact,
          phase2: data.phase2 || data.report?.phase2,
          smartRecommendation: data.smart_recommendation || data.report?.smartRecommendation,
          simulation: data.simulation || data.report?.simulation,
          simulationScenarios: data.simulation?.scenarios || data.report?.simulationScenarios || [],
          hourlyTraffic: data.report?.hourlyTraffic || [],
          metrics: data.report?.metrics,
          cautions: data.report?.cautions,
          demand: data.demand,
          artifacts: data.artifacts || data.report?.artifacts,
          selectedAlternative: selectedAlternative ? {
            candidateId: selectedAlternative.candidate?.candidate_id,
            candidateType: selectedAlternative.candidate?.candidate_type,
            infrastructureType: selectedAlternative.candidate?.infrastructure?.label,
            rank: selectedAlternative.rank,
            route4LengthM: selectedAlternative.route_validation?.route4_length_m,
          } : null,
          selectionGeoJSON: turf.bboxPolygon(bbox),
        });

        setShowAlternatives(false);
      } else {
        setStatusMsg('Error: ' + (data.message || 'Unknown error'));
      }
    } catch (e) {
      console.error(e);
      setStatusMsg('Backend error. Start the API: cd backend && uvicorn api.server:app --reload --port 8000');
    } finally {
      clearInterval(progressInterval);
      setAnalysisRunning(false);
      onAnalyzingChange?.(false);
    }
  }


  const heatmapScenarioKeys = Object.keys(heatmap?.scenarios || {});
  const selectedHeatmapScenario = heatmapScenarioKeys
    .map((key) => [key, heatmap?.scenarios?.[key]])
    .find(([key]) => key === String(heatmapAdoption) || Number(key) === Number(heatmapAdoption))?.[1]
    || heatmap?.scenarios?.[heatmapScenarioKeys[heatmapScenarioKeys.length - 1]]
    || {};
  const availableHeatmapCollection = heatmap?.before?.features?.length
    ? heatmap.before
    : (selectedHeatmapScenario.after?.features?.length
      ? selectedHeatmapScenario.after
      : (heatmap?.after?.features?.length
        ? heatmap.after
        : (selectedHeatmapScenario.difference?.features?.length
          ? selectedHeatmapScenario.difference
          : heatmap?.difference)));
  const heatmapFeatureCollection = heatmapMode === 'before'
    ? (heatmap?.before?.features?.length ? heatmap.before : availableHeatmapCollection)
    : heatmapMode === 'after'
    ? (selectedHeatmapScenario.after?.features?.length ? selectedHeatmapScenario.after : (heatmap?.after?.features?.length ? heatmap.after : availableHeatmapCollection))
    : (selectedHeatmapScenario.difference?.features?.length ? selectedHeatmapScenario.difference : (heatmap?.difference?.features?.length ? heatmap.difference : availableHeatmapCollection));
  const heatmapDifference = selectedHeatmapScenario.difference || heatmap?.difference;
  const heatmapHotspots = heatmapDifference?.hotspots || [];
  const allImprovedHeatmapHotspots = heatmapMode === 'difference'
    ? (heatmapDifference?.improved_hotspots || [])
    : [];
  const improvedHeatmapHotspots = allImprovedHeatmapHotspots.slice(0, 10);
  const improvedHotspotCount = allImprovedHeatmapHotspots.length;
  const displayHotspots = hotspotView === 'improved'
    ? improvedHeatmapHotspots
    : heatmapHotspots;
  const heatmapSummary = heatmapDifference?.summary || {};
  const heatmapActive = Boolean(showHeatmap && heatmapFeatureCollection?.features?.length);

  function focusHeatmapEdge(edge) {
    if (!edge?.edge_id) return;
    setFocusedHeatmapEdgeId(edge.edge_id);
    setFocusedHeatmapLocation({
      lat: Number(edge.lat),
      lon: Number(edge.lon),
    });
    setStatusMsg(`Showing ${edge.road_name || edge.edge_id} (${edge.edge_id}).`);
  }

  // --- RENDER ---
  return (
    <div className={`command-center flex h-full w-full font-sans ${isMapFocusMode ? 'is-map-focus-mode' : ''} ${isSplitView ? 'is-split-view' : ''}`}>
      {/* MAP */}
      <div className="command-map flex-1 relative">
        {isSplitView ? (
          <div className="flex h-full w-full divide-x-2 divide-slate-800">
            {/* LEFT: BASELINE (BEFORE INTERVENTION) */}
            <div className="flex-1 relative h-full">
              <div className="absolute top-4 left-4 z-[1000] bg-slate-900/90 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/80 shadow-lg text-xs font-extrabold text-blue-400">
                BASELINE (BEFORE INTERVENTION)
              </div>
              <MapContainer center={CORRIDOR_CENTER} zoom={16} zoomControl={false} minZoom={11} maxBounds={CORRIDOR_BOUNDS} maxBoundsViscosity={1} style={{ height: "100%", width: "100%" }}>
                <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap contributors" />
                <MapController center={mapCenter} zoom={16} bounds={CORRIDOR_BOUNDS} />
                <MapResizeController trigger={isMapFocusMode} />
                <SyncedViewSetter syncState={syncedMapState} />
                <MapSyncController onSync={setSyncedMapState} />
                
                {heatmapActive && (
                  <TrafficHeatmapLayer
                    featureCollection={heatmap?.before || heatmapFeatureCollection}
                    mode="before"
                    scenarioKey={heatmapAdoption}
                    focusedEdgeId={focusedHeatmapEdgeId}
                    onFocusEdge={focusHeatmapEdge}
                    hideLowConfidence={hideLowConfidenceEdges}
                  />
                )}
                <CollisionBuildingsLayer featureCollection={collisionGeoJSON} visible={visibleLayers.collisions} />
                {visibleLayers.endpoints && startLoc && <Marker position={[startLoc.lat, startLoc.lon]} icon={START_ICON} />}
                {visibleLayers.endpoints && endLoc && <Marker position={[endLoc.lat, endLoc.lon]} icon={END_ICON} />}
                
                {/* Render Baseline Routes (1, 2, 3) */}
                {routes.filter((r) => r.id !== 4).map((route, index) => {
                  const routeKey = `${route.id}-${index}`;
                  if (focusedRouteKey !== null && routeKey !== focusedRouteKey) return null;
                  const style = ROUTE_STYLES[route.id] || ROUTE_STYLES[(index % 4) + 1];
                  const positions = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
                  return (
                    <Polyline
                      key={`baseline-${route.id}`}
                      positions={positions}
                      pathOptions={{ color: style.color, weight: 5, opacity: 0.75 }}
                      eventHandlers={{ click: () => toggleRoute(route, index) }}
                    />
                  );
                })}
              </MapContainer>
            </div>

            {/* RIGHT: PROPOSED INTERVENTION (AFTER) */}
            <div className="flex-1 relative h-full">
              <div className="absolute top-4 left-4 z-[1000] bg-slate-900/90 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/80 shadow-lg text-xs font-extrabold text-amber-400">
                AFTER (PROPOSED INTERVENTION)
              </div>
              <MapContainer center={CORRIDOR_CENTER} zoom={16} zoomControl={false} minZoom={11} maxBounds={CORRIDOR_BOUNDS} maxBoundsViscosity={1} style={{ height: "100%", width: "100%" }}>
                <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap contributors" />
                <MapController center={mapCenter} zoom={16} bounds={CORRIDOR_BOUNDS} />
                <MapResizeController trigger={isMapFocusMode} />
                <SyncedViewSetter syncState={syncedMapState} />
                <MapSyncController onSync={setSyncedMapState} />
                
                {heatmapActive && (
                  <TrafficHeatmapLayer
                    featureCollection={heatmapMode === 'before'
                      ? (selectedHeatmapScenario.after || heatmap?.after)
                      : heatmapFeatureCollection}
                    mode={heatmapMode === 'before' ? 'after' : heatmapMode}
                    scenarioKey={heatmapAdoption}
                    focusedEdgeId={focusedHeatmapEdgeId}
                    onFocusEdge={focusHeatmapEdge}
                    hideLowConfidence={hideLowConfidenceEdges}
                  />
                )}
                <CollisionBuildingsLayer featureCollection={collisionGeoJSON} visible={visibleLayers.collisions} />
                {visibleLayers.endpoints && startLoc && <Marker position={[startLoc.lat, startLoc.lon]} icon={START_ICON} />}
                {visibleLayers.endpoints && endLoc && <Marker position={[endLoc.lat, endLoc.lon]} icon={END_ICON} />}
                
                {/* Render Proposed Route 4 & All Routes */}
                {routes.map((route, index) => {
                  const routeKey = `${route.id}-${index}`;
                  if (focusedRouteKey !== null && routeKey !== focusedRouteKey) return null;
                  const isSelected = selectedRouteKey === routeKey;
                  const style = ROUTE_STYLES[route.id] || ROUTE_STYLES[(index % 4) + 1];
                  const routeCoordinates = route.id === 4
                    ? (route.existing_route_geometries || [])
                    : [{ coordinates: route.geometry.coordinates }];
                  return (
                    <React.Fragment key={`after-${route.id}`}>
                      {routeCoordinates.map((segment, segmentIndex) => (
                        <Polyline
                          key={`after-${route.id}-segment-${segmentIndex}`}
                          positions={segment.coordinates.map(([lon, lat]) => [lat, lon])}
                          pathOptions={{
                            color: isSelected ? '#ffffff' : (route.id === 4 ? PROPOSED_ROUTE_COLOR : style.color),
                            weight: isSelected ? (route.id === 4 ? 11 : 9) : (route.id === 4 ? 6 : 4),
                            opacity: isSelected ? 0.98 : 0.9,
                          }}
                          eventHandlers={{ click: () => toggleRoute(route, index) }}
                        />
                      ))}
                      {route.id === 4 && route.proposal_geometry?.coordinates?.length >= 2 && (
                        <Polyline
                          positions={route.proposal_geometry.coordinates.map(([lon, lat]) => [lat, lon])}
                          pathOptions={{
                            color: PROPOSED_INFRA_COLOR,
                            weight: isSelected ? 11 : 8,
                            opacity: isSelected ? 1 : 0.95,
                            dashArray: '12 8',
                          }}
                          eventHandlers={{ click: () => toggleRoute(route, index) }}
                        />
                      )}
                    </React.Fragment>
                  );
                })}
              </MapContainer>
            </div>
          </div>
        ) : (
          <MapContainer center={CORRIDOR_CENTER} zoom={16} minZoom={11} maxBounds={CORRIDOR_BOUNDS} maxBoundsViscosity={1} style={{ height: "100%", width: "100%" }}>
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution="&copy; OpenStreetMap contributors"
            />
            <MapController center={mapCenter} zoom={16} bounds={CORRIDOR_BOUNDS} />
            <MapResizeController trigger={isMapFocusMode} />
            <HeatmapViewportController
              active={heatmapActive}
              bbox={heatmapFeatureCollection?.zone_bbox || heatmap?.before?.zone_bbox}
            />
            <HeatmapFocusController focus={focusedHeatmapLocation} />
            {step === 1 && <CorridorPointPicker onPick={handleMapPick} />}

            {heatmapActive && (
              <TrafficHeatmapLayer
                featureCollection={heatmapFeatureCollection}
                mode={heatmapMode}
                scenarioKey={heatmapAdoption}
                focusedEdgeId={focusedHeatmapEdgeId}
                onFocusEdge={focusHeatmapEdge}
              />
            )}
            <CollisionBuildingsLayer featureCollection={collisionGeoJSON} visible={visibleLayers.collisions} />

            {/* Clear origin/destination markers instead of generic map pins. */}
            {visibleLayers.endpoints && startLoc && <Marker position={[startLoc.lat, startLoc.lon]} icon={START_ICON} />}
            {visibleLayers.endpoints && endLoc && <Marker position={[endLoc.lat, endLoc.lon]} icon={END_ICON} />}

            {routes.map((route, index) => {
              const routeKey = `${route.id}-${index}`;
              if (focusedRouteKey !== null && routeKey !== focusedRouteKey) return null;
              if (route.id === 4 ? !visibleLayers.proposedRoute : !visibleLayers.existingRoutes) return null;
              const style = ROUTE_STYLES[route.id] || ROUTE_STYLES[(index % 4) + 1];
              const isSelected = selectedRouteKey === routeKey;
              // Route 4 must remain the exact line the user submitted. The
              // backend also preserves this geometry, but prefer the live draw
              // locally so stale/fallback simulation responses cannot alter it.
              const displayCoordinates = route.geometry.coordinates;
              const positions = displayCoordinates.map(([lon, lat]) => [lat, lon]);
              const isProposed = route.id === 4;
              const directionCue = getRouteDirectionCue(positions);

              return <React.Fragment key={routeKey}>
                {isSelected && !isProposed && (
                  <Polyline
                    positions={positions}
                    pathOptions={{
                      color: '#ffffff',
                      weight: isProposed ? 13 : 11,
                      opacity: 0.92,
                      lineCap: 'round',
                      lineJoin: 'round',
                    }}
                    interactive={false}
                  />
                )}
                {isProposed ? (
                  <>
                    {(route.existing_route_geometries || []).map((segment, segmentIndex) => (
                      <Polyline
                        key={`${routeKey}-existing-${segmentIndex}`}
                        positions={segment.coordinates.map(([lon, lat]) => [lat, lon])}
                        pathOptions={{
                          color: PROPOSED_ROUTE_COLOR,
                          weight: isSelected ? 8 : 6,
                          opacity: isSelected ? 1 : 0.82,
                          lineCap: 'round',
                          lineJoin: 'round',
                        }}
                        eventHandlers={{ click: () => toggleRoute(route, index) }}
                      />
                    ))}
                    {route.proposal_geometry?.coordinates?.length >= 2 && (
                      <Polyline
                        positions={route.proposal_geometry.coordinates.map(([lon, lat]) => [lat, lon])}
                        pathOptions={{
                          color: PROPOSED_INFRA_COLOR,
                          weight: isSelected ? 9 : 7,
                          opacity: isSelected ? 1 : 0.95,
                          dashArray: '12 8',
                          lineCap: 'round',
                          lineJoin: 'round',
                        }}
                        eventHandlers={{ click: () => toggleRoute(route, index) }}
                      />
                    )}
                  </>
                ) : (
                  <Polyline
                    positions={positions}
                    pathOptions={{
                      color: style.color,
                      weight: isSelected ? 7 : 4,
                      opacity: isSelected ? 1 : 0.62,
                      lineCap: 'round',
                      lineJoin: 'round',
                    }}
                    eventHandlers={{ click: () => toggleRoute(route, index) }}
                  />
                )}
                {directionCue && (
                  <Marker
                    position={directionCue.position}
                    icon={createRouteArrowIcon(style.color, directionCue.angle)}
                    interactive={false}
                  />
                )}
              </React.Fragment>;
            })}

            {isAddingInfrastructure && drawMethod === 'edge' && routes.map((route, routeIndex) => {
              const coordinates = route?.geometry?.coordinates || [];
              return coordinates.slice(0, -1).map((point, segmentIndex) => {
                const segmentCoordinates = [point, coordinates[segmentIndex + 1]];
                const edgeKey = `${route.id ?? routeIndex}-${segmentIndex}`;
                const isEdgeSelected = selectedEdges.some((edge) => edge.key === edgeKey);
                return (
                  <Polyline
                    key={`edge-select-${edgeKey}`}
                    positions={segmentCoordinates.map(([lon, lat]) => [lat, lon])}
                    pathOptions={{
                      color: isEdgeSelected ? '#16a34a' : '#8b5cf6',
                      weight: isEdgeSelected ? 12 : 10,
                      opacity: 0.95,
                      dashArray: isEdgeSelected ? undefined : '5 5',
                      lineCap: 'round',
                    }}
                    eventHandlers={{ click: (event) => {
                      event.originalEvent?.stopPropagation();
                      handleEdgePick(route, segmentCoordinates, segmentIndex, routeIndex);
                    } }}
                  />
                );
              });
            })}

            {visibleLayers.proposedRoute && alternativeCandidates.map((evaluation) => {
              const candidate = evaluation.candidate;
              const route4 = evaluation.route_validation?.routes?.find((route) => route.id === 4);
              const isSelected = selectedAlternative?.candidate?.candidate_id === candidate?.candidate_id;
              const candidatePositions = candidate?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]);
              const route4Positions = route4?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]);
              if (!candidatePositions || candidatePositions.length < 2) return null;

              return (
                <React.Fragment key={candidate.candidate_id}>
                  <Polyline
                    positions={candidatePositions}
                    pathOptions={{
                      color: isSelected ? '#111827' : '#6b7280',
                      weight: isSelected ? 6 : 3,
                      opacity: isSelected ? 1 : 0.55,
                      dashArray: '8, 8',
                    }}
                    eventHandlers={{ click: () => handleSelectAlternative(evaluation) }}
                  />
                  {route4Positions && route4Positions.length >= 2 && (
                    <Polyline
                      positions={route4Positions}
                      pathOptions={{
                        color: isSelected ? '#a855f7' : '#c084fc',
                        weight: isSelected ? 7 : 4,
                        opacity: isSelected ? 1 : 0.65,
                      }}
                      eventHandlers={{ click: () => handleSelectAlternative(evaluation) }}
                    />
                  )}
                </React.Fragment>
              );
            })}

            {infraItems.concat(draftInfrastructure ? [draftInfrastructure] : []).map((item) => {
              const coordinates = item.geometry?.coordinates || [];
              if (coordinates.length < 2) return null;
              const isDraft = draftInfrastructure?.id === item.id;
              const isSelected = selectedInfraId === item.id || isDraft;
              const positions = coordinates.map(([lon, lat]) => [lat, lon]);
              const labelPosition = positions[Math.floor((positions.length - 1) / 2)];
              return (
                <React.Fragment key={`infrastructure-overlay-${item.id}`}>
                  <Polyline
                    positions={positions}
                    pathOptions={{
                      color: '#ffffff',
                      weight: isSelected ? 17 : 14,
                      opacity: 0.95,
                      lineCap: 'round',
                      lineJoin: 'round',
                    }}
                    interactive={false}
                  />
                  <Polyline
                    positions={positions}
                    pathOptions={{
                      color: isSelected ? '#f472b6' : '#fb923c',
                      weight: isSelected ? 11 : 8,
                      opacity: 1,
                      dashArray: isDraft ? '14 8' : undefined,
                      lineCap: 'round',
                      lineJoin: 'round',
                    }}
                  />
                  {labelPosition && (
                    <Marker
                      position={labelPosition}
                      icon={createInfrastructureLabelIcon(item.serial_number, isSelected)}
                      interactive={false}
                    />
                  )}
                </React.Fragment>
              );
            })}

            {visibleLayers.proposedRoute && drawMethod === 'auto' && drawnFeature && (
              <Polyline
                positions={drawnFeature.geometry.coordinates.map(c => [c[1], c[0]])}
                pathOptions={{ color: '#f97316', weight: 5, opacity: 0.9, dashArray: '12 8', lineCap: 'round', lineJoin: 'round' }}
              />
            )}

            {/* Manual Draw Control */}
            <FeatureGroup ref={drawnGroupRef}>
              <EditControl
                position="topleft"
                onCreated={onCreated}
                draw={{
                  rectangle: false, polygon: false, circle: false, circlemarker: false, marker: false,
                  // Only enable polyline if Manual Mode selected
                  polyline: drawMethod === 'manual' && visibleLayers.proposedRoute ? {
                    shapeOptions: { color: '#f97316', weight: 5, dashArray: '12 8', lineCap: 'round', lineJoin: 'round' }
                  } : false
                }}
                edit={{ edit: false, remove: false }}
              />
            </FeatureGroup>

          </MapContainer>
        )}

        <div className="command-brand">
          <span className="command-brand-mark" />
          <div>
            <div className="command-brand-title">Corridor map</div>
            <div className="command-brand-subtitle">Click to place route points</div>
          </div>
        </div>

        <div className="map-view-controls">
          <button
            type="button"
            className={`map-view-toggle ${isSplitView ? 'is-active' : ''}`}
            onClick={() => {
              const next = !isSplitView;
              setIsSplitView(next);
              if (next && heatmap?.before?.features?.length) {
                setShowHeatmap(true);
                if (heatmapMode === 'before') setHeatmapMode('after');
              }
            }}
            aria-pressed={isSplitView}
            title="Toggle side-by-side comparison map view"
          >
            <span className="map-view-toggle-icon" aria-hidden="true">◫</span>
            {isSplitView ? 'Single View' : 'Split View'}
          </button>
          <button
            type="button"
            className={`map-view-toggle ${isMapFocusMode ? 'is-active' : ''}`}
            onClick={() => setIsMapFocusMode((current) => !current)}
            aria-pressed={isMapFocusMode}
          >
            <span className="map-view-toggle-icon" aria-hidden="true">{isMapFocusMode ? '↙' : '↗'}</span>
            {isMapFocusMode ? 'Exit map focus' : 'Focus map'}
          </button>
        </div>

        {availableHeatmapCollection?.features?.length > 0 && (
          <div className="heatmap-control">
            <button
              type="button"
              className={`heatmap-toggle ${showHeatmap ? 'is-active' : ''}`}
              onClick={() => setShowHeatmap((current) => !current)}
              aria-pressed={showHeatmap}
            >
              <span aria-hidden="true">◉</span> {showHeatmap ? 'Hide network heatmap' : 'Show network heatmap'}
            </button>

            {showHeatmap && (
              <div className="heatmap-menu">
                <div className="heatmap-menu-header">
                  <div>
                    <div className="heatmap-menu-title">Network traffic state</div>
                    <div className="heatmap-menu-subtitle">Active vehicles during the selected measurement window</div>
                  </div>
                  <button
                    type="button"
                    className="heatmap-collapse-toggle"
                    onClick={() => setIsHeatmapCollapsed((current) => !current)}
                    aria-expanded={!isHeatmapCollapsed}
                  >
                    <span aria-hidden="true">{isHeatmapCollapsed ? '▾' : '▴'}</span>
                    {isHeatmapCollapsed ? 'Expand' : 'Collapse'}
                  </button>
                </div>

                {!isHeatmapCollapsed && (
                  <div className="heatmap-menu-body">
                    <div className="heatmap-mode-row">
                      {[
                        ['before', 'Before'],
                        ['after', 'After'],
                        ['difference', 'Difference'],
                      ].map(([mode, label]) => (
                        <button
                          key={mode}
                          type="button"
                          className={`heatmap-mode-button ${heatmapMode === mode ? 'is-selected' : ''}`}
                          onClick={() => {
                            setHeatmapMode(mode);
                            setFocusedHeatmapEdgeId(null);
                            setFocusedHeatmapLocation(null);
                          }}
                        >
                          {label}
                        </button>
                      ))}
                    </div>

                    {heatmapScenarioKeys.length > 0 && (
                      <label className="heatmap-select-label">
                        After adoption scenario
                        <select
                          value={heatmapAdoption}
                          onChange={(event) => {
                            setHeatmapAdoption(event.target.value);
                            setFocusedHeatmapEdgeId(null);
                            setFocusedHeatmapLocation(null);
                          }}
                        >
                          {heatmapScenarioKeys.map((rate) => (
                            <option key={rate} value={rate}>{rate}% adoption</option>
                          ))}
                        </select>
                      </label>
                    )}

                    <div className="heatmap-scale">
                      <span><i className="heatmap-scale-swatch low" /> Low</span>
                      <span><i className="heatmap-scale-swatch medium" /> Medium</span>
                      <span><i className="heatmap-scale-swatch high" /> High</span>
                    </div>

                    {heatmapMode === 'difference' && (
                      <>
                        <div className="heatmap-view-toggle flex gap-2 mt-3">
                          {[
                            ['worsened', 'Worsened'],
                            ['improved', 'Improved'],
                          ].map(([value, label]) => (
                            <button
                              key={value}
                              type="button"
                              className={`heatmap-mode-button ${hotspotView === value ? 'is-selected' : ''}`}
                              onClick={() => setHotspotView(value)}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                        <div className="heatmap-difference-scale mt-2">
                          <span className="improved">Improved</span>
                          <span className="worsened">Worsened</span>
                        </div>
                      </>
                    )}

                    <label className="heatmap-select-label mt-3">
                      <input type="checkbox" checked={hideLowConfidenceEdges} onChange={(e) => setHideLowConfidenceEdges(e.target.checked)} /> Hide low-confidence edges
                    </label>

                    <div className="heatmap-stats">
                      <span>{heatmapFeatureCollection?.observed_edge_count || heatmapFeatureCollection?.features?.length || 0} road segments</span>
                      <span>{heatmapFeatureCollection?.observed_vehicle_count || 0} active vehicles</span>
                    </div>

                    {heatmapMode === 'difference' && (
                      <div className="heatmap-hotspots">
                        <div className={`heatmap-hotspots-title ${hotspotView === 'improved' ? 'improved' : ''}`}>
                          {hotspotView === 'improved' ? 'Improved hotspots' : 'Indirect hotspots'}
                          <span>{
                            hotspotView === 'improved'
                              ? improvedHotspotCount
                              : heatmapSummary.worsened_edge_count || 0
                          }</span>
                        </div>
                        {displayHotspots.length > 0 ? displayHotspots.slice(0, 10).map((hotspot) => {
                          const delta = Number(hotspot.delta_congestion || 0);
                          const isImproved = delta < 0;
                          const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
                          return (
                            <button
                              type="button"
                              className={`heatmap-hotspot ${focusedHeatmapEdgeId === hotspot.edge_id ? 'is-focused' : ''} ${isImproved ? 'is-improved' : 'is-worsened'}`}
                              key={hotspot.edge_id}
                              onClick={() => focusHeatmapEdge(hotspot)}
                            >
                              <div className="heatmap-hotspot-name">{hotspot.road_name || hotspot.edge_id}</div>
                              <div className="heatmap-hotspot-detail">
                                {sign}{(Math.abs(delta) * 100).toFixed(1)} pts · {Number(hotspot.after_speed_kmh || 0).toFixed(1)} km/h
                              </div>
                              <div className="heatmap-hotspot-edge">Edge: {hotspot.edge_id}</div>
                            </button>
                          );
                        }) : (
                          <div className="heatmap-no-hotspots">No significant hotspots detected.</div>
                        )}
                        {displayHotspots.length > 0 && displayHotspots.length < (hotspotView === 'improved' ? improvedHeatmapHotspots.length : heatmapSummary.worsened_edge_count || 0) && (
                          <div className="heatmap-hotspot-note">Showing top {displayHotspots.length} of {(hotspotView === 'improved' ? improvedHeatmapHotspots.length : heatmapSummary.worsened_edge_count || 0)} {hotspotView === 'improved' ? 'improved' : 'worsened'} hotspots.</div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="map-layer-control">
          <button
            type="button"
            className="map-layer-toggle"
            onClick={() => setShowLayerControls((current) => !current)}
            aria-expanded={showLayerControls}
          >
            <span aria-hidden="true">☷</span> Layers
          </button>
          {showLayerControls && (
            <div className="map-layer-menu">
              <div className="map-layer-menu-title">Map layers</div>
              <label className="map-layer-option">
                <input
                  type="checkbox"
                  checked={visibleLayers.existingRoutes}
                  onChange={(event) => setVisibleLayers((current) => ({ ...current, existingRoutes: event.target.checked }))}
                />
                <span className="map-layer-swatch existing" /> Existing routes
              </label>
              <label className="map-layer-option">
                <input
                  type="checkbox"
                  checked={visibleLayers.proposedRoute}
                  onChange={(event) => setVisibleLayers((current) => ({ ...current, proposedRoute: event.target.checked }))}
                />
                <span className="map-layer-swatch proposed" /> Proposed route
              </label>
              <label className="map-layer-option">
                <input
                  type="checkbox"
                  checked={visibleLayers.endpoints}
                  onChange={(event) => setVisibleLayers((current) => ({ ...current, endpoints: event.target.checked }))}
                />
                <span className="map-layer-point" /> A/B markers
              </label>
              <label className="map-layer-option">
                <input
                  type="checkbox"
                  checked={visibleLayers.collisions}
                  onChange={(event) => setVisibleLayers((current) => ({ ...current, collisions: event.target.checked }))}
                />
                <span className="map-layer-swatch" style={{ width: 12, height: 12, borderRadius: 2, background: '#ef4444' }} /> Colliding buildings
              </label>
            </div>
          )}
        </div>

        {/* STATUS BAR */}
        <div className="command-status absolute z-[999]">
          <div>
            {statusMsg}
          </div>
        </div>

        <div className="command-map-legend">
          <span className="map-legend-item"><span className="map-legend-swatch" /> Existing route</span>
          <span className="map-legend-item"><span className="map-legend-swatch proposed" /> Proposed route</span>
          {collisionGeoJSON?.features?.length > 0 && (
            <span className="map-legend-item">
              <span className="map-legend-swatch" style={{ width: 12, height: 12, borderRadius: 2, background: '#ef4444' }} />
              Colliding building ({countUniqueCollisionBuildings(collisionGeoJSON)})
            </span>
          )}
          {startLoc && <span className="map-legend-item"><span className="map-legend-point start">A</span> Origin</span>}
          {endLoc && <span className="map-legend-item"><span className="map-legend-point end">B</span> Destination</span>}
          {focusedRouteKey !== null && (
            <button type="button" className="map-focus-clear" onClick={showAllRoutes}>
              Show all routes
            </button>
          )}
        </div>
      </div>

      {/* WIZARD SIDEBAR */}
      <aside className={`command-sidebar z-[1000] flex flex-col ${isMapFocusMode ? 'is-hidden-in-focus-mode' : ''}`}>
        <div className="command-sidebar-header">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h1 className="command-sidebar-title text-gray-800">Command Center</h1>
              <p className="command-sidebar-subtitle">Model a corridor intervention</p>
            </div>
            {backendOnline === true && (
              <span className="api-status online">API online</span>
            )}
            {backendOnline === false && (
              <span className="api-status offline">API offline</span>
            )}
            <button
              type="button"
              onClick={handleReset}
              disabled={simulationRunning || analysisRunning || alternativesLoading}
              className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-bold text-gray-600 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reset
            </button>
          </div>
          <div className={`workflow-steps is-step-${step}`}>
            <div className={`workflow-step ${step > 1 ? 'is-complete' : 'is-active'}`}><span>1</span><label>ROUTE</label></div>
            <div className={`workflow-step ${step > 2 ? 'is-complete' : step === 2 ? 'is-active' : ''}`}><span>2</span><label>INTERVENE</label></div>
            <div className={`workflow-step ${step === 3 ? 'is-active' : ''}`}><span>3</span><label>SIMULATE</label></div>
          </div>
        </div>

        <div className="command-sidebar-content flex-1 overflow-y-auto space-y-8">

          {/* STEP 1 */}
          <section className={step === 1 ? 'block' : 'hidden'}>
            <h2 className="text-lg font-bold text-gray-800 mb-2">Select Corridor</h2>
            <p className="text-xs text-gray-500 mb-4">Choose a point on the map. Select Start Point A or Destination B below whenever you want to move one.</p>

            <div className="space-y-4">
              <div className={`p-3 border rounded-lg cursor-pointer transition-all ${isSearchingStart ? 'ring-2 ring-blue-500 bg-blue-50 border-blue-200' : 'border-gray-200'}`} onClick={() => { setIsSearchingStart(true); setStatusMsg('Click the map to move origin A.'); }}>
                <label className="text-xs font-bold text-gray-500 uppercase">Start Point (A)</label>
                <div className="text-sm font-medium text-gray-800">{startLoc ? startLoc.name : "Select Start..."}</div>
              <CoordinateEditor label="Origin coordinates" point={startLoc} onChange={(point) => updateRoutePoint('start', point)} onPick={() => { setIsSearchingStart(true); setStatusMsg('Click the map to choose origin A.'); }} />
              </div>

              <div className={`p-3 border rounded-lg cursor-pointer transition-all ${!isSearchingStart ? 'ring-2 ring-blue-500 bg-blue-50 border-blue-200' : 'border-gray-200'}`} onClick={() => { setIsSearchingStart(false); setStatusMsg('Click the map to move destination B.'); }}>
                <label className="text-xs font-bold text-gray-500 uppercase">Destination (B)</label>
                <div className="text-sm font-medium text-gray-800">{endLoc ? endLoc.name : "Select Destination..."}</div>
              <CoordinateEditor label="Destination coordinates" point={endLoc} onChange={(point) => updateRoutePoint('end', point)} onPick={() => { setIsSearchingStart(false); setStatusMsg('Click the map to choose destination B.'); }} />
              </div>

            </div>

            <div className="mt-4 grid grid-cols-2 gap-4">
              <TimeField label="Start time" value={startTime} onChange={handleStartTimeChange} minHour={7} maxHour={22} />
              <TimeField label="End time" value={endTime} onChange={setEndTime} minHour={8} maxHour={23} />
            </div>
            <div className="mt-4">
              <DayOfWeekField value={dayOfWeek} onChange={setDayOfWeek} />
            </div>

            <button
              disabled={!startLoc || !endLoc}
              onClick={handleRunSimulation}
              className="w-full mt-6 bg-blue-600 text-white py-3 rounded-lg font-bold shadow hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {simulationRunning ? 'SUMO Running...' : 'Run SUMO Simulation'}
            </button>
          </section>


          {/* STEP 2 */}
          <section className={step === 2 ? 'block' : 'hidden'}>
            <h2 className="text-lg font-bold text-gray-800 mb-4">Choose Solution</h2>

            {routes.length > 0 && (
              <div className="mb-5 space-y-2">
                {routes.map((route, index) => {
                  const routeKey = `${route.id}-${index}`;
                  const colors = ["border-blue-400", "border-green-400", "border-orange-400", "border-purple-400"];
                  const textColors = ["text-blue-700", "text-green-700", "text-orange-700", "text-purple-700"];
                  const isSelected = selectedRouteKey === routeKey;
                  return (
                    <div
                      key={routeKey}
                      onClick={() => toggleRoute(route, index)}
                      className={`rounded-lg border-2 p-3 text-xs cursor-pointer transition-all ${
                        isSelected
                          ? `${colors[index]} bg-gray-50`
                          : "border-gray-200 hover:border-gray-300"
                      }`}
                    >
                      <div className={`font-bold mb-1 ${textColors[index]}`}>
                        Route {route.id}
                      </div>
                      <div>Throughput: {route.metrics.throughput} vehicles</div>
                      <div>Avg time: {(route.metrics.avg_travel_time_sec / 60).toFixed(2)} min</div>
                      <div>Congestion: {route.metrics.avg_congestion_ratio}</div>
                      <div>Active: {route.metrics.active_vehicles}</div>
                    </div>
                  );
                })}
              </div>
              )}
            

            {/* Toggle Main Type */}
            <div className="flex bg-gray-100 p-1 rounded-lg mb-6">
              <button onClick={() => setInterventionType('infrastructure')} className={`flex-1 py-2 text-sm font-medium rounded-md ${interventionType === 'infrastructure' ? 'bg-white shadow text-blue-600' : 'text-gray-500'}`}>Build New</button>
              <button onClick={() => setInterventionType('optimize')} className={`flex-1 py-2 text-sm font-medium rounded-md ${interventionType === 'optimize' ? 'bg-white shadow text-purple-600' : 'text-gray-500'}`}>Optimize</button>
            </div>

            {interventionType === 'infrastructure' && (
              <div className="space-y-6 animate-fadeIn">
                {showAlternatives && (
                  <div className="space-y-3">
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-sm font-bold text-indigo-900">Find feasible alternatives</div>
                          <div className="text-xs text-indigo-700">The original intervention failed. These candidates are checked for collisions and Route 4 validity.</div>
                        </div>
                        <button
                          onClick={handleFindAlternatives}
                          disabled={!startLoc || !endLoc || alternativesLoading}
                          className="shrink-0 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {alternativesLoading ? 'Checking…' : 'Find Alternatives'}
                        </button>
                      </div>
                    </div>

                    {alternativeCandidates.length > 0 && (
                      <div className="space-y-2">
                        <div className="text-xs font-bold uppercase text-gray-500">Candidate alignments</div>
                        <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                          {alternativeCandidates.map((evaluation) => {
                            const candidate = evaluation.candidate;
                            const routeValidation = evaluation.route_validation || {};
                            const isSelected = selectedAlternative?.candidate?.candidate_id === candidate.candidate_id;
                            return (
                              <button
                                key={candidate.candidate_id}
                                onClick={() => handleSelectAlternative(evaluation)}
                                className={`w-full rounded-lg border-2 p-3 text-left text-xs transition-all ${
                                  isSelected ? 'border-indigo-500 bg-indigo-50' : 'border-gray-200 hover:border-indigo-300'
                                }`}
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-bold text-gray-800">#{evaluation.rank} {candidate.infrastructure?.label}</span>
                                  <span className={evaluation.ranking_eligible ? 'font-bold text-green-700' : 'font-bold text-red-600'}>
                                    {evaluation.ranking_eligible ? 'Route 4 ready' : evaluation.status}
                                  </span>
                                </div>
                                <div className="mt-1 text-gray-600">
                                  {candidate.candidate_type === 'same_endpoints' ? 'Same endpoints' : `${candidate.metadata?.endpoint_shift_m}m endpoint shift`}
                                  {routeValidation.route4_length_m ? ` · Route 4 ${Math.round(routeValidation.route4_length_m)}m` : ''}
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                <div>
                  <label className="text-xs font-bold text-gray-500 uppercase block mb-2">Project Type</label>
                  <div className="grid grid-cols-2 gap-3">
                    <button type="button" onClick={() => changeProjectMode('package')} className={`p-3 border rounded-lg text-left ${projectMode === 'package' ? 'border-blue-500 bg-blue-50' : 'hover:bg-gray-50'}`}>
                      <div className={`text-sm font-bold ${projectMode === 'package' ? 'text-blue-700' : 'text-gray-800'}`}>Package</div>
                      <div className="mt-1 text-xs text-gray-500">One project: main structure plus connectors and widening, built together.</div>
                    </button>
                    <button type="button" onClick={() => changeProjectMode('compare')} className={`p-3 border rounded-lg text-left ${projectMode === 'compare' ? 'border-blue-500 bg-blue-50' : 'hover:bg-gray-50'}`}>
                      <div className={`text-sm font-bold ${projectMode === 'compare' ? 'text-blue-700' : 'text-gray-800'}`}>Compare options</div>
                      <div className="mt-1 text-xs text-gray-500">Up to 3 alternatives, each simulated on its own and ranked.</div>
                    </button>
                  </div>
                </div>

                {projectMode === 'package' && (
                  <label className="block rounded-lg border border-gray-200 p-3 text-xs text-gray-500">
                    <span className="block mb-1 font-bold uppercase">Package adoption %</span>
                    <select value={packageAdoption} onChange={(event) => setPackageAdoption(Number(event.target.value))} className="w-full rounded border px-2 py-1">
                      {ADOPTION_RATES.map((rate) => <option key={rate} value={rate}>{rate}%</option>)}
                    </select>
                    <span className="mt-1 block">Share of corridor vehicles that take the new route through the main structure. Widening needs no adoption: every vehicle on the widened road gets the extra lanes.</span>
                  </label>
                )}

                {!isAddingInfrastructure && infraItems.length === 0 && (
                <button type="button" onClick={beginAddInfrastructure} className="w-full rounded-lg border-2 border-dashed border-blue-300 bg-blue-50 p-4 text-left text-blue-700 hover:border-blue-500 hover:bg-blue-100">
                  <span className="block text-sm font-extrabold">Add infrastructure</span>
                  <span className="mt-1 block text-xs text-blue-600">Start a new intervention and configure it step by step.</span>
                </button>
                )}

                {isAddingInfrastructure && <div>
                  <label className="text-xs font-bold text-gray-500 uppercase block mb-2">Structure Type</label>
                  <div className="grid grid-cols-3 gap-3">
                    {infraTypesForMode(projectMode).map((type) => (
                      <button key={type} onClick={() => selectInfrastructureType(type)} className={`p-3 border rounded-lg text-sm text-center ${infraType === type ? 'border-blue-500 bg-blue-50 text-blue-700 font-bold' : 'hover:bg-gray-50'}`}>{INFRA_TYPE_LABELS[type]}</button>
                    ))}
                  </div>
                </div>}

                {!isAddingInfrastructure && <div>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <label className="text-xs font-bold text-gray-500 uppercase block">Project infrastructure items</label>
                    <span className="text-[10px] uppercase tracking-wide text-gray-400">{infraItems.length} total</span>
                  </div>
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <p className="text-[11px] text-gray-500">Use the checkbox to include each item in the next analysis run. Select a row to edit its geometry and settings.</p>
                    <button
                      type="button"
                      onClick={beginAddInfrastructure}
                      className="shrink-0 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-[10px] font-bold uppercase tracking-wide text-blue-700 hover:border-blue-300 hover:bg-blue-100"
                    >
                      Add another intervention
                    </button>
                  </div>
                  <div className="space-y-3">
                    {infraItems.length === 0 && (
                      <div className="rounded-lg border border-dashed border-gray-300 p-3 text-xs text-gray-500">No infrastructure items yet. Draw a path or auto-connect the route to create the first one.</div>
                    )}
                    {infraItems.map((item) => (
                      <div key={item.id} className={`rounded-lg border p-3 space-y-2 ${selectedInfraId === item.id ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="flex items-center gap-1.5 min-w-[110px] text-[10px] font-bold uppercase text-gray-600">
                            <input type="checkbox" checked={activeInfraIds.includes(item.id)} onChange={() => {
                              setActiveInfraIds((current) => current.includes(item.id)
                                ? current.filter((id) => id !== item.id)
                                : [...current, item.id]);
                            }} className="h-4 w-4" />
                            Include
                          </label>
                          <span className="text-xs font-bold text-gray-700">#{item.serial_number}</span>
                          <input value={item.name} onChange={(event) => {
                            setInfraItems((current) => current.map((entry) => entry.id === item.id ? { ...entry, name: event.target.value } : entry));
                          }} className="min-w-[130px] flex-1 rounded border px-2 py-1 text-xs" />
                          <button type="button" onClick={() => {
                            setSelectedInfraId(item.id);
                            setInfraType(item.infra_type);
                            setDrawnFeature({ type: 'Feature', geometry: item.geometry, properties: {} });
                            setStatusMsg(`Selected ${item.name}.`);
                          }} className="rounded bg-gray-100 px-2 py-1 text-xs">Select</button>
                          <button type="button" onClick={() => {
                            setInfraItems((current) => current.filter((entry) => entry.id !== item.id));
                            setActiveInfraIds((current) => current.filter((id) => id !== item.id));
                            if (selectedInfraId === item.id) setSelectedInfraId(null);
                          }} className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">Delete</button>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                          <label className="text-xs text-gray-500">
                            <span className="block mb-1 font-bold uppercase">Type</span>
                            <select className="w-full rounded border px-2 py-1" value={item.infra_type} onChange={(event) => {
                              const nextType = event.target.value;
                              setInfraItems((current) => current.map((entry) => entry.id === item.id
                                ? { ...entry, infra_type: nextType, is_main: NEW_ALIGNMENT_TYPES.includes(nextType) ? entry.is_main : false }
                                : entry));
                            }}>
                              {infraTypesForMode(projectMode).map((type) => (
                                <option key={type} value={type}>{INFRA_TYPE_LABELS[type]}</option>
                              ))}
                              {!infraTypesForMode(projectMode).includes(item.infra_type) && (
                                <option value={item.infra_type} disabled>{item.infra_type} (not allowed here)</option>
                              )}
                            </select>
                          </label>
                          <label className="text-xs text-gray-500">
                            <span className="block mb-1 font-bold uppercase">{laneFieldLabel(item.infra_type)}</span>
                            <select
                              className="w-full rounded border px-2 py-1"
                              value={widthToLaneCount(item.road_width_m, item.infra_type === 'widening' ? 1 : 2)}
                              onChange={(event) => {
                                const lanes = Number(event.target.value);
                                setInfraItems((current) => current.map((entry) => entry.id === item.id
                                  ? { ...entry, road_width_m: lanes * LANE_WIDTH_M }
                                  : entry));
                              }}
                            >
                              {laneOptionsForDirection(item.is_oneway).map((lanes) => <option key={lanes} value={lanes}>{lanes} {lanes === 1 ? 'lane' : 'lanes'}</option>)}
                            </select>
                          </label>
                          <label className="text-xs text-gray-500">
                            <span className="block mb-1 font-bold uppercase">Direction</span>
                            <select className="w-full rounded border px-2 py-1" value={item.is_oneway ? 'oneway' : 'twoway'} onChange={(event) => {
                              const isOneway = event.target.value === 'oneway';
                              setInfraItems((current) => current.map((entry) => entry.id === item.id
                                ? { ...entry, is_oneway: isOneway, road_width_m: !isOneway && widthToLaneCount(entry.road_width_m, 2) < 2 ? 2 * LANE_WIDTH_M : entry.road_width_m }
                                : entry));
                            }}>
                              <option value="twoway">Two-way</option>
                              <option value="oneway">One-way</option>
                            </select>
                          </label>
                          {projectMode === 'compare' ? (
                            <label className="text-xs text-gray-500">
                              <span className="block mb-1 font-bold uppercase">Adoption %</span>
                              <select value={item.adoption_rate_pct ?? ADOPTION_RATES[ADOPTION_RATES.length - 1]} onChange={(event) => {
                                setInfraItems((current) => current.map((entry) => entry.id === item.id ? { ...entry, adoption_rate_pct: Number(event.target.value) } : entry));
                              }} className="w-full rounded border px-2 py-1">
                              {ADOPTION_RATES.map((rate) => <option key={rate} value={rate}>{rate}%</option>)}
                            </select>
                            </label>
                          ) : NEW_ALIGNMENT_TYPES.includes(item.infra_type) ? (
                            <label className="flex items-end gap-2 pb-1 text-xs text-gray-600">
                              <input type="radio" name="package-main" checked={Boolean(item.is_main)} onChange={() => setMainInfra(item.id)} className="h-4 w-4" />
                              <span className="font-bold uppercase">Main structure</span>
                            </label>
                          ) : (
                            <div className="flex items-end pb-1 text-xs text-gray-400">No adoption needed</div>
                          )}
                        </div>
                        {['road_block', 'widening'].includes(item.infra_type) && item.edge_label && (
                          <div className="rounded border border-violet-200 bg-violet-50 p-2 text-[10px] text-violet-800">
                            <div className="font-bold uppercase tracking-wide">Selected corridor edge</div>
                            <div className="mt-1 text-violet-900">{item.edge_label}</div>
                          </div>
                        )}
                        <InfrastructureCoordinates item={item} onChange={(geometry) => updateInfrastructureGeometry(item.id, geometry)} />
                      </div>
                    ))}
                  </div>
                </div>}

                {isAddingInfrastructure && <div>
                  <label className="text-xs font-bold text-gray-500 uppercase block mb-2">How to create the infrastructure</label>
                  <div className="space-y-2">
                    {(infraType === 'road_block' || infraType === 'widening') ? (
                      <button onClick={startEdgeSelection} className={`edge-selection-button w-full flex items-center justify-between p-3 border rounded-lg text-left hover:border-violet-300 ${drawMethod === 'edge' ? 'border-violet-500 bg-violet-50' : ''}`}>
                        <span className="text-sm font-medium">Select corridor edge</span>
                        <span className="text-xs text-gray-400">Click on road segment</span>
                      </button>
                    ) : (
                      <>
                        <button onClick={handleAutoConnect} className={`w-full flex items-center justify-between p-3 border rounded-lg text-left hover:border-blue-300 ${drawMethod === 'auto' ? 'border-blue-500 bg-blue-50' : ''}`}>
                          <span className="text-sm font-medium">Auto-connect route</span>
                          <span className="text-xs text-gray-400">Straight line</span>
                        </button>
                        <button onClick={startManualDraw} className={`w-full flex items-center justify-between p-3 border rounded-lg text-left hover:border-blue-300 ${drawMethod === 'manual' ? 'border-blue-500 bg-blue-50' : ''}`}>
                          <span className="text-sm font-medium">Draw on the map</span>
                          <span className="text-xs text-gray-400">Polyline tool</span>
                        </button>
                      </>
                    )}
                  </div>
                  {drawMethod === 'edge' && (
                    <div className="mt-3 space-y-2 rounded-lg border border-violet-200 bg-violet-50 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-bold text-violet-900">Selected edges</span>
                        <span className="text-xs text-violet-700">{selectedEdges.length}</span>
                      </div>
                      <p className="text-[11px] text-violet-800">Choose connected road segments for one continuous stretch. Selected segments turn green.</p>
                      {selectedEdges.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {selectedEdges.map((edge) => (
                            <button
                              key={edge.key}
                              type="button"
                              onClick={() => setSelectedEdges((current) => current.filter((item) => item.key !== edge.key))}
                              className="rounded border border-violet-200 bg-white px-2 py-1 text-[10px] text-violet-800 hover:bg-violet-100"
                              title={`Remove ${edge.label}`}
                            >
                              {edge.label} ×
                            </button>
                          ))}
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={finishEdgeSelection}
                        disabled={selectedEdges.length === 0}
                        className="w-full rounded-lg bg-violet-700 px-3 py-2 text-xs font-extrabold text-white hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Done — create one {INFRA_TYPE_LABELS[infraType] || infraType}
                      </button>
                    </div>
                  )}
                  {draftInfrastructure && (
                    <div className="mt-4 space-y-3 rounded-lg border border-blue-200 bg-blue-50 p-3">
                      <div className="text-xs font-extrabold uppercase tracking-wide text-blue-800">Intervention details</div>
                      <label className="block text-xs text-blue-900">
                        Name
                        <input value={draftInfrastructure.name} onChange={(event) => setDraftInfrastructure((current) => ({ ...current, name: event.target.value }))} className="mt-1 w-full rounded border px-2 py-1 text-xs" />
                      </label>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="text-xs text-blue-900">{laneFieldLabel(draftInfrastructure.infra_type)}
                          <select
                            value={widthToLaneCount(draftInfrastructure.road_width_m, draftInfrastructure.infra_type === 'widening' ? 1 : 2)}
                            onChange={(event) => setDraftInfrastructure((current) => ({ ...current, road_width_m: Number(event.target.value) * LANE_WIDTH_M }))}
                            className="mt-1 w-full rounded border px-2 py-1"
                          >
                            {laneOptionsForDirection(draftInfrastructure.is_oneway).map((lanes) => <option key={lanes} value={lanes}>{lanes} {lanes === 1 ? 'lane' : 'lanes'}</option>)}
                          </select>
                        </label>
                        <label className="text-xs text-blue-900">Adoption<select value={draftInfrastructure.adoption_rate_pct} onChange={(event) => setDraftInfrastructure((current) => ({ ...current, adoption_rate_pct: Number(event.target.value) }))} className="mt-1 w-full rounded border px-2 py-1">{ADOPTION_RATES.map((rate) => <option key={rate} value={rate}>{rate}%</option>)}</select></label>
                      </div>
                      <label className="flex items-center gap-2 text-xs text-blue-900"><input type="checkbox" checked={draftInfrastructure.is_oneway} onChange={(event) => setDraftInfrastructure((current) => {
                        const isOneway = event.target.checked;
                        return current ? {
                          ...current,
                          is_oneway: isOneway,
                          road_width_m: !isOneway && widthToLaneCount(current.road_width_m, 2) < 2 ? 2 * LANE_WIDTH_M : current.road_width_m,
                        } : current;
                      })} /> One-way intervention</label>
                      <div className="text-[11px] text-blue-700">{draftInfrastructure.edge_label || `${draftInfrastructure.geometry.coordinates.length} geometry points captured`}</div>
                      <button type="button" onClick={finishAddInfrastructure} className="w-full rounded-lg bg-blue-600 px-3 py-2 text-xs font-extrabold text-white hover:bg-blue-700">Done — register intervention</button>
                    </div>
                  )}
                </div>}
              </div>
            )}

            {interventionType === 'optimize' && (
              <div className="space-y-4 animate-fadeIn">
                <p className="text-xs text-purple-700 bg-purple-50 border border-purple-200 rounded-lg p-3">
                  Phase 2 optimization — heuristic estimates applied to the report until the full engine ships.
                </p>
                <label className="flex items-center gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                  <input type="checkbox" checked={optimizeOptions.widening} onChange={() => setOptimizeOptions(s => ({ ...s, widening: !s.widening }))} className="w-5 h-5 accent-purple-600" />
                  <div>
                    <div className="text-sm font-bold text-gray-800">Road Widening</div>
                    <div className="text-xs text-gray-500">Add lanes to existing route</div>
                  </div>
                </label>
                <label className="flex items-center gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                  <input type="checkbox" checked={optimizeOptions.signal} onChange={() => setOptimizeOptions(s => ({ ...s, signal: !s.signal }))} className="w-5 h-5 accent-purple-600" />
                  <div>
                    <div className="text-sm font-bold text-gray-800">Smart Signals</div>
                    <div className="text-xs text-gray-500">AI-driven signal timing</div>
                  </div>
                </label>
              </div>
            )}

            <div className="flex gap-3 mt-8">
              <button onClick={() => setStep(1)} className="px-4 py-3 text-sm font-bold text-gray-500 hover:bg-gray-100 rounded-lg">Back</button>
              <button
                disabled={!canProceedStep2}
                onClick={() => setStep(3)}
                className="flex-1 bg-blue-600 text-white py-3 rounded-lg font-bold shadow hover:bg-blue-700 disabled:opacity-50"
              >
                Next: Analyze
              </button>
            </div>
            {canReturnToPreviousSimulation && (
              <button
                type="button"
                onClick={returnToPreviousSimulation}
                className="mt-3 w-full rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm font-semibold text-blue-800 hover:bg-blue-100 flex items-center justify-center gap-2"
              >
                <ArrowRight className="h-4 w-4" />
                Return to previous simulation
              </button>
            )}
          </section>

          {/* STEP 3 */}
          <section className={step === 3 ? 'block' : 'hidden'}>
            <h2 className="text-lg font-bold text-gray-800 mb-4">Finalize & Simulate</h2>

            {analysisComplete && analysisMetrics ? (
              <div className="space-y-6 animate-fadeIn">
                <div className={analysisMetrics.is_feasible === false
                  ? 'bg-red-50 border border-red-200 rounded-xl p-4 text-xs space-y-2'
                  : 'bg-green-50 border border-green-200 rounded-xl p-4 text-xs space-y-2'}>
                  <h3 className={analysisMetrics.is_feasible === false
                    ? 'font-extrabold text-red-950 text-sm mb-1'
                    : 'font-extrabold text-green-950 text-sm mb-1'}>
                    {analysisMetrics.is_feasible === false ? 'Feasibility Failed — Simulation Preview' : 'Intervention Active'}
                  </h3>
                  {analysisMetrics.is_feasible === false && (
                    <div className="rounded bg-red-100 px-2 py-1 text-red-900 font-semibold">
                      Proposed route intersects {collisionGeoJSON?.features?.length
                        ? countUniqueCollisionBuildings(collisionGeoJSON)
                        : analysisMetrics.collision_count} building(s). Review the routes and statistics before construction.
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span>Travel Time Before:</span>
                    <span className="font-bold">{analysisMetrics.avg_travel_time_before_min} min</span>
                  </div>
                  <div className="flex justify-between">
                    <span>{analysisMetrics.is_feasible === false ? 'Simulated Time After (Preview):' : 'Travel Time After:'}</span>
                    <span className={analysisMetrics.is_feasible === false ? 'font-bold text-red-700' : 'font-bold text-green-700'}>{analysisMetrics.avg_travel_time_after_min} min</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Average Speed Before:</span>
                    <span className="font-bold">{analysisMetrics.avg_speed_before_kmh ?? '—'} km/h</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Average Speed After:</span>
                    <span className={analysisMetrics.is_feasible === false ? 'font-bold text-red-700' : 'font-bold text-green-700'}>{analysisMetrics.avg_speed_after_kmh ?? '—'} km/h</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Corridor Throughput Before:</span>
                    <span className="font-bold">{analysisMetrics.before_throughput ?? '—'} veh</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Corridor Throughput After:</span>
                    <span className="font-bold text-green-700">{analysisMetrics.after_throughput ?? '—'} veh</span>
                  </div>
                  {analysisMetrics.comparison?.scenarios?.length > 0 && (
                    <div className="pt-1 border-t border-green-200 space-y-1">
                      <div className="font-bold">Comparison (map shows #1)</div>
                      {[...analysisMetrics.comparison.scenarios].sort((a, b) => a.rank - b.rank).map((row) => (
                        <div key={row.key} className="flex justify-between">
                          <span>#{row.rank} {row.label}</span>
                          <span className="font-bold">{row.error ? 'failed' : `${row.timeSavedPct ?? '—'}% time saved`}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {analysisMetrics.adoption_rate_pct !== undefined && (
                    <div className="flex justify-between pt-1 border-t border-green-200 font-semibold">
                      <span>Primary Route 4 Adoption (your input):</span>
                      <span className="text-green-800">
                        {analysisMetrics.adoption_rate_pct}% ({analysisMetrics.adopted_vehicle_count ?? '—'} of {analysisMetrics.eligible_vehicle_count ?? '—'} veh)
                      </span>
                    </div>
                  )}
                  {analysisMetrics.time_saved_pct !== undefined && (
                    <div className="flex justify-between pt-1 border-t border-green-200 font-semibold">
                      <span>Travel Time Saved:</span>
                      <span className="text-green-800">{analysisMetrics.time_saved_pct}%</span>
                    </div>
                  )}
                </div>

                {analysisMetrics.simulation_scenarios?.length > 0 && (
                  <div>
                    <h3 className="text-sm font-bold text-gray-800 mb-3">Adoption Scenario Comparison (SUMO)</h3>
                    <div className="space-y-3 max-h-96 overflow-y-auto pr-1">
                      {analysisMetrics.simulation_scenarios.map((scenario) => {
                        const scenarioRoutes = scenario.routes || [];
                        const assignedTotal = Number(scenario.eligible_vehicle_count)
                          || scenarioRoutes.reduce((total, route) => total + Number(route.assigned_vehicle_count || 0), 0);
                        return (
                          <div key={`scenario-${scenario.adoption_rate_pct}`} className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs">
                            <div className="flex items-center justify-between font-bold text-gray-800">
                              <span>{scenario.adoption_rate_pct}% adoption</span>
                              <span className="text-green-700">Route 4: {scenario.adopted_vehicle_count ?? '—'} vehicles</span>
                            </div>
                            <div className="grid grid-cols-2 gap-2 mt-2 text-gray-600">
                              <span>After time: <b>{scenario.avg_travel_time_after_min ?? '—'} min</b></span>
                              <span>After speed: <b>{scenario.avg_speed_after_kmh ?? '—'} km/h</b></span>
                              <span>Congestion: <b>{scenario.congestion_reduction_pct ?? '—'}%</b></span>
                            </div>
                            <div className="grid grid-cols-4 gap-1 mt-2">
                              {scenarioRoutes.map((route) => (
                                <div key={`${scenario.adoption_rate_pct}-route-${route.id}`} className="rounded bg-white p-1 text-center">
                                  <div className="font-bold">R{route.id}</div>
                                  <div>{route.assigned_vehicle_count ?? 0} veh</div>
                                  <div>{assignedTotal > 0 ? `${((Number(route.assigned_vehicle_count || 0) / assignedTotal) * 100).toFixed(1)}%` : '—'}</div>
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                <div>
                  <h3 className="text-sm font-bold text-gray-800 mb-3">Route Split Performance (SUMO)</h3>
                  <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
                    {routes.map((route, index) => {
                      const colors = ["border-blue-400", "border-green-400", "border-orange-400", "border-purple-400"];
                      const textColors = ["text-blue-700", "text-green-700", "text-orange-700", "text-purple-700"];
                      const routeKey = `${route.id}-${index}`;
                      const isSelected = selectedRouteKey === routeKey;
                      return (
                        <div
                          key={routeKey}
                          onClick={() => toggleRoute(route, index)}
                          className={`rounded-lg border-2 p-3 text-xs cursor-pointer transition-all ${
                            isSelected
                              ? `${colors[index % colors.length]} bg-gray-50`
                              : "border-gray-200 hover:border-gray-300"
                          }`}
                        >
                          <div className={`font-bold mb-1 flex justify-between items-center ${textColors[index % textColors.length]}`}>
                            <span>Route {route.id} {route.id === 4 ? '(New Road)' : ''}</span>
                            {isSelected && <span className="text-[10px] bg-gray-200 px-1.5 py-0.5 rounded">selected</span>}
                          </div>
                          <div className="grid grid-cols-2 gap-x-2 text-gray-600 mt-1">
                            <div>Assigned: <span className="font-bold text-gray-800">{route.assigned_vehicle_count ?? route.metrics?.matched_vehicle_count ?? 0} veh</span></div>
                            <div>Throughput: <span className="font-bold text-gray-800">{route.metrics?.throughput ?? 0} veh</span></div>
                            <div>Avg Time: <span className="font-bold text-gray-800">{(route.metrics?.avg_travel_time_sec / 60).toFixed(2)} min</span></div>
                            <div>Avg Speed: <span className="font-bold text-gray-800">{route.metrics?.avg_speed_kmh ?? '—'} km/h</span></div>
                            <div>Congestion: <span className="font-bold text-gray-800">{route.metrics?.avg_congestion_ratio ?? 0}</span></div>
                            <div>Active: <span className="font-bold text-gray-800">{route.metrics?.active_vehicles ?? 0}</span></div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-2 pt-4">
                  <button
                    type="button"
                    onClick={beginEditingCompletedAnalysis}
                    className="w-full bg-blue-50 hover:bg-blue-100 text-blue-800 py-2.5 rounded-lg text-sm font-semibold flex items-center justify-center gap-2"
                  >
                    <ArrowLeft className="h-4 w-4" />
                    Edit interventions
                  </button>
                  <button
                    onClick={onViewReport}
                    className="w-full bg-green-600 text-white py-3 rounded-lg font-bold shadow-lg hover:bg-green-700 flex items-center justify-center gap-2"
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                    </svg>
                    View Full Report
                  </button>
                  <button
                    onClick={handleReset}
                    className="w-full bg-gray-100 hover:bg-gray-200 text-gray-700 py-2.5 rounded-lg text-sm font-semibold"
                  >
                    Reset & Start Over
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                <div className="bg-gray-50 p-4 rounded-lg border mb-6 text-sm text-gray-600 space-y-2">
                  <div className="flex justify-between"><span>Start:</span> <span className="font-bold text-gray-800">{startLoc?.name}</span></div>
                  <div className="flex justify-between"><span>End:</span> <span className="font-bold text-gray-800">{endLoc?.name}</span></div>
                  <hr className="border-gray-200" />
                  <div className="flex justify-between"><span>Action:</span> <span className="font-bold text-blue-600">{interventionType === 'infrastructure' ? `Build ${infraType}` : 'Optimize Route'}</span></div>
                </div>

                <div className="mb-4 grid grid-cols-2 gap-4">
                  <TimeField label="Start time" value={startTime} onChange={handleStartTimeChange} minHour={7} maxHour={22} />
                  <TimeField label="End time" value={endTime} onChange={setEndTime} minHour={8} maxHour={23} />
                </div>

                <div className="mb-6">
                  <label className="text-xs font-bold text-gray-500 uppercase mb-1 block">Send Report To (Optional)</label>
                  <div className="email-editor" onDoubleClick={() => setEditingEmail(true)} title="Double-click to edit email">
                    {editingEmail ? (
                      <div className="email-editor-fields">
                        <input className="w-full p-3 border rounded-lg" placeholder="Email Address" value={email} onChange={e => setEmail(e.target.value)} autoFocus />
                        <button type="button" onClick={() => setEditingEmail(false)}>Save</button>
                      </div>
                    ) : (
                      <div className="email-editor-value">{email || 'No email set'} <span>Double-click to edit</span></div>
                    )}
                  </div>
                </div>

                <div className="flex gap-3">
                  <button onClick={() => setStep(2)} className="px-4 py-3 text-sm font-bold text-gray-500 hover:bg-gray-100 rounded-lg">Back</button>
                  <button
                    onClick={handleGenerate}
                    disabled={backendOnline === false || analysisRunning}
                    className="flex-1 bg-green-600 text-white py-3 rounded-lg font-bold shadow-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {analysisRunning ? 'Running Analysis…' : 'Run Analysis'}
                  </button>
                </div>
                <p className="text-[11px] text-gray-500 text-center mt-2.5 font-medium">
                  Analysis: May take a few minutes (SUMO micro-simulation & heatmap calculation).
                </p>
              </div>
            )}
          </section>

        </div>
      </aside>

      {analysisRunning && (
        <div className="absolute inset-0 bg-slate-950/70 backdrop-blur-md z-[3000] flex items-center justify-center p-6 text-white">
          <div className="bg-slate-900 border border-slate-700/80 p-8 rounded-2xl max-w-md w-full shadow-2xl space-y-5 text-center">
            <div className="inline-flex p-4 rounded-full bg-blue-500/20 text-blue-400 animate-pulse mx-auto">
              <svg className="w-9 h-9 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            </div>
            <h3 className="text-xl font-extrabold text-slate-100">Simulating Digital Twin</h3>
            <p className="text-xs text-slate-300 font-medium leading-relaxed">{statusMsg}</p>
            <div className="w-full bg-slate-800 rounded-full h-2.5 overflow-hidden p-0.5 border border-slate-700">
              <div className="bg-gradient-to-r from-blue-500 to-indigo-500 h-full rounded-full transition-all duration-500" style={{ width: `${analysisProgress}%` }} />
            </div>
            <div className="text-[10px] text-slate-500 tracking-wider font-semibold uppercase">SUMO Microscopic Engine + NetworkX Graph Analysis</div>
          </div>
        </div>
      )}
    </div>
  );
}
