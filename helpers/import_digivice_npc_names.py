#!/usr/bin/env python3
"""
import_digivice_npc_names.py
Safely import Digivice NPC names using replace_map reverse rules.

CSV input:
    string_index, name

Rules:
    • Forbidden characters → skip
    • Encoded byte-length must match original → skip
    • Only Digivice NPC string indexes are modified
    • No stats updated here

Usage:
    python import_digivice_npc_names.py Digivice.bin npc.csv replace_map.csv --out Digivice.bin
"""

import sys, os, csv, struct, re

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

FORBIDDEN_CHARS = set("+-:<>?!~`'\"[]{}\\|@#$%^&*=,")

def le16(b,o): return struct.unpack_from("<H", b, o)[0]
def pack16(v): return struct.pack("<H", v)

# ---------------- Text/archive helpers (same as export script) ---------------- #

def is_probable_tama_archive(buf, abs_off):
    if abs_off+4 > len(buf): return None
    if le16(buf,abs_off)!=0x3232: return None
    count=le16(buf,abs_off+2)
    if not (1<=count<=65535): return None
    table_end = abs_off+4+count*16
    if table_end > len(buf): return None

    entries=[]
    for i in range(count):
        e=abs_off+4+i*16
        flags=struct.unpack_from("<I",buf,e)[0]
        off  =struct.unpack_from("<I",buf,e+4)[0]
        clen =struct.unpack_from("<I",buf,e+8)[0]
        dlen =struct.unpack_from("<I",buf,e+12)[0]
        if abs_off+off > len(buf): return None
        entries.append((flags,off,clen,dlen))
    return abs_off,count,entries

def iter_archives(buf, depth=3):
    tops=[]
    for off in range(0,len(buf)-4,2):
        arc=is_probable_tama_archive(buf,off)
        if arc: tops.append((f"off=0x{off:X}",arc))
    q=[(p,a,0) for (p,a) in tops]
    while q:
        path,arc,d=q.pop(0)
        yield path,arc
        if d>=depth: continue
        base,count,entries=arc
        for i,(flags,off,_,_) in enumerate(entries):
            sub=is_probable_tama_archive(buf,base+off)
            if sub: q.append((f"{path}/idx={i}",sub,d+1))

def is_text_archive(view):
    if len(view)<4: return None
    n=le16(view,0)
    if not (1<=n<=20000): return None
    if 2+2*n > len(view): return None
    offs=[le16(view,2+2*i) for i in range(n)]
    prev=0
    for w in offs:
        if w<prev or w*2 >= len(view): return None
        prev=w
    return n,offs

def extract_text_archives(buf):
    out=[]
    for path,arc in iter_archives(buf):
        base,count,entries=arc
        for idx,(flags,off,clen,dlen) in enumerate(entries):
            if (flags & 0xF)!=0: continue
            abs_off = base+off
            size = dlen if dlen>0 else clen
            if size<=0 or abs_off+size>len(buf): continue
            view=buf[abs_off:abs_off+size]
            ta=is_text_archive(view)
            if ta: out.append((f"{path}/idx={idx}",abs_off,view,ta[0],ta[1]))
    return out

# ---------------- Replace-map / encoding ---------------- #

def load_reverse_replace_map(path):
    rules=[]
    with open(path,"r",encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if len(r)>=2:
                try: src=bytes(r[0],"utf-8").decode("unicode_escape")
                except: src=r[0]
                try: dst=bytes(r[1],"utf-8").decode("unicode_escape")
                except: dst=r[1]
                rules.append((dst,src))
    rules.sort(key=lambda x: len(x[0]), reverse=True)
    return rules

TAG_RAW = re.compile(r"<([0-9A-Fa-f]{4})>")

def apply_reverse(text,rules):
    for a,b in rules: text=text.replace(a,b)
    return text

def encode_tagstring_to_bytes(tag_str):
    codes=[]
    i=0
    while i<len(tag_str):
        if tag_str[i]=="<":
            m=TAG_RAW.match(tag_str,i)
            if not m: raise ValueError(f"Bad tag {tag_str[i:i+6]}")
            codes.append(int(m.group(1),16))
            i=m.end()
        else:
            raise ValueError("Literal chars not allowed")
    out=bytearray()
    for c in codes: out+=pack16(c)
    out+=pack16(0)
    return bytes(out)

# ---------------- Main ---------------- #

def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("csv")
    ap.add_argument("replace_map")
    ap.add_argument("--out",required=True)
    args=ap.parse_args()

    # Load BIN
    with open(args.bin,"rb") as f:
        data = bytearray(f.read())

    # Load CSV
    rows=[]
    with open(args.csv,"r",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): rows.append(r)

    rules = load_reverse_replace_map(args.replace_map)

    # Find text archive
    tas = extract_text_archives(data)
    name_ta = None
    for ta in tas:
        if ta[0] in ALLOWED_PATHS:
            name_ta = ta
            break

    if not name_ta:
        print("ERROR: Name archive not found!")
        sys.exit(1)

    _, base_off, view, n_strings, offsets = name_ta

    updated = 0

    for r in rows:
        si = int(r["string_index"])
        if si not in DIGIVICE_NPC_INDEXES: continue
        if si >= n_strings: continue

        new_name = r["name"]

        # forbidden chars
        if any(c in FORBIDDEN_CHARS for c in new_name):
            continue

        old_start = offsets[si]*2
        old_end   = offsets[si+1]*2 if si+1 < n_strings else len(view)
        old_bytes = view[old_start:old_end]

        new_tag = apply_reverse(new_name, rules)
        try:
            enc = encode_tagstring_to_bytes(new_tag)
        except:
            continue

        if len(enc) != len(old_bytes):
            # skip if length mismatch
            continue

        # apply patch
        abs_pos = base_off + old_start
        data[abs_pos:abs_pos+len(enc)] = enc
        updated += 1

    with open(args.out,"wb") as f:
        f.write(data)

    print(f"[DONE] Updated NPC names: {updated}")
    print(f"Saved → {args.out}")

if __name__=="__main__":
    main()
