"""Bounded emitted-instruction executor, not a full device emulator."""
import struct
from d3_paging_codegen import BASE
class CPU:
 def __init__(self,rom):
  self.rom=rom;self.m={};self.r=[0]*8;self.ds=0;self.c=0;self.z=False;self.pc=BASE;self.writes=[];self.calls=[];self.unlocked=lambda x:True
 def read(self,a):
  if a in self.m:return self.m[a]
  off=(a-0x9000)*2
  if 0<=off<len(self.rom):return struct.unpack_from('<H',self.rom,off)[0]
  return 0xA55A
 def write(self,a,v):assert a<0x9000;self.m[a]=v&65535;self.writes.append(a)
 def push(self,v):self.write(self.r[0],v);self.r[0]=(self.r[0]-1)&65535
 def pop(self):self.r[0]=(self.r[0]+1)&65535;return self.read(self.r[0])
 def fetch(self):v=self.read(self.pc);self.pc+=1;return v
 def alu(self,kind,rd,v):
  lhs=self.r[rd]
  if kind in (0,1):
   n=lhs+v+(self.c if kind==1 else 0);self.c=n>65535;self.r[rd]=n&65535;self.z=self.r[rd]==0
  elif kind in (2,4):
   n=(lhs-v)&65535;self.c=lhs>=v;self.z=n==0
   if kind==2:self.r[rd]=n
  elif kind==9:self.r[rd]=v
  else:raise AssertionError(('alu',kind))
 def run(self,stop,max_steps=10000):
  for _ in range(max_steps):
   if self.pc==stop:return
   if self.pc==0x571FC:
    self.calls.append('stock_modulo');num=self.read(self.r[0]+3);den=self.read(self.r[0]+4);self.r[1]=num%den
    sr=self.pop();lo=self.pop();self.ds=sr>>10;self.pc=((sr&63)<<16)|lo;continue
   at=self.pc;op=self.fetch();rd=(op>>9)&7;mode=(op>>6)&7;rs=op&7;kind=op>>12
   if op==0x9A90:
    sr=self.pop();self.pc=((sr&63)<<16)|self.pop();self.ds=sr>>10;continue
   if op&0xFFC0==0xFE00:self.ds=op&63;continue
   if op&0xFFF8==0xF028:self.ds=self.r[rs]&63;continue
   if op&0xFFC0==0xFE80:self.pc=((op&63)<<16)|self.fetch();continue
   if op&0xFFC0==0xF040:
    target=((op&63)<<16)|self.fetch()
    if BASE<=target<0x60000:
     self.push(self.pc&65535);self.push((self.ds<<10)|(self.pc>>16));self.pc=target;continue
    assert target==0x2F4F8,hex(target)
    logical=self.read(self.r[0]+1);self.calls.append(logical)
    self.r[1]=int(self.unlocked(logical));self.r[2:5]=[0x1234,0xABCD,0x6789]
    continue
   if op&0xFFC0 in (0x4E00,0x5E00,0xEE00):
    take=(not self.z if op&0xFFC0==0x4E00 else self.z if op&0xFFC0==0x5E00 else True)
    if take:self.pc+=op&63
    continue
   if op&0xF1FF==0xD088: # push one register
    self.push(self.r[rd]);continue
   if op&0xF1FF==0x9088: # pop one into encoded rd+1
    self.r[rd+1]=self.pop();continue
   if mode==0:
    a=(self.r[5]+(op&63))&65535
    if kind==13:self.write(a,self.r[rd])
    else:self.alu(kind,rd,self.read(a))
    continue
   if mode==1:
    self.alu(kind,rd,op&63);continue
   if mode==4:
    sub=(op>>3)&7
    if sub==0:self.alu(kind,rd,self.r[rs]);continue
    if sub==1:
     val=self.fetch()
     if kind in (0,1,2):
      # three-operand immediate; destination gets lhs register rs
      self.r[rd]=self.r[rs]
     self.alu(kind,rd,val);continue
   if mode==3:
    # indirect operand: bit5 chooses DS, bits3-4 update the address register
    far=bool(op&32);update=(op>>3)&3;address=(self.ds<<16 if far else 0)|self.r[rs]
    value=self.read(address)
    if update==2:self.r[rs]=(self.r[rs]+1)&65535
    elif update!=0:raise AssertionError(('unsupported indirect',hex(op)))
    if kind==13:self.write(address,self.r[rd])
    else:self.alu(kind,rd,value)
    continue
   raise AssertionError(f'Unsupported {op:04x} at {at:06x}')
  raise AssertionError('instruction budget exceeded')
