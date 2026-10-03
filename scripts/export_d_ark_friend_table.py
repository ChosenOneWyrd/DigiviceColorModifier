#!/usr/bin/env python3
"""
export_d_ark_friend_table.py

Export the 76 selectable Friend Digimon from a D-Ark -25th Color Evolution-
firmware BIN.

Reverse-engineered layout (validated against the supplied D-Ark firmware):

    compact enemy/battle table base : 0x000D4018
    record size                    : 0x14 bytes (10 uint16 words)
    record count                   : 98

Record layout:
    +0x00  string_index
    +0x02  sprite_index
    +0x04  battle_sprite_index
    +0x06  attack_voice_sound_id
    +0x08  attack_shot_sprite_index
    +0x0A  attack_shot_sound_id
    +0x0C  special_flag
    +0x0E  stage_id
    +0x10  power
    +0x12  battle_id

The 76 selectable Friends are exactly the records whose stored battle_id is
11..86 inclusive.

This is important: D-Ark does not appear to have a D3-style standalone Friend
record array with friend_id 0..N. Instead, Friend-capable Digimon are a subset
of this compact enemy/battle database.

The exporter also decodes each record's string_index from the D-Ark text
archive so the CSV contains a readable name. It does not require replace_map.csv.

Usage:
    python export_d_ark_friend_table.py D-Ark.bin d_ark_friend_table.csv
"""

import argparse
import csv
import struct
from pathlib import Path

GP_HEADER = b"GP-SPIF-HEADER"

BATTLE_TABLE_BASE = 0x000D4018
BATTLE_RECORD_SIZE = 0x14
BATTLE_RECORD_COUNT = 98

FRIEND_BATTLE_ID_MIN = 11
FRIEND_BATTLE_ID_MAX = 86
FRIEND_COUNT = 76

# Strong structural signature for this validated firmware.
LEADING_STRING_INDEXES = [219, 220, 221, 222, 226, 231, 235, 236]
NPC_STRING_INDEX_FIRST = 248
NPC_STRING_INDEX_LAST = 337

TEXT_ARCHIVE_PATHS = [
    (0x27E000, [0]),
    (0x172000, [4, 0]),
]

CSV_FIELDS = [
    "record_offset",
    "table_record_index",
    "battle_id",
    "string_index",
    "name",
    "sprite_index",
    "battle_sprite_index",
    "attack_voice_sound_id",
    "attack_shot_sprite_index",
    "attack_shot_sound_id",
    "special_flag",
    "stage_id",
    "power",
]


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def read_record(data, off):
    return struct.unpack_from("<10H", data, off)


def validate_table_at(data, base):
    end = base + BATTLE_RECORD_COUNT * BATTLE_RECORD_SIZE
    if base < 0 or end > len(data):
        return False

    # First 8 compact records are selected partner/story battle forms.
    for i, expected_si in enumerate(LEADING_STRING_INDEXES):
        if u16(data, base + i * BATTLE_RECORD_SIZE) != expected_si:
            return False

    # The following 90 records have string indexes 248..337 in exact order.
    npc_base = base + len(LEADING_STRING_INDEXES) * BATTLE_RECORD_SIZE
    for j, expected_si in enumerate(
        range(NPC_STRING_INDEX_FIRST, NPC_STRING_INDEX_LAST + 1)
    ):
        if u16(data, npc_base + j * BATTLE_RECORD_SIZE) != expected_si:
            return False

    return True


def find_battle_table(data):
    if validate_table_at(data, BATTLE_TABLE_BASE):
        return BATTLE_TABLE_BASE

    # Fallback structural scan for compatible relocated firmware.
    needle = struct.pack("<H", LEADING_STRING_INDEXES[0])
    start = 0
    hits = []

    while True:
        pos = data.find(needle, start)
        if pos < 0:
            break

        if pos % 2 == 0 and validate_table_at(data, pos):
            hits.append(pos)

        start = pos + 1

    hits = sorted(set(hits))

    if not hits:
        raise RuntimeError(
            "Compatible D-Ark compact enemy/battle table was not found."
        )

    if len(hits) != 1:
        where = ", ".join(f"0x{x:08X}" for x in hits)
        raise RuntimeError(
            f"Battle-table detection is ambiguous; found {len(hits)} tables: {where}"
        )

    return hits[0]


# ----------------------------------------------------------------------
# Minimal D-Ark text archive reader
# ----------------------------------------------------------------------

def read_archive(data, base_off):
    if base_off < 0 or base_off + 4 > len(data):
        return None

    if u16(data, base_off) != 0x3232:
        return None

    count = u16(data, base_off + 2)
    if not (1 <= count <= 10000):
        return None

    table_end = base_off + 4 + count * 16
    if table_end > len(data):
        return None

    entries = []

    for i in range(count):
        eoff = base_off + 4 + i * 16
        rel_off = u32(data, eoff + 4)
        comp_len = u32(data, eoff + 8)
        decomp_len = u32(data, eoff + 12)

        abs_off = base_off + rel_off
        size = decomp_len if decomp_len > 0 else comp_len

        if abs_off < 0 or size < 0 or abs_off + size > len(data):
            return None

        entries.append((abs_off, size))

    return entries


