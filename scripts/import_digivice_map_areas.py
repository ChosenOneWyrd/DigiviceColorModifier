#!/usr/bin/env python3
# python import_digivice_map_areas.py Digivice.bin digivice_map_areas.csv Digivice_modified.bin

import csv
import struct
import sys
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

TABLE_OFFSET = 0x00094C04
NUM_RECORDS = 184
RECORD_SIZE = 0x38

# ============================================================

def read_u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def write_u16(buf, offset, value):
    struct.pack_into("<H", buf, offset, value & 0xFFFF)

# ============================================================

def main():

    if len(sys.argv) < 4:
        print("Usage:")
        print(
            "  python import_digivice_map_areas.py "
            "Digivice.bin digivice_map_areas.csv Digivice_modified.bin"
        )
        return

    rom_path = Path(sys.argv[1])
    csv_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    rom = bytearray(rom_path.read_bytes())

    # --------------------------------------------------------
    # Read existing records from ROM
    # --------------------------------------------------------

    records = []

    for i in range(NUM_RECORDS):

        offset = TABLE_OFFSET + (i * RECORD_SIZE)

        values = [
            read_u16(rom, offset + j)
            for j in range(0, RECORD_SIZE, 2)
        ]

        records.append(values)

    # --------------------------------------------------------
    # Read CSV
    # --------------------------------------------------------

    csv_rows = []

    with open(csv_path, newline="", encoding="utf-8") as f:

        reader = csv.reader(f)

        header = next(reader)

        for row in reader:

            if not row:
                continue

            values = [int(x) for x in row]

            if len(values) != 28:
                raise ValueError(
                    f"Expected 28 columns, got {len(values)}"
                )

            csv_rows.append(values)

    if len(csv_rows) != NUM_RECORDS:
        raise ValueError(
            f"Expected {NUM_RECORDS} rows but found {len(csv_rows)}"
        )

    # --------------------------------------------------------
    # Apply CSV body data
    # --------------------------------------------------------

    for i, row in enumerate(csv_rows):

        # logical row:
        #
        # [prev_region][prev_area][prev_map]
        # [body(25)]
        #
        # body begins at column 3

        body = row[3:]

        if len(body) != 25:
            raise ValueError(
                f"Row {i}: expected body length 25"
            )

        records[i][:25] = body

    # --------------------------------------------------------
    # Rebuild next-region/area/map links
    # --------------------------------------------------------

    for i in range(NUM_RECORDS - 1):

        next_row = csv_rows[i + 1]

        next_region = next_row[0]
        next_area = next_row[1]
        next_map = next_row[2]

        records[i][25] = next_region
        records[i][26] = next_area
        records[i][27] = next_map

    # --------------------------------------------------------
    # Leave last record's link fields unchanged
    # --------------------------------------------------------

    # records[-1][25:28] preserved from original ROM

    # --------------------------------------------------------
    # Write records back
    # --------------------------------------------------------

    for i, values in enumerate(records):

        record_offset = TABLE_OFFSET + (i * RECORD_SIZE)

        for j, value in enumerate(values):

            write_u16(
                rom,
                record_offset + (j * 2),
                value
            )

    output_path.write_bytes(rom)

    print("Wrote modified ROM:")
    print(f"  {output_path}")

if __name__ == "__main__":
    main()