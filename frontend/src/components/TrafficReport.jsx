import React, { useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import {
  AlertTriangle, BarChart3, CheckCircle2, Clock, FileSpreadsheet, MapPin, TrendingDown, Upload, Users,
} from 'lucide-react';
import { Moon, Sun } from 'lucide-react';
import { apiUrl } from '../api';

const numberOrDash = (value, suffix = '') => (
  value === null || value === undefined || value === '' ? '—' : `${value}${suffix}`
);

const displayPercent = (value) => (
  Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : '—'
);

const parseCsvLine = (line) => {
  const values = [];
  let value = '';
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (character === '"' && line[index + 1] === '"' && quoted) {
      value += '"';
      index += 1;
    } else if (character === '"') {
      quoted = !quoted;
    } else if (character === ',' && !quoted) {
      values.push(value.trim());
      value = '';
    } else {
      value += character;
    }
  }
  values.push(value.trim());
  return values;
};

const parseCsv = (text) => {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) return [];
  const headers = parseCsvLine(lines[0]);
  return lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    return headers.reduce((record, header, index) => {
      record[header] = values[index]?.trim() ?? '';
      return record;
    }, {});
  });
};

const metricValue = (metric, key) => metric?.[key] == null ? '—' : Number(metric[key]).toFixed(2);

