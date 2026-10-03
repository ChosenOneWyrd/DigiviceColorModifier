"""Copy-on-write for simple D3 image/bank/wait animation leaves.

No existing animation IDs are renumbered. The clone is appended. Only callers
owned by the selected layout root are redirected. Archive sections 15..20 move
into checked erased padding; data at/after 0x1EC000 remains untouched.
"""
import struct
from d3_particle_effects import Archive, BASE, u16, u32, decode_commands

ARCHIVE_LIMIT = 0x1EC000

from d3_archive_storage import archive_end

def leaf_selector(archive,rid):
    cmds=archive.commands[rid]
    start,end=archive.records[rid]
    if u16(archive.data,start)!=6 or len(cmds)!=3:return None
    _,op,args=cmds[0]
    if op==0x8001:image,sub=args[0],0
    elif op==0xA001:image,sub=args
    else:return None
    if cmds[1][1]!=0x2003 or cmds[2][1:]!=(0x0200,[0x7fff]):return None
    return image,sub,cmds[1][2][0]

def resolve(archive,evo,image,sub,bank,membership):
    if evo not in archive.roots:return []
    root=archive.roots[evo];seen,_=archive.trace(root)
    leaves=[r for r in sorted(seen) if leaf_selector(archive,r)==(image,sub,bank)]
    if not leaves:return resolve_sequence(archive,evo,image,sub,bank)
    if len(leaves)!=1:
        raise RuntimeError('Multiple matching simple scene leaves; isolated batch cloning is not implemented.')
    leaf=leaves[0]
    callers=[]
    for rid in sorted(seen):
        for pos,op,args in archive.commands[rid]:
            if op&0x80ff==0x8010 and args[0]==leaf:callers.append((rid,pos+1))
    if not callers:raise RuntimeError('The matching scene leaf is a root; root cloning is not implemented.')
    owner_roots={r for r in archive.roots.values()
                 if any(c in archive.trace(r)[0] for c,_ in callers)}
    if owner_roots!={root}:
        raise RuntimeError('The matching leaf has shared callers; deeper path cloning is required.')
    end=archive.sections[-1][1]
    # One uint16 offset entry plus one 16-byte clone. Source templates both
    # encode into 16 bytes, including a nonzero subimage when requested.
    growth=18
    if end+growth>ARCHIVE_LIMIT or archive.data[end:end+growth]!=b'\xff'*growth:
        raise RuntimeError('Insufficient erased padding after the animation archive for an isolated clone.')
    return [{'group_index':-1,'slot':leaf,'animation_id':leaf,
             'record':{'start':archive.records[leaf][0]},
             'ref':{'kind':'isolated_scene','opcode':archive.commands[leaf][0][1],
                    'image_word_index':2,'subimage_word_index':None,
                    'image_index':image,'subimage_index':sub,'source_bank':bank},
             'clone_plan':{'evo':evo,'root':root,'leaf':leaf,'callers':callers,
                           'table_start':archive.sections[13][0],
                           'old_end':end,'new_end':end+growth},
             'same_root_layout_aliases':archive.aliases[root]}]

def allowed_offsets(match):
    plan=match['clone_plan']
    return set(range(BASE+14*4,BASE+21*4)) | set(range(plan['table_start'],plan['new_end']))

