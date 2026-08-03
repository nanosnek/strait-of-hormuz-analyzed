# StraitOfHormuzAnalyzed

Vessel traffic data collection for the Strait of Hormuz, the Persian Gulf and
the Gulf of Oman.

**Global Fishing Watch is the main source.** It's free, documented, allowed by
its terms, and reaches back to 2012. It runs about 72 hours behind real time,
which doesn't matter for analysis.

MarineTraffic is kept as a secondary source for one thing GFW can't do: a
live "what's in the strait right now" reading.

## Setup

Use a virtual environment. Installing straight into a system Python fails on
macOS with Homebrew and on most Linux distributions -- they refuse with
`error: externally-managed-environment`.

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`venv/` is gitignored. Activate it in every new shell, or call the interpreter
directly as `./venv/bin/python main.py ...`.

Get a free Global Fishing Watch API token from
[their API portal](https://globalfishingwatch.org/our-apis/), then:

```bash
cp .env.example .env      # paste the token in as GFW_API_ACCESS_TOKEN
```

`.env` is gitignored, and each clone needs its own. Nothing else needs
credentials except `data_get.py`.

Python 3.11 or newer is required, by the GFW client.

## Usage

```bash
python main.py regions                              # list the areas

# Global Fishing Watch -- history and behaviour
python main.py presence --days 90                   # traffic volume, day by day
python main.py presence --days 365 --group-by FLAG  # broken down by flag state
python main.py events --type encounter --days 90    # vessels meeting at sea
python main.py events --type gap --days 90          # vessels going dark

# MarineTraffic -- live only, no history
python main.py snapshot                             # what's there right now
python main.py watch --interval 900                 # keep taking readings
```

Everything writes JSON and CSV side by side.

## Choosing a source

| | Global Fishing Watch | MarineTraffic live map | MarineTraffic reports |
|---|---|---|---|
| Module | `gfw.py` | `livemap.py` | `data_get.py` |
| Credentials | free API token | none | signed-in session |
| History | **2012 onwards** | none — forward only | none |
| Freshness | ~72 h behind | live | live |
| Position detail | 1/vessel/hour | every few minutes | current only |
| Encounters, loitering, AIS gaps | **yes** | no | no |
| IMO / MMSI / callsign | yes | no | yes |
| Allowed by terms | **yes** | no | no |

For coursework, prefer GFW. The MarineTraffic paths use internal endpoints —
fine for exploration, but they break without warning and are against the
site's terms of service.

## Why the events matter here

For the Strait of Hormuz, raw vessel counts are the less interesting half. The
Events API surfaces the behaviour this region is actually studied for:

- **encounter** — two vessels close and slow together for long enough to
  suggest a ship-to-ship transfer
- **gap** — a stretch with no AIS from a vessel that should be transmitting,
  i.e. going dark
- **loitering** — a single vessel lingering in open water
- **port_visit** — arrivals and departures

Tankers in this region routinely switch AIS off. GFW records that as a gap
event; a positions-only source just silently lacks a row.

## Notes on the GFW API

- **Vessel classes are `BUNKER`, `CARGO`, `CARRIER`, `FISHING`, `PASSENGER`,
  `SUPPORT`, `OTHER`.** There's no `TANKER` — oil tankers land under `CARGO`,
  and dedicated fuel-supply vessels under `BUNKER`. `gfw.COMMERCIAL` selects
  cargo, bunker and carrier together.
- **`--days` maxes out at 366.** The API rejects longer ranges; run a loop of
  yearly windows if you need more.
- **Asking for today returns nothing** because of the ~72 h delay. `main.py`
  ends every window `LATENCY_DAYS` in the past automatically.
- **One report at a time.** Concurrent 4Wings reports return HTTP 429.
- `--group-by` has no `VESSEL_TYPE` option, despite what the docs suggest.
  Run once per vessel type instead. It is also **required** -- omitting it is
  a 422, even though the docs call it optional.

### Why so many columns are empty

The report response uses one flat schema shared by every 4Wings report type,
so each request fills in only the columns that apply to it and leaves the rest
null. An empty column means "not applicable to this query", not "missing
data". What you get depends on what you asked for:

| Column | Filled when |
|---|---|
| `date`, `hours`, `report_dataset` | always |
| `flag`, `vessel_ids` (a count) | `--group-by FLAG` |
| `vessel_id`, `imo`, `mmsi`, `call_sign`, `ship_name`, `vessel_type`, `gear_type`, entry/exit/transmission timestamps | `--group-by VESSEL_ID` |
| `lat`, `lon` | `--grid` |
| `detections` | never -- SAR dataset only |

How full the result is depends on the combination:

| `--group-by` | `--grid` | columns filled |
|---|---|---|
| `FLAG` (default) | no | 5 / 20 |
| `FLAG` | yes | 7 / 20 |
| `VESSEL_ID` | no | 16 / 20 |
| **`VESSEL_ID`** | **yes** | **18 / 20** |

So for the richest output:

```bash
python main.py presence --days 90 --grid --group-by VESSEL_ID
```

The two that stay empty are structural: `detections` belongs to the SAR
dataset, and `vessel_ids` is a count, meaningless once each row is already a
single vessel.

Columns empty in *every* row are dropped from the CSV, since nothing is lost
and a spreadsheet with 13 dead columns is hard to read. The JSON keeps the
full schema.

## Notes on the MarineTraffic live map

The map is drawn from one undocumented endpoint:

```
GET https://www.marinetraffic.com/getData/get_data_json_4/z:{Z}/X:{X}/Y:{Y}/station:0
Header: X-Requested-With: XMLHttpRequest
```

Four things worth knowing, none of them documented:

**The `X-Requested-With` header is mandatory** — without it you get a 400 HTML
page rather than JSON.

**The tile grid is `2^(Z-1)`, not `2^Z`.** Standard Web Mercator slippy tiles,
but 512px ones, so the API's `Z` is one higher than conventional tile zoom.

**Cloudflare blocks on TLS fingerprint, not cookies.** `requests`, `httpx` and
`curl` all get a 403 no matter what headers you send. `curl_cffi` impersonating
Chrome gets a 200 with no cookies at all.

**Low zoom is silently truncated** to roughly the largest 2,400 vessels per
tile. If a count looks low, raise `--zoom` before believing it.

Fields arrive as strings and are often absent. `SPEED` is in **tenths of a
knot** (`128` is 12.8 kn), `ELAPSED` is minutes since the last position, and
`SHIP_ID` is a MarineTraffic internal id, not an MMSI. Rows named `[SAT-AIS]`
are satellite teasers with no real identity.

## Layout

```
main.py         CLI: presence / events / snapshot / watch / regions
gfw.py          Global Fishing Watch -- presence, events, vessel search
regions.py      the bounding boxes, shared by both sources
livemap.py      MarineTraffic live-map tiles
data_get.py     MarineTraffic reports (needs a session; credentials via .env)
json_to_csv.py  JSON -> CSV for any of the above
```

## Credits

Original reports collection by eveie Prewitt.
