import csv
import struct
from pathlib import Path
import sys

if len(sys.argv) >= 3:
    BIN_IN = sys.argv[1]
    CSV_OUT = sys.argv[2]
else:
    # fallback for manual run
    BIN_IN = "D3.bin"
    CSV_OUT = "d3_link_battle_table.csv"

TABLE_START = 0x000A21C8
RECORD_SIZE = 10
SENTINEL = 42989

HEADERS = [
    "digimon_id",
    "string_index",
    "stage",
    "sprite_index",
    "power",
]

data = Path(BIN_IN).read_bytes()

rows = []
off = TABLE_START

while True:
    first = struct.unpack_from("<H", data, off)[0]
    if first == SENTINEL:
        break

    values = struct.unpack_from("<5H", data, off)
    rows.append(dict(zip(HEADERS, values)))

    off += RECORD_SIZE

with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)

print(f"Exported {len(rows)} records")
print(f"Table start: 0x{TABLE_START:X}")
print(f"Table end:   0x{off:X}")
print(f"Wrote: {CSV_OUT}")