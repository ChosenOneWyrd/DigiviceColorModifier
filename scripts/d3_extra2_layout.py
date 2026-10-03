#!/usr/bin/env python3
"""Add a second external Partner to a copy of confirmed C / Additional Partners v1."""
import argparse,hashlib,json,os,struct,sys,tempfile
from pathlib import Path
import d3_additional_partners_legacy as one
import d3_transfer_evolution_lines as transfer
from two_partner_codegen import assemble
from viewer_sites import FLAGS,RESULT,ANIM

OUTPUT='D3_external_partners_D_tyranomon_kuwagamon.bin'
RECORDS=(0xADF00,0xADF20)
TABLE,CODE,META,END=0xAD400,0xAD500,0xAD600,0xADE00
MAGIC=b'D3EXPRB2'

def sha(b):return hashlib.sha256(b).hexdigest()
def pack(w):return struct.pack('<'+'H'*len(w),*w)
class Asm:
 def __init__(self):self.words=[];self.labels={};self.fix=[]
 def emit(self,*w):self.words.extend(w)
 def label(self,n):self.labels[n]=CODE//2+0x9000+len(self.words)
 def jump(self,n):self.emit(0xFE85);self.fix.append((len(self.words),n));self.emit(0)
 def if_equal(self,n):self.emit(0x4E02);self.jump(n)
 def finish(self):
  for i,n in self.fix:self.words[i]=self.labels[n]&65535
  return pack(self.words)

def adapters():
 a=Asm();e=a.emit
 a.label('resolve');e(0x0908,3,0x98C4)
 for key in (38,39):e(0x4840+key);a.if_equal(f'record_{key}')
 e(0xFE84,0xC70E)
 for key,offset in zip((38,39),RECORDS):
  a.label(f'record_{key}');e(0x9309,(offset//2+0x9000)&65535,0x9445,0x9A90)
 a.label('unlock');e(0x0908,3,0x98C4)
 for key in (38,39):e(0x4840+key);a.if_equal(f'unlock_{key}')
 e(0xFE82,0xF4F8)
 for key,offset in zip((38,39),RECORDS):
  a.label(f'unlock_{key}')
  # Read this record's shared ID, preserving the caller's virtual record key.
  e(0x970B,((offset//2+0x9000)+1)&65535,0xFE05,0x96E3,0xD688,0xF042,0xF4F8,0x0041,0x9A90)
 return a.finish(),a.labels

def viewer_changes():
 changes=[]
 for off in FLAGS:changes.append((off,0x81,0x82))
 for off in RESULT:changes.append((off,0xAE,0xB0))
 for off in ANIM:changes.append((off,0xAF,0xB1))
 for off in (0x5B384,0x5B3C2):changes.append((off,0xB0,0xB2))
 for off in (0x5B498,0x5B96C):changes.append((off,0x486C,0x486D))
 for off in (0x5BBF4,0x5CD74):changes.append((off,0x966D,0x966E))
 # Only the unlock entry moves; the record-resolver entry still starts at FA80.
 changes.append((0x5B2CC,one.ADAPTER_LABELS['unlock']&65535,adapters()[1]['unlock']&65535))
 return changes

def auth(data,payload):
 return (payload+bytes(data[one.CODE_START:one.VIEW_HEADER])+bytes(data[TABLE:TABLE+184])
         +bytes(data[CODE:CODE+len(adapters()[0])])+bytes(data[RECORDS[0]:RECORDS[1]+32]))

def read_probe(data):
 transfer.check_layout(data)
 if data[META:META+8]!=MAGIC:raise ValueError('Not a two-record D probe.')
 version,length=struct.unpack_from('<HH',data,META+8)
 if version!=2 or not 0<length<=END-META-48 or any(data[META+12:META+16]):raise ValueError('Invalid probe metadata.')
 payload=bytes(data[META+48:META+48+length])
 if any(data[META+48+length:END]) or data[META+16:META+48]!=hashlib.sha256(auth(data,payload)).digest():raise ValueError('Probe metadata checksum mismatch.')
 state=json.loads(payload);pages={int(k):v for k,v in state['pages'].items()};state['pages']=pages
 if set(pages)!={1}:raise ValueError('Invalid probe pages.')
 host,donor=pages[1]
 if not 2<=len(host)<=9 or not 3<=len(donor)<=8 or donor[-2:]!=[38,39] or len(set(host+donor))!=len(host+donor):raise ValueError('Invalid probe page choices.')
 if any(type(r) is not int or not 0<=r<40 for r in host+donor):raise ValueError('Invalid record key.')
 order=state['viewer_order']
 if len(order)!=46 or order.count(38)!=1 or order.count(39)!=1 or order.index(39)!=order.index(38)+1:raise ValueError('Invalid Viewer order.')
 if any(type(r) is not int or not 0<=r<40 for r in order):raise ValueError('Invalid Viewer record.')
 words=[list(struct.unpack_from('<16H',data,off)) for off in RECORDS]
 for w in words:one.validate_words(data,w)
 code,labels=assemble(pages)
 if data[one.START:one.START+8]!=b'D3XPRB02' or struct.unpack_from('<HHI',data,one.START+8)!=(2,len(code),RECORDS[0]):raise ValueError('Invalid paging header.')
 if data[one.START+16:one.START+48]!=hashlib.sha256(code).digest() or any(data[one.START+48:one.CODE_START]):raise ValueError('Paging checksum mismatch.')
 if data[one.CODE_START:one.CODE_START+len(code)]!=code or any(data[one.CODE_START+len(code):one.VIEW_HEADER]):raise ValueError('Paging data mismatch.')
 if not transfer.hooks_match(data,transfer.expected_hooks(labels)):raise ValueError('Paging hook mismatch.')
 if data[one.VIEW_HEADER:one.VIEW_HEADER+16]!=b'D3VIEW02'+struct.pack('<II',TABLE,46):raise ValueError('Viewer header mismatch.')
 if data[TABLE:TABLE+184]!=struct.pack('<46I',*order) or data[CODE:CODE+len(adapters()[0])]!=adapters()[0]:raise ValueError('Viewer data mismatch.')
 # Normalize all known Viewer operand changes and compare its full code body to C.
 view=bytearray(data[0x5B276:0x5D0E0])
 for off,before,after in viewer_changes():
  if struct.unpack_from('<H',data,off)[0]!=after:raise ValueError('Viewer operand mismatch.')
  struct.pack_into('<H',view,off-0x5B276,before)
 if hashlib.sha256(view).hexdigest()!=one.VIEWER_CODE_HASH:raise ValueError('Unrecognized Viewer code.')
 for a,b in [(one.VIEW_HEADER+16,TABLE),(TABLE+184,CODE),(CODE+len(adapters()[0]),META),(END,RECORDS[0]),(RECORDS[1]+32,0xAE000)]:
  if any(data[a:b]):raise ValueError('Unexpected occupied probe padding.')
 return {**state,'words':words}
