#!/usr/bin/env python3
"""
Extract partner Digimon stats from Digivice 25th Color (Digivice.bin).

Outputs CSV:

    string_index, DigimonName, Stage, Power, SlotIndex, OffsetHex

Where:
  - DigimonName is decoded from Digivice.bin text archive at off=0x194000/idx=0
  - Power is read from the 10-byte stats table found in Digivice.bin
  - Stage is currently set to 0 (unknown) because we have not yet
    located a trustworthy stage table in Digivice.bin.
  - SlotIndex is just the computed index in this stats list (1-based).

Usage:

    python export_digivice_data.py Digivice.bin replace_map.csv data.csv

`replace_map.csv` is the same style you used for D3: mapping tag-strings
like "<0041>" to human-readable letters. If you don't have/need it,
you can pass a dummy CSV and you'll get raw <XXXX> tags as names.
"""

import sys
import os
import csv
import struct
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

# -------------------------------------------------------------------
# CONFIG: Digivice-specific
# -------------------------------------------------------------------

# Text archive with Digivice Digimon names
ALLOWED_PATHS = {
    "off=0x194000/idx=0",
}

# Friend / partner stats table.
# This offset & layout were discovered by scanning Digivice.bin and
# matching your power sequence. Each entry is 10 bytes = 5×LE16 words.
#
# Record n is:
#   w0, w1, w2, w3, w4   (all 16-bit little-endian)
#
# We have a sliding relationship between records and the "partner slots":
#   For partner i:
#     string_index = record[i].w2
#     power        = record[i+1].w0
#
# Record 0 is a header / precursor, so the first *real* partner is i=0:
#   record[0].w2 -> string_index of first mon
#   record[1].w0 -> power of first mon
#
BASE_STATS   = 0x00097F2A   # first stats record we use (record index 0)
RECORD_SIZE  = 10           # bytes
NUM_PARTNERS = 112          # safe upper bound; we will trim when entries go invalid


# -------------------------------------------------------------------
# Little-endian helpers
# -------------------------------------------------------------------

def le16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]

def le32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


# -------------------------------------------------------------------
# Archive / text-archive parsing (same style as your D3 tools)
# -------------------------------------------------------------------

@dataclass
class ArchEntry:
    flags: int
    offset: int
    comp_len: int
    decomp_len: int

@dataclass
class Archive:
    base_off: int
    count: int
    entries: List[ArchEntry]
    data: bytes

@dataclass
class TextArchive:
    base_off: int
    path: str
    n_strings: int
    offsets_word: List[int]
    data: bytes


def is_probable_tama_archive(buf: bytes, abs_off: int) -> Optional[Archive]:
    if abs_off + 4 > len(buf):
        return None
    magic = le16(buf, abs_off)
    if magic != 0x3232:
        return None
    count = le16(buf, abs_off + 2)
    if not (1 <= count <= 65535):
        return None
    table_end = abs_off + 4 + count * 16
    if table_end > len(buf):
        return None

    entries: List[ArchEntry] = []
    for i in range(count):
        e_off = abs_off + 4 + i*16
        flags = le32(buf, e_off + 0)
        off   = le32(buf, e_off + 4)
        clen  = le32(buf, e_off + 8)
        dlen  = le32(buf, e_off + 12)
        if abs_off + off > len(buf):
            return None
        entries.append(ArchEntry(flags, off, clen, dlen))
    return Archive(abs_off, count, entries, buf)


def iter_all_archives(buf: bytes, max_depth: int = 3):
    """Walk BIN, yielding (path, Archive) for each archive and sub-archive."""
    tops: List[Tuple[str, Archive]] = []
    for off in range(0, len(buf)-4, 2):
        arc = is_probable_tama_archive(buf, off)
        if arc:
            tops.append((f"off=0x{off:X}", arc))

    queue: List[Tuple[str, Archive, int]] = [(p,a,0) for (p,a) in tops]
    while queue:
        path, arc, depth = queue.pop(0)
        yield path, arc
        if depth >= max_depth:
            continue
        for idx, e in enumerate(arc.entries):
            sub_off = arc.base_off + e.offset
            sub = is_probable_tama_archive(buf, sub_off)
            if sub:
                queue.append((f"{path}/idx={idx}", sub, depth+1))


def is_probable_text_archive(view: bytes) -> Optional[TextArchive]:
    if len(view) < 4:
        return None
    n = le16(view, 0)
    if not (1 <= n <= 20000):
        return None
    if 2 + 2*n > len(view):
        return None
    offs = [le16(view, 2 + 2*i) for i in range(n)]
    prev = 0
    for w in offs:
        if w < prev or w*2 >= len(view):
            return None
        prev = w
    # each string must have a 0 terminator within a reasonable bound
    for w in offs:
        p = w*2
        ok = False
        while p + 2 <= len(view) and p - (w*2) <= 4096:
            if le16(view, p) == 0:
                ok = True
                break
            p += 2
        if not ok:
            return None
    return TextArchive(base_off=0, path="", n_strings=n, offsets_word=offs, data=view)


def parse_text_archive(view: bytes, abs_off: int, path: str) -> Optional[TextArchive]:
    ta = is_probable_text_archive(view)
    if ta:
        ta.base_off = abs_off
        ta.path     = path
        return ta
    return None


