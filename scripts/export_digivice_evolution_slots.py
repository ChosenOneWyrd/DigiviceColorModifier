#!/usr/bin/env python3
"""
export_digivice_evolution_slots.py

Export Digivice -25th Color Evolution- complete partner evolution-line
membership/order to CSV.

CSV:
    line_id,line_name,slot_1,...,slot_10

The Digivice stores the complete line assignment directly in the 37-record
Partner Table:

    Partner Table base : 0x000946D4
    record size        : 0x1A bytes (13 uint16 words)
    record count       : 37
    digimon_id         : record + 0x00
    line / slot_type   : record + 0x02

The exporter reads the current line/slot_type from every Partner Table record,
then groups records by line_id while preserving physical Partner Table order.
It does NOT assume the stock line lengths, so a later modified BIN can also be
exported as long as:
  - digimon_id records remain 0..36 in order,
  - line_id remains 0..9,
  - no line grows beyond the 10-slot CSV format.

Known Digivice line IDs:
    0 Agumon Line
    1 Gabumon Line
    2 Piyomon Line
    3 Tentomon Line
    4 Palmon Line
    5 Gomamon Line
    6 Patamon Line
    7 Tailmon Line
    8 Wizarmon Line
    9 V-dramon Line

Usage:
    python export_digivice_evolution_slots.py \
        Digivice.bin digivice_evolution_slots.csv
"""

import argparse
import csv
import struct
from pathlib import Path

GP_HEADER = b"GP-SPIF-HEADER"

PARTNER_TABLE_OFFSET = 0x000946D4
PARTNER_RECORD_SIZE = 0x1A
PARTNER_COUNT = 37

P_DIGIMON_ID_OFFSET = 0x00
P_LINE_ID_OFFSET = 0x02

MAX_CSV_SLOTS = 10

LINE_NAMES = {
    0: "Agumon Line",
    1: "Gabumon Line",
    2: "Piyomon Line",
    3: "Tentomon Line",
    4: "Palmon Line",
    5: "Gomamon Line",
    6: "Patamon Line",
    7: "Tailmon Line",
    8: "Wizarmon Line",
    9: "V-dramon Line",
}

HEADERS = (
    ["line_id", "line_name"]
    + [f"slot_{i}" for i in range(1, MAX_CSV_SLOTS + 1)]
)


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def validate_and_read_lines(data):
    if not data.startswith(GP_HEADER):
        raise RuntimeError(
            "Input does not look like a compatible GP-SPIF Digivice BIN."
        )

    table_end = PARTNER_TABLE_OFFSET + PARTNER_COUNT * PARTNER_RECORD_SIZE
    if table_end > len(data):
        raise RuntimeError(
            "BIN is too small for the known Digivice Partner Table layout."
        )

    lines = {line_id: [] for line_id in LINE_NAMES}

    for digimon_id in range(PARTNER_COUNT):
        rec = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE

        stored_id = u16(data, rec + P_DIGIMON_ID_OFFSET)
        if stored_id != digimon_id:
            raise RuntimeError(
                "Partner Table identity validation failed at "
                f"record {digimon_id}: expected digimon_id {digimon_id}, "
                f"found {stored_id}."
            )

        line_id = u16(data, rec + P_LINE_ID_OFFSET)
        if line_id not in LINE_NAMES:
            raise RuntimeError(
                f"Partner Digimon ID {digimon_id} has unsupported "
                f"line/slot_type {line_id}; expected 0..9."
            )

        lines[line_id].append(digimon_id)

    used = [
        digimon_id
        for line_id in range(10)
        for digimon_id in lines[line_id]
    ]

    if len(used) != PARTNER_COUNT or set(used) != set(range(PARTNER_COUNT)):
        raise RuntimeError(
            "Partner line grouping did not cover Digimon IDs 0..36 exactly once."
        )

    for line_id in range(10):
        if not lines[line_id]:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} is empty; this exporter expects all "
                "10 known partner lines to remain represented."
            )

        if len(lines[line_id]) > MAX_CSV_SLOTS:
            raise RuntimeError(
                f"{LINE_NAMES[line_id]} contains {len(lines[line_id])} Digimon, "
                f"but the CSV format supports only {MAX_CSV_SLOTS} slots."
            )

    return lines


def export_csv(bin_path, csv_path):
    data = Path(bin_path).read_bytes()
    lines = validate_and_read_lines(data)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()

        for line_id in range(10):
            row = {
                "line_id": line_id,
                "line_name": LINE_NAMES[line_id],
            }

            members = lines[line_id]
            for i in range(MAX_CSV_SLOTS):
                row[f"slot_{i + 1}"] = members[i] if i < len(members) else ""

            writer.writerow(row)

    print(f"[DONE] Exported Digivice evolution slots to: {csv_path}")
    print(
        f"[INFO] Partner Table: 0x{PARTNER_TABLE_OFFSET:08X}, "
        f"{PARTNER_COUNT} records x 0x{PARTNER_RECORD_SIZE:X} bytes"
    )

    for line_id in range(10):
        print(f"  {line_id} {LINE_NAMES[line_id]}: {lines[line_id]}")


def main():
    ap = argparse.ArgumentParser(
        description="Export Digivice 25th Color evolution-line slots"
    )
    ap.add_argument("bin", help="Input Digivice.bin")
    ap.add_argument("csv", help="Output CSV")
    args = ap.parse_args()

    export_csv(args.bin, args.csv)


if __name__ == "__main__":
    main()
