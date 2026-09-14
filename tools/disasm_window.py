#!/usr/bin/env python3
from pathlib import Path
import argparse
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_ARCH_ARM, CS_MODE_THUMB, CS_ARCH_X86, CS_MODE_32

ap=argparse.ArgumentParser(); ap.add_argument('binary',type=Path); ap.add_argument('offset',type=lambda x:int(x,0)); ap.add_argument('--size',type=lambda x:int(x,0),default=0x80); ap.add_argument('--arch',choices=['arm64','thumb','x86'],default='arm64'); ap.add_argument('--address',type=lambda x:int(x,0)); a=ap.parse_args()
b=a.binary.read_bytes()[a.offset:a.offset+a.size]
if a.arch=='arm64': md=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
elif a.arch=='thumb': md=Cs(CS_ARCH_ARM,CS_MODE_THUMB)
else: md=Cs(CS_ARCH_X86,CS_MODE_32)
for i in md.disasm(b,a.address if a.address is not None else a.offset): print(f'{i.address:#x}:\t{i.bytes.hex():<20}\t{i.mnemonic}\t{i.op_str}')
