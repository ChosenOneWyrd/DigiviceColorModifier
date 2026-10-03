#!/usr/bin/env python3
"""
import_digivice_evolution_slots.py

Import Digivice -25th Color Evolution- evolution-line membership from the
CSV produced by export_digivice_evolution_slots.py.

CSV:
    line_id,line_name,slot_1,...,slot_10

Known writable structure
------------------------
The 37-record Digivice Partner Table stores the current evolution-line
assignment directly:

    Partner Table base : 0x000946D4
    record size        : 0x1A bytes
    record count       : 37
    digimon_id         : record + 0x00
    line / slot_type   : record + 0x02

This importer changes ONLY the line / slot_type uint16 field at record + 0x02.
It does not relocate Partner records and does not modify any other partner
metadata, animation IDs, sprites, names, requirements, or game-progress data.

Important limitation: slot order
--------------------------------
The Partner Table records themselves remain in fixed digimon_id order 0..36.
The exporter obtains each line by scanning those records in that physical order.

Therefore the currently proven writable structure controls LINE MEMBERSHIP,
not an independent arbitrary within-line order.  Nonblank Digimon IDs in every
CSV row must consequently be in ascending digimon_id order.

For example this is representable:

    6,Patamon Line,29,30,31,32,34
    7,Tailmon Line,33

because ID 34 was moved from Tailmon Line to Patamon Line.

This is NOT representable by the known line field alone:

    6,Patamon Line,34,29,30,31,32

The importer rejects such a row instead of silently producing a different
order.

Known line IDs
--------------
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

Safety / validation
-------------------
- GP-SPIF header is required.
- Partner Table record identities must still be exactly digimon_id 0..36.
- Existing line IDs must be 0..9.
- CSV must contain line_id 0..9 exactly once.
- line_name must match the known line name.
- Blank / FFFF / 0xFFFF means an empty slot.
- Nonblank slots must be contiguous from slot_1.
- Every Digimon ID 0..36 must appear exactly once across the ten rows.
- Every known line must remain non-empty.
- Only the 37 record+0x02 uint16 fields are allowed to change.
- In-place writes are atomic.
- --dry-run prints the exact planned changes without writing.

Usage:
    python import_digivice_evolution_slots.py \
        Digivice.bin digivice_evolution_slots.csv Digivice_out.bin

In place:
    python import_digivice_evolution_slots.py \
        Digivice.bin digivice_evolution_slots.csv Digivice.bin

Preview:
    python import_digivice_evolution_slots.py \
        Digivice.bin digivice_evolution_slots.csv Digivice_out.bin --dry-run

Reverse-engineering note
------------------------
A separate ROM list near 0x0009D6CA contains all 30 non-baby Partner Digimon
IDs. It is global rather than partitioned by evolution line and already
contains every non-baby ID, so this importer does not modify it.

The effect of moving a Digimon between lines should still be hardware-tested.
This script deliberately avoids modifying structures whose relationship to
line membership has not been proven.
"""

import argparse
import csv
import hashlib
import os
import struct
import tempfile
from collections import Counter
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

REQUIRED_HEADERS = (
    ["line_id", "line_name"]
    + [f"slot_{i}" for i in range(1, MAX_CSV_SLOTS + 1)]
)


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def put_u16(data, off, value):
    struct.pack_into("<H", data, off, value)


def sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def line_field_offset(digimon_id):
    return (
        PARTNER_TABLE_OFFSET
        + digimon_id * PARTNER_RECORD_SIZE
        + P_LINE_ID_OFFSET
    )


def validate_bin(data):
    if not data.startswith(GP_HEADER):
        raise RuntimeError(
            "Input does not look like a compatible GP-SPIF Digivice BIN."
        )

    table_end = PARTNER_TABLE_OFFSET + PARTNER_COUNT * PARTNER_RECORD_SIZE
    if table_end > len(data):
        raise RuntimeError(
            "BIN is too small for the known Digivice Partner Table layout."
        )

    current_lines = {}

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
                f"Existing Partner Digimon ID {digimon_id} has unsupported "
                f"line/slot_type {line_id}; expected 0..9."
            )

        current_lines[digimon_id] = line_id

    return current_lines


