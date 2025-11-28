#!/usr/bin/env python3
"""
Import NPC names back into D3.bin.

CSV format:
    string_index, name

Writes ONLY the NPC string indexes defined in DTHREE_STRING_INDEXES.

NEW RULE:
    The number of characters in the new name MUST match the character count
    of the old name (after decoding via replace_map). If not equal → skip.
"""

import sys, csv, struct, re
from dataclasses import dataclass
from typing import Optional, List, Dict

# ------------------------------------------------------------
# NPC STRING INDEXES
# ------------------------------------------------------------
DTHREE_STRING_INDEXES = [
    136, 144, 151, 155, 187, 302, 303, 304, 305, 306, 307, 308, 309,
    310, 311, 212, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222,
    223, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235,
    236, 237, 238, 239, 240, 241, 242, 243, 244, 245, 246, 247, 248,
    249, 250, 251, 252, 253, 254
]

ALLOWED_PATHS = [
    "off=0x1EC000/idx=0",
    "off=0x140000/idx=4/idx=0",
]

FORBIDDEN_CHARS = set("+-:<>?!~`'\"[]{}\\|@#$%^&*,")

# ------------------------------------------------------------
# Helpers / structures
# ------------------------------------------------------------
def le16(b,o): return struct.unpack_from("<H",b,o)[0]
def le32(b,o): return struct.unpack_from("<I",b,o)[0]

@dataclass
class ArchEntry:
    flags:int
    offset:int
    comp_len:int
    decomp_len:int

@dataclass
class Archive:
    base_off:int
    count:int
    entries:list
    data:bytes

@dataclass
class TextArchive:
    base_off:int
    path:str
    n_strings:int
    offsets_word:List[int]
    data:bytearray

def is_probable_tama_archive(buf,abs_off):
    if abs_off+4>len(buf): return None
    if le16(buf,abs_off)!=0x3232: return None
    c=le16(buf,abs_off+2)
    if not(1<=c<=65535): return None
    if abs_off+4+c*16>len(buf): return None
    ents=[]
    for i in range(c):
        e=abs_off+4+i*16
        fl=le32(buf,e)
        off=le32(buf,e+4)
        cl=le32(buf,e+8)
        dl=le32(buf,e+12)
        if abs_off+off>len(buf): return None
        ents.append(ArchEntry(fl,off,cl,dl))
    return Archive(abs_off,c,ents,buf)

def iter_all_archives(buf,max_depth=3):
    tops=[]
    for off in range(0,len(buf)-4,2):
        a=is_probable_tama_archive(buf,off)
        if a: tops.append((f"off=0x{off:X}",a))
    q=[(p,a,0) for p,a in tops]
    while q:
        path,arc,depth=q.pop(0)
        yield path,arc
        if depth>=max_depth: continue
        for idx,e in enumerate(arc.entries):
            sub=is_probable_tama_archive(buf,arc.base_off+e.offset)
            if sub:
                q.append((f"{path}/idx={idx}",sub,depth+1))

def is_probable_text_archive(view):
    if len(view)<4: return None
    n=le16(view,0)
    if not(1<=n<=20000): return None
    if 2+2*n>len(view): return None
    offs=[le16(view,2+2*i) for i in range(n)]
    prev=0
    for w in offs:
        if w<prev or w*2>=len(view): return None
        prev=w
    return TextArchive(0,"",n,offs,bytearray(view))

def parse_text_archive(view,abs_off,path):
    ta=is_probable_text_archive(view)
    if ta:
        ta.base_off=abs_off
        ta.path=path
        return ta
    return None

# ------------------------------------------------------------
# STRING DECODING
# ------------------------------------------------------------
def decode_old_string(ta, si, fwd_rules):
    """
    Decode existing old name → readable text using forward replace_map.
    Returns a simple visible-name string (e.g., "Vmon").
    """
    start = ta.offsets_word[si]*2
    p = start
    out_tags = []

    while p+2 <= len(ta.data):
        w = le16(ta.data, p)
        p += 2
        if w == 0:
            break
        if w >= 0xF000:
            continue
        out_tags.append(f"<{w:04X}>")

    # Convert tags → visible chars via forward replace_map
    raw = "".join(out_tags)
    # Apply all <####> → character
    for k,v in fwd_rules:
        raw = raw.replace(k, v)

    return raw  # visible, human characters

