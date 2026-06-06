#!/usr/bin/env python3

import csv
import struct
import sys
from pathlib import Path

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


def fail(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


def parse_u16(value, field_name, row_num):
    try:
        value = int(str(value).strip(), 0)
    except Exception:
        fail(
            f"Row {row_num}: "
            f"{field_name}='{value}' is not a valid integer."
        )

    if not (0 <= value <= 65535):
        fail(
            f"Row {row_num}: "
            f"{field_name}={value} outside uint16 range (0-65535)."
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
        rows = list(csv.DictReader(f))

    if len(rows) != EXPECTED_RECORDS:
        fail(
            f"Expected {EXPECTED_RECORDS} records "
            f"but CSV contains {len(rows)}."
        )

    for row_num, row in enumerate(rows, start=1):

        values = []

        for field in FIELDS:

            if field not in row:
                fail(
                    f"CSV missing required column '{field}'."
                )

            values.append(
                parse_u16(
                    row[field],
                    field,
                    row_num,
                )
            )

        offset = TABLE_START + (row_num - 1) * RECORD_SIZE

        struct.pack_into(
            "<5H",
            data,
            offset,
            *values,
        )

    Path(bin_out).write_bytes(data)

    print(
        f"Imported {EXPECTED_RECORDS} records "
        f"into {bin_out}"
    )

    print(
        f"Table range: "
        f"0x{TABLE_START:X} - "
        f"0x{TABLE_START + EXPECTED_RECORDS * RECORD_SIZE:X}"
    )


if __name__ == "__main__":
    main()