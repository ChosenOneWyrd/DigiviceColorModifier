#!/usr/bin/env python3
"""
Export the 40 D-Ark 25th Color card-definition records.

Usage:
    python export_d_ark_card_table.py D-Ark.bin d_ark_card_table.csv

CSV columns:
    card_id
    effect_type_id
    string_index
    card_menu_string_index
    description_string_index
    search_unlock_value
    sprite_index
    alternate_sprite_index

Notes:
- card_id is exported as the user-facing 1..40 ID.
- The underlying record stores card_id as 0..39 at +0x00.
- This exporter does not alter the BIN.
"""

import argparse
import csv
import struct
from pathlib import Path

CARD_COUNT = 40
RECORD_SIZE = 0x42
KNOWN_TABLE_BASE = 0x000D8CEA

FIELD_OFFSETS = {
    "raw_card_index": 0x00,
    "effect_type_id": 0x02,
    "string_index": 0x04,
    "card_menu_string_index": 0x06,
    "description_string_index": 0x08,
    "search_unlock_value": 0x0A,
    "sprite_index": 0x0C,
    "alternate_sprite_index": 0x0E,
}

CSV_FIELDS = [
    "card_id",
    "effect_type_id",
    "string_index",
    "card_menu_string_index",
    "description_string_index",
    "search_unlock_value",
    "sprite_index",
    "alternate_sprite_index",
]


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def is_card_table_at(data, base):
    if base < 0 or base + CARD_COUNT * RECORD_SIZE > len(data):
        return False

    for i in range(CARD_COUNT):
        if u16(data, base + i * RECORD_SIZE) != i:
            return False

    # Immutable tail fields from the D-Ark card-definition records.
    tail_checks = {
        0x10: 0,
        0x1A: 40980,
        0x1E: 41029,
        0x22: 41066,
        0x26: 41102,
        0x2A: 40960,
        0x2C: 40960,
        0x2E: 40980,
        0x32: 41029,
        0x36: 41066,
        0x3A: 41102,
        0x3E: 40960,
        0x40: 40960,
    }

    for i in range(CARD_COUNT):
        r = base + i * RECORD_SIZE
        for rel, expected in tail_checks.items():
            if u16(data, r + rel) != expected:
                return False

    return True


def find_card_table(data):
    if is_card_table_at(data, KNOWN_TABLE_BASE):
        return KNOWN_TABLE_BASE

    hits = []
    start = 0

    while True:
        pos = data.find(b"\x00\x00", start)
        if pos < 0:
            break

        if (
            pos % 2 == 0
            and pos + 2 * RECORD_SIZE + 2 <= len(data)
            and u16(data, pos + RECORD_SIZE) == 1
            and u16(data, pos + 2 * RECORD_SIZE) == 2
            and is_card_table_at(data, pos)
        ):
            hits.append(pos)

        start = pos + 1

    if not hits:
        raise RuntimeError("Compatible D-Ark card table was not found.")

    if len(hits) != 1:
        where = ", ".join(f"0x{x:X}" for x in hits)
        raise RuntimeError(
            f"Card-table detection is ambiguous: found {len(hits)} tables at {where}"
        )

    return hits[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin", help="D-Ark 25th Color BIN")
    ap.add_argument("out_csv", help="output CSV")
    args = ap.parse_args()

    data = Path(args.bin).read_bytes()
    base = find_card_table(data)

    rows = []

    for i in range(CARD_COUNT):
        r = base + i * RECORD_SIZE

        raw_id = u16(data, r + FIELD_OFFSETS["raw_card_index"])
        if raw_id != i:
            raise RuntimeError(
                f"Card record {i + 1} has unexpected raw ID {raw_id}"
            )

        rows.append({
            "card_id": i + 1,
            "effect_type_id": u16(data, r + FIELD_OFFSETS["effect_type_id"]),
            "string_index": u16(data, r + FIELD_OFFSETS["string_index"]),
            "card_menu_string_index": u16(
                data, r + FIELD_OFFSETS["card_menu_string_index"]
            ),
            "description_string_index": u16(
                data, r + FIELD_OFFSETS["description_string_index"]
            ),
            "search_unlock_value": u16(
                data, r + FIELD_OFFSETS["search_unlock_value"]
            ),
            "sprite_index": u16(data, r + FIELD_OFFSETS["sprite_index"]),
            "alternate_sprite_index": u16(
                data, r + FIELD_OFFSETS["alternate_sprite_index"]
            ),
        })

    with open(args.out_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[*] D-Ark card table: 0x{base:X}")
    print(f"[DONE] Exported {len(rows)} card records -> {args.out_csv}")


if __name__ == "__main__":
    main()
