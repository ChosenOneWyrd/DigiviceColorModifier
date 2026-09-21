#!/usr/bin/env python3
"""
Export all strings/names from a D-Ark 25th Color BIN.

Usage:
    python export_d_ark_names.py D-Ark.bin replace_map.csv d_ark_names.csv

Output CSV:
    string_index,name

Known D-Ark text archive paths:
    (0x27E000, [0])
    (0x172000, [4, 0])

Both paths resolve to the same physical text archive in the currently
validated D-Ark firmware:
    0x27E014

The archive contains 338 strings (indexes 0..337).

Unlike the older D3 exporter, this script preserves 0xF000+ words as
<XXXX> tags instead of silently dropping them. This makes export/import
round-trips safer for strings containing special/control words.
"""

import argparse
import csv
import struct
from pathlib import Path


TEXT_ARCHIVE_PATHS = [
    (0x27E000, [0]),
    (0x172000, [4, 0]),
]


def le16(buf, off):
    return struct.unpack_from("<H", buf, off)[0]


def le32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def read_archive(buf, base_off):
    if base_off < 0 or base_off + 4 > len(buf):
        return None

    if le16(buf, base_off) != 0x3232:
        return None

    count = le16(buf, base_off + 2)
    if not (1 <= count <= 10000):
        return None

    table_end = base_off + 4 + count * 16
    if table_end > len(buf):
        return None

    entries = []

    for i in range(count):
        eoff = base_off + 4 + i * 16

        flags = le32(buf, eoff)
        rel_off = le32(buf, eoff + 4)
        comp_len = le32(buf, eoff + 8)
        decomp_len = le32(buf, eoff + 12)

        abs_off = base_off + rel_off
        size = decomp_len if decomp_len > 0 else comp_len

        if abs_off < 0 or size < 0 or abs_off + size > len(buf):
            return None

        entries.append({
            "flags": flags,
            "abs_off": abs_off,
            "size": size,
            "comp_len": comp_len,
            "decomp_len": decomp_len,
        })

    return entries


def get_entry_view(buf, root_off, path_indexes):
    base = root_off
    size = None

    for depth, idx in enumerate(path_indexes):
        arc = read_archive(buf, base)

        if arc is None:
            raise RuntimeError(f"Archive not found at 0x{base:X}")

        if not (0 <= idx < len(arc)):
            raise RuntimeError(
                f"Index {idx} out of range at archive 0x{base:X}"
            )

        entry = arc[idx]
        base = entry["abs_off"]
        size = entry["size"]

    if size is None:
        raise RuntimeError("Invalid empty archive path")

    return memoryview(buf)[base:base + size], base


def parse_text_archive(view):
    if len(view) < 4:
        return None

    count = le16(view, 0)
    if not (1 <= count <= 20000):
        return None

    table_end = 2 + 2 * count
    if table_end > len(view):
        return None

    offsets = [le16(view, 2 + 2 * i) for i in range(count)]

    previous = -1

    for word_off in offsets:
        start = word_off * 2

        if word_off < previous:
            return None

        if start < table_end or start >= len(view):
            return None

        previous = word_off

    return offsets


def decode_string(view, start_byte):
    """
    Return the complete encoded string as <XXXX> tags.

    We preserve every nonzero 16-bit word, including 0xF000+ special words.
    """
    out = []
    p = start_byte

    while p + 2 <= len(view):
        word = le16(view, p)
        p += 2

        if word == 0:
            break

        out.append(f"<{word:04X}>")

    return "".join(out)


def _decode_backslash_escapes(text):
    # Keep ordinary Unicode text untouched. Decode escape syntax only when
    # the cell actually contains a backslash.
    if "\\" not in text:
        return text

    try:
        return bytes(text, "utf-8").decode("unicode_escape")
    except Exception:
        return text


def load_replace_map(path):
    rules = []

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue

            src = _decode_backslash_escapes(row[0])
            dst = _decode_backslash_escapes(row[1])

            if src:
                rules.append((src, dst))

    # Longest source first avoids partial replacement collisions.
    rules.sort(key=lambda pair: len(pair[0]), reverse=True)
    return rules


def apply_replacements(text, rules):
    for src, dst in rules:
        text = text.replace(src, dst)
    return text


def locate_unique_text_archive(data):
    """
    Resolve every known D-Ark archive path and deduplicate by physical offset.

    The validated firmware has two paths pointing to one physical text archive.
    """
    targets = {}

    for root_off, path_indexes in TEXT_ARCHIVE_PATHS:
        try:
            view, abs_off = get_entry_view(data, root_off, path_indexes)
        except Exception as exc:
            print(
                f"[WARN] Could not resolve path "
                f"(0x{root_off:X}, {path_indexes}): {exc}"
            )
            continue

        offsets = parse_text_archive(view)
        if offsets is None:
            print(
                f"[WARN] Entry at 0x{abs_off:X} is not a valid text archive"
            )
            continue

        targets[abs_off] = (view, offsets)

    if not targets:
        raise RuntimeError(
            "No compatible D-Ark text archive was found."
        )

    if len(targets) != 1:
        where = ", ".join(f"0x{x:X}" for x in sorted(targets))
        raise RuntimeError(
            f"Expected one physical D-Ark text archive, found "
            f"{len(targets)} at {where}"
        )

    abs_off = next(iter(targets))
    view, offsets = targets[abs_off]

    return view, abs_off, offsets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin", help="D-Ark 25th Color BIN")
    ap.add_argument("replace_map", help="replace_map.csv")
    ap.add_argument("out_csv", help="output CSV")
    args = ap.parse_args()

    data = Path(args.bin).read_bytes()
    rules = load_replace_map(args.replace_map)

    view, abs_off, offsets = locate_unique_text_archive(data)

    print(
        f"[*] D-Ark text archive: 0x{abs_off:X} "
        f"({len(offsets)} strings)"
    )

    rows = []

    for string_index, word_off in enumerate(offsets):
        raw = decode_string(view, word_off * 2)
        decoded = apply_replacements(raw, rules)
        rows.append((string_index, decoded))

    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["string_index", "name"])
        writer.writerows(rows)

    print(
        f"[DONE] Exported {len(rows)} strings/names -> {args.out_csv}"
    )


if __name__ == "__main__":
    main()
