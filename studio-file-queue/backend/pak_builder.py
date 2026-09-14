#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, os, re, shutil, struct, tempfile, hashlib
from pathlib import Path
from localization_tm import RESOURCE_PATH_PATTERN, validate_tokens, internal_text, exchange_text
from localization_analyzer import decode_best, encode_text_for_source, is_valid_utf8, normalize_text_resource_utf8
from tsv_localization import KV_RE, encode_cell, normalize_placeholder_markers, token_template
from pak_core import HEADER_SIZE, ENTRY_SIZE, PakError, extract_one, nrv2b
from lua_localization import iter_lua_strings, iter_lua_text_parts, replace_lua_text_parts, lua_code_skeleton, lua_structure_skeleton

class BitWriter:
    """NRV2B 8-bit interleaved bit writer.

    Control bits are stored MSB-first in control bytes, matching
    ucl_nrv2b_decompress_8 and the game's Method-1 streams.
    """
    def __init__(self):
        self.out=bytearray(); self.ctrl_pos=-1; self.mask=0
    def bit(self,v):
        if self.mask==0:
            self.ctrl_pos=len(self.out); self.out.append(0); self.mask=0x80
        if v: self.out[self.ctrl_pos]|=self.mask
        self.mask >>= 1
    def byte(self,b): self.out.append(b & 0xff)


def _nrv_prefix(w:BitWriter,n:int):
    """Encode the NRV2B variable prefix used for offsets/long lengths."""
    if n < 2:
        raise ValueError(f'NRV2B prefix value too small: {n}')
    bits=bin(n)[2:]
    # Decoder starts with 2 + one bit => binary 10/11.
    if len(bits)<2 or bits[0] != '1':
        raise ValueError(f'Invalid NRV2B prefix: {n}')
    w.bit(bits[1] == '1')
    for ch in bits[2:]:
        w.bit(0)
        w.bit(ch == '1')
    w.bit(1)


def _nrv_emit_match(w:BitWriter,off:int,length:int):
    """Emit a new-offset NRV2B match.

    We deliberately do not use the optional "reuse previous offset" form.  New-offset
    matches are fully compatible and keep the encoder simple/deterministic.
    """
    if off <= 0 or length < 2:
        raise ValueError(f'Invalid NRV2B match off={off}, length={length}')
    tmp=off-1
    high=tmp//256 + 3
    low=tmp & 0xff
    _nrv_prefix(w,high)
    w.byte(low)

    # Decoder copies (encoded_len + 1), and adds one first for long offsets.
    n=length - 1 - (1 if off > 0xD00 else 0)
    if n < 1:
        raise ValueError(f'NRV2B match too short for offset {off}: {length}')
    if n <= 3:
        w.bit((n >> 1) & 1)
        w.bit(n & 1)
    else:
        w.bit(0); w.bit(0)
        _nrv_prefix(w,n-2)


def _nrv_finish(w:BitWriter):
    # End marker: decoded offset word becomes 0xFFFFFFFF.
    w.bit(0)
    _nrv_prefix(w,0x1000002)
    w.byte(0xff)


def nrv2b_compress(data:bytes)->bytes:
    """Pure-Python NRV2B compressor for the game's Method-1 entries.

    This is a greedy LZ encoder using the exact 8-bit NRV2B stream syntax decoded by
    pak_core.nrv2b().  It is intentionally dependency-free for Windows users while
    providing compression close to the original UCL packer on these UI resources.

    Every produced stream is immediately decoded and byte-compared before it is returned;
    a bad stream can therefore never be written into a rebuilt PAK.
    """
    data=bytes(data)
    n=len(data)
    w=BitWriter()
    if n == 0:
        _nrv_finish(w)
        packed=bytes(w.out)
        if nrv2b(packed,0) != data:
            raise ValueError('NRV2B compressor self-check failed for empty stream')
        return packed

    # 3-byte hash -> recent positions.  Text/config resources benefit strongly from
    # repeated tags, keys and words.  Limiting candidates keeps build latency bounded.
    table={}
    max_candidates=64
    max_offset=0x20000
    max_match=0x7fff
    i=0

    def remember(pos:int):
        if pos+3 > n: return
        key=data[pos:pos+3]
        arr=table.setdefault(key,[])
        arr.append(pos)
        # Keep a modest rolling history; newest candidates are searched first.
        if len(arr)>128:
            del arr[:-64]

    while i<n:
        best_len=0; best_off=0
        if i+3<=n:
            arr=table.get(data[i:i+3])
            if arr:
                for p in reversed(arr[-max_candidates:]):
                    off=i-p
                    if off<=0 or off>max_offset:
                        continue
                    ml=3
                    limit=min(n-i,max_match)
                    while ml<limit and data[p+ml] == data[i+ml]:
                        ml+=1
                    if ml>best_len:
                        best_len=ml; best_off=off
                        if ml>=128:
                            break

        # Using matches shorter than three bytes saves little and can be counterproductive
        # once offset coding overhead is counted.  Three is valid for both short/long offs.
        if best_len>=3:
            w.bit(0)
            _nrv_emit_match(w,best_off,best_len)
            end=i+best_len
            for pos in range(i,min(end,n-2)):
                remember(pos)
            i=end
        else:
            w.bit(1)
            w.byte(data[i])
            remember(i)
            i+=1

    _nrv_finish(w)
    packed=bytes(w.out)
    # Mandatory compressor gate: exact round-trip before the archive is touched.
    decoded=nrv2b(packed,n)
    if decoded != data:
        raise ValueError('NRV2B compressor self-check failed: decoded bytes differ')
    return packed

def _split_line_ending(line:bytes):
    if line.endswith(b'\r\n'): return line[:-2],b'\r\n'
    if line.endswith(b'\n'): return line[:-1],b'\n'
    if line.endswith(b'\r'): return line[:-1],b'\r'
    return line,b''

def _encode_target(text:str)->bytes:
    """Encode a translated UI value using the game's mixed legacy text convention.

    Chinese text is emitted as GBK/GB18030-compatible bytes while Vietnamese glyphs are
    emitted as TCVN3 single-byte codes (or base-letter + combining accent when needed).
    ASCII/control markup is preserved byte-for-byte.  This mirrors the mixed decoder used
    by the analyzer and allows partially translated strings to remain buildable.
    """
    # Keep the legacy UI path in lockstep with the raw-cell encoder, including
    # TCVN3 horned vowels such as Ứ/ự.
    from localization_analyzer import encode_legacy_text
    return encode_legacy_text(text)

    import unicodedata
    from localization_analyzer import TCVN_MAP, VI_CHARS

    rev={v:k for k,v in TCVN_MAP.items()}
    # A few TCVN3 characters are most reliably represented as decomposed base + accent.
    combining={
        '\u0300':176, # grave
        '\u0309':177, # hook above
        '\u0303':178, # tilde
        '\u0301':179, # acute
        '\u0323':180, # dot below
    }
    out=bytearray()
    for ch in str(text or ''):
        o=ord(ch)
        if o < 128:
            out.append(o); continue
        if ch in rev:
            out.append(rev[ch]); continue
        # Try canonical decomposition for Vietnamese glyphs not directly present in the map.
        if ch in VI_CHARS:
            dec=unicodedata.normalize('NFD',ch)
            tmp=bytearray(); ok=True
            for dc in dec:
                if ord(dc)<128: tmp.append(ord(dc))
                elif dc in rev: tmp.append(rev[dc])
                elif dc in combining: tmp.append(combining[dc])
                else: ok=False; break
            if ok:
                out.extend(tmp); continue
        # Chinese and punctuation are encoded in the same legacy GBK family used by the client.
        try:
            out.extend(ch.encode('gbk')); continue
        except UnicodeEncodeError as e:
            raise ValueError(
                f'译文包含当前游戏混合编码无法表示的字符 U+{ord(ch):04X} {ch!r}；'
                f'请修改该字符后重试。文本片段：{str(text)[:120]!r}'
            ) from e
    return bytes(out)

