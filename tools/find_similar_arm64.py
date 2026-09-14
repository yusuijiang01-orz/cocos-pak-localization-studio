#!/usr/bin/env python3
from __future__ import annotations
import argparse, difflib, struct
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_IMM, CS_OP_MEM, CS_OP_REG

def elf_va_to_off(d,va):
 e='<' if d[5]==1 else '>'; phoff,=struct.unpack_from(e+'Q',d,0x20); ents,n=struct.unpack_from(e+'HH',d,0x36)
 for i in range(n):
  o=phoff+i*ents; typ,flags,fo,fva,pa,fs,ms,al=struct.unpack_from(e+'IIQQQQQQ',d,o)
  if typ==1 and fva<=va<fva+ms:return fo+va-fva
 raise ValueError('VA not mapped')

def macho_info(d):
 ncmds,=struct.unpack_from('<I',d,16); off=32; segs=[]; starts=None
 for _ in range(ncmds):
  cmd,sz=struct.unpack_from('<II',d,off)
  if cmd==0x19:
   name=d[off+8:off+24].rstrip(b'\0').decode(); va,vs,fo,fs=struct.unpack_from('<QQQQ',d,off+24); segs.append((name,va,vs,fo,fs))
  if cmd==0x26: starts=struct.unpack_from('<II',d,off+8)
  off+=sz
 text=next(x for x in segs if x[0]=='__TEXT'); dataoff,size=starts; blob=d[dataoff:dataoff+size]
 vals=[]; cur=0;i=0
 while i<len(blob):
  v=0;shift=0
  while i<len(blob):
   x=blob[i];i+=1;v|=(x&0x7f)<<shift
   if not x&0x80:break
   shift+=7
  if v==0:break
  cur+=v; vals.append(text[1]+cur)
 return segs,vals

def mva_to_off(segs,va):
 for _,sva,vs,fo,fs in segs:
  if sva<=va<sva+vs:return fo+va-sva
 raise ValueError('VA not mapped')

md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);md.detail=True
def tokens(code,addr,limit=140):
 out=[]
 for n,i in enumerate(md.disasm(code,addr)):
  if n>=limit:break
  ops=[]
  for o in i.operands:
   if o.type==CS_OP_REG:ops.append('r')
   elif o.type==CS_OP_IMM:ops.append('i')
   elif o.type==CS_OP_MEM:ops.append('m')
   else:ops.append('x')
  out.append(i.mnemonic+':'+'/'.join(ops))
 return out

ap=argparse.ArgumentParser();ap.add_argument('elf',type=Path);ap.add_argument('elf_va',type=lambda x:int(x,0));ap.add_argument('elf_size',type=lambda x:int(x,0));ap.add_argument('macho',type=Path);ap.add_argument('--top',type=int,default=20);a=ap.parse_args()
ed=a.elf.read_bytes();mdt=a.macho.read_bytes(); eo=elf_va_to_off(ed,a.elf_va); target=tokens(ed[eo:eo+a.elf_size],a.elf_va)
segs,starts=macho_info(mdt); results=[]
for idx,va in enumerate(starts):
 end=starts[idx+1] if idx+1<len(starts) else va+0x400
 size=min(end-va,max(a.elf_size*2,0x100)); off=mva_to_off(segs,va); cand=tokens(mdt[off:off+size],va,len(target)+30)
 if len(cand)<max(5,len(target)//4):continue
 score=difflib.SequenceMatcher(None,target,cand,autojunk=False).ratio()
 results.append((score,va,end-va,len(cand)))
for score,va,size,n in sorted(results,reverse=True)[:a.top]:print(f'{score:.4f}\t{va:#x}\t{size:#x}\t{n}')
