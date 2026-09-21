#!/usr/bin/env python3
"""
import_d_ark_database_evolution.py

Import D-Ark 25th Color Database_Evolution line arrays.

Behavior:
- Writes the five 10-slot Database_Evolution line arrays.
- Automatically regenerates the flattened 21-Digimon order at 0x000D75E8.
- AUTOMATICALLY synchronizes Partner Table slot_type_id for all Digimon IDs
  0..20 to the line_id containing that Digimon.
- Requires Digimon IDs 0..20 to appear exactly once across the five lines.
- Blank/FFFF/0xFFFF cells mean an empty slot.
- Input and output may be the same BIN; in-place writes are atomic.

Usage:
    python import_d_ark_database_evolution.py \
        D-Ark.bin d_ark_database_evolution.csv D-Ark_out.bin

No extra synchronization flag is needed.
"""

import argparse
import csv
import os
import struct
import tempfile
from pathlib import Path

LINE_DEFS = [
    (0, "Guilmon",      0x000D7584),
    (1, "Terriermon",   0x000D7598),
    (2, "Renamon",      0x000D75AC),
    (3, "Impmon",       0x000D75C0),
    (4, "Cyberdramon",  0x000D75D4),
]

SLOTS_PER_LINE = 10
FLAT_ORDER_OFFSET = 0x000D75E8

# The original flattened database orders Cyberdramon before Impmon even though
# the physical line arrays store Impmon before Cyberdramon.
FLAT_LINE_ORDER = [0, 1, 2, 4, 3]

PARTNER_TABLE_START = 0x000D6ACA
PARTNER_RECORD_SIZE = 0x5E
PARTNER_COUNT = 21
PARTNER_DIGIMON_ID_OFFSET = 0x02
PARTNER_SLOT_TYPE_ID_OFFSET = 0x04

PARTNER_IDS = set(range(21))
GP_HEADER = b"GP-SPIF-HEADER"

REQUIRED_HEADERS = (
    ["line_id"]
    + [f"slot_{i}" for i in range(1, SLOTS_PER_LINE + 1)]
)


def validate_current_database(data):
    seen = []

    for _line_id, _name, off in LINE_DEFS:
        values = struct.unpack_from("<10H", data, off)
        seen.extend(value for value in values if value != 0xFFFF)

    if (
        len(seen) != 21
        or set(seen) != PARTNER_IDS
        or len(set(seen)) != 21
    ):
        raise RuntimeError(
            "Existing Database_Evolution failed validation before import. "
            f"Found partner IDs: {seen}"
        )

    flat = list(struct.unpack_from("<21H", data, FLAT_ORDER_OFFSET))
    if (
        len(flat) != 21
        or set(flat) != PARTNER_IDS
        or len(set(flat)) != 21
    ):
        raise RuntimeError(
            f"Existing flattened evolution order failed validation: {flat}"
        )

    # Validate Partner Table identity, but do NOT require its current
    # slot_type_id values to agree.  The purpose of this importer is to repair/
    # synchronize those values from the CSV.
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


def parse_slot(text, row_number, field):
    s = str(text).strip()

    if s == "" or s.upper() == "FFFF" or s.lower() == "0xffff":
        return 0xFFFF

    try:
        value = int(s, 0)
    except ValueError:
        raise RuntimeError(
            f"CSV row {row_number}: invalid {field} value {s!r}"
        )

    if not 0 <= value <= 20:
        raise RuntimeError(
            f"CSV row {row_number}: {field} must be "
            "Digimon ID 0..20 or blank/FFFF"
        )

    return value