def apply_ui_translations(extracted:Path,records,pak_name:str,work:Path):
    shutil.copytree(extracted,work,dirs_exist_ok=True)
    modified=[r for r in records if r.get('pak')==pak_name and exchange_text(r.get('original',''))!=exchange_text(r.get('source_original',r.get('original','')))]
    byfile={}
    risks=[]
    for r in modified:
        src=r.get('source_original',''); tgt=internal_text(r.get('original',''),src)
        ok,a,b=validate_tokens(src,tgt)
        if not ok:
            risks.append({'id':r.get('id'),'source_tokens':a,'target_tokens':b}); continue
        byfile.setdefault(r['source_file'],[]).append((r,tgt))
    if risks: raise ValueError('存在控制标记不一致，已拒绝构建：'+json.dumps(risks[:10],ensure_ascii=False))
    changed_files=[]
    for name,recs in byfile.items():
        p=work/name; ext=p.suffix.lower()
        if ext not in ('.ini','.txt'):
            raise ValueError(f'首轮回包仅允许 UI 的 INI/TXT；检测到已修改 {name}')
        lines=p.read_bytes().splitlines(keepends=True)
        line_map={}
        for r,tgt in recs: line_map.setdefault(int(r['line']),[]).append((r,tgt))
        for lineno,items in line_map.items():
            if lineno<1 or lineno>len(lines): raise ValueError(f'{name}: line {lineno} 越界')
            body,eol=_split_line_ending(lines[lineno-1])
            if ext=='.ini':
                if len(items)!=1 or b'=' not in body: raise ValueError(f'{name}: line {lineno} 不是可安全回写的单值 INI 行')
                r,tgt=items[0]; left,value=body.split(b'=',1)
                m=re.match(br'^(\s*)(.*?)(\s*)$',value,flags=re.S); pre,_,post=m.groups()
                newv=_encode_target(tgt)
                lines[lineno-1]=left+b'='+pre+newv+post+eol
            else:
                # TXT is only safe when the whole extracted unit owns the line.
                if len(items)!=1: raise ValueError(f'{name}: line {lineno} 有多个文本单元，暂不安全回写')
                r,tgt=items[0]
                lines[lineno-1]=_encode_target(tgt)+eol
        p.write_bytes(b''.join(lines)); changed_files.append(name)
    return changed_files

def _read_index(blob:bytes):
    if len(blob)<HEADER_SIZE or blob[:4] not in (b'PACK',b'PAK '): raise PakError('Unsupported PACK')
    count=struct.unpack_from('<I',blob,4)[0]; idx=struct.unpack_from('<I',blob,8)[0]
    entries=[]
    for i in range(count):
        pos=idx+i*ENTRY_SIZE
        hid,off,real=struct.unpack_from('<III',blob,pos); packed=int.from_bytes(blob[pos+12:pos+15],'little'); method=blob[pos+15]
        entries.append({'index':i,'hid':hid,'offset':off,'real':real,'packed':packed,'method':method})
    return count,idx,entries

def rebuild_pak(original_pak:Path,modified_dir:Path,changed_files:list[str],output:Path):
    blob=original_pak.read_bytes(); count,old_idx,entries=_read_index(blob)
    changed_indices={int(x.split('_',1)[0]) for x in changed_files}
    files_by_idx={}
    for name in changed_files:
        p=modified_dir/name
        if p.is_file() and re.match(r'^\d+_',p.name):
            files_by_idx[int(p.name.split('_',1)[0])]=p

    # Rebuild in the archive's physical offset order.  The old implementation
    # kept the entire original PAK, appended every changed stream, then appended
    # a second index.  That made each build permanently larger and left stale
    # archive structures in the output.  Repacking in index order is also unsafe
    # for this game because physical gaps contain non-indexed bytes and duplicate
    # index entries may alias the same payload.  This implementation preserves:
    #   * the original 32-byte header/prefix and every physical gap byte;
    #   * unchanged compressed payloads byte-for-byte;
    #   * duplicate/aliased payloads when their resulting bytes still match;
    # while writing changed payloads once and emitting exactly one final index.
    by_offset={}
    for e in entries:
        by_offset.setdefault(e['offset'],[]).append(e)
    physical_offsets=sorted(by_offset)
    if not physical_offsets:
        raise ValueError('PAK 没有可重建的资源条目')
    first_offset=physical_offsets[0]
    if first_offset<12 or first_offset>old_idx:
        raise ValueError(f'PAK 首资源偏移异常：{first_offset}')
    out=bytearray(blob[:first_offset])
    new_by_index={}
    old_cursor=first_offset
    unchanged_payloads=0
    alias_entries=0
    for old_offset in physical_offsets:
        group=sorted(by_offset[old_offset],key=lambda item:item['index'])
        group_old_end=max(old_offset+e['packed'] for e in group)
        if old_offset<old_cursor:
            # Exact aliases are supported; partially overlapping payloads are
            # ambiguous and must never be guessed around.
            if old_offset!=group[0]['offset'] or group_old_end>old_cursor:
                raise ValueError(f'PAK 存在无法安全重建的重叠资源：offset={old_offset}')
        elif old_offset>old_cursor:
            out.extend(blob[old_cursor:old_offset])

        variants={}
        for e in group:
            if e['index'] in changed_indices:
                p=files_by_idx.get(e['index'])
                if not p:
                    raise ValueError(f'Modified entry file missing: {e["index"]}')
                # The builder implements NRV2B method 1 only.  Re-labeling a
                # method-17 stream as method 1 can pass our own extractor yet
                # crash the game's native reader.  Refuse that transformation
                # until the original method has a verified encoder.
                if e['method'] != 1:
                    raise ValueError(
                        f'Entry {e["index"]} uses compression method {e["method"]}; '
                        'this Studio cannot safely rewrite it. Keep the original payload.'
                    )
                raw=p.read_bytes()
                packed_data=nrv2b_compress(raw)
                real=len(raw); method=1
            else:
                packed_data=blob[e['offset']:e['offset']+e['packed']]
                real=e['real']; method=e['method']
                unchanged_payloads+=1
            if len(packed_data)>0xFFFFFF:
                raise ValueError(f'Entry {e["index"]} packed size exceeds 24-bit field')
            variant_key=(packed_data,real,method)
            if variant_key in variants:
                off=variants[variant_key]
                alias_entries+=1
            else:
                off=len(out)
                out.extend(packed_data)
                variants[variant_key]=off
            new_by_index[e['index']] = (e['hid'],off,real,len(packed_data),method)
        old_cursor=max(old_cursor,group_old_end)
    if old_cursor<old_idx:
        out.extend(blob[old_cursor:old_idx])

    new_entries=[new_by_index[i] for i in range(count)]
    new_idx=len(out)
    for hid,off,real,packed,method in new_entries:
        out.extend(struct.pack('<III',hid,off,real)); out.extend(int(packed).to_bytes(3,'little')); out.append(method)
    struct.pack_into('<I',out,4,count); struct.pack_into('<I',out,8,new_idx)
    # Final structural gate.  A successful self-extraction is necessary but not
    # sufficient: verify the index itself, exact archive boundary, non-overlap,
    # and byte identity of every untouched compressed stream before publishing.
    rebuilt_blob=bytes(out)
    parsed_count,parsed_idx,parsed_entries=_read_index(rebuilt_blob)
    if parsed_count!=count or parsed_idx!=new_idx:
        raise ValueError('PAK 索引数量或偏移回读不一致')
    if len(rebuilt_blob)!=new_idx+count*ENTRY_SIZE:
        raise ValueError('PAK 末尾存在多余旧索引或未登记数据')
    unique_spans={}
    for old,new in zip(entries,parsed_entries):
        if old['hid']!=new['hid']:
            raise ValueError(f'Entry {old["index"]} 资源标识发生变化')
        start=new['offset']; end=start+new['packed']
        if start<first_offset or end>new_idx:
            raise ValueError(f'Entry {old["index"]} 数据偏移越界')
        span=(start,end)
        for known_start,known_end in unique_spans:
            if span==(known_start,known_end):
                continue
            if start<known_end and end>known_start:
                raise ValueError(f'Entry {old["index"]} 与其他资源数据重叠')
        unique_spans[span]=True
        if old['index'] not in changed_indices:
            old_payload=blob[old['offset']:old['offset']+old['packed']]
            new_payload=rebuilt_blob[start:end]
            if (
                new_payload!=old_payload
                or new['real']!=old['real']
                or new['packed']!=old['packed']
                or new['method']!=old['method']
            ):
                raise ValueError(f'Entry {old["index"]} 未修改资源未能逐字节保留')
    output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(out)
    return {
        'entries':count,
        'changed_entries':len(changed_indices),
        'unchanged_payloads':unchanged_payloads,
        'alias_entries':alias_entries,
        'archive_size':len(out),
        'original_archive_size':len(blob),
        'size_delta':len(out)-len(blob),
        'index_offset':new_idx,
        'rebuild_mode':'compact-physical-order-v1',
    }

