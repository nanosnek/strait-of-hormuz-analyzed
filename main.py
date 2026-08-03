"""
Command line entry point for Strait of Hormuz vessel traffic.

Global Fishing Watch is the main source -- documented, free, and with history
back to 2012. It runs about 72 hours behind real time, which is fine for
analysis:

    python main.py presence --days 90              # traffic volume, day by day
    python main.py presence --days 365 --group-by FLAG
    python main.py events --type encounter --days 90    # possible transfers
    python main.py events --type gap --days 90          # vessels going dark

MarineTraffic gives a live snapshot instead, for when "right now" is the
question. It has no history at all -- you only get what you collect from the
moment you start:

    python main.py snapshot                        # tankers in the strait now
    python main.py watch --interval 900            # keep taking readings

    python main.py regions                         # list the areas

Both write JSON and CSV. See README.md for how the two sources differ.
"""

import argparse
import datetime
import json
import os
import sys
import time

import regions
from json_to_csv import write_csv


def timestamp():
    """
    Build a filename-safe UTC timestamp for the current moment.

    Colons aren't safe in filenames, so this is the compact ISO form rather
    than the one stored inside records.

    Returns:
        str: The current UTC time, e.g. "20260802T060221Z".
    """
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def date_window(days, latency_days):
    """
    Work out the most recent date range the API actually has data for.

    Asking Global Fishing Watch for today returns nothing, because AIS lands
    around three days behind, so the window ends in the past.

    Args:
        days (int): How long the window should be, in days.
        latency_days (int): How far in the past the window should end, to stay
            behind the feed's delay.

    Returns:
        tuple[str, str]: The (start, end) dates as "YYYY-MM-DD" strings.
    """
    end = datetime.date.today() - datetime.timedelta(days=latency_days)
    return str(end - datetime.timedelta(days=days)), str(end)


def write(records, json_path, csv_path):
    """
    Write records to JSON, and optionally to CSV alongside.

    Creates the parent directory if it doesn't exist.

    Args:
        records (list[dict]): The records to write.
        json_path (str): Path of the JSON file to create.
        csv_path (str | None): Path of the CSV file, or None to skip CSV.

    Returns:
        None

    Raises:
        SystemExit: If records is empty, since an empty result usually means
            the query was wrong rather than that the sea was.
    """
    if not records:
        sys.exit("no records returned - try a wider date range or a larger region")

    directory = os.path.dirname(json_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2, default=str)
    print(f"wrote {len(records)} records -> {json_path}", file=sys.stderr)

    if csv_path:
        write_csv(records, csv_path)
        print(f"wrote {len(records)} records -> {csv_path}", file=sys.stderr)


def output_paths(args, stem):
    """
    Decide where a command's output should go.

    Args:
        args (argparse.Namespace): Parsed options, using .out and .no_csv.
        stem (str): Default filename without extension, used when --out
            wasn't given.

    Returns:
        tuple[str, str | None]: The (json_path, csv_path). The CSV path is the
            JSON path with a .csv extension, or None if --no-csv was passed.
    """
    json_path = args.out or f"{stem}.json"
    csv_path = None if args.no_csv else os.path.splitext(json_path)[0] + ".csv"
    return json_path, csv_path


# --------------------------------------------------------------------------
# Global Fishing Watch (main source)
# --------------------------------------------------------------------------

def cmd_presence(args):
    """
    Run the `presence` subcommand: traffic volume over time.

    Args:
        args (argparse.Namespace): Parsed options -- region, days,
            temporal_resolution, group_by, all_types, out, no_csv.

    Returns:
        None
    """
    import gfw  # imported here so the live-map commands don't need the GFW client

    start, end = date_window(args.days, gfw.LATENCY_DAYS)
    print(f"presence in {args.region}, {start} to {end}", file=sys.stderr)

    records = gfw.presence(
        regions.REGIONS[args.region], start, end,
        temporal_resolution=args.temporal_resolution,
        group_by=args.group_by,
        vessel_types=None if args.all_types else gfw.COMMERCIAL,
        spatial_aggregation=not args.grid,
    )
    write(records, *output_paths(args, f"presence_{args.region}_{start}_{end}"))


def cmd_events(args):
    """
    Run the `events` subcommand: encounters, loitering, gaps, port visits.

    Args:
        args (argparse.Namespace): Parsed options -- region, type, days,
            limit, all_types, out, no_csv.

    Returns:
        None
    """
    import gfw

    start, end = date_window(args.days, gfw.LATENCY_DAYS)
    print(f"{args.type} events in {args.region}, {start} to {end}", file=sys.stderr)

    records = gfw.events(
        regions.REGIONS[args.region], start, end,
        event_type=args.type,
        vessel_types=None if args.all_types else gfw.COMMERCIAL,
        limit=args.limit,
    )
    write(records, *output_paths(args, f"{args.type}_{args.region}_{start}_{end}"))


# --------------------------------------------------------------------------
# MarineTraffic live map
# --------------------------------------------------------------------------

def live_reading(args):
    """
    Take one live reading from the MarineTraffic map.

    Shared by the snapshot and watch subcommands.

    Args:
        args (argparse.Namespace): Parsed options -- region, all_types,
            keep_small_craft, zoom, delay, quiet.

    Returns:
        list[dict]: Decoded vessels currently in the region.
    """
    import livemap  # imported here so the GFW commands don't need curl_cffi

    bbox = regions.REGIONS[args.region]
    hide = None if args.keep_small_craft else livemap.DEFAULT_HIDE_TYPES
    fetch = livemap.fetch_region if args.all_types else livemap.fetch_tankers
    return fetch(bbox, zoom=args.zoom, delay=args.delay,
                 hide_types=hide, verbose=not args.quiet)


