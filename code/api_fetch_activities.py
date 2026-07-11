import requests
import os
import sys
from glob import glob
import time
from datetime import datetime, timedelta
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from gpx_utils import build_gpx_from_streams as gpx_build_from_streams

drive = '/media/yuval/KINGSTON/'
if not os.path.isdir(drive):
    raise ValueError("Where is the drive?")
activities_dir = drive + 'Strava/activities'
os.makedirs(activities_dir, exist_ok=True)

# Get already downloaded GPX files (extract activity ID from filename)
already = set()
for p in glob(activities_dir + "/*.gpx"):
    try:
        activity_id = int(p.split('/')[-1][:-4])
        already.add(activity_id)
    except (ValueError, IndexError):
        pass

STRAVA_CLIENT_ID = os.environ['STRAVA_ID']
STRAVA_CLIENT_SECRET = os.environ['STRAVA_SECRET']
STRAVA_REFRESH_TOKEN = os.environ['STRAVA_REFRESH']

auth_url = "https://www.strava.com/oauth/token"
payload = {
    "client_id": STRAVA_CLIENT_ID,
    "client_secret": STRAVA_CLIENT_SECRET,
    "refresh_token": STRAVA_REFRESH_TOKEN,
    "grant_type": "refresh_token",
}

print("Refreshing access token...")
print(f"  Client ID: {STRAVA_CLIENT_ID[:10]}...")
print(f"  Refresh token: {STRAVA_REFRESH_TOKEN[:10]}...")

# CRITICAL: Use data=payload (form-encoded), NOT json=payload
auth_response = requests.post(auth_url, data=payload)

# Debug: Print response status and content
print(f"  Auth response status: {auth_response.status_code}")
if auth_response.status_code != 200:
    print(f"  Auth response body: {auth_response.text}")

# If this raises an HTTPError, it will print the exact status code to help debug
auth_response.raise_for_status()

# This will now successfully parse JSON instead of throwing a JSONDecodeError
try:
    auth_data = auth_response.json()
    access_token = auth_data["access_token"]
except Exception as e:
    print(f"ERROR parsing auth response: {e}")
    print(f"  Response body: {auth_response.text}")
    raise

print(f"Success! Access token retrieved: {access_token[:20]}...")
print(f"  Token type: {auth_data.get('token_type', 'unknown')}")
print(f"  Expires in: {auth_data.get('expires_in', 'unknown')} seconds")

# Fetch all activities from Strava API
print(f"\nAlready have {len(already)} activities on drive.")
print("Fetching activities from Strava API...")

all_activities = []
page = 1
headers = {"Authorization": f"Bearer {access_token}"}

print(f"  Authorization header: Bearer {access_token[:20]}...")

while True:
    url = f"https://www.strava.com/api/v3/athlete/activities?per_page=200&page={page}"
    response = requests.get(url, headers=headers)

    # Debug response status
    if response.status_code != 200:
        print(f"ERROR on page {page}: {response.status_code}")
        print(f"  URL: {url}")
        print(f"  Response: {response.text[:500]}")

        # Check for missing scope error
        try:
            error_data = response.json()
            if 'activity:read' in str(error_data):
                print("\n" + "=" * 70)
                print("AUTHORIZATION SCOPE ERROR")
                print("=" * 70)
                print("\nYour access token is missing the 'activity:read_permission' scope.")
                print("You need to re-authorize the app with the correct permissions.")
                print("\nTo fix this, run:")
                print("  python oauth_authorize.py")
                print("\nThen update STRAVA_REFRESH with the new token and try again.")
                print("=" * 70)
                sys.exit(1)
        except:
            pass

    response.raise_for_status()
    activities = response.json()

    if not activities:
        break

    all_activities.extend(activities)
    print(f"  Fetched page {page}: {len(activities)} activities")
    page += 1
    time.sleep(0.5)  # Rate limit friendly

print(f"Total activities from API: {len(all_activities)}")

# Filter for activities not yet downloaded
to_download = [a for a in all_activities if a['id'] not in already]
print(f"Activities to download: {len(to_download)}")

if to_download:
    print("\nDownloading missing GPX files...")
    for i, activity in enumerate(to_download, 1):
        activity_id = activity['id']
        activity_name = activity.get('name', f'Activity_{activity_id}')

        # Download GPX file
        gpx_url = f"https://www.strava.com/api/v3/activities/{activity_id}/export_gpx"
        gpx_response = requests.get(gpx_url, headers=headers)

        if gpx_response.status_code != 200:
            print(f"  [{i}/{len(to_download)}] ERROR downloading {activity_id}: {gpx_response.status_code}")
            print(f"    Response: {gpx_response.text[:200]}")
            # If export_gpx is not available (404), try to build GPX from Streams as a fallback.
            if gpx_response.status_code == 404:
                print(f"    export_gpx returned 404 — attempting to build GPX from streams for {activity_id}")
                try:
                    streams_url = (
                        f"https://www.strava.com/api/v3/activities/{activity_id}/streams?"
                        "keys=latlng,altitude,time,heartrate&key_by_type=true"
                    )
                    streams_resp = requests.get(streams_url, headers=headers)
                    if streams_resp.status_code != 200:
                        print(f"    Streams request failed: {streams_resp.status_code}")
                        print(f"    Streams response: {streams_resp.text[:200]}")
                        continue

                    streams = streams_resp.json()
                    gpx_path = os.path.join(activities_dir, f"{activity_id}.gpx")
                    ok = gpx_build_from_streams(activity, streams, gpx_path)
                    if ok:
                        print(f"  [{i}/{len(to_download)}] Built GPX from streams for {activity_id}: {activity_name}")
                        time.sleep(0.5)
                        continue
                    else:
                        print(f"    No latlng stream available for {activity_id}; cannot build GPX.")
                        continue
                except Exception as e:
                    print(f"    Failed to build GPX from streams for {activity_id}: {e}")
                    continue
            else:
                continue

        gpx_response.raise_for_status()

        # Save to disk
        gpx_path = os.path.join(activities_dir, f"{activity_id}.gpx")
        with open(gpx_path, 'wb') as f:
            f.write(gpx_response.content)

        print(f"  [{i}/{len(to_download)}] Downloaded {activity_id}: {activity_name}")
        time.sleep(0.5)  # Rate limit friendly

    print(f"\n✓ Successfully downloaded {len(to_download)} new GPX files!")
else:
    print("✓ All activities already downloaded!")

