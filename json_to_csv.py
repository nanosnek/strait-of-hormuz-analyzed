import csv
import json

with open("test.json", "r", encoding="utf-8") as j_file:
    parsed_data = json.load(j_file)

ship_data = parsed_data["data"]

if ship_data:
    with open("output.csv", "w", newline="", encoding="utf-8") as c_file:
        headers = ship_data[0].keys()
        writer = csv.DictWriter(c_file, fieldnames=headers)
        writer.writeheader()  # write headers
        writer.writerows(ship_data)
    print("Data written to output.csv")
else:
    print("Error: No data to write")
