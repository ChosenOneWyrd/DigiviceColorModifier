#!/usr/bin/env python3
"""Edit and preserve one hardware-confirmed external D-3 Partner record."""
from __future__ import annotations
import argparse,csv,hashlib,json,struct
from pathlib import Path
from d3_extra_layout import *
from d3_extra_paging_codegen import assemble

START,CODE_START=0xAD060,0xAD0A0
VIEW_HEADER,TABLE,ADAPTERS,RECORD=0xAD3C0,0xAD400,0xAD500,0xADF00
META,META_END=0xAD600,0xADE00
MAGIC=b'D3EXTRA1'
KEY='extra_1'
FIELDS=('stage','digimon_id','slot_type','win_requirement_for_next_evo','sprite_index','string_index',
 'evo_animation1_id','evo_animation2_id','evo_animation3_id','evo_animation4_id','evo_animation5_id',
 'background_music_during_battle_id','attack_voice_sound_id','attack_shot_sprite_index','attack_shot_sound_id','special_unlock')
HEADERS=('record_key','map_line','map_page','viewer_line')+FIELDS

def digest(b):return hashlib.sha256(b).hexdigest()
def packed(values):return struct.pack('<'+'H'*len(values),*values)
def detected(data):
 return data[START:START+8]==b'D3XPRB01' or data[VIEW_HEADER:VIEW_HEADER+8]==b'D3VIEW01' or data[META:META+8]==MAGIC

def adapter_code(alias):
 b=bytearray(ADAPTER_CODE);struct.pack_into('<H',b,UNLOCK_ALIAS_OFFSET-ADAPTERS,0x9640+alias);return bytes(b)

def validate_words(data,words):
 if len(words)!=16 or any(type(x) is not int or not 0<=x<=65535 for x in words):raise ValueError('All 16 Partner fields must be unsigned 16-bit integers.')
 if not 1<=words[0]<=5:raise ValueError('Stage must be 1–5. Removing the extra form through stage 0 is not supported in this version.')
 if words[2]!=1:raise ValueError('This version keeps extra_1 in Wormmon line 1; slot_type must remain 1.')
 if words[15] not in (0,1):raise ValueError('special_unlock must be 0 or 1.')
 import d3_evolution_core as core
 ids={r.link_id for r in core.read_partner_records(data) if r.active}
 if words[1] not in ids or words[1]>37:raise ValueError('digimon_id must share an existing active Partner ID (0–37), not a Friend ID or new Link identity.')
 seen_zero=False
 for anim in words[6:11]:
  if not anim:seen_zero=True
  elif seen_zero:raise ValueError('Evolution animation IDs must be consecutive, followed by zeros for unused fields.')

def validate_state(state):
 pages=state['pages'];order=state['viewer_order']
 if set(pages)!={1}:raise ValueError('Only the confirmed Wormmon/Tailmon transfer is supported for additional records.')
 host,donor=pages[1]
 if not 2<=len(host)<=9 or not 2<=len(donor)<=8:raise ValueError('Additional Partner paging requires 2–9 host and 2–8 second-page choices.')
 if donor[-1]!=38 or donor.count(38)!=1 or 38 in host:raise ValueError('The additional record must end Wormmon’s second page.')
 if any(type(r) is not int or r not in range(39) for r in host+donor) or len(set(host+donor))!=len(host+donor):raise ValueError('Invalid or duplicate page record keys.')
 if len(order)!=45 or order.count(38)!=1 or any(type(r) is not int or r not in range(39) for r in order):raise ValueError('Invalid 45-entry Viewer list.')

def auth_payload(data,payload):
 return payload+bytes(data[CODE_START:VIEW_HEADER])+bytes(data[TABLE:TABLE+180])+bytes(data[ADAPTERS:ADAPTERS+len(ADAPTER_CODE)])+bytes(data[RECORD:RECORD+32])