def extract_text_archives(bin_bytes: bytes) -> List[TextArchive]:
    found: List[TextArchive] = []
    for path, arc in iter_all_archives(bin_bytes, 3):
        for idx, e in enumerate(arc.entries):
            if (e.flags & 0xF) != 0:   # skip compressed; same as your D3 tools
                continue
            abs_off = arc.base_off + e.offset
            length = e.decomp_len if e.decomp_len > 0 else e.comp_len
            if length <= 0:
                continue
            view = bin_bytes[abs_off:abs_off+length]
            ta = parse_text_archive(view, abs_off, f"{path}/idx={idx}")
            if ta:
                found.append(ta)
    return found


# -------------------------------------------------------------------
# Replace-map + name decoding
# -------------------------------------------------------------------

def load_replace_map(path: str):
    """
    CSV: from,to  (both can use \\uXXXX escapes; applied on decoded tag-string)
    """
    rules = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            src, dst = row
            try:
                src = bytes(src, "utf-8").decode("unicode_escape")
            except:
                pass
            try:
                dst = bytes(dst, "utf-8").decode("unicode_escape")
            except:
                pass
            if src:
                rules.append((src, dst))
    rules.sort(key=lambda x: len(x[0]), reverse=True)
    return rules


def apply_replacements(text: str, rules):
    for src, dst in rules:
        text = text.replace(src, dst)
    return text


def decode_string_as_tags(view: bytes, start_byte: int) -> str:
    """
    Minimal decoder: keep all non-control codes as <XXXX> tags.
    Control codes (>=0xF000) are ignored for now.
    """
    out = []
    p = start_byte
    while p + 2 <= len(view):
        w = le16(view, p)
        p += 2
        if w == 0:
            break
        if w >= 0xF000:
            # you can special-case 0xF000 as newline if needed
            continue
        out.append(f"<{w:04X}>")
    return "".join(out)


def build_name_map(bin_bytes: bytes, replace_rules) -> Dict[int, str]:
    """
    Extract Digimon names from Digivice.bin from off=0x194000/idx=0.
    Returns {string_index: decoded_name}.
    """
    name_map: Dict[int,str] = {}

    for ta in extract_text_archives(bin_bytes):
        if ta.path not in ALLOWED_PATHS:
            continue

        for si in range(ta.n_strings):
            start = ta.offsets_word[si] * 2
            raw   = decode_string_as_tags(ta.data, start)
            decoded = apply_replacements(raw, replace_rules)
            name_map[si] = decoded

    return name_map


# -------------------------------------------------------------------
# Digivice stats table decoding
# -------------------------------------------------------------------

def extract_partner_stats(bin_bytes: bytes):
    """
    Decode partner stats from the Digivice stats table.

    Layout (5×u16 per record):
        rec[i]:  w0, w1, w2, w3, w4

    Empirically, the best match to your expected power/name ordering is:

        partner i:
           string_index = rec[i].w2
           power        = rec[i+1].w0

    We stop when string_index or power become 0 or clearly invalid,
    or when we hit NUM_PARTNERS.
    """
    partners = []

    for i in range(NUM_PARTNERS):
        rec_i_off  = BASE_STATS + i * RECORD_SIZE
        rec_ip1_off = BASE_STATS + (i+1) * RECORD_SIZE
        if rec_ip1_off + RECORD_SIZE > len(bin_bytes):
            break

        si   = le16(bin_bytes, rec_i_off + 4)    # rec[i].w2
        pwr  = le16(bin_bytes, rec_ip1_off + 0)  # rec[i+1].w0
        off0 = rec_i_off

        # crude stop condition: string_index/power both zero → end
        if si == 0 and pwr == 0:
            break

        partners.append({
            "slot": i+1,             # 1-based
            "string_index": si,
            "stage": 0,              # UNKNOWN for now; better than wrong values
            "power": pwr,
            "offset_hex": f"0x{off0:06X}",
        })

    return partners


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    if len(sys.argv) < 4:
        print("Usage: python export_digivice_data.py Digivice.bin replace_map.csv data.csv")
        sys.exit(1)

    bin_path, repl_path, out_path = sys.argv[1:4]

    if not os.path.exists(bin_path):
        print(f"ERROR: BIN '{bin_path}' not found.")
        sys.exit(1)

    print(f"[*] Loading {bin_path} ...")
    with open(bin_path, "rb") as f:
        data = f.read()

    print("[*] Loading replace_map...")
    repl_rules = load_replace_map(repl_path)

    print("[*] Extracting Digimon names from Digivice text archive (off=0x194000/idx=0)...")
    names = build_name_map(data, repl_rules)
    print(f"    → {len(names)} string indices with names.")

    print("[*] Decoding partner stats table from Digivice.bin...")
    partners = extract_partner_stats(data)
    print(f"    → {len(partners)} partner stat rows decoded.")

    print(f"[*] Writing CSV → {out_path}")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["string_index", "DigimonName", "Stage", "Power", "SlotIndex", "OffsetHex"])
        for p in partners:
            si = p["string_index"]
            name = names.get(si, f"(string_index={si})")
            w.writerow([
                si,
                name,
                p["stage"],      # currently 0 (unknown)
                p["power"],
                p["slot"],
                p["offset_hex"],
            ])

    print(f"[*] Done. Wrote {len(partners)} rows.")


if __name__ == "__main__":
    main()