def cmd_snapshot(args):
    """
    Run the `snapshot` subcommand: one live reading, written to disk.

    Args:
        args (argparse.Namespace): Parsed options, as for live_reading plus
            out and no_csv.

    Returns:
        None
    """
    vessels = live_reading(args)
    write(vessels, *output_paths(args, f"snapshot_{args.region}_{timestamp()}"))

    moving = [v for v in vessels if (v["speed_knots"] or 0) >= 0.5]
    print(f"\n{len(vessels)} vessels, {len(moving)} under way "
          f"({len(vessels) - len(moving)} stopped or anchored)", file=sys.stderr)


def cmd_watch(args):
    """
    Run the `watch` subcommand: readings on a loop, one file each.

    Runs until interrupted. A failed reading is reported and skipped rather
    than ending a series that may already be hours long.

    Args:
        args (argparse.Namespace): Parsed options, as for live_reading plus
            interval, out_dir and no_csv.

    Returns:
        None
    """
    os.makedirs(args.out_dir, exist_ok=True)
    print(f"polling every {args.interval}s, writing to {args.out_dir}/ "
          f"(ctrl-c to stop)", file=sys.stderr)

    reading = 0
    while True:
        reading += 1
        stamp = timestamp()
        print(f"\n--- reading {reading} at {stamp} ---", file=sys.stderr)
        try:
            vessels = live_reading(args)
        except Exception as error:
            # Don't let one bad reading end a series that's hours long.
            print(f"  ! reading failed: {error!r}", file=sys.stderr)
            vessels = []

        if vessels:
            base = os.path.join(args.out_dir, f"hormuz_{args.region}_{stamp}")
            write(vessels, base + ".json", None if args.no_csv else base + ".csv")

        time.sleep(args.interval)


def cmd_regions(args):
    """
    Run the `regions` subcommand: print the available areas and their bounds.

    Args:
        args (argparse.Namespace): Parsed options. Unused, but accepted so
            every subcommand has the same signature.

    Returns:
        None
    """
    print("Available regions:\n")
    for name, box in regions.REGIONS.items():
        print(f"  {name:14s} {box.west:6.2f} {box.south:6.2f} .. "
              f"{box.east:6.2f} {box.north:6.2f}")


# --------------------------------------------------------------------------

def add_shared_args(parser):
    """
    Add the options every subcommand takes.

    Args:
        parser (argparse.ArgumentParser): The subcommand parser to add to.

    Returns:
        None
    """
    parser.add_argument("--region", choices=sorted(regions.REGIONS), default="hormuz")
    parser.add_argument("--all-types", action="store_true",
                        help="every vessel type, not just cargo/tanker traffic")
    parser.add_argument("-o", "--out", help="output .json path")
    parser.add_argument("--no-csv", action="store_true")


def add_live_args(parser):
    """
    Add the options specific to the MarineTraffic live-map subcommands.

    Args:
        parser (argparse.ArgumentParser): The subcommand parser to add to.

    Returns:
        None
    """
    add_shared_args(parser)
    parser.add_argument("--zoom", type=int, default=9,
                        help="tile zoom 3-12. Low zoom is truncated server-side; "
                             "raise for completeness. Default 9.")
    parser.add_argument("--keep-small-craft", action="store_true",
                        help="also keep nav aids, fishing boats and pleasure craft")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="seconds between requests (be polite)")
    parser.add_argument("-q", "--quiet", action="store_true")


def build_parser():
    """
    Build the command line parser and its subcommands.

    Each subcommand sets func to its handler, so __main__ can dispatch without
    knowing the commands.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    presence = sub.add_parser("presence", help="[GFW] traffic volume over time")
    add_shared_args(presence)
    presence.add_argument("--days", type=int, default=90,
                          help="how far back to look, up to 366. Default 90.")
    presence.add_argument("--grid", action="store_true",
                          help="one row per map cell with lat/lon, instead of "
                               "one total per time bucket. Use for mapping.")
    presence.add_argument("--temporal-resolution", default="DAILY",
                          choices=["HOURLY", "DAILY", "MONTHLY", "YEARLY", "ENTIRE"])
    presence.add_argument("--group-by", default="FLAG",
                          choices=["VESSEL_ID", "FLAG", "GEARTYPE", "FLAGANDGEARTYPE", "MMSI"],
                          help="break the totals down by this field. The API "
                               "requires one; sum across groups for a total. "
                               "Default FLAG.")
    presence.set_defaults(func=cmd_presence)

    events = sub.add_parser("events", help="[GFW] encounters, loitering, gaps, port visits")
    add_shared_args(events)
    events.add_argument("--type", default="encounter",
                        choices=["encounter", "loitering", "port_visit", "gap", "fishing"])
    events.add_argument("--days", type=int, default=90)
    events.add_argument("--limit", type=int, default=1000)
    events.set_defaults(func=cmd_events)

    snapshot = sub.add_parser("snapshot", help="[MarineTraffic] one live reading")
    add_live_args(snapshot)
    snapshot.set_defaults(func=cmd_snapshot)

    watch = sub.add_parser("watch", help="[MarineTraffic] repeated live readings")
    add_live_args(watch)
    watch.add_argument("--interval", type=int, default=900, help="seconds, default 900")
    watch.add_argument("--out-dir", default="data")
    watch.set_defaults(func=cmd_watch)

    listing = sub.add_parser("regions", help="list the areas")
    listing.set_defaults(func=cmd_regions)

    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    try:
        arguments.func(arguments)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
