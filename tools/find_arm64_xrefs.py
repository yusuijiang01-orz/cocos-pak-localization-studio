#!/usr/bin/env python3
from pathlib import Path
import argparse, struct
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_IMM, CS_OP_REG

ap=argparse.ArgumentParser();ap.add_argument('binary',type=Path);ap.add_argument('target',type=lambda x:int(x,0));a=ap.parse_args();d=a.binary.read_bytes()
ncmds,=struct.unpack_from('<I',d,16);off=32;text=None
for _ in range(ncmds):
 cmd,sz=struct.unpack_from('<II',d,off)
 if cmd==0x19:
  seg=d[off+8:off+24].rstrip(b'\0');nsects,=struct.unpack_from('<I',d,off+64);so=off+72
  for j in range(nsects):
   sect=d[so:so+16].rstrip(b'\0');sg=d[so+16:so+32].rstrip(b'\0');addr,size=struct.unpack_from('<QQ',d,so+32);fo,=struct.unpack_from('<I',d,so+48)
   if sect==b'__text':text=(addr,size,fo)
   so+=80
 off+=sz
va,size,fo=text;md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);md.detail=True;ins=list(md.disasm(d[fo:fo+size],va));hits=[]
for idx,i in enumerate(ins):
 if i.mnemonic!='adrp' or len(i.operands)<2 or i.operands[1].type!=CS_OP_IMM:continue
 page=i.operands[1].imm;reg=i.operands[0].reg
 for j in ins[idx+1:idx+5]:
  if j.mnemonic=='add' and len(j.operands)>=3 and j.operands[0].type==CS_OP_REG and j.operands[0].reg==reg and j.operands[2].type==CS_OP_IMM:
   if page+j.operands[2].imm==a.target:hits.append((i.address,j.address,j.op_str))
for h in hits:print(f'{h[0]:#x}\t{h[1]:#x}\t{h[2]}')
