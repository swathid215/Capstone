#!/usr/bin/env python3
"""Compute per-edge FCD vs tripinfo congestion correlation across runs.

Usage: python3 backend/tools/heatmap_correlation_report.py /path/to/run_dir1 /path/to/run_dir2 ...

Each run directory should contain `fcd_before.xml`, `fcd_after.xml`, `tripinfo_before.xml`, `tripinfo_after.xml`.
"""
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
from collections import defaultdict
from statistics import mean
import math


def parse_fcd_edge_congestion(fcd_path, net_dummy=None):
    # Minimal parser to compute average speed per edge and return congestion (1 - avg_speed / speed_limit)
    if not fcd_path.exists():
        return {}
    samples = defaultdict(lambda: {'speed_sum': 0.0, 'count': 0})
    for event, elem in ET.iterparse(str(fcd_path), events=('end',)):
        if elem.tag != 'timestep':
            continue
        for veh in elem.findall('vehicle'):
            lane = veh.get('lane') or ''
            edge = lane.rsplit('_', 1)[0]
            try:
                speed = float(veh.get('speed') or 0.0)
            except (TypeError, ValueError):
                continue
            samples[edge]['speed_sum'] += speed
            samples[edge]['count'] += 1
        elem.clear()

    out = {}
    for edge, s in samples.items():
        avg_speed = (s['speed_sum'] / s['count']) * 3.6 if s['count'] else 0.0
        # unknown speed_limit -> assume 50 km/h
        congestion = max(0.0, min(1.0, 1.0 - (avg_speed / 50.0)))
        out[edge] = {'avg_speed_kmh': avg_speed, 'congestion': congestion, 'sample_count': s['count']}
    return out


def parse_tripinfo_congestion(tripinfo_path):
    if not tripinfo_path.exists():
        return {}
    root = ET.parse(str(tripinfo_path)).getroot()
    trips = list(root.findall('tripinfo'))
    # tripinfo has per-vehicle waiting and duration; aggregate to route-edge mapping isn't available, so provide global avg
    durations = []
    waiting = []
    for trip in trips:
        try:
            d = float(trip.get('duration') or 0)
            w = float(trip.get('waitingTime') or 0)
        except (TypeError, ValueError):
            continue
        if d > 0:
            durations.append(d)
            waiting.append(w)

    if not durations:
        return {'avg_congestion_ratio': None}
    avg_travel = mean(durations)
    avg_wait = mean(waiting) if waiting else 0.0
    return {'avg_congestion_ratio': min(1.0, avg_wait / avg_travel if avg_travel else 0.0)}


def pearson(xs, ys):
    if not xs or not ys or len(xs) != len(ys):
        return None
    n = len(xs)
    mean_x = mean(xs)
    mean_y = mean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    deny = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)


def main(run_dirs):
    per_run = []
    for rd in run_dirs:
        rd = Path(rd)
        fcd_before = rd / 'fcd_before.xml'
        fcd_after = rd / 'fcd_after.xml'
        trip_before = rd / 'tripinfo_before.xml'
        trip_after = rd / 'tripinfo_after.xml'

        f_before = parse_fcd_edge_congestion(fcd_before)
        f_after = parse_fcd_edge_congestion(fcd_after)
        t_before = parse_tripinfo_congestion(trip_before)
        t_after = parse_tripinfo_congestion(trip_after)

        # compute per-edge delta congestion and global tripinfo delta
        edges = set(f_before.keys()) | set(f_after.keys())
        deltas = {}
        for e in edges:
            cb = f_before.get(e, {}).get('congestion', 0.0)
            ca = f_after.get(e, {}).get('congestion', 0.0)
            deltas[e] = ca - cb

        trip_delta = None
        if t_before.get('avg_congestion_ratio') is not None and t_after.get('avg_congestion_ratio') is not None:
            trip_delta = t_after['avg_congestion_ratio'] - t_before['avg_congestion_ratio']

        per_run.append({'run': str(rd), 'edge_deltas': deltas, 'trip_delta': trip_delta})

    # Combine across runs: for edges present in multiple runs, build arrays of (edge_delta, trip_delta) and compute pearson
    edge_series = defaultdict(lambda: {'x': [], 'y': []})
    for item in per_run:
        trip_delta = item['trip_delta']
        if trip_delta is None:
            continue
        for e, ed in item['edge_deltas'].items():
            edge_series[e]['x'].append(ed)
            edge_series[e]['y'].append(trip_delta)

    results = []
    for e, s in edge_series.items():
        corr = pearson(s['x'], s['y'])
        results.append((e, corr, len(s['x'])))

    results.sort(key=lambda r: (r[1] is None, -(r[1] or 0)))

    print('Edge correlation summary (edge_id, pearson_r, samples):')
    for e, r, n in results[:50]:
        print(f'{e}\t{r}\t{n}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: heatmap_correlation_report.py /path/to/run_dir1 [/path/to/run_dir2 ...]')
        sys.exit(1)
    main(sys.argv[1:])
