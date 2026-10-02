#!/usr/bin/env python3
"""List or replace D3 evolution sound references without rewriting audio.

List:
  python replace_d3_evo_sound.py D3.bin 396 --list
Replace:
  python replace_d3_evo_sound.py D3.bin 396 chunk_0050.a18.wav chunk_0049.a18.wav D3_out.bin

The bundled maps supply names. Numeric chunk IDs also work. Only decoded
sound-command operands in the selected layout's call graph can be edited.
Shared animation records are refused; same-root layout aliases share edits.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import struct
import tempfile

BASE=0x1B3000
ARCHIVE_LIMIT=0x1EC000
SOUND_TAG=0x2000

def u16(b,p):return struct.unpack_from('<H',b,p)[0]
def u32(b,p):return struct.unpack_from('<I',b,p)[0]
def words(b,s,e):
    if not 0<=s<=e<=len(b) or (e-s)%2:raise ValueError('Invalid archive word range')
    return list(struct.unpack_from('<'+'H'*((e-s)//2),b,s))

def commands(ws):
    if not ws:raise ValueError('Empty animation record')
    out=[];p=1
    while p<len(ws):
        op=ws[p]
        if op==0xFCBD and p==len(ws)-1:break
        n=(op>>8).bit_count()
        if p+1+n>len(ws):raise ValueError('Truncated animation command')
        out.append((p,op,ws[p+1:p+1+n]));p+=1+n
    return out

class Archive:
    def __init__(self,data):
        self.data=data
        if not data.startswith(b'GP-SPIF-HEADER') or len(data)<ARCHIVE_LIMIT:
            raise ValueError('Not a compatible D3 BIN')
        from d3_particle_effects import parse_sections
        self.sections=parse_sections(data)
        sec=self.sections;offsets=words(data,*sec[13])
        if len(offsets)<2 or offsets!=sorted(offsets) or offsets[-1]*4!=sec[14][1]-sec[14][0]:
            raise ValueError('Invalid animation offset table')
        self.records=[(sec[14][0]+a*4,sec[14][0]+z*4) for a,z in zip(offsets,offsets[1:])]
        self.commands=[commands(words(data,s,e)) for s,e in self.records]
        self.calls={i:set() for i in range(len(self.records))}
        for i,cs in enumerate(self.commands):
            for _,op,args in cs:
                if op&0x80ff==0x8010:
                    if args[0]>=len(self.records):raise ValueError('Invalid animation call target')
                    self.calls[i].add(args[0])
        lo=words(data,*sec[4]);ld=words(data,*sec[5])
        if not lo or lo!=sorted(lo) or lo[-1]>len(ld):raise ValueError('Invalid layout offsets')
        self.roots={}
        for i,(a,z) in enumerate(zip(lo,lo[1:]+[len(ld)])):
            w=ld[a:z]
            if len(w)>=4 and w[0]>>12==5:
                if w[3]>=len(self.records):raise ValueError('Invalid layout root')
                self.roots[i]=w[3]
        self.owners={i:set() for i in range(len(self.records))}
        for root in set(self.roots.values()):
            for rid in self.trace(root):self.owners[rid].add(root)

    def trace(self,root):
        parents={root:None};todo=[root]
        while todo:
            rid=todo.pop()
            for nxt in sorted(self.calls[rid]):
                if nxt not in parents:parents[nxt]=rid;todo.append(nxt)
        return parents

    def sounds(self,evo):
        if evo not in self.roots:raise ValueError(f'Layout {evo} has no directly selected animation root')
        root=self.roots[evo];parents=self.trace(root);out=[]
        aliases=sorted(e for e,r in self.roots.items() if r==root)
        for rid in sorted(parents):
            for pos,op,args in self.commands[rid]:
                # Low byte 0E = sound command. Bit 15 supplies its resource.
                # 0x2000 namespace identifies this audio bank; other resources
                # and commands lacking a sound operand are never edited.
                if op&0x80ff!=0x800e or args[0]&0xe000!=SOUND_TAG:continue
                chunk=args[0]&0x1fff
                chain=[];v=rid
                while v is not None:chain.append(v);v=parents[v]
                out.append({'record':rid,'word_index':pos+1,'opcode':op,
                            'chunk':chunk,'resource':args[0],
                            'offset':self.records[rid][0]+2*(pos+1),
                            'other_operands':args[1:], 'path':list(reversed(chain)),
                            'root_owners':sorted(self.owners[rid]),
                            'same_root_layout_aliases':aliases})
        for i,r in enumerate(out,1):r['occurrence']=i
        return out

def load_sound_map(path):
    names={}
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not {'original','new'}<=set(reader.fieldnames or []):raise ValueError('Sound map needs original,new columns')
        for row in reader:
            m=re.fullmatch(r'chunk_(\d+)\.a18\.wav',row['original'].strip(),re.I)
            if not m:raise ValueError('Invalid chunk filename in sound map')
            i=int(m[1])
            if i in names:raise ValueError('Duplicate chunk ID in sound map')
            names[i]=row['new'].strip()
    return names

def sound_id(value,names):
    text=str(value).strip();m=re.fullmatch(r'(?:chunk_)?(\d+)(?:\.a18\.wav)?',text,re.I)
    if m:result=int(m[1])
    else:
        found=[i for i,name in names.items() if name.casefold()==text.casefold()]
        if len(found)!=1:raise ValueError(f'Sound name {text!r} is unknown or ambiguous; use a chunk ID')
        result=found[0]
    if not 0<=result<0x2000 or result not in names:raise ValueError(f'Chunk {result} is absent from the sound map')
    return result

def evo_id(value,path):
    if str(value).isdigit():return int(value)
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        found=[int(r['value']) for r in csv.DictReader(f) if r['key'].casefold()==str(value).casefold()]
    if len(found)!=1:raise ValueError('Unknown or ambiguous evolution name; use its numeric ID')
    return found[0]

def audio_offsets(data):
    """Importer-compatible raw-u32 discovery; no payload is decoded or written.

    This layout's supplied importer merges 272 leaves, identical to this scan.
    Skip intervals and length limits match its scan_u32_audio implementation.
    """
    out=[];p=0
    while p<len(data)-8:
        marker=data.find(b'\x80\x3e',p+4)
        if marker<0:break
        off=marker-4
        if off>=len(data)-8:break
        declared=u32(data,off);total=declared+6
        if 0<declared<=0x400000 and 8<=total<=0x400000 and off+total<=len(data):
            out.append(off);p=off+max(4,min(total//8,0x1000))
        else:p=off+1
    return out

def patch(data,evo,source,destination,occurrence=None,all_matches=False):
    archive=Archive(data);root=archive.roots.get(evo)
    rows=[r for r in archive.sounds(evo) if r['chunk']==source]
    if occurrence is not None:rows=[r for r in rows if r['occurrence']==occurrence]
    if not rows:raise ValueError('Source sound was not found in the selected evolution/occurrence')
    if len(rows)>1 and not all_matches:
        raise ValueError('Multiple occurrences found. Use --occurrence from --list, or --all-matches')
    if source==destination:raise ValueError('Source and destination are identical')
    if not 0<=destination<0x2000:raise ValueError('Destination chunk ID is out of range')
    for r in rows:
        if set(r['root_owners'])!={root}:
            raise ValueError(f"Record {r['record']} is shared by animation roots {r['root_owners']}; "
                             'isolated sound editing requires path cloning, which this version refuses')
    output=bytearray(data);allowed=set()
    for r in rows:
        struct.pack_into('<H',output,r['offset'],SOUND_TAG|destination)
        allowed.update((r['offset'],r['offset']+1))
    changes=[i for i,(a,b) in enumerate(zip(data,output)) if a!=b]
    if len(output)!=len(data) or not set(changes)<=allowed:raise ValueError('Byte validation failed')
    # Every change is inside selected exclusive records; validate decoded result
    # and other roots independently before exposing the output.
    after=Archive(output)
    for rid,cmds in enumerate(archive.commands):
        expected=[]
        offsets={r['word_index']:r for r in rows if r['record']==rid}
        for pos,op,args in cmds:
            args=list(args)
            if pos+1 in offsets:args[0]=SOUND_TAG|destination
            expected.append((pos,op,args))
        if after.commands[rid]!=expected:raise ValueError('Unexpected command change')
    for other in set(archive.roots.values())-{root}:
        for rid in archive.trace(other):
            if archive.commands[rid]!=after.commands[rid]:raise ValueError('Another animation root changed')
    return bytes(output),rows,changes

def write_new(path,data):
    path=Path(path)
    if path.exists():raise ValueError('Output already exists; choose a new filename')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.d3_sound_',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data)
        # An exclusive destination avoids overwriting the input or existing work.
        os.link(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    here=Path(__file__).resolve().parent
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('input_bin',type=Path);p.add_argument('evolution')
    p.add_argument('source',nargs='?');p.add_argument('replacement',nargs='?');p.add_argument('output_bin',nargs='?',type=Path)
    p.add_argument('--list',action='store_true');p.add_argument('--json',action='store_true')
    p.add_argument('--sound-map',type=Path,default=here/'d3_sound_map.csv')
    p.add_argument('--evo-map',type=Path,default=here/'d3_evo_animation_map.csv')
    p.add_argument('--dry-run',action='store_true')
    g=p.add_mutually_exclusive_group();g.add_argument('--occurrence',type=int);g.add_argument('--all-matches',action='store_true')
    args=p.parse_args();names=load_sound_map(args.sound_map);evo=evo_id(args.evolution,args.evo_map)
    data=args.input_bin.read_bytes();archive=Archive(data)
    rows=archive.sounds(evo)
    for r in rows:r['name']=names.get(r['chunk'],'(not in sound map)')
    if args.list:
        if args.source or args.replacement or args.output_bin:p.error('--list cannot be combined with replacement arguments')
        if args.json:print(json.dumps(rows,indent=2));return
        print(f'Evolution {evo} -> animation root {archive.roots[evo]}')
        for r in rows:
            print(f"{r['occurrence']:2}: chunk_{r['chunk']:04}.a18.wav  {r['name']}  "
                  f"record {r['record']}  @0x{r['offset']:08X}  other operands {r['other_operands']}")
        return
    if args.source is None or args.replacement is None or args.output_bin is None:
        p.error('Supply source, replacement and output BIN, or --list')
    if args.output_bin.resolve()==args.input_bin.resolve():p.error('Use a separate output BIN')
    si=sound_id(args.source,names);di=sound_id(args.replacement,names)
    audio=audio_offsets(data)
    if set(names)!=set(range(len(audio))):
        raise ValueError('Sound map and discovered audio layout disagree; chunk-to-resource mapping needs verification')
    if si>=len(audio) or di>=len(audio):raise ValueError('Sound chunk is outside discovered audio data')
    out,selected,changes=patch(data,evo,si,di,args.occurrence,args.all_matches)
    print(f'Evolution {evo}: chunk_{si:04}.a18.wav ({names[si]}) -> chunk_{di:04}.a18.wav ({names[di]})')
    print('Changed byte offsets:',', '.join(f'0x{x:08X}' for x in changes))
    print('Same-root layout aliases:',selected[0]['same_root_layout_aliases'])
    print('Verified: all other bytes, audio payloads, other sound operands, and different animation roots unchanged.')
    if args.dry_run:print('Dry run: no output written.');return
    write_new(args.output_bin,out);print('Wrote',args.output_bin)

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,IndexError,KeyError,struct.error) as exc:raise SystemExit('ERROR: '+str(exc))
