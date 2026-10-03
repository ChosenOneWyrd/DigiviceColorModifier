"""Add and edit D-3 partners without extending the fixed Link roster.

Only the authenticated D-3 firmware family is accepted. New records use private
ROM storage and a private Viewer order. Existing Partner/Link rows remain fixed.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,struct,zlib
from pathlib import Path
import d3_evolution_core as core
import d3_transfer_evolution_lines as transfer
from d3_additional_paging_codegen import assemble,assemble_gate,GATE_OFFSET
from d3_additional_paging_v2 import assemble as assemble_v2
from d3_additional_paging_v1 import assemble as assemble_v1
from viewer_sites import TABLE as TABLE_SITES,RESOLVE,FLAGS,RESULT,ANIM

START,CODE_START,CODE_END=0xAD060,0xAD0A0,0xAD640
ADAPTERS,TABLE,RECORDS,META,END=0xAD640,0xAD900,0xADA00,0xADC00,0xAE000
MAX_RECORDS=16
MAGIC=b'D3ADD001'
VIEW_START,VIEW_END=0x5B276,0x5D0E0
STOCK_VIEW_HASH='6b16b37dfde1ecbd382fa248c879ac7906976effbd81a4c133c74478524e2995'
FIELDS=('stage','digimon_id','slot_type','win_requirement_for_next_evo','sprite_index','string_index','evo_animation1_id','evo_animation2_id','evo_animation3_id','evo_animation4_id','evo_animation5_id','background_music_during_battle_id','attack_voice_sound_id','attack_shot_sprite_index','attack_shot_sound_id','special_unlock')
CSV_HEADERS=('record_key','destination_line','friend_row','template_record')+FIELDS

def digest(data):return hashlib.sha256(data).hexdigest()
def pack(words):return struct.pack('<'+'H'*len(words),*words)
def pointer(key):return (RECORDS//2+0x9000+16*(key-38)) if key>=38 else 0x57CB4+16*key

def legacy_detected(data):
 return data[START:START+8] in (b'D3XPRB01',b'D3XPRB02') or data[0xAD3C0:0xAD3C8] in (b'D3VIEW01',b'D3VIEW02') or data[0xAD600:0xAD608] in (b'D3EXTRA1',b'D3EXPRB2')
def detected(data):return data[START:START+8]==MAGIC or legacy_detected(data)

class Asm:
 def __init__(self):self.words=[];self.labels={};self.fix=[]
 def emit(self,*w):self.words.extend(w)
 def label(self,name):self.labels[name]=ADAPTERS//2+0x9000+len(self.words)
 def jump(self,name):self.emit(0xFE85);self.fix.append((len(self.words),name));self.emit(0)
 def equal(self,name):self.emit(0x4E02);self.jump(name)
 def finish(self):
  for i,n in self.fix:self.words[i]=self.labels[n]&65535
  return pack(self.words)

def adapters(count):
 a=Asm();e=a.emit;a.label('resolve');e(0x0908,3,0x98C4)
 for key in range(38,38+count):e(0x4840+key);a.equal(f'r{key}')
 e(0xFE84,0xC70E)
 for key in range(38,38+count):a.label(f'r{key}');e(0x9309,pointer(key)&65535,0x9445,0x9A90)
 a.label('unlock');e(0x0908,3,0x98C4)
 for key in range(38,38+count):e(0x4840+key);a.equal(f'u{key}')
 e(0xFE82,0xF4F8)
 for key in range(38,38+count):
  a.label(f'u{key}');e(0x970B,(pointer(key)+1)&65535,0xFE05,0x96E3,0xD688,0xF042,0xF4F8,0x0041,0x9A90)
 code=a.finish()
 if ADAPTERS+len(code)>TABLE:raise ValueError('Too many additional Partner adapters.')
 return code,a.labels

def viewer_edits(count):
 n=44+count;labels=adapters(count)[1];edits={}
 for off in TABLE_SITES:edits[off]=([0xE6EF],[(TABLE//2+0x9000)&65535])
 for off in RESOLVE:edits[off]=([0xF044,0xC70E],[0xF045,labels['resolve']&65535])
 edits[0x5B2CA]=([0xF042,0xF4F8],[0xF045,labels['unlock']&65535])
 for off in FLAGS:edits[off]=([0x80],[0x54+n])
 for off in RESULT:edits[off]=([0xAC],[0x54+2*n])
 for off in ANIM:edits[off]=([0xAD],[0x55+2*n])
 for off in (0x5B384,0x5B3C2):edits[off]=([0xAE],[0x56+2*n])
 for off in (0x5B498,0x5B96C):edits[off]=([0x486B],[0x4840+n-1])
 for off in (0x5BBF4,0x5CD74):edits[off]=([0x966C],[0x9640+n])
 return edits

def check_viewer(data,count=None):
 view=bytearray(data[VIEW_START:VIEW_END])
 if count is not None:
  for off,(before,after) in viewer_edits(count).items():
   if data[off:off+2*len(after)]!=pack(after):raise ValueError(f'Unrecognized Viewer patch at {off:#x}.')
   view[off-VIEW_START:off-VIEW_START+2*len(before)]=pack(before)
 if digest(view)!=STOCK_VIEW_HASH:raise ValueError('Viewer code differs from the supported D-3 firmware.')

def validate_words(data,words,line):
 if type(line) is not int or line not in range(7):raise ValueError('Choose a destination line from 0 to 6.')
 if len(words)!=16 or any(type(x) is not int or not 0<=x<=65535 for x in words):raise ValueError('Partner fields must be unsigned 16-bit integers.')
 if words[0] not in range(1,6):raise ValueError('Additional partners require stage 1–5.')
 if words[2]!=line:raise ValueError('Record line membership must match its destination.')
 if words[15] not in (0,1):raise ValueError('Special unlock must be 0 or 1.')
 ids={r.link_id for r in core.read_partner_records(data) if r.active}
 if words[1] not in ids or words[1]>37:raise ValueError('Shared digimon_id must be an existing active Partner ID, not the Friend ID.')
 seen_zero=False
 for value in words[6:11]:
  if not value:seen_zero=True
  elif seen_zero:raise ValueError('Evolution animation IDs must be consecutive, with zeros only at the end.')

def validate_entries(data,entries):
 if len(entries)>MAX_RECORDS:raise ValueError(f'This layout supports at most {MAX_RECORDS} additional partners across all lines.')
 for i,r in enumerate(entries):
  if set(r)!={'key','line','friend','template','words'} or r['key']!=38+i:raise ValueError('Invalid additional Partner keys.')
  if type(r['friend']) is not int or not -1<=r['friend']<91:raise ValueError('Invalid Friend row.')
  if type(r['template']) is not int or not -1<=r['template']<38:raise ValueError('Invalid Partner template.')
  validate_words(data,r['words'],r['line'])

def derive_pages(data,entries,mappings,lines=None,slot_order=None):
 transfer.validate_mappings(mappings)
 if lines is None:lines=core.read_source_lines(data)[0]
 physical=core.read_partner_records(data);active={i:[r for r in lines[i] if physical[r].active] for i in range(7)}
 combined=merge_slots(lines,entries,slot_order)
 words={r['key']:r['words'] for r in entries}
 all_active={line:[k for k in keys if (words[k][0]>0 if k>=38 else physical[k].active)] for line,keys in combined.items()}
 pages={}
 for line in sorted(set(mappings)|{r['line'] for r in entries}):
  host=active[line];donor=[]
  own_added=[k for k in combined[line] if k>=38]
  if line in mappings:donor=[k for k in all_active[mappings[line]][1:] if k not in host and k not in own_added]
  if not 2<=len(host)<=9:raise ValueError(f'Line {line} needs 2–9 active original choices for paging.')
  overflow=[]
  # Keep all source additions before the donor, without mixing the two groups.
  for group in (own_added,donor):
   chunks=[group[i:i+9] for i in range(0,len(group),9)]
   if chunks and len(chunks[-1])==1:chunks[-1].append(chunks[-1][0])
   overflow.extend(chunks)
  if not overflow:continue
  pages[line]=[host]+overflow
 return pages

def derive_order(data,entries,lines,slot_order=None):
 original=list(struct.unpack_from('<44I',data,core.ORDER_OFFSET));out=[];position=0
 for line in range(7):
  n=len(lines[line])
  if original[position:position+n]!=lines[line]:raise ValueError('Viewer order does not match the current evolution lines.')
  out.extend(slot_order[line] if slot_order else lines[line]+[r['key'] for r in entries if r['line']==line]);position+=n
 out.extend(original[position:])
 if len(out)!=44+len(entries):raise ValueError('Invalid extended Viewer order.')
 return out

def merge_slots(lines,entries,previous=None):
 result={}
 for line in range(7):
  added=[r['key'] for r in entries if r['line']==line]
  old=(previous or {}).get(line,[])
  if [k for k in old if k<38]==lines[line]:result[line]=[k for k in old if k<38 or k in added]
  else:result[line]=list(lines[line])+[k for k in old if k in added]
  result[line]+=[k for k in added if k not in result[line]]
 return result

def combined_lines(data,state=None):
 state=read_state(data) if state is None else state
 return merge_slots(core.read_source_lines(data)[0],state['entries'],state.get('slots'))

def validate_slots(data,entries,slots):
 if set(slots)!=set(range(7)):raise ValueError('Evolution Slots requires lines 0–6.')
 originals={line:[k for k in values if k<38] for line,values in slots.items()}
 core.derive_slot_types(originals)
 all_keys=[k for values in slots.values() for k in values]
 if any(type(k) is not int or not 0<=k<38+len(entries) for k in all_keys):raise ValueError('Unknown Partner record key in Evolution Slots.')
 for r in entries:
  locations=[line for line,values in slots.items() for k in values if k==r['key']]
  if locations!=[r['line']]:raise ValueError('Each added Partner must appear exactly once on its assigned line.')
 if any(len(set(values))!=len(values) for values in slots.values()):raise ValueError('Duplicate record in a line.')
 return originals

def update_slots_bytes(original,slots):
 state=read_state(original);entries=[{**r,'words':list(r['words'])} for r in state['entries']]
 for r in entries:
  locations=[line for line,values in slots.items() for k in values if k==r['key']]
  if len(locations)!=1:raise ValueError(f"Added record {r['key']} must appear exactly once. Use Additional Partners to remove it.")
  r['line']=locations[0];r['words'][2]=locations[0]
 originals=validate_slots(original,entries,slots)
 work=bytearray(original)
 result=core._synchronize_existing_bin_without_transfers(work,originals)
 if entries:install_into(work,entries,state['mappings'],result[0],slots)
 elif state['mappings']:transfer.install_into(work,state['mappings'],result[0])
 if work[:0xE000]!=original[:0xE000] or len(work)!=len(original):raise RuntimeError('Unexpected startup/size change.')
 return bytes(work)

def _decode(data):
 version,count,code_len,json_len=struct.unpack_from('<4H',data,START+8)
 if version not in (1,2,3,4,5) or not 1<=count<=MAX_RECORDS or not 0<code_len<=CODE_END-CODE_START or code_len%2 or not 0<json_len<=END-META:raise ValueError('Invalid additional Partner header.')
 if any(data[START+48:CODE_START]) or hashlib.sha256(data[CODE_START:END]).digest()!=data[START+16:START+48]:raise ValueError('Additional Partner checksum mismatch; no changes made.')
 try:
  payload=bytes(data[META:META+json_len])
  if version>=4:payload=zlib.decompress(payload)
  meta=json.loads(payload);mappings={int(k):v for k,v in meta['m'].items()};pages={int(k):v for k,v in meta['p'].items()};order=meta['o']
  entries=[dict(key=38+i,line=row[0],friend=row[1],template=row[2],words=list(struct.unpack_from('<16H',data,RECORDS+32*i))) for i,row in enumerate(meta['r'])]
  if len(entries)!=count or any(len(row)!=3 for row in meta['r']):raise ValueError('Invalid record metadata.')
  validate_entries(data,entries);transfer.validate_mappings(mappings)
  if set(pages)!=set(mappings)|{r['line'] for r in entries}:raise ValueError('Invalid page membership.')
  for line,choices in pages.items():
   if line not in range(7) or len(choices)<2:raise ValueError('Invalid page list.')
   for index,page in enumerate(choices):
    if not (1 if version==4 and index>0 else 2)<=len(page)<=9 or (len(set(page))!=len(page) and not (version>=5 and index>0 and len(page)==2 and page[0]==page[1])):raise ValueError('Invalid page lengths.')
    if any(type(k) is not int or not 0<=k<(38 if index==0 else 38+count) for k in page):raise ValueError('Invalid page record key.')
   expected={r['key'] for r in entries if r['line']==line}
   if version>=3 and line in mappings:
    donor_line=mappings[line]
    donor_slots=merge_slots(core.read_source_lines(data)[0],entries,{int(k):v for k,v in meta.get('s',{}).items()})[donor_line]
    physical=core.read_partner_records(data)
    donor_active=[k for k in donor_slots if k>=38 or physical[k].active]
    expected.update(k for k in donor_active[1:] if k>=38)
   if sorted(set(k for page in choices[1:] for k in page if k>=38) if version>=5 else [k for page in choices[1:] for k in page if k>=38])!=sorted(expected):raise ValueError('Invalid additional page records.')
  if len(order)!=44+count or any(type(k) is not int or not 0<=k<38+count for k in order) or any(order.count(k)!=1 for k in range(38,38+count)):raise ValueError('Invalid Viewer order.')
  code,labels=(assemble_v1 if version==1 else assemble_v2 if version!=4 else assemble)(pages);adapter,_=adapters(count)
  if code_len!=len(code) or data[CODE_START:CODE_START+code_len]!=code or not transfer.hooks_match(data,transfer.expected_hooks(labels)):raise ValueError('Paging code does not match metadata.')
  if data[ADAPTERS:ADAPTERS+len(adapter)]!=adapter or data[TABLE:TABLE+4*len(order)]!=struct.pack('<'+'I'*len(order),*order):raise ValueError('Viewer data does not match metadata.')
  metadata_end=END
  if version==4:
   gate,_=assemble_gate();metadata_end=GATE_OFFSET
   if META+json_len>metadata_end or data[GATE_OFFSET:GATE_OFFSET+len(gate)]!=gate or any(data[GATE_OFFSET+len(gate):END]):raise ValueError('Invalid singleton navigation code.')
  for a,b in [(CODE_START+code_len,ADAPTERS),(ADAPTERS+len(adapter),TABLE),(TABLE+4*len(order),RECORDS),(RECORDS+32*count,META),(META+json_len,metadata_end)]:
   if any(data[a:b]):raise ValueError('Unexpected occupied additional Partner padding.')
 except (KeyError,TypeError,IndexError,UnicodeError,json.JSONDecodeError,struct.error,zlib.error) as ex:raise ValueError('Invalid additional Partner metadata.') from ex
 check_viewer(data,count)
 slots={int(k):v for k,v in meta['s'].items()} if 's' in meta else None
 if slots is not None:validate_slots(data,entries,slots)
 return dict(entries=entries,mappings=mappings,pages=pages,viewer_order=order,format='current',slots=slots)

def read_state(data):
 transfer.check_layout(data)
 if data[START:START+8]==MAGIC:return _decode(data)
 if legacy_detected(data):
  if data[START:START+8]==b'D3XPRB02':
   from d3_extra2_layout import read_probe
   old=read_probe(data);records=old['words']
  else:
   from d3_additional_partners_legacy import read_state as read_old
   old=read_old(data);records=[old['words']]
  entries=[dict(key=38+i,line=1,friend=-1,template=-1,words=list(w)) for i,w in enumerate(records)]
  return dict(entries=entries,mappings={1:3},pages=old['pages'],viewer_order=old['viewer_order'],format='legacy')
 check_viewer(data);mappings=transfer.read_state(data)
 if not mappings and any(data[START:END]):raise ValueError('Additional Partner storage is occupied by another patch.')
 return dict(entries=[],mappings=mappings,pages=transfer.derive_pages(data,mappings),viewer_order=list(struct.unpack_from('<44I',data,core.ORDER_OFFSET)),format='stock')

def _restore_viewer(data):
 # Caller authenticates the complete input layout before invoking this.
 for off,(before,after) in viewer_edits(1).items():data[off:off+2*len(before)]=pack(before)
 check_viewer(data)

def install_into(data,entries,mappings,lines,slot_order=None):
 """Internal writer. Caller has authenticated the previous state on a copy."""
 validate_entries(data,entries)
 if slot_order is not None:validate_slots(data,entries,slot_order)
 pages=derive_pages(data,entries,mappings,lines,slot_order)
 order=derive_order(data,entries,lines,slot_order)
 code,labels=assemble_v2(pages);adapter,_=adapters(len(entries))
 if not entries:raise ValueError('Use the restore path when removing the last additional partner.')
 if CODE_START+len(code)>CODE_END:raise ValueError('Paging code exceeds the reserved area.')
 meta={'r':[[r['line'],r['friend'],r['template']] for r in entries],'m':mappings,'p':pages,'o':order}
 if slot_order is not None:meta['s']=slot_order
 payload=zlib.compress(json.dumps(meta,sort_keys=True,separators=(',',':')).encode())
 if META+len(payload)>END:raise ValueError('Settings exceed the reserved metadata area.')
 data[START:END]=bytes(END-START);data[START:START+8]=MAGIC
 struct.pack_into('<4H',data,START+8,5,len(entries),len(code),len(payload))
 data[CODE_START:CODE_START+len(code)]=code;data[ADAPTERS:ADAPTERS+len(adapter)]=adapter
 data[TABLE:TABLE+4*len(order)]=struct.pack('<'+'I'*len(order),*order)
 for i,r in enumerate(entries):data[RECORDS+32*i:RECORDS+32*(i+1)]=pack(r['words'])
 data[META:META+len(payload)]=payload;data[START+16:START+48]=hashlib.sha256(data[CODE_START:END]).digest()
 for off,words in transfer.expected_hooks(labels).items():data[off:off+2*len(words)]=pack(words)
 for off,(before,after) in viewer_edits(len(entries)).items():data[off:off+2*len(after)]=pack(after)
 _decode(data)

def rebuild(data,lines,state):
 install_into(data,state['entries'],state['mappings'],lines,merge_slots(lines,state['entries'],state.get('slots')) if state.get('slots') else None)

def update_bytes(original,entries,mappings=None):
 state=read_state(original);entries=[{**r,'words':list(r['words'])} for r in entries]
 key_map={r['key']:38+i for i,r in enumerate(entries)}
 old_slots=state.get('slots')
 if old_slots:old_slots={line:[key_map[k] if k>=38 else k for k in values if k<38 or k in key_map] for line,values in old_slots.items()}
 for i,r in enumerate(entries):r['key']=38+i;r['words'][2]=r['line']
 mappings=dict(state['mappings'] if mappings is None else mappings)
 validate_entries(original,entries);transfer.validate_mappings(mappings)
 if entries==state['entries'] and mappings==state['mappings'] and derive_pages(original,entries,mappings,slot_order=state.get('slots'))==state['pages']:return bytes(original)
 work=bytearray(original)
 if transfer.is_probe_c(work):transfer.strip_probe_c(work)
 if state['format']!='stock':_restore_viewer(work)
 # Clear only previously authenticated owned storage; restore stock hooks for the core.
 work[START:END]=bytes(END-START)
 for off,words in transfer.STOCK.items():work[off:off+2*len(words)]=pack(words)
 lines,*_=core._synchronize_existing_bin_without_transfers(work)
 if entries:install_into(work,entries,mappings,lines,merge_slots(lines,entries,old_slots) if old_slots else None)
 else:transfer.install_into(work,mappings,lines)
 if len(work)!=len(original) or work[:0xE000]!=original[:0xE000]:raise RuntimeError('Unexpected file-size/startup change.')
 if work[core.LINK_TABLE_OFFSET:core.LINK_TABLE_END]!=original[core.LINK_TABLE_OFFSET:core.LINK_TABLE_END]:raise RuntimeError('Unexpected Link table change.')
 read_state(work);return bytes(work)

def friend_assets(data,index,words):
 if type(index) is not int or not 0<=index<91:raise ValueError('Choose a Friend row from 0 to 90.')
 friend=struct.unpack_from('<6H',data,0x9DEEA+12*index);out=list(words)
 for dest,src in ((4,2),(5,1),(13,3),(14,4)):out[dest]=friend[src]
 return out

def new_entry(data,line,friend,template):
 if type(template) is not int or not 0<=template<38:raise ValueError('Choose an original Partner template.')
 words=list(core.read_partner_records(data)[template].words);words[2]=line;words=friend_assets(data,friend,words)
 validate_words(data,words,line)
 return dict(key=38+len(read_state(data)['entries']),line=line,friend=friend,template=template,words=words)

def commit(path,result,before):
 path=Path(path)
 if path.read_bytes()!=before:raise ValueError('BIN changed while saving. Refresh and retry.')
 if result!=before:core.atomic_write(path,result)
 return result

def save(path,entry,expected_digest,key=None):
 path=Path(path);before=path.read_bytes()
 if digest(before)!=expected_digest:raise ValueError('The BIN changed since Refresh. Refresh and reapply the edit.')
 state=read_state(before);entries=state['entries']
 if key is None:entries.append(entry)
 else:
  if type(key) is not int or not 38<=key<38+len(entries):raise ValueError('Selected record is missing.')
  entries[key-38]=entry
 return commit(path,update_bytes(before,entries),before)

def remove(path,key,expected_digest):
 path=Path(path);before=path.read_bytes()
 if digest(before)!=expected_digest:raise ValueError('The BIN changed since Refresh. Refresh before removing.')
 entries=read_state(before)['entries']
 if type(key) is not int or not 38<=key<38+len(entries):raise ValueError('Selected record is missing.')
 del entries[key-38];return commit(path,update_bytes(before,entries),before)

def export_csv(path,entry):
 with Path(path).open('w',encoding='utf-8',newline='') as f:
  writer=csv.writer(f);writer.writerow(CSV_HEADERS);writer.writerow([entry['key'],entry['line'],entry['friend'],entry['template']]+entry['words'])
def import_csv(path,key):
 with Path(path).open(encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=list(CSV_HEADERS):raise ValueError('Use a CSV exported by this Additional Partners version.')
  rows=list(reader)
 if len(rows)!=1:raise ValueError('CSV must contain one selected record.')
 try:
  values=[int(rows[0][n].strip(),0) for n in CSV_HEADERS]
 except (ValueError,TypeError,AttributeError) as ex:raise ValueError('CSV fields must be integers.') from ex
 if values[0]!=key:raise ValueError('CSV belongs to a different record. Select its matching record first.')
 return dict(key=values[0],line=values[1],friend=values[2],template=values[3],words=values[4:])

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('bin',type=Path);p.add_argument('--show',action='store_true');p.add_argument('--line',type=int);p.add_argument('--friend',type=int);p.add_argument('--template',type=int);p.add_argument('--output',type=Path)
 args=p.parse_args();data=args.bin.read_bytes();state=read_state(data)
 if args.show:print(json.dumps(state,indent=2));return
 if None in (args.line,args.friend,args.template) or not args.output:p.error('Use --show or --line N --friend N --template N --output NEW.bin')
 if args.output.exists():p.error('Output exists. Use a new filename.')
 entry=new_entry(data,args.line,args.friend,args.template);result=update_bytes(data,state['entries']+[entry])
 with args.output.open('xb') as f:f.write(result)
 print(args.output,digest(result))
if __name__=='__main__':main()
