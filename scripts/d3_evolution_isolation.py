"""Copy-on-write image edits rooted at ONE D3 layout entry.

Protected baseline records/effects remain byte-identical. Private copies are
compacted with ID remapping; only the selected layout changes behavior.
"""
import struct
import hashlib
from d3_particle_effects import Archive, BASE, u16, u32, words
from d3_archive_storage import allocation, protected_counts, metadata, repack

def plan(data,evo,image,sub,bank,allow_expand=True):
    from replace_d3_evo_image import parse_sprite_package, all_image_refs
    a=Archive(data)
    if evo not in a.roots:raise RuntimeError('Selected layout has no supported animation root.')
    n,counts=parse_sprite_package(data)
    if not 0<=image<n or not 0<=sub<counts(image):raise RuntimeError('Invalid source image/subimage.')
    root=a.roots[evo];seen,_=a.trace(root);direct={};effects={}
    for rid in sorted(seen):
        rec={'start':a.records[rid][0],'words':words(data,*a.records[rid])}
        decoded={pos:(op,args) for pos,op,args in a.commands[rid]}
        # Constant palette commands can follow the initial image command.
        banks={args[0] for _,op,args in a.commands[rid] if op==0x2003}
        if len(banks)>1:
            known_bank=None
        else:known_bank=next(iter(banks),0)
        for ref in all_image_refs(rec,n,counts):
            pos=ref['opcode_word_index']
            if pos not in decoded:continue
            if ref['image_index']!=image or ref['subimage_index']!=sub:continue
            if known_bank is None:raise RuntimeError(f'Record {rid} changes palette banks; exact-bank selection is not supported.')
            if known_bank!=bank:continue
            direct.setdefault(rid,[]).append(ref)
    for row in a.describe(evo):
        if not (row['image_range'][0]<=image<=row['image_range'][1]
                and row['subimage_range'][0]<=sub<=row['subimage_range'][1]
                and row['bank_range'][0]<=bank<=row['bank_range'][1]):continue
        if row['random_image'] or any(row[k][0]!=row[k][1] for k in ('image_range','subimage_range','bank_range')):
            raise RuntimeError(f"Effect {row['effect_id']} uses a variable image/frame/bank range; replacing a single selection is not supported.")
        effects[row['effect_id']]=row
    targets=set(direct)
    for rid in seen:
        if a.effects[rid]&effects.keys():targets.add(rid)
    if not targets:raise RuntimeError(f'No supported reachable image {image}, subimage {sub}, bank {bank} in evolution {evo}. No shared records will be edited.')
    clones=set(targets)
    while True:
        add={rid for rid in seen if a.calls[rid]&clones}-clones
        if not add:break
        clones|=add
    if root not in clones:raise RuntimeError('Cannot connect selection to the layout root.')
    clone_ids=sorted(clones)
    nr,ne=len(a.records),len(a.definitions)
    floor_r,floor_e,guard=protected_counts(data,a)
    clone_map={r:nr+i for i,r in enumerate(clone_ids)}
    effect_clones={e:ne+i for i,e in enumerate(sorted(effects))}
    origin={**{r:r for r in range(nr)},**{new:old for old,new in clone_map.items()}}
    calls=dict(a.calls);effect_calls=dict(a.effects)
    for old,new in clone_map.items():
        calls[new]={clone_map.get(r,r) for r in a.calls[old]}
        effect_calls[new]={effect_clones.get(e,e) for e in a.effects[old]}
    roots=dict(a.roots);roots[evo]=clone_map[root]
    keep=set();todo=list(range(floor_r))+list(roots.values())
    while todo:
        rid=todo.pop()
        if rid in keep:continue
        keep.add(rid);todo.extend(calls[rid]-keep)
    keep_effects=set(range(floor_e))
    for rid in keep:keep_effects.update(effect_calls[rid])
    order=sorted(keep);eorder=sorted(keep_effects)
    rmap={old:new for new,old in enumerate(order)}
    emap={old:new for new,old in enumerate(eorder)}
    payload_size=sum(a.records[origin[r]][1]-a.records[origin[r]][0] for r in order)
    if payload_size//4>65535 or len(order)>65536 or len(eorder)>65536:
        raise RuntimeError('Animation/effect encoding capacity exceeded. No changes made.')
    size=84+sum(e-s for s,e in a.sections)+payload_size-(a.sections[14][1]-a.sections[14][0])
    size+=2*(len(order)-nr)+66*(len(eorder)-ne)
    storage=allocation(data,a,size)
    representative=min(targets)
    ref=next(iter(direct.get(representative,[])),None)
    old_size=84+sum(e-s for s,e in a.sections)
    return {'evo':evo,'root':root,'image':image,'sub':sub,'bank':bank,
            'clones':clone_ids,'direct':direct,'effects':effects,
            'clone_map':clone_map,'effect_clones':effect_clones,'origin':origin,
            'order':order,'effect_order':eorder,'record_map':rmap,'effect_map':emap,
            'protected_records':floor_r,'protected_effects':floor_e,'guard':guard,
            'reclaimed_records':nr-len(keep.intersection(range(nr))),
            'old_end':a.sections[-1][1],'growth':size-old_size,**storage,
            'snapshot':hashlib.sha256(data).digest(),
            'representative':representative,'representative_start':a.records[representative][0],
            'representative_index':ref['image_word_index'] if ref else 1}

