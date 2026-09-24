#!/usr/bin/env python3
"""Export D-3 evolution lines using physical Partner record indexes."""

import argparse
import csv
from pathlib import Path

from d3_evolution_core import (
    LINE_DEFS,
    MAX_LINE_SLOTS,
    format_report,
    reconcile_lines_to_partner,
    require_safe,
)


def export_csv(bin_path: str, csv_path: str) -> None:
    data = Path(bin_path).read_bytes()
    lines, source_kind, notes = reconcile_lines_to_partner(data)
    report = require_safe(data, lines, "Input BIN")

    headers = ["line_id", "line_name"] + [
        f"slot_{index}" for index in range(1, MAX_LINE_SLOTS + 1)
    ]
    destination = Path(csv_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for line_id, line_name in LINE_DEFS:
            row = {"line_id": line_id, "line_name": line_name}
            values = lines[line_id]
            for index in range(MAX_LINE_SLOTS):
                row[f"slot_{index + 1}"] = values[index] if index < len(values) else ""
            writer.writerow(row)

    print(f"Exported: {destination}")
    print(f"Source selector: {source_kind}")
    for note in notes:
        print("Reconciled: " + note)
    for line_id, line_name in LINE_DEFS:
        print(f"  {line_id} {line_name}: {lines[line_id]}")
    print(format_report(report))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export hardware-confirmed v15 D-3 evolution slots to CSV"
    )
    parser.add_argument("bin", help="Input D3 .bin")
    parser.add_argument("csv", help="Output CSV")
    args = parser.parse_args()
    export_csv(args.bin, args.csv)


if __name__ == "__main__":
    main()