# ------------------------------------------------------------
# TAG ENCODING
# ------------------------------------------------------------
RE_RAW = re.compile(r"<([0-9A-Fa-f]{4})>")

def encode_to_codes(s: str):
    out=[]
    i=0
    while i<len(s):
        if s[i]=="<":
            m=RE_RAW.match(s,i)
            if not m:
                raise ValueError(f"Bad tag {s[i:i+6]}")
            v=int(m.group(1),16)
            if v!=0:
                out.append(v)
            i=m.end()
        else:
            raise ValueError(f"Literal char {s[i]!r} in encoded string")
    return out

def encode_bytes(codes):
    b=bytearray()
    for c in codes:
        b+=struct.pack("<H",c)
    b+=b"\x00\x00"
    return b

def string_capacity(ta,si):
    start=ta.offsets_word[si]*2
    if si+1<ta.n_strings:
        end=ta.offsets_word[si+1]*2
    else:
        end=len(ta.data)
    return end-start

# ------------------------------------------------------------
# Replace map
# ------------------------------------------------------------
def load_replace_map(path):
    fwd=[]
    inv=[]
    with open(path,"r",encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row)>=2:
                a,b=row
                fwd.append((a,b))   # <####> -> visible
                inv.append((b,a))   # visible -> <####>
    fwd.sort(key=lambda x:len(x[0]),reverse=True)
    inv.sort(key=lambda x:len(x[0]),reverse=True)
    return fwd,inv

def apply_rules(s,rules):
    for a,b in rules:
        s=s.replace(a,b)
    return s

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    if len(sys.argv)<5:
        print("Usage: python import_d3_npc_names.py D3.bin npc_names.csv replace_map.csv --out NEW.bin")
        return

    bin_path=sys.argv[1]
    csv_path=sys.argv[2]
    replace_map=sys.argv[3]
    out_path=sys.argv[-1]

    with open(bin_path,"rb") as f:
        data=bytearray(f.read())

    npc_map={}
    with open(csv_path,"r",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if "string_index" not in r or "name" not in r:
                continue
            try:
                si=int(r["string_index"])
            except:
                continue
            name=r["name"].strip()
            if si in DTHREE_STRING_INDEXES and name:
                npc_map[si]=name

    fwd,inv=load_replace_map(replace_map)
    changes=0

    for path,arc in iter_all_archives(data,3):
        for idx,e in enumerate(arc.entries):
            if (e.flags&0xF)!=0: continue
            abs_off=arc.base_off+e.offset
            length=e.decomp_len if e.decomp_len>0 else e.comp_len
            if length<=0: continue
            view=data[abs_off:abs_off+length]
            ta=parse_text_archive(view,abs_off,f"{path}/idx={idx}")
            if not ta: continue
            if ta.path not in ALLOWED_PATHS: continue

            for si,new_name in npc_map.items():
                if si>=ta.n_strings: 
                    continue

                # 1) FORBIDDEN_CHARS character check
                if any(c in FORBIDDEN_CHARS for c in new_name):
                    print(f"[WARN] string_index {si}: FORBIDDEN_CHARS characters in {new_name!r}. Skipping.")
                    continue

                # 2) Obtain old name (decoded)
                old_name = decode_old_string(ta, si, fwd)

                # 3) Character count comparison
                if len(new_name) != len(old_name):
                    print(f"[WARN] string_index {si}: name length mismatch "
                          f"(old={len(old_name)}, new={len(new_name)}). Skipping.")
                    continue

                # 4) Convert visible → <####> tags
                encoded = apply_rules(new_name, inv)

                try:
                    codes = encode_to_codes(encoded)
                except ValueError as e:
                    print(f"[WARN] string_index {si}: encode error for name "
                          f"{new_name!r} ({e}). Skipping.")
                    continue

                enc = encode_bytes(codes)
                cap = string_capacity(ta, si)

                if len(enc)>cap:
                    print(f"[WARN] string_index {si}: name too long for slot "
                          f"(capacity {cap} bytes). Skipping.")
                    continue

                write_off = ta.base_off + ta.offsets_word[si]*2
                data[write_off:write_off+len(enc)] = enc
                changes+=1

    with open(out_path,"wb") as f:
        f.write(data)

    print(f"[DONE] Applied {changes} NPC name changes → {out_path}")


if __name__=="__main__":
    main()
