"""
Live vessel positions for the Strait of Hormuz, pulled from MarineTraffic's
public live-map tiles.

This is the live-only counterpart to gfw.py. The live map is drawn from a
single undocumented endpoint that needs no account, no API key and no session
cookie:

    GET /getData/get_data_json_4/z:{Z}/X:{X}/Y:{Y}/station:0
    Header: X-Requested-With: XMLHttpRequest    <- required, else 400 HTML

Trade-off vs. gfw.py: this gives positions from the last few minutes, but has
no history at all -- you only ever get what you collect from the moment you
start. Use gfw.py for anything over time, and this for "right now".
"""

import json
import math
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    sys.exit("livemap.py needs curl_cffi:  pip install -r requirements.txt")

# Region boxes and the BBox class are shared with gfw.py.
from regions import REGIONS, STRAIT_OF_HORMUZ  # noqa: E402  (after the import guard)


BASE_URL = "https://www.marinetraffic.com/getData/get_data_json_4"

HEADERS = {
    # Without this the site returns a 400 HTML error page instead of JSON.
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.marinetraffic.com/en/ais/home",
}

# Cloudflare blocks on TLS fingerprint, not on cookies or headers: plain
# requests/urllib/curl get a 403 here no matter what you send. curl_cffi
# impersonating Chrome gets through with no cookies at all.
SESSION = cffi_requests.Session(impersonate="chrome")

# MarineTraffic's coarse ship-type buckets (the SHIPTYPE field).
SHIP_TYPES = {
    0: "Unspecified",
    1: "Navigation Aid",
    2: "Fishing",
    3: "Tug & Special Craft",
    4: "High Speed Craft",
    6: "Passenger",
    7: "Cargo",
    8: "Tanker",
    9: "Pleasure Craft",
}
TANKER = 8
CARGO = 7

# Small craft that aren't commercial traffic, hidden by default.
DEFAULT_HIDE_TYPES = [1, 2, 9]  # nav aids, fishing, pleasure


# --------------------------------------------------------------------------
# tile geometry
# --------------------------------------------------------------------------

def lonlat_to_tile(lon, lat, zoom):
    """
    Find the map tile containing a longitude/latitude point.

    These are ordinary Web Mercator "slippy" tiles with one twist: the grid is
    2 ** (zoom - 1) across, not the usual 2 ** zoom. The map uses 512-pixel
    tiles, so its zoom number is one higher than conventional 256-pixel tile
    zoom. Getting this wrong returns tiles for entirely the wrong part of the
    world, or empty ones.

    Latitude is clamped to +/-85.05 degrees, the limit of the Mercator
    projection, and the returned indices are clamped to the grid, so points
    at or beyond the antimeridian and the poles stay in range.

    Args:
        lon (float): Longitude in degrees, -180 to 180.
        lat (float): Latitude in degrees. Values beyond +/-85.05 are clamped.
        zoom (int): Map zoom level, 3 to 12 in practice.

    Returns:
        tuple[int, int]: The (x, y) tile indices, each 0 to 2 ** (zoom - 1) - 1.
            x increases eastward and y increases *southward*, so the north
            edge of a box gives the smaller y.
    """
    n = 2 ** (zoom - 1)
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tiles_for_bbox(bbox, zoom):
    """
    List every tile a bounding box touches.

    Args:
        bbox (regions.BBox): The area to cover.
        zoom (int): Map zoom level. Each step up roughly quadruples the tile
            count, so a large region at high zoom is a lot of requests.

    Returns:
        list[tuple[int, int]]: The (x, y) index of each tile overlapping the
            box, in column-major order. Always at least one tile.
    """
    x0, y0 = lonlat_to_tile(bbox.west, bbox.north, zoom)  # north -> smaller y
    x1, y1 = lonlat_to_tile(bbox.east, bbox.south, zoom)
    return [
        (x, y)
        for x in range(min(x0, x1), max(x0, x1) + 1)
        for y in range(min(y0, y1), max(y0, y1) + 1)
    ]


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch_tile(zoom, x, y, filters=None, retries=3):
    """
    Download the vessels in a single map tile.

    Failure is reported on stderr and returns an empty list rather than
    raising, so one unreachable tile doesn't abandon a whole region.

    Args:
        zoom (int): Map zoom level.
        x (int): Tile column index, from lonlat_to_tile.
        y (int): Tile row index, from lonlat_to_tile.
        filters (dict | None): Optional filter object, URL-encoded into the
            path. The site's "type" key is an *exclusion* map: the ship-type
            codes listed there are the ones hidden. Defaults to no filter.
        retries (int): How many times to try before giving up. Defaults to 3.

    Returns:
        list[dict]: One raw dict per vessel, with the site's own field names
            and every value a string. Empty if the tile has no vessels or if
            every attempt failed.
    """
    url = f"{BASE_URL}/z:{zoom}/X:{x}/Y:{y}/station:0"
    if filters:
        url += "/filters:" + quote(json.dumps(filters, separators=(",", ":")), safe="")

    problem = None
    for _ in range(retries):
        try:
            response = SESSION.get(url, headers=HEADERS, timeout=30)
            if response.status_code == 200 and "json" in response.headers.get("content-type", ""):
                return response.json().get("data", {}).get("rows", [])
            problem = f"HTTP {response.status_code}"
            if response.status_code == 403:
                problem += " (Cloudflare - try a different impersonate profile)"
        except Exception as error:
            problem = repr(error)
        time.sleep(1)

    print(f"  ! tile z:{zoom}/X:{x}/Y:{y} failed: {problem}", file=sys.stderr)
    return []


# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------

def to_number(value):
    """
    Convert one raw API field to a float.

    Every value the endpoint returns is a string, and any field can be absent,
    empty or null for a given vessel, so this never raises.

    Args:
        value (str | None): The raw field value.

    Returns:
        float | None: The number, or None if the field was missing, empty or
            not numeric.
    """
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def decode_row(row, observed_at):
    """
    Convert one raw API row into named fields with real units.

    This is where the endpoint's undocumented encodings get undone -- most
    importantly SPEED, which arrives in tenths of a knot.

    Args:
        row (dict): One raw vessel dict, as returned by fetch_tile.
        observed_at (str): ISO 8601 UTC timestamp of when this row was
            fetched, copied onto the record so a series of readings can be
            ordered later.

    Returns:
        dict: The vessel with typed values and readable keys, including:
            ship_id (str): MarineTraffic's internal id -- *not* an MMSI.
            shipname (str | None): None for satellite rows, which are
                anonymised.
            lat, lon (float | None): Position in degrees.
            speed_knots (float | None): Speed over ground, converted from
                the raw tenths-of-a-knot value.
            course_deg, heading_deg (float | None): Direction of travel and
                direction the hull points, which differ when a vessel is
                pushed sideways by current.
            minutes_since_position (float | None): Age of the position report.
            ship_type (str): Human-readable type from SHIP_TYPES.
            position_source (str): "terrestrial" or "satellite".
    """
    speed = to_number(row.get("SPEED"))
    type_code = row.get("SHIPTYPE")
    if type_code is not None:
        type_code = int(type_code)
    # Rows named [SAT-AIS] are satellite teasers: obfuscated id, no identity.
    satellite = row.get("SHIPNAME") == "[SAT-AIS]"
    ship_id = row.get("SHIP_ID")

    return {
        "observed_at": observed_at,
        "ship_id": ship_id,  # MarineTraffic internal id, NOT MMSI
        "shipname": None if satellite else row.get("SHIPNAME"),
        "lat": to_number(row.get("LAT")),
        "lon": to_number(row.get("LON")),
        "speed_knots": None if speed is None else speed / 10.0,  # raw is 0.1 kn
        "course_deg": to_number(row.get("COURSE")),
        "heading_deg": to_number(row.get("HEADING")),
        "rate_of_turn": to_number(row.get("ROT")),
        "minutes_since_position": to_number(row.get("ELAPSED")),
        "destination": row.get("DESTINATION"),
        "flag": row.get("FLAG"),  # ISO-3166 alpha-2
        "length_m": to_number(row.get("LENGTH")),
        "width_m": to_number(row.get("WIDTH")),
        "dwt": to_number(row.get("DWT")),
        "ship_type_code": type_code,
        "ship_type": SHIP_TYPES.get(type_code, "Unknown") if type_code is not None else None,
        "detailed_type_code": row.get("GT_SHIPTYPE"),
        "nav_status": row.get("STATUS_NAME"),
        "position_source": "satellite" if satellite else "terrestrial",
        "url": None if satellite else f"https://www.marinetraffic.com/en/ais/details/ships/shipid:{ship_id}",
    }


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def fetch_region(bbox, zoom=9, delay=0.3, hide_types=None, verbose=True):
    """
    Fetch every vessel currently inside a region.

    Walks the tiles covering the box, drops duplicates where tiles overlap,
    and clips to the box because tiles overspill their nominal bounds.

    Args:
        bbox (regions.BBox): The area to cover.
        zoom (int): API zoom, 3 to 12. Low zoom is truncated server-side: a
            tile returns roughly the largest 2,400 vessels and silently drops
            the rest, so a z:7 tile can report a third of what its four z:8
            children do. If a count looks low, raise this before believing it.
            Defaults to 9.
        delay (float): Seconds to wait between requests, so we don't hammer
            the site. Defaults to 0.3.
        hide_types (list[int] | None): SHIPTYPE codes to exclude, e.g.
            [1, 2, 9] to drop navigation aids, fishing boats and pleasure
            craft. The site's filter is exclusion-only -- there is no way to
            ask for a single type, which is why fetch_tankers has to filter
            again afterwards. Defaults to hiding nothing.
        verbose (bool): Whether to report per-tile progress on stderr.
            Defaults to True.

    Returns:
        list[dict]: One decoded vessel per entry, as built by decode_row,
            deduplicated by ship id. Empty if nothing was in range or every
            tile request failed.
    """
    tiles = tiles_for_bbox(bbox, zoom)
    filters = {"type": {str(t): 1 for t in hide_types}} if hide_types else None

    if verbose:
        print(f"zoom {zoom}: {len(tiles)} tile(s) over "
              f"[{bbox.west}, {bbox.south}] .. [{bbox.east}, {bbox.north}]", file=sys.stderr)

    seen = set()
    vessels = []

    for number, (x, y) in enumerate(tiles, start=1):
        time.sleep(delay)
        rows = fetch_tile(zoom, x, y, filters)
        # Stamp per tile, not once for the whole region: a large region at high
        # zoom is hundreds of tiles and takes minutes, so a single timestamp
        # would be badly wrong for everything fetched after the first one.
        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        added = 0
        for row in rows:
            ship_id = row.get("SHIP_ID")
            if ship_id in seen:
                continue
            lat = to_number(row.get("LAT"))
            lon = to_number(row.get("LON"))
            if lat is None or lon is None:
                continue
            # Tiles overspill their nominal bounds, so clip to the region.
            if not bbox.contains(lon, lat):
                continue
            seen.add(ship_id)
            vessels.append(decode_row(row, observed_at))
            added += 1

        if verbose:
            print(f"  [{number}/{len(tiles)}] {len(rows):5d} rows, "
                  f"+{added} in-region (total {len(vessels)})", file=sys.stderr)

    return vessels


def fetch_tankers(bbox=STRAIT_OF_HORMUZ, **kwargs):
    """
    Fetch only the tankers currently inside a region.

    The SHIPTYPE check here is what guarantees tankers-only; passing
    hide_types just shrinks the payload on the wire first, because the site's
    filter can only exclude types, never select one.

    Args:
        bbox (regions.BBox): The area to cover. Defaults to the Strait of
            Hormuz.
        **kwargs: Passed straight through to fetch_region -- zoom, delay,
            hide_types and verbose.

    Returns:
        list[dict]: Decoded vessels whose ship_type_code is TANKER (8).
    """
    return [v for v in fetch_region(bbox, **kwargs) if v["ship_type_code"] == TANKER]


def total_ships_worldwide():
    """
    Fetch the site's global count of currently tracked vessels.

    Mostly useful as a cheap check that the endpoint is reachable and that
    Cloudflare is still letting us through.

    Returns:
        int | None: The worldwide vessel count, or None if the response
            couldn't be parsed as a number.
    """
    response = SESSION.get("https://www.marinetraffic.com/getData/get_total_ships",
                           headers=HEADERS, timeout=30)
    try:
        return int(response.text.strip())
    except (ValueError, AttributeError):
        return None
