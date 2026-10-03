#!/usr/bin/env python3
"""
Export the 21 playable D-Ark 25th Color partner records.

Usage:
    python export_d_ark_partner_table.py D-Ark.bin d_ark_partner_table.csv

The D-Ark stores a larger 0x5E-byte Digimon record table beginning at
0x000D6ACA. The first 21 records are the playable partner forms.

Only the per-partner fields are exported. The remaining 28 uint16 values in
each 0x5E-byte record are identical generic battle data and are intentionally
not exported.
"""

import csv
import struct
import sys
from pathlib import Path

KNOWN_TABLE_START = 0x000D6ACA
RECORD_SIZE = 0x5E
PARTNER_COUNT = 21

HEADERS = [
    "offset",
    "stage",
    "digimon_id",
    "slot_type_id",
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
    "evo_unlock_conditions_id",
    "wins_after_previous_evo",
    "power",
]

GENERIC_TAIL = (
    0, 25, 1242, 12,
    0, 25, 1253, 10,
    0, 25, 1208, 10,
    0, 25, 1242, 12,
    0, 25, 1245, 10,
    0, 25, 1204, 12,
    0, 25, 1212, 9,
)

def read_record(data, off):
    return struct.unpack_from("<47H", data, off)

def is_partner_table_at(data, base):
    if base < 0 or base + PARTNER_COUNT * RECORD_SIZE > len(data):
        return False

    for i in range(PARTNER_COUNT):
        rec = read_record(data, base + i * RECORD_SIZE)

        # digimon_id is the stable record identity for these 21 partner rows.
        if rec[1] != i:
            return False

        # The final 28 words are identical generic battle data in every
        # validated partner record. Using them as the structural signature
        # allows all exported fields to be edited later without breaking
        # table detection.
        if tuple(rec[19:47]) != GENERIC_TAIL:
            return False

    return True

def find_partner_table(data):
    if is_partner_table_at(data, KNOWN_TABLE_START):
        return KNOWN_TABLE_START

    hits = []

    # Fallback search for a record whose digimon_id is 0 and whose generic
    # tail matches, then validate all 21 sequential records.
    for base in range(0, len(data) - PARTNER_COUNT * RECORD_SIZE + 1, 2):
        if struct.unpack_from("<H", data, base + 2)[0] != 0:
            continue

        try:
            first = read_record(data, base)
        except struct.error:
            continue

        if tuple(first[19:47]) != GENERIC_TAIL:
            continue

        if is_partner_table_at(data, base):
            hits.append(base)

    hits = sorted(set(hits))

    if not hits:
        raise RuntimeError("Compatible D-Ark partner table was not found.")

    if len(hits) != 1:
        where = ", ".join(f"0x{x:08X}" for x in hits)
        raise RuntimeError(f"Partner-table detection is ambiguous: {where}")

    return hits[0]

def main():
    if len(sys.argv) >= 3:
        bin_in = sys.argv[1]
        csv_out = sys.argv[2]
    else:
        bin_in = "D-Ark.bin"
        csv_out = "d_ark_partner_table.csv"

    print(f"[+] Using BIN: {bin_in}")
    print(f"[+] Writing CSV: {csv_out}")

    data = Path(bin_in).read_bytes()
    table_start = find_partner_table(data)
    rows = []

    for i in range(PARTNER_COUNT):
        off = table_start + i * RECORD_SIZE
        rec = read_record(data, off)

        rows.append({
            "offset": f"0x{off:08X}",
            "stage": rec[0],
            "digimon_id": rec[1],
            "slot_type_id": rec[2],
            "win_requirement_for_next_evo": rec[3],
            "sprite_index": rec[4],
            "string_index": rec[5],
            "evo_animation1_id": rec[6],
            "evo_animation2_id": rec[7],
            "evo_animation3_id": rec[8],
            "evo_animation4_id": rec[9],
            "evo_animation5_id": rec[10],
            "background_music_during_battle_id": rec[11],
            "attack_voice_sound_id": rec[12],
            "attack_shot_sprite_index": rec[13],
            "attack_shot_sound_id": rec[14],
            "special_unlock": rec[15],
            "evo_unlock_conditions_id": rec[16],
            "wins_after_previous_evo": rec[17],
            "power": rec[18],
        })

    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[+] Partner table: 0x{table_start:08X}")
    print(f"[DONE] Exported {len(rows)} partner rows")
    print(f"[DONE] Wrote {csv_out}")

if __name__ == "__main__":
    main()
