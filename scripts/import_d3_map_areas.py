#!/usr/bin/env python3
import csv
import struct
import sys
from pathlib import Path

TABLE_OFFSET = 0x9E3B6
RECORD_SIZE = 0x34
NUM_RECORDS = 225
EXPECTED_COLUMNS = 26

def read_u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]

def write_u16(buf, offset, value):
    struct.pack_into("<H", buf, offset, int(value) & 0xFFFF)

def main():
    if len(sys.argv) < 4:
        print("Usage: python import_d3_map_areas.py D3.bin d3_map_areas.csv D3_modified.bin")
        return
    rom_path = Path(sys.argv[1])
    csv_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    rom = bytearray(rom_path.read_bytes())
    records = []
    for i in range(NUM_RECORDS):
        offset = TABLE_OFFSET + (i * RECORD_SIZE)
        records.append([read_u16(rom, offset + j) for j in range(0, RECORD_SIZE, 2)])
    csv_rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            values = [int(str(x).strip(), 0) for x in row]
            if len(values) != EXPECTED_COLUMNS:
                raise ValueError(f"Line {line_no}: expected {EXPECTED_COLUMNS} columns, got {len(values)}")
            csv_rows.append(values)
    expected_rows = NUM_RECORDS - 1
    if len(csv_rows) != expected_rows:
        raise ValueError(f"Expected {expected_rows} rows but found {len(csv_rows)}")
    for i, row in enumerate(csv_rows):
        records[i][23] = row[0]
        records[i][24] = row[1]
        records[i][25] = row[2]
        for j in range(23):
            records[i + 1][j] = row[j + 3]
    for i, values in enumerate(records):
        record_offset = TABLE_OFFSET + (i * RECORD_SIZE)
        for j, value in enumerate(values):
            write_u16(rom, record_offset + (j * 2), value)
    output_path.write_bytes(rom)
    print("Wrote modified ROM:")
    print(f"  {output_path}")

if __name__ == "__main__":
    main()
