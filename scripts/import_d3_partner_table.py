#!/usr/bin/env python3
"""Import all 38 Partner rows, then atomically resynchronize Evolution Slots.

This is the order-independent replacement for the older Partner importer.  It
never edits the Link Battle tables.  If the resulting active logical-ID set is
not exactly the fixed 33-row Link roster, the output is refused.
"""

import argparse
import csv
from pathlib import Path
import struct

from d3_evolution_core import (
    PARTNER_COUNT,
    PARTNER_RECORD_SIZE,
    PARTNER_TABLE_OFFSET,
    atomic_write,
    changed_ranges,
    format_report,
    sha256_bytes,
    synchronize_existing_bin,
)


HEADERS = [
    "meta_offset",
    "data_offset",
    "stage",
    "digimon_id",
    "jogress_win_partner_id",
    "win_requirement_for_next_evo",
    "sprite_index",
    "string_index",
    "evo_animation1_id",
    "evo_animation2_id",
    "evo_animation3_id",
    "evo_animation4_id",
    "evo_animation5_id",
    "background_music_during_battle_id",
    "attack_voice_sound_id",
    "attack_shot_sprite_index",
    "attack_shot_sound_id",
    "special_unlock",
]

DATA_HEADERS = [
    "stage",
    "digimon_id",
    "jogress_win_partner_id",
    "win_requirement_for_next_evo",
    "sprite_index",
    "string_index",
    "evo_animation1_id",
    "evo_animation2_id",
    "evo_animation3_id",
    "evo_animation4_id",
    "evo_animation5_id",
    "background_music_during_battle_id",
    "attack_voice_sound_id",
    "attack_shot_sprite_index",
    "attack_shot_sound_id",
    "special_unlock",
]


def value(row: dict[str, str], column: str, row_number: int) -> int:
    try:
        result = int(str(row[column]).strip(), 0)
    except ValueError as exc:
        raise RuntimeError(
            f"CSV row {row_number}, {column}: invalid uint16 value"
        ) from exc
    if not 0 <= result <= 0xFFFF:
        raise RuntimeError(
            f"CSV row {row_number}, {column}: {result} is outside uint16"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import D-3 Partner Table and resynchronize selector v15"
    )
    parser.add_argument("bin_in")
    parser.add_argument("csv")
    parser.add_argument("bin_out")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.csv, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise RuntimeError("CSV has no header")
        missing = [name for name in HEADERS if name not in reader.fieldnames]
        if missing:
            raise RuntimeError("CSV missing columns: " + ", ".join(missing))
        rows = list(reader)
    if len(rows) != PARTNER_COUNT:
        raise RuntimeError(
            f"Partner CSV must contain exactly {PARTNER_COUNT} physical rows; "
            f"found {len(rows)}"
        )

    original = Path(args.bin_in).read_bytes()
    data = bytearray(original)
    for index, row in enumerate(rows):
        values = [value(row, name, index + 2) for name in DATA_HEADERS]
        struct.pack_into(
            "<16H",
            data,
            PARTNER_TABLE_OFFSET + index * PARTNER_RECORD_SIZE,
            *values,
        )

    lines, source_kind, notes, changes, report = synchronize_existing_bin(data)
    print(f"Evolution order recovered from: {source_kind}")
    for note in notes:
        print("Reconciled: " + note)
    print(format_report(report))
    print(f"Synchronization changes: {len(changes)} fields/words")
    print(
        "Changed byte ranges: "
        + ", ".join(
            f"0x{start:06X}-0x{end - 1:06X}"
            for start, end in changed_ranges(original, bytes(data))
        )
    )
    print(f"Output SHA-256: {sha256_bytes(data)}")
    if args.dry_run:
        print("Dry run: no file written")
        return
    atomic_write(args.bin_out, data)
    print(f"Wrote: {args.bin_out}")


if __name__ == "__main__":
    main()
