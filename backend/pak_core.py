#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, re, struct, sys, traceback, mmap
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from parallel_config import worker_count

HEADER_SIZE=32; ENTRY_SIZE=16
class PakError(Exception): pass

class NRV2B8Reader:
    __slots__=("src","i","bb")
    def __init__(self,src:bytes): self.src=src; self.i=0; self.bb=0
    def bit(self):
        if self.bb & 0x7F: self.bb*=2
        else:
            if self.i>=len(self.src): raise PakError('NRV2B: unexpected end of bitstream')
            self.bb=self.src[self.i]*2+1; self.i+=1
        return (self.bb>>8)&1
    def byte(self):
        if self.i>=len(self.src): raise PakError('NRV2B: unexpected end of byte stream')
        v=self.src[self.i]; self.i+=1; return v

def nrv2b(src:bytes, expected:int|None=None)->bytes:
    r=NRV2B8Reader(src); out=bytearray(); last=1
    while True:
        while r.bit():
            out.append(r.byte())
            if expected is not None and len(out)>expected: raise PakError('NRV2B output exceeded expected size')
        off=2+r.bit()
        while not r.bit(): off=2*off+r.bit()
        if off==2: off=last
        else:
            off=(off-3)*0x100+r.byte()
            if off==0xFFFFFFFF: break
            off+=1; last=off
        ln=r.bit(); ln=2*ln+r.bit()
        if ln==0:
            ln=2+r.bit()
            while not r.bit(): ln=2*ln+r.bit()
            ln+=2
        if off>0xD00: ln+=1
        if off<=0 or off>len(out): raise PakError(f'NRV2B invalid back-reference: offset={off}, output={len(out)}')
        for _ in range(ln+1):
            out.append(out[-off])
            if expected is not None and len(out)>expected: raise PakError('NRV2B output exceeded expected size')
    if expected is not None and len(out)!=expected: raise PakError(f'NRV2B size mismatch: got {len(out)}, expected {expected}')
    return bytes(out)

def decompress_frame_spr(src:bytes, expected:int)->bytes:
    """Expand the classic Kingsoft TYPE_FRAME|TYPE_UCL SPR representation.

    The SPR header and palette are stored verbatim.  The normal SPROFFS table is
    replaced by XPackSprFrameInfo pairs (packed length, unpacked length), followed
    by the frame streams.  A negative unpacked length means that frame was stored
    verbatim because compression did not make it smaller.
    """
    if len(src)<40 or not src.startswith(b'SPR\0'):
        raise PakError('Method 17: invalid SPR header')
    frames,colors=struct.unpack_from('<HH',src,12)
    if frames<=0 or colors>256:
        raise PakError(f'Method 17: invalid frame/color count ({frames}/{colors})')
    # Legacy 8-bit sprites carry a 3-byte RGB palette.  The newer 32-bit SPR
    # variant (bit depth stored in Reserved[3]) has no palette at all.
    bit_depth=struct.unpack_from('<H',src,26)[0]
    head_size=32 if bit_depth==32 else 32+colors*3
    table_size=frames*8
    data_pos=head_size+table_size
    if data_pos>len(src):
        raise PakError('Method 17: truncated frame table')
    frame_info=[]
    for i in range(frames):
        packed_len,real_len=struct.unpack_from('<Ii',src,head_size+i*8)
        unpacked_len=-real_len if real_len<0 else real_len
        if packed_len<=0 or unpacked_len<=0:
            raise PakError(f'Method 17: invalid frame {i} lengths ({packed_len}/{real_len})')
        frame_info.append((packed_len,real_len,unpacked_len))
    if data_pos+sum(x[0] for x in frame_info)!=len(src):
        raise PakError('Method 17: unsupported SPR storage variant')

    frames_out=[]
    cursor=data_pos
    for i,(packed_len,real_len,unpacked_len) in enumerate(frame_info):
        block=src[cursor:cursor+packed_len]; cursor+=packed_len
        if real_len<0:
            if packed_len!=unpacked_len:
                raise PakError(f'Method 17: raw frame {i} size mismatch')
            frame=block
        else:
            frame=nrv2b(block,unpacked_len)
        frames_out.append(frame)

    offset=0
    offset_table=bytearray()
    for frame in frames_out:
        offset_table.extend(struct.pack('<II',offset,len(frame)))
        offset+=len(frame)
    data=src[:head_size]+bytes(offset_table)+b''.join(frames_out)
    if len(data)!=expected:
        raise PakError(f'Method 17 size mismatch: got {len(data)}, expected {expected}')
    return data

def byte_textlike(data:bytes)->bool:
    s=data[:65536]
    if not s or b'\x00' in s: return False
    bad=sum((b<32 and b not in (9,10,13)) or b==127 for b in s)
    return bad/max(1,len(s)) < 0.01

def decode_for_detection(data:bytes):
    # Detection only. Extraction always preserves original bytes.
    for enc in ('utf-8-sig','utf-8','gb18030','cp1252','latin1'):
        try: return data.decode(enc),enc
        except UnicodeError: pass
    return data.decode('latin1','replace'),'latin1-replace'

