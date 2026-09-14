#!/usr/bin/env python3
import argparse, struct
from pathlib import Path

p=argparse.ArgumentParser(); p.add_argument('binary',type=Path); p.add_argument('--minimum',type=int,default=64); a=p.parse_args()
d=a.binary.read_bytes(); n,=struct.unpack_from('<I',d,16); o=32
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,o)
    if cmd==0x19:
        seg=d[o+8:o+24].split(b'\0')[0].decode(); vm,vs,fo,fs,maxp,initp,nsects,flags=struct.unpack_from('<QQQQIIII',d,o+24)
        so=o+72
        for i in range(nsects):
            sect=d[so:so+16].split(b'\0')[0].decode(); sseg=d[so+16:so+32].split(b'\0')[0].decode()
            addr,size,off=struct.unpack_from('<QQI',d,so+32)
            sflags,=struct.unpack_from('<I',d,so+64)
            print(f'{sseg},{sect}: va={addr:#x} off={off:#x} size={size:#x} flags={sflags:#x}')
            if initp & 4 and off and size:
                blob=d[off:off+size]; j=0
                while j<len(blob):
                    if blob[j] not in (0,): j+=1; continue
                    k=j+1
                    while k<len(blob) and blob[k]==0:k+=1
                    if k-j>=a.minimum: print(f'  ZERO cave va={addr+j:#x} off={off+j:#x} len={k-j:#x}')
                    j=k
            so+=80
    o+=sz
