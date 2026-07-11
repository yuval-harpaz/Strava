"""
Utility helpers for building and parsing GPX files from Strava API streams.

Provides a single function `build_gpx_from_streams(activity, streams, out_path)`
which writes a GPX file that includes timestamps, elevation and heart-rate
in the GPX TrackPointExtension namespace when available.
"""
from datetime import datetime, timedelta
import os


GPX_HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<gpx version="1.1" creator="Strava API streams fallback" '
              'xmlns="http://www.topografix.com/GPX/1/1" '
              'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">')


def _get_stream_data(streams, key):
    val = streams.get(key, [])
    if isinstance(val, dict) and 'data' in val:
        return val['data']
    return val or []


def build_gpx_from_streams(activity, streams, out_path):
    """Build a GPX file from Strava streams and write to out_path.

    activity: dict returned by Strava activities API (may contain start_date, name)
    streams:  dict returned by /activities/{id}/streams?key_by_type=true
    out_path: path to write GPX file

    Returns True on success, False otherwise.
    """
    latlng = _get_stream_data(streams, 'latlng')
    if not latlng:
        return False

    time_stream = _get_stream_data(streams, 'time')
    altitude = _get_stream_data(streams, 'altitude')
    heartrate = _get_stream_data(streams, 'heartrate') or _get_stream_data(streams, 'heart_rate')

    # Parse activity start date if available to convert seconds-since-start to absolute times
    start_dt = None
    start_date = activity.get('start_date') or activity.get('start_date_local')
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        except Exception:
            start_dt = None

    name = activity.get('name', activity.get('id', 'activity'))

    lines = [GPX_HEADER, '  <metadata>']
    if start_dt:
        lines.append(f'    <time>{start_dt.isoformat().replace("+00:00","Z")}</time>')
    lines.append('  </metadata>')
    lines.append('  <trk>')
    lines.append(f'    <name>{name}</name>')
    atype = activity.get('type', '')
    if atype:
        lines.append(f'    <type>{atype}</type>')
    lines.append('    <trkseg>')

    for idx, coord in enumerate(latlng):
        try:
            lat, lon = coord
        except Exception:
            continue
        lines.append(f'      <trkpt lat="{lat}" lon="{lon}">')
        if idx < len(altitude):
            lines.append(f'        <ele>{altitude[idx]}</ele>')
        if start_dt and idx < len(time_stream):
            ts = start_dt + timedelta(seconds=int(time_stream[idx]))
            lines.append(f'        <time>{ts.isoformat().replace("+00:00","Z")}</time>')
        # Add HR if present
        if idx < len(heartrate) and heartrate[idx] is not None:
            hr_val = heartrate[idx]
            lines.append('        <extensions>')
            lines.append('          <gpxtpx:TrackPointExtension>')
            lines.append(f'            <gpxtpx:hr>{int(hr_val)}</gpxtpx:hr>')
            lines.append('          </gpxtpx:TrackPointExtension>')
            lines.append('        </extensions>')
        lines.append('      </trkpt>')

    lines.extend(['    </trkseg>', '  </trk>', '</gpx>'])

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return True