def read_state(data):
 import d3_transfer_evolution_lines as transfer
 if not detected(data):raise ValueError('No supported additional Partner found. Start with your working C TYRANOMON Viewer BIN.')
 transfer.check_layout(data)
 if hashlib.sha256(data[0x5B276:0x5D0E0]).hexdigest()!=VIEWER_CODE_HASH:raise ValueError('Viewer code differs from the confirmed C layout.')
 if data[VIEW_HEADER:VIEW_HEADER+16]!=b'D3VIEW01'+struct.pack('<II',TABLE,45):raise ValueError('Missing or changed Viewer metadata.')
 words=list(struct.unpack_from('<16H',data,RECORD));validate_words(data,words)
 if data[META:META+8]==MAGIC:
  version,length=struct.unpack_from('<HH',data,META+8)
  if version!=1 or not 0<length<=META_END-META-48 or any(data[META+12:META+16]):raise ValueError('Invalid additional Partner metadata header.')
  payload=bytes(data[META+48:META+48+length])
  if any(data[META+48+length:META_END]):raise ValueError('Unexpected data after additional Partner metadata.')
  if hashlib.sha256(auth_payload(data,payload)).digest()!=data[META+16:META+48]:raise ValueError('Additional Partner data/checksum mismatch. No changes made.')
  try:
   meta=json.loads(payload);state={'pages':{int(k):v for k,v in meta['pages'].items()},'viewer_order':meta['viewer_order']}
  except (ValueError,TypeError,KeyError) as e:raise ValueError('Invalid additional Partner metadata.') from e
 else:
  if any(data[META:META_END]) or words!=C_WORDS:raise ValueError('This is not the unmodified C external record or an authenticated later save.')
  state={'pages':C_PAGES,'viewer_order':C_ORDER}
 validate_state(state)
 code,labels=assemble(state['pages'])
 if CODE_START+len(code)>VIEW_HEADER:raise ValueError('Paging code overlaps Viewer storage.')
 if data[START:START+8]!=b'D3XPRB01' or struct.unpack_from('<HHI',data,START+8)!=(1,len(code),RECORD):raise ValueError('Invalid external paging header.')
 if data[START+16:START+48]!=hashlib.sha256(code).digest() or any(data[START+48:CODE_START]):raise ValueError('Invalid external paging digest/header padding.')
 if data[CODE_START:CODE_START+len(code)]!=code or any(data[CODE_START+len(code):VIEW_HEADER]):raise ValueError('External paging code differs from its recorded choices.')
 if not transfer.hooks_match(data,transfer.expected_hooks(labels)):raise ValueError('External paging hook mismatch.')
 if data[TABLE:TABLE+180]!=struct.pack('<45I',*state['viewer_order']):raise ValueError('Viewer order differs from metadata.')
 if data[ADAPTERS:ADAPTERS+len(ADAPTER_CODE)]!=adapter_code(words[1]):raise ValueError('Viewer record/unlock adapter mismatch.')
 for a,b in [(VIEW_HEADER+16,TABLE),(TABLE+180,ADAPTERS),(ADAPTERS+len(ADAPTER_CODE),META),(META_END,RECORD),(RECORD+32,0xAE000)]:
  if any(data[a:b]):raise ValueError(f'Unrecognized data in additional Partner storage at {a:#x}.')
 return {**state,'words':words,'record_key':KEY}

def write_state(data,state):
 import d3_transfer_evolution_lines as transfer
 validate_words(data,state['words']);validate_state(state)
 code,labels=assemble(state['pages'])
 if CODE_START+len(code)>VIEW_HEADER:raise ValueError('Too many second-page choices for the confirmed storage layout.')
 data[CODE_START:VIEW_HEADER]=code+bytes(VIEW_HEADER-CODE_START-len(code))
 struct.pack_into('<HHI',data,START+8,1,len(code),RECORD)
 data[START+16:START+48]=hashlib.sha256(code).digest()
 for off,words in transfer.expected_hooks(labels).items():data[off:off+len(words)*2]=packed(words)
 data[TABLE:TABLE+180]=struct.pack('<45I',*state['viewer_order'])
 data[ADAPTERS:ADAPTERS+len(ADAPTER_CODE)]=adapter_code(state['words'][1])
 data[RECORD:RECORD+32]=packed(state['words'])
 payload=json.dumps({'pages':state['pages'],'viewer_order':state['viewer_order']},sort_keys=True,separators=(',',':')).encode()
 if len(payload)>META_END-META-48:raise ValueError('Additional Partner metadata does not fit.')
 data[META:META_END]=bytes(META_END-META)
 data[META:META+8]=MAGIC;struct.pack_into('<HH',data,META+8,1,len(payload))
 data[META+16:META+48]=hashlib.sha256(auth_payload(data,payload)).digest();data[META+48:META+48+len(payload)]=payload
 read_state(data)

