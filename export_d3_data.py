#!/usr/bin/env python3
"""
Extract partner Digimon stats AND resolve Digimon names directly from D3.bin.

Final output columns:
    string_index, DigimonName, Stage, Power, Unknown1, Unknown2
"""

import sys
import os
import csv
import struct
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

# ------------------ Partner table constants ------------------
BASE_PARTNER = 0x0A21CC
RECORD_SIZE  = 8
NUM_RECORDS  = 155

# First partner (V-MON) has no string_index stored; hardcode from names table
V_MON_STRING_INDEX = 2

# ------------------ Allowed text archive paths ------------------
ALLOWED_PATHS = {
    "off=0x1EC000/idx=0",
    "off=0x140000/idx=4/idx=0",
}

# ------------------ LE helpers ------------------
def le16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]

def le32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]

# ------------------ Archive structures ------------------
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

# ------------------ Archive detection ------------------
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

    entries = []
    for i in range(count):
        e_off = abs_off + 4 + i * 16
        flags = le32(buf, e_off)
        off   = le32(buf, e_off + 4)
        clen  = le32(buf, e_off + 8)
        dlen  = le32(buf, e_off + 12)
        if abs_off + off > len(buf):
            return None
        entries.append(ArchEntry(flags, off, clen, dlen))
    return Archive(abs_off, count, entries, buf)

def iter_all_archives(buf: bytes, max_depth: int = 3):
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
            sub_off = arc.base_off + e.offset
            sub = is_probable_tama_archive(buf, sub_off)
            if sub:
                queue.append((f"{path}/idx={idx}", sub, depth+1))

# ------------------ Text archive detection ------------------
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
        if w < prev or w * 2 >= len(view):
            return None
        prev = w
    for w in offs:
        p = w * 2
        ok = False
        while p + 2 <= len(view) and p - (w * 2) <= 4096:
            if le16(view, p) == 0:
                ok = True
                break
            p += 2
        if not ok:
            return None
    return TextArchive(0, "", n, offs, view)

def parse_text_archive(view: bytes, abs_off: int, path: str):
    ta = is_probable_text_archive(view)
    if ta:
        ta.base_off = abs_off
        ta.path = path
        return ta
    return None

# ------------------ Replace-map ------------------
def load_replace_map(path: str):
    rules = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            src, dst = row
            try: src = bytes(src, "utf-8").decode("unicode_escape")
            except: pass
            try: dst = bytes(dst, "utf-8").decode("unicode_escape")
            except: pass
            if src:
                rules.append((src, dst))
    rules.sort(key=lambda x: len(x[0]), reverse=True)
    return rules

def apply_replacements(text: str, rules):
    for src, dst in rules:
        text = text.replace(src, dst)
    return text

# ------------------ Name decoding ------------------
def decode_string(view: bytes, start_byte: int) -> str:
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

def extract_names(bin_bytes: bytes, repl_rules):
    name_map = {}
    for path, arc in iter_all_archives(bin_bytes, 3):
        for idx, e in enumerate(arc.entries):
            if (e.flags & 0xF) != 0:
                continue
            abs_off = arc.base_off + e.offset
            length = e.decomp_len if e.decomp_len > 0 else e.comp_len
            if length <= 0:
                continue
            view = bin_bytes[abs_off:abs_off+length]
            ta = parse_text_archive(view, abs_off, f"{path}/idx={idx}")
            if not ta:
                continue
            if ta.path not in ALLOWED_PATHS:
                continue
            for si in range(ta.n_strings):
                start_byte = ta.offsets_word[si] * 2
                raw = decode_string(ta.data, start_byte)
                decoded = apply_replacements(raw, repl_rules)
                name_map[si] = decoded
    return name_map

# ------------------ Partner table decode ------------------
def extract_partner_words(bin_bytes: bytes):
    words = []
    for i in range(NUM_RECORDS):
        off = BASE_PARTNER + i * RECORD_SIZE
        for j in range(0, RECORD_SIZE, 2):
            words.append(le16(bin_bytes, off + j))
    return words

def decode_partners(words):
    partners = []

    # V-MON first
    partners.append({
        "string_index": V_MON_STRING_INDEX,
        "stage":    words[0],
        "unknown1": words[1],
        "power":    words[2],
        "unknown2": words[3],
    })

    remaining = len(words) - 4
    num_more = remaining // 5
    for i in range(num_more):
        b = 4 + i*5
        partners.append({
            "string_index": words[b + 0],
            "stage":        words[b + 1],
            "unknown1":     words[b + 2],
            "power":        words[b + 3],
            "unknown2":     words[b + 4],
        })

    return partners

# ------------------ Main ------------------
def main():
    if len(sys.argv) < 4:
        print("Usage: python export_d3_data.py D3.bin replace_map.csv data.csv")
        return

    bin_path, repl_path, out_path = sys.argv[1:4]

    with open(bin_path, "rb") as f:
        data = f.read()

    print("[*] Extracting Digimon Data")
    print("[*] THIS CAN TAKE A WHILE...")
    repl = load_replace_map(repl_path)
    print("[*] Extracting Digimon names from text archives...")
    names = extract_names(data, repl)
    print("[*] Reading partner table words...")
    words = extract_partner_words(data)
    print("[*] Decoding partner records...")
    partners = decode_partners(words)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["string_index", "DigimonName", "Stage", "Power", "Unknown1", "Unknown2"])

        for p in partners:
            si = p["string_index"]
            name = names.get(si, f"(string_index={si})")
            w.writerow([
                si,
                name,
                p["stage"],
                p["power"],
                p["unknown1"],
                p["unknown2"]
            ])

    print(f"[*] Wrote {len(partners)} partner rows → {out_path}")


if __name__ == "__main__":
    main()
