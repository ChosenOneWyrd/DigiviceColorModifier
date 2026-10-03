"""u'nSP paging code, generalized from device-confirmed probe C."""
import struct
BASE=0xAD0A0//2+0x9000

class Asm:
 def __init__(self): self.w=[];self.labels={};self.fix=[]
 def emit(self,*w):self.w.extend(w)
 def label(self,name):self.labels[name]=BASE+len(self.w)
 def jump(self,name):self.emit(0xFE85);self.fix.append((len(self.w),name));self.emit(0)
 def cond(self,condition,name):
  # Invert condition and skip a far GOTO. Avoid short-branch range assumptions.
  self.emit({'eq':0x4E02,'ne':0x5E02}[condition]);self.jump(name)
 def imm(self,r,v):self.emit(0x9040+r*0x200+v) if 0<=v<64 else self.emit(0x9108+r*0x201,v&65535)
 def load(self,r,slot):self.emit(0x9000+r*0x200+slot)
 def store(self,r,slot):self.emit(0xD000+r*0x200+slot)
 def add(self,r,n):self.emit(r*0x200+0x40+n) if 0<=n<64 else self.emit(r*0x200+0x108+r,n&65535)
 def ptr(self,lo,off=0):
  self.load(3,lo);self.load(4,lo+1);self.add(3,off);self.emit(0x1840,0xF02C)
 def parent(self,off):self.load(4,9);self.add(4,off)
 def finish(self):
  for i,n in self.fix:self.w[i]=self.labels[n]&65535
  return struct.pack('<'+'H'*len(self.w),*self.w)
