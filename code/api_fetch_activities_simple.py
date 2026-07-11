"""
Simplified Strava activities -> GPX builder

This script does NOT call the `export_gpx` endpoint (which may return 404).
Instead it fetches activity streams (latlng, altitude, time) and builds a
minimal GPX file for each activity that doesn't already have a .gpx file on disk.

Usage: set STRAVA_ID, STRAVA_SECRET, STRAVA_REFRESH in the environment and run:
    python code/api_fetch_activities_simple.py

This file intentionally omits long explanatory authorization-scope messages
and any fallback download attempts of the `export_gpx` endpoint.
"""

import os
import sys
import time
import requests
from glob import glob
from datetime import datetime, timedelta
import sys
sys.path.insert(0, os.path.dirname(__file__))
from gpx_utils import build_gpx_from_streams as gpx_build_from_streams


DRIVE = '/media/yuval/KINGSTON/'
ACTIVITIES_DIR = os.path.join(DRIVE, 'Strava', 'activities')


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def get_access_token():
    try:
        client_id = os.environ['STRAVA_ID']
        client_secret = os.environ['STRAVA_SECRET']
        refresh_token = os.environ['STRAVA_REFRESH']
    except KeyError as e:
        print(f"Missing environment variable: {e}")
        sys.exit(1)

    auth_url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }

    resp = requests.post(auth_url, data=payload)
    if resp.status_code != 200:
        print(f"Auth error: {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()

    data = resp.json()
    token = data.get('access_token')
    if not token:
        print("No access_token in auth response")
        print(resp.text)
        sys.exit(1)
    return token


def get_already_downloaded(dirpath):
    out = set()
    for p in glob(os.path.join(dirpath, '*.gpx')):
        try:
            aid = int(os.path.basename(p)[:-4])
            out.add(aid)
        except Exception:
            continue
    return out


def fetch_all_activities(headers):
    activities = []
    page = 1
    while True:
        url = f"https://www.strava.com/api/v3/athlete/activities?per_page=200&page={page}"
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        activities.extend(data)
        page += 1
        time.sleep(0.3)
    return activities


def fetch_and_build_gpx(activity, headers, out_dir):
    """Fetch streams (including heartrate) and delegate GPX building to gpx_utils."""
    activity_id = activity['id']
    streams_url = (
        f"https://www.strava.com/api/v3/activities/{activity_id}/streams?"
        "keys=latlng,altitude,time,heartrate&key_by_type=true"
    )
    r = requests.get(streams_url, headers=headers)
    if r.status_code != 200:
        print(f"    Streams request failed for {activity_id}: {r.status_code}")
        return False

    streams = r.json()
    out_path = os.path.join(out_dir, f"{activity_id}.gpx")
    ok = gpx_build_from_streams(activity, streams, out_path)
    if ok:
        print(f"  Built GPX from streams for {activity_id}: {activity.get('name', activity_id)}")
    else:
        print(f"  Failed to build GPX for {activity_id}")
    return ok


def main():
    ensure_dir(ACTIVITIES_DIR)
    already = get_already_downloaded(ACTIVITIES_DIR)

    token = get_access_token()
    headers = {'Authorization': f'Bearer {token}'}

    print("Fetching activities from Strava API...")
    activities = fetch_all_activities(headers)
    print(f"Total activities from API: {len(activities)}")

    to_download = [a for a in activities if a['id'] not in already]
    print(f"Activities to process (missing .gpx): {len(to_download)}")

    if not to_download:
        print("Nothing to do.")
        return

    print('\nProcessing activities (building GPX from streams where available)...')
    for activity in to_download:
        fetch_and_build_gpx(activity, headers, ACTIVITIES_DIR)
        time.sleep(0.3)


if __name__ == '__main__':
    main()

