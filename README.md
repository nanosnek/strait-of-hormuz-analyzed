# StraitOfHormuzAnalyzed

Vessel traffic data collection for the Strait of Hormuz, the Persian Gulf and
the Gulf of Oman.

**Global Fishing Watch is the main source.** It's free, documented, allowed by
its terms, and reaches back to 2012. It runs about 72 hours behind real time,
which doesn't matter for analysis.

MarineTraffic is kept as a secondary source for one thing GFW can't do: a
live "what's in the strait right now" reading.

## Setup

### Windows (PowerShell)

Open PowerShell **in the project folder**. If you're not sure you're in the
right place, run `dir` — you should see `main.py` listed.

**1. Make a virtual environment and turn it on.**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If that second line fails with _"running scripts is disabled on this system"_,
Windows is blocking the activate script. Allow it for your own account, then
try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

You know it worked when your prompt starts with `(venv)`. **You have to do
this in every new PowerShell window** — the `(venv)` prefix is how you tell.

**2. Install the packages.**

```powershell
pip install -r requirements.txt
```

**3. Get a free API token** from
[globalfishingwatch.org/our-apis](https://globalfishingwatch.org/our-apis/),
then make your `.env` file:

```powershell
Copy-Item .env.example .env
notepad .env
```

Paste the token after `GFW_API_ACCESS_TOKEN=` on line 5, with no spaces and no
quotes, then save and close Notepad.

**Copy the token as text.** Do not retype it and do not read it off a
screenshot — a token has ~870 characters, and OCR silently swaps lookalikes
(`I` for `l`, `0` for `O`, Cyrillic `З` for `3`). It will fail in a way that
looks like a code bug.

**4. Check it works.**

```powershell
python main.py regions
```

### macOS and Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # paste the token in as GFW_API_ACCESS_TOKEN
```

Installing without a virtual environment fails on Homebrew Python and most
Linux distributions — they refuse with `error: externally-managed-environment`.

### Either way

`venv/` and `.env` are both gitignored. Each clone needs its own `.env`.
Python 3.11 or newer is required, by the GFW client.

## When something goes wrong on Windows

**`python : The term 'python' is not recognized`**
Python isn't on your PATH. Try `py` instead of `python` everywhere, or
reinstall Python with "Add python.exe to PATH" ticked.

**`running scripts is disabled on this system`**
See step 1 above — run the `Set-ExecutionPolicy` line.

**`ModuleNotFoundError: No module named 'gfwapiclient'`**
Your venv isn't active, or the packages aren't installed. Check for `(venv)`
at the start of your prompt; if it's missing, run `.\venv\Scripts\Activate.ps1`
again, then `pip install -r requirements.txt`.

**`PermissionError: [Errno 13] Permission denied` on a `.csv`**
The file is open in Excel. Excel locks whatever it has open, so nothing else
can write to it. Close it and run again:

```powershell
Get-Process EXCEL -ErrorAction SilentlyContinue | Stop-Process -Force
```

(That discards unsaved changes, so save first if you have edits.) Writing into
a OneDrive folder can cause the same thing mid-sync.

**`UnicodeEncodeError: 'ascii' codec can't encode character`**
Your token has non-ASCII characters in it — see step 3. Re-copy it as text.

**`422 Unprocessable Entity`**
The API rejected a parameter. The message names which one; `--group-by` is
required and must be one of the five listed values.

**`AttributeError: The geopandas.dataset has been deprecated`**
You're following a tutorial written for GeoPandas 0.x. See
[The GeoPandas trap](#the-geopandas-trap) below — nothing is wrong with your
install.

**`has no latitude/longitude columns`** when mapping
The presence file was collected without `--grid`, so it has no coordinates.
Re-collect with `--grid`.

## Usage

On Windows use `python`; on macOS/Linux use `python3` (or `./venv/bin/python`
if you'd rather not activate the venv).

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

# Map any of the above
python main.py map presence_hormuz_2026-04-30_2026-07-29.csv
python main.py map snapshot.csv -o vessels.png
```

Everything writes JSON and CSV side by side.

## Choosing a source

|                                 | Global Fishing Watch | MarineTraffic live map | MarineTraffic reports |
| ------------------------------- | -------------------- | ---------------------- | --------------------- |
| Module                          | `gfw.py`             | `livemap.py`           | `data_get.py`         |
| Credentials                     | free API token       | none                   | signed-in session     |
| History                         | **2012 onwards**     | none — forward only    | none                  |
| Freshness                       | ~72 h behind         | live                   | live                  |
| Position detail                 | 1/vessel/hour        | every few minutes      | current only          |
| Encounters, loitering, AIS gaps | **yes**              | no                     | no                    |
| IMO / MMSI / callsign           | yes                  | no                     | yes                   |
| Allowed by terms                | **yes**              | no                     | no                    |

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

| Column                                                                                                               | Filled when               |
| -------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| `date`, `hours`, `report_dataset`                                                                                    | always                    |
| `flag`, `vessel_ids` (a count)                                                                                       | `--group-by FLAG`         |
| `vessel_id`, `imo`, `mmsi`, `call_sign`, `ship_name`, `vessel_type`, `gear_type`, entry/exit/transmission timestamps | `--group-by VESSEL_ID`    |
| `lat`, `lon`                                                                                                         | `--grid`                  |
| `detections`                                                                                                         | never -- SAR dataset only |

How full the result is depends on the combination:

| `--group-by`     | `--grid` | columns filled |
| ---------------- | -------- | -------------- |
| `FLAG` (default) | no       | 5 / 20         |
| `FLAG`           | yes      | 7 / 20         |
| `VESSEL_ID`      | no       | 16 / 20        |
| **`VESSEL_ID`**  | **yes**  | **18 / 20**    |

So for the richest output:

```bash
python main.py presence --days 90 --grid --group-by VESSEL_ID
```

The two that stay empty are structural: `detections` belongs to the SAR
dataset, and `vessel_ids` is a count, meaningless once each row is already a
single vessel.

Columns empty in _every_ row are dropped from the CSV, since nothing is lost
and a spreadsheet with 13 dead columns is hard to read. The JSON keeps the
full schema.

## Mapping the data

```powershell
python main.py map presence_hormuz_2026-04-30_2026-07-29.csv
```

It picks the map type from the columns: a file with `hours` becomes a density
map (one coloured square per grid cell), anything else becomes a point per
vessel, coloured by ship type. The image lands next to the CSV unless you pass
`-o`.

**A presence CSV only has coordinates if you collected it with `--grid`.**
Without that flag, `lat` and `lon` are empty and there is nothing to plot:

```powershell
python main.py presence --days 90 --grid --group-by VESSEL_ID
```

### The GeoPandas trap

Nearly every tutorial gets the basemap like this:

```python
world = geopandas.read_file(geopandas.datasets.get_path("naturalearth_lowres"))
```

**That was removed in GeoPandas 1.0** and now raises `AttributeError`. There is
nothing wrong with your install. `mapping.py` downloads the same Natural Earth
data from its CDN instead and caches it in `basemap/`, so it is fetched once
and then works offline. If you need it in a notebook of your own:

```python
import geopandas as gpd
land = gpd.read_file("https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip")
```

Use the 10m ("large scale") data, not the usual 110m — the strait is barely two
degrees across, and at 110m the coastline is too coarse to recognise.

### Using it from a notebook

```python
import mapping, regions

gdf = mapping.load_csv("presence_hormuz_2026-04-30_2026-07-29.csv")
m = mapping.RegionMap(regions.STRAIT_OF_HORMUZ)
m.density(gdf)                 # or m.points(gdf)
m.save("hormuz.png")
```

`load_csv` returns an ordinary GeoDataFrame in EPSG:4326, so everything else
in GeoPandas works on it normally.

Two things about the coordinates worth knowing. The API returns float32, so
`55.8` arrives as `55.79999923706055` — round before grouping by cell or every
row looks distinct. And the colour scale is logarithmic by default, because
the busiest cell off Bandar Abbas holds over a hundred times what open water
does; on a linear scale the port swamps everything else.

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
main.py         CLI: presence / events / map / snapshot / watch / regions
source.py       VesselDataSource -- the base class both sources inherit
gfw.py          GFWSource: presence, events, vessel search
regions.py      BBox and the bounding boxes, shared by both sources
livemap.py      LiveMapSource: MarineTraffic live-map tiles
mapping.py      RegionMap: draw any collected CSV with GeoPandas
data_get.py     MarineTraffic reports (needs a session; credentials via .env)
json_to_csv.py  JSON -> CSV for any of the above
```

## Credits

Original reports collection by eveie Prewitt.
