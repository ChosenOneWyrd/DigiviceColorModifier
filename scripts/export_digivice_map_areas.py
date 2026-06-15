#!/usr/bin/env python3
# python export_digivice_map_areas.py D3.bin 

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

OUTPUT_CSV = "digivice_map_areas.csv"

# ============================================================
# COLUMN NAMES
# ============================================================

COLUMNS = [
    "map_id",
    "region_id",
    "area_id",

    "steps",
    "encounter_type",
    "friend_digimon_id",

    "skip1",
    "skip2",
    "skip3",
    "skip4",
    "skip5",
    "skip6",
    "skip7",
    "skip8",
    "skip9",
    "skip10",

    "friend_digimon_id_2",
    "encounter_type_2",
    "friend_digimon_id_3",

    "enemy_stage",
    "battle_type",
    "shake_mash_follow_win_count",

    "friend_digimon_id_4",

    "min_stage_required",

    "unknown1",
    "unknown2",
    "unknown3",

    "skip11",
]

# ============================================================

def read_u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]

# ============================================================

def main():

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python export_digivice_map_areas.py Digivice.bin")
        return

    rom_path = Path(sys.argv[1])

    with open(rom_path, "rb") as f:
        rom = f.read()

    records = []

    # --------------------------------------------------------
    # Read physical records
    # --------------------------------------------------------

    for i in range(NUM_RECORDS):

        offset = TABLE_OFFSET + (i * RECORD_SIZE)

        if offset + RECORD_SIZE > len(rom):
            break

        entry = rom[offset:offset + RECORD_SIZE]

        values = [
            read_u16(entry, j)
            for j in range(0, RECORD_SIZE, 2)
        ]

        records.append(values)

    # --------------------------------------------------------
    # Build logical rows
    #
    # Physical:
    #   [ body(23) ][ next_region ][ next_area ][ next_map ]
    #
    # Logical:
    #   [ prev_region ][ prev_area ][ prev_map ]
    #   [ body(25) ]
    # --------------------------------------------------------

    rows = []

    previous_ids = [0, 0, 0]

    for values in records:

        body = values[:-3]
        next_ids = values[-3:]

        logical_row = previous_ids + body

        rows.append(logical_row)

        previous_ids = next_ids

    # --------------------------------------------------------
    # Export CSV
    # --------------------------------------------------------

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow(COLUMNS)

        for row in rows:
            writer.writerow(row)

    print(f"Exported {len(rows)} rows to {OUTPUT_CSV}")

if __name__ == "__main__":
    main()