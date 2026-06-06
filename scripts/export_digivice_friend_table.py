# export_digivice_friend_table.py
#!/usr/bin/env python3
import struct
import csv
import sys
from pathlib import Path

DATA_OFFSET = 0x0009418E
RECORD_SIZE = 12
NUM_RECORDS = 82

def main():
    if len(sys.argv) < 3:
        print("Usage: python export_digivice_friend_table.py Digivice.bin digivice_friend_table.csv")
        return

    bin_path = sys.argv[1]
    out_csv = sys.argv[2]

    data = Path(bin_path).read_bytes()
    rows = []

    for i in range(NUM_RECORDS):
        off = DATA_OFFSET + i * RECORD_SIZE
        vals = struct.unpack_from("<6H", data, off)

        rows.append({
            "data_offset": f"0x{off:08X}",
            "digimon_id": vals[0],
            "string_index": vals[1],
            "sprite_index": vals[2],
            "attack_shot_sprite_index": vals[3],
            "attack_shot_sound_id": vals[4],
            "unknown": vals[5],
        })

    fieldnames = [
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