def changed_files_between(original_dir:Path, modified_dir:Path):
    changed=[]
    for p in sorted(modified_dir.iterdir()):
        if (
            not p.is_file()
            or not re.match(r'^\d+_',p.name)
            or p.suffix.lower() not in ('.tsv','.csv','.ini','.txt','.bin','.lua','.spr','.jpg','.png','.dat')
        ):
            continue
        src=original_dir/p.name
        if not src.exists() or src.read_bytes()!=p.read_bytes():
            changed.append(p.name)
    return changed

UTF8_TEXT_EXTENSIONS={'.tsv','.csv','.ini','.txt','.lua'}

def _normalize_all_text_resources_utf8(original_dir:Path, modified_dir:Path)->list[str]:
    """Stage every legacy text resource as UTF-8, including untranslated files."""
    normalized=[]
    modified_dir.mkdir(parents=True,exist_ok=True)
    names={
        p.name for p in original_dir.iterdir()
        if p.is_file() and p.suffix.lower() in UTF8_TEXT_EXTENSIONS
    }
    names.update(
        p.name for p in modified_dir.iterdir()
        if p.is_file() and p.suffix.lower() in UTF8_TEXT_EXTENSIONS
    )
    for name in sorted(names):
        src=original_dir/name
        dst=modified_dir/name
        basis=dst if dst.is_file() else src
        if not basis.is_file():
            continue
        after=normalize_text_resource_utf8(basis.read_bytes(),basis.suffix.lower())
        if not is_valid_utf8(after):
            raise ValueError(f'{name} 无法完整转换为 UTF-8，已阻止构建')
        original_bytes=src.read_bytes() if src.is_file() else None
        if original_bytes is None or after!=original_bytes:
            dst.write_bytes(after)
            normalized.append(name)
        elif dst.is_file():
            dst.unlink()
    return normalized

def _validate_changed_text_resources_utf8(modified_dir:Path, changed_files:list[str], original_dir:Path|None=None)->None:
    failures=[]
    for name in changed_files:
        path=modified_dir/name
        if path.suffix.lower() not in UTF8_TEXT_EXTENSIONS or not path.is_file():
            continue
        try:
            text=path.read_bytes().decode('utf-8')
        except UnicodeDecodeError as exc:
            failures.append(f'{name}: byte {exc.start}')
            continue
        if '\ufffd' in text:
            source_count=0
            source=(original_dir/name) if original_dir else None
            if source is not None and source.is_file():
                source_text=normalize_text_resource_utf8(source.read_bytes(),source.suffix.lower()).decode('utf-8')
                source_count=source_text.count('\ufffd')
            if text.count('\ufffd')>source_count:
                failures.append(f'{name}: 新增 Unicode 替换字符 U+FFFD')
    if failures:
        raise ValueError('构建输入仍包含非 UTF-8 文本，已停止打包：'+' | '.join(failures[:30]))

def _dedupe_changed_files(changed_files:list[str])->list[str]:
    priority={'.tsv':70,'.ini':60,'.txt':50,'.lua':40,'.bin':30,'.dat':30,'.spr':20,'.png':20,'.jpg':20}
    best={}
    for pos,name in enumerate(changed_files):
        try:
            idx=int(name.split('_',1)[0])
        except Exception:
            continue
        p=Path(name)
        score=(priority.get(p.suffix.lower(),0),-pos,name)
        if idx not in best or score>best[idx][0]:
            best[idx]=(score,name)
    return [item[1] for _idx,item in sorted(best.items())]

def _repair_unresolved_placeholders_in_cell(
    original_cell:bytes,
    modified_cell:bytes,
    source_encoding_hint:str='',
) -> tuple[bytes,bool,str]:
    """Restore {P1}/{P2} style protected tokens from the untouched source cell.

    These placeholders are an internal CSV/XLSX translation aid.  They must never reach
    rebuilt resources because the game client does not understand them.
    """
    if not re.search(rb'\{P\d+[^{}\r\n]*\}', modified_cell):
        return modified_cell, False, ''
    source_text, source_encoding, _language, _score = decode_best(original_cell)
    target_text, target_encoding, _target_language, _target_score = decode_best(modified_cell)
    meta = token_template(source_text)
    tokens = {item['key']: item['value'] for item in meta.get('tokens', [])}
    target_text = normalize_placeholder_markers(target_text, tokens)
    unknown=[]
    def replace_present(match):
        key='P'+match.group(1)
        value=tokens.get(key)
        if value is None:
            unknown.append('{'+key+'}')
            return match.group(0)
        return value
    restored=re.sub(r'\{P(\d+)\}',replace_present,target_text)
    if unknown:
        return modified_cell,False,'unknown placeholders: '+' | '.join(unknown[:8])
    if re.search(r'\{P\d+[^{}\r\n]*\}',restored):
        return modified_cell,False,'unresolved damaged placeholder'
    encoding = source_encoding_hint or target_encoding or source_encoding
    try:
        return encode_cell(restored, modified_cell, encoding), True, ''
    except Exception as exc:
        return modified_cell, False, str(exc)

