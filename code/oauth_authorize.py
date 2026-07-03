#!/usr/bin/env python3
"""
Strava OAuth Authorization Helper
Gets a new refresh token with proper scopes (activity:read_permission)
"""

import os
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
import sys

STRAVA_CLIENT_ID = os.environ.get('STRAVA_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_SECRET')

if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
    print("ERROR: STRAVA_ID and STRAVA_SECRET environment variables not set!")
    sys.exit(1)

# OAuth configuration
REDIRECT_URI = "http://localhost:8888/authorized"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
# Default scopes: request standard read and activity read scopes. Allow override via STRAVA_OAUTH_SCOPES env var.
SCOPES = os.environ.get('STRAVA_OAUTH_SCOPES', 'read,activity:read,activity:read_all')

# Store the authorization code and state
auth_code = None
auth_error = None

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code, auth_error

        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)

        if 'code' in params:
            auth_code = params['code'][0]
            response = """
            <html>
            <head><title>Authorization Successful</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1 style="color: green;">✓ Authorization Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
            </body>
            </html>
            """
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(response.encode())
            print("\n✓ Authorization code received!")

        elif 'error' in params:
            auth_error = params['error'][0]
            response = f"""
            <html>
            <head><title>Authorization Failed</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1 style="color: red;">✗ Authorization Failed</h1>
                <p>Error: {auth_error}</p>
            </body>
            </html>
            """
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(response.encode())
            print(f"\n✗ Authorization failed: {auth_error}")

    def log_message(self, format, *args):
        pass  # Suppress logging

def main():
    global auth_code, auth_error

    print("=" * 70)
    print("Strava OAuth Authorization")
    print("=" * 70)
    print(f"\nClient ID: {STRAVA_CLIENT_ID}")
    print(f"Redirect URI: {REDIRECT_URI}")
    print(f"Scopes: {SCOPES}")
    print("\n" + "=" * 70)

    # Step 1: Start local HTTP server for callback
    print("\nStarting local OAuth callback server on http://localhost:8888...")
    server = HTTPServer(('localhost', 8888), OAuthCallbackHandler)

    # Step 2: Generate authorization URL
    auth_params = {
        'client_id': STRAVA_CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': SCOPES,
        'approval_prompt': 'force',  # Force re-approval to ensure scopes are updated
    }

    # URL-encode the scope parameter safely
    scope_enc = quote(auth_params['scope'], safe='')
    auth_url = f"{AUTHORIZE_URL}?client_id={auth_params['client_id']}&redirect_uri={auth_params['redirect_uri']}&response_type={auth_params['response_type']}&scope={scope_enc}&approval_prompt={auth_params['approval_prompt']}"

    print("\n" + "=" * 70)
    print("STRAVA AUTHORIZATION URL")
    print("=" * 70)
    print(f"\n{auth_url}\n")
    print("=" * 70)
    print("Copy and paste the above URL into your browser.")
    print("Then click 'Authorize' on the Strava page.")
    print("=" * 70 + "\n")

    # Step 3: Wait for callback
    print("Waiting for authorization (this will time out after 5 minutes)...")

    # Handle one request
    server.handle_request()

    if auth_error:
        print(f"Authorization failed: {auth_error}")
        sys.exit(1)

    if not auth_code:
        print("No authorization code received!")
        sys.exit(1)

    # Step 4: Exchange code for tokens
    print("\nExchanging authorization code for refresh token...")

    token_payload = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'code': auth_code,
        'grant_type': 'authorization_code',
    }

    token_response = requests.post(TOKEN_URL, data=token_payload)

    if token_response.status_code != 200:
        print(f"ERROR: Token exchange failed with status {token_response.status_code}")
        print(f"Response: {token_response.text}")
        sys.exit(1)

    token_data = token_response.json()

    refresh_token = token_data.get('refresh_token')
    access_token = token_data.get('access_token')
    token_type = token_data.get('token_type')
    expires_in = token_data.get('expires_in')

    print("\n" + "=" * 70)
    print("✓ SUCCESS! Token obtained with correct scopes")
    print("=" * 70)
    print(f"\nRefresh Token: {refresh_token}")
    print(f"Access Token: {access_token[:30]}...")
    print(f"Token Type: {token_type}")
    print(f"Expires In: {expires_in} seconds")

    # Verify the token has the correct scope
    print("\n" + "=" * 70)
    print("Verifying token scopes...")

    verify_headers = {"Authorization": f"Bearer {access_token}"}
    verify_response = requests.get("https://www.strava.com/api/v3/athlete", headers=verify_headers)

    if verify_response.status_code == 200:
        athlete = verify_response.json()
        print(f"✓ Token is valid!")
        print(f"  Athlete: {athlete.get('firstname')} {athlete.get('lastname')}")
        print(f"  Username: {athlete.get('username')}")
    else:
        print(f"✗ Token verification failed: {verify_response.status_code}")
        print(f"  Response: {verify_response.text}")

    print("\n" + "=" * 70)
    print("UPDATE YOUR ENVIRONMENT VARIABLES:")
    print("=" * 70)
    print(f"\nexport STRAVA_REFRESH='{refresh_token}'")
    print("\nThen run: python api_fetch_activities.py")
    print("=" * 70)

if __name__ == '__main__':
    main()
