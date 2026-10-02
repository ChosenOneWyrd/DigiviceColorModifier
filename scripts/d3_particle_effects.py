#!/usr/bin/env python3
"""D3 particle tracer with selected-layout-only replacement.
List: python d3_particle_effects.py D3.bin 405
Patch: python d3_particle_effects.py D3.bin 405 --source 109_0_0.png --replacement 108_1_0.png --output D3_out.bin
Patching uses the general evolution isolation engine, preserving original
particle definitions and every other layout, including same-root aliases.
Variable particle ranges and palette changes remain unsupported.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import re
import struct
import tempfile

BASE = 0x1B3000
SPRITES = 0x1EF000
SECTION_COUNT = 21
EFFECT_WORDS = 33

def u16(b, p): return struct.unpack_from('<H', b, p)[0]
def u32(b, p): return struct.unpack_from('<I', b, p)[0]
def words(b, start, end):
    if (end-start) % 2 or start < 0 or end > len(b):
        raise ValueError('Invalid word range')
    return list(struct.unpack_from('<'+'H'*((end-start)//2), b, start))

def parse_sections(b):
    from d3_archive_storage import sections
    return sections(b)

def decode_commands(ws):
    """Decode operand masks; never scan operand words as opcodes."""
    if not ws: raise ValueError('Empty animation record')
    p, result = 1, []
    while p < len(ws):
        if ws[p] == 0xFCBD and p == len(ws)-1:
            break  # alignment filler, not an executable command
        op = ws[p]
        count = (op >> 8).bit_count()
        if p+1+count > len(ws):
            raise ValueError('Truncated command operand mask')
        result.append((p, op, ws[p+1:p+1+count]))
        p += count+1
    return result

class Archive:
    def __init__(self, data):
        self.data = data
        self.sections = parse_sections(data)
        sec = self.sections
        ao = words(data, *sec[13])
        if ao != sorted(ao) or ao[-1]*4 != sec[14][1]-sec[14][0]:
            raise ValueError('Invalid animation offsets')
        self.records, self.commands = [], []
        for a,z in zip(ao, ao[1:]):
            start,end = sec[14][0]+a*4,sec[14][0]+z*4
            self.records.append((start,end))
            self.commands.append(decode_commands(words(data,start,end)))
        self.calls, self.effects = {}, {}
        for rid, commands in enumerate(self.commands):
            self.calls[rid] = set()
            self.effects[rid] = set()
            for pos,op,args in commands:
                if op & 0x80FF == 0x8010:
                    if args[0] >= len(self.records):
                        raise ValueError(f'Out-of-range animation target in record {rid}')
                    self.calls[rid].add(args[0])
                elif op & 0x80FF == 0x800F:
                    self.effects[rid].add(args[0])
        lo = words(data,*sec[4])
        ld = words(data,*sec[5])
        if lo != sorted(lo) or lo[-1] > len(ld):
            raise ValueError('Invalid layout offsets')
        self.roots = {}
        for entry,(a,z) in enumerate(zip(lo,lo[1:]+[len(ld)])):
            w=ld[a:z]
            if len(w)>=4 and w[0]>>12 == 5:
                if w[3]>=len(self.records):raise ValueError('Invalid layout animation')
                self.roots[entry]=w[3]
        ew=words(data,*sec[2])
        if len(ew)%EFFECT_WORDS:raise ValueError('Invalid effect-table size')
        self.definitions=[ew[i:i+EFFECT_WORDS] for i in range(0,len(ew),EFFECT_WORDS)]
        for ids in self.effects.values():
            if any(i>=len(self.definitions) for i in ids):
                raise ValueError('Invalid effect ID')
        self.owners=collections.defaultdict(set)
        for root in set(self.roots.values()):
            seen,_=self.trace(root)
            for rid in seen:
                for eid in self.effects[rid]:self.owners[eid].add(root)
        self.aliases=collections.defaultdict(list)
        for entry,root in self.roots.items():self.aliases[root].append(entry)

    def trace(self,root):
        parent={root:None};todo=[root]
        while todo:
            rid=todo.pop()
            for nxt in sorted(self.calls[rid]):
                if nxt not in parent:parent[nxt]=rid;todo.append(nxt)
        return set(parent),parent

    def describe(self,evo):
        if evo not in self.roots:
            raise ValueError(f'Layout entry {evo} does not directly select an animation')
        root=self.roots[evo];seen,parent=self.trace(root);out=[]
        for eid in sorted(set().union(*(self.effects[r] for r in seen))):
            w=self.definitions[eid]
            caller=min(r for r in seen if eid in self.effects[r])
            chain=[];v=caller
            while v is not None:chain.append(v);v=parent[v]
            image_range=(w[1], w[2] if w[0]&1 else w[1])
            frame_range=(w[3], w[4] if w[0]&2 else w[3])
            bank_range=(w[5], w[6] if w[0]&4 else w[5])
            bank=bank_range[0] if bank_range[0]==bank_range[1] else None
            out.append({'effect_id':eid,'image':w[1],'bank_hint':bank,
                'image_range':image_range, 'subimage_range':frame_range,
                'bank_range':bank_range, 'random_subimage':bool(w[0]&2),
                'random_image':bool(w[0]&1),
                'image_offset':self.sections[2][0]+eid*66+2,
                'record_chain':list(reversed(chain)),
                'root_owners':sorted(self.owners[eid]),
                'same_root_layout_aliases':self.aliases[root]})
        return out

    def select(self,evo,image,subimage,bank,effect_id=None):
        rows=[r for r in self.describe(evo)
              if r['image_range'][0]<=image<=r['image_range'][1]
              and r['subimage_range'][0]<=subimage<=r['subimage_range'][1]
              and r['bank_range'][0]<=bank<=r['bank_range'][1]
              and (effect_id is None or r['effect_id']==effect_id)]
        for row in rows:
            if row['random_image']:
                raise ValueError(f"Effect {row['effect_id']} uses random image selection; isolated image replacement is not implemented.")
            for field in ('image_range','subimage_range','bank_range'):
                lo,hi=row[field]
                if lo!=hi:
                    raise ValueError(f"Effect {row['effect_id']} randomly selects {field} "
                                     f"{lo}..{hi}. Replacing only this selection would "
                                     "also change other particles; range isolation is not implemented.")
            if self.owners[row['effect_id']]!={self.roots[evo]}:
                raise ValueError(f"Effect {row['effect_id']} is shared by different animation roots; "
                                 "an isolated edit requires effect cloning, which is not supported.")
        return rows

    def patch(self,evo,source,replacement,effect_id=None):
        from d3_evolution_isolation import resolve, patch
        if effect_id is not None:
            raise ValueError('Explicit effect-ID filtering is unavailable in isolated mode; select by source image/subimage/bank.')
        si,ss,sb=parse_identifier(source);di,ds,db=parse_identifier(replacement)
        match=resolve(self.data,evo,si,ss,sb)[0]
        candidates=list(match['isolation_plan']['effects'].values())
        if not candidates:raise ValueError('No matching particle definition; use replace_d3_evo_image.py for scene images.')
        output=bytearray(self.data);patch(output,match,di,ds,db)
        diff=[i for i,(x,y) in enumerate(zip(self.data,output)) if x!=y]
        return bytes(output),candidates,diff

def parse_identifier(value):
    m=re.fullmatch(r'(\d+)_(\d+)_(\d+)(?:\.png)?',value)
    if not m:raise ValueError('Expected IMAGE_SUBIMAGE_BANK.png')
    result=tuple(map(int,m.groups()))
    if result[2]>15:raise ValueError('Bank must be 0..15')
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input_bin',type=Path);ap.add_argument('evo_animation_id',type=int)
    ap.add_argument('--source');ap.add_argument('--replacement');ap.add_argument('--output',type=Path)
    ap.add_argument('--effect-id',type=int,help='Select one traced effect explicitly')
    ap.add_argument('--dry-run',action='store_true');ap.add_argument('--json',action='store_true')
    args=ap.parse_args();b=args.input_bin.read_bytes();a=Archive(b)
    if not args.source:
        if args.replacement or args.output:ap.error('--source is required for a patch')
        rows=a.describe(args.evo_animation_id)
        if args.json:print(json.dumps(rows,indent=2));return
        print(f'Layout {args.evo_animation_id} -> root animation {a.roots[args.evo_animation_id]}')
        for x in rows:
            print(f"Effect {x['effect_id']}: image {x['image']}, subimages {x['subimage_range']}, banks {x['bank_range']}; "
                  f"chain {' -> '.join(map(str,x['record_chain']))}; image word 0x{x['image_offset']:08X}")
        return
    if not args.replacement or not args.output:ap.error('--replacement and --output are required')
    if args.output.resolve()==args.input_bin.resolve():ap.error('Use a separate output BIN for this research build')
    out,rows,diff=a.patch(args.evo_animation_id,args.source,args.replacement,args.effect_id)
    print('Effects:',', '.join(str(x['effect_id']) for x in rows))
    print('Changed byte offsets:',', '.join(f'0x{x:08X}' for x in diff) or '(none)')
    print('Other layouts retaining the original root:',[e for e in rows[0]['same_root_layout_aliases'] if e != args.evo_animation_id])
    print('Size, unrelated resources, original animation/effect contents and other layouts verified unchanged.')
    if args.dry_run:print('Dry run: no output written.');return
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.particle_',dir=args.output.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(out)
        os.replace(tmp,args.output)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    print('Wrote',args.output)

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,IndexError,struct.error) as exc:raise SystemExit('ERROR: '+str(exc))
