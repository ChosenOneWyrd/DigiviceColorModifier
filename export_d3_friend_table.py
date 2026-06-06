#!/usr/bin/env python3
import struct
import csv
import sys
from pathlib import Path

META_OFFSET = 0x0009DEE6
DATA_OFFSET = META_OFFSET + 4
RECORD_SIZE = 12
NUM_RECORDS = 91

def main():
    if len(sys.argv) < 3:
        print("Usage: python export_d3_friend_table.py D3.bin out.csv")
        return

    bin_path = sys.argv[1]
    out_csv = sys.argv[2]

    data = Path(bin_path).read_bytes()

    rows = []

    for i in range(NUM_RECORDS):
        off = DATA_OFFSET + i * RECORD_SIZE
        vals = struct.unpack_from("<6H", data, off)

        row = {
            "meta_offset": f"0x{META_OFFSET:08X}",
            "data_offset": f"0x{off:08X}",
            "digimon_id": vals[0],
            "string_index": vals[1],
            "sprite_index": vals[2],
            "attack_shot_sprite_index": vals[3],
            "attack_shot_sound_id": vals[4],
            "unknown": vals[5],
        }

        rows.append(row)

    fieldnames = [
        "meta_offset",
        "data_offset",
        "digimon_id",
        "string_index",
        "sprite_index",
        "attack_shot_sprite_index",
        "attack_shot_sound_id",
        "unknown",
    ]

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[+] Exported {len(rows)} records to {out_csv}")

if __name__ == "__main__":
    main()