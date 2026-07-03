import requests
import os

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
# CRITICAL: Use data=payload (form-encoded), NOT json=payload
auth_response = requests.post(auth_url, data=payload)

# If this raises an HTTPError, it will print the exact status code to help debug
auth_response.raise_for_status()

# This will now successfully parse JSON instead of throwing a JSONDecodeError
access_token = auth_response.json()["access_token"]
print("Success! Access token retrieved.")
