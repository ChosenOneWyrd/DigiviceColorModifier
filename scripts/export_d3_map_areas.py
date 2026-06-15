#!/usr/bin/env python3
# python export_d3_map_areas.py D3.bin 

import csv
import struct
import sys
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

TABLE_OFFSET = 0x9E3B6
RECORD_SIZE = 0x34      # 26 u16 values
NUM_RECORDS = 225

OUTPUT_CSV = "d3_map_areas.csv"

# ============================================================
# COLUMN NAMES
# ============================================================

COLUMNS = [
    "area_id",
    "region_id",
    "map_id",
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
    "encounter_type_2",
    "friend_digimon_id_2",
    "enemy_stage",
    "battle_type",
    "shake_mash_follow_win_count",
    "friend_digimon_id_3",
    "min_stage_required",
    "unknown1",
    "unknown2",
    "unknown3",
    "digital_gate_open",
    "boss_cut_scene_id",
]

# ============================================================

def read_u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]

# ============================================================

def main():

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python export_adventure_areas.py D3.bin")
        return

    rom_path = Path(sys.argv[1])

    with open(rom_path, "rb") as f:
        rom = f.read()

    records = []

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

    rows = []

    for i in range(len(records) - 1):

        prev_record = records[i]
        current_record = records[i + 1]

        row = (
            prev_record[-3:] +
            current_record[:23]
        )

        rows.append(row)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:

        writer = csv.writer(f)

        writer.writerow(COLUMNS)

        for row in rows:
            writer.writerow(row)

    print(f"Exported {len(rows)} rows to {OUTPUT_CSV}")

if __name__ == "__main__":
    main()