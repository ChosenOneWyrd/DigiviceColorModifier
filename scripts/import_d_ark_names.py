#!/usr/bin/env python3
"""
Import edited D-Ark 25th Color strings/names from CSV.

Usage:
    python import_d_ark_names.py D-Ark.bin d_ark_names.csv replace_map.csv \
        --out D-Ark_out.bin

CSV:
    string_index,name

Modes:
    --mode skip      Skip rows that do not fit / cannot be encoded. (default)
    --mode error     Stop immediately on an invalid row or overflow.
    --mode truncate  Truncate encoded strings that exceed their fixed slot.

Safety:
- Uses the known D-Ark archive paths:
      (0x27E000, [0])
      (0x172000, [4, 0])
- Resolves/deduplicates them to one physical text archive.
- Never changes archive sizes or offset tables.
- Only writes rows whose decoded CSV value differs from the current BIN.
- Preserves 0xF000+ words because the exporter emits them as <XXXX> tags.
- Does not touch the D-Ark save blocks, Search Points, inventory, unlock flags,
  or firmware patches.
"""

import argparse
import csv
import os
import re
import struct
import sys
import tempfile
from pathlib import Path


TEXT_ARCHIVE_PATHS = [
    (0x27E000, [0]),
    (0x172000, [4, 0]),
]

RE_TAG = re.compile(r"<([0-9A-Fa-f]{4})>")


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

    for idx in path_indexes:
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


def locate_unique_text_archive(data):
    targets = {}

    for root_off, path_indexes in TEXT_ARCHIVE_PATHS:
        try:
            view, abs_off = get_entry_view(data, root_off, path_indexes)
        except Exception:
            continue

        offsets = parse_text_archive(view)

        if offsets is not None:
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


def string_capacity(view, offsets, string_index):
    start = offsets[string_index] * 2

    if string_index + 1 < len(offsets):
        end = offsets[string_index + 1] * 2
    else:
        end = len(view)

    capacity = end - start

    if capacity < 2 or capacity % 2:
        raise RuntimeError(
            f"Invalid capacity {capacity} for string {string_index}"
        )

    return capacity


def decode_raw_string(view, offsets, string_index):
    p = offsets[string_index] * 2
    out = []

    while p + 2 <= len(view):
        word = le16(view, p)
        p += 2

        if word == 0:
            break

        out.append(f"<{word:04X}>")

    return "".join(out)


def _decode_backslash_escapes(text):
    if "\\" not in text:
        return text

    try:
        return bytes(text, "utf-8").decode("unicode_escape")
    except Exception:
        return text


def load_replace_map(path):
    forward = []

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue

            src = _decode_backslash_escapes(row[0])
            dst = _decode_backslash_escapes(row[1])

            if src:
                forward.append((src, dst))

    forward.sort(key=lambda pair: len(pair[0]), reverse=True)

    inverse = [(dst, src) for src, dst in forward if dst]
    inverse.sort(key=lambda pair: len(pair[0]), reverse=True)

    return forward, inverse


def apply_replacements(text, rules):
    for src, dst in rules:
        text = text.replace(src, dst)
    return text


def encode_tag_string(text):
    """
    Encode a string consisting entirely of <XXXX> tags.

    Every 16-bit value except 0000 is allowed. 0000 is reserved as terminator.
    """
    words = []
    pos = 0

    while pos < len(text):
        match = RE_TAG.match(text, pos)

        if not match:
            snippet = text[pos:pos + 20]
            raise ValueError(
                f"unsupported literal text near {snippet!r}; "
                f"replace_map.csv did not convert it to <XXXX> codes"
            )

        value = int(match.group(1), 16)

        if value == 0:
            raise ValueError(
                "<0000> cannot appear inside a name because it is "
                "the string terminator"
            )

        words.append(value)
        pos = match.end()

    out = bytearray()

    for value in words:
        out += struct.pack("<H", value)

    out += b"\x00\x00"
    return bytes(out)


