#!/usr/bin/env python3
"""
export_d3_evolution_slots.py

Export the D-3 25th Color complete evolution-line membership/order to a simple CSV:

    line_id,line_name,slot_1,...,slot_10

Line names:
    0 Vmon Line
    1 Wormmon Line
    2 Hawkmon Line
    3 Tailmon Line
    4 Armadimon Line
    5 Patamon Line
    6 Terriermon Line

This reads the actual D3 structures rather than assuming the original stock
line lengths.  It reconstructs each line from:
  - Partner Table slot_type membership
  - known shared-evolution slot types 18/19/20
  - the 44-entry flat evolution-order table at 0x000AADDE

That means it can also export a BIN in which, for example, one normal Digimon
has been moved from Tailmon Line to Wormmon Line and the line lengths changed.

Usage:
    python export_d3_evolution_slots.py D3.bin d3_evolution_slots.csv
"""

import argparse
import csv
import struct
from pathlib import Path

GP_HEADER = b"GP-SPIF-HEADER"

ORDER_OFFSET = 0x000AADDE
ORDER_COUNT = 44

PARTNER_TABLE_OFFSET = 0x0009D968
PARTNER_RECORD_SIZE = 0x20
PARTNER_COUNT = 38
P_DIGIMON_ID = 1
P_SLOT_TYPE = 2

LINE_DEFS = [
    (0, "Vmon Line"),
    (1, "Wormmon Line"),
    (2, "Hawkmon Line"),
    (3, "Tailmon Line"),
    (4, "Armadimon Line"),
    (5, "Patamon Line"),
    (6, "Terriermon Line"),
]

SPECIAL_SLOT_LINES = {
    18: (0, 1),  # Paildramon / Imperialdramon forms
    19: (2, 3),  # Silphymon
    20: (4, 5),  # Shakkoumon
}

MAX_CSV_SLOTS = 10

ORDER_PRE_OFFSET = 0x000AADDA
ORDER_PRE_BYTES = bytes.fromhex("C3 01 C4 01")
ORDER_POST_OFFSET = ORDER_OFFSET + ORDER_COUNT * 4
ORDER_POST_BYTES = bytes.fromhex(
    "6B 6D 03 00 87 6D 03 00 9A 6F 03 00 D2 74 03 00"
)


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def read_order(data):
    return list(struct.unpack_from("<" + "I" * ORDER_COUNT, data, ORDER_OFFSET))


def validate_bin(data):
    if len(data) < ORDER_POST_OFFSET + len(ORDER_POST_BYTES):
        raise RuntimeError("BIN is too small for the known D3 layout")

    if not data.startswith(GP_HEADER):
        raise RuntimeError("Input does not look like a compatible D3 GP-SPIF BIN")

    if data[ORDER_PRE_OFFSET:ORDER_PRE_OFFSET + len(ORDER_PRE_BYTES)] != ORDER_PRE_BYTES:
        raise RuntimeError(
            "44-entry evolution-order pre-signature did not match. "
            "This may be a different firmware layout."
        )

    if data[ORDER_POST_OFFSET:ORDER_POST_OFFSET + len(ORDER_POST_BYTES)] != ORDER_POST_BYTES:
        raise RuntimeError(
            "44-entry evolution-order post-signature did not match. "
            "This may be a different firmware layout."
        )

    for digimon_id in range(PARTNER_COUNT):
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE
        stored_id = u16(data, base + P_DIGIMON_ID * 2)
        if stored_id != digimon_id:
            raise RuntimeError(
                f"Partner Table identity mismatch at record {digimon_id}: "
                f"stored digimon_id={stored_id}"
            )