def _auto_repair_modified_resources(original_dir:Path, modified_dir:Path, candidate_files:list[str]|None=None):
    unresolved_re=re.compile(rb'\{P\d+[^{}\r\n]*\}')
    diamond_placeholder_re=re.compile('◈\\s*(?:P\\s*)?\\d+\\s*◈'.encode('utf-8'),re.I)
    code_block_re=re.compile(rb'<(style|script)\b[^>]*>.*?</\1>',re.I|re.S)
    angle_tag_re=re.compile(rb'(?<!<)<[^<>\r\n]{1,160}>(?!>)')
    control_angle_name_re=re.compile(rb'^/?(?:c|color|font|size|img|image|sprite|br|b|i|u|p|a|style|script)\b',re.I)
    path_exts=(b'.spr',b'.jpg',b'.png',b'.lua',b'.ini',b'.txt',b'.wav',b'.mp3',b'.dat',b'.bin')
    repaired=[]
    unresolved=[]

    def is_resource_path(cell:bytes)->bool:
        low=cell.lower()
        return (b'\\' in cell or b'/' in cell) and any(ext in low for ext in path_exts)

    def structural_angle_tags(cell:bytes)->list[bytes]:
        return angle_tag_re.findall(cell)

    def changed_placeholder_cells(before_items:list[bytes], after_items:list[bytes])->list[str]:
        out=[]
        for row,(old_line,new_line) in enumerate(zip(before_items,after_items),1):
            if old_line==new_line:
                continue
            old_cells=old_line.split(b'\t')
            new_cells=new_line.split(b'\t')
            if len(old_cells)==len(new_cells):
                for col,(old_cell,new_cell) in enumerate(zip(old_cells,new_cells),1):
                    if old_cell!=new_cell and diamond_placeholder_re.search(new_cell):
                        out.append(f'{row}:C{col}')
            elif diamond_placeholder_re.search(new_line):
                out.append(str(row))
            if len(out)>=20:
                break
        return out

    def whole_replacement_problems(blob:bytes, ext:str)->list[str]:
        problems=[]
        if unresolved_re.search(blob):
            problems.append('存在未还原 {Pn} 占位符')
        if diamond_placeholder_re.search(blob):
            problems.append('存在未还原 ◈N◈ 导出占位符')
        if ext=='.tsv':
            rows=[line for line in blob.splitlines() if line.strip()]
            if rows:
                tab_counts=[line.count(b'\t') for line in rows[:200]]
                expected=tab_counts[0]
                bad=[idx+1 for idx,count in enumerate(tab_counts) if count!=expected]
                if bad:
                    problems.append(f'TSV 列数不一致：行 {bad[:8]}')
        return problems

    candidates=candidate_files
    if candidates is None:
        candidates=[
            p.name for p in sorted(modified_dir.iterdir())
            if p.is_file() and re.match(r'^\d+_',p.name) and p.suffix.lower() in ('.tsv','.ini','.txt')
        ]
    for name in candidates:
        ext=Path(name).suffix.lower()
        if ext not in ('.tsv','.ini','.txt','.lua'):
            continue
        src=original_dir/name; dst=modified_dir/name
        if not src.exists() or not dst.exists():
            continue
        after=dst.read_bytes()
        before=src.read_bytes()
        before_lines=before.splitlines()
        after_lines=after.splitlines()
        if len(before_lines)!=len(after_lines):
            problems=whole_replacement_problems(after,ext)
            if problems:
                unresolved.append(f'{name}: 整文件替换校验失败：'+'；'.join(problems[:4]))
            else:
                repaired.append(f'{name}:整文件替换，跳过行级自动修复')
            continue
        sep,had_terminal_newline=_line_ending(after)
        changed=False
        def same_logical(old_value:bytes,new_value:bytes)->bool:
            return decode_best(old_value)[0] == decode_best(new_value)[0]
        def logical_tags(value:bytes)->list[str]:
            return re.findall(r'(?<!<)<[^<>\r\n]{1,160}>(?!>)',decode_best(value)[0])
        def resource_paths(value:bytes)->list[str]:
            return re.findall(RESOURCE_PATH_PATTERN,decode_best(value)[0],re.I)

        if ext=='.txt' and (b'<style' in before.lower() or b'<script' in before.lower()):
            old_blocks=list(code_block_re.finditer(before))
            new_blocks=list(code_block_re.finditer(after))
            if len(old_blocks)==len(new_blocks):
                rebuilt_after=after
                offset=0
                html_repaired=0
                for old_block,new_block in zip(old_blocks,new_blocks):
                    old_value=old_block.group(0)
                    start=new_block.start()+offset
                    end=new_block.end()+offset
                    if rebuilt_after[start:end]!=old_value:
                        rebuilt_after=rebuilt_after[:start]+old_value+rebuilt_after[end:]
                        offset+=len(old_value)-(end-start)
                        html_repaired+=1
                if html_repaired:
                    after=rebuilt_after
                    after_lines=after.splitlines()
                    sep,had_terminal_newline=_line_ending(after)
                    repaired.append(f'{name}:HTML style/script x{html_repaired}')
                    changed=True
            else:
                unresolved.append(f'{name}: HTML style/script 代码块数量不一致，无法自动修复')
        if (ext=='.tsv' and before_lines and after_lines and before_lines[0]!=after_lines[0]
                and not same_logical(before_lines[0],after_lines[0])):
            after_lines[0]=before_lines[0]
            repaired.append(f'{name}:1:TSV 表头')
            changed=True
        for row,(old_line,new_line) in enumerate(zip(before_lines,after_lines),1):
            if old_line!=new_line:
                old_cells=old_line.split(b'\t')
                new_cells=new_line.split(b'\t')
                if len(old_cells)==len(new_cells)==1 and diamond_placeholder_re.search(new_cells[0]):
                    after_lines[row-1]=old_line
                    new_line=old_line
                    repaired.append(f'{name}:{row}:残留占位符回退')
                    changed=True
                if (len(old_cells)==len(new_cells)==1 and old_cells[0]!=new_cells[0]
                        and is_resource_path(old_cells[0]) and resource_paths(old_cells[0])!=resource_paths(new_cells[0])):
                    after_lines[row-1]=old_line
                    new_line=old_line
                    repaired.append(f'{name}:{row}:资源路径回退')
                    changed=True
                if (len(old_cells)==len(new_cells)==1
                        and logical_tags(old_cells[0])!=logical_tags(new_cells[0])):
                    after_lines[row-1]=old_line
                    new_line=old_line
                    repaired.append(f'{name}:{row}:尖括号控制标记回退')
                    changed=True
                if len(old_cells)==len(new_cells) and len(new_cells)>1:
                    restored_paths=0
                    reverted_placeholders=0
                    reverted_tags=0
                    for col in range(len(new_cells)):
                        if (old_cells[col]!=new_cells[col] and is_resource_path(old_cells[col])
                                and resource_paths(old_cells[col])!=resource_paths(new_cells[col])):
                            new_cells[col]=old_cells[col]
                            restored_paths+=1
                        if old_cells[col]!=new_cells[col] and diamond_placeholder_re.search(new_cells[col]):
                            new_cells[col]=old_cells[col]
                            reverted_placeholders+=1
                        if (old_cells[col]!=new_cells[col]
                                and logical_tags(old_cells[col])!=logical_tags(new_cells[col])):
                            new_cells[col]=old_cells[col]
                            reverted_tags+=1
                    if restored_paths:
                        repaired.append(f'{name}:{row}:资源路径x{restored_paths}')
                        changed=True
                    if reverted_placeholders:
                        repaired.append(f'{name}:{row}:残留占位符回退x{reverted_placeholders}')
                        changed=True
                    if reverted_tags:
                        repaired.append(f'{name}:{row}:尖括号控制标记回退x{reverted_tags}')
                        changed=True
                    if restored_paths or reverted_placeholders or reverted_tags:
                        after_lines[row-1]=b'\t'.join(new_cells)
                        new_line=after_lines[row-1]
            if not unresolved_re.search(new_line):
                continue
            if ext in ('.ini','.txt'):
                old_cells=old_line.split(b'\t')
                new_cells=new_line.split(b'\t')
                if len(old_cells)!=len(new_cells):
                    unresolved.append(f'{name}:{row}: 列数不一致，无法自动修复')
                    continue
                if len(new_cells)==1:
                    old_match=KV_RE.match(old_line)
                    new_match=KV_RE.match(new_line)
                    if old_line!=new_line and diamond_placeholder_re.search(new_line):
                        after_lines[row-1]=old_line
                        repaired.append(f'{name}:{row}:残留占位符回退')
                        changed=True
                        continue
                    if old_match and new_match and old_match.group(1)==new_match.group(1):
                        repaired_cell, ok, err = _repair_unresolved_placeholders_in_cell(old_match.group(2), new_match.group(2))
                        if ok:
                            after_lines[row-1]=new_match.group(1)+repaired_cell
                            repaired.append(f'{name}:{row}')
                            changed=True
                        else:
                            unresolved.append(f'{name}:{row}: {err or "占位符还原失败"}')
                        continue
                for col in range(min(len(old_cells),len(new_cells))):
                    if not unresolved_re.search(new_cells[col]):
                        continue
                    repaired_cell, ok, err = _repair_unresolved_placeholders_in_cell(old_cells[col], new_cells[col])
                    if ok:
                        new_cells[col]=repaired_cell
                        repaired.append(f'{name}:{row}:C{col+1}')
                        changed=True
                    else:
                        unresolved.append(f'{name}:{row}:C{col+1}: {err or "占位符还原失败"}')
                after_lines[row-1]=b'\t'.join(new_cells)
                continue
            old_cells=old_line.split(b'\t')
            new_cells=new_line.split(b'\t')
            if len(old_cells)!=len(new_cells):
                unresolved.append(f'{name}:{row}: TSV 列数不一致，无法自动修复')
                continue
            for col in range(len(new_cells)):
                if old_cells[col]!=new_cells[col] and diamond_placeholder_re.search(new_cells[col]):
                    new_cells[col]=old_cells[col]
                    repaired.append(f'{name}:{row}:C{col+1}:残留占位符回退')
                    changed=True
                    continue
                if not unresolved_re.search(new_cells[col]):
                    continue
                repaired_cell, ok, err = _repair_unresolved_placeholders_in_cell(old_cells[col], new_cells[col])
                if ok:
                    new_cells[col]=repaired_cell
                    repaired.append(f'{name}:{row}:C{col+1}')
                    changed=True
                else:
                    unresolved.append(f'{name}:{row}:C{col+1}: {err or "占位符还原失败"}')
            after_lines[row-1]=b'\t'.join(new_cells)
        leftovers=changed_placeholder_cells(before_lines,after_lines)
        if leftovers:
            unresolved.append(f'{name}: 仍残留导出占位符 ◈N◈：{",".join(leftovers[:8])}')
        if changed:
            rebuilt=sep.join(after_lines)
            if had_terminal_newline and after_lines:
                rebuilt+=sep
            dst.write_bytes(rebuilt)
    return {'repaired_count':len(repaired),'repaired':repaired[:100],'unresolved_count':len(unresolved),'unresolved':unresolved[:100]}

