"""Copy-on-write for simple D3 image/bank/wait animation leaves.

No existing animation IDs are renumbered. The clone is appended. Only callers
owned by the selected layout root are redirected. Archive sections 15..20 move
into checked erased padding; data at/after 0x1EC000 remains untouched.
"""
import struct
from d3_particle_effects import Archive, BASE, u16, u32, decode_commands

ARCHIVE_LIMIT = 0x1EC000

def archive_end(data, starts):
    end=starts[20]+2
    if (not BASE+84 <= starts[19] <= starts[20] < end <= ARCHIVE_LIMIT
        or starts[20]-starts[19]!=2
        or data[starts[19]:end] != b'\x00\x00\x43\x00'):
        raise ValueError('Unsupported D3 archive tail; relocation is refused')
    return end

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
    if not leaves:return []
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