def get_entry_view(data, root_off, path_indexes):
    base = root_off
    size = None

    for idx in path_indexes:
        archive = read_archive(data, base)
        if archive is None:
            raise RuntimeError(f"Archive not found at 0x{base:X}")

        if not (0 <= idx < len(archive)):
            raise RuntimeError(
                f"Archive index {idx} is invalid at 0x{base:X}"
            )

        base, size = archive[idx]

    if size is None:
        raise RuntimeError("Invalid empty archive path")

    return memoryview(data)[base:base + size], base


def parse_text_archive(view):
    if len(view) < 4:
        return None

    count = u16(view, 0)
    if not (1 <= count <= 20000):
        return None

    table_end = 2 + 2 * count
    if table_end > len(view):
        return None

    offsets = [u16(view, 2 + 2 * i) for i in range(count)]

    previous = -1
    for word_off in offsets:
        byte_off = word_off * 2
        if word_off < previous:
            return None
        if byte_off < table_end or byte_off >= len(view):
            return None
        previous = word_off

    return offsets


def locate_text_archive(data):
    targets = {}

    for root, path in TEXT_ARCHIVE_PATHS:
        try:
            view, abs_off = get_entry_view(data, root, path)
        except Exception:
            continue

        offsets = parse_text_archive(view)
        if offsets is not None:
            targets[abs_off] = (view, offsets)

    if len(targets) != 1:
        where = ", ".join(f"0x{x:X}" for x in sorted(targets))
        raise RuntimeError(
            "Expected one D-Ark text archive; "
            f"found {len(targets)}"
            + (f" at {where}" if where else "")
        )

    abs_off = next(iter(targets))
    view, offsets = targets[abs_off]
    return view, abs_off, offsets


def decode_device_name(view, offsets, string_index):
    if not (0 <= string_index < len(offsets)):
        return f"(string {string_index})"

    p = offsets[string_index] * 2
    out = []

    while p + 2 <= len(view):
        word = u16(view, p)
        p += 2

        if word == 0:
            break

        # Device's Latin alphabet encoding.
        if 0x11 <= word <= 0x2A:
            out.append(chr(ord("A") + word - 0x11))
        elif word in (0x4A, 0x57):
            # These are separator/punctuation codes used in names.
            # A simple space is safest for a readable exporter.
            out.append(" ")
        elif 0x02 <= word <= 0x0B:
            out.append(str(word - 0x02))
        elif word == 0x01:
            out.append(" ")
        else:
            out.append(f"<{word:04X}>")

    return "".join(out).strip()


def export_friend_table(bin_path, csv_path):
    data = Path(bin_path).read_bytes()

    if not data.startswith(GP_HEADER):
        raise RuntimeError(
            "Input does not look like a compatible GP-SPIF D-Ark BIN."
        )

    base = find_battle_table(data)
    text_view, text_base, text_offsets = locate_text_archive(data)

    friend_rows = []

    for table_index in range(BATTLE_RECORD_COUNT):
        off = base + table_index * BATTLE_RECORD_SIZE
        (
            string_index,
            sprite_index,
            battle_sprite_index,
            attack_voice_sound_id,
            attack_shot_sprite_index,
            attack_shot_sound_id,
            special_flag,
            stage_id,
            power,
            battle_id,
        ) = read_record(data, off)

        if not (FRIEND_BATTLE_ID_MIN <= battle_id <= FRIEND_BATTLE_ID_MAX):
            continue

        friend_rows.append({
            "record_offset": f"0x{off:08X}",
            "table_record_index": table_index,
            "battle_id": battle_id,
            "string_index": string_index,
            "name": decode_device_name(
                text_view, text_offsets, string_index
            ),
            "sprite_index": sprite_index,
            "battle_sprite_index": battle_sprite_index,
            "attack_voice_sound_id": attack_voice_sound_id,
            "attack_shot_sprite_index": attack_shot_sprite_index,
            "attack_shot_sound_id": attack_shot_sound_id,
            "special_flag": special_flag,
            "stage_id": stage_id,
            "power": power,
        })

    if len(friend_rows) != FRIEND_COUNT:
        raise RuntimeError(
            f"Expected {FRIEND_COUNT} Friend-capable records, "
            f"found {len(friend_rows)}."
        )

    battle_ids = {row["battle_id"] for row in friend_rows}
    expected_ids = set(range(FRIEND_BATTLE_ID_MIN, FRIEND_BATTLE_ID_MAX + 1))

    if battle_ids != expected_ids:
        missing = sorted(expected_ids - battle_ids)
        extra = sorted(battle_ids - expected_ids)
        raise RuntimeError(
            "Friend battle_id validation failed. "
            f"Missing={missing}, extra={extra}"
        )

    # Preserve physical ROM order. record_offset/table_record_index make the
    # exact source record unambiguous.
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(friend_rows)

    print(f"[+] Compact enemy/battle table: 0x{base:08X}")
    print(
        f"[+] Text archive: 0x{text_base:08X} "
        f"({len(text_offsets)} strings)"
    )
    print(
        f"[+] Friend selector: stored battle_id "
        f"{FRIEND_BATTLE_ID_MIN}..{FRIEND_BATTLE_ID_MAX}"
    )
    print(f"[DONE] Exported {len(friend_rows)} Friend records -> {csv_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Export D-Ark 25th Color Friend records"
    )
    ap.add_argument("bin", help="Input D-Ark.bin")
    ap.add_argument("out_csv", help="Output CSV")
    args = ap.parse_args()

    export_friend_table(args.bin, args.out_csv)


if __name__ == "__main__":
    main()
