#!/usr/bin/env python3
"""
export_digivice_npc_names.py
Extract only the Digivice NPC names at the given string indexes.

CSV output:
    string_index, name

Usage:
    python export_digivice_npc_names.py Digivice.bin replace_map.csv npc_names.csv
"""

import sys, os, csv, struct, re
from typing import List, Dict, Optional, Tuple

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------

ALLOWED_PATHS = {"off=0x194000/idx=0"}

DIGIVICE_NPC_INDEXES = [
    95, 102, 106, 109, 112, 115, 118,
    139, 140, 141, 142, 143, 144, 145, 146,
    147, 148, 149, 150, 151, 152, 153, 154,
    155, 156, 157, 158, 159, 160, 161, 162,
    163, 164, 165, 166, 167, 168, 169, 170,
    171, 172, 173, 174, 175, 176, 177, 178,
    179, 180, 181
]

def le16(b, o): return struct.unpack_from("<H", b, o)[0]

# -------------------------------------------------------------------
# Archive parsing (same as your digivice tools)
# -------------------------------------------------------------------

def is_probable_tama_archive(buf, abs_off):
    if abs_off+4 > len(buf): return None
    if le16(buf, abs_off) != 0x3232: return None
    count = le16(buf, abs_off+2)
    if not (1 <= count <= 65535): return None
    table_end = abs_off + 4 + count*16
    if table_end > len(buf): return None

    entries=[]
    for i in range(count):
        e = abs_off+4+i*16
        flags = struct.unpack_from("<I", buf, e)[0]
        off   = struct.unpack_from("<I", buf, e+4)[0]
        clen  = struct.unpack_from("<I", buf, e+8)[0]
        dlen  = struct.unpack_from("<I", buf, e+12)[0]
        if abs_off+off > len(buf): return None
        entries.append((flags, off, clen, dlen))
    return abs_off, count, entries

def iter_archives(buf, depth=3):
    tops=[]
    for off in range(0, len(buf)-4, 2):
        arc=is_probable_tama_archive(buf, off)
        if arc: tops.append((f"off=0x{off:X}", arc))
    q=[(p,a,0) for (p,a) in tops]
    while q:
        path,arc,d=q.pop(0)
        yield path,arc
        if d>=depth: continue
        base,count,entries=arc
        for i,(flags,off,_,_) in enumerate(entries):
            sub=is_probable_tama_archive(buf, base+off)
            if sub: q.append((f"{path}/idx={i}", sub, d+1))

def is_text_archive(view):
    if len(view) < 4: return None
    n = le16(view, 0)
    if not (1 <= n <= 20000): return None
    if 2+2*n > len(view): return None
    offs=[le16(view,2+2*i) for i in range(n)]
    prev=0
    for w in offs:
        if w < prev or w*2 >= len(view): return None
        prev = w
    return n, offs

def extract_text_archives(buf):
    out=[]
    for path,arc in iter_archives(buf):
        base,count,entries = arc
        for idx,(flags,off,clen,dlen) in enumerate(entries):
            if (flags & 0xF) != 0: continue
            abs_off = base + off
            size = dlen if dlen>0 else clen
            if size<=0 or abs_off+size>len(buf): continue
            view = buf[abs_off:abs_off+size]
            ta=is_text_archive(view)
            if ta:
                out.append((f"{path}/idx={idx}", abs_off, view, ta[0], ta[1]))
    return out

# -------------------------------------------------------------------
# Replace-map utilities
# -------------------------------------------------------------------

def load_replace_map(path):
    rules=[]
    with open(path,"r",encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if len(r) < 2: continue
            a,b = r
            try: a = bytes(a,"utf-8").decode("unicode_escape")
            except: pass
            try: b = bytes(b,"utf-8").decode("unicode_escape")
            except: pass
            rules.append((a,b))
    rules.sort(key=lambda x: len(x[0]), reverse=True)
    return rules

def apply_replacements(text, rules):
    for a,b in rules: text = text.replace(a,b)
    return text

def decode_string(view, start):
    out=[]
    p=start
    while p+2 <= len(view):
        w = le16(view,p); p+=2
        if w==0: break
        if w >= 0xF000: continue
        out.append(f"<{w:04X}>")
    return "".join(out)

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    if len(sys.argv) < 4:
        print("Usage: python export_digivice_npc_names.py Digivice.bin replace_map.csv npc.csv")
        sys.exit(1)

    bin_path, repl_path, out_csv = sys.argv[1:4]

    with open(bin_path,"rb") as f: data = f.read()

    print("[*] Loading replace_map...")
    rules = load_replace_map(repl_path)

    print("[*] Searching text archives...")
    tas = extract_text_archives(data)

    name_ta = None
    for ta in tas:
        if ta[0] in ALLOWED_PATHS:
            name_ta = ta
            break

    if not name_ta:
        print("ERROR: Name archive not found.")
        sys.exit(1)

    path, base_off, view, n_strings, offsets = name_ta

    print("[*] Extracting NPC names...")

    with open(out_csv,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["string_index","name"])

        for si in DIGIVICE_NPC_INDEXES:
            if si >= n_strings:
                w.writerow([si,""])
                continue
            start = offsets[si]*2
            raw = decode_string(view,start)
            decoded = apply_replacements(raw, rules)
            w.writerow([si, decoded])

    print(f"[*] Done. Wrote {out_csv}.")

if __name__=="__main__":
    main()
