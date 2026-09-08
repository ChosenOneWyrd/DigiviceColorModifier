#!/usr/bin/env python3
"""
export_d_ark_evolution_slots.py

Export D-Ark 25th Color Database_Evolution line arrays to CSV.

CSV:
    line_id,line_name,line_offset,slot_1,...,slot_10

The exporter also validates that the Partner Table slot_type_id field agrees
with Database_Evolution membership for every Digimon ID 0..20.

Usage:
    python export_d_ark_evolution_slots.py \
        D-Ark.bin d_ark_evolution_slots.csv

Known layout:
    0x000D7584  Guilmon line
    0x000D7598  Terriermon line
    0x000D75AC  Renamon line
    0x000D75C0  Impmon line
    0x000D75D4  Cyberdramon line
    0x000D75E8  flattened 21-Digimon order

Partner Table:
    base          0x000D6ACA
    record size   0x5E
    count         21
    digimon_id    record + 0x02
    slot_type_id  record + 0x04
"""

import csv
import struct
import sys
from pathlib import Path

LINE_DEFS = [
    (0, "Guilmon",      0x000D7584),
    (1, "Terriermon",   0x000D7598),
    (2, "Renamon",      0x000D75AC),
    (3, "Impmon",       0x000D75C0),
    (4, "Cyberdramon",  0x000D75D4),
]

FLAT_ORDER_OFFSET = 0x000D75E8
SLOTS_PER_LINE = 10
PARTNER_IDS = set(range(21))
GP_HEADER = b"GP-SPIF-HEADER"

PARTNER_TABLE_START = 0x000D6ACA
PARTNER_RECORD_SIZE = 0x5E
PARTNER_COUNT = 21
PARTNER_DIGIMON_ID_OFFSET = 0x02
PARTNER_SLOT_TYPE_ID_OFFSET = 0x04

HEADERS = (
    ["line_id", "line_name", "line_offset"]
    + [f"slot_{i}" for i in range(1, SLOTS_PER_LINE + 1)]
)


def read_u16s(data, off, count):
    return struct.unpack_from("<" + "H" * count, data, off)


def build_digimon_to_line(data):
    digimon_to_line = {}
    seen = []

    for line_id, _name, off in LINE_DEFS:
        values = read_u16s(data, off, SLOTS_PER_LINE)

        hit_empty = False
        for value in values:
            if value == 0xFFFF:
                hit_empty = True
                continue

            if hit_empty:
                raise RuntimeError(
                    f"Database_Evolution line {line_id} contains a Digimon ID "
                    "after an FFFF/empty slot."
                )

            if value not in PARTNER_IDS:
                raise RuntimeError(
                    f"Database_Evolution line {line_id} contains invalid "
                    f"Digimon ID {value}; expected 0..20 or FFFF."
                )

            if value in digimon_to_line:
                raise RuntimeError(
                    f"Digimon ID {value} appears more than once across "
                    "Database_Evolution."
                )

            digimon_to_line[value] = line_id
            seen.append(value)

    if len(seen) != 21 or set(seen) != PARTNER_IDS:
        raise RuntimeError(
            "Database_Evolution does not contain each partner Digimon ID 0..20 "
            f"exactly once. Found: {seen}"
        )

    return digimon_to_line


def validate_partner_table(data, digimon_to_line):
    for digimon_id in range(PARTNER_COUNT):
        rec_off = PARTNER_TABLE_START + digimon_id * PARTNER_RECORD_SIZE

        stored_id = struct.unpack_from(
            "<H", data, rec_off + PARTNER_DIGIMON_ID_OFFSET
        )[0]
        if stored_id != digimon_id:
            raise RuntimeError(
                f"Partner Table validation failed at row {digimon_id}: "
                f"expected digimon_id {digimon_id}, found {stored_id}"
            )

        slot_type_id = struct.unpack_from(
            "<H", data, rec_off + PARTNER_SLOT_TYPE_ID_OFFSET
        )[0]
        expected = digimon_to_line[digimon_id]

        if slot_type_id != expected:
            raise RuntimeError(
                "Database_Evolution and Partner Table are inconsistent: "
                f"Digimon ID {digimon_id} belongs to line_id {expected}, "
                f"but Partner Table slot_type_id is {slot_type_id}."
            )


def validate_database(data):
    digimon_to_line = build_digimon_to_line(data)

    flat = list(read_u16s(data, FLAT_ORDER_OFFSET, 21))
    if len(flat) != 21 or set(flat) != PARTNER_IDS or len(set(flat)) != 21:
        raise RuntimeError(
            f"Flattened evolution order is invalid: {flat}"
        )

    validate_partner_table(data, digimon_to_line)
    return digimon_to_line


def main():
    bin_in = sys.argv[1] if len(sys.argv) >= 2 else "D-Ark.bin"
    csv_out = (
        sys.argv[2]
        if len(sys.argv) >= 3
        else "d_ark_evolution_slots.csv"
    )

    data = Path(bin_in).read_bytes()

    if not data.startswith(GP_HEADER):
        raise RuntimeError(
            "Input does not look like a compatible GP-SPIF D-Ark BIN"
        )

    digimon_to_line = validate_database(data)

    rows = []
    for line_id, line_name, off in LINE_DEFS:
        values = read_u16s(data, off, SLOTS_PER_LINE)

        row = {
            "line_id": line_id,
            "line_name": line_name,
            "line_offset": f"0x{off:08X}",
        }

        for i, value in enumerate(values, 1):
            row[f"slot_{i}"] = "" if value == 0xFFFF else value

        rows.append(row)

    with open(csv_out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    flat = read_u16s(data, FLAT_ORDER_OFFSET, 21)

    print(f"[+] Database_Evolution exported from {bin_in}")
    print(
        f"[+] Line arrays: 0x{LINE_DEFS[0][2]:08X}.."
        f"0x{LINE_DEFS[-1][2] + 20:08X}"
    )
    print(
        f"[+] Flattened order @ 0x{FLAT_ORDER_OFFSET:08X}: "
        + ", ".join(str(x) for x in flat)
    )
    print(
        "[+] Partner Table slot_type_id values match "
        "Database_Evolution membership for all 21 Digimon."
    )
    print(f"[DONE] Wrote {csv_out}")


if __name__ == "__main__":
    main()
