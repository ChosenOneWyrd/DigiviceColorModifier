import csv, sys
import struct
from pathlib import Path

if len(sys.argv) >= 4:
    BIN_IN = sys.argv[1]
    CSV_IN = sys.argv[2]
    BIN_OUT = sys.argv[3]
else:
    BIN_IN = "D3.bin"
    CSV_IN = "d3_link_battle_table.csv"
    BIN_OUT = "D3.bin"

MAX_POWER = 255
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

data = bytearray(Path(BIN_IN).read_bytes())

# Count existing records until sentinel
existing_count = 0
off = TABLE_START

while True:
    first = struct.unpack_from("<H", data, off)[0]
    if first == SENTINEL:
        break
    existing_count += 1
    off += RECORD_SIZE

sentinel_offset = off

with open(CSV_IN, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

missing = [h for h in HEADERS if h not in reader.fieldnames]
if missing:
    raise RuntimeError(f"CSV missing columns: {missing}")

if len(rows) != existing_count:
    raise RuntimeError(
        f"CSV has {len(rows)} records, but BIN table has {existing_count}. "
        "Do not add/remove rows; only edit values."
    )

for i, row in enumerate(rows):
    off = TABLE_START + i * RECORD_SIZE

    values = []
    for h in HEADERS:
        value = int(row[h])

        # Generic uint16 validation
        if not (0 <= value <= 65535):
            raise ValueError(f"Row {i + 1}, column {h}: value out of uint16 range: {value}")

        # 🔴 Specific constraint for power
        if h == "power" and value > MAX_POWER:
            raise ValueError(
                f"Row {i + 1}, column power: {value} exceeds MAX_POWER ({MAX_POWER})"
            )

        values.append(value)

    struct.pack_into("<5H", data, off, *values)

# Safety check: sentinel must still be untouched
sentinel = struct.unpack_from("<H", data, sentinel_offset)[0]
if sentinel != SENTINEL:
    raise RuntimeError(
        f"Sentinel corrupted at 0x{sentinel_offset:X}: expected {SENTINEL}, found {sentinel}"
    )

Path(BIN_OUT).write_bytes(data)

print(f"Imported {len(rows)} records")
print(f"Preserved sentinel at 0x{sentinel_offset:X}")
print(f"Wrote: {BIN_OUT}")