#!/usr/bin/env python3
"""
Strava GPX Running Analysis
Clusters routes, detects faulty HR, visualizes HR improvement over time.
Usage: python strava_analysis.py [--gpx-dir /path/to/gpx/files]
"""

import os
import sys
import argparse
import glob
import math
import json
from datetime import datetime, timedelta
from collections import defaultdict

import xml.etree.ElementTree as ET
import numpy as np
from sklearn.cluster import DBSCAN
import plotly.graph_objects as go

# Known issues
bad = [18767282334, 18757050177, 18726249587, 18663603021, 18610526273, 18532370069, 18348237273, 18068507969,
       17474314206, 17235917782, 16852683261, 15880099077, 15047930706, 14768519424, 14479333822, 14264064245,
       19088581908, 18906815918, 14128647827, 13797004948, 13774752963]


# ── GPX parsing ──────────────────────────────────────────────────────────────

NS = {
    'gpx':    'http://www.topografix.com/GPX/1/1',
    'gpxtpx': 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1',
}

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2*R*math.asin(math.sqrt(a))

def parse_gpx(path):
    """Return dict with activity metadata, or None if not a running activity."""
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return None
    root = tree.getroot()

    # Activity type
    # Try to find a namespaced <trk> (official GPX) but accept non-namespaced
    trk = root.find('gpx:trk', NS)
    use_ns = True
    if trk is None:
        trk = root.find('trk')
        use_ns = False
    if trk is None:
        return None

    # Read <type> and <name> with or without namespace. If type is missing,
    # assume running (to support API-generated GPX built from streams).
    if use_ns:
        typ = trk.findtext('gpx:type', default='', namespaces=NS).lower()
        name = trk.findtext('gpx:name', default=os.path.basename(path), namespaces=NS)
    else:
        typ = trk.findtext('type', default='').lower()
        name = trk.findtext('name', default=os.path.basename(path))

    if typ and 'run' not in typ:
        return None

    points = []
    # Collect track points, handling both namespaced and plain GPX files
    if use_ns:
        trkpts = trk.findall('.//gpx:trkpt', NS)
    else:
        trkpts = trk.findall('.//trkpt')

    for pt in trkpts:
        try:
            lat = float(pt.get('lat'))
            lon = float(pt.get('lon'))
        except (TypeError, ValueError):
            continue
        if use_ns:
            time_el = pt.findtext('gpx:time', namespaces=NS)
            hr_el = pt.find('.//gpxtpx:hr', NS)
        else:
            time_el = pt.findtext('time')
            hr_el = pt.find('.//hr') or pt.find('hr')
        if time_el:
            try:
                t = datetime.strptime(time_el, '%Y-%m-%dT%H:%M:%SZ')
            except ValueError:
                t = None
        else:
            t = None
        hr = int(hr_el.text) if hr_el is not None and hr_el.text else None
        points.append({'lat': lat, 'lon': lon, 'time': t, 'hr': hr})

    if not points:
        return None

    # Distance (metres)
    dist = 0.0
    for i in range(1, len(points)):
        dist += haversine(points[i-1]['lat'], points[i-1]['lon'],
                          points[i]['lat'],   points[i]['lon'])

    # Duration
    times = [p['time'] for p in points if p['time']]
    duration_s = (times[-1] - times[0]).total_seconds() if len(times) >= 2 else 0

    # Heart rate series
    hrs = [p['hr'] for p in points if p['hr'] is not None]
    has_hr = len(hrs) > 0

    # Centre of route (for clustering)
    lats = [p['lat'] for p in points]
    lons = [p['lon'] for p in points]
    centre_lat = float(np.mean(lats))
    centre_lon = float(np.mean(lons))

    # Representative waypoints for shape clustering (every Nth point)
    step = max(1, len(points) // 20)
    waypoints = [(points[i]['lat'], points[i]['lon']) for i in range(0, len(points), step)]

    # Full coordinate list (used for optional plotting)
    coords = [(p['lat'], p['lon']) for p in points]

    # Time series: seconds from start → hr value
    t0 = times[0] if times else None
    hr_series = []
    for p in points:
        if p['time'] and p['hr'] is not None and t0:
            hr_series.append((( p['time'] - t0).total_seconds(), p['hr']))

    # Cumulative distances at each point (metres)
    cum_dists = [0.0]
    for i in range(1, len(points)):
        seg = haversine(points[i-1]['lat'], points[i-1]['lon'],
                        points[i]['lat'], points[i]['lon'])
        cum_dists.append(cum_dists[-1] + seg)

    # Helper: estimate timestamp at a given cumulative distance (metres)
    def _time_at_distance(points, cum_dists, target_m):
        # Indices with timestamps
        timed = [i for i, p in enumerate(points) if p['time']]
        if not timed:
            return None
        for idx in timed:
            if cum_dists[idx] >= target_m:
                # find previous timed index before idx
                prev = None
                for j in reversed(timed):
                    if j < idx:
                        prev = j
                        break
                if prev is None:
                    return points[idx]['time']
                d0 = cum_dists[prev]
                d1 = cum_dists[idx]
                t0 = points[prev]['time']
                t1 = points[idx]['time']
                if d1 == d0:
                    return t1
                frac = (target_m - d0) / (d1 - d0)
                return t0 + (t1 - t0) * frac
        return None

    def _pace_for_k(km):
        if not times:
            return None
        target_m = km * 1000.0
        if cum_dists[-1] < target_m:
            return None
        t_at = _time_at_distance(points, cum_dists, target_m)
        if not t_at:
            return None
        dur_s = (t_at - times[0]).total_seconds()
        if dur_s <= 0:
            return None
        # pace in minutes per km
        return (dur_s / 60.0) / km

    # Distance (km) at which HR first reaches 170 bpm (if available)
    km_to_hr170 = None
    for i, p in enumerate(points):
        if p.get('hr') is not None and p['hr'] >= 170:
            km_to_hr170 = cum_dists[i] / 1000.0
            break

    return {
        'file': os.path.basename(path),
        'name': name,
        'date': times[0] if times else None,
        'dist_km': dist / 1000,
        'duration_s': duration_s,
        'avg_speed_kmh': (dist / 1000) / (duration_s / 3600) if duration_s > 0 else 0,
        'centre_lat': centre_lat,
        'centre_lon': centre_lon,
        'waypoints': waypoints,
        'coords': coords,
        'has_hr': has_hr,
        'hr_series': hr_series,
        'hr_values': hrs,
        'max_hr': max(hrs) if hrs else None,
        'avg_hr': float(np.mean(hrs)) if hrs else None,
        'km_to_hr170': km_to_hr170,
        'mean_pace_5': _pace_for_k(5),
        'mean_pace_10': _pace_for_k(10),
    }

def is_rehovot(activity):
    """
    Check if activity is in Rehovot area based on route centre.
    Bounding box: lat [31.8, 32.0], lon [34.75, 34.85]
    """
    lat = activity.get('centre_lat')
    lon = activity.get('centre_lon')
    if lat is None or lon is None:
        return False
    return 31.8 <= lat <= 32.0 and 34.75 <= lon <= 34.85

# ── Clustering ────────────────────────────────────────────────────────────────
#for cluster activities, add plot=True argument for creating a plotly html with all coordinates. make them colored by cluster, and show activity index on hover
def cluster_activities(activities, eps_km=0.1, plot=True, out_html=None):
    """DBSCAN on route centres. Returns list of cluster labels (-1 = noise).

    If plot=True, write a simple Plotly HTML showing each activity's
    coordinates coloured by cluster. out_html may be provided; otherwise
    'clusters_map.html' is used.
    """
    if not activities:
        return []
    coords = np.radians([[a['centre_lat'], a['centre_lon']] for a in activities])
    eps_rad = eps_km / 6371.0
    labels = DBSCAN(eps=eps_rad, min_samples=2, algorithm='ball_tree',
                    metric='haversine').fit_predict(coords)
    labels = labels.tolist()

    if plot:
        # Simple 2D plot: longitude (x) vs latitude (y)
        PALETTE = ['#6c63ff','#43c59e','#ffb347','#ff6584','#56cfe1',
                   '#c77dff','#f4a261','#2ec4b6','#e76f51','#a8dadc']
        def colour_for(lbl):
            return '#555' if lbl < 0 else PALETTE[lbl % len(PALETTE)]

        fig = go.Figure()
        for i, a in enumerate(activities):
            pts = a.get('coords') or a.get('waypoints') or []
            if not pts:
                continue
            lats = [p[0] for p in pts]
            lons = [p[1] for p in pts]
            lbl = labels[i]
            hover = f"Activity {i}: {a.get('name','')} (cluster {lbl})"
            fig.add_trace(go.Scatter(
                x = lons,
                y = lats,
                mode = 'lines',
                line = dict(color=colour_for(lbl), width=2),
                text = hover,
                hoverinfo = 'text',
                showlegend = False
            ))

        fig.update_layout(
            title = 'Activity tracks (longitude vs latitude)',
            xaxis = dict(title='Longitude', range=[34.75, 34.85]),
            yaxis = dict(title='Latitude', range=[31.8, 32.0]),
            height = 600,
            margin = dict(l=60, r=20, t=50, b=50),
            hovermode = 'closest'
        )

        if out_html is None:
            out_html = 'clusters_map.html'
        try:
            fig.write_html(out_html, include_plotlyjs='cdn')
            print(f"  ✓ Cluster map written → {out_html}")
        except Exception as e:
            print(f"  Failed to write cluster map ({e})")

    return labels

# ── Faulty HR detection ───────────────────────────────────────────────────────

def is_hr_faulty(activity):
    """
    Heuristics:
      - Fewer than 30 HR samples
      - Max HR > 220 or < 60
      - More than 40 % of readings stuck at the same value (sensor dropout)
      - Huge sudden jumps (> 60 bpm in one step) in > 5 % of consecutive pairs
    """
    hrs = activity['hr_values']
    if len(hrs) < 30:
        return True, 'too few HR samples'
    if max(hrs) > 220:
        return True, f'max HR {max(hrs)} > 220'
    if max(hrs) < 60:
        return True, f'max HR {max(hrs)} < 60 (sensor off?)'
    # Flatline check
    from collections import Counter
    mode_count = Counter(hrs).most_common(1)[0][1]
    if mode_count / len(hrs) > 0.40:
        return True, 'sensor flatline (>40 % identical values)'
    # Spike check
    jumps = [abs(hrs[i] - hrs[i-1]) for i in range(1, len(hrs))]
    if sum(j > 60 for j in jumps) / max(len(jumps), 1) > 0.05:
        return True, '>5 % of steps have 60+ bpm jumps'
    return False, ''

# ── HR rise time ──────────────────────────────────────────────────────────────

def time_to_threshold(hr_series, threshold):
    """Seconds from start until HR first exceeds `threshold`. None if never."""
    for t, hr in hr_series:
        if hr >= threshold:
            return t
    return None

# ── Rolling workload ──────────────────────────────────────────────────────────

def rolling_4week(activities_sorted, target_date, use_km=False):
    """
    Average weekly count (or km) over the 4 calendar weeks ending on the
    Saturday at or before target_date.
    """
    # Find the Saturday on or before target_date
    dow = target_date.weekday()   # Mon=0 … Sat=5, Sun=6
    days_since_sat = (dow - 5) % 7
    ref_sat = target_date - timedelta(days=days_since_sat)

    window_start = ref_sat - timedelta(weeks=4)
    window_end   = ref_sat

    in_window = [a for a in activities_sorted
                 if a['date'] and window_start <= a['date'] <= window_end]

    if not in_window:
        return 0.0

    if use_km:
        total = sum(a['dist_km'] for a in in_window)
    else:
        total = len(in_window)

    return total / 4.0   # average per week

# ── Chart building ────────────────────────────────────────────────────────────

def build_html(activities, cluster_labels, output_path):
    # Attach cluster labels and compute is_rehovot flag
    for a, lbl in zip(activities, cluster_labels):
        a['cluster'] = lbl
        a['is_rehovot'] = is_rehovot(a)

    # Serialise activities to JSON for the in-browser JS
    def ser(a):
        return {
            'file':        a['file'],
            'name':        a['name'],
            'date':        a['date'].strftime('%Y-%m-%d') if a['date'] else None,
            'date_ts':     a['date'].timestamp() if a['date'] else None,
            'dist_km':     round(a['dist_km'], 2),
            'duration_s':  a['duration_s'],
            'max_hr':      a['max_hr'],
            'avg_hr':      round(a['avg_hr'], 1) if a['avg_hr'] else None,
            'has_hr':      a['has_hr'],
            'hr_faulty':   a.get('hr_faulty', False),
            'hr_fault_reason': a.get('hr_fault_reason', ''),
            'cluster':     a['cluster'],
            'is_rehovot':  a.get('is_rehovot', False),
            'km_to_hr170': round(a.get('km_to_hr170'), 2) if a.get('km_to_hr170') is not None else None,
            'hr_series':   a.get('hr_series', []),
            'mean_pace_5': round(a.get('mean_pace_5'), 2) if a.get('mean_pace_5') is not None else None,
            'mean_pace_10': round(a.get('mean_pace_10'), 2) if a.get('mean_pace_10') is not None else None,
        }

    acts_json = json.dumps([ser(a) for a in activities])

    # Read HTML template and substitute ACTS JSON
    tpl_path = os.path.join(os.path.dirname(__file__), 'template_analysis.html')
    try:
        with open(tpl_path, 'r', encoding='utf-8') as tf:
            html = tf.read()
    except FileNotFoundError:
        raise RuntimeError(f"Template not found: {tpl_path}")

    html = html.replace('{{ACTS_JSON}}', acts_json)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  ✓ HTML written → {output_path}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Strava GPX HR Analysis')
    parser.add_argument('--gpx-dir', default='/media/yuval/PNY/Strava/activities', help='Directory containing GPX files')
    parser.add_argument('--out', default='running_analysis.html', help='Output HTML file')
    parser.add_argument('--cluster-eps', type=float, default=1.5,
                        help='DBSCAN radius in km for route clustering (default 1.5)')
    parser.add_argument('--cluster', action='store_true',
                        help='Perform DBSCAN clustering to assign cluster labels (disabled by default)')
    parser.add_argument('--cluster-map', action='store_true',
                        help='Write a cluster map HTML file showing tracks coloured by cluster (implies --cluster)')
    parser.add_argument('--hr-check', action='store_true',
                        help='Perform HR fault detection (disabled by default)')
    args = parser.parse_args()

    # ── Find GPX files
    pattern = os.path.join(args.gpx_dir, '**', '*.gpx')
    paths = glob.glob(pattern, recursive=True)
    if not paths:
        pattern2 = os.path.join(args.gpx_dir, '*.gpx')
        paths = glob.glob(pattern2)
    if not paths:
        print(f"No GPX files found in: {args.gpx_dir}")
        sys.exit(1)
    print(f"Found {len(paths)} GPX file(s)…")

    # ── Parse
    activities = []
    for p in paths:
        bn = os.path.basename(p)
        # If filename is an integer activity id and is known-bad, skip early
        try:
            aid = int(bn[:-4])
        except Exception:
            aid = None
        if aid is not None and aid in bad:
            # print(f"  skip: {bn} (known bad activity)")
            continue
        a = parse_gpx(p)
        if a:
            activities.append(a)
        # else:
        #     print(f"  skip: {bn} (not a run or unparseable)")

    if not activities:
        print("No valid running activities found.")
        sys.exit(1)
    print(f"Parsed {len(activities)} running activities.")

    # ── Sort by date
    activities.sort(key=lambda a: a['date'] or datetime.min)

    # ── Filter out short runs (< 5 km)
    orig_count = len(activities)
    activities = [a for a in activities if a['dist_km'] >= 5.0]
    if len(activities) != orig_count:
        print(f"Filtered out {orig_count - len(activities)} activities shorter than 5 km; {len(activities)} remain.")

    # ── Cluster (only if explicitly requested)
    if args.cluster or args.cluster_map:
        try:
            out_map = None
            if args.cluster_map:
                base, _ = os.path.splitext(args.out)
                out_map = f"{base}_clusters_map.html"
            labels = cluster_activities(activities, eps_km=args.cluster_eps,
                                        plot=bool(args.cluster_map), out_html=out_map)
        except Exception as e:
            print(f"  Clustering failed ({e}); assigning all to cluster 0.")
            labels = [0] * len(activities)
    else:
        # default: no clustering performed, assign cluster 0 to all activities
        labels = [0] * len(activities)

    cluster_counts = defaultdict(int)
    for l in labels:
        cluster_counts[l] += 1
    print(f"Clusters: { {k:v for k,v in sorted(cluster_counts.items())} }")

    # ── HR fault detection (only if explicitly requested)
    n_faulty = 0
    if args.hr_check:
        for a in activities:
            if a['has_hr']:
                faulty, reason = is_hr_faulty(a)
                a['hr_faulty'] = faulty
                a['hr_fault_reason'] = reason
                if faulty:
                    n_faulty += 1
                    print(f"  ⚠ faulty HR: {a['file']}  ({reason})")
            else:
                a['hr_faulty'] = False
                a['hr_fault_reason'] = ''
        print(f"Faulty HR activities: {n_faulty}")
    else:
        # mark as unchecked
        for a in activities:
            a['hr_faulty'] = False
            a['hr_fault_reason'] = ''

    # ── Attach cluster labels
    for a, lbl in zip(activities, labels):
        a['cluster'] = lbl

    # ── Build HTML
    print(f"Building HTML → {args.out} …")
    build_html(activities, labels, args.out)
    print("Done.")

if __name__ == '__main__':
    main()