def _validate_original_matches_manifest(original_pak:Path, original_dir:Path):
    manifest_path=original_dir/'manifest.json'
    if not manifest_path.exists():
        return
    try:
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        expected_size=int(manifest.get('archive_size') or 0)
        expected_header=manifest.get('header') or {}
        blob=original_pak.read_bytes()
        count,index_offset,_entries=_read_index(blob)
    except Exception as exc:
        raise ValueError(f'无法校验原始 PAK 与解包清单：{exc}') from exc
    mismatches=[]
    if expected_size and len(blob)!=expected_size:
        mismatches.append(f'文件大小 {len(blob)} != {expected_size}')
    if expected_header.get('count') is not None and count!=int(expected_header['count']):
        mismatches.append(f'条目数 {count} != {expected_header["count"]}')
    if expected_header.get('index_offset') is not None and index_offset!=int(expected_header['index_offset']):
        mismatches.append(f'索引偏移 {index_offset} != {expected_header["index_offset"]}')
    if mismatches:
        raise ValueError(
            '当前“原始 PAK”不是生成该解包目录的文件，禁止叠加重构：'
            + '；'.join(mismatches)
            + '。请重新导入未修改的原包并重新分析。'
        )

def _line_ending(raw:bytes)->tuple[bytes,bool]:
    if b'\r\n' in raw:
        sep=b'\r\n'
    elif b'\n' in raw:
        sep=b'\n'
    elif b'\r' in raw:
        sep=b'\r'
    else:
        sep=b'\r\n'
    return sep, raw.endswith((b'\r\n',b'\n',b'\r'))