def assemble(pages):
 a=Asm();e=a.emit
 a.label('paging')
 e(0xDA88,0x2052,0x0B08,1) # 18 locals; original bp at bp+18
 a.load(4,18);a.store(4,9)
 a.imm(3,0);a.parent(0xB);e(0xD6C4) # per-callback boundary flag
 # Main object in global 0x12C4. No writes before the identity checks.
 e(0xFE00);a.imm(4,0x12C4);e(0x96F4,0x98E4);a.store(3,0);a.store(4,1)
 e(0x4840);a.cond('ne','fallback') # heap objects for this firmware are near RAM
 e(0x4640);a.cond('eq','fallback')
 a.ptr(0,0x7B);e(0x98E3)
 for line in sorted(pages):
  e(0x4840+line);a.cond('eq',f'config_{line}')
 a.jump('fallback')
 for line,(host,donor) in sorted(pages.items()):
  a.label(f'config_{line}')
  for slot,value in ((12,len(host)),(13,len(donor)),(14,0x7CB4+16*host[0]),(15,0x7CB4+16*donor[0])):
   a.imm(3,value);a.store(3,slot)
  for slot,name in ((16,f'host_{line}'),(17,f'donor_{line}')):
   e(0x970B);a.fix.append((len(a.w),name));e(0);a.store(3,slot)
  a.jump('identity')
 a.label('identity')
 # Exact current map widget identity, including high half.
 a.ptr(0,0xE);e(0x94F3,0x96E3)
 a.parent(0x28);e(0x92D4,0x4501);a.cond('ne','fallback')
 e(0x98C4,0x4704);a.cond('ne','fallback')
 # Callback's internal state pointer; labels/flags must belong to main object.
 a.parent(0);e(0x96D4,0x98C4);a.store(3,2);a.store(4,3)
 for field,off in ((0x1D,0x89),(0x1F,0x92)):
  a.ptr(0,off);e(0x9503,0x9304) # r2=expected low,r1=high
  a.ptr(2,field);e(0x98F3,0x4504);a.cond('ne','fallback')
  e(0x98E3,0x4304);a.cond('ne','fallback')
 # Distinct first physical records identify the page even with equal counts.
 a.ptr(0,0x9D);e(0x92F3,0x98E3,0x4845);a.cond('ne','fallback')
 a.load(4,22);a.load(3,12);e(0x4903);a.cond('ne','try_donor')
 a.load(3,14);e(0x4303);a.cond('ne','try_donor')
 a.load(3,13);a.store(3,5);a.load(3,17);a.store(3,4);a.jump('boundary')
 a.label('try_donor')
 a.load(4,22);a.load(3,13);e(0x4903);a.cond('ne','fallback')
 a.load(3,15);e(0x4303);a.cond('ne','fallback')
 a.load(3,12);a.store(3,5);a.load(3,16);a.store(3,4)
 a.label('boundary')
 a.ptr(0,0xAF);e(0x96E3);a.load(4,22);e(0x4704);a.cond('ne','fallback')
 a.ptr(2,0x1B);e(0x96E3);a.load(4,22);e(0x4704);a.cond('ne','fallback')
 # numerator=count+index-direction; previous/next wrap are count-1 / 2*count.
 a.load(3,22);e(0x2641);a.load(4,21);e(0x4903);a.cond('eq','backward')
 a.load(3,22);e(0x0703,0x4903);a.cond('ne','fallback')
 a.imm(1,0);a.store(1,6);a.jump('switch')
 a.label('backward');a.load(1,5);e(0x2241);a.store(1,6)
 a.label('switch')
 # Save outgoing label/flags in otherwise unused event-9 stack locals. The
 # original slide animation still renders the outgoing form after the swap.
 a.parent(6);e(0x94C4)
 a.ptr(0,0x89);e(0x0702,0x1840,0xF02C,0x96E3);a.parent(0xC);e(0xD6C4)
 a.parent(6);e(0x94C4)
 a.ptr(0,0x92);e(0x0702,0x1840,0xF02C,0x96E3);a.parent(0xD);e(0xD6C4)
 a.imm(3,1);a.parent(0xB);e(0xD6C4)
 a.imm(1,0);a.store(1,7)
 a.label('loop')
 # Table holds low words of physical record pointers, all in ROM segment 5.
 a.load(3,4);e(0xFE05,0x96E3);a.store(3,8)
 a.load(4,4);a.add(4,1);a.store(4,4)
 # output candidate pointers [main+0x9D+2*i]
 a.load(2,7);e(0x0502);a.ptr(0,0x9D);e(0x0702,0x1840,0xF02C)
 a.load(1,8);e(0xD2F3);a.imm(1,5);e(0xD2E3)
 # Call the stock unlock predicate with the record's logical ID.
 a.load(3,8);a.add(3,1);e(0xFE05,0x96E3,0xD688,0xF042,0xF4F8,0x0041,0x4240)
 a.cond('eq','locked')
 a.load(3,8);a.add(3,5);e(0xFE05,0x92E3);a.store(1,10)
 a.load(3,8);a.add(3,15);e(0xFE05,0x98E3);a.imm(1,0);e(0x4840);a.cond('eq','flag_ready')
 a.imm(1,4);a.jump('flag_ready')
 a.label('locked');a.imm(1,1);a.store(1,10);a.imm(1,0x101)
 a.label('flag_ready');a.store(1,11)
 a.load(2,7);a.ptr(0,0x89);e(0x0702,0x1840,0xF02C);a.load(1,10);e(0xD2E3)
 a.load(2,7);a.ptr(0,0x92);e(0x0702,0x1840,0xF02C);a.load(1,11);e(0xD2E3)
 a.load(4,7);a.add(4,1);a.store(4,7);a.load(3,5);e(0x4903);a.cond('ne','loop')
 a.ptr(0,0xAF);a.load(1,5);e(0xD2E3)
 a.ptr(2,0x1B);a.load(1,5);e(0xD2E3)
 a.load(1,6);e(0x0052,0x9888,0x9A90)
 a.label('fallback');e(0x0052,0x9888,0xFE85,0x71FC)
 # Used only in the standard-label outgoing rendering branch of this callback.
 a.label('old_flags');e(0xD688,0x960B,0x4640,0x5E02,0x980D,0xEE01,0x98E4,0xD818,0x9488,0x9A90)
 a.label('old_label');e(0x960B,0x4640,0x5E02,0x960C,0xEE01,0x96E4,0x0908,7,0xFE81,0xA77A)
 for line,(host,donor) in sorted(pages.items()):
  a.label(f'host_{line}');e(*(0x7CB4+16*i for i in host))
  a.label(f'donor_{line}');e(*(0x7CB4+16*i for i in donor))
 return a.finish(),a.labels
