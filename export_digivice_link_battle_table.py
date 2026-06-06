#!/usr/bin/env python3
import csv
import struct
import sys
from pathlib import Path

TABLE_START = 0x00097F2C
RECORD_SIZE = 10
NUM_RECORDS = 112

HEADERS = [
    "data_offset",
    "digimon_id",
    "string_index",
    "sprite_index",
    "unknown",
    "power",
]

def main():
    if len(sys.argv) >= 3:
        bin_path = sys.argv[1]
        out_csv = sys.argv[2]
    else:
        # fallback for manual run
        bin_path = "Digivice.bin"
        out_csv = "digivice_link_battle_table.csv"

    data = Path(bin_path).read_bytes()
    rows = []

    for i in range(NUM_RECORDS):
        off = TABLE_START + i * RECORD_SIZE
        digimon_id, string_index, sprite_index, unknown, power = struct.unpack_from("<5H", data, off)

        rows.append({
            "data_offset": f"0x{off:08X}",
            "digimon_id": digimon_id,
            "string_index": string_index,
            "sprite_index": sprite_index,
            "unknown": unknown,
            "power": power,
        })

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} records")
    print(f"Table start: 0x{TABLE_START:X}")
    print(f"Table end:   0x{TABLE_START + NUM_RECORDS * RECORD_SIZE:X}")
    print(f"Wrote: {out_csv}")

if __name__ == "__main__":
    main()