def patch(data,match,image,sub,bank=None):
    if match["clone_plan"].get("mode")=="frame_sequence":
        return patch_sequence(data,match,image,sub,bank)
    from replace_d3_evo_image import parse_sprite_package
    ref=match['ref'];plan=match['clone_plan'];original=bytes(data)
    count,sub_count=parse_sprite_package(original)
    if not 0<=image<count or not 0<=sub<sub_count(image):
        raise RuntimeError('Replacement image/subimage is outside the sprite allocation.')
    if not 0<=sub<=0xffff:raise RuntimeError('Replacement subimage must fit uint16.')
    if bank is not None and bank!=ref['source_bank']:
        raise RuntimeError('Isolated scene replacement preserves the source palette bank.')
    before=Archive(original)
    fresh=resolve(before,plan['evo'],ref['image_index'],ref['subimage_index'],ref['source_bank'],{})
    if len(fresh)!=1 or fresh[0]['clone_plan']!=plan:
        raise RuntimeError('Scene clone source changed during planning.')
    sections=before.sections;table_start=sections[13][0];payload_start,payload_end=sections[14]
    old_end=plan['old_end'];new_id=len(before.records)
    payload=bytearray(original[payload_start:payload_end])
    for rid,word_index in plan['callers']:
        off=before.records[rid][0]-payload_start+word_index*2
        if u16(payload,off)!=plan['leaf']:raise RuntimeError('Unexpected scene call target.')
        struct.pack_into('<H',payload,off,new_id)
    clone_words=([6,0x8001,image,0x2003,ref['source_bank'],0x0200,0x7fff,0xfcbd]
                 if sub==0 else [6,0xa001,image,sub,0x2003,ref['source_bank'],0x0200,0x7fff])
    clone=struct.pack('<8H',*clone_words)
    old_units=len(payload)//4
    if old_units+4>0xffff:raise RuntimeError('Animation offset table capacity exceeded.')
    table=original[table_start:payload_start]+struct.pack('<H',old_units+4)
    rebuilt=table+payload+clone+original[payload_end:old_end]
    out=bytearray(original)
    out[table_start:plan['new_end']]=rebuilt
    for section in range(14,21):
        shift=2 if section==14 else 18
        struct.pack_into('<I',out,BASE+section*4,u32(original,BASE+section*4)+shift//2)
    after=Archive(out)
    # Verify all records by ID, including every operand, and all sections.
    for rid in range(new_id):
        expected=bytearray(original[slice(*before.records[rid])])
        for caller,word_index in plan['callers']:
            if rid==caller:struct.pack_into('<H',expected,word_index*2,new_id)
        if out[slice(*after.records[rid])]!=expected:
            raise RuntimeError(f'Unexpected change to animation record {rid}.')
    if out[slice(*after.records[new_id])]!=clone:raise RuntimeError('Clone verification failed.')
    for i in list(range(13))+list(range(15,21)):
        if original[slice(*sections[i])]!=out[slice(*after.sections[i])]:
            raise RuntimeError(f'Unexpected change to archive section {i}.')
    if len(out)!=len(original) or out[ARCHIVE_LIMIT:]!=original[ARCHIVE_LIMIT:]:
        raise RuntimeError('Unrelated data or BIN size changed.')
    allowed=allowed_offsets(match)
    if any(a!=b and i not in allowed for i,(a,b) in enumerate(zip(original,out))):
        raise RuntimeError('Change outside the clone allocation.')
    for other in set(before.roots.values())-{plan['root']}:
        old_seen,_=before.trace(other);new_seen,_=after.trace(other)
        if old_seen!=new_seen:raise RuntimeError('Another animation root changed.')
        for rid in old_seen:
            if before.commands[rid]!=after.commands[rid]:raise RuntimeError('Another animation root command changed.')
    data[:]=out
    return [{'group':-1,'slot':plan['leaf'],'record':new_id,'opcode':clone_words[1],
             'offset':after.records[new_id][0]+4,'field':'isolated_image',
             'old':ref['image_index'],'new':image}]

# Preserve complete frame sequences, cloning shared ancestors up to a private caller.
def resolve_sequence(a,evo,image,sub,bank):
    if evo not in a.roots or sub!=0 or bank!=0:return []
    root=a.roots[evo];seen,_=a.trace(root)
    targets={}
    for rid in sorted(seen):
        cs=a.commands[rid]
        # The Hawkmon sequence: control, two 401 frames, two 402 frames.
        # Match command structure, not raw words, so operands cannot be opcodes.
        if [op for _,op,_ in cs]!=[0x200d,0x8201,0x000d,0x8201,0x8201,0x200d,0x8201]:continue
        if u16(a.data,a.records[rid][0])!=15:continue
        indices=[p+1 for p,op,args in cs if op==0x8201 and args[0]==image]
        if indices:targets[rid]=indices
    if not targets:return []
    owners={rid:set() for rid in range(len(a.records))}
    for r in set(a.roots.values()):
        for rid in a.trace(r)[0]:owners[rid].add(r)
    clone_set=set(targets)
    while True:
        added={rid for rid in seen if a.calls[rid]&clone_set and owners[rid]!={root}}-clone_set
        if not added:break
        clone_set|=added
    if root in clone_set:raise RuntimeError('Cannot isolate this frame sequence without cloning the layout root.')
    boundary=[]
    for rid in sorted(seen-clone_set):
        for pos,op,args in a.commands[rid]:
            if op&0x80ff==0x8010 and args[0] in clone_set:
                if owners[rid]!={root}:raise RuntimeError('Shared caller remained at clone boundary.')
                boundary.append((rid,pos+1,args[0]))
    if not boundary:raise RuntimeError('No private caller for frame sequence.')
    clones=sorted(clone_set)
    growth=2*len(clones)+sum(a.records[r][1]-a.records[r][0] for r in clones)
    end=a.sections[-1][1]
    if end+growth>ARCHIVE_LIMIT or a.data[end:end+growth]!=b'\xff'*growth:
        raise RuntimeError('Insufficient erased archive padding for isolated frame-sequence copies.')
    leaf=min(targets);idx=targets[leaf][0]
    return [{'group_index':-1,'slot':leaf,'animation_id':leaf,
             'record':{'start':a.records[leaf][0]},
             'ref':{'kind':'isolated_scene','opcode':0x8201,'image_word_index':idx,
                    'subimage_word_index':None,'image_index':image,'subimage_index':0,'source_bank':bank},
             'clone_plan':{'mode':'frame_sequence','evo':evo,'root':root,'leaf':leaf,
                           'clones':clones,'targets':targets,'boundary':boundary,
                           'table_start':a.sections[13][0],'old_end':end,'new_end':end+growth},
             'same_root_layout_aliases':a.aliases[root]}]

def patch_sequence(data,match,image,sub,bank):
    from replace_d3_evo_image import parse_sprite_package
    if sub!=0:raise RuntimeError('This frame-sequence command supports replacement subimage 0 only.')
    if bank is not None and bank!=match['ref']['source_bank']:raise RuntimeError('Frame-sequence replacement preserves palette selection.')
    original=bytes(data);a=Archive(original);plan=match['clone_plan'];ref=match['ref']
    count,counts=parse_sprite_package(original)
    if not 0<=image<count or counts(image)<1:raise RuntimeError('Invalid replacement image allocation.')
    fresh=resolve_sequence(a,plan['evo'],ref['image_index'],0,ref['source_bank'])
    if len(fresh)!=1 or fresh[0]['clone_plan']!=plan:raise RuntimeError('Frame sequence changed during planning.')
    mapping={rid:len(a.records)+i for i,rid in enumerate(plan['clones'])}
    payload_start,payload_end=a.sections[14]
    payload=bytearray(original[payload_start:payload_end]);expected={}
    for rid,index,target in plan['boundary']:
        expected.setdefault(rid,bytearray(original[slice(*a.records[rid])]))
        struct.pack_into('<H',expected[rid],index*2,mapping[target])
        struct.pack_into('<H',payload,a.records[rid][0]-payload_start+index*2,mapping[target])
    appended=bytearray();offsets=[];copies={}
    for rid in plan['clones']:
        copy=bytearray(original[slice(*a.records[rid])])
        for pos,op,args in a.commands[rid]:
            if op&0x80ff==0x8010 and args[0] in mapping:struct.pack_into('<H',copy,(pos+1)*2,mapping[args[0]])
        for idx in plan['targets'].get(rid,[]):struct.pack_into('<H',copy,idx*2,image)
        copies[rid]=copy;appended+=copy
        units=(len(payload)+len(appended))//4
        if units>65535:raise RuntimeError('Animation offset capacity exceeded.')
        offsets.append(units)
    table=original[a.sections[13][0]:payload_start]+struct.pack('<'+'H'*len(offsets),*offsets)
    rebuilt=table+payload+appended+original[payload_end:plan['old_end']]
    out=bytearray(original);out[plan['table_start']:plan['new_end']]=rebuilt
    for sec in range(14,21):
        shift=len(mapping)*2 if sec==14 else plan['new_end']-plan['old_end']
        struct.pack_into('<I',out,BASE+sec*4,u32(original,BASE+sec*4)+shift//2)
    after=Archive(out)
    for rid in range(len(a.records)):
        want=expected.get(rid,original[slice(*a.records[rid])])
        if out[slice(*after.records[rid])]!=want:raise RuntimeError('Unexpected original record change.')
    for rid,copy in copies.items():
        if out[slice(*after.records[mapping[rid]])]!=copy:raise RuntimeError('Clone verification failed.')
    for sec in list(range(13))+list(range(15,21)):
        if original[slice(*a.sections[sec])]!=out[slice(*after.sections[sec])]:raise RuntimeError('Unrelated section changed.')
    for other in set(a.roots.values())-{plan['root']}:
        seen=a.trace(other)[0]
        if seen!=after.trace(other)[0] or any(a.commands[r]!=after.commands[r] for r in seen):
            raise RuntimeError('Another animation root changed.')
    allowed=allowed_offsets(match)
    if len(out)!=len(original) or any(x!=y and i not in allowed for i,(x,y) in enumerate(zip(original,out))):
        raise RuntimeError('Change outside clone allocation.')
    data[:]=out
    return [{'group':-1,'slot':rid,'record':mapping[rid],'opcode':0x8201,
             'offset':after.records[mapping[rid]][0]+index*2,'field':'isolated_image',
             'old':ref['image_index'],'new':image}
            for rid,indices in plan['targets'].items() for index in indices]