def load_lines(csv_path):
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise RuntimeError("CSV has no header")

        missing = [
            h for h in REQUIRED_HEADERS
            if h not in reader.fieldnames
        ]
        if missing:
            raise RuntimeError(
                "CSV is missing required columns: "
                + ", ".join(missing)
            )

        rows = list(reader)

    if len(rows) != 5:
        raise RuntimeError(
            f"CSV must contain exactly 5 evolution-line rows; "
            f"found {len(rows)}"
        )

    by_line = {}

    for row_number, row in enumerate(rows, start=2):
        try:
            line_id = int(
                str(row.get("line_id", "")).strip(), 0
            )
        except Exception:
            raise RuntimeError(
                f"CSV row {row_number}: invalid line_id"
            )

        if line_id not in range(5):
            raise RuntimeError(
                f"CSV row {row_number}: line_id must be 0..4"
            )

        if line_id in by_line:
            raise RuntimeError(
                f"CSV row {row_number}: duplicate line_id {line_id}"
            )

        values = [
            parse_slot(
                row.get(f"slot_{i}", ""),
                row_number,
                f"slot_{i}",
            )
            for i in range(1, SLOTS_PER_LINE + 1)
        ]

        # Keep non-empty slots contiguous.  This mirrors the structure used by
        # the stock arrays and prevents ambiguous line length.
        hit_empty = False
        for i, value in enumerate(values, start=1):
            if value == 0xFFFF:
                hit_empty = True
            elif hit_empty:
                raise RuntimeError(
                    f"CSV row {row_number}: slot_{i} is filled after "
                    "an earlier blank/FFFF slot."
                )

        by_line[line_id] = values

    if set(by_line) != set(range(5)):
        raise RuntimeError(
            "CSV must contain line_id values 0,1,2,3,4 exactly once"
        )

    used = [
        value
        for line_id in range(5)
        for value in by_line[line_id]
        if value != 0xFFFF
    ]

    if len(used) != 21:
        raise RuntimeError(
            f"Exactly 21 non-empty Digimon slots are required; "
            f"found {len(used)}"
        )

    if (
        set(used) != PARTNER_IDS
        or len(set(used)) != 21
    ):
        raise RuntimeError(
            "Digimon IDs 0..20 must each appear exactly once "
            "across Database_Evolution. "
            f"Found: {used}"
        )

    return by_line


def build_flat_order(by_line):
    flat = []

    for line_id in FLAT_LINE_ORDER:
        flat.extend(
            value
            for value in by_line[line_id]
            if value != 0xFFFF
        )

    if (
        len(flat) != 21
        or set(flat) != PARTNER_IDS
        or len(set(flat)) != 21
    ):
        raise RuntimeError(
            f"Generated flattened order is invalid: {flat}"
        )

    return flat


def build_digimon_to_line(by_line):
    digimon_to_line = {}

    for line_id, values in by_line.items():
        for digimon_id in values:
            if digimon_id == 0xFFFF:
                continue

            if digimon_id in digimon_to_line:
                raise RuntimeError(
                    f"Digimon ID {digimon_id} is assigned to more "
                    "than one evolution line."
                )

            digimon_to_line[digimon_id] = line_id

    if set(digimon_to_line) != PARTNER_IDS:
        missing = sorted(PARTNER_IDS - set(digimon_to_line))
        raise RuntimeError(
            f"Could not derive line assignment for Digimon IDs: {missing}"
        )

    return digimon_to_line


def sync_partner_slot_type_ids(data, by_line):
    digimon_to_line = build_digimon_to_line(by_line)
    changes = []

    for digimon_id in range(PARTNER_COUNT):
        rec_off = (
            PARTNER_TABLE_START
            + digimon_id * PARTNER_RECORD_SIZE
        )

        stored_id = struct.unpack_from(
            "<H",
            data,
            rec_off + PARTNER_DIGIMON_ID_OFFSET,
        )[0]

        if stored_id != digimon_id:
            raise RuntimeError(
                f"Partner Table validation failed at row {digimon_id}: "
                f"expected digimon_id {digimon_id}, found {stored_id}"
            )

        field_off = rec_off + PARTNER_SLOT_TYPE_ID_OFFSET
        old_slot_type_id = struct.unpack_from(
            "<H", data, field_off
        )[0]
        new_slot_type_id = digimon_to_line[digimon_id]

        if old_slot_type_id != new_slot_type_id:
            struct.pack_into(
                "<H",
                data,
                field_off,
                new_slot_type_id,
            )
            changes.append(
                (
                    digimon_id,
                    field_off,
                    old_slot_type_id,
                    new_slot_type_id,
                )
            )

    return changes


