#!/usr/bin/env python3
import csv
import struct
import sys
from pathlib import Path

TABLE_START = 0x000946D2
RECORD_SIZE = 26
RECORD_COUNT = 37

FIELD_NAMES = [
    "stage",
    "digimon_id",
    "jogress_win_partner_id",
    "win_requirement_for_next_evo",
    "sprite_index",
    "string_index",
    "evo_animation1_id",
    "evo_animation2_id",
    "attack_voice_sound_id",
    "attack_shot_sprite_index",
    "attack_shot_sound_id",
    "attack_led_color_id",
    "unknown_column",
]

def main():
    if len(sys.argv) >= 3:
        bin_path = sys.argv[1]
        csv_path = sys.argv[2]
    else:
        bin_path = "Digivice.bin"
        csv_path = "digivice_partner_table.csv"

    data = Path(bin_path).read_bytes()
    rows = []

    for row_index in range(RECORD_COUNT):
        off = TABLE_START + row_index * RECORD_SIZE
        values = struct.unpack_from("<13H", data, off)

        row = {
            "offset": f"0x{off:08X}",
        }

        for field, value in zip(FIELD_NAMES, values):
            row[field] = value

        rows.append(row)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["offset"] + FIELD_NAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[DONE] Exported {len(rows)} records to {csv_path}")
    print(f"Table start: 0x{TABLE_START:08X}")
    print(f"Table end:   0x{TABLE_START + RECORD_COUNT * RECORD_SIZE:08X}")

if __name__ == "__main__":
    main()