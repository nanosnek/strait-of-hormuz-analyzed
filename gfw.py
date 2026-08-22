"""
Vessel traffic from Global Fishing Watch -- the main data source for this
project.

GFW publishes a documented, free API built on the same AIS feed the commercial
trackers use. Compared with scraping MarineTraffic (livemap.py) it is easier to
work with and allowed by its terms, and it reaches back to 2012 instead of only
forward from the day you start collecting.

The one thing it will not do is "right now": AIS data lands about 72 hours
behind real time, and positions are binned to one per vessel per hour. So this
is the source for analysis, and livemap.py is the source for a live snapshot.

Two kinds of data are useful here:

  presence  gridded vessel-hours in an area over a date range, optionally
            broken down by flag or by vessel. This is the traffic-volume
            measurement -- how busy the strait was, day by day.

  events    encounters, loitering, port visits, and AIS gaps. For the Strait
            of Hormuz these matter more than raw counts: an encounter is two
            vessels meeting at sea, and a gap is a vessel switching its AIS
            off, which is how ship-to-ship transfers and sanctions evasion
            show up in the data.

Setup:
    pip install -r requirements.txt
    Register at https://globalfishingwatch.org/our-apis/ for a free token,
    then copy .env.example to .env and paste it in as GFW_API_ACCESS_TOKEN.
"""

import asyncio
import sys

try:
    import gfwapiclient
except ImportError:
    sys.exit("gfw.py needs the GFW client:  pip install -r requirements.txt")

from source import VesselDataSource

# The API is delayed by roughly this much, so asking for today returns nothing.
LATENCY_DAYS = 4

# Event datasets, copied from the client's own enums so they can't drift.
# (There is no presence equivalent here: create_ais_presence_report selects
# public-global-presence itself.)
EVENT_DATASETS = {
    "encounter": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
    "port_visit": "public-global-port-visits-events:latest",
    "gap": "public-global-gaps-events:latest",
    "fishing": "public-global-fishing-events:latest",
}

# Vessel classes GFW recognises. Note there is no "tanker" class here -- GFW
# groups oil tankers under CARGO, and calls dedicated fuel-supply vessels
# BUNKER. Both are worth pulling for a Hormuz study.
VESSEL_TYPES = ["BUNKER", "CARGO", "CARRIER", "FISHING", "PASSENGER",
                "SUPPORT", "OTHER"]
COMMERCIAL = ["CARGO", "BUNKER", "CARRIER"]

TOKEN_HELP = (
    "Register for a free token at https://globalfishingwatch.org/our-apis/\n"
    "then copy .env.example to .env and paste it in."
)


def to_records(result):
    """
    Convert a client response object into plain dictionaries.

    Free function rather than a method because it touches no source state --
    it is a pure reshaping of whatever the client handed back. Single results
    are wrapped in a list so callers always get the same shape.

    Args:
        result: A result object from the GFW client, exposing .data().

    Returns:
        list[dict]: One dict per record, with JSON-safe values (dates become
            strings).
    """
    items = result.data()
    if not isinstance(items, list):
        items = [items]
    return [item.model_dump(mode="json") for item in items]


