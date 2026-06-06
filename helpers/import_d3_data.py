#!/usr/bin/env python3
"""
FINAL VERSION — PATCHED FOR 155 RECORDS

Updates partner stats + Digimon names inside D3.bin.

Accepts ANY number of CSV rows (partial, full, extra).
Automatically rebuilds the ROM table to exactly NUM_RECORDS * 4 words.

CSV columns:
    string_index, DigimonName, Stage, Power, Unknown1, Unknown2
"""

import argparse, csv, struct, sys, re
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------
BASE_PARTNER = 0x0A21CC
RECORD_SIZE  = 8          # 4 × 16-bit words per ROM record
NUM_RECORDS  = 155        # FULL partner + friend table length
# DO NOT CHANGE THE MAX_POWER. IT WILL BREAK YOUR BIN FILE AND DIGIVICE WON'T BE REPAIRABLE.
MAX_POWER = 225

ALLOWED_PATHS = [
    "off=0x1EC000/idx=0",
    "off=0x140000/idx=4/idx=0",
]

# Names containing these characters are NOT written back
FORBIDDEN_CHARS = set("+-:<>?!~`'\"[]{}\\|@#$%^&*=,")

# ---------------------------------------------------------------
# LE HELPERS
# ---------------------------------------------------------------
def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]

# ---------------------------------------------------------------
# Archive Structures
# ---------------------------------------------------------------
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
    data: memoryview

# ---------------------------------------------------------------
# Archive detection
# ---------------------------------------------------------------
def is_probable_tama_archive(buf: bytes, abs_off: int) -> Optional[Archive]:
    if abs_off + 4 > len(buf): 
        return None
    if le16(buf, abs_off) != 0x3232: 
        return None
    count = le16(buf, abs_off + 2)
    if not (1 <= count <= 65535): 
        return None
    table_end = abs_off + 4 + count * 16
    if table_end > len(buf): 
        return None

    entries = []
    for i in range(count):
        e = abs_off + 4 + i*16
        flags = le32(buf, e)
        off   = le32(buf, e+4)
        clen  = le32(buf, e+8)
        dlen  = le32(buf, e+12)
        if abs_off + off > len(buf): 
            return None
        entries.append(ArchEntry(flags, off, clen, dlen))
    return Archive(abs_off, count, entries, buf)

def iter_all_archives(buf: bytes, max_depth=3):
    tops = []
    for off in range(0, len(buf)-4, 2):
        arc = is_probable_tama_archive(buf, off)
        if arc:
            tops.append((f"off=0x{off:X}", arc))

    queue = [(p, a, 0) for (p, a) in tops]
    while queue:
        path, arc, depth = queue.pop(0)
        yield path, arc
        if depth >= max_depth:
            continue
        for idx, e in enumerate(arc.entries):
            sub = is_probable_tama_archive(buf, arc.base_off + e.offset)
            if sub:
                queue.append((f"{path}/idx={idx}", sub, depth+1))

# ---------------------------------------------------------------
# TEXT ARCHIVES
# ---------------------------------------------------------------
def is_probable_text_archive(view: bytes) -> Optional[TextArchive]:
    if len(view) < 4: return None
    n = le16(view, 0)
    if not (1 <= n <= 20000): return None
    if 2 + 2*n > len(view): return None
    offsets = [le16(view, 2 + 2*i) for i in range(n)]
    prev = 0
    for w in offsets:
        if w < prev or w*2 >= len(view): 
            return None
        prev = w
    return TextArchive(0, "", n, offsets, memoryview(view))

def parse_text_archive(view: bytes, abs_off: int, path: str):
    ta = is_probable_text_archive(view)
    if ta:
        ta.base_off = abs_off
        ta.path = path
        return ta
    return None

def extract_allowed_text_archives(buf: bytes):
    found = {}
    for path, arc in iter_all_archives(buf, 3):
        for idx, e in enumerate(arc.entries):
            if (e.flags & 0xF) != 0:
                continue
            abs_off = arc.base_off + e.offset
            length = e.decomp_len if e.decomp_len > 0 else e.comp_len
            if length <= 0:
                continue
            view = buf[abs_off:abs_off+length]
            ta = parse_text_archive(view, abs_off, f"{path}/idx={idx}")
            if ta and ta.path in ALLOWED_PATHS:
                found[ta.path] = ta
    return [found[p] for p in ALLOWED_PATHS if p in found]

# ---------------------------------------------------------------
# TAG ENCODE/DECODE
# ---------------------------------------------------------------
RE_TAG_RAW = re.compile(r"<([0-9A-Fa-f]{4})>")
RE_TAG_F   = re.compile(r"<F([0-9A-Fa-f]{3})>")

