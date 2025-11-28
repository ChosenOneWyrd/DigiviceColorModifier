#!/usr/bin/env python3
"""
Export NPC names from D3.bin into a CSV.

Output columns:
    string_index, name

Only extracts names for the NPC string indexes defined in DTHREE_STRING_INDEXES.
"""

import sys, csv, struct, re
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict

# ------------------------------------------------------------
# NPC STRING INDEX LIST
# ------------------------------------------------------------
DTHREE_STRING_INDEXES = [
    136, 144, 151, 155, 187, 302, 303, 304, 305, 306, 307, 308, 309,
    310, 311, 212, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222,
    223, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235,
    236, 237, 238, 239, 240, 241, 242, 243, 244, 245, 246, 247, 248,
    249, 250, 251, 252, 253, 254
]

ALLOWED_PATHS = {
    "off=0x1EC000/idx=0",
    "off=0x140000/idx=4/idx=0",
}

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]

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

# ------------------------------------------------------------
# Archive detection (same as your original script)
# ------------------------------------------------------------
def is_probable_tama_archive(buf, abs_off):
    if abs_off + 4 > len(buf): return None
    if le16(buf, abs_off) != 0x3232: return None
    count = le16(buf, abs_off+2)
    if not (1 <= count <= 65535): return None
    table_end = abs_off + 4 + count * 16
    if table_end > len(buf): return None

    entries=[]
    for i in range(count):
        e = abs_off+4+i*16
        flags = le32(buf,e)
        off   = le32(buf,e+4)
        clen  = le32(buf,e+8)
        dlen  = le32(buf,e+12)
        if abs_off+off > len(buf): return None
        entries.append(ArchEntry(flags,off,clen,dlen))
    return Archive(abs_off,count,entries,buf)

def iter_all_archives(buf, max_depth=3):
    tops=[]
    for off in range(0,len(buf)-4,2):
        arc=is_probable_tama_archive(buf,off)
        if arc: tops.append((f"off=0x{off:X}",arc))
    q=[(p,a,0) for (p,a) in tops]
    while q:
        path,arc,depth=q.pop(0)
        yield path,arc
        if depth>=max_depth: continue
        for idx,e in enumerate(arc.entries):
            sub=is_probable_tama_archive(buf,arc.base_off+e.offset)
            if sub:
                q.append((f"{path}/idx={idx}",sub,depth+1))

# ------------------------------------------------------------
# Text archive decode
# ------------------------------------------------------------
def is_probable_text_archive(view):
    if len(view)<4: return None
    n=le16(view,0)
    if not (1<=n<=20000): return None
    if 2+2*n>len(view): return None
    offs=[le16(view,2+2*i) for i in range(n)]
    prev=0
    for w in offs:
        if w<prev or w*2>=len(view): return None
        prev=w
    return TextArchive(0,"",n,offs,view)

def parse_text_archive(view, abs_off, path):
    ta=is_probable_text_archive(view)
    if ta:
        ta.base_off=abs_off
        ta.path=path
        return ta
    return None

# ------------------------------------------------------------
# Replacement map
# ------------------------------------------------------------
def load_replace_map(path):
    rules=[]
    with open(path,"r",encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row)>=2:
                rules.append((row[0],row[1]))
    rules.sort(key=lambda x:len(x[0]),reverse=True)
    return rules

def apply_replacements(txt,rules):
    for a,b in rules:
        txt = txt.replace(a,b)
    return txt

# ------------------------------------------------------------
# String decode
# ------------------------------------------------------------
def decode_string(view,start):
    out=[]
    p=start
    while p+2<=len(view):
        w=le16(view,p)
        p+=2
        if w==0: break
        if w>=0xF000: continue
        out.append(f"<{w:04X}>")
    return "".join(out)

# ------------------------------------------------------------
# Extract NPC names
# ------------------------------------------------------------
def extract_names(bin_bytes, rules):
    names={}
    for path,arc in iter_all_archives(bin_bytes,3):
        for idx,e in enumerate(arc.entries):
            if (e.flags&0xF)!=0: continue
            abs_off = arc.base_off+e.offset
            length = e.decomp_len if e.decomp_len>0 else e.comp_len
            if length<=0: continue
            view = bin_bytes[abs_off:abs_off+length]
            ta=parse_text_archive(view,abs_off,f"{path}/idx={idx}")
            if not ta: continue
            if ta.path not in ALLOWED_PATHS: continue

            # scan only NPC indexes
            for si in DTHREE_STRING_INDEXES:
                if si < ta.n_strings:
                    start = ta.offsets_word[si]*2
                    raw = decode_string(ta.data,start)
                    decoded = apply_replacements(raw,rules)
                    names[si] = decoded

    return names

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    if len(sys.argv)<4:
        print("Usage: python export_d3_npc_names.py D3.bin replace_map.csv npc_names.csv")
        return

    bin_path, repl_path, out_csv = sys.argv[1:4]

    with open(bin_path,"rb") as f:
        data=f.read()

    rules=load_replace_map(repl_path)
    names=extract_names(data,rules)

    with open(out_csv,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["string_index","name"])
        for si in DTHREE_STRING_INDEXES:
            nm = names.get(si,"")
            w.writerow([si,nm])

    print(f"[DONE] Exported {len(DTHREE_STRING_INDEXES)} NPC names → {out_csv}")

if __name__=="__main__":
    main()