class GFWSource(VesselDataSource):
    """
    Queries the Global Fishing Watch API.

    Holds the validated token and a single API client, so a run that makes
    several calls -- stepping through years to get around the 366-day window
    limit, say -- reuses one connection pool instead of building a new one
    per call.

    The client is asynchronous, but every method here is ordinary and
    blocking: each wraps its own call in asyncio.run so callers never have to
    know. That costs about a tenth of a millisecond per call, which is
    nothing next to the request itself.

    Attributes:
        token (str): The API access token, validated at construction.
        verbose (bool): Inherited; whether progress goes to stderr.
    """

    def __init__(self, token=None, verbose=True):
        """
        Args:
            token (str | None): API token. Read from GFW_API_ACCESS_TOKEN
                (or .env) when omitted.
            verbose (bool): Print progress to stderr. Defaults to True.

        Raises:
            SystemExit: If no token is set, or the token is corrupted.
        """
        super().__init__(verbose)
        self.token = token or self.require_env("GFW_API_ACCESS_TOKEN",
                                               TOKEN_HELP)
        self._check_token(self.token)
        self._client = None

    @staticmethod
    def _check_token(token):
        """
        Reject a token that can't be a real one, with a useful message.

        A JWT is base64url, so it is ASCII by definition. Anything else means
        the token was retyped or read off a screenshot by OCR, which silently
        swaps in lookalike characters -- Cyrillic Ze for 3, Cyrillic Ha for X,
        and so on. Left alone this surfaces much later as a UnicodeEncodeError
        from deep inside the HTTP library, which says nothing about the real
        problem.

        This catches only part of that, though: OCR mostly confuses characters
        that are both ASCII -- I for l, O for 0, S for 5 -- and those stay
        invisible here. Failing this check means the token is definitely
        corrupt; passing it does not mean it is clean.

        Args:
            token (str): The token to check.

        Returns:
            None

        Raises:
            SystemExit: If the token contains non-ASCII characters.
        """
        bad = [(i, c) for i, c in enumerate(token) if not c.isascii()]
        if bad:
            spots = ", ".join(f"position {i} (U+{ord(c):04X})"
                              for i, c in bad[:5])
            sys.exit(
                f"GFW_API_ACCESS_TOKEN contains {len(bad)} non-ASCII "
                f"character(s): {spots}.\n"
                "An API token is always plain ASCII, so this copy is corrupted"
                " -- usually from copying it out of a screenshot or a chat"
                "that \n reformatted it. Copy the token as text straight from"
                " the API portal and paste it into .env again."
            )

    @property
    def client(self):
        """
        The API client, built once on first use.

        Returns:
            gfwapiclient.Client: Authenticated client. The library reads the
                token from the environment itself.
        """
        if self._client is None:
            self._client = gfwapiclient.Client(access_token=self.token)
        return self._client

    def presence(self, region, start_date, end_date,
                 temporal_resolution="DAILY",
                 group_by="FLAG", vessel_types=None, spatial_resolution="LOW",
                 spatial_aggregation=True):
        """
        Measure vessel presence inside a region over a date range.

        Presence is roughly vessel-hours: how much vessel activity the area
        saw, which is the traffic-volume measurement for a chokepoint study.

        Args:
            region (regions.BBox): The area to measure.
            start_date (str): Start of the window, "YYYY-MM-DD".
            end_date (str): End of the window, "YYYY-MM-DD". Should be at
                least LATENCY_DAYS in the past, or the API has no data yet.
                The window must be 366 days or less.
            temporal_resolution (str): Size of each time bucket -- "HOURLY",
                "DAILY", "MONTHLY", "YEARLY", or "ENTIRE" for a single total.
                Defaults to "DAILY".
            group_by (str): Break the totals down by "VESSEL_ID", "FLAG",
                "GEARTYPE", "FLAGANDGEARTYPE" or "MMSI". Defaults to "FLAG".
                The API *requires* this -- omitting it returns HTTP 422 --
                even though the published docs call it optional. There is also
                no "VESSEL_TYPE" option, despite the docs listing one; to
                split by vessel type, call this once per type. Sum across the
                groups to get an overall total.
            vessel_types (list[str] | None): Restrict to these classes, e.g.
                COMMERCIAL for cargo, bunker and carrier traffic. Defaults to
                all vessels.
            spatial_resolution (str): "LOW" (0.1 degree cells) or "HIGH"
                (0.01 degree). Only matters when spatial_aggregation is False.
                Defaults to "LOW".
            spatial_aggregation (bool): True totals the whole region into one
                row per time bucket. False returns one row per grid cell,
                which is what fills in lat and lon for mapping. Defaults to
                True.

        Returns:
            list[dict]: One record per time bucket, per group, and -- when
                spatial_aggregation is False -- per grid cell.

                The response schema is shared across every 4Wings report type,
                so most columns come back empty on any given request. Which
                ones fill in depends on what you asked for:

                  always            date, hours, report_dataset
                  group_by=FLAG     flag, vessel_ids (a count)
                  group_by=VESSEL_ID  vessel_id, imo, mmsi, call_sign,
                                    ship_name, vessel_type, gear_type, and
                                    the entry, exit and transmission stamps
                  aggregation off   lat, lon
                  never (SAR only)  detections

                So an empty column means "not applicable to this query", not
                "missing data". Grouping by vessel with aggregation off is the
                fullest combination, at 18 of 20 columns.
        """
        filters = []
        if vessel_types:
            quoted = ", ".join(f"'{t.lower()}'" for t in vessel_types)
            filters.append(f"vessel_type in ({quoted})")

        async def call():
            return await self.client.fourwings.create_ais_presence_report(
                geojson=region.to_geojson(),
                start_date=start_date,
                end_date=end_date,
                temporal_resolution=temporal_resolution,
                spatial_resolution=spatial_resolution,
                spatial_aggregation=spatial_aggregation,
                group_by=group_by,
                filters=filters or None,
            )

        return to_records(asyncio.run(call()))

    def events(self, region, start_date, end_date, event_type="encounter",
               vessel_types=None, limit=1000):
        """
        Fetch vessel behaviour events inside a region over a date range.

        For the Strait of Hormuz these are usually more revealing than raw
        counts. An encounter is two vessels close and slow together for long
        enough to suggest a transfer; a gap is a stretch with no AIS from a
        vessel that should be transmitting, i.e. one that has gone dark.

        Args:
            region (regions.BBox): The area to search.
            start_date (str): Start of the window, "YYYY-MM-DD".
            end_date (str): End of the window, "YYYY-MM-DD". Should be at
                least LATENCY_DAYS in the past.
            event_type (str): One of the keys of EVENT_DATASETS --
                "encounter", "loitering", "port_visit", "gap" or "fishing".
                Defaults to "encounter".
            vessel_types (list[str] | None): Restrict to these classes, e.g.
                COMMERCIAL. Defaults to all vessels.
            limit (int): Maximum events to return. Defaults to 1000.

        Returns:
            list[dict]: One record per event, each with start and end times, a
                position, the vessel involved and type-specific detail.

        Raises:
            SystemExit: If event_type isn't recognised.
        """
        if event_type not in EVENT_DATASETS:
            sys.exit(f"unknown event type {event_type!r}; "
                     f"choose from {', '.join(sorted(EVENT_DATASETS))}")

        async def call():
            return await self.client.events.get_all_events(
                datasets=[EVENT_DATASETS[event_type]],
                geometry=region.to_geojson(),
                start_date=start_date,
                end_date=end_date,
                vessel_types=vessel_types,
                limit=limit,
            )

        return to_records(asyncio.run(call()))

    def search_vessels(self, query, limit=20):
        """
        Look a vessel up by name, MMSI, IMO or callsign.

        Useful for turning an identifier from another dataset into the GFW
        vessel id that events() and the presence VESSEL_ID grouping use.

        Args:
            query (str): Name, MMSI, IMO or callsign to search for.
            limit (int): Maximum matches to return. Defaults to 20.

        Returns:
            list[dict]: One record per matching vessel, with its GFW id and
                registry identifiers.
        """
        async def call():
            return await self.client.vessels.search_vessels(query=query,
                                                            limit=limit)

        return to_records(asyncio.run(call()))
