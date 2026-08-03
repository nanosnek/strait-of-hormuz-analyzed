"""
Author: eveie Prewitt
Citation: https://curlconverter.com/
Retrieves data from marinetraffic.com relating to oil tankers in the Persian
and Oman gulfs.

This hits the /en/reports/ endpoint, which requires a signed-in MarineTraffic
account. It returns richer per-vessel identity than the live map does --
IMO, MMSI, callsign, ETA, current and next port -- but the session it depends
on expires within minutes and has to be re-captured from a browser.

Credentials come from the environment instead of being written into this
file. Copy .env.example to .env and fill it in; .env is gitignored.

    MT_COOKIE        the full Cookie: header string from a logged-in session
    MT_ACCESS_TOKEN  the X-Access-Token bearer JWT (optional)

To capture them: sign in at marinetraffic.com, open DevTools > Network, click
any request to /en/reports/, then "Copy as cURL" and lift the two values.

For vessel data with no account at all, use gfw.py (history) or livemap.py
(live positions) instead.
"""

import json
import os
import sys

import requests

REPORTS_URL = "https://www.marinetraffic.com/en/reports/"

# flag,shipname,photo,... -- the column set the project's output.csv is built on
COLUMNS = (
    "flag,shipname,photo,recognized_next_port,reported_eta,reported_destination,"
    "current_port,imo,ship_type,show_on_live_map,time_of_latest_position,"
    "lat_of_latest_position,lon_of_latest_position,notes"
)

# area_local_in: 25 = Persian Gulf, 41 = Oman Gulf.  ship_type_in: 8 = Tankers.
DEFAULT_PARAMS = {
    "asset_type": "vessels",
    "columns": COLUMNS,
    "area_local_in": "25,41",
    "ship_type_in": "8",
    "filters_with_name_filtering": "yard_number_in",
}


def load_dotenv(path=".env"):
    """
    Load KEY=value pairs from a .env file into the environment.

    Blank lines and lines starting with # are skipped, and surrounding quotes
    are stripped from values. Existing environment variables win, so an
    exported value overrides the file. Does nothing if the file is absent.

    Args:
        path (str): Path to the .env file. Defaults to ".env" in the working
            directory.

    Returns:
        None
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_headers():
    """
    Assemble the request headers, including the captured session.

    Returns:
        dict: Headers for the reports request. X-Access-Token is included
            only if MT_ACCESS_TOKEN is set.

    Raises:
        SystemExit: If MT_COOKIE isn't set, with instructions for capturing
            one. This is a normal setup step rather than a bug, so it exits
            with a readable message instead of a traceback.
    """
    load_dotenv()
    cookie = os.environ.get("MT_COOKIE")
    if not cookie:
        sys.exit(
            "MT_COOKIE is not set.\n"
            "The reports endpoint needs a signed-in session. Copy .env.example "
            "to .env and paste in a fresh Cookie header, or use gfw.py, which "
            "needs only a free API token."
        )

    headers = {
        "User-Agent": os.environ.get(
            "MT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
        ),
        "Accept": "application/json, text/plain, */*",
        # Same undocumented requirement as the live map: without this header
        # the site answers with an HTML error page instead of JSON.
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie,
        "Referer": "https://www.marinetraffic.com/en/data/?asset_type=vessels",
    }
    token = os.environ.get("MT_ACCESS_TOKEN")
    if token:
        headers["X-Access-Token"] = token
    return headers


def fetch_reports(params=None):
    """
    Download the tanker report for the Persian and Oman gulfs.

    Args:
        params (dict | None): Query parameters overriding DEFAULT_PARAMS, for
            example a different area or ship type. Defaults to the project's
            standard filter.

    Returns:
        dict: The raw payload, shaped {"data": [ {...}, ... ]}, where each
            entry carries identity fields the live map doesn't expose --
            IMO, MMSI, CALLSIGN, ETA and port names.

    Raises:
        SystemExit: If no session cookie is configured.
        requests.HTTPError: If the request fails, most often 401 or 403 once
            the captured session has expired.
    """
    response = requests.get(
        REPORTS_URL,
        params={**DEFAULT_PARAMS, **(params or {})},
        headers=build_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    payload = fetch_reports()
    out = sys.argv[1] if len(sys.argv) > 1 else "test.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent="\t")
    print(f"wrote {len(payload.get('data', []))} vessels -> {out}")
