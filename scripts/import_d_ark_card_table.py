#!/usr/bin/env python3
"""
Import editable fields of the 40 D-Ark 25th Color card-definition records.

Usage:
    python import_d_ark_card_table.py D-Ark.bin d_ark_card_table.csv D-Ark_out.bin

Editable CSV columns:
    card_id
    effect_type_id
    string_index
    card_menu_string_index
    description_string_index
    search_unlock_value
    sprite_index
    alternate_sprite_index

Safety:
- card_id is identity only and is NEVER written.
- The CSV must contain exactly one row for each card_id 1..40.
- effect_type_id must be 0..13.
- All other editable values must fit uint16 (0..65535).
- Only the seven editable uint16 fields are modified; the other 26 words in
  each 0x42-byte record are preserved byte-for-byte.
- Input and output may be the same path; the full BIN is read before writing.
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

EDITABLE_FIELDS = [
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


def parse_u16(text, field_name, card_id):
    s = str(text).strip()
    if s == "":
        raise RuntimeError(
            f"Card {card_id}: {field_name} is blank"
        )

    try:
        value = int(s, 0)
    except ValueError:
        raise RuntimeError(
            f"Card {card_id}: invalid {field_name} value {s!r}"
        )

    if not 0 <= value <= 0xFFFF:
        raise RuntimeError(
            f"Card {card_id}: {field_name}={value} is outside 0..65535"
        )

    return value


def load_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise RuntimeError("CSV has no header")

        missing = [x for x in CSV_FIELDS if x not in reader.fieldnames]
        if missing:
            raise RuntimeError(
                "CSV is missing required columns: " + ", ".join(missing)
            )

        by_id = {}

        for row_number, row in enumerate(reader, start=2):
            try:
                card_id = int(str(row["card_id"]).strip(), 0)
            except Exception:
                raise RuntimeError(
                    f"CSV row {row_number}: invalid card_id {row.get('card_id')!r}"
                )

            if not 1 <= card_id <= CARD_COUNT:
                raise RuntimeError(
                    f"CSV row {row_number}: card_id {card_id} is outside 1..40"
                )

            if card_id in by_id:
                raise RuntimeError(
                    f"CSV contains duplicate card_id {card_id}"
                )

            parsed = {"card_id": card_id}

            for field in EDITABLE_FIELDS:
                parsed[field] = parse_u16(
                    row.get(field, ""),
                    field,
                    card_id,
                )

            if not 0 <= parsed["effect_type_id"] <= 13:
                raise RuntimeError(
                    f"Card {card_id}: effect_type_id must be 0..13, "
                    f"got {parsed['effect_type_id']}"
                )

            by_id[card_id] = parsed

    expected = set(range(1, CARD_COUNT + 1))
    actual = set(by_id)

    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        bits = []
        if missing:
            bits.append(f"missing card IDs {missing}")
        if extra:
            bits.append(f"unexpected card IDs {extra}")
        raise RuntimeError(
            "CSV must contain exactly cards 1..40: " + "; ".join(bits)
        )

    return by_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin", help="input D-Ark BIN")
    ap.add_argument("csv", help="edited card-table CSV")
    ap.add_argument("out", help="output BIN (may be same as input)")
    args = ap.parse_args()

    src = Path(args.bin)
    dst = Path(args.out)

    data = bytearray(src.read_bytes())
    base = find_card_table(data)
    rows = load_csv(args.csv)

    changed_fields = 0

    for card_id in range(1, CARD_COUNT + 1):
        i = card_id - 1
        r = base + i * RECORD_SIZE

        # card_id identity must match the immutable raw record ID.
        raw_id = u16(data, r + FIELD_OFFSETS["raw_card_index"])
        if raw_id != i:
            raise RuntimeError(
                f"BIN card record {card_id} has unexpected raw ID {raw_id}"
            )

        row = rows[card_id]

        for field in EDITABLE_FIELDS:
            off = r + FIELD_OFFSETS[field]
            old = u16(data, off)
            new = row[field]

            if old != new:
                struct.pack_into("<H", data, off, new)
                changed_fields += 1

    dst.write_bytes(data)

    print(f"[*] D-Ark card table: 0x{base:X}")
    print(f"[DONE] Updated {changed_fields} card-table field(s) -> {dst}")


if __name__ == "__main__":
    main()
