"""Native-directory local repacking. Never allocate at the far end of flash."""
import hashlib
import struct
import zlib

PACK=0x140000
ORIGINAL_BASE=0x1B3000
LOCAL_BASE=0x1B2200
META=0x1B21C0
SPRITES=0x1EF000
MAGIC=b'D3LOCAL-COW-v1\0\0'
PROBE_ARCHIVE_SHA256='44acd18a2affcd766bcfb15a2e07e229d12396ddc56e5344c825e5d7dce58a13'

def entry(data,index):
    p=PACK+8+index*16
    off,size,payload=struct.unpack_from('<3I',data,p)
    start=PACK+off
    if not 0<off or not 0<size or start+size>len(data):
        raise ValueError('Invalid D3 resource directory')
    return p,start,size,payload

def archive_base(data):
    if len(data)!=0x2000000 or not data.startswith(b'GP-SPIF-HEADER'):
        raise ValueError('Expected a compatible 32 MiB D3 BIN')
    base=entry(data,3)[1]
    if base not in (ORIGINAL_BASE,LOCAL_BASE):
        raise ValueError('Unsupported archive location')
    if struct.unpack_from('<I',data,base)[0]!=42:
        raise ValueError('Unsupported archive header. For the old far-expanded BIN, use the working B recovery BIN instead.')
    return base

def archive_end(data,starts):
    base=archive_base(data)
    end=starts[-1]+2
    neighbor=entry(data,4)[1]
    if (len(starts)!=21 or starts!=sorted(starts) or starts[0]!=base+84
        or not starts[0]<=starts[-2]<starts[-1]<end<=neighbor
        or data[starts[-2]:end]!=b'\0\0\x43\0'):
        raise ValueError('Invalid local archive section bounds')
    return end

def sections(data):
    base=archive_base(data)
    starts=[base+x*2 for x in struct.unpack_from('<21I',data,base)]
    end=archive_end(data,starts)
    return list(zip(starts,starts[1:]+[end]))

def guard_digest(data,archive):
    h=hashlib.sha256(data[:PACK])
    # New copies are introduced only through layout roots and decoded commands.
    # If another tool changes other reference tables or code, protect all IDs.
    for i in sorted(set(range(21))-{2,5,13,14}):
        h.update(data[slice(*archive.sections[i])])
    return h.digest()

def protected_counts(data,archive):
    nr,ne=len(archive.records),len(archive.definitions)
    raw=bytes(data[META:META+64]);guard=guard_digest(data,archive)
    if raw[:16]==MAGIC:
        if struct.unpack_from('<I',raw,60)[0]!=zlib.crc32(raw[:60]) or struct.unpack_from('<I',raw,56)[0]!=1:
            raise ValueError('Damaged local edit metadata')
        r,e=struct.unpack_from('<II',raw,16)
        if not 0<r<=nr or not 0<=e<=ne:raise ValueError('Invalid protected ID counts')
        if raw[24:56]!=guard:r,e=nr,ne
    elif raw==b'\xff'*64:
        base=archive_base(data)
        digest=hashlib.sha256(data[base:archive.sections[-1][1]]).hexdigest()
        # Exact archive identity of the device-confirmed B probe; asset edits
        # outside the archive do not invalidate its established provenance.
        r,e=(2374,98) if digest==PROBE_ARCHIVE_SHA256 else (nr,ne)
    else:
        raise ValueError('Local metadata area is occupied; no changes made')
    if any(t>=r for i in range(r) for t in archive.calls[i]):r=nr
    if any(t>=e for i in range(r) for t in archive.effects[i]):e=ne
    return r,e,guard

def metadata(r,e,guard):
    raw=MAGIC+struct.pack('<II',r,e)+guard+struct.pack('<I',1)
    return raw+struct.pack('<I',zlib.crc32(raw))

def allocation(data,archive,new_size):
    base=archive_base(data);end=archive.sections[-1][1]
    _,prev,ps,_=entry(data,2)
    p3,_,_,_=entry(data,3)
    p4,neighbor,size,payload=entry(data,4)
    if prev+ps>META or entry(data,5)[1]!=SPRITES or neighbor<0x1EC000 or neighbor+size>SPRITES:
        raise ValueError('Unsupported neighboring resource layout')
    for s,e in [(LOCAL_BASE,base),(end,neighbor),(neighbor+size,SPRITES)]:
        if data[s:e]!=b'\xff'*(e-s):raise ValueError('Local resource padding is occupied; no changes made')
    new_end=LOCAL_BASE+new_size
    new_neighbor=max(0x1EC000,(new_end+255)&~255)
    if new_neighbor+size>SPRITES:
        raise RuntimeError(f'Active isolated edits exceed local capacity by {new_neighbor+size-SPRITES} bytes after compaction. No changes made.')
    return dict(base=LOCAL_BASE,body_start=LOCAL_BASE+84,new_end=new_end,
                neighbor_start=neighbor,neighbor_size=size,neighbor_payload=payload,
                new_neighbor=new_neighbor,p3=p3,p4=p4,
                write_spans=[(META,META+64),(LOCAL_BASE,SPRITES),(p3,p3+12),(p4,p4+12)])

def verify_unchanged_outside(original,output,spans):
    if len(original)!=len(output):raise RuntimeError('BIN size changed')
    cursor=0
    for start,end in sorted(spans):
        if not 0<=start<=end<=len(original):raise RuntimeError('Invalid write span')
        if original[cursor:start]!=output[cursor:start]:raise RuntimeError('Unrelated bytes changed')
        cursor=max(cursor,end)
    if original[cursor:]!=output[cursor:]:raise RuntimeError('Unrelated bytes changed')

def repack(original,parts,storage,meta):
    p=storage;header=bytearray(84);body=bytearray()
    for i,part in enumerate(parts):
        if len(part)%2:raise RuntimeError('Unaligned archive section')
        struct.pack_into('<I',header,i*4,(84+len(body))//2);body+=part
    if LOCAL_BASE+len(header)+len(body)!=p['new_end']:raise RuntimeError('Archive size mismatch')
    out=bytearray(original)
    out[LOCAL_BASE:SPRITES]=b'\xff'*(SPRITES-LOCAL_BASE)
    out[LOCAL_BASE:p['new_end']]=header+body
    s=p['neighbor_start'];size=p['neighbor_size'];dest=p['new_neighbor']
    out[dest:dest+size]=original[s:s+size]
    struct.pack_into('<3I',out,p['p3'],LOCAL_BASE-PACK,len(header)+len(body),len(header)+len(body))
    struct.pack_into('<3I',out,p['p4'],dest-PACK,size,p['neighbor_payload'])
    out[META:META+64]=meta
    verify_unchanged_outside(original,out,p['write_spans'])
    if out[dest:dest+size]!=original[s:s+size]:raise RuntimeError('Neighboring resource changed')
    if out[SPRITES:]!=original[SPRITES:]:raise RuntimeError('Sprite/audio/tail data changed')
    return out