def guess_extension(data:bytes):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return '.png','PNG image'
    if data.startswith(b'\xff\xd8\xff'): return '.jpg','JPEG image'
    if data.startswith((b'GIF87a',b'GIF89a')): return '.gif','GIF image'
    if data.startswith(b'BM'): return '.bmp','BMP image'
    if data.startswith(b'RIFF') and len(data)>=12 and data[8:12]==b'WAVE': return '.wav','WAVE audio'
    if data.startswith(b'OggS'): return '.ogg','Ogg stream'
    if data.startswith(b'DDS '): return '.dds','DDS texture'
    if data.startswith(b'PK\x03\x04'): return '.zip','ZIP container'
    if data.startswith(b'\x1bLua'): return '.luac','Lua bytecode'
    if data.startswith((b'SPR',b'SPR ')): return '.spr','Kingsoft SPR sprite'

    if byte_textlike(data):
        raw=data[:131072]; low=raw.lower()
        # Lua/source heuristics first; these files often use legacy Vietnamese/Chinese encodings.
        lua_tokens=[b'function ',b'require(',b'local ',b' end',b'return ',b'--',b'gettask(',b'settask(',b'newtask']
        if sum(t in low for t in lua_tokens)>=2 or low.lstrip().startswith((b'function ',b'require(')):
            return '.lua','Lua/source text (legacy encoding possible)'
        if re.search(br'(?m)^\s*\[[^]\r\n]{1,160}\]\s*$',raw) and re.search(br'(?m)^\s*[^;#\r\n=]{1,200}\s*=',raw):
            return '.ini','INI-like text'
        if raw.lstrip().startswith(b'AddCommand('): return '.ini','Kingsoft command/config text'

        # Prefer byte-level physical lines for TSV/CSV detection.  Decoding a
        # legacy resource as latin1 can turn bytes such as 0x85 into Unicode
        # line separators, which makes str.splitlines() invent row breaks and
        # misclassify wide TSV tables as plain .txt.
        raw_lines=[x.rstrip(b'\r') for x in raw.split(b'\n')[:30] if x.strip()]
        if len(raw_lines)>=2:
            raw_tabs=[x.count(b'\t') for x in raw_lines]
            raw_commas=[x.count(b',') for x in raw_lines]
            if min(raw_tabs)>=1 and max(raw_tabs)-min(raw_tabs)<=2:
                return '.tsv','tabular text (byte-level)'
            if min(raw_commas)>=2 and max(raw_commas)-min(raw_commas)<=2:
                return '.csv','comma-separated text (byte-level)'

        text,enc=decode_for_detection(data)
        stripped=text.lstrip('\ufeff\x00 \t\r\n')
        if stripped.startswith(('{','[')):
            try: json.loads(stripped); return '.json',f'JSON text ({enc})'
            except Exception: pass
        if stripped.startswith('<?xml') or re.match(r'^<[-A-Za-z_:][^>]*>',stripped): return '.xml',f'XML-like text ({enc})'
        lines=[x for x in text.splitlines()[:30] if x.strip()]
        if len(lines)>=2:
            tabs=[x.count('\t') for x in lines]
            commas=[x.count(',') for x in lines]
            if min(tabs)>=1 and max(tabs)-min(tabs)<=2: return '.tsv',f'tabular text ({enc})'
            if min(commas)>=2 and max(commas)-min(commas)<=2: return '.csv',f'comma-separated text ({enc})'
        return '.txt',f'text ({enc})'
    return '.bin','binary'

def parse_header(blob:bytes):
    if len(blob)<HEADER_SIZE: raise PakError('File is smaller than 32-byte PACK header')
    sig,count,index_offset,data_field,crc=struct.unpack_from('<4sIIII',blob,0)
    if sig not in (b'PACK',b'PAK '): raise PakError(f'Unsupported signature: {sig!r}')
    if not (0<count<10_000_000): raise PakError(f'Invalid entry count: {count}')
    if index_offset<HEADER_SIZE or index_offset+count*ENTRY_SIZE>len(blob): raise PakError('Index table outside file')
    return dict(signature=sig.decode('ascii','replace'),count=count,index_offset=index_offset,data_field=data_field,crc32=crc)

_PAK_MM=None
_PAK_FH=None
_PAK_INDEX_OFFSET=0

def _extract_worker_init(pak_path:str,index_offset:int):
    global _PAK_MM,_PAK_FH,_PAK_INDEX_OFFSET
    _PAK_FH=open(pak_path,'rb')
    _PAK_MM=mmap.mmap(_PAK_FH.fileno(),0,access=mmap.ACCESS_READ)
    _PAK_INDEX_OFFSET=index_offset

def _close_worker_pak():
    global _PAK_MM,_PAK_FH,_PAK_INDEX_OFFSET
    if _PAK_MM is not None:
        _PAK_MM.close()
        _PAK_MM=None
    if _PAK_FH is not None:
        _PAK_FH.close()
        _PAK_FH=None
    _PAK_INDEX_OFFSET=0