const BTPValidationPanel = () => {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError('');
    setResult(null);
    setLoading(true);
    try {
      const text = await file.text();
      const parsed = file.name.toLowerCase().endsWith('.json') ? JSON.parse(text) : parseCsv(text);
      const records = Array.isArray(parsed) ? parsed : parsed?.records;
      if (!Array.isArray(records)) throw new Error('The file must contain an array of records.');
      const response = await fetch(apiUrl('/api/validation/btp'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ records }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'BTP validation failed.');
      setResult(payload);
    } catch (validationError) {
      setError(validationError.message || 'Unable to read the BTP file.');
    } finally {
      setLoading(false);
      event.target.value = '';
    }
  };

  const metrics = [
    ['Speed (km/h)', result?.metrics?.speed_kmh],
    ['Travel time (s)', result?.metrics?.travel_time_s],
  ];

  return (
    <section className="report-card btp-validation-card">
      <div className="btp-validation-heading">
        <div>
          <p className="report-section-label">EXTERNAL VALIDATION</p>
          <h2 className="report-card-title"><FileSpreadsheet className="h-5 w-5" /> BTP observation check</h2>
        </div>
        <label className="report-action btp-upload-button">
          <Upload className="h-4 w-4" /> Upload CSV/JSON
          <input type="file" accept=".csv,.json,application/json,text/csv" onChange={handleFile} hidden />
        </label>
      </div>
      <p className="text-xs text-slate-500">Use paired observed_speed_kmh, simulated_speed_kmh, observed_travel_time_s, and simulated_travel_time_s columns. Missing pairs are excluded, never imputed.</p>
      {loading && <p className="btp-validation-status">Calculating reproducible error metrics...</p>}
      {error && <p className="btp-validation-error">{error}</p>}
      {result && (
        <div className="btp-validation-result">
          <div className="btp-validation-status"><CheckCircle2 className="h-4 w-4" />{result.status === 'complete' ? 'Paired validation metrics computed' : 'No complete observation pairs found'}</div>
          <div className="btp-validation-meta">{result.record_count} records · {result.excluded_record_count} excluded</div>
          <div className="btp-metric-grid">
            {metrics.map(([label, metric]) => (
              <div className="btp-metric-group" key={label}>
                <strong>{label}</strong>
                <span>MAE <b>{metricValue(metric, 'mae')}</b></span>
                <span>RMSE <b>{metricValue(metric, 'rmse')}</b></span>
                <span>MAPE <b>{metricValue(metric, 'mape_pct')}%</b></span>
                <span>Bias <b>{metricValue(metric, 'bias')}</b></span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
};

const ReportSummary = ({ data, onOpenVariant }) => {
  const summary = data.overallSummary || {};
  const rows = [...(summary.scenarios || [])].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const openable = new Set((data.variants || []).filter((variant) => variant.report).map((variant) => variant.key));

  return (
    <>
      <section className="report-hero">
        <div className="report-hero-copy">
          <p className="report-kicker">UIDT / INFRASTRUCTURE COMPARISON</p>
          <h1 className="report-title">{data.reportName || 'Infrastructure comparison'}</h1>
          <p className="report-subtitle">
            Each option was simulated on its own on {summary.route || `${data.fromPlace || 'the selected origin'} to ${data.toPlace || 'the selected destination'}`}, at its own adoption rate.
          </p>
          <div className="report-hero-status">{summary.verdict || `${rows.length} options analyzed`}</div>
        </div>
      </section>
      <section className="report-card">
        <p className="report-section-label">RANKING</p>
        <p className="text-xs text-slate-500 mb-3">
          Ranked by buildability (no building collisions and a valid new route), then travel time saved, then congestion reduction.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm border-collapse">
            <thead>
              <tr className="text-xs uppercase text-slate-500">
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Rank</th>
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Option</th>
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Buildable</th>
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Adoption</th>
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Travel time</th>
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Time saved</th>
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Congestion</th>
                <th className="p-2.5 border-b border-slate-200 dark:border-slate-800" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {rows.map((row) => (
                <tr key={row.key} className={row.key === summary.winnerKey ? 'bg-blue-50/40 dark:bg-blue-950/30 font-semibold' : ''}>
                  <td className="p-2.5 font-bold">#{row.rank}</td>
                  <td className="p-2.5">{row.label} <span className="text-xs text-slate-500">({row.infraType})</span></td>
                  {row.error ? (
                    <td className="p-2.5 text-red-600" colSpan={5}>Failed: {row.error}</td>
                  ) : (
                    <>
                      <td className="p-2.5">
                        {row.feasible && row.route4Valid
                          ? 'Yes'
                          : !row.feasible
                            ? `No (${row.collisionCount} collisions)`
                            : 'No (no new route)'}
                      </td>
                      <td className="p-2.5">{displayPercent(row.adoptionRatePct)}</td>
                      <td className="p-2.5">{numberOrDash(row.travelTimeBeforeMin, ' min')} → {numberOrDash(row.travelTimeAfterMin, ' min')}</td>
                      <td className="p-2.5 text-emerald-600 dark:text-emerald-400">{displayPercent(row.timeSavedPct)}</td>
                      <td className="p-2.5 text-emerald-600 dark:text-emerald-400">{displayPercent(row.congestionReductionPct)}</td>
                    </>
                  )}
                  <td className="p-2.5">
                    {openable.has(row.key) && (
                      <button type="button" className="report-action" onClick={() => onOpenVariant(row.key)}>Details</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
};

const PackageItems = ({ items, widenedEdgeCount }) => (
  <section className="report-card">
    <p className="report-section-label">PACKAGE ITEMS</p>
    <p className="text-xs text-slate-500 mb-3">
      All items were built into the network together. Route 4 runs through the main structure; connectors are used wherever they shorten the trip.
      {widenedEdgeCount > 0 && ` Widening added lanes to ${widenedEdgeCount} existing road segment(s).`}
    </p>
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm border-collapse">
        <thead>
          <tr className="text-xs uppercase text-slate-500">
            <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Item</th>
            <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Role</th>
            <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Type</th>
            <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Length</th>
            <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Size</th>
            <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Feasibility</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
          {items.map((item) => (
            <tr key={`${item.index}-${item.name}`}>
              <td className="p-2.5 font-bold">{item.name}</td>
              <td className="p-2.5 capitalize">{item.role}</td>
              <td className="p-2.5">{item.infra_type}</td>
              <td className="p-2.5">{numberOrDash(item.length_m, ' m')}</td>
              <td className="p-2.5">{item.infra_key === 'widening' ? `+${item.extra_lanes} lane(s)` : `${item.road_width_m} m wide`}</td>
              <td className={`p-2.5 ${item.is_feasible ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600'}`}>
                {item.is_feasible ? 'Approved' : `${item.collision_count} collision(s)`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </section>
);

const SCOPE_ORDER = ['corridor', 'affected', 'network'];
const SCOPE_NAMES = {
  corridor: 'Selected corridor',
  affected: 'Vehicles using modified roads',
  network: 'Whole network',
};

const secondsToMinutes = (seconds) => (
  seconds == null || !Number.isFinite(Number(seconds)) ? '—' : `${(Number(seconds) / 60).toFixed(2)} min`
);

const signedSeconds = (seconds) => {
  if (seconds == null || !Number.isFinite(Number(seconds))) return '—';
  const value = Number(seconds);
  return `${value > 0 ? '+' : ''}${value.toFixed(1)} s`;
};

const formatPValue = (value) => {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return Number(value) < 0.001 ? '< 0.001' : Number(value).toFixed(3);
};

const tableCell = 'p-2.5 border-b border-slate-200 dark:border-slate-800';

const RoadModificationPanel = ({ simulation, modification, blockedEdgeCount, widenedEdgeCount, lanesAddedCount }) => {
  const scopes = simulation.metric_scopes || {};
  const primaryScope = simulation.primary_metric_scope;
  const sumoStats = simulation.sumo_statistics || {};
  const routes = Array.isArray(simulation.routes) ? simulation.routes : [];
  const stats = modification || simulation.road_modification || {};
  const teleportsBefore = sumoStats.before?.teleport_count;
  const teleportsAfter = sumoStats.after?.teleport_count;
  const facts = [
    blockedEdgeCount > 0 && ['Segments blocked', blockedEdgeCount],
    widenedEdgeCount > 0 && ['Segments widened', widenedEdgeCount],
    widenedEdgeCount > 0 && ['Lanes added', lanesAddedCount],
    ['Vehicles measured', stats.measured_vehicle_count],
    ['Using modified roads', stats.affected_vehicle_count],
    blockedEdgeCount > 0 && ['Rerouted', stats.rerouted_vehicle_count],
    blockedEdgeCount > 0 && ['Origins / destinations moved', `${stats.relocated_origin_count ?? 0} / ${stats.relocated_destination_count ?? 0}`],
    blockedEdgeCount > 0 && ['No open path', stats.unroutable_vehicle_count],
    ['Teleports before → after', `${teleportsBefore ?? '—'} → ${teleportsAfter ?? '—'}`],
  ].filter(Boolean);

  return (
    <section className="report-card">
      <h2 className="report-card-title"><TrendingDown className="h-5 w-5" /> Road network modification</h2>
      <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
        {facts.map(([label, value]) => (
          <div key={label} className="report-kpi">
            <div className="report-kpi-label">{label.toUpperCase()}</div>
            <div className="text-xl font-extrabold">{numberOrDash(value)}</div>
          </div>
        ))}
      </div>

      {Object.keys(scopes).length > 0 && (
        <>
          <h3 className="report-section-label mt-6">BEFORE / AFTER BY VEHICLE COHORT</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="uppercase text-slate-500">
                  <th className={tableCell}>Cohort</th>
                  <th className={tableCell}>Vehicles</th>
                  <th className={tableCell}>Paired</th>
                  <th className={tableCell}>Mean before</th>
                  <th className={tableCell}>Mean after</th>
                  <th className={tableCell}>Δ per vehicle (95% CI)</th>
                  <th className={tableCell}>Completed before → after</th>
                  <th className={tableCell}>Wilcoxon p</th>
                </tr>
              </thead>
              <tbody>
                {SCOPE_ORDER.filter((key) => scopes[key]).map((key) => {
                  const scope = scopes[key];
                  const paired = scope.paired || {};
                  const ci = paired.ci95_mean_delta_sec;
                  return (
                    <tr key={key} className={key === primaryScope ? 'bg-blue-50/40 dark:bg-blue-950/30 font-semibold' : ''}>
                      <td className="p-2.5">{SCOPE_NAMES[key]}{key === primaryScope ? ' · headline' : ''}</td>
                      <td className="p-2.5">{scope.vehicle_count}</td>
                      <td className="p-2.5">{paired.paired_vehicle_count ?? 0}</td>
                      <td className="p-2.5">{secondsToMinutes(paired.mean_before_sec)}</td>
                      <td className="p-2.5">{secondsToMinutes(paired.mean_after_sec)}</td>
                      <td className="p-2.5">
                        {signedSeconds(paired.mean_delta_sec)}
                        {Array.isArray(ci) && ` [${signedSeconds(ci[0])}, ${signedSeconds(ci[1])}]`}
                      </td>
                      <td className="p-2.5">{displayPercent(scope.before?.completion_rate_pct)} → {displayPercent(scope.after?.completion_rate_pct)}</td>
                      <td className="p-2.5">{formatPValue(paired.wilcoxon_p_value)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Each cohort is the same set of vehicles in both runs. Means are paired per vehicle over trips that finished in both runs, and a negative Δ means a faster trip.
            {blockedEdgeCount > 0 && ' Only vehicles whose route used a blocked segment were rerouted; every other vehicle kept its baseline route.'}
          </p>
        </>
      )}

      {routes.length > 0 && (
        <>
          <h3 className="report-section-label mt-6">CORRIDOR ROUTES</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="uppercase text-slate-500">
                  <th className={tableCell}>Route</th>
                  <th className={tableCell}>Vehicles</th>
                  <th className={tableCell}>Modified segments</th>
                  <th className={tableCell}>Avg time before</th>
                  <th className={tableCell}>Avg time after</th>
                  <th className={tableCell}>Speed before → after</th>
                </tr>
              </thead>
              <tbody>
                {routes.map((route) => (
                  <tr key={`road-mod-route-${route.id}`}>
                    <td className="p-2.5 font-bold">Route {route.id}</td>
                    <td className="p-2.5">{route.assigned_vehicle_count ?? 0}</td>
                    <td className="p-2.5">
                      {route.blocked_edge_count > 0 && `${route.blocked_edge_count} blocked `}
                      {route.widened_edge_count > 0 && `${route.widened_edge_count} widened`}
                      {!route.blocked_edge_count && !route.widened_edge_count && '—'}
                    </td>
                    <td className="p-2.5">{secondsToMinutes(route.before_metrics?.avg_travel_time_sec || null)}</td>
                    <td className="p-2.5">{secondsToMinutes(route.metrics?.avg_travel_time_sec || null)}</td>
                    <td className="p-2.5">{numberOrDash(route.before_metrics?.avg_speed_kmh)} → {numberOrDash(route.metrics?.avg_speed_kmh, ' km/h')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {simulation.warnings?.length > 0 && (
        <p className="mt-3 text-xs text-amber-800">{simulation.warnings.join(' ')}</p>
      )}
    </section>
  );
};

const ReportDetail = ({ data, darkMode = false, onToggleDarkMode }) => {
  if (!data) {
    return (
      <div className="empty-state min-h-full flex items-center justify-center p-8 text-center">
        <div>
          <div className="empty-state-mark mx-auto">UIDT</div>
          <p className="text-gray-600">Run an analysis from the Command Center to generate a report.</p>
        </div>
      </div>
    );
  }

  const feasibility = data.feasibility || {};
  const impact = data.impact || {};
  const simulation = data.simulation || {};
  const simulationScenarios = data.simulationScenarios?.length
    ? data.simulationScenarios
    : (simulation.scenarios?.length
      ? simulation.scenarios
      : (simulation.status === 'complete' ? [simulation] : []));
  const primaryRate = Number(simulation.adoption_rate_pct);
  const primarySimulation = simulationScenarios.find((scenario) => Number.isFinite(primaryRate) && Number(scenario.adoption_rate_pct) === primaryRate)
    || simulationScenarios[simulationScenarios.length - 1]
    || simulation;
  const selectedPrimaryRate = primarySimulation.adoption_rate_pct;
  const simulationRoutes = Array.isArray(primarySimulation.routes) ? primarySimulation.routes : [];
  const assignedVehicleTotal = Number(primarySimulation.eligible_vehicle_count)
    || simulationRoutes.reduce((total, route) => total + Number(route.assigned_vehicle_count || 0), 0);
  const smart = data.smartRecommendation || {};
  const hourlyTraffic = data.hourlyTraffic?.length ? data.hourlyTraffic : [];
  const cautions = data.cautions?.length ? data.cautions : ['No specific cautions returned.'];
  const phase2 = data.phase2 || {};
  const artifacts = data.artifacts || {};
  const isWideningOnly = data.isWideningOnly ?? data.report?.isWideningOnly ?? false;
  const isRoadBlockOnly = data.isRoadBlockOnly ?? data.report?.isRoadBlockOnly ?? false;
  const isRoadModificationOnly = data.isRoadModificationOnly
    ?? data.report?.isRoadModificationOnly
    ?? (isWideningOnly || isRoadBlockOnly);
  const widenedEdgeCount = Number(data.widenedEdgeCount ?? data.report?.widenedEdgeCount ?? 0);
  const blockedEdgeCount = Number(data.blockedEdgeCount ?? data.report?.blockedEdgeCount ?? 0);
  const lanesAddedCount = Number(data.lanesAddedCount ?? data.report?.lanesAddedCount ?? widenedEdgeCount);
  const roadModification = data.roadModification ?? data.report?.roadModification ?? simulation.road_modification;
  const headlineScope = SCOPE_NAMES[primarySimulation.primary_metric_scope];
  const infraLabel = data.solutions?.type === 'optimize'
    ? 'Route Optimization'
    : data.solutions?.infraType
      ? data.solutions.infraType.charAt(0).toUpperCase() + data.solutions.infraType.slice(1)
      : 'Infrastructure';
  const proposedRouteLabel = data.selectedAlternative?.infrastructureType || infraLabel;
  const timeSaved = primarySimulation.time_reduction_pct ?? impact.time_saved_pct;
  const congestionReduction = primarySimulation.congestion_reduction_pct ?? impact.congestion_reduction_pct;
  const adoption = primarySimulation.actual_adoption_rate_pct ?? primarySimulation.adoption_rate_pct;
  const hasBeforeAfterMetrics = primarySimulation.avg_travel_time_before_min != null
    && primarySimulation.avg_travel_time_after_min != null;
  const comparisonUnavailable = primarySimulation.status !== 'complete' || !hasBeforeAfterMetrics;
  const simulationError = data.route4Error || data.route4_error || data.report?.route4Error || primarySimulation.error;

  return (
    <main className="report-shell">
      <div className="report-container">
        <section className="report-hero">
          <button
            type="button"
            className="theme-toggle report-theme-toggle"
            onClick={onToggleDarkMode}
            aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
            title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            <span>{darkMode ? 'Light mode' : 'Dark mode'}</span>
          </button>
          <div className="report-hero-copy">
            <p className="report-kicker">UIDT / TRAFFIC IMPACT REPORT</p>
            <h1 className="report-title">{data.reportName || 'Corridor intervention report'}</h1>
            <p className="report-subtitle">
              A decision-ready view of the proposed {infraLabel.toLowerCase()} intervention, based on the selected corridor and SUMO simulation.
            </p>
            <div className={`report-hero-status ${!feasibility.is_feasible ? 'failed' : ''}`}>
              {feasibility.is_feasible ? 'Feasibility approved' : 'Feasibility failed'} · {feasibility.collision_count ?? 0} building collisions
            </div>
            <div className="report-actions mt-4">
              {artifacts.map_html && <a className="report-action" href={artifacts.map_html} target="_blank" rel="noreferrer">Infrastructure map ↗</a>}
              {artifacts.demand_map_html && <a className="report-action" href={artifacts.demand_map_html} target="_blank" rel="noreferrer">Demand map ↗</a>}
              {artifacts.report_markdown && <a className="report-action" href={artifacts.report_markdown} download="phase34_impact_report.md">Impact report ↓</a>}
            </div>
          </div>
          <div className="report-hero-visual" aria-hidden="true">
            <div className="report-hero-grid" />
            <div className="report-route-line" />
            <div className="report-route-node start" />
            <div className="report-route-node end" />
            <div className="report-route-tag start">{data.fromPlace || 'Origin A'}</div>
            <div className="report-route-tag end">{data.toPlace || 'Destination B'}</div>
          </div>
        </section>

        <section className="report-card">
          <p className="report-section-label">SELECTED CORRIDOR</p>
          <div className="report-route-grid">
            <div className="report-route">
              <div className="report-route-label"><MapPin className="mr-1 inline h-3 w-3" /> FROM</div>
              <div className="report-route-name">{data.fromPlace || 'Selected origin'}</div>
            </div>
            <div className="report-route">
              <div className="report-route-label"><MapPin className="mr-1 inline h-3 w-3" /> TO</div>
              <div className="report-route-name">{data.toPlace || 'Selected destination'}</div>
            </div>
          </div>
        </section>

        {data.packageItems?.length > 1 && (
          <PackageItems items={data.packageItems} widenedEdgeCount={data.widenedEdgeCount || 0} />
        )}

        {data.selectedAlternative && (
          <section className="report-card report-recommendation">
            <div className="report-recommendation-label">SELECTED ALTERNATIVE</div>
            <h3>{data.selectedAlternative.infrastructureType || 'Infrastructure'} · {data.selectedAlternative.candidateId}</h3>
            <p>
              Ranked #{data.selectedAlternative.rank ?? '—'} · Route 4 length {data.selectedAlternative.route4LengthM
                ? `${Math.round(data.selectedAlternative.route4LengthM)} m` : '—'}
            </p>
          </section>
        )}

        {comparisonUnavailable && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <div className="font-bold">Before/after travel-time comparison unavailable for this run — showing corridor-only preview.</div>
            {simulationError && <div className="mt-1">{simulationError}</div>}
          </div>
        )}

        <div className="report-kpi-grid">
          <div className="report-kpi">
            <div className="report-kpi-label">TRAVEL TIME SAVED</div>
            <div className="report-kpi-value positive">{displayPercent(timeSaved)}</div>
            <div className="report-kpi-note">
              {numberOrDash(primarySimulation.avg_travel_time_before_min, ' min')} → {numberOrDash(primarySimulation.avg_travel_time_after_min, ' min')}
              {isRoadModificationOnly && headlineScope && ` · ${headlineScope.toLowerCase()}`}
            </div>
          </div>
          <div className="report-kpi">
            <div className="report-kpi-label">CONGESTION REDUCTION</div>
            <div className="report-kpi-value positive">{displayPercent(congestionReduction)}</div>
            <div className="report-kpi-note">{isRoadModificationOnly ? 'Modified road network vs. baseline' : `Primary ${selectedPrimaryRate ?? '—'}% adoption scenario`}</div>
          </div>
          {isWideningOnly ? (
            <div className="report-kpi">
              <div className="report-kpi-label">LANES ADDED</div>
              <div className="report-kpi-value">{numberOrDash(lanesAddedCount)}</div>
              <div className="report-kpi-note">across {widenedEdgeCount} road segment(s)</div>
            </div>
          ) : isRoadBlockOnly ? (
            <div className="report-kpi">
              <div className="report-kpi-label">ROAD SEGMENTS BLOCKED</div>
              <div className="report-kpi-value">{numberOrDash(blockedEdgeCount)}</div>
              <div className="report-kpi-note">
                {roadModification?.rerouted_vehicle_count != null
                  ? `${roadModification.rerouted_vehicle_count} vehicle(s) rerouted around the closure`
                  : 'existing road segment(s) closed'}
              </div>
            </div>
          ) : isRoadModificationOnly ? (
            <div className="report-kpi">
              <div className="report-kpi-label">ROAD SEGMENTS MODIFIED</div>
              <div className="report-kpi-value">{numberOrDash(widenedEdgeCount + blockedEdgeCount)}</div>
              <div className="report-kpi-note">widened or blocked</div>
            </div>
          ) : (
            <div className="report-kpi">
              <div className="report-kpi-label">NEW ROUTE ADOPTION</div>
              <div className="report-kpi-value">{displayPercent(adoption)}</div>
              <div className="report-kpi-note">{primarySimulation.adopted_vehicle_count ?? '—'} of {primarySimulation.eligible_vehicle_count ?? '—'} eligible vehicles</div>
            </div>
          )}
        </div>

        {isRoadModificationOnly && (data.route4Error || data.route4_error || data.report?.route4Error) && (
          <p className="mt-2 text-xs text-amber-800">
            Road-modification simulation used the corridor fallback: {data.route4Error || data.route4_error || data.report?.route4Error}
          </p>
        )}



        <div className="report-two-column">
          <section className="report-card">
            <h2 className="report-card-title"><TrendingDown className="h-5 w-5" /> Intervention impact</h2>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="report-kpi"><div className="report-kpi-label">TRAVEL TIME BEFORE</div><div className="text-xl font-extrabold">{numberOrDash(primarySimulation.avg_travel_time_before_min, ' min')}</div></div>
              <div className="report-kpi"><div className="report-kpi-label">TRAVEL TIME AFTER</div><div className="text-xl font-extrabold text-green-700">{numberOrDash(primarySimulation.avg_travel_time_after_min, ' min')}</div></div>
              <div className="report-kpi"><div className="report-kpi-label">SPEED BEFORE</div><div className="text-xl font-extrabold">{numberOrDash(primarySimulation.avg_speed_before_kmh, ' km/h')}</div></div>
              <div className="report-kpi"><div className="report-kpi-label">SPEED AFTER</div><div className="text-xl font-extrabold text-green-700">{numberOrDash(primarySimulation.avg_speed_after_kmh, ' km/h')}</div></div>
            </div>
          </section>

          <section className="report-card report-recommendation">
            <div className="report-recommendation-label">SMART ARCHITECT</div>
            <h3>{smart.recommendation || `${infraLabel} scenario`}</h3>
            {smart.confidence != null && <div className="report-confidence">Confidence: {smart.confidence}%</div>}
            {smart.reasoning && <p>{smart.reasoning}</p>}
            {!smart.recommendation && <p>Review the scenario comparison and route distribution below for the full recommendation.</p>}
          </section>
        </div>

        <section className="report-card">
          <h2 className="report-card-title"><BarChart3 className="h-5 w-5" /> Simulation overview</h2>
          <div className="flex flex-wrap items-end justify-between gap-4 mb-5">
            <div>
              <div className="text-4xl font-extrabold tracking-tight text-slate-900">{(data.totalVehicles ?? 0).toLocaleString()}</div>
              <div className="mt-1 text-xs text-slate-500">{data.vehicleCountLabel || 'Vehicles in the SUMO simulation window'}</div>
            </div>
            <div className="text-right text-xs text-slate-500">
              <div>Source</div>
              <div className="mt-1 font-semibold text-slate-700">{data.routeFileSource || 'auto_tool/simulation.rou.xml'}</div>
            </div>
          </div>
          {data.routes?.length > 0 ? (
            <div className="report-sumo-routes grid grid-cols-1 gap-2 md:grid-cols-2">
              {data.routes.map((route, idx) => (
                <div key={idx} className={`report-sumo-route route-share-${idx}`}>
                  <div className="sumo-route-head">
                    <div className="sumo-route-name"><span className="sumo-route-index">0{idx + 1}</span><span>{route.name}</span></div>
                    <strong>{(route.vehicles ?? 0).toLocaleString()} <small>vehicles</small></strong>
                  </div>
                  <div className="sumo-route-stats">
                    <div><span>Throughput</span><b>{route.throughput ?? '—'} veh</b></div>
                    <div><span>Average time</span><b>{route.time || '—'}</b></div>
                  </div>
                </div>
              ))}
            </div>
          ) : <p className="text-xs text-slate-500">Route-level corridor details are available below when SUMO matches vehicles to the selected route.</p>}
        </section>

        {isRoadModificationOnly && (
          <RoadModificationPanel
            simulation={primarySimulation}
            modification={roadModification}
            blockedEdgeCount={blockedEdgeCount}
            widenedEdgeCount={widenedEdgeCount}
            lanesAddedCount={lanesAddedCount}
          />
        )}

        {phase2.status === 'heuristic_preview' && (phase2.widening_impact || phase2.signal_optimization) && (
          <section className="report-card report-recommendation">
            <div className="report-recommendation-label">PHASE 2 · OPTIMIZATION PREVIEW</div>
            {phase2.widening_impact && <p>{phase2.widening_impact.summary}</p>}
            {phase2.signal_optimization && <p>{phase2.signal_optimization.summary}</p>}
          </section>
        )}

        {!isRoadModificationOnly && simulationScenarios.length > 0 && (
          <section className="report-card">
            <h2 className="report-card-title"><Users className="h-5 w-5" /> Adoption scenario comparison</h2>
            <div className="overflow-x-auto my-4">
              <table className="w-full text-xs text-left border-collapse border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden">
                <thead className="bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 font-semibold">
                  <tr>
                    <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Adoption Target</th>
                    <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Diverted Vehicles (Route 4)</th>
                    <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Avg Travel Time</th>
                    <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Time Reduction</th>
                    <th className="p-2.5 border-b border-slate-200 dark:border-slate-800">Congestion Impact</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                  {simulationScenarios.map((scenario) => (
                    <tr key={`matrix-${scenario.adoption_rate_pct}`} className={Number(scenario.adoption_rate_pct) === Number(selectedPrimaryRate) ? 'bg-blue-50/40 dark:bg-blue-950/30 font-semibold' : ''}>
                      <td className="p-2.5 font-bold">{scenario.adoption_rate_pct}% Adoption</td>
                      <td className="p-2.5 text-purple-600 dark:text-purple-400 font-medium">{scenario.adopted_vehicle_count ?? '—'} veh</td>
                      <td className="p-2.5">{numberOrDash(scenario.avg_travel_time_after_min, ' min')}</td>
                      <td className="p-2.5 text-emerald-600 dark:text-emerald-400">{displayPercent(scenario.time_reduction_pct)}</td>
                      <td className="p-2.5 text-emerald-600 dark:text-emerald-400">{displayPercent(scenario.congestion_reduction_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="report-scenario-list">
              {simulationScenarios.map((scenario, scenarioIndex) => {
                const scenarioRoutes = scenario.routes || [];
                const scenarioTotal = Number(scenario.eligible_vehicle_count)
                  || scenarioRoutes.reduce((total, route) => total + Number(route.assigned_vehicle_count || 0), 0);
                const adoptionRate = Number(scenario.adoption_rate_pct) || 0;
                return (
                  <div key={`report-scenario-${scenario.adoption_rate_pct}`} className={`report-scenario scenario-band-${scenarioIndex} ${adoptionRate === Number(selectedPrimaryRate) ? 'is-primary' : ''}`}>
                    <div className="scenario-head">
                      <div className="scenario-badge"><strong>{scenario.adoption_rate_pct}%</strong><span>adoption</span></div>
                      <div className="scenario-headline">
                        <div className="scenario-kicker">SCENARIO {scenarioIndex + 1}</div>
                        <h3>{adoptionRate === Number(selectedPrimaryRate) ? 'Recommended reference scenario' : 'Network response scenario'}</h3>
                      </div>
                      <div className="scenario-route4">
                        <span>ROUTE 4</span>
                        <strong>{scenario.adopted_vehicle_count ?? '—'}</strong>
                        <small>vehicles</small>
                      </div>
                    </div>
                    <div className="scenario-rate-meter"><div style={{ width: `${Math.min(100, adoptionRate)}%` }} /></div>
                    <div className="report-scenario-metrics">
                      <span>After time<b>{numberOrDash(scenario.avg_travel_time_after_min, ' min')}</b></span>
                      <span>After speed<b>{numberOrDash(scenario.avg_speed_after_kmh, ' km/h')}</b></span>
                      <span>Time saved<b>{displayPercent(scenario.time_reduction_pct)}</b></span>
                      <span>Congestion<b>{displayPercent(scenario.congestion_reduction_pct)}</b></span>
                      <span>Eligible<b>{scenario.eligible_vehicle_count ?? '—'}</b></span>
                    </div>
                    <div className="scenario-route-grid">
                      {scenarioRoutes.map((route) => {
                        const routeShare = scenarioTotal > 0 ? (Number(route.assigned_vehicle_count || 0) / scenarioTotal) * 100 : 0;
                        return (
                        <div key={`report-${scenario.adoption_rate_pct}-route-${route.id}`} className={`scenario-route-card ${route.id === 4 ? 'is-new-road' : ''} ${routeShare === 0 ? 'is-empty' : ''}`}>
                          <div className="scenario-route-title"><span>Route {route.id}</span><strong>{routeShare.toFixed(1)}%</strong></div>
                          <div className="scenario-route-bar"><div style={{ width: `${Math.min(100, routeShare)}%` }} /></div>
                          <div className="scenario-route-note">{route.assigned_vehicle_count ?? 0} vehicles {route.id === 4 ? '· proposed' : ''}</div>
                        </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {!isRoadModificationOnly && simulationScenarios.length > 0 && simulationRoutes.length > 0 && (
          <section className="report-card route-analysis-card">
            <div className="route-analysis-header">
              <div>
                <h2 className="report-card-title">Route distribution</h2>
                <p>Where traffic is flowing in the primary {selectedPrimaryRate ?? '—'}% adoption scenario.</p>
              </div>
              <span className="route-analysis-pill">{selectedPrimaryRate ?? '—'}% adoption</span>
            </div>
            <div className="route-stacked-bar" aria-label="Traffic share by route">
              {simulationRoutes.map((route, idx) => {
                const routeShare = assignedVehicleTotal > 0 ? (Number(route.assigned_vehicle_count || 0) / assignedVehicleTotal) * 100 : 0;
                return <div key={`stack-${route.id}`} className={`route-segment route-segment-${idx}`} style={{ width: `${Math.min(100, routeShare)}%` }} title={`Route ${route.id}: ${routeShare.toFixed(1)}%`} />;
              })}
            </div>
            <div className="report-route-share">
              {simulationRoutes.map((route, idx) => {
                const assignedVehicles = Number(route.assigned_vehicle_count || 0);
                const routeShare = assignedVehicleTotal > 0 ? (assignedVehicles / assignedVehicleTotal) * 100 : 0;
                return (
                  <div key={`distribution-${route.id}`} className={`report-route-share-card route-share-${idx} ${route.id === 4 ? 'is-new-road' : ''}`}>
                    <div className="report-route-share-head"><span><i />Route {route.id}</span><strong>{routeShare.toFixed(1)}%</strong></div>
                    <div className="report-progress"><div style={{ width: `${Math.min(100, routeShare)}%` }} /></div>
                    <div className="report-route-share-note">{assignedVehicles} assigned vehicles</div>
                  </div>
                );
              })}
            </div>

            <h3 className="report-section-label mt-7">ROUTE PERFORMANCE DETAILS</h3>
            <div className="report-detail-grid">
              {simulationRoutes.map((route) => {
                const assignedVehicles = Number(route.assigned_vehicle_count || 0);
                const routeLabel = route.id === 4 ? `· ${proposedRouteLabel}` : '';
                return (
                  <div key={`detail-${route.id}`} className={`report-detail route-detail-${route.id} ${route.id === 4 ? 'is-new-road' : ''}`}>
                    <h4>Route {route.id} {routeLabel}</h4>
                    <div className="report-detail-row"><span>Assigned</span><strong>{route.assigned_vehicle_count ?? route.metrics?.matched_vehicle_count ?? 0} veh</strong></div>
                    <div className="report-detail-row"><span>Share</span><strong>{assignedVehicleTotal > 0 ? `${((assignedVehicles / assignedVehicleTotal) * 100).toFixed(1)}%` : '—'}</strong></div>
                    <div className="report-detail-row"><span>Throughput</span><strong>{route.metrics?.throughput ?? 0} veh</strong></div>
                    <div className="report-detail-row"><span>Avg time</span><strong>{route.metrics?.avg_travel_time_sec != null ? `${(route.metrics.avg_travel_time_sec / 60).toFixed(2)} min` : '—'}</strong></div>
                    <div className="report-detail-row"><span>Avg speed</span><strong>{numberOrDash(route.metrics?.avg_speed_kmh, ' km/h')}</strong></div>
                    <div className="report-detail-row"><span>Congestion</span><strong>{route.metrics?.avg_congestion_ratio ?? '—'}</strong></div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {hourlyTraffic.length > 0 && (
          <section className="report-card">
            <h2 className="report-card-title"><Clock className="h-5 w-5" /> Hourly traffic volume</h2>
            <div className="report-chart">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={hourlyTraffic} margin={{ top: 8, right: 12, left: -12, bottom: 4 }}>
                  <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                  <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#64748b' }} />
                  <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="before" name="Current baseline" stroke="#64748b" strokeWidth={2} dot={false} />
                  <Line dataKey="after" name="After intervention" stroke="#2563eb" strokeWidth={2.5} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}

        <BTPValidationPanel />

        <section className="report-card report-cautions">
          <h2 className="report-card-title"><AlertTriangle className="h-5 w-5" /> Implementation challenges</h2>
          <div className="report-caution-grid">
            {cautions.map((caution, idx) => (
              <div key={idx} className="report-caution"><AlertTriangle className="h-4 w-4" /><span>{caution}</span></div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
};

const CompareReport = ({ data, darkMode, onToggleDarkMode, onVariantSelect }) => {
  const [activeKey, setActiveKey] = React.useState('summary');
  const variants = (data.variants || []).filter((variant) => variant.report);
  const activeVariant = variants.find((variant) => variant.key === activeKey);
  const selectVariant = (key) => {
    setActiveKey(key);
    const variantKey = key === 'summary' ? data.overallSummary?.winnerKey : key;
    const variant = variants.find((item) => item.key === variantKey);
    onVariantSelect?.(variant?.report?.artifacts || null);
  };

  return (
    <div>
      <nav className="report-container report-actions" style={{ paddingTop: '1rem' }}>
        <button type="button" className="report-action" aria-pressed={activeKey === 'summary'} onClick={() => selectVariant('summary')}>
          {activeKey === 'summary' ? '● ' : ''}Comparison
        </button>
        {variants.map((variant) => (
          <button key={variant.key} type="button" className="report-action" aria-pressed={activeKey === variant.key} onClick={() => selectVariant(variant.key)}>
            {activeKey === variant.key ? '● ' : ''}{variant.label}
          </button>
        ))}
      </nav>
      {activeVariant ? (
        <ReportDetail data={{ ...activeVariant.report, fromPlace: data.fromPlace, toPlace: data.toPlace }} darkMode={darkMode} onToggleDarkMode={onToggleDarkMode} />
      ) : (
        <main className="report-shell">
          <div className="report-container">
            <ReportSummary data={data} onOpenVariant={selectVariant} />
          </div>
        </main>
      )}
    </div>
  );
};

const TrafficReport = ({ data, darkMode = false, onToggleDarkMode, onCompareVariantSelect }) => {
  if (data?.projectMode === 'compare') {
    return <CompareReport data={data} darkMode={darkMode} onToggleDarkMode={onToggleDarkMode} onVariantSelect={onCompareVariantSelect} />;
  }
  return <ReportDetail data={data} darkMode={darkMode} onToggleDarkMode={onToggleDarkMode} />;
};

export default TrafficReport;