def parse_slot(text, row_number, column):
    s = str(text or "").strip()

    if s == "" or s.upper() == "FFFF" or s.lower() == "0xffff":
        return None

    try:
        value = int(s, 0)
    except ValueError:
        raise RuntimeError(
            f"CSV row {row_number}, {column}: invalid Digimon ID {s!r}."
        )

    if not 0 <= value < PARTNER_COUNT:
        raise RuntimeError(
            f"CSV row {row_number}, {column}: Digimon ID must be "
            f"0..{PARTNER_COUNT - 1} or blank/FFFF."
        )

    return value


def load_csv(csv_path):
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise RuntimeError("CSV has no header.")

        missing = [h for h in REQUIRED_HEADERS if h not in reader.fieldnames]
        if missing:
            raise RuntimeError(
                "CSV is missing required column(s): " + ", ".join(missing)
            )

        rows = list(reader)

    if len(rows) != len(LINE_NAMES):
        raise RuntimeError(
            f"CSV must contain exactly {len(LINE_NAMES)} evolution-line rows; "
            f"found {len(rows)}."
        )

    lines = {}
    seen_line_ids = set()

    for row_number, row in enumerate(rows, start=2):
        raw_line_id = str(row.get("line_id", "")).strip()

        try:
            line_id = int(raw_line_id, 0)
        except Exception:
            raise RuntimeError(
                f"CSV row {row_number}: invalid line_id {raw_line_id!r}."
            )

        if line_id not in LINE_NAMES:
            raise RuntimeError(
                f"CSV row {row_number}: line_id must be 0..9; found {line_id}."
            )

        if line_id in seen_line_ids:
            raise RuntimeError(
                f"CSV row {row_number}: duplicate line_id {line_id}."
            )

        seen_line_ids.add(line_id)

        actual_name = str(row.get("line_name", "")).strip()
        expected_name = LINE_NAMES[line_id]

        if actual_name != expected_name:
            raise RuntimeError(
                f"CSV row {row_number}: line_name for line_id {line_id} must be "
                f"{expected_name!r}; found {actual_name!r}."
            )

        values = []
        hit_blank = False

        for i in range(1, MAX_CSV_SLOTS + 1):
            col = f"slot_{i}"
            value = parse_slot(row.get(col, ""), row_number, col)

            if value is None:
                hit_blank = True
                continue

            if hit_blank:
                raise RuntimeError(
                    f"CSV row {row_number}: {col} is filled after an earlier "
                    "blank/FFFF slot. Non-empty slots must be contiguous from slot_1."
                )

            if value in values:
                raise RuntimeError(
                    f"CSV row {row_number}: Digimon ID {value} appears more than "
                    f"once in {expected_name}."
                )

            values.append(value)

        if not values:
            raise RuntimeError(
                f"CSV row {row_number}: {expected_name} cannot be empty in the "
                "currently validated Digivice line model."
            )

        # The known writable line field does not encode arbitrary within-line
        # order. Physical record scan order is digimon_id 0..36.
        if values != sorted(values):
            raise RuntimeError(
                f"CSV row {row_number}: {expected_name} has slot order {values}, "
                "but Digivice Partner records are fixed in digimon_id order. "
                "With the currently proven structure, IDs within a line must be "
                f"ascending; use {sorted(values)}."
            )

        lines[line_id] = values

    if seen_line_ids != set(LINE_NAMES):
        raise RuntimeError(
            "CSV must contain line_id values 0..9 exactly once."
        )

    used = [
        digimon_id
        for line_id in range(10)
        for digimon_id in lines[line_id]
    ]

    counts = Counter(used)

    missing_ids = [
        digimon_id
        for digimon_id in range(PARTNER_COUNT)
        if counts[digimon_id] == 0
    ]
    duplicate_ids = [
        digimon_id
        for digimon_id in range(PARTNER_COUNT)
        if counts[digimon_id] > 1
    ]

    if missing_ids or duplicate_ids or len(used) != PARTNER_COUNT:
        parts = []
        if missing_ids:
            parts.append(
                "missing Digimon ID(s): "
                + ", ".join(str(x) for x in missing_ids)
            )
        if duplicate_ids:
            parts.append(
                "duplicate Digimon ID(s): "
                + ", ".join(str(x) for x in duplicate_ids)
            )

        raise RuntimeError(
            "Every Digimon ID 0..36 must appear exactly once across all "
            "evolution lines; " + "; ".join(parts) + "."
        )

    return lines


