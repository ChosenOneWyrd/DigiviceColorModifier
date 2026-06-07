#!/usr/bin/env python3

import csv
import struct
import sys
from pathlib import Path

MAX_POWER = 255
TABLE_START = 0x00097F2C
RECORD_SIZE = 10
EXPECTED_RECORDS = 112

FIELDS = [
    "digimon_id",
    "string_index",
    "sprite_index",
    "unknown",
    "power",
]


def parse_u16(value, field_name, row_num):
    try:
        value = int(str(value).strip(), 0)
    except Exception:
        raise ValueError(
            f"Row {row_num}, column {field_name}: "
            f"'{value}' is not a valid integer"
        )

    if not (0 <= value <= 65535):
        raise ValueError(
            f"Row {row_num}, column {field_name}: "
            f"value out of uint16 range: {value}"
        )

    return value


def main():

    if len(sys.argv) >= 4:
        bin_in = sys.argv[1]
        csv_in = sys.argv[2]
        bin_out = sys.argv[3]
    else:
        bin_in = "Digivice.bin"
        csv_in = "digivice_link_battle_table.csv"
        bin_out = "Digivice.bin"

    data = bytearray(Path(bin_in).read_bytes())

    with open(csv_in, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    missing = [h for h in FIELDS if h not in reader.fieldnames]
    if missing:
        raise RuntimeError(f"CSV missing columns: {missing}")

    if len(rows) != EXPECTED_RECORDS:
        raise RuntimeError(
            f"Expected {EXPECTED_RECORDS} records but CSV contains {len(rows)}."
        )

    for i, row in enumerate(rows):
        offset = TABLE_START + i * RECORD_SIZE

        values = []

        for h in FIELDS:
            value = parse_u16(row[h], h, i + 1)

            # Specific constraint for power
            if h == "power" and value > MAX_POWER:
                raise ValueError(
                    f"Row {i + 1}, column power: {value} exceeds MAX_POWER ({MAX_POWER})"
                )

            values.append(value)

        struct.pack_into("<5H", data, offset, *values)

    Path(bin_out).write_bytes(data)

    print(f"Imported {len(rows)} records")
    print(
        f"Table range: "
        f"0x{TABLE_START:X} - "
        f"0x{TABLE_START + EXPECTED_RECORDS * RECORD_SIZE:X}"
    )
    print(f"Wrote: {bin_out}")


if __name__ == "__main__":
    main()