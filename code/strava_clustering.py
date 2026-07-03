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
    trk = root.find('gpx:trk', NS)
    if trk is None:
        return None
    typ = trk.findtext('gpx:type', default='', namespaces=NS).lower()
    if 'run' not in typ:
        return None

    name = trk.findtext('gpx:name', default=os.path.basename(path), namespaces=NS)

    points = []
    for pt in trk.findall('.//gpx:trkpt', NS):
        try:
            lat = float(pt.get('lat'))
            lon = float(pt.get('lon'))
        except (TypeError, ValueError):
            continue
        time_el = pt.findtext('gpx:time', namespaces=NS)
        if time_el:
            try:
                t = datetime.strptime(time_el, '%Y-%m-%dT%H:%M:%SZ')
            except ValueError:
                t = None
        else:
            t = None
        hr_el = pt.find('.//gpxtpx:hr', NS)
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
    }

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
    # Attach cluster labels
    for a, lbl in zip(activities, cluster_labels):
        a['cluster'] = lbl

    # Cluster summary for the UI
    clusters = sorted(set(cluster_labels))
    cluster_info = {}
    for c in clusters:
        members = [a for a, l in zip(activities, cluster_labels) if l == c]
        cluster_info[c] = {
            'count': len(members),
            'avg_dist': float(np.mean([m['dist_km'] for m in members])),
            'label': f'Cluster {c}' if c >= 0 else 'Noise / solo',
        }

    # Serialise activities to JSON for the in-browser JS
    def ser(a):
        return {
            'file':        a['file'],
            'name':        a['name'],
            'date':        a['date'].strftime('%Y-%m-%d') if a['date'] else None,
            'date_ts':     a['date'].timestamp() if a['date'] else None,
            'dist_km':     round(a['dist_km'], 2),
            'duration_s':  a['duration_s'],
            'avg_speed':   round(a['avg_speed_kmh'], 2),
            'max_hr':      a['max_hr'],
            'avg_hr':      round(a['avg_hr'], 1) if a['avg_hr'] else None,
            'has_hr':      a['has_hr'],
            'hr_faulty':   a.get('hr_faulty', False),
            'hr_fault_reason': a.get('hr_fault_reason', ''),
            'cluster':     a['cluster'],
            'hr_series':   a.get('hr_series', []),
        }

    acts_json = json.dumps([ser(a) for a in activities])
    clusters_json = json.dumps(cluster_info)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Running HR Analysis</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg:      #0f1117;
    --surface: #1a1d27;
    --border:  #2a2d3a;
    --accent:  #6c63ff;
    --accent2: #ff6584;
    --text:    #e2e4ed;
    --muted:   #7b7f96;
    --good:    #43c59e;
    --warn:    #ffb347;
    --bad:     #ff6b6b;
  }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', system-ui, sans-serif;
    font-size: 14px;
    min-height: 100vh;
  }}
  header {{
    padding: 24px 32px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: baseline;
    gap: 16px;
  }}
  header h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -.4px; }}
  header .sub {{ color: var(--muted); font-size: 13px; }}
  .layout {{
    display: grid;
    grid-template-columns: 260px 1fr;
    min-height: calc(100vh - 64px);
  }}
  /* ─ Sidebar ─ */
  aside {{
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 20px 16px;
    overflow-y: auto;
  }}
  aside h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
               color: var(--muted); margin-bottom: 10px; }}
  .cluster-list {{ display: flex; flex-direction: column; gap: 6px; margin-bottom: 20px; }}
  .cluster-item {{
    display: flex; align-items: center; gap: 8px;
    padding: 8px 10px;
    border-radius: 6px;
    border: 1px solid var(--border);
    cursor: pointer;
    transition: border-color .15s;
  }}
  .cluster-item:hover {{ border-color: var(--accent); }}
  .cluster-item.active {{ border-color: var(--accent); background: #1e1b3a; }}
  .cluster-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .cluster-meta {{ color: var(--muted); font-size: 12px; }}
  .stat-grid {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 20px;
  }}
  .stat-box {{
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 12px;
  }}
  .stat-box .val {{ font-size: 20px; font-weight: 700; }}
  .stat-box .lbl {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  /* ─ Controls ─ */
  .controls {{
    display: flex; flex-wrap: wrap; align-items: center; gap: 12px;
    padding: 14px 24px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }}
  .ctrl-group {{ display: flex; align-items: center; gap: 6px; }}
  label {{ font-size: 12px; color: var(--muted); }}
  input[type=number] {{
    width: 70px; padding: 5px 8px;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 5px; color: var(--text); font-size: 13px;
  }}
  .toggle-wrap {{
    display: flex; align-items: center; gap: 6px; font-size: 12px;
  }}
  .toggle {{
    position: relative; width: 40px; height: 20px;
  }}
  .toggle input {{ opacity:0; width:0; height:0; }}
  .toggle .slider {{
    position:absolute; inset:0;
    background: var(--border); border-radius: 20px; cursor:pointer;
    transition: background .2s;
  }}
  .toggle .slider::before {{
    content:''; position:absolute;
    width:14px; height:14px; left:3px; top:3px;
    background:#fff; border-radius:50%;
    transition: transform .2s;
  }}
  .toggle input:checked + .slider {{ background: var(--accent); }}
  .toggle input:checked + .slider::before {{ transform: translateX(20px); }}
  .badge {{
    font-size: 11px; padding: 2px 7px; border-radius: 10px; font-weight: 600;
  }}
  .badge-ok   {{ background: #1a3a2e; color: var(--good); }}
  .badge-warn {{ background: #3a2e1a; color: var(--warn); }}
  .badge-bad  {{ background: #3a1a1a; color: var(--bad); }}
  /* ─ Main ─ */
  main {{
    display: flex; flex-direction: column;
    overflow: hidden;
  }}
  .charts {{ flex: 1; display: flex; flex-direction: column; gap: 0; }}
  .chart-wrap {{
    flex: 1; min-height: 380px;
    border-bottom: 1px solid var(--border);
  }}
  /* ─ Table ─ */
  .table-section {{ padding: 16px 24px 24px; }}
  .table-section h2 {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th {{ text-align:left; color:var(--muted); font-weight:500;
        padding:6px 10px; border-bottom:1px solid var(--border); }}
  td {{ padding:6px 10px; border-bottom:1px solid #1e2130; }}
  tr:hover td {{ background: var(--surface); }}
</style>
</head>
<body>
<header>
  <h1>🏃 Running HR Analysis</h1>
  <span class="sub" id="summary-line">Loading…</span>
</header>

<div class="layout">
  <!-- Sidebar -->
  <aside>
    <h2>Route Clusters</h2>
    <div class="cluster-list" id="cluster-list"></div>

    <h2>Overview</h2>
    <div class="stat-grid" id="stat-grid"></div>

    <h2>Legend</h2>
    <div style="display:flex;flex-direction:column;gap:6px;font-size:12px;">
      <div><span class="badge badge-ok">✓ valid HR</span></div>
      <div><span class="badge badge-warn">⚠ excluded cluster</span></div>
      <div><span class="badge badge-bad">✗ faulty HR</span></div>
    </div>
  </aside>

  <!-- Main content -->
  <main>
    <!-- Controls -->
    <div class="controls">
      <div class="ctrl-group">
        <label for="threshold">HR threshold (bpm):</label>
        <input type="number" id="threshold" value="160" min="100" max="220">
      </div>
      <div class="ctrl-group toggle-wrap">
        <span>Runs/week</span>
        <label class="toggle">
          <input type="checkbox" id="use-km">
          <span class="slider"></span>
        </label>
        <span>km/week</span>
      </div>
      <div class="ctrl-group" style="margin-left:auto;">
        <label style="color:var(--text);">Included clusters:</label>
        <span id="included-summary" style="color:var(--muted);font-size:12px;"></span>
      </div>
    </div>

    <!-- Charts -->
    <div class="charts">
      <div class="chart-wrap" id="chart-main"></div>
      <div class="chart-wrap" id="chart-desc"></div>
    </div>

    <!-- Table -->
    <div class="table-section">
      <h2>Activities in selected clusters</h2>
      <div id="table-wrap"></div>
    </div>
  </main>
</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────────
const ACTS = {acts_json};
const CLUSTER_INFO = {clusters_json};

// Cluster colours
const PALETTE = [
  '#6c63ff','#43c59e','#ffb347','#ff6584','#56cfe1',
  '#c77dff','#f4a261','#2ec4b6','#e76f51','#a8dadc',
];
function clusterColor(c) {{
  if (c < 0) return '#555';
  return PALETTE[c % PALETTE.length];
}}

// ── State ─────────────────────────────────────────────────────────────────
const allClusters = [...new Set(ACTS.map(a => a.cluster))].sort((a,b)=>a-b);
let includedClusters = new Set(allClusters.filter(c => c >= 0));
let hrThreshold = 160;
let useKm = false;

// ── Helpers ───────────────────────────────────────────────────────────────
function rollingLoad(sortedActs, targetDateStr, useKm) {{
  const target = new Date(targetDateStr + 'T12:00:00Z');
  const dow = target.getDay(); // 0=Sun,6=Sat
  const daysSinceSat = (dow === 6) ? 0 : (dow + 1);
  const refSat = new Date(target - daysSinceSat * 86400000);
  const winStart = new Date(refSat - 28 * 86400000);

  const inWindow = sortedActs.filter(a => {{
    if (!a.date) return false;
    const d = new Date(a.date + 'T12:00:00Z');
    return d >= winStart && d <= refSat;
  }});

  if (!inWindow.length) return 0;
  const total = useKm ? inWindow.reduce((s,a) => s + a.dist_km, 0) : inWindow.length;
  return total / 4;
}}

function timeToThreshold(hrSeries, thresh) {{
  for (const [t, hr] of hrSeries) {{
    if (hr >= thresh) return t;
  }}
  return null;
}}

function fmtDuration(s) {{
  const m = Math.floor(s/60), sec = Math.round(s%60);
  return `${{m}}:${{String(sec).padStart(2,'0')}}`;
}}

function fmtDate(d) {{
  return d ? new Date(d+'T12:00:00Z').toLocaleDateString('en-GB',
    {{day:'2-digit',month:'short',year:'numeric'}}) : '—';
}}

// ── Sidebar ───────────────────────────────────────────────────────────────
function buildSidebar() {{
  const list = document.getElementById('cluster-list');
  list.innerHTML = '';
  allClusters.forEach(c => {{
    const info = CLUSTER_INFO[c] || {{}};
    const el = document.createElement('div');
    el.className = 'cluster-item' + (includedClusters.has(c) ? ' active' : '');
    el.dataset.cluster = c;
    el.innerHTML = `
      <div class="cluster-dot" style="background:${{clusterColor(c)}}"></div>
      <div>
        <div>${{info.label || 'C'+c}}</div>
        <div class="cluster-meta">${{info.count}} runs · ~${{(info.avg_dist||0).toFixed(1)}} km avg</div>
      </div>`;
    el.addEventListener('click', () => {{
      if (includedClusters.has(c)) includedClusters.delete(c);
      else includedClusters.add(c);
      refresh();
    }});
    list.appendChild(el);
  }});

  // Stats
  const total = ACTS.length;
  const withHr = ACTS.filter(a => a.has_hr && !a.hr_faulty).length;
  const faulty = ACTS.filter(a => a.hr_faulty).length;
  const totalKm = ACTS.reduce((s,a) => s+a.dist_km, 0);
  document.getElementById('stat-grid').innerHTML = `
    <div class="stat-box"><div class="val">${{total}}</div><div class="lbl">Total runs</div></div>
    <div class="stat-box"><div class="val">${{withHr}}</div><div class="lbl">Valid HR</div></div>
    <div class="stat-box"><div class="val">${{faulty}}</div><div class="lbl">Faulty HR</div></div>
    <div class="stat-box"><div class="val">${{totalKm.toFixed(0)}}</div><div class="lbl">Total km</div></div>
  `;
  document.getElementById('summary-line').textContent =
    `${{total}} runs loaded · ${{allClusters.length}} clusters detected`;
}}

// ── Main chart ────────────────────────────────────────────────────────────
function buildMainChart() {{
  const sortedActs = [...ACTS].sort((a,b) => (a.date||'') < (b.date||'') ? -1 : 1);

  // Points for scatter
  const xVals=[], yVals=[], colors=[], texts=[], dates=[];
  let skipCount = 0;

  sortedActs.forEach(a => {{
    if (!includedClusters.has(a.cluster)) {{ skipCount++; return; }}
    if (!a.has_hr || a.hr_faulty) return;
    const tt = timeToThreshold(a.hr_series, hrThreshold);
    if (tt === null) return;  // never reached threshold

    const load = rollingLoad(sortedActs, a.date, useKm);
    const dateObj = new Date(a.date+'T12:00:00Z');
    const ts = dateObj.getTime();

    xVals.push(load);
    yVals.push(tt);
    dates.push(ts);
    texts.push(
      `<b>${{a.name}}</b><br>${{fmtDate(a.date)}}<br>`+
      `${{a.dist_km.toFixed(1)}} km · ${{fmtDuration(a.duration_s)}}<br>`+
      `Max HR: ${{a.max_hr}} bpm<br>`+
      `Load: ${{load.toFixed(1)}} ${{useKm?'km':'runs'}}/wk<br>`+
      `Time to HR≥${{hrThreshold}}: ${{fmtDuration(tt)}}`
    );
    colors.push(ts);
  }});

  const xLabel = useKm ? 'Avg km / week (last 4 weeks)' : 'Avg runs / week (last 4 weeks)';

  const scatter = {{
    type: 'scatter',
    mode: 'markers',
    x: xVals,
    y: yVals,
    text: texts,
    hoverinfo: 'text',
    marker: {{
      size: 10,
      color: colors,
      colorscale: [
        [0,   '#3a3060'],
        [0.33,'#6c63ff'],
        [0.66,'#43c59e'],
        [1,   '#ffb347'],
      ],
      showscale: true,
      colorbar: {{
        title: 'Date',
        tickvals: colors.length ? [Math.min(...colors), Math.max(...colors)] : [],
        ticktext: colors.length ? [
          new Date(Math.min(...colors)).getFullYear().toString(),
          new Date(Math.max(...colors)).getFullYear().toString(),
        ] : [],
        thickness: 12,
        len: 0.6,
        bgcolor: 'rgba(0,0,0,0)',
        tickfont: {{ color: '#7b7f96' }},
        titlefont: {{ color: '#7b7f96' }},
      }},
      line: {{ width: 0.5, color: '#0f1117' }},
    }},
  }};

  // Trend line (simple linear regression)
  let shapes = [];
  if (xVals.length >= 3) {{
    const n = xVals.length;
    const mx = xVals.reduce((s,v)=>s+v,0)/n;
    const my = yVals.reduce((s,v)=>s+v,0)/n;
    const num = xVals.reduce((s,v,i)=>s+(v-mx)*(yVals[i]-my),0);
    const den = xVals.reduce((s,v)=>s+(v-mx)**2,0);
    if (den > 0) {{
      const slope = num/den, inter = my - slope*mx;
      const x0 = Math.min(...xVals), x1 = Math.max(...xVals);
      shapes.push({{
        type:'line', x0, y0: slope*x0+inter, x1, y1: slope*x1+inter,
        line: {{ color:'#ff6584', width:1.5, dash:'dot' }},
      }});
    }}
  }}

  const layout = {{
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: {{ color: '#e2e4ed', family: 'Inter, system-ui' }},
    title: {{ text: `Time to reach HR ≥ ${{hrThreshold}} bpm  vs  Training Load`,
               font:{{size:14}}, x:0.02 }},
    xaxis: {{ title: xLabel, gridcolor:'#2a2d3a', zerolinecolor:'#2a2d3a' }},
    yaxis: {{ title: 'Seconds to HR threshold', gridcolor:'#2a2d3a', zerolinecolor:'#2a2d3a',
              tickformat: 'd',
              ticksuffix: 's' }},
    shapes,
    margin: {{ l:60, r:20, t:50, b:50 }},
    hoverlabel: {{ bgcolor:'#1a1d27', bordercolor:'#6c63ff', font:{{color:'#e2e4ed'}} }},
  }};

  Plotly.react('chart-main', [scatter], layout, {{responsive:true}});
  document.getElementById('included-summary').textContent =
    `${{includedClusters.size}} / ${{allClusters.length}} clusters · ${{skipCount}} runs excluded`;
}}

// ── Descriptive chart ─────────────────────────────────────────────────────
function buildDescChart() {{
  const sorted = [...ACTS]
    .filter(a => includedClusters.has(a.cluster) && a.has_hr && !a.hr_faulty && a.date)
    .sort((a,b) => a.date < b.date ? -1 : 1);

  const dates     = sorted.map(a => a.date);
  const maxHrs    = sorted.map(a => a.max_hr);
  const avgHrs    = sorted.map(a => a.avg_hr);
  const dists     = sorted.map(a => a.dist_km);
  const speeds    = sorted.map(a => a.avg_speed);

  const hoverText = sorted.map(a =>
    `<b>${{a.name}}</b><br>${{fmtDate(a.date)}}<br>`+
    `${{a.dist_km.toFixed(1)}} km · ${{a.avg_speed.toFixed(1)}} km/h<br>`+
    `Max HR: ${{a.max_hr}} · Avg HR: ${{a.avg_hr}}`
  );

  const traces = [
    {{
      name: 'Max HR',
      x: dates, y: maxHrs, text: hoverText, hoverinfo:'text',
      type:'scatter', mode:'lines+markers',
      line:{{color:'#ff6584',width:2}},
      marker:{{size:6}},
      yaxis:'y',
    }},
    {{
      name: 'Avg HR',
      x: dates, y: avgHrs, text: hoverText, hoverinfo:'text',
      type:'scatter', mode:'lines+markers',
      line:{{color:'#6c63ff',width:2,dash:'dot'}},
      marker:{{size:5}},
      yaxis:'y',
    }},
    {{
      name: 'Distance (km)',
      x: dates, y: dists, text: hoverText, hoverinfo:'text',
      type:'bar',
      marker:{{color:'rgba(67,197,158,0.35)'}},
      yaxis:'y2',
    }},
    {{
      name: 'Avg Speed (km/h)',
      x: dates, y: speeds, text: hoverText, hoverinfo:'text',
      type:'scatter', mode:'markers',
      marker:{{color:'#ffb347',size:7,symbol:'diamond'}},
      yaxis:'y3',
    }},
  ];

  const layout = {{
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor:  'rgba(0,0,0,0)',
    font: {{ color:'#e2e4ed', family:'Inter, system-ui' }},
    title: {{ text:'Activity Statistics Over Time', font:{{size:14}}, x:0.02 }},
    xaxis: {{ gridcolor:'#2a2d3a', zerolinecolor:'#2a2d3a' }},
    yaxis:  {{ title:'Heart Rate (bpm)',  gridcolor:'#2a2d3a', side:'left' }},
    yaxis2: {{ title:'Distance (km)',     overlaying:'y', side:'right', showgrid:false }},
    yaxis3: {{ title:'Speed (km/h)',      overlaying:'y', side:'right',
               position:0.95, showgrid:false }},
    legend: {{ orientation:'h', y:-0.15, x:0.5, xanchor:'center' }},
    margin: {{ l:60, r:80, t:50, b:60 }},
    hoverlabel: {{ bgcolor:'#1a1d27', bordercolor:'#43c59e', font:{{color:'#e2e4ed'}} }},
    barmode: 'overlay',
  }};

  Plotly.react('chart-desc', traces, layout, {{responsive:true}});
}}

// ── Activity table ────────────────────────────────────────────────────────
function buildTable() {{
  const filtered = ACTS
    .filter(a => includedClusters.has(a.cluster))
    .sort((a,b) => (b.date||'') < (a.date||'') ? -1 : 1);

  let rows = filtered.map(a => {{
    const badge = a.hr_faulty
      ? `<span class="badge badge-bad" title="${{a.hr_fault_reason}}">✗ faulty</span>`
      : a.has_hr
        ? `<span class="badge badge-ok">✓ valid</span>`
        : `<span class="badge badge-warn">no HR</span>`;
    return `<tr>
      <td>${{fmtDate(a.date)}}</td>
      <td>${{a.name}}</td>
      <td>${{a.dist_km.toFixed(1)}}</td>
      <td>${{fmtDuration(a.duration_s)}}</td>
      <td>${{a.avg_speed.toFixed(1)}}</td>
      <td>${{a.max_hr ?? '—'}}</td>
      <td>${{badge}}</td>
    </tr>`;
  }}).join('');

  document.getElementById('table-wrap').innerHTML = `
    <table>
      <thead><tr>
        <th>Date</th><th>Name</th><th>Dist (km)</th>
        <th>Duration</th><th>Speed (km/h)</th><th>Max HR</th><th>HR Status</th>
      </tr></thead>
      <tbody>${{rows}}</tbody>
    </table>`;
}}

// ── Refresh ───────────────────────────────────────────────────────────────
function refresh() {{
  // Update sidebar active states
  document.querySelectorAll('.cluster-item').forEach(el => {{
    const c = parseInt(el.dataset.cluster);
    el.classList.toggle('active', includedClusters.has(c));
  }});
  buildMainChart();
  buildDescChart();
  buildTable();
}}

// ── Init ──────────────────────────────────────────────────────────────────
buildSidebar();
refresh();

document.getElementById('threshold').addEventListener('input', e => {{
  hrThreshold = parseInt(e.target.value) || 160;
  buildMainChart();
}});
document.getElementById('use-km').addEventListener('change', e => {{
  useKm = e.target.checked;
  buildMainChart();
}});
</script>
</body>
</html>
"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  ✓ HTML written → {output_path}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Strava GPX HR Analysis')
    parser.add_argument('--gpx-dir', default='.', help='Directory containing GPX files')
    parser.add_argument('--out', default='running_analysis.html', help='Output HTML file')
    parser.add_argument('--cluster-eps', type=float, default=1.5,
                        help='DBSCAN radius in km for route clustering (default 1.5)')
    parser.add_argument('--cluster-map', action='store_true',
                        help='Write a cluster map HTML file showing tracks coloured by cluster')
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
        a = parse_gpx(p)
        if a:
            activities.append(a)
        else:
            print(f"  skip: {os.path.basename(p)} (not a run or unparseable)")

    if not activities:
        print("No valid running activities found.")
        sys.exit(1)
    print(f"Parsed {len(activities)} running activities.")

    # ── Sort by date
    activities.sort(key=lambda a: a['date'] or datetime.min)

    # ── Cluster
    try:
        out_map = None
        if args.cluster_map:
            base, _ = os.path.splitext(args.out)
            out_map = f"{base}_clusters_map.html"
        labels = cluster_activities(activities, eps_km=args.cluster_eps,
                                    plot=args.cluster_map, out_html=out_map)
    except Exception as e:
        print(f"  Clustering failed ({e}); assigning all to cluster 0.")
        labels = [0] * len(activities)

    cluster_counts = defaultdict(int)
    for l in labels:
        cluster_counts[l] += 1
    print(f"Clusters: { {k:v for k,v in sorted(cluster_counts.items())} }")

    # ── HR fault detection
    n_faulty = 0
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

    # ── Attach cluster labels
    for a, lbl in zip(activities, labels):
        a['cluster'] = lbl

    # ── Build HTML
    print(f"Building HTML → {args.out} …")
    build_html(activities, labels, args.out)
    print("Done.")

if __name__ == '__main__':
    main()