def build_desired_line_by_id(lines):
    desired = {}

    for line_id in range(10):
        for digimon_id in lines[line_id]:
            if digimon_id in desired:
                raise RuntimeError(
                    f"Internal error: Digimon ID {digimon_id} was assigned twice."
                )
            desired[digimon_id] = line_id

    if set(desired) != set(range(PARTNER_COUNT)):
        raise RuntimeError(
            "Internal error: desired line assignment does not cover IDs 0..36."
        )

    return desired


def apply_changes(data, desired_line_by_id):
    changes = []

    for digimon_id in range(PARTNER_COUNT):
        off = line_field_offset(digimon_id)
        old = u16(data, off)
        new = desired_line_by_id[digimon_id]

        if old != new:
            put_u16(data, off, new)
            changes.append((digimon_id, off, old, new))

    return changes


def validate_result(data, desired_line_by_id):
    # Record identities must still be intact.
    validate_bin(data)

    for digimon_id in range(PARTNER_COUNT):
        off = line_field_offset(digimon_id)
        actual = u16(data, off)
        expected = desired_line_by_id[digimon_id]

        if actual != expected:
            raise RuntimeError(
                "Post-write validation failed for Digimon ID "
                f"{digimon_id}: expected line_id {expected}, found {actual}."
            )


def validate_only_expected_bytes_changed(before, after):
    allowed = set()

    for digimon_id in range(PARTNER_COUNT):
        off = line_field_offset(digimon_id)
        allowed.add(off)
        allowed.add(off + 1)

    unexpected = [
        off
        for off, (old, new) in enumerate(zip(before, after))
        if old != new and off not in allowed
    ]

    if unexpected:
        raise RuntimeError(
            "Safety check failed: bytes outside Partner Table line fields "
            "would change: "
            + ", ".join(f"0x{x:08X}" for x in unexpected[:20])
        )


def safe_write(src, dst, data):
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.resolve() != dst.resolve():
        dst.write_bytes(data)

        if dst.stat().st_size != len(data):
            raise RuntimeError("Output BIN size validation failed.")

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
            raise RuntimeError("Temporary BIN size validation failed.")

        os.replace(temp_path, dst)

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def print_plan(lines, changes):
    print("=== Evolution-line membership from CSV ===")
    for line_id in range(10):
        print(f"  {line_id} {LINE_NAMES[line_id]}: {lines[line_id]}")

    print()
    print(f"=== Planned Partner Table line changes: {len(changes)} ===")

    if not changes:
        print("  No line/slot_type fields need to change.")
        return

    for digimon_id, off, old, new in changes:
        print(
            f"  ID {digimon_id:2d} @ 0x{off:08X}: "
            f"{old} ({LINE_NAMES[old]}) -> {new} ({LINE_NAMES[new]})"
        )


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Import Digivice 25th Color evolution-line membership and update "
            "Partner Table line/slot_type fields"
        )
    )

    ap.add_argument("input_bin", help="Input Digivice.bin")
    ap.add_argument("csv", help="Edited digivice_evolution_slots.csv")
    ap.add_argument(
        "output_bin",
        help="Output BIN; may be the same path as input for atomic in-place write",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print exact changes without writing output",
    )

    args = ap.parse_args()

    src = Path(args.input_bin)
    dst = Path(args.output_bin)

    original = src.read_bytes()
    data = bytearray(original)

    current = validate_bin(data)
    lines = load_csv(args.csv)
    desired = build_desired_line_by_id(lines)

    before_sha = sha256_bytes(data)

    changes = apply_changes(data, desired)

    validate_result(data, desired)
    validate_only_expected_bytes_changed(original, data)

    print_plan(lines, changes)

    if args.dry_run:
        print()
        print("[DRY RUN] No output BIN written.")
        return

    safe_write(src, dst, data)

    # Re-read the actual output and verify it, including in-place writes.
    written = dst.read_bytes()
    validate_result(written, desired)

    if len(written) != len(original):
        raise RuntimeError(
            "Post-write validation failed: output BIN size changed."
        )

    print()
    print(f"[DONE] Wrote: {dst}")
    print(f"  changed line field(s): {len(changes)}")
    print(f"  input SHA-256 : {before_sha}")
    print(f"  output SHA-256: {sha256_bytes(written)}")
    print()
    print(
        "Only Partner Table record+0x02 line/slot_type fields were modified."
    )
    print(
        "Other evolution-related structures were intentionally left unchanged."
    )


if __name__ == "__main__":
    main()
