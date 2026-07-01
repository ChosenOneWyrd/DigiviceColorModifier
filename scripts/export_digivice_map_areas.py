#!/usr/bin/env python3
import csv
import struct
import sys
from pathlib import Path

TABLE_OFFSET = 0x00094C04
NUM_RECORDS = 184
RECORD_SIZE = 0x38
OUTPUT_CSV = "digivice_map_areas.csv"

COLUMNS = [
    "map_id", "region_id", "area_id", "steps", "encounter_type", "friend_digimon_id",
    "skip1", "skip2", "skip3", "skip4", "skip5", "skip6", "skip7", "skip8", "skip9", "skip10",
    "friend_digimon_id_2", "encounter_type_2", "friend_digimon_id_3", "enemy_stage",
    "battle_type", "shake_mash_follow_win_count", "friend_digimon_id_4", "min_stage_req",
    "unknown1", "unknown2", "unknown3", "skip11",
]

def read_u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]

def main():
    if len(sys.argv) < 2:
        print("Usage: python export_digivice_map_areas.py Digivice.bin [output_csv]")
        return
    rom_path = Path(sys.argv[1])
    output_csv = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path(OUTPUT_CSV)
    rom = rom_path.read_bytes()
    records = []
    for i in range(NUM_RECORDS):
        offset = TABLE_OFFSET + (i * RECORD_SIZE)
        if offset + RECORD_SIZE > len(rom):
            break
        entry = rom[offset:offset + RECORD_SIZE]
        records.append([read_u16(entry, j) for j in range(0, RECORD_SIZE, 2)])
    rows = []
    previous_ids = [0, 0, 0]
    for values in records:
        body = values[:-3]
        next_ids = values[-3:]
        rows.append(previous_ids + body)
        previous_ids = next_ids
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    print(f"Exported {len(rows)} rows to {output_csv}")

if __name__ == "__main__":
    main()