def validate_result(data, by_line, flat):
    # Verify line arrays exactly.
    for line_id, _name, off in LINE_DEFS:
        actual = list(struct.unpack_from("<10H", data, off))
        expected = list(by_line[line_id])

        if actual != expected:
            raise RuntimeError(
                f"Post-write validation failed for line {line_id}: "
                f"expected {expected}, found {actual}"
            )

    # Verify flattened order exactly.
    actual_flat = list(
        struct.unpack_from("<21H", data, FLAT_ORDER_OFFSET)
    )
    if actual_flat != list(flat):
        raise RuntimeError(
            "Post-write flattened-order validation failed: "
            f"expected {flat}, found {actual_flat}"
        )

    # Verify Partner Table slot_type_id exactly.
    digimon_to_line = build_digimon_to_line(by_line)

    for digimon_id in range(PARTNER_COUNT):
        rec_off = (
            PARTNER_TABLE_START
            + digimon_id * PARTNER_RECORD_SIZE
        )
        actual = struct.unpack_from(
            "<H",
            data,
            rec_off + PARTNER_SLOT_TYPE_ID_OFFSET,
        )[0]
        expected = digimon_to_line[digimon_id]

        if actual != expected:
            raise RuntimeError(
                "Post-write Partner Table validation failed for "
                f"Digimon ID {digimon_id}: expected slot_type_id "
                f"{expected}, found {actual}"
            )


def safe_write(src, dst, data):
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.resolve() != dst.resolve():
        dst.write_bytes(data)

        if dst.stat().st_size != len(data):
            raise RuntimeError(
                "Output BIN size validation failed"
            )

        return

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{dst.name}.",
        suffix=".tmp",
        dir=str(dst.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        temp_path.write_bytes(data)

        if temp_path.stat().st_size != len(data):
            raise RuntimeError(
                "Temporary BIN size validation failed"
            )

        os.replace(temp_path, dst)

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_bin")
    ap.add_argument("csv")
    ap.add_argument("output_bin")
    args = ap.parse_args()

    src = Path(args.input_bin)
    dst = Path(args.output_bin)
    data = bytearray(src.read_bytes())

    if not data.startswith(GP_HEADER):
        raise RuntimeError(
            "Input does not look like a compatible GP-SPIF D-Ark BIN"
        )

    validate_current_database(data)

    by_line = load_lines(args.csv)
    flat = build_flat_order(by_line)

    # 1. Write Database_Evolution line arrays.
    for line_id, _name, off in LINE_DEFS:
        struct.pack_into(
            "<10H",
            data,
            off,
            *by_line[line_id],
        )

    # 2. Rebuild flattened 21-Digimon order.
    struct.pack_into(
        "<21H",
        data,
        FLAT_ORDER_OFFSET,
        *flat,
    )

    # 3. Always synchronize Partner Table slot_type_id.
    partner_changes = sync_partner_slot_type_ids(
        data,
        by_line,
    )

    validate_result(data, by_line, flat)
    safe_write(src, dst, data)

    print("[DONE] Database_Evolution updated.")

    for line_id, name, off in LINE_DEFS:
        shown = [
            "FFFF" if x == 0xFFFF else str(x)
            for x in by_line[line_id]
        ]
        print(
            f"  line {line_id} {name:12s} "
            f"@ 0x{off:08X}: "
            + ", ".join(shown)
        )

    print(
        f"  flat @ 0x{FLAT_ORDER_OFFSET:08X}: "
        + ", ".join(str(x) for x in flat)
    )

    if partner_changes:
        print(
            "  Partner Table slot_type_id synchronized "
            f"for {len(partner_changes)} Digimon:"
        )
        for digimon_id, off, old, new in partner_changes:
            print(
                f"    ID {digimon_id:2d} "
                f"@ 0x{off:08X}: {old} -> {new}"
            )
    else:
        print(
            "  Partner Table slot_type_id already matched "
            "all imported evolution lines."
        )

    print(f"[DONE] Wrote {dst}")


if __name__ == "__main__":
    main()
