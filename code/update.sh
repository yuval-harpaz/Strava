#!/usr/bin/env bash
python3 code/api_fetch_activities_simple.py
python3 code/strava_analysis.py 
echo "Done Strava update"