def _validate_modified_resources(original_dir:Path, modified_dir:Path, changed_files:list[str]):
    issues=[]
    unresolved_re=re.compile(rb'\{P\d+[^{}\r\n]*\}')
    identifier_re=re.compile(r'^[A-Za-z_][A-Za-z0-9_./\\:-]*$')
    html_tag_re=re.compile(rb'</?[A-Za-z][^<>]*>')
    code_block_re=re.compile(rb'<(style|script)\b[^>]*>.*?</\1>',re.I|re.S)
    angle_tag_re=re.compile(rb'(?<!<)<[^<>\r\n]{1,160}>(?!>)')
    control_angle_name_re=re.compile(rb'^/?(?:c|color|font|size|img|image|sprite|br|b|i|u|p|a|style|script)\b',re.I)
    path_exts=(b'.spr',b'.jpg',b'.png',b'.lua',b'.ini',b'.txt',b'.wav',b'.mp3',b'.dat',b'.bin')

    def readable(cell:bytes)->str:
        try:
            text=cell.decode('utf-8')
            if any('\u3400' <= ch <= '\u9fff' for ch in text):
                return text
        except UnicodeDecodeError:
            pass
        return decode_best(cell)[0]

    def normalize_newlines(value:bytes)->bytes:
        return value.replace(b'\r\n',b'\n').replace(b'\r',b'\n')

    def is_resource_path(cell:bytes)->bool:
        low=cell.lower()
        return (b'\\' in cell or b'/' in cell) and any(ext in low for ext in path_exts)

    def structural_angle_tags(cell:bytes)->list[bytes]:
        return angle_tag_re.findall(cell)

    def logical_tags(cell:bytes)->list[str]:
        return re.findall(r'(?<!<)<[^<>\r\n]{1,160}>(?!>)',readable(cell))

    def resource_paths(cell:bytes)->list[str]:
        return re.findall(RESOURCE_PATH_PATTERN,readable(cell),re.I)

    def validate_whole_replacement(name:str, ext:str, blob:bytes):
        if unresolved_re.search(blob):
            issues.append(f'{name}: 整文件替换存在未还原 {{Pn}} 占位符')
        diamond_placeholder_re=re.compile('◈\\s*(?:P\\s*)?\\d+\\s*◈'.encode('utf-8'),re.I)
        if diamond_placeholder_re.search(blob):
            issues.append(f'{name}: 整文件替换存在未还原 ◈N◈ 导出占位符')
        if ext=='.tsv':
            rows=[line for line in blob.splitlines() if line.strip()]
            if rows:
                expected=rows[0].count(b'\t')
                bad=[idx+1 for idx,line in enumerate(rows[:300]) if line.count(b'\t')!=expected]
                if bad:
                    issues.append(f'{name}: 整文件替换 TSV 列数不一致（行 {bad[:8]}）')

    for name in changed_files:
        ext=Path(name).suffix.lower()
        if ext not in ('.tsv','.ini','.txt','.lua'):
            continue
        src=original_dir/name; dst=modified_dir/name
        before=src.read_bytes(); after=dst.read_bytes()
        before_lines=before.splitlines(); after_lines=after.splitlines()
        if len(before_lines)!=len(after_lines):
            validate_whole_replacement(name,ext,after)
            continue
        if ext=='.txt' and (b'<html' in before.lower() or b'<style' in before.lower() or b'<script' in before.lower()):
            before_tags=html_tag_re.findall(before)
            after_tags=html_tag_re.findall(after)
            if before_tags!=after_tags:
                issues.append(f'{name}: HTML 标签结构被改变')
            before_blocks=code_block_re.findall(before)
            after_blocks=code_block_re.findall(after)
            if len(before_blocks)!=len(after_blocks):
                issues.append(f'{name}: HTML style/script 代码块数量被改变')
            else:
                for idx,(old_block,new_block) in enumerate(zip(code_block_re.finditer(before),code_block_re.finditer(after)),1):
                    if normalize_newlines(old_block.group(0))!=normalize_newlines(new_block.group(0)):
                        issues.append(f'{name}: HTML style/script 代码块被改变（块 {idx}）')
        for row,(old,new) in enumerate(zip(before_lines,after_lines),1):
            if ext=='.lua':
                # Decode the complete line first.  Tiny isolated legacy tag
                # fragments are too short for reliable codec detection.
                old_utf8=decode_best(old)[0].encode('utf-8')
                new_utf8=decode_best(new)[0].encode('utf-8')
                old_code=lua_code_skeleton(old_utf8)
                new_code=lua_code_skeleton(new_utf8)
                old_structure=lua_structure_skeleton(old_utf8)
                new_structure=lua_structure_skeleton(new_utf8)
                if old_code!=new_code:
                    issues.append(f'{name}:{row}: Lua 字符串以外的代码被改变')
                elif old_structure!=new_structure:
                    issues.append(f'{name}:{row}: Lua 标签、坐标或非显示内容被改变')
                if len(list(iter_lua_strings(old)))!=len(list(iter_lua_strings(new))):
                    issues.append(f'{name}:{row}: Lua 字符串结构被改变')
                if unresolved_re.search(new):
                    issues.append(f'{name}:{row}: 存在未还原占位符')
                continue
            if ext in ('.tsv','.txt') and old.count(b'\t')!=new.count(b'\t'):
                issues.append(f'{name}:{row}: TSV/制表列数被改变')
            if ext=='.ini' and b'=' in old and not old.lstrip().startswith((b';',b'#')):
                old_key,old_value=old.split(b'=',1)
                if b'=' in new:
                    new_key,new_value=new.split(b'=',1)
                else:
                    new_key,new_value=None,new
                old_key_text=readable(old_key)
                new_key_text=readable(new_key) if new_key is not None else None
                if old_key_text!=new_key_text:
                    issues.append(f'{name}:{row}: INI 键名或等号被改变')
                old_cells=[old_value]
                new_cells=[new_value]
            else:
                old_cells=old.split(b'\t')
                new_cells=new.split(b'\t')
            for col,(old_cell,new_cell) in enumerate(zip(old_cells,new_cells),1):
                if old_cell==new_cell:
                    continue
                if is_resource_path(old_cell) and resource_paths(old_cell)!=resource_paths(new_cell):
                    issues.append(f'{name}:{row}:C{col}: 资源路径字节被改变')
                old_tags=structural_angle_tags(old_cell)
                new_tags=structural_angle_tags(new_cell)
                if old_tags!=new_tags and logical_tags(old_cell)!=logical_tags(new_cell):
                    issues.append(f'{name}:{row}:C{col}: 尖括号控制标记被改变')
            if ext=='.tsv' and row==1:
                for col,(old_cell,new_cell) in enumerate(zip(old_cells,new_cells),1):
                    if old_cell==new_cell:
                        continue
                    old_text=readable(old_cell).strip()
                    new_text=readable(new_cell).strip()
                    if identifier_re.match(old_text) and old_text!=new_text:
                        issues.append(f'{name}:{row}:C{col}: TSV 表头字段被改变（{old_text} -> {new_text}）')
            if unresolved_re.search(new):
                issues.append(f'{name}:{row}: 存在未还原占位符')
        if len(issues)>=30:
            break
    if issues:
        raise ValueError('文本资源结构校验失败，已阻止构建损坏的 PAK：'+' | '.join(issues[:30]))

