"""
Flatten a JSON vessel dump to CSV.

Handles both shapes this project produces:
  * a bare list of records (gfw.py, livemap.py, main.py)
  * {"data": [ {...}, ... ]}  (data_get.py)

Records are allowed to have differing keys -- live-map rows omit DWT, ROT and
others when a vessel hasn't reported them -- so the header is the union of
every key seen, and missing values are written empty. The original version
took its header from the first record only, which silently dropped columns.

    python json_to_csv.py                       # test.json -> output.csv
    python json_to_csv.py snapshot.json out.csv
"""

import csv
import json
import sys


def load_records(path):
    """
    Read a JSON file and return the list of records inside it.

    Args:
        path (str): Path to the JSON file.

    Returns:
        list[dict]: The records. A file that is already a JSON array is
            returned as-is; otherwise the "data" key is unwrapped.

    Raises:
        KeyError: If the file is a JSON object with no "data" key.
        json.JSONDecodeError: If the file isn't valid JSON.
        FileNotFoundError: If the path doesn't exist.
    """
    with open(path, "r", encoding="utf-8") as j_file:
        parsed_data = json.load(j_file)

    if isinstance(parsed_data, list):
        return parsed_data
    return parsed_data["data"]


def write_csv(ship_data, path, keep_empty_columns=False):
    """
    Write records to a CSV file.

    The header is the union of every key across every record, in the order
    keys are first seen, so no column is lost when records differ. Records
    missing a column get an empty cell.

    Columns that are empty in *every* record are dropped by default. The GFW
    report response shares one flat schema across all its report types, so a
    given query leaves most columns null -- a plain flag-grouped presence
    report fills 5 of 20. Dropping them loses nothing, since no record had a
    value, and it makes the file readable in a spreadsheet. The JSON written
    alongside always keeps the full schema.

    Args:
        ship_data (list[dict]): The records to write.
        path (str): Path of the CSV file to create, overwriting if it exists.
        keep_empty_columns (bool): Keep columns that are empty in every
            record. Defaults to False.

    Returns:
        int: How many rows were written. Zero if ship_data was empty, in
            which case no file is created and a message goes to stderr.
    """
    if not ship_data:
        print("Error: No data to write", file=sys.stderr)
        return 0

    # Union of keys, in first-seen order, so the common fields lead.
    headers = []
    for record in ship_data:
        for key in record:
            if key not in headers:
                headers.append(key)

    if not keep_empty_columns:
        used = [h for h in headers
                if any(record.get(h) not in (None, "") for record in ship_data)]
        dropped = len(headers) - len(used)
        if dropped and used:
            print(f"  ({dropped} column(s) empty in every row, omitted from CSV)",
                  file=sys.stderr)
            headers = used

    with open(path, "w", newline="", encoding="utf-8") as c_file:
        writer = csv.DictWriter(c_file, fieldnames=headers, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ship_data)
    return len(ship_data)


def main():
    """
    Convert a JSON file to CSV, using paths from the command line.

    Reads sys.argv for an input and an output path, defaulting to test.json
    and output.csv.

    Returns:
        None
    """
    source = sys.argv[1] if len(sys.argv) > 1 else "test.json"
    target = sys.argv[2] if len(sys.argv) > 2 else "output.csv"

    ship_data = load_records(source)
    written = write_csv(ship_data, target)
    if written:
        print(f"Data written to {target} ({written} rows)")


if __name__ == "__main__":
    main()