def rebuild(data,lines,state):
 import d3_evolution_core as core
 records=core.read_partner_records(data)
 host=[r for r in lines[1] if records[r].active]
 donor=[r for r in lines[3] if records[r].active][1:]
 donor=[r for r in donor if r not in host]+[38]
 order=list(struct.unpack_from('<44I',data,core.ORDER_OFFSET))
 at=len(lines[0])+len(lines[1]);expected=lines[0]+lines[1]
 if order[:at]!=expected:raise ValueError('Cannot identify the Wormmon section in the rebuilt Viewer order.')
 state={**state,'pages':{1:(host,donor)},'viewer_order':order[:at]+[38]+order[at:]}
 write_state(data,state)

def update_bytes(original,words):
 state=read_state(original);validate_words(original,words);work=bytearray(original)
 write_state(work,{**state,'words':list(words)})
 if work[:0xE000]!=original[:0xE000] or len(work)!=len(original):raise RuntimeError('Unexpected startup/size change.')
 return bytes(work)

def save(path,words,expected_digest):
 from d3_evolution_core import atomic_write
 path=Path(path);before=path.read_bytes()
 if digest(before)!=expected_digest:raise ValueError('The BIN changed since Refresh. Refresh and reapply your edit before saving.')
 after=update_bytes(before,words)
 if path.read_bytes()!=before:raise ValueError('BIN changed while saving; no changes written.')
 atomic_write(path,after);return after

def export_csv(path,state):
 with Path(path).open('w',encoding='utf-8',newline='') as f:
  w=csv.writer(f);w.writerow(HEADERS);w.writerow([KEY,1,2,1]+state['words'])

def import_csv(path):
 with Path(path).open(encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=list(HEADERS):raise ValueError('Use an Additional Partners CSV exported by this version.')
  rows=list(reader)
 if len(rows)!=1 or rows[0]['record_key']!=KEY:raise ValueError('CSV must contain exactly one extra_1 record.')
 r=rows[0]
 if [r[k].strip() for k in ('map_line','map_page','viewer_line')]!=['1','2','1']:raise ValueError('This release preserves Wormmon line 1, battle page 2, Viewer line 1.')
 try:return [int(r[k].strip(),0) for k in FIELDS]
 except ValueError as e:raise ValueError('CSV field values must be integers.') from e

def friend_assets(data,index,words):
 if type(index) is not int or not 0<=index<91:raise ValueError('Friend row must be 0–90.')
 friend=struct.unpack_from('<6H',data,0x9DEEA+12*index)
 result=list(words)
 for to,source in [(4,2),(5,1),(13,3),(14,4)]:result[to]=friend[source]
 return result

def partner_template(data,index,words):
 if type(index) is not int or not 0<=index<38:raise ValueError('Template record must be 0–37.')
 # Keep selected Friend identity/assets and the confirmed membership.
 result=list(struct.unpack_from('<16H',data,0x9D968+32*index))
 for field in (2,4,5,13,14):result[field]=words[field]
 validate_words(data,result);return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('bin',type=Path)
 g=p.add_mutually_exclusive_group(required=True);g.add_argument('--export-csv',type=Path);g.add_argument('--import-csv',type=Path);g.add_argument('--show',action='store_true')
 args=p.parse_args();data=args.bin.read_bytes();state=read_state(data)
 if args.export_csv:export_csv(args.export_csv,state)
 elif args.import_csv:save(args.bin,import_csv(args.import_csv),digest(data));print('Saved additional Partner. No .bak generated.')
 else:print(json.dumps(dict(zip(FIELDS,state['words'])),indent=2))
if __name__=='__main__':main()