def derive_expected_line_members(data):
    """
    Build the membership SET for each line from Partner Table slot_type.

    Direct slot_type 0..6:
        belongs to exactly that line.

    Shared slot_type:
        18 -> Vmon + Wormmon
        19 -> Hawkmon + Tailmon
        20 -> Armadimon + Patamon
    """
    members = {line_id: set() for line_id, _ in LINE_DEFS}

    for digimon_id in range(PARTNER_COUNT):
        base = PARTNER_TABLE_OFFSET + digimon_id * PARTNER_RECORD_SIZE
        slot_type = u16(data, base + P_SLOT_TYPE * 2)

        if 0 <= slot_type <= 6:
            members[slot_type].add(digimon_id)
        elif slot_type in SPECIAL_SLOT_LINES:
            for line_id in SPECIAL_SLOT_LINES[slot_type]:
                members[line_id].add(digimon_id)
        else:
            raise RuntimeError(
                f"Unsupported Partner Table slot_type={slot_type} for "
                f"digimon_id={digimon_id}. This exporter currently understands "
                "direct line types 0..6 and shared types 18/19/20."
            )

    total_occurrences = sum(len(v) for v in members.values())
    if total_occurrences != ORDER_COUNT:
        raise RuntimeError(
            f"Partner-derived line membership contains {total_occurrences} "
            f"occurrences, but the D3 flat order table has {ORDER_COUNT}."
        )

    return members


def split_flat_order_by_membership(data):
    """
    The flat order is line-concatenated.  Dynamic line lengths can be recovered
    from Partner Table membership counts.

    We then verify that each physical segment contains exactly the IDs expected
    for that line.  This catches inconsistent BINs instead of silently exporting
    a misleading CSV.
    """
    flat = read_order(data)

    for i, value in enumerate(flat):
        if not 0 <= value < PARTNER_COUNT:
            raise RuntimeError(
                f"Unexpected flat-order value 0x{value:08X} at global slot {i}"
            )

    expected = derive_expected_line_members(data)

    result = {}
    cursor = 0

    for line_id, line_name in LINE_DEFS:
        count = len(expected[line_id])
        segment = flat[cursor:cursor + count]

        if len(segment) != count:
            raise RuntimeError(
                f"Flat order ended while reading {line_name}: "
                f"wanted {count} entries"
            )

        if len(set(segment)) != len(segment):
            raise RuntimeError(
                f"{line_name} flat segment contains duplicate IDs: {segment}"
            )

        if set(segment) != expected[line_id]:
            missing = sorted(expected[line_id] - set(segment))
            extra = sorted(set(segment) - expected[line_id])
            raise RuntimeError(
                f"{line_name} flat-order segment does not match Partner Table "
                f"membership.\n  segment={segment}\n  missing={missing}\n  extra={extra}"
            )

        if count > MAX_CSV_SLOTS:
            raise RuntimeError(
                f"{line_name} has {count} entries, but this CSV format supports "
                f"only {MAX_CSV_SLOTS} slots per line."
            )

        result[line_id] = segment
        cursor += count

    if cursor != ORDER_COUNT:
        raise RuntimeError(
            f"Internal split error: consumed {cursor} of {ORDER_COUNT} flat entries"
        )

    return result


def export_csv(bin_path, csv_path):
    data = Path(bin_path).read_bytes()
    validate_bin(data)
    lines = split_flat_order_by_membership(data)

    headers = ["line_id", "line_name"] + [
        f"slot_{i}" for i in range(1, MAX_CSV_SLOTS + 1)
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()

        for line_id, line_name in LINE_DEFS:
            row = {
                "line_id": line_id,
                "line_name": line_name,
            }

            slots = lines[line_id]
            for i in range(MAX_CSV_SLOTS):
                row[f"slot_{i + 1}"] = slots[i] if i < len(slots) else ""

            writer.writerow(row)

    print(f"[DONE] Exported evolution lines to: {csv_path}")
    for line_id, line_name in LINE_DEFS:
        print(f"  {line_id} {line_name}: {lines[line_id]}")


def main():
    ap = argparse.ArgumentParser(
        description="Export D3 complete evolution lines to a simple CSV"
    )
    ap.add_argument("bin", help="Input D3.bin")
    ap.add_argument("csv", help="Output CSV")
    args = ap.parse_args()

    export_csv(args.bin, args.csv)


if __name__ == "__main__":
    main()
