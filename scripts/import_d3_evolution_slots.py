#!/usr/bin/env python3
"""Import physical D-3 evolution slots and install hardware-confirmed v15."""

import argparse
import csv
from pathlib import Path

from d3_evolution_core import (
    LINE_DEFS,
    LINE_NAMES,
    MAX_LINE_SLOTS,
    atomic_write,
    changed_ranges,
    format_report,
    sha256_bytes,
    synchronize_existing_bin,
)


def load_csv(path: str) -> dict[int, list[int]]:
    required = ["line_id", "line_name"] + [
        f"slot_{index}" for index in range(1, MAX_LINE_SLOTS + 1)
    ]
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise RuntimeError("CSV has no header")
        missing = [name for name in required if name not in reader.fieldnames]
        if missing:
            raise RuntimeError("CSV missing columns: " + ", ".join(missing))
        rows = list(reader)
    if len(rows) != len(LINE_DEFS):
        raise RuntimeError(f"CSV must contain exactly 7 rows; found {len(rows)}")

    result: dict[int, list[int]] = {}
    for row_number, row in enumerate(rows, start=2):
        try:
            line_id = int(str(row["line_id"]).strip(), 0)
        except ValueError as exc:
            raise RuntimeError(f"CSV row {row_number}: invalid line_id") from exc
        if line_id not in LINE_NAMES or line_id in result:
            raise RuntimeError(f"CSV row {row_number}: duplicate/invalid line_id {line_id}")
        line_name = str(row["line_name"]).strip()
        if line_name != LINE_NAMES[line_id]:
            raise RuntimeError(
                f"CSV row {row_number}: line_name must be {LINE_NAMES[line_id]!r}"
            )

        values: list[int] = []
        hit_blank = False
        for slot_index in range(1, MAX_LINE_SLOTS + 1):
            column = f"slot_{slot_index}"
            text = str(row.get(column, "")).strip()
            if not text:
                hit_blank = True
                continue
            if hit_blank:
                raise RuntimeError(
                    f"CSV row {row_number}: {column} is filled after a blank slot"
                )
            try:
                value = int(text, 0)
            except ValueError as exc:
                raise RuntimeError(
                    f"CSV row {row_number}, {column}: invalid physical record {text!r}"
                ) from exc
            if not 0 <= value < 38:
                raise RuntimeError(
                    f"CSV row {row_number}, {column}: physical record must be 0..37"
                )
            if value in values:
                raise RuntimeError(
                    f"CSV row {row_number}: physical record {value} appears twice"
                )
            values.append(value)
        result[line_id] = values

    if set(result) != set(range(7)):
        raise RuntimeError("CSV must contain line_id 0..6 exactly once")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import D-3 evolution slots with the hardware-confirmed v15 "
            "map-only reordered selector and stock Link fallback"
        )
    )
    parser.add_argument("bin_in", help="Input D3 .bin")
    parser.add_argument("csv", help="Evolution Slots CSV")
    parser.add_argument("bin_out", help="Output D3 .bin")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    original = Path(args.bin_in).read_bytes()
    data = bytearray(original)
    requested = load_csv(args.csv)
    lines, source_kind, notes, changes, report = synchronize_existing_bin(
        data, requested
    )

    print(f"Source: {source_kind}")
    for line_id, line_name in LINE_DEFS:
        print(f"  {line_id} {line_name}: {lines[line_id]}")
    print(format_report(report))
    print(f"Changed fields/words: {len(changes)}")
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