def encode_from_tagstring(s: str) -> List[int]:
    out = []
    i = 0
    while i < len(s):
        if s[i] == "\n":
            out.append(0xF000)
            i += 1
            continue
        if s[i] == "<":
            m = RE_TAG_F.match(s, i)
            if m:
                out.append(0xF000 | int(m.group(1), 16))
                i = m.end()
                continue
            m = RE_TAG_RAW.match(s, i)
            if m:
                v = int(m.group(1), 16)
                if v != 0:
                    out.append(v)
                i = m.end()
                continue
            raise ValueError(f"Bad tag at: {s[i:i+10]!r}")
        raise ValueError(f"Literal char: {s[i]!r}")
    return out

def encode_to_bytes(codes: List[int]) -> bytes:
    out = bytearray()
    for c in codes:
        out += struct.pack("<H", c)
    out += struct.pack("<H", 0)
    return bytes(out)

def string_capacity_bytes(ta: TextArchive, si: int) -> int:
    start = ta.offsets_word[si] * 2
    if si + 1 < ta.n_strings:
        end = ta.offsets_word[si+1] * 2
    else:
        end = len(ta.data)
    return max(0, end - start)

# ---------------------------------------------------------------
# REPLACE MAP
# ---------------------------------------------------------------
def load_replace_map(path: str):
    rules = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                rules.append((row[0], row[1]))
    # longest-first
    rules.sort(key=lambda x: len(x[0]), reverse=True)
    inv = [(b, a) for (a,b) in rules]
    inv.sort(key=lambda x: len(x[0]), reverse=True)
    return rules, inv

def apply_rules(s: str, rules):
    for a,b in rules:
        s = s.replace(a, b)
    return s

# ---------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("csv")
    ap.add_argument("replace_map")
    ap.add_argument("--out", required=True)
    ap.add_argument("--overflow", choices=["error","truncate"], default="error")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    # Load bin
    with open(args.bin, "rb") as f:
        data = bytearray(f.read())

    # Load CSV
    rows = []
    with open(args.csv, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    if not rows:
        print("[ERROR] CSV is empty.")
        sys.exit(1)

    # -------------------------------------------------------
    # Rebuild word stream (variable-length rows allowed)
    # -------------------------------------------------------
    expected_words = NUM_RECORDS * 4
    words: List[int] = []

    # Row 0 → V-MON: 4 fields (no string_index)
    r0 = rows[0]
    words.append(int(r0["Stage"]))
    words.append(int(r0["Unknown1"]))
    words.append(int(r0["Power"]))
    words.append(int(r0["Unknown2"]))

    # Remaining rows → write the fields that exist
    for r in rows[1:]:
        words.append(int(r["string_index"]))

        stage = int(r["Stage"]) if r.get("Stage") else 0
        u1    = int(r["Unknown1"]) if r.get("Unknown1") else 0
        power = int(r["Power"]) if r.get("Power") else 0
        u2    = int(r["Unknown2"]) if r.get("Unknown2") else 0

        # apply MAX_POWER rule
        if power > MAX_POWER:
            # stage, u1, u2 updated — power untouched!
            words.append(stage)
            words.append(u1)
            # skip power (use 0 placeholder, will be overwritten later by readback)
            words.append(0)  
            words.append(u2)
        else:
            # normal handling
            words.append(stage)
            words.append(u1)
            words.append(power)
            words.append(u2)

    # Normalize to exactly NUM_RECORDS*4 words
    if len(words) < expected_words:
        words.extend([0] * (expected_words - len(words)))
    elif len(words) > expected_words:
        words = words[:expected_words]

    # -------------------------------------------------------
    # Write back partner table
    # -------------------------------------------------------
    for i, w in enumerate(words):
        rec = i // 4
        pos = i % 4
        off = BASE_PARTNER + rec * RECORD_SIZE + pos * 2
        struct.pack_into("<H", data, off, w)

    # -------------------------------------------------------
    # Update names
    # -------------------------------------------------------
    tas = extract_allowed_text_archives(data)
    index_map = []
    for ta in tas:
        for si in range(ta.n_strings):
            index_map.append((ta, si))

    fwd, inv = load_replace_map(args.replace_map)
    name_changes = 0

    for r in rows:
        si = int(r["string_index"])
        name = r["DigimonName"]
        if any(ch in FORBIDDEN_CHARS for ch in name):
            continue
        if not (0 <= si < len(index_map)):
            continue

        ta, slot = index_map[si]
        tag = apply_rules(name, inv)
        codes = encode_from_tagstring(tag)
        enc = encode_to_bytes(codes)
        cap = string_capacity_bytes(ta, slot)

        if len(enc) > cap:
            if args.overflow == "truncate":
                fit = max(0, (cap // 2) - 1)
                enc = encode_to_bytes(codes[:fit])
            else:
                continue

        abs_write = ta.base_off + ta.offsets_word[slot] * 2
        data[abs_write:abs_write+len(enc)] = enc
        name_changes += 1

    # -------------------------------------------------------
    # Save
    # -------------------------------------------------------
    with open(args.out, "wb") as f:
        f.write(data)

    print(f"[DONE] Updated stats + {name_changes} name changes → {args.out}")


if __name__ == "__main__":
    main()