def resolve(data,evo,image,sub,bank,allow_expand=True):
    p=plan(data,evo,image,sub,bank,allow_expand)
    return [{'group_index':-1,'slot':0,'animation_id':p['representative'],
             'record':{'start':p['representative_start']},
             'ref':{'kind':'isolated_evolution','opcode':0,
                    'image_word_index':p['representative_index'],'subimage_word_index':None,
                    'image_index':image,'subimage_index':sub,'source_bank':bank},
             'isolation_plan':p}]

def validate(match,sub,bank=None):
    p=match['isolation_plan']
    if not 0<=sub<=65535:raise RuntimeError('Subimage must fit uint16.')
    if bank is not None and bank!=p['bank']:raise RuntimeError('Palette-bank changes are not supported; keep the same bank.')
    if p['effects'] and sub>511:raise RuntimeError('Particle subimages must fit 0..511.')
    if sub and any(r['kind']!='explicit' for refs in p['direct'].values() for r in refs):
        raise RuntimeError('A matched compact image command cannot select a nonzero replacement subimage.')

def patch(data,match,image,sub,bank=None):
    from replace_d3_evo_image import parse_sprite_package
    validate(match,sub,bank);p=match['isolation_plan'];original=bytes(data)
    if plan(original,p['evo'],p['image'],p['sub'],p['bank'])!=p:
        raise RuntimeError('The BIN changed during edit planning.')
    n,counts=parse_sprite_package(original)
    if not 0<=image<n or not 0<=sub<counts(image):raise RuntimeError('Invalid replacement image/subimage allocation.')
    if (image,sub)==(p['image'],p['sub']):raise RuntimeError('Replacement equals source.')
    a=Archive(original);sec=[bytearray(original[s:e]) for s,e in a.sections]
    rmap=p['record_map'];emap=p['effect_map'];clones=p['clone_map'];eclones=p['effect_clones']
    reverse_effects={new:old for old,new in eclones.items()}
    definitions=[]
    for eid in p['effect_order']:
        w=list(a.definitions[reverse_effects.get(eid,eid)])
        if eid in reverse_effects:
            w[1]=image;w[3]=sub
            if w[0]&2:w[4]=sub
        definitions.append(w)
    sec[2]=b''.join(struct.pack('<33H',*w) for w in definitions)
    records=[];offsets=[0]
    for temp in p['order']:
        old=p['origin'][temp];is_clone=temp>=len(a.records)
        blob=bytearray(original[slice(*a.records[old])])
        for pos,op,args in a.commands[old]:
            if op&0x80ff==0x8010:
                target=clones.get(args[0],args[0]) if is_clone else args[0]
                struct.pack_into('<H',blob,(pos+1)*2,rmap[target])
            if op&0x80ff==0x800f:
                effect=eclones.get(args[0],args[0]) if is_clone else args[0]
                struct.pack_into('<H',blob,(pos+1)*2,emap[effect])
        if is_clone:
            for ref in p['direct'].get(old,[]):
                struct.pack_into('<H',blob,ref['image_word_index']*2,image)
                if ref['kind']=='explicit':struct.pack_into('<H',blob,ref['subimage_word_index']*2,sub)
        records.append(bytes(blob));offsets.append(offsets[-1]+len(blob)//4)
    sec[13]=struct.pack('<'+'H'*len(offsets),*offsets);sec[14]=b''.join(records)
    roots={}
    for evo,root in a.roots.items():
        roots[evo]=rmap[clones[root] if evo==p['evo'] else root]
        off=u16(sec[4],evo*2)*2+6
        struct.pack_into('<H',sec[5],off,roots[evo])
    out=repack(original,sec,p,metadata(p['protected_records'],p['protected_effects'],p['guard']))
    after=Archive(out)
    if after.roots!=roots:raise RuntimeError('Unexpected layout roots')
    for i,blob in enumerate(records):
        if out[slice(*after.records[i])]!=blob:raise RuntimeError('Record verification failed')
    if after.definitions!=definitions:raise RuntimeError('Particle verification failed')
    for i in range(p['protected_records']):
        if original[slice(*a.records[i])]!=out[slice(*after.records[i])]:raise RuntimeError('Protected record changed')
    if after.definitions[:p['protected_effects']]!=a.definitions[:p['protected_effects']]:
        raise RuntimeError('Protected particle definition changed')
    for i in set(range(21))-{2,5,13,14}:
        if out[slice(*after.sections[i])]!=original[slice(*a.sections[i])]:raise RuntimeError('Unrelated section changed')
    if out[slice(*after.sections[5])]!=sec[5]:raise RuntimeError('Layout section verification failed')
    # Independent semantic check of every retained old record: only ID remapping
    # is permitted. New clones must differ only at the chosen fields and calls.
    for temp in p['order']:
        old=p['origin'][temp];is_clone=temp>=len(a.records);expected=[]
        fields={}
        if is_clone:
            for ref in p['direct'].get(old,[]):
                fields[ref['image_word_index']]=image
                if ref['kind']=='explicit':fields[ref['subimage_word_index']]=sub
        for pos,op,args in a.commands[old]:
            args=list(args)
            if op&0x80ff==0x8010:args[0]=rmap[clones.get(args[0],args[0]) if is_clone else args[0]]
            if op&0x80ff==0x800f:args[0]=emap[eclones.get(args[0],args[0]) if is_clone else args[0]]
            for j in range(len(args)):
                if pos+1+j in fields:args[j]=fields[pos+1+j]
            expected.append((pos,op,args))
        if after.commands[rmap[temp]]!=expected:raise RuntimeError('Decoded command verification failed')
    for evo,root in a.roots.items():
        seen=a.trace(root)[0]
        expected={rmap[clones.get(r,r)] if evo==p['evo'] else rmap[r] for r in seen}
        if after.trace(after.roots[evo])[0]!=expected:raise RuntimeError('Layout call graph changed unexpectedly')
    reports=[]
    for old,refs in p['direct'].items():
        rid=rmap[clones[old]]
        for ref in refs:reports.append({'group':-1,'slot':0,'record':rid,'opcode':ref['opcode'],
            'offset':after.records[rid][0]+ref['image_word_index']*2,'field':'isolated_image','old':p['image'],'new':image})
    for old,new in eclones.items():
        eid=emap[new]
        reports.append({'group':-1,'slot':eid,'record':roots[p['evo']],'opcode':0x800f,
            'offset':after.sections[2][0]+eid*66+2,'field':'isolated_particle_image','old':p['image'],'new':image})
    data[:]=out
    return reports
