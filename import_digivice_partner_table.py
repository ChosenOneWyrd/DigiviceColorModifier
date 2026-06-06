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

def fail(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)

def parse_u16(value, field_name, row_num):
    try:
        n = int(str(value).strip(), 0)
    except Exception:
        fail(f"Row {row_num}: {field_name}='{value}' is not a valid integer.")

    if not (0 <= n <= 65535):
        fail(f"Row {row_num}: {field_name}={n} outside uint16 range 0-65535.")

    return n

def main():
    if len(sys.argv) >= 4:
        bin_in = sys.argv[1]
        csv_in = sys.argv[2]
        bin_out = sys.argv[3]
    else:
        bin_in = "Digivice.bin"
        csv_in = "digivice_partner_table.csv"
        bin_out = "Digivice.bin"

    data = bytearray(Path(bin_in).read_bytes())

    with open(csv_in, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if len(rows) != RECORD_COUNT:
        fail(f"CSV has {len(rows)} rows, expected {RECORD_COUNT} rows.")

    for row_index, row in enumerate(rows):
        row_num = row_index + 2
        off = TABLE_START + row_index * RECORD_SIZE

        values = [
            parse_u16(row.get(field, ""), field, row_num)
            for field in FIELD_NAMES
        ]

        struct.pack_into("<13H", data, off, *values)

    Path(bin_out).write_bytes(data)

    print(f"[DONE] Imported {len(rows)} records into {bin_out}")
    print(f"Table start: 0x{TABLE_START:08X}")
    print(f"Table end:   0x{TABLE_START + RECORD_COUNT * RECORD_SIZE:08X}")

if __name__ == "__main__":
    main()