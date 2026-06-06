#!/usr/bin/env python3
"""
Import names into D3.bin (FAST + SAFE).

Usage:
    python import_d3_names.py D3.bin d3_names.csv replace_map.csv --out D3_out.bin

CSV format:
    string_index,name
"""

import argparse, csv, struct, sys, re
from pathlib import Path

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
TEXT_ARCHIVE_PATHS = [
    (0x1EC000, [0]),
    (0x140000, [4, 0]),
]

FORBIDDEN_CHARS = set("+-:<>?!~`'\"[]{}\\|@#$%^&*=,")

# --------------------------------------------------
# LE helpers
# --------------------------------------------------
def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]

# --------------------------------------------------
# Archive reading (FAST)
# --------------------------------------------------
def read_archive(buf, base_off):
    if le16(buf, base_off) != 0x3232:
        return None

    count = le16(buf, base_off + 2)
    entries = []

    for i in range(count):
        eoff = base_off + 4 + i*16
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
        entry = arc[idx]

        base = base + entry["offset"]
        size = entry["dlen"] if entry["dlen"] > 0 else entry["clen"]

    return buf[base:base+size], base

# --------------------------------------------------
# Text archive parsing
# --------------------------------------------------
def parse_text_archive(view):
    n = le16(view, 0)
    offsets = [le16(view, 2 + 2*i) for i in range(n)]
    return offsets

def string_capacity(view, offsets, si):
    start = offsets[si] * 2
    if si + 1 < len(offsets):
        end = offsets[si+1] * 2
    else:
        end = len(view)
    return end - start

# --------------------------------------------------
# Encoding
# --------------------------------------------------
RE_TAG = re.compile(r"<([0-9A-Fa-f]{4})>")

def encode_name(name):
    codes = []
    i = 0

    while i < len(name):
        if name[i] == "<":
            m = RE_TAG.match(name, i)
            if not m:
                raise ValueError(f"Invalid tag at {name[i:i+10]}")
            val = int(m.group(1), 16)
            codes.append(val)
            i = m.end()
        else:
            raise ValueError(f"Invalid literal char: {name[i]}")

    out = bytearray()
    for c in codes:
        out += struct.pack("<H", c)
    out += b"\x00\x00"
    return bytes(out)

def decode_string(view, offsets, si):
    start = offsets[si] * 2
    out = []
    p = start

    while p + 2 <= len(view):
        w = le16(view, p)
        p += 2

        if w == 0:
            break

        if w >= 0xF000:
            continue

        out.append(f"<{w:04X}>")

    return "".join(out)

# --------------------------------------------------
# Replace map
# --------------------------------------------------
def load_replace_map(path):
    rules = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                rules.append((row[0], row[1]))

    rules.sort(key=lambda x: len(x[0]), reverse=True)
    inv = [(b, a) for (a, b) in rules]
    inv.sort(key=lambda x: len(x[0]), reverse=True)
    return inv

def apply_inverse(name, inv):
    for a, b in inv:
        name = name.replace(a, b)
    return name
# --------------------------------------------------
# MAIN
# --------------------------------------------------
def main():
    skipped_forbidden = []
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("csv")
    ap.add_argument("replace_map")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["error", "truncate", "skip"], default="skip")
    args = ap.parse_args()

    data = bytearray(Path(args.bin).read_bytes())

    # Load CSV
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig")))

    # Load baseline names
    baseline = {}
    with open("d3_names_original.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            baseline[int(r["string_index"])] = r["name"]

    # Load replace map
    inv = load_replace_map(args.replace_map)

    # Load text archives
    archives = []
    for root, path in TEXT_ARCHIVE_PATHS:
        view, base = get_entry_view(data, root, path)
        offsets = parse_text_archive(view)
        archives.append((view, base, offsets))

    changes = 0

    for r in rows:
        si = int(r["string_index"])
        name = r["name"]

        # forbidden chars
        if any(c in FORBIDDEN_CHARS for c in name):
            base_name = baseline.get(si)

            # Only report if user actually changed the name
            if base_name is None or name != base_name:
                skipped_forbidden.append(si)

            continue

        name = apply_inverse(name, inv)

        try:
            enc = encode_name(name)
        except:
            continue

        # find correct archive
        for view, base, offsets in archives:
            if si >= len(offsets):
                continue

            cap = string_capacity(view, offsets, si)

            if len(enc) > cap:
                if args.mode == "truncate":
                    enc = enc[:cap-2] + b"\x00\x00"
                elif args.mode == "error":
                    raise RuntimeError(f"Overflow at string {si}")
                else:
                    continue

            write_off = base + offsets[si] * 2
            data[write_off:write_off+len(enc)] = enc

            changes += 1
            break

    Path(args.out).write_bytes(data)
    if skipped_forbidden:
        skipped_forbidden = sorted(set(skipped_forbidden))
        print("\nOnly letters and numbers are allowed in names. So, names with these string_index were skipped during import:")
        print(skipped_forbidden)
    
    print(f"[DONE] {changes} names updated → {args.out}")

if __name__ == "__main__":
    main()