def materialize_records_to_modified_dir(extracted:Path, records_path:Path, pak_name:str, output_dir:Path):
    records=json.loads(records_path.read_text(encoding='utf-8'))
    translated_statuses={'已翻译','已迁移','已审核'}
    modified=[
        r for r in records
        if r.get('pak')==pak_name
        and r.get('source_file')
        # Older record caches do not contain this derived field.  They were
        # already filtered by the analyzer, so absence must remain compatible;
        # only an explicit False excludes an internal field.
        and r.get('_isPlayerVisible',True) is True
        and (
            exchange_text(r.get('original','')) != exchange_text(r.get('source_original',r.get('original','')))
            or (
                str(r.get('status','')) in translated_statuses
                and str(r.get('language','')).lower()=='zh'
            )
        )
    ]
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    # Materialization must be sparse and byte-preserving.  The analyzer's
    # extracted tree can contain UTF-8 display normalizations; copying that
    # entire tree caused unrelated legacy files and resource paths to enter a
    # build.  Start each edited file from the raw decompressed archive bytes
    # and emit only files that actually own translated records.
    raw_candidate=extracted.parent.parent/'_raw_reference'/extracted.name
    source_root=raw_candidate if raw_candidate.is_dir() else extracted
    normalized_files=_normalize_all_text_resources_utf8(source_root,output_dir)
    byfile={}
    for r in modified:
        name=r.get('source_file')
        if not name:
            continue
        byfile.setdefault(name,[]).append(r)
    changed_files=list(normalized_files)
    skipped=[]
    fallbacks=[]
    for name,recs in byfile.items():
        source_path=source_root/name
        p=output_dir/name
        if not source_path.exists():
            skipped.append({'file':name,'reason':'source file missing','count':len(recs)})
            continue
        p.parent.mkdir(parents=True,exist_ok=True)
        if not p.is_file():
            p.write_bytes(normalize_text_resource_utf8(source_path.read_bytes(),source_path.suffix.lower()))
        ext=p.suffix.lower()
        if ext not in ('.tsv','.csv','.ini','.txt','.lua'):
            skipped.append({'file':name,'reason':f'unsupported extension {ext}','count':len(recs)})
            continue
        original_bytes=p.read_bytes()
        updates={}
        for r in recs:
            try:
                row=int(r.get('line')); col=int(r.get('column') or 1)
            except Exception:
                skipped.append({'file':name,'id':r.get('id'),'reason':'bad row/column'})
                continue
            if ext=='.tsv' and row==1:
                # TSV headers are schema fields, not translatable content.
                # They are intentionally ignored and must not block a build.
                continue
            if ext=='.ini':
                # INI line numbers can drift between language/version resources.
                # Locate keyed entries by the key itself; use the recorded line
                # only to disambiguate repeated keys.
                current_lines=original_bytes.splitlines()
                record_key=str(r.get('key') or '').strip()
                if record_key:
                    key_rows=[]
                    for current_row,current_line in enumerate(current_lines,1):
                        current_match=re.match(rb'^([^=\r\n]{1,120})\s*=',current_line)
                        if current_match and current_match.group(1).decode('utf-8','replace').strip()==record_key:
                            key_rows.append(current_row)
                    if not key_rows:
                        skipped.append({'file':name,'id':r.get('id'),'reason':f'INI key missing: {record_key!r}'})
                        continue
                    row=min(key_rows,key=lambda candidate:abs(candidate-row))
                else:
                    current_line=current_lines[row-1] if 0 < row <= len(current_lines) else b''
                    if re.match(rb'^([^=\r\n]{1,120})\s*=',current_line):
                        skipped.append({'file':name,'id':r.get('id'),'reason':'INI comment/key row mismatch'})
                        continue
            target=internal_text(str(r.get('original','')),str(r.get('source_original','')))
            source=str(r.get('source_original',''))
            # Google Sheets/Translate sometimes changes the resource's leading
            # structural '#' into a single diamond.  This is not a game token;
            # restore it deterministically for every text resource type.
            if source.startswith('#') and target.startswith('◈') and not target.startswith('◈1◈'):
                target='#'+target[1:]
            if '\ufffd' in target:
                fallbacks.append({'file':name,'id':r.get('id'),'reason':'译文包含 Unicode 替换字符 U+FFFD，已保留原始资源文本'})
                continue
            if re.search(r'\{P\d+[^{}\r\n]*\}',target):
                fallbacks.append({'file':name,'id':r.get('id'),'reason':'占位符未还原，已保留原始资源文本'})
                continue
            if any(target.count(ch)!=source.count(ch) for ch in ('\t','\r','\n')):
                fallbacks.append({'file':name,'id':r.get('id'),'reason':'制表符/换行会改变资源结构，已保留原始资源文本'})
                continue
            if ext in ('.ini','.txt'):
                structural=re.match(r'^[#$=]+',source)
                if structural and not target.startswith(structural.group(0)):
                    target=structural.group(0)+target
            ok,source_tokens,target_tokens=validate_tokens(source,target)
            if not ok:
                fallbacks.append({'file':name,'id':r.get('id'),'reason':'控制标记不一致，已保留原始资源文本','source_tokens':source_tokens,'target_tokens':target_tokens})
                continue
            updates[(row,col)]=(target,str(r.get('encoding','')),r.get('id'))
        working_bytes=original_bytes
        line_sep,had_terminal_newline=_line_ending(working_bytes)
        lines=working_bytes.splitlines()
        file_changed=False
        for row_no,raw in enumerate(lines,1):
            if ext=='.lua':
                replacements={}
                for col_no,literal in ((x.ordinal,x) for x in iter_lua_text_parts(raw)):
                    key=(row_no,col_no)
                    if key not in updates:
                        continue
                    target,source_encoding,record_id=updates[key]
                    try:
                        replacements[col_no]=encode_cell(target,literal.content,source_encoding)
                    except (UnicodeEncodeError,ValueError) as exc:
                        fallbacks.append({'file':name,'id':record_id,'reason':f'译文无法使用游戏编码表示，已保留原始资源文本: {exc}'})
                lines[row_no-1],applied=replace_lua_text_parts(raw,replacements)
                file_changed=file_changed or bool(applied)
                continue
            if ext=='.ini':
                key=(row_no,1)
                if key in updates:
                    target,source_encoding,record_id=updates[key]
                    match=re.match(rb'^([^=\r\n]{1,120}\s*=\s*)(.*)$',raw)
                    original=match.group(2) if match else raw
                    try:
                        encoded=encode_cell(target,original,source_encoding)
                    except (UnicodeEncodeError,ValueError) as exc:
                        fallbacks.append({'file':name,'id':record_id,'reason':f'译文无法使用游戏编码表示，已保留原始资源文本: {exc}'})
                        continue
                    left_len=len(original)-len(original.lstrip(b' \t'))
                    right_len=len(original)-len(original.rstrip(b' \t'))
                    leading=original[:left_len]
                    trailing=original[len(original)-right_len:] if right_len else b''
                    lines[row_no-1]=(match.group(1) if match else b'')+leading+encoded+trailing
                    file_changed=True
                continue
            if ext=='.txt':
                cells=raw.split(b'\t')
                if len(cells)>1:
                    for col_no in range(1,len(cells)+1):
                        key=(row_no,col_no)
                        if key not in updates:
                            continue
                        target,source_encoding,record_id=updates[key]
                        try:
                            cells[col_no-1]=encode_cell(target,cells[col_no-1],source_encoding)
                        except (UnicodeEncodeError,ValueError) as exc:
                            fallbacks.append({'file':name,'id':record_id,'reason':f'译文无法使用游戏编码表示，已保留原始资源文本: {exc}'})
                            continue
                        file_changed=True
                    lines[row_no-1]=b'\t'.join(cells)
                    continue
                key=(row_no,1)
                if key in updates:
                    target,source_encoding,record_id=updates[key]
                    match=KV_RE.match(raw)
                    original=match.group(2) if match else raw
                    try:
                        encoded=encode_cell(target,original,source_encoding)
                    except (UnicodeEncodeError,ValueError) as exc:
                        fallbacks.append({'file':name,'id':record_id,'reason':f'译文无法使用游戏编码表示，已保留原始资源文本: {exc}'})
                        continue
                    lines[row_no-1]=(match.group(1) if match else b'')+encoded
                    file_changed=True
                continue
            sep=b'\t' if ext=='.tsv' else b','
            cells=raw.split(sep)
            for col_no in range(1,len(cells)+1):
                key=(row_no,col_no)
                if key in updates:
                    target,source_encoding,record_id=updates[key]
                    try:
                        cells[col_no-1]=encode_text_for_source(target,source_encoding,cells[col_no-1])
                    except (UnicodeEncodeError,ValueError) as exc:
                        fallbacks.append({'file':name,'id':record_id,'reason':f'译文无法使用游戏编码表示，已保留原始资源文本: {exc}'})
                        continue
                    file_changed=True
            lines[row_no-1]=sep.join(cells)
        if file_changed:
            rebuilt=line_sep.join(lines)
            if had_terminal_newline and lines:
                rebuilt+=line_sep
            p.write_bytes(rebuilt)
            if name not in changed_files:
                changed_files.append(name)
    _validate_changed_text_resources_utf8(output_dir,changed_files,source_root)
    encoding_mode='all-text-resources-utf8-v1'
    report={'output_dir':str(output_dir),'pak':pak_name,'encoding':encoding_mode,'modified_records':len(modified),'changed_files':changed_files,'changed_file_count':len(changed_files),'normalized_text_files':len(normalized_files),'skipped_count':len(skipped),'skipped':skipped[:100],'skipped_ids':list(dict.fromkeys(str(item.get('id') or '') for item in skipped if item.get('id'))),'safe_fallback_count':len(fallbacks),'safe_fallbacks':fallbacks[:100],'safe_fallback_ids':list(dict.fromkeys(str(item.get('id') or '') for item in fallbacks if item.get('id'))),'no_safe_changes':not changed_files,'no_changes':not changed_files}
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/'_records_materialize_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def build_from_modified_dir(original_pak:Path, original_dir:Path, modified_dir:Path, output:Path, workers:int=1, verify:bool=True):
    _validate_original_matches_manifest(original_pak,original_dir)
    with tempfile.TemporaryDirectory(prefix='pakloc_build_work_') as work_td:
        build_modified=Path(work_td)/'modified'
        shutil.copytree(modified_dir,build_modified)
        normalized_files=_normalize_all_text_resources_utf8(original_dir,build_modified)
        changed=_dedupe_changed_files(changed_files_between(original_dir,build_modified))
        if not changed:
            raise ValueError('没有检测到可构建的修改文件，且原包文本已经全部是 UTF-8')
        repair_report=_auto_repair_modified_resources(original_dir,build_modified,changed)
        if int(repair_report.get('unresolved_count') or 0):
            problems=' | '.join(repair_report.get('unresolved',[])[:20])
            raise ValueError('文本资源自动修复失败，已阻止构建损坏的 PAK：'+problems)
        # Structural repair can copy an original header, tag or code block back
        # into a file.  Official sources may use a legacy codec, so normalize a
        # second time after repair before the strict UTF-8 gate.
        post_repair_normalized=_normalize_all_text_resources_utf8(original_dir,build_modified)
        normalized_files=list(dict.fromkeys([*normalized_files,*post_repair_normalized]))
        changed=_dedupe_changed_files(changed_files_between(original_dir,build_modified))
        if not changed:
            raise ValueError('没有检测到可构建的修改文件')
        _validate_changed_text_resources_utf8(build_modified,changed,original_dir)
        _validate_modified_resources(original_dir,build_modified,changed)
        # Build and verify in an isolated temporary path.  A failed verification
        # must never replace the last known-good PAK in the user's build folder.
        candidate=Path(work_td)/('candidate'+output.suffix)
        info=rebuild_pak(original_pak,build_modified,changed,candidate)
        if verify:
            verify_dir=Path(work_td)/'verify'
            out_dir,count,ok,fail,methods,types=extract_one(candidate,verify_dir,workers=workers)
            if fail or ok!=count or count!=int(info.get('entries') or 0):
                raise ValueError(f'PAK 完整回读失败：entries={info.get("entries")}, count={count}, ok={ok}, fail={fail}')
            mismatches=[]
            changed_indices={int(name.split('_',1)[0]) for name in changed}
            expected_by_idx={
                int(name.split('_',1)[0]):build_modified/name
                for name in changed
                if (build_modified/name).is_file()
            }
            actual_by_idx={int(p.name.split('_',1)[0]):p for p in verify_dir.iterdir() if p.is_file() and re.match(r'^\d+_',p.name)}
            for i,p in expected_by_idx.items():
                ap=actual_by_idx.get(i)
                if not ap or p.read_bytes()!=ap.read_bytes():
                    mismatches.append(i)
            if mismatches:
                raise ValueError(f'Round-trip 内容不一致：{mismatches[:20]}')
            report={**info,'output':str(output),'sha256':sha256(candidate),'changed_files':changed,'normalized_utf8_files':len(normalized_files),'encoding':'all-text-resources-utf8-v1','utf8_gate':'pass','auto_repair':repair_report,'roundtrip':'pass','verify_ok':ok,'verify_count':count,'verify_failed':fail}
        else:
            report={**info,'output':str(output),'sha256':sha256(candidate),'changed_files':changed,'normalized_utf8_files':len(normalized_files),'encoding':'all-text-resources-utf8-v1','utf8_gate':'pass','auto_repair':repair_report,'roundtrip':'skipped','verify_ok':None,'verify_count':None,'verify_failed':None}
        output.parent.mkdir(parents=True,exist_ok=True)
        # The workspace may live on D: while Windows' temporary directory is
        # on C:. os.replace cannot cross volumes, so copy the verified archive
        # to a same-directory staging file and atomically publish from there.
        stage_fd,stage_name=tempfile.mkstemp(prefix=f'.{output.name}.',suffix='.verified.tmp',dir=output.parent)
        os.close(stage_fd)
        stage=Path(stage_name)
        try:
            shutil.copy2(candidate,stage)
            os.replace(stage,output)
        finally:
            if stage.exists():
                stage.unlink()
    output.with_suffix(output.suffix+'.build.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def sha256(p:Path):
    h=hashlib.sha256();
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def build_from_workspace(workspace:Path,pak_name:str,output:Path|None=None):
    project=json.loads((workspace/'project.json').read_text(encoding='utf-8'))
    records_path=workspace/'localization'/'text_records.json'
    item=next((x for x in project.get('paks',[]) if x.get('pak')==pak_name),None)
    if not item: raise ValueError(f'PAK not found in project: {pak_name}')
    original=Path(item['path']); extracted=Path(item['extracted'])
    if output is None: output=workspace/'build'/Path(original).name
    modified=workspace/'modified'/Path(pak_name).stem
    materialize_report=materialize_records_to_modified_dir(extracted,records_path,pak_name,modified)
    if int(materialize_report.get('skipped_count') or 0):
        details=' | '.join(
            f"{item.get('file')}:{item.get('id') or ''} {item.get('reason')}"
            for item in materialize_report.get('skipped',[])[:20]
        )
        raise ValueError(
            f'{pak_name} 有 {materialize_report.get("skipped_count")} 条界面编辑未能安全写回，已停止构建：{details}'
        )
    return build_from_modified_dir(original,extracted,modified,output,workers=1,verify=True)