def _extract_entry_worker(task):
    i,hid,offset,real,packed,method,out_dir=task
    e={'index':i,'id_hash_hex':f'{hid:08X}','offset':offset,'real_length':real,'packed_length':packed,'method':method}
    try:
        if packed<=0 or offset<HEADER_SIZE or offset+packed>_PAK_INDEX_OFFSET:
            raise PakError('Data block outside PACK data region')
        src=_PAK_MM[offset:offset+packed]
        if method==0: data=src
        elif method in (1,16,32): data=nrv2b(src,real)
        elif method==17: data=decompress_frame_spr(src,real)
        else: raise PakError(f'Unsupported compression method {method}')
        ext,kind=guess_extension(data); fn=f'{i:04d}_{hid:08X}{ext}'
        (Path(out_dir)/fn).write_bytes(data)
        e.update(status='ok',output=fn,type=kind,ext=ext)
    except Exception as ex:
        fn=f'{i:04d}_{hid:08X}.packed'
        try: (Path(out_dir)/fn).write_bytes(_PAK_MM[offset:offset+packed])
        except Exception: fn=None
        e.update(status='failed',error=str(ex),packed_output=fn,ext=None)
    return e

def extract_one(pak:Path, out_override:Path|None=None, workers:int|None=None):
    # Header/index parsing stays in the parent; CPU-heavy NRV2B work is distributed.
    with pak.open('rb') as f:
        head=f.read(HEADER_SIZE)
        if len(head)<HEADER_SIZE: raise PakError('File is smaller than 32-byte PACK header')
        sig,count,index_offset,data_field,crc=struct.unpack_from('<4sIIII',head,0)
        f.seek(0,2); file_size=f.tell()
    # parse_header expects bytes; validate equivalent constraints without loading a huge PAK in RAM.
    if sig not in (b'PACK',b'PAK '): raise PakError(f'Unsupported signature: {sig!r}')
    if not (0<count<10_000_000): raise PakError(f'Invalid entry count: {count}')
    if index_offset<HEADER_SIZE or index_offset+count*ENTRY_SIZE>file_size: raise PakError('Index table outside file')
    h=dict(signature=sig.decode('ascii','replace'),count=count,index_offset=index_offset,data_field=data_field,crc32=crc)
    out=out_override if out_override is not None else pak.with_name(pak.stem+'_unpacked'); out.mkdir(parents=True,exist_ok=True)
    tasks=[]; methods={}
    with pak.open('rb') as f:
        f.seek(index_offset)
        table=f.read(count*ENTRY_SIZE)
    for i in range(count):
        pos=i*ENTRY_SIZE
        hid,offset,real=struct.unpack_from('<III',table,pos)
        packed=int.from_bytes(table[pos+12:pos+15],'little'); method=table[pos+15]
        methods[method]=methods.get(method,0)+1
        tasks.append((i,hid,offset,real,packed,method,str(out)))
    wc=worker_count(workers)
    if wc<=1 or count<24:
        _extract_worker_init(str(pak),index_offset)
        try:
            entries=[_extract_entry_worker(t) for t in tasks]
        finally:
            _close_worker_pak()
    else:
        with ProcessPoolExecutor(max_workers=wc,initializer=_extract_worker_init,initargs=(str(pak),index_offset)) as ex:
            # map preserves source index order, keeping manifests deterministic.
            entries=list(ex.map(_extract_entry_worker,tasks,chunksize=max(1,min(32,count//(wc*4) or 1))))
    ok=sum(1 for e in entries if e.get('status')=='ok'); fail=len(entries)-ok
    type_counts={}
    for e in entries:
        if e.get('status')=='ok' and e.get('ext'): type_counts[e['ext']]=type_counts.get(e['ext'],0)+1
        e.pop('ext',None)
    manifest={'source':str(pak),'archive_size':file_size,'header':h,'workers':wc,'note':'Original names are not stored in this PACK; index uses 32-bit path hashes. Compressed entries are expanded and verified against their indexed real length.','entries':entries}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=[f'Source: {pak}',f'Entries: {h["count"]}',f'Success: {ok}',f'Failed: {fail}',f'Workers: {wc}',f'Methods: {methods}',f'Types: {type_counts}',f'Output: {out}','','Note: manifest.json is an extraction manifest. It does not mean the archive itself contains JSON files.']
    (out/'_unpack_report.txt').write_text('\n'.join(lines),encoding='utf-8')
    return out,h['count'],ok,fail,methods,type_counts

def main(argv):
    if len(argv)<2:
        print('Usage: kingsoft_pak_unpack_v2.py file1.pak [file2.pak ...]')
        return 2
    rc=0
    for arg in argv[1:]:
        p=Path(arg.strip('"')).expanduser()
        print('\n'+'='*72); print('PAK:',p)
        try:
            out,count,ok,fail,methods,types=extract_one(p)
            print('Output :',out); print('Entries:',count,' Success:',ok,' Failed:',fail); print('Methods:',methods); print('Types  :',types)
            if fail: rc=1
        except Exception as e:
            rc=1; print('ERROR:',e); traceback.print_exc()
    return rc
if __name__=='__main__': raise SystemExit(main(sys.argv))
