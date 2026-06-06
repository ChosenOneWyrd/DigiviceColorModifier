#!/usr/bin/env python3
import struct
import csv
import sys
from pathlib import Path

META_OFFSET = 0x0009DEE6
DATA_OFFSET = META_OFFSET + 4
RECORD_SIZE = 12
NUM_RECORDS = 91


def parse_int(x):
    x = str(x).strip()
    if x.lower().startswith("0x"):
        return int(x, 16)
    return int(x)


def main():
    if len(sys.argv) < 4:
        print("Usage: python import_d3_friend_table.py in.bin csv out.bin")
        return

    in_bin = sys.argv[1]
    csv_path = sys.argv[2]
    out_bin = sys.argv[3]

    data = bytearray(Path(in_bin).read_bytes())

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if len(rows) != NUM_RECORDS:
        raise RuntimeError(f"CSV row count {len(rows)} != expected {NUM_RECORDS}")

    for i, row in enumerate(rows):
        off = DATA_OFFSET + i * RECORD_SIZE

        if off + RECORD_SIZE > len(data):
            raise RuntimeError(f"Write out of bounds at record {i}")

        try:
            digimon_id = parse_int(row["digimon_id"])
            string_index = parse_int(row["string_index"])
            sprite_index = parse_int(row["sprite_index"])
            attack_sprite = parse_int(row["attack_shot_sprite_index"])
            attack_sound = parse_int(row["attack_shot_sound_id"])
            unknown = parse_int(row["unknown"])
        except Exception as e:
            raise RuntimeError(f"Row {i} parse error: {e}")

        struct.pack_into(
            "<6H",
            data,
            off,
            digimon_id,
            string_index,
            sprite_index,
            attack_sprite,
            attack_sound,
            unknown,
        )

    Path(out_bin).write_bytes(data)

    print(f"[+] Imported {len(rows)} records into {out_bin}")


if __name__ == "__main__":
    main()