def truncate_encoded(encoded, capacity):
    """
    Truncate only at 16-bit boundaries and always keep the final 0000 terminator.
    """
    if capacity < 2:
        raise RuntimeError("String slot has no room for a terminator")

    payload_capacity = capacity - 2

    # encoded is word-aligned. Drop its original terminator and take as many
    # complete encoded words as fit.
    payload = encoded[:-2][:payload_capacity]
    payload = payload[:len(payload) - (len(payload) % 2)]

    return payload + b"\x00\x00"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin", help="input D-Ark BIN")
    ap.add_argument("csv", help="edited names CSV")
    ap.add_argument("replace_map", help="replace_map.csv")
    ap.add_argument("--out", required=True, help="output BIN")
    ap.add_argument(
        "--mode",
        choices=["error", "truncate", "skip"],
        default="skip",
    )
    args = ap.parse_args()

    src = Path(args.bin)
    dst = Path(args.out)

    data = bytearray(src.read_bytes())

    view, archive_base, offsets = locate_unique_text_archive(data)
    forward, inverse = load_replace_map(args.replace_map)

    print(
        f"[*] D-Ark text archive: 0x{archive_base:X} "
        f"({len(offsets)} strings)"
    )

    with open(args.csv, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise RuntimeError("CSV contains no data rows")

    if "string_index" not in rows[0] or "name" not in rows[0]:
        raise RuntimeError(
            "CSV must contain columns: string_index,name"
        )

    changes = 0
    unchanged = 0
    skipped = []

    # Refresh a read-only view from the original data. Since every string has
    # its own fixed slot, later writes cannot move other strings.
    original_view = memoryview(bytes(data))[archive_base:archive_base + len(view)]

    seen = set()

    for row_number, row in enumerate(rows, start=2):
        try:
            string_index = int(row["string_index"])
        except Exception:
            message = f"CSV row {row_number}: invalid string_index"
            if args.mode == "error":
                raise RuntimeError(message)
            skipped.append(message)
            continue

        if string_index in seen:
            message = (
                f"CSV row {row_number}: duplicate string_index "
                f"{string_index}"
            )
            if args.mode == "error":
                raise RuntimeError(message)
            skipped.append(message)
            continue

        seen.add(string_index)

        if not (0 <= string_index < len(offsets)):
            message = (
                f"CSV row {row_number}: string_index {string_index} "
                f"is outside 0..{len(offsets)-1}"
            )
            if args.mode == "error":
                raise RuntimeError(message)
            skipped.append(message)
            continue

        desired_display = row.get("name", "")

        current_raw = decode_raw_string(
            original_view,
            offsets,
            string_index,
        )
        current_display = apply_replacements(current_raw, forward)

        # Critical safety feature: importing an unedited exported CSV makes
        # no writes at all.
        if desired_display == current_display:
            unchanged += 1
            continue

        raw_text = apply_replacements(desired_display, inverse)

        try:
            encoded = encode_tag_string(raw_text)
        except ValueError as exc:
            message = (
                f"string {string_index}: cannot encode {desired_display!r}: "
                f"{exc}"
            )
            if args.mode == "error":
                raise RuntimeError(message)
            skipped.append(message)
            continue

        capacity = string_capacity(
            original_view,
            offsets,
            string_index,
        )

        if len(encoded) > capacity:
            if args.mode == "truncate":
                encoded = truncate_encoded(encoded, capacity)
            elif args.mode == "error":
                raise RuntimeError(
                    f"string {string_index}: encoded length "
                    f"{len(encoded)} exceeds capacity {capacity}"
                )
            else:
                skipped.append(
                    f"string {string_index}: encoded length "
                    f"{len(encoded)} exceeds capacity {capacity}"
                )
                continue

        write_off = (
            archive_base
            + offsets[string_index] * 2
        )

        # Clear the entire fixed string slot before writing the new value.
        # This is not required after a 0000 terminator, but makes the output
        # deterministic and removes stale bytes from a longer old name.
        data[write_off:write_off + capacity] = b"\x00" * capacity
        data[write_off:write_off + len(encoded)] = encoded

        changes += 1

    # ---------------------------------------------------------
    # Write output safely.
    #
    # The GUI intentionally passes the same BIN as both input
    # and output. Since the entire source BIN was already read
    # into memory above, in-place output is safe.
    #
    # For input == output, use an atomic temporary-file replace
    # so an interrupted write cannot leave a partial BIN.
    # ---------------------------------------------------------
    same_file = src.resolve() == dst.resolve()

    if same_file:
        dst.parent.mkdir(parents=True, exist_ok=True)

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{dst.name}.",
            suffix=".tmp",
            dir=str(dst.parent),
        )

        os.close(fd)
        temp_path = Path(temp_name)

        try:
            temp_path.write_bytes(data)

            # Sanity check before replacing the real BIN.
            if temp_path.stat().st_size != len(data):
                raise RuntimeError(
                    "Temporary BIN size does not match expected output size"
                )

            os.replace(temp_path, dst)

        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)


    print(f"[DONE] {changes} strings updated -> {dst}")
    print(f"[*] {unchanged} CSV rows were unchanged")

    if skipped:
        print()
        print(f"[WARN] {len(skipped)} rows were skipped:")
        for message in skipped:
            print(f"  - {message}")


if __name__ == "__main__":
    main()
