#!/usr/bin/env python3
"""
Import names into Digivice.bin.

Usage:
    python import_digivice_names.py Digivice.bin digivice_names.csv replace_map.csv --out Digivice_out.bin

CSV format:
    string_index,name
"""

import argparse
import csv
import struct
import re
from pathlib import Path


TEXT_ARCHIVE_PATHS = [
    (0x100000, [5, 0]),
]

BASELINE_NAMES_CSV = "digivice_names_original.csv"

FORBIDDEN_CHARS = set("+-:<>?!~`'\"[]{}\\|@#$%^&*=,")


def le16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def le32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def read_archive(buf, base_off):
    if le16(buf, base_off) != 0x3232:
        return None

    count = le16(buf, base_off + 2)
    entries = []

    for i in range(count):
        eoff = base_off + 4 + i * 16
        entries.append({
            "flags": le32(buf, eoff),
            "offset": le32(buf, eoff + 4),
            "clen": le32(buf, eoff + 8),
            "dlen": le32(buf, eoff + 12),
        })

    return entries


def get_entry_view(buf, root, path):
    base = root

    for idx in path:
        arc = read_archive(buf, base)
        if arc is None:
            raise RuntimeError(f"Archive not found at 0x{base:X}")

        if idx >= len(arc):
            raise RuntimeError(f"Index {idx} out of range at archive 0x{base:X}")

        entry = arc[idx]
        base = base + entry["offset"]
        size = entry["dlen"] if entry["dlen"] > 0 else entry["clen"]

    return buf[base:base + size], base


def parse_text_archive(view):
    n = le16(view, 0)
    return [le16(view, 2 + 2 * i) for i in range(n)]


def string_capacity(view, offsets, si):
    start = offsets[si] * 2
    if si + 1 < len(offsets):
        end = offsets[si + 1] * 2
    else:
        end = len(view)
    return end - start


RE_TAG = re.compile(r"<([0-9A-Fa-f]{4})>")


def encode_name(name):
    codes = []
    i = 0

    while i < len(name):
        if name[i] == "<":
            m = RE_TAG.match(name, i)
            if not m:
                raise ValueError(f"Invalid tag at {name[i:i+10]}")
            codes.append(int(m.group(1), 16))
            i = m.end()
        else:
            raise ValueError(f"Invalid literal char: {name[i]}")

    out = bytearray()
    for c in codes:
        out += struct.pack("<H", c)

    out += b"\x00\x00"
    return bytes(out)


def load_replace_map(path):
    rules = []

    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                src, dst = row[0], row[1]

                try:
                    src = bytes(src, "utf-8").decode("unicode_escape")
                except Exception:
                    pass

                try:
                    dst = bytes(dst, "utf-8").decode("unicode_escape")
                except Exception:
                    pass

                rules.append((src, dst))

    rules.sort(key=lambda x: len(x[0]), reverse=True)

    inv = [(dst, src) for src, dst in rules]
    inv.sort(key=lambda x: len(x[0]), reverse=True)

    return inv


def apply_inverse(name, inv):
    for src, dst in inv:
        name = name.replace(src, dst)
    return name


def load_baseline_names(path):
    baseline = {}

    if not Path(path).is_file():
        return baseline

    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                baseline[int(row["string_index"])] = row["name"]
            except Exception:
                pass

    return baseline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("csv")
    ap.add_argument("replace_map")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["error", "truncate", "skip"], default="skip")
    args = ap.parse_args()

    data = bytearray(Path(args.bin).read_bytes())

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    baseline = load_baseline_names(BASELINE_NAMES_CSV)
    inv = load_replace_map(args.replace_map)

    archives = []
    for root, path in TEXT_ARCHIVE_PATHS:
        view, base = get_entry_view(data, root, path)
        offsets = parse_text_archive(view)
        archives.append((view, base, offsets))

    changes = 0
    skipped_forbidden = []
    skipped_overflow = []
    skipped_encode = []

    for r in rows:
        try:
            si = int(str(r["string_index"]).strip())
        except Exception:
            continue

        name = str(r.get("name", ""))

        if any(c in FORBIDDEN_CHARS for c in name):
            base_name = baseline.get(si)
            if base_name is None or name != base_name:
                skipped_forbidden.append(si)
            continue

        raw_name = apply_inverse(name, inv)

        try:
            enc = encode_name(raw_name)
        except Exception:
            skipped_encode.append(si)
            continue

        written = False

        for view, base, offsets in archives:
            if si >= len(offsets):
                continue

            cap = string_capacity(view, offsets, si)

            if len(enc) > cap:
                if args.mode == "truncate":
                    enc = enc[:cap - 2] + b"\x00\x00"
                elif args.mode == "error":
                    raise RuntimeError(f"Overflow at string_index {si}")
                else:
                    skipped_overflow.append(si)
                    written = True
                    break

            write_off = base + offsets[si] * 2
            data[write_off:write_off + len(enc)] = enc

            changes += 1
            written = True
            break

        if not written:
            skipped_encode.append(si)

    Path(args.out).write_bytes(data)

    if skipped_forbidden:
        print("\nOnly letters and numbers are allowed in names. These string_index values were skipped:")
        print(sorted(set(skipped_forbidden)))

    if skipped_overflow:
        print("\nThese string_index values were skipped because the edited name was too long:")
        print(sorted(set(skipped_overflow)))

    if skipped_encode:
        print("\nThese string_index values could not be encoded or found:")
        print(sorted(set(skipped_encode)))

    print(f"[DONE] {changes} names updated -> {args.out}")


if __name__ == "__main__":
    main()