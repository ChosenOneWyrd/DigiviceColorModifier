#!/usr/bin/env python3
"""
Fast export of D3 names from D3.bin.

Usage:
    python export_d3_names.py D3.bin replace_map.csv d3_names.csv

Output:
    string_index,name
"""

import sys
import csv
import struct
from pathlib import Path


TEXT_ARCHIVE_PATHS = [
    (0x1EC000, [0]),
    (0x140000, [4, 0]),
]


def le16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def le32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def read_archive(buf, base_off):
    if base_off + 4 > len(buf):
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

        if abs_off < 0 or abs_off + size > len(buf):
            return None

        entries.append({
            "flags": flags,
            "abs_off": abs_off,
            "size": size,
        })

    return entries


def get_entry_view(buf, root_off, path_indexes):
    base = root_off

    for depth, idx in enumerate(path_indexes):
        arc = read_archive(buf, base)
        if arc is None:
            raise RuntimeError(f"Archive not found at 0x{base:X}")

        if idx >= len(arc):
            raise RuntimeError(f"Index {idx} out of range at archive 0x{base:X}")

        entry = arc[idx]
        base = entry["abs_off"]
        size = entry["size"]

        if depth == len(path_indexes) - 1:
            return buf[base:base + size], base

    raise RuntimeError("Invalid path")


def parse_text_archive(view):
    if len(view) < 4:
        return None

    n = le16(view, 0)
    if not (1 <= n <= 20000):
        return None

    offsets_table_size = 2 + 2 * n
    if offsets_table_size > len(view):
        return None

    offsets = [le16(view, 2 + 2 * i) for i in range(n)]

    prev = 0
    for w in offsets:
        start = w * 2
        if w < prev:
            return None
        if start >= len(view):
            return None
        prev = w

    return offsets


def decode_string(view, start_byte):
    out = []
    p = start_byte

    while p + 2 <= len(view):
        w = le16(view, p)
        p += 2

        if w == 0:
            break

        if w >= 0xF000:
            continue

        out.append(f"<{w:04X}>")

    return "".join(out)


def load_replace_map(path):
    rules = []

    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue

            src, dst = row[0], row[1]

            try:
                src = bytes(src, "utf-8").decode("unicode_escape")
            except Exception:
                pass

            try:
                dst = bytes(dst, "utf-8").decode("unicode_escape")
            except Exception:
                pass

            if src:
                rules.append((src, dst))

    rules.sort(key=lambda x: len(x[0]), reverse=True)
    return rules


def apply_replacements(text, rules):
    for src, dst in rules:
        text = text.replace(src, dst)
    return text


def extract_names(buf, rules):
    names = {}

    for root_off, path_indexes in TEXT_ARCHIVE_PATHS:
        view, abs_off = get_entry_view(buf, root_off, path_indexes)

        offsets = parse_text_archive(view)
        if offsets is None:
            raise RuntimeError(
                f"Target entry at 0x{abs_off:X} does not look like a text archive"
            )

        print(f"[*] Text archive 0x{abs_off:X}: {len(offsets)} strings")

        for si, word_off in enumerate(offsets):
            if si > 382:
                break
            start = word_off * 2
            raw = decode_string(view, start)
            decoded = apply_replacements(raw, rules)

            # Later archive can overwrite earlier same string_index if needed,
            # matching your old scripts' behavior.
            names[si] = decoded

    return names


def main():
    if len(sys.argv) < 4:
        print("Usage: python export_d3_names.py D3.bin replace_map.csv names.csv")
        return

    bin_path = sys.argv[1]
    replace_map_path = sys.argv[2]
    out_csv = sys.argv[3]

    data = Path(bin_path).read_bytes()
    rules = load_replace_map(replace_map_path)

    print("[*] Extracting D3 names...")
    names = extract_names(data, rules)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["string_index", "name"])

        for si in sorted(names):
            writer.writerow([si, names[si]])

    print(f"[DONE] Exported {len(names)} names -> {out_csv}")


if __name__ == "__main__":
    main()