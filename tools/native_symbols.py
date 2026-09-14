#!/usr/bin/env python3
from __future__ import annotations
import argparse, struct
from pathlib import Path


def elf_symbols(data: bytes):
    e = '<' if data[5] == 1 else '>'
    shoff, = struct.unpack_from(e+'Q', data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from(e+'HHH', data, 0x3A)
    sections=[]
    for i in range(shnum):
        o=shoff+i*shentsize
        name,typ,flags,addr,off,size,link,info,align,entsize=struct.unpack_from(e+'IIQQQQIIQQ',data,o)
        sections.append(dict(nameoff=name,type=typ,addr=addr,off=off,size=size,link=link,entsize=entsize))
    shstr=sections[shstrndx]; names=data[shstr['off']:shstr['off']+shstr['size']]
    def cstr(blob,o): return blob[o:blob.find(b'\0',o)].decode('utf-8','replace') if o<len(blob) else ''
    for s in sections: s['name']=cstr(names,s['nameoff'])
    out=[]
    for s in sections:
        if s['type'] not in (2,11) or not s['entsize']: continue
        st=sections[s['link']]; strings=data[st['off']:st['off']+st['size']]
        for o in range(s['off'],s['off']+s['size'],s['entsize']):
            no,info,other,shndx,val,size=struct.unpack_from(e+'IBBHQQ',data,o)
            name=cstr(strings,no)
            if name and val: out.append((val,size,name))
    return out

def elf_file_to_va(data: bytes, file_off: int):
    e='<' if data[5]==1 else '>'; phoff,=struct.unpack_from(e+'Q',data,0x20); ents,n=struct.unpack_from(e+'HH',data,0x36)
    for i in range(n):
        o=phoff+i*ents; typ,flags,off,va,pa,filesz,memsz,align=struct.unpack_from(e+'IIQQQQQQ',data,o)
        if typ==1 and off<=file_off<off+filesz: return va+(file_off-off)
    return file_off

def macho_file_to_va(data: bytes, file_off: int):
    ncmds,=struct.unpack_from('<I',data,16); off=32
    for _ in range(ncmds):
        cmd,cmdsize=struct.unpack_from('<II',data,off)
        if cmd==0x19:
            vmaddr,vmsize,fo,fs=struct.unpack_from('<QQQQ',data,off+24)
            if fo<=file_off<fo+fs: return vmaddr+(file_off-fo)
        off+=cmdsize
    return file_off


def macho_symbols(data: bytes):
    magic=data[:4]
    if magic != bytes.fromhex('cffaedfe'): raise ValueError(f'unsupported Mach-O magic {magic.hex()}')
    e='<'; ncmds,=struct.unpack_from(e+'I',data,16); off=32; symtab=None; export_info=None; image_base=None
    for _ in range(ncmds):
        cmd,cmdsize=struct.unpack_from(e+'II',data,off)
        if cmd==2: symtab=struct.unpack_from(e+'IIII',data,off+8)
        if cmd==0x19:
            vmaddr,=struct.unpack_from(e+'Q',data,off+24)
            fileoff,=struct.unpack_from(e+'Q',data,off+40)
            if fileoff==0 and image_base is None: image_base=vmaddr
        if cmd in (0x22,0x80000022):
            vals=struct.unpack_from(e+'IIIIIIIIII',data,off+8)
            export_info=(vals[8],vals[9])
        off+=cmdsize
    out=[]
    if symtab:
        symoff,nsyms,stroff,strsize=symtab; strings=data[stroff:stroff+strsize]
        for i in range(nsyms):
            no,typ,sect,desc,val=struct.unpack_from(e+'IBBHQ',data,symoff+i*16)
            if no>=len(strings) or not val: continue
            end=strings.find(b'\0',no); name=strings[no:end].decode('utf-8','replace')
            if name: out.append((val,0,name))
    if export_info and export_info[1]:
        exoff,exsize=export_info; blob=data[exoff:exoff+exsize]
        def uleb(pos):
            val=shift=0
            while pos<len(blob):
                x=blob[pos];pos+=1;val|=(x&0x7f)<<shift
                if not x&0x80:return val,pos
                shift+=7
            return val,pos
        seen=set()
        def walk(pos,prefix):
            if pos in seen or pos>=len(blob):return
            seen.add(pos); term,p=uleb(pos); term_end=p+term
            if term:
                flags,q=uleb(p); addr,q=uleb(q)
                out.append(((image_base or 0)+addr,0,prefix))
            p=term_end
            if p>=len(blob):return
            count=blob[p];p+=1
            for _ in range(count):
                end=blob.find(b'\0',p); edge=blob[p:end].decode('utf-8','replace'); child,p2=uleb(end+1);p=p2;walk(child,prefix+edge)
        walk(0,'')
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('binary',type=Path); ap.add_argument('--contains',default=''); ap.add_argument('--near',type=lambda x:int(x,0)); ap.add_argument('--file-offset',action='store_true'); args=ap.parse_args()
    data=args.binary.read_bytes(); syms=elf_symbols(data) if data[:4]==b'\x7fELF' else macho_symbols(data)
    if args.near is not None and args.file_offset:
        args.near=elf_file_to_va(data,args.near) if data[:4]==b'\x7fELF' else macho_file_to_va(data,args.near)
    if args.contains: syms=[s for s in syms if args.contains.lower() in s[2].lower()]
    if args.near is not None: syms=sorted(syms,key=lambda s:abs(s[0]-args.near))[:30]
    else: syms=sorted(syms)
    for val,size,name in syms[:1000]: print(f'{val:#x}\t{size:#x}\t{name}')

if __name__=='__main__': main()
