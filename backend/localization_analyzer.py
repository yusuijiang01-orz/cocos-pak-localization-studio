#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, json, os, re, sys, unicodedata, hashlib
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from parallel_config import worker_count
from lua_localization import iter_lua_text_parts
from localization_tm import TOKEN_RE

TARGET_TEXT_EXTS={'.tsv','.ini','.txt','.lua'}
GENERATED_METADATA_NAMES={'_unpack_report.txt','_quality_report.txt'}
NATURAL_TEXT_RE=re.compile(r'[A-Za-z\u00c0-\u024f\u1e00-\u1eff\u3400-\u9fff]')
CODE_CALL_RE=re.compile(r'^\s*[A-Za-z_][A-Za-z0-9_.:]*\s*\(.*\)\s*;?\s*$',re.S)
CODE_ASSIGN_RE=re.compile(r'^\s*(?:local\s+)?[A-Za-z_][A-Za-z0-9_.]*\s*=')
STRUCTURAL_IDENTIFIER_RE=re.compile(r'^\s*\[?[A-Za-z_][A-Za-z0-9_.:\-]*\]?\s*$')

def is_localizable_text_path(path:Path)->bool:
    """Return False for tool-generated reports that are not game resources."""
    return path.is_file() and path.suffix.lower() in TARGET_TEXT_EXTS and path.name.lower() not in GENERATED_METADATA_NAMES


def has_editable_natural_text(text:str)->bool:
    """Reject records made entirely from runtime syntax or identifiers."""
    value=str(text or '').strip()
    if not value: return False
    if CODE_CALL_RE.fullmatch(value) or CODE_ASSIGN_RE.match(value): return False
    if STRUCTURAL_IDENTIFIER_RE.fullmatch(value): return False
    remainder=TOKEN_RE.sub(' ',value)
    remainder=re.sub(r'<[^<>\r\n]{1,512}>',' ',remainder)
    return bool(NATURAL_TEXT_RE.search(remainder))

FULLY_PROTECTED_TSV_FILES={'0553_38ebcb17.tsv','0999_64d8690e.tsv'}
# Ambiguous legacy schemas whose visible columns cannot safely be inferred from
# the header alone.  Keep these overrides narrow and backed by the data audit.
VISIBLE_TSV_FILE_COLUMNS={
    '0030_0363ae07.tsv': {'tenqua'},
    '0811_52ef96d7.tsv': {'ghi chó','ghi chú'},
    '0814_5325a29a.tsv': {'value'},
    '1021_677fba5b.tsv': {'note'},
    '2422_ec1243ff.tsv': {'property'},
}

def is_visible_tsv_header(header:str)->bool:
    value=str(header or '').strip()
    return bool(value and VISIBLE_TSV_HEADER_RE.search(value) and not INTERNAL_TSV_HEADER_RE.search(value))

def is_visible_tsv_column(header:str, filename:str='')->bool:
    """Classify one TSV column using both semantic header and known schema."""
    name=Path(str(filename or '')).name.lower()
    if name in FULLY_PROTECTED_TSV_FILES:
        return False
    value=str(header or '').strip().lower()
    if value in VISIBLE_TSV_FILE_COLUMNS.get(name,set()):
        return True
    return is_visible_tsv_header(header)

def is_visible_ini_key(key:str)->bool:
    raw=str(key or '').strip()
    value=re.sub(r'(?<=[a-z0-9])(?=[A-Z])','_',raw).lower()
    if not value or INTERNAL_INI_KEY_RE.search(value):
        return False
    # Keys such as durability_v / requirelevel / requireseries own visible
    # labels even though the key itself does not say text/title/name. Natural
    # text and resource-reference checks run afterwards, so accept every
    # non-structural INI key here and decide from its value.
    return True
TCVN_MAP={128:'À',129:'Ả',130:'Ã',131:'Á',132:'Ạ',133:'Ặ',134:'Ậ',135:'È',136:'Ẻ',137:'Ẽ',138:'É',139:'Ẹ',140:'Ệ',141:'Ì',142:'Ỉ',143:'Ĩ',144:'Í',145:'Ị',146:'Ò',147:'Ỏ',148:'Õ',149:'Ó',150:'Ọ',151:'Ộ',152:'Ờ',153:'Ở',154:'Ỡ',155:'Ớ',156:'Ợ',157:'Ù',158:'Ủ',159:'Ũ',161:'Ă',162:'Â',163:'Ê',164:'Ô',165:'Ơ',166:'Ư',167:'Đ',168:'ă',169:'â',170:'ê',171:'ô',172:'ơ',173:'ư',174:'đ',175:'Ằ',176:'̀',177:'̉',178:'̃',179:'́',180:'̣',181:'à',182:'ả',183:'ã',184:'á',185:'ạ',186:'Ẳ',187:'ằ',188:'ẳ',189:'ẵ',190:'ắ',191:'Ẵ',192:'Ắ',193:'Ầ',194:'Ẩ',195:'Ẫ',196:'Ấ',197:'Ề',198:'ặ',199:'ầ',200:'ẩ',201:'ẫ',202:'ấ',203:'ậ',204:'è',205:'Ể',206:'ẻ',207:'ẽ',208:'é',209:'ẹ',210:'ề',211:'ể',212:'ễ',213:'ế',214:'ệ',215:'ì',216:'ỉ',217:'Ễ',218:'Ế',219:'Ồ',220:'ĩ',221:'í',222:'ị',223:'ò',224:'Ổ',225:'ỏ',226:'õ',227:'ó',228:'ọ',229:'ồ',230:'ổ',231:'ỗ',232:'ố',233:'ộ',234:'ờ',235:'ở',236:'ỡ',237:'ớ',238:'ợ',239:'ù',240:'Ỗ',241:'ủ',242:'ũ',243:'ú',244:'ụ',245:'ừ',246:'ử',247:'ữ',248:'ứ',249:'ự',250:'ỳ',251:'ỷ',252:'ỹ',254:'ỵ',255:'Ố'}
VI_CHARS=set('ăâđêôơưĂÂĐÊÔƠƯàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊÒỎÕÓỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ')
VI_WORDS={'nhiệm','vụ','kỹ','năng','vật','phẩm','trang','bị','vũ','khí','tên','mô','tả','nhận','thưởng','cấp','pháp','bảo','người','chơi','điểm','sát','thương','tấn','công','phòng','thủ','thành','công','thất','bại','đóng','mở','xác','nhận','hủy','thông','báo','nhiệm vụ','kỹ năng','vật phẩm','trang bị','vũ khí',
          'giáp','kiếm','đao','kích','tiêu','trưởng','phủ','hồn','tộc','thần','linh','cổ','phong','thiên','hỏa','băng','lôi','thổ','mộc','kim','ngọc','tinh','cang','yêu','ma','tiên','cung','trào',
          'chiến','kết','một','xuống','thừa','khấp','tịch','bí','bảo','đầu','lục'}
# ``bảo`` already existed in the v1 vocabulary and must remain in compat mode.
DECODER_V2_WORDS={'chiến','kết','một','xuống','thừa','khấp','tịch','bí','đầu','lục'}
SHORT_VI_PHRASES={'đạo sĩ','dị nhân','giáp sĩ'}

def active_vi_words():
    return VI_WORDS - DECODER_V2_WORDS if os.environ.get('PAKLOC_DECODER_V1') == '1' else VI_WORDS
ZH_HINTS=['名称','名字','技能','任务','武器','装备','道具','物品','说明','描述','对话','奖励','按钮','界面','提示','标题','内容','文本','物品名','技能名','任务名']
KEY_HINTS=['name','title','text','caption','tip','desc','description','content','message','msg','talk','say','task','quest','mission','skill','item','weapon','equip','label']
VISIBLE_TSV_HEADER_RE=re.compile(r'(?:^|_)(?:npc_?name|name|title|text|string|intro|caption|tip|des|desc(?:ription)?|content|message|msg|label|dialog|talk|say|notice|help|story|mission|tutorial|memo|prefix|quest(?:_?name|desc)?|task(?:_?name|content|info|tips)?|skill(?:_?name|desc)|item_?name|weapon_?name|equip_?name|map_?name|drop_?name|reward(?:name|description)|right(?:name|desc)|title_?name|pack_?name|duty(?:name|desc))(?:$|_)|名称|名字|标题|说明文字|说明|描述|简介|内容|文本|对白|对话|提示|消息|任务名|技能名|物品名|装备名|地图名|备注',re.I)
INTERNAL_TSV_HEADER_RE=re.compile(r'(?:^|_)(?:id|res(?:ource)?|idx|index|code|path|file|image|icon|spr|anim(?:ation)?|script|function|param|value|type|kind|genre|detail|particular|level|map|width|height|price|count|weight|quantity|series|option|flag|time|lock|trade|drop|x|y|z|skill\d+)(?:$|_)|(?:资源|动画|文件|路径|索引|编号|种类|类别|类型|数值|最小值|最大值|属性|价格|等级|宽度|高度|负重|标记|时间|是否|装备id|套装id|resid)|技能\d+',re.I)
INTERNAL_INI_KEY_RE=re.compile(r'(?:^|_)(?:id|res(?:ource)?|idx|index|code|path|file|image|icon|spr|anim(?:ation)?|script|function|param|value|type|kind|level|map|width|height|x|y|z|color|font|sound|music|offset|frame|count|time|flag)(?:$|_)',re.I)
RESOURCE_EXTS=('spr','bmp','png','jpg','jpeg','gif','dds','tga','wav','mp3','ogg','mid','ani','cur','ico','ttf','fnt','ini','lua','txt','tsv','csv','xml','json','pak','dat','bin')

# Text that is embedded data/code, not a player-facing sentence.  This guard
# deliberately runs after decoding but before a record is admitted to the
# translation allowlist.  A natural-language fragment inside JSON or a game
# control expression must never make the whole payload translatable.
STRUCTURAL_TEXT_RE = re.compile(
    r"(?:^\s*\{.*\}\s*$)"
    r"|(?:\]\s*\*\*\s*[{}]|\*\*\s*[{}])"
    r"|(?:^\s*(?:px\)\s*)?\{.*\}\s*$)"
    r"|(?:\b(?:function|local|return|elseif|then|end)\b\s*[^\n]*[=(){};])",
    re.I | re.S,
)

# These are not player strings even when an old extractor classified them as
# text.  CSS is embedded in a few TXT/LUA resources and is executable layout
# data.  Game rich-text tags are handled later by the XLSX skeleton splitter:
# tags stay out of the worksheet while their visible inner text can be translated.
STYLESHEET_DIRECTIVE_RE = re.compile(r"^\s*@(?:media|keyframes|font-face|supports|import|charset)\b", re.I)
STYLESHEET_DECLARATION_RE = re.compile(
    r"^\s*(?:(?:align-content|align-items|align-self|background(?:-[\w-]+)?|border(?:-[\w-]+)?|"
    r"bottom|clear|color|column(?:-[\w-]+)?|display|float|font(?:-[\w-]+)?|height|left|"
    r"line-height|margin(?:-[\w-]+)?|max-(?:width|height)|min-(?:width|height)|opacity|overflow(?:-[\w-]+)?|"
    r"padding(?:-[\w-]+)?|position|right|text-align|text-decoration|text-shadow|top|transform|"
    r"vertical-align|visibility|white-space|width|z-index)\s*:\s*[^{};]+;?\s*)+$",
    re.I,
)
STYLESHEET_DECLARATION_PREFIX_RE = re.compile(
    r"^\s*(?:align-content|align-items|align-self|background(?:-[\w-]+)?|border(?:-[\w-]+)?|"
    r"bottom|clear|color|column(?:-[\w-]+)?|display|float|font(?:-[\w-]+)?|height|left|"
    r"line-height|margin(?:-[\w-]+)?|max-(?:width|height)|min-(?:width|height)|opacity|overflow(?:-[\w-]+)?|"
    r"padding(?:-[\w-]+)?|position|right|text-align|text-decoration|text-shadow|top|transform|"
    r"vertical-align|visibility|white-space|width|z-index)\s*:",
    re.I,
)
STYLESHEET_PROPERTY_NAME_RE = re.compile(
    r"^\s*(?:align-content|align-items|align-self|background(?:-[\w-]+)?|border(?:-[\w-]+)?|"
    r"bottom|clear|color|column(?:-[\w-]+)?|display|float|font(?:-[\w-]+)?|height|left|"
    r"line-height|margin(?:-[\w-]+)?|max-(?:width|height)|min-(?:width|height)|opacity|overflow(?:-[\w-]+)?|"
    r"padding(?:-[\w-]+)?|position|right|text-align|text-decoration|text-shadow|top|transform|"
    r"vertical-align|visibility|white-space|width|z-index)\s*$",
    re.I,
)
MOJIBAKE_PREFIX_RE = re.compile(r"^\s*(?:Ă|Ã|Â|Ä|Å|Æ){2,}")

def is_structural_translation_payload(text:str)->bool:
    s=(text or '').strip()
    if not s:
        return False
    if ('\ufffd' in s or MOJIBAKE_PREFIX_RE.match(s) or s.startswith('=')
            or s.startswith('#') and len(s) > 1 and is_structural_translation_payload(s[1:])
            or STYLESHEET_DIRECTIVE_RE.search(s)
            or STYLESHEET_DECLARATION_RE.fullmatch(s) or STYLESHEET_DECLARATION_PREFIX_RE.match(s)
            or STYLESHEET_PROPERTY_NAME_RE.fullmatch(s)
            or s.startswith('@=') and s.count('@=') >= 2):
        return True
    # JSON/object/array payloads, including compact one-line dialogue tables.
    if ((s.startswith('{') and s.endswith('}')) or (s.startswith('[') and s.endswith(']'))) and (':' in s or '"' in s or "'" in s):
        return True
    return bool(STRUCTURAL_TEXT_RE.search(s))
RESOURCE_KEYS={'image','spr','bkimage','smallbackimage','splitimage','pointimage','maleimage','femaleimage','imgfile_0','imgfile_1','imgfile_2','imgfile_3','imgfile_4','imgfile_5','captainflagimage_0','captainflagimage_1','captainflagimage_2','font','sound','music','texture','icon','filename','file','path'}
MOJIBAKE_HINTS={'脿','峄','岷','霉','铆','啤','农','锚','谩','瓢','芒','膩','獜','牰','楜','瑩','浥','出来'}
UTF8_LEGACY_MOJIBAKE_RE=re.compile(r'[ĂĐưƠƯÀ-ỹµÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ³»¼½¾¿]{4,}')


def decode_tcvn(data:bytes)->str:
    s=''.join(TCVN_MAP.get(b, chr(b)) for b in data)
    return unicodedata.normalize('NFC',s)

def encode_legacy_text(text:str)->bytes:
    """Encode edited game text with the legacy TSV encoding mix."""
    text=str(text or '').replace('\u00a0',' ')
    rev={v:k for k,v in TCVN_MAP.items()}
    combining={'\u0300':176,'\u0309':177,'\u0303':178,'\u0301':179,'\u0323':180}
    # TCVN3 stores Ư/ư as a base byte followed by the tone byte.  Python
    # decomposes Ứ/Ừ/Ử/Ữ/Ự (and lowercase variants) into U/u + U+031B + tone,
    # so handle that horn explicitly instead of rejecting otherwise valid UI
    # translations.
    horned_tones={
        'Ứ':(166,179),'Ừ':(166,176),'Ử':(166,177),'Ữ':(166,178),'Ự':(166,180),
        'ứ':(173,179),'ừ':(173,176),'ử':(173,177),'ữ':(173,178),'ự':(173,180),
    }
    out=bytearray()
    for ch in str(text or ''):
        o=ord(ch)
        if o < 128:
            out.append(o); continue
        if ch in horned_tones:
            out.extend(horned_tones[ch]); continue
        if ch in rev:
            out.append(rev[ch]); continue
        if ch in VI_CHARS:
            dec=unicodedata.normalize('NFD',ch)
            tmp=bytearray(); ok=True
            for dc in dec:
                if ord(dc)<128:
                    tmp.append(ord(dc))
                elif dc in rev:
                    tmp.append(rev[dc])
                elif dc in combining:
                    tmp.append(combining[dc])
                else:
                    ok=False; break
            if ok:
                out.extend(tmp); continue
        # Preserve genuinely unmapped legacy bytes only after trying the TCVN3 map.
        # Otherwise common Vietnamese letters such as à/á/ò are emitted as Latin-1
        # instead of their TCVN3 byte values.
        if 0x80 <= o <= 0xFF:
            out.append(o); continue
        try:
            out.extend(ch.encode('gbk')); continue
        except UnicodeEncodeError as e:
            raise ValueError(f'文本包含游戏编码无法表示的字符 U+{ord(ch):04X} {ch!r}: {str(text)[:120]!r}') from e
    return bytes(out)

def has_utf16_byte_structure(data:bytes)->bool:
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return True
    if len(data)<4:
        return False
    even=data[0::2]; odd=data[1::2]
    return max(even.count(0)/max(1,len(even)), odd.count(0)/max(1,len(odd))) >= 0.25

def contains_cjk(text:str)->bool:
    return any(
        '\u3400' <= ch <= '\u4dbf'
        or '\u4e00' <= ch <= '\u9fff'
        or '\uf900' <= ch <= '\ufaff'
        for ch in str(text or '')
    )

def is_valid_utf8(data:bytes)->bool:
    try:
        data.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False

def maybe_recover_legacy_bytes_from_utf8_mojibake(data:bytes)->bytes:
    """Undo legacy single-byte data that was accidentally decoded then saved as UTF-8."""
    try:
        text=data.decode('utf-8')
    except UnicodeDecodeError:
        return data
    sample=text[:8192]
    if len(UTF8_LEGACY_MOJIBAKE_RE.findall(sample)) < 3:
        return data
    try:
        recovered=text.encode('cp1258')
    except UnicodeEncodeError:
        return data
    return recovered

def normalize_text_resource_utf8(data:bytes, extension:str)->bytes:
    """Convert every textual field in one legacy resource to a single UTF-8 codec."""
    recovered=maybe_recover_legacy_bytes_from_utf8_mojibake(data)
    if recovered is data and is_valid_utf8(data):
        return data
    data=recovered
    if b'\r\n' in data:
        line_sep=b'\r\n'
    elif b'\n' in data:
        line_sep=b'\n'
    elif b'\r' in data:
        line_sep=b'\r'
    else:
        line_sep=b'\r\n'
    terminal=data.endswith((b'\r\n',b'\n',b'\r'))
    ext=str(extension or '').lower()
    output=[]
    cache={}
    def convert(part:bytes)->bytes:
        if not part or part.isascii():
            return part
        left_len=len(part)-len(part.lstrip(b' \t'))
        right_len=len(part)-len(part.rstrip(b' \t'))
        left=part[:left_len]
        end=len(part)-right_len if right_len else len(part)
        core=part[left_len:end]
        right=part[end:]
        if not core:
            return part
        cached=cache.get(core)
        if cached is None:
            text, _encoding, _language, _score=decode_best(core)
            cached=text.encode('utf-8')
            cache[core]=cached
        return left+cached+right
    for raw_line in data.splitlines():
        if ext=='.ini' and b'=' in raw_line:
            key,value=raw_line.split(b'=',1)
            output.append(convert(key)+b'='+convert(value))
            continue
        parts=raw_line.split(b'\t') if ext=='.tsv' or (ext=='.txt' and b'\t' in raw_line) else [raw_line]
        output.append(b'\t'.join(convert(part) for part in parts))
    rebuilt=line_sep.join(output)
    if terminal and output:
        rebuilt+=line_sep
    return rebuilt

def normalize_text_resource_gbk(data:bytes, extension:str)->bytes:
    """Convert every textual field in one resource to GBK bytes."""
    if b'\r\n' in data:
        line_sep=b'\r\n'
    elif b'\n' in data:
        line_sep=b'\n'
    elif b'\r' in data:
        line_sep=b'\r'
    else:
        line_sep=b'\r\n'
    terminal=data.endswith((b'\r\n',b'\n',b'\r'))
    ext=str(extension or '').lower()
    output=[]
    cache={}
    def convert(part:bytes)->bytes:
        if not part:
            return part
        left_len=len(part)-len(part.lstrip(b' \t'))
        right_len=len(part)-len(part.rstrip(b' \t'))
        left=part[:left_len]
        end=len(part)-right_len if right_len else len(part)
        core=part[left_len:end]
        right=part[end:]
        if not core:
            return part
        cached=cache.get(core)
        if cached is None:
            text, _encoding, _language, _score=decode_best(core)
            cached=text.replace('\u00a0',' ').encode('gbk','replace')
            cache[core]=cached
        return left+cached+right
    for raw_line in data.splitlines():
        if ext=='.ini' and b'=' in raw_line:
            key,value=raw_line.split(b'=',1)
            output.append(key+b'='+convert(value))
            continue
        parts=raw_line.split(b'\t') if ext=='.tsv' or (ext=='.txt' and b'\t' in raw_line) else [raw_line]
        output.append(b'\t'.join(convert(part) for part in parts))
    rebuilt=line_sep.join(output)
    if terminal and output:
        rebuilt+=line_sep
    return rebuilt

def encode_text_for_source(text:str, source_encoding:str='', original:bytes|None=None)->bytes:
    """Encode every edited text cell as UTF-8.

    The patched client accepts valid UTF-8 directly and only sends invalid byte
    strings through its legacy TCVN3 converter. Following the original cell's
    codec would therefore turn Chinese translations into runtime mojibake.
    ``source_encoding`` and ``original`` remain only for API compatibility.
    """
    normalized=str(text or '').replace('\u00a0',' ')
    return normalized.encode('utf-8')

def decode_utf16_loose(data:bytes, endian:str)->str:
    """Decode UTF-16 resource fragments split at byte-level line breaks.

    Some localized PAK text files are UTF-16LE/BE without a clean per-line BOM.
    Byte-level splitting can leave an odd trailing byte around CR/LF, so trim one
    dangling byte before decoding instead of letting the candidate disappear.
    """
    b=data.strip()
    if len(b)%2:
        b=b[:-1]
    if not b:
        return ''
    return unicodedata.normalize('NFC',b.decode(f'utf-16-{endian}'))

def looks_utf16(data:bytes, endian:str)->bool:
    if len(data)<4:
        return False
    if data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff'):
        return True
    even=data[0::2]; odd=data[1::2]
    high_ratio=sum(b>=128 for b in data)/max(1,len(data))
    if endian=='le':
        nul_ratio=odd.count(0)/max(1,len(odd))
    else:
        nul_ratio=even.count(0)/max(1,len(even))
    return nul_ratio>=0.25 or high_ratio>=0.35


def decode_mixed_legacy(data:bytes)->str:
    """Decode legacy lines that mix GBK Chinese and single-byte TCVN3 Vietnamese.
    This occurs in the target game's localized INI files, sometimes in the same value.
    """
    out=[]; i=0
    punct='，。！？：；“”‘’（）【】《》、·—…￥'
    while i < len(data):
        b=data[i]
        if b < 128:
            out.append(chr(b)); i += 1; continue
        nxt=data[i+1] if i+1 < len(data) else 0
        # Two high bytes that form a CJK character are overwhelmingly GBK/GB18030.
        if nxt >= 128:
            try:
                pair=data[i:i+2].decode('gb18030')
                if any('\u4e00' <= c <= '\u9fff' for c in pair) or pair in punct:
                    out.append(pair); i += 2; continue
            except Exception:
                pass
        prev=data[i-1] if i else 0
        prev_alpha=(65<=prev<=90 or 97<=prev<=122)
        next_alpha=(65<=nxt<=90 or 97<=nxt<=122)
        # TCVN3 diacritics are normally embedded in otherwise ASCII Vietnamese words.
        if b in TCVN_MAP and (prev_alpha or next_alpha):
            out.append(TCVN_MAP[b]); i += 1; continue
        if i+1 < len(data):
            try:
                pair=data[i:i+2].decode('gb18030')
                if any('\u4e00' <= c <= '\u9fff' for c in pair):
                    out.append(pair); i += 2; continue
            except Exception:
                pass
        out.append(TCVN_MAP.get(b, chr(b))); i += 1
    return unicodedata.normalize('NFC',''.join(out))

def is_resource_reference(text:str,key:str='')->bool:
    t=clean_text(text).strip('"\'')
    k=key.strip().lower()
    if k in RESOURCE_KEYS or k.startswith(('image','imgfile','sprfile','texture','soundfile')):
        return True
    low=t.lower()
    ext='|'.join(re.escape(x) for x in RESOURCE_EXTS)
    if re.fullmatch(rf'[A-Za-z]:?[\\/].*\.({ext})(?:[?#].*)?', t, re.I): return True
    if re.fullmatch(rf'[\\/].*\.({ext})(?:[?#].*)?', t, re.I): return True
    if ('\\' in t or '/' in t) and re.search(rf'\.({ext})$', low, re.I): return True
    if ('\\' in t or '/' in t) and re.fullmatch(r'[A-Za-z0-9_./\\:\-]+', t):
        return True
    if re.fullmatch(rf'[A-Za-z0-9_.\-]+\.(?:{ext})(?:[?#].*)?', t, re.I):
        return True
    return False

def is_resource_reference_bytes(data:bytes,key:bytes=b'')->bool:
    s=data.strip().strip(b'"\'')
    k=key.strip().lower()
    if k in {x.encode() for x in RESOURCE_KEYS} or k.startswith((b'image',b'imgfile',b'sprfile',b'texture',b'soundfile')):
        return True
    low=s.lower()
    if re.search(rb'\.(?:spr|bmp|png|jpg|jpeg|gif|dds|tga|wav|mp3|ogg|mid|ani|cur|ico|ttf|fnt|ini|lua|txt|tsv|csv|xml|json|pak|dat|bin)(?:[?#].*)?$',low):
        return True
    if (b'\\' in s or b'/' in s) and re.fullmatch(rb'[A-Za-z0-9_./\\:\-]+', s):
        return True
    return False

def clean_text(s:str)->str:
    return s.replace('\x00','').strip(' \t\r\n\ufeff')

def score_text(s:str):
    if not s: return (-999,'unknown')
    bad=sum(1 for c in s if ord(c)<32 and c not in '\t\r\n') + s.count('�')*4
    han=sum('\u4e00'<=c<='\u9fff' for c in s)
    vi=sum(c in VI_CHARS for c in s)
    low=' '+s.lower()+' '
    viwords=sum(1 for w in active_vi_words() if w in low)
    printable=sum(c.isprintable() or c in '\t\r\n' for c in s)
    ratio=printable/max(1,len(s))
    latin=sum(('a'<=c.lower()<='z') for c in s)
    spaces=s.count(' ')
    score=ratio*5 - bad*2 + han*2.2 + vi*0.18 + viwords*7
    normalized=' '.join(s.lower().split()).strip(' #$')
    vi_plausible = normalized in SHORT_VI_PHRASES or viwords>=1 or (vi>=2 and latin>=4 and spaces>=1)
    lang='mixed' if (han and vi_plausible) else ('vi' if vi_plausible and han==0 else ('zh' if han else 'other'))
    return score,lang

def decode_best(data:bytes):
    # UTF-8 must be authoritative when the complete cell is strictly valid.
    # Scoring it against GB18030/TCVN candidates is unsafe: arbitrary UTF-8
    # Chinese byte sequences are also frequently legal GB18030 and can decode
    # to a larger number of bogus Han glyphs, which previously won the score.
    if not has_utf16_byte_structure(data):
        try:
            utf8_text=clean_text(unicodedata.normalize('NFC',data.decode('utf-8')))
            utf8_score,utf8_lang=score_text(utf8_text)
            suspicious_utf8=any(unicodedata.category(ch) in {'Cn','Co','Cs'} for ch in utf8_text)
            suspicious_utf8 = suspicious_utf8 or (
                not contains_cjk(utf8_text)
                and not any(ch in VI_CHARS for ch in utf8_text)
                and bool(re.search(r'[\u0370-\u052f]', utf8_text))
            )
            if not suspicious_utf8:
                return utf8_text,'utf-8',utf8_lang,round(utf8_score,2)
        except UnicodeDecodeError:
            pass
    candidates=[]
    if 'utf8_text' in locals():
        candidates.append(('utf-8-suspicious',utf8_text))
    # High-byte density alone is not a UTF-16 signal: short GBK Chinese values
    # such as "微笑" consist almost entirely of high bytes. Treating those as
    # UTF-16 produced valid Unicode garbage (for example ΤЦ), which was then
    # irreversibly saved as UTF-8. Require a BOM or alternating NUL structure.
    utf16_structured=has_utf16_byte_structure(data)
    for endian in ('le','be'):
        if utf16_structured and looks_utf16(data,endian):
            try: candidates.append((f'utf-16-{endian}',decode_utf16_loose(data,endian)))
            except: pass
    try: candidates.append(('gb18030',data.decode('gb18030')))
    except: pass
    try: candidates.append(('windows-1258',unicodedata.normalize('NFC',data.decode('cp1258'))))
    except: pass
    candidates.append(('tcvn3',decode_tcvn(data)))
    if any(b>=128 for b in data): candidates.append(('mixed-gbk-tcvn3',decode_mixed_legacy(data)))
    best=None
    for enc,s in candidates:
        s=clean_text(s)
        sc,lang=score_text(s)
        han=sum('\u4e00'<=c<='\u9fff' for c in s)
        vi=sum(c in VI_CHARS for c in s)
        mojibake=sum(s.count(x) for x in MOJIBAKE_HINTS)
        if enc=='utf-8' and vi:
            sc += 35
        if enc.startswith('utf-16'):
            bad16=sum(1 for c in s if 0xD800<=ord(c)<=0xDFFF or 0xE000<=ord(c)<=0xF8FF)
            uncommon=sum(1 for c in s if 0x3400<=ord(c)<=0x4DBF or 0xA000<=ord(c)<=0xABFF)
            sc -= bad16*12 + uncommon*10 + mojibake*8
        if enc=='mixed-gbk-tcvn3':
            if han and vi and lang=='mixed': sc += 18
            sc -= mojibake*10
        if enc=='windows-1258' and os.environ.get('PAKLOC_DECODER_V1') != '1':
            # TCVN3 bytes are valid cp1258 surprisingly often, but produce
            # impossible mid-word capitals such as ChiƠn / mĐt / KƠt.  Penalize
            # that signature so the proper TCVN3 candidate can win.
            sc -= len(re.findall(r'[a-zà-ỹ][ĂÂĐÊÔƠƯ][a-zà-ỹ]', s))*14
        # Mojibake penalties. Mixed Latin + one/few Han glyphs is a common false GBK decode of TCVN3.
        sc -= sum(s.count(x) for x in ['Ê','µ','Ö','×','¼','¹','¶','Ã','Â']) * (0.35 if enc!='gb18030' else 0)
        if enc=='gb18030':
            han=sum('\u4e00'<=c<='\u9fff' for c in s); latin=sum(('a'<=c.lower()<='z') for c in s)
            if 0 < han <= 3 and latin >= 5 and latin > han*4: sc -= han*5
            # GBK can falsely consume TCVN3 diacritic bytes into CJK glyphs inside Latin words.
            embedded=len(re.findall(r'[A-Za-z][\u4e00-\u9fff]|[\u4e00-\u9fff][A-Za-z]',s))
            weird=sum(1 for c in s if 0xE000<=ord(c)<=0xF8FF or 0x2500<=ord(c)<=0x257F)
            sc -= embedded*9 + weird*5
        if best is None or sc>best[0]: best=(sc,enc,s,lang)
    return best[2],best[1],best[3],round(best[0],2)

def likely_translatable(s:str,lang:str)->bool:
    if len(s)<2 or len(s)>2000: return False
    if re.fullmatch(r'[\d\s.,:+\-*/%(){}\[\]_=<>\\/|]+',s): return False
    if re.fullmatch(r'[A-Za-z0-9_./\\:-]+',s) and lang=='other': return False
    low=' '+s.lower().strip('#$ ')+' '
    if any(ch in VI_CHARS for ch in s) and bool(re.search(r'[A-Za-zÀ-ỹ]', s)):
        return True
    vi_name_hint=sum(1 for w in active_vi_words() if w in low)>=1 and bool(re.search(r'[A-Za-zÀ-ỹ]',s))
    return lang in ('vi','zh','mixed') or vi_name_hint or any(k.lower() in s.lower() for k in ('task','skill','item','talk','message'))

def category_for(key:str,context:str,text:str,ext:str):
    z=' '.join([key,context,text]).lower()
    def has(*xs): return any(x.lower() in z for x in xs)
    if has('weapon','武器','binh khí','vũ khí'): return ('武器','weapon')
    if has('skill','技能','kỹ năng','magic'): return ('技能','skill')
    if has('task','quest','mission','任务','nhiệm vụ'): return ('任务','quest')
    if has('item','道具','物品','vật phẩm'): return ('道具','item')
    if has('equip','装备','trang bị','armor'): return ('装备','equipment')
    if has('talk(','say(','dialog','对话','npc'): return ('NPC对话','dialogue')
    if ext=='.ini' or has('button','caption','tooltip','界面','按钮','ui','window'): return ('UI文字','ui')
    if has('msg','message','notice','提示','通知','thông báo'): return ('系统提示','system')
    return ('其他文本','other')

def iter_units(path:Path):
    raw=path.read_bytes(); ext=path.suffix.lower()
    # Byte-level lines preserve mixed-encoding legacy files.
    lines=raw.splitlines()
    for lineno,line in enumerate(lines,1):
        if not line.strip(): continue
        if ext=='.ini' and line.lstrip().startswith((b';',b'#')): continue
        if ext=='.ini' and re.fullmatch(rb'\s*\[[^\]\r\n]+\]\s*',line):
            # Section names identify controls/config blocks; they are not
            # player-visible labels.  The visible text lives in Text/Title/etc.
            continue
        if ext in ('.tsv','.csv') or (ext=='.txt' and b'\t' in line):
            sep=b'\t' if ext=='.tsv' else b','
            if ext=='.txt': sep=b'\t'
            cells=line.split(sep)
            for col,cell in enumerate(cells,1):
                if cell.strip(): yield lineno,col,b'',cell,line[:500]
            continue
        # Lua: pull string literals first to avoid extracting code identifiers.
        if ext=='.lua':
            for part in iter_lua_text_parts(line):
                cell=part.content
                if cell.strip():
                    yield lineno,part.ordinal,('lua-'+part.kind).encode('ascii'),cell,line[:800]
            continue
        # INI key=value
        if b'=' in line and ext=='.ini':
            k,v=line.split(b'=',1)
            if v.strip(): yield lineno,1,k.strip(),v.strip(),line[:800]
            continue
        # Generic line
        yield lineno,1,b'',line.strip(),line[:800]

def is_candidate_bytes(b:bytes)->bool:
    s=b.strip()
    # Excel accepts at most 32,767 characters in a cell.  The former 4 KiB
    # scanner cap silently dropped legitimate long quest and story text.
    # Keep the analysis limit aligned with the export format instead.
    if len(s)<2 or len(s)>32767: return False
    # Fast-path: skip the huge volume of numeric/config cells before any codec work.
    if re.fullmatch(br'[0-9\s.,:+\-*/%(){}\[\]_=<>\\/|]+',s): return False
    if any(x>=128 for x in s): return True
    low=s.lower()
    return any(k.encode() in low for k in KEY_HINTS) or b' ' in s

def _analyze_one_file(args):
    path_s,pak_name=args
    p=Path(path_s); records=[]; stats=Counter()
    visible_tsv_columns=set()
    if p.suffix.lower()=='.tsv':
        first=p.read_bytes().splitlines()[:1]
        if first:
            for col,cell in enumerate(first[0].split(b'\t'),1):
                header=decode_best(cell)[0].strip()
                if is_visible_tsv_column(header,p.name):
                    visible_tsv_columns.add(col)
    hash_match=re.search(r'_([0-9A-Fa-f]{8})\.',p.name)
    h=hash_match.group(1).upper() if hash_match else ''
    last_ctx_raw=None; last_ctx_decoded=''
    for lineno,col,key_b,val_b,ctx_b in iter_units(p):
        if not is_candidate_bytes(val_b): continue
        if p.suffix.lower()=='.tsv' and (lineno==1 or col not in visible_tsv_columns): continue
        key,_,_,_=decode_best(key_b) if key_b and key_b.strip() else ('','','',0)
        if p.suffix.lower()=='.ini' and not is_visible_ini_key(key): continue
        if is_resource_reference_bytes(val_b,key_b):
            continue
        text,enc,lang,score=decode_best(val_b)
        if is_structural_translation_payload(text):
            stats['excluded_structural'] += 1
            continue
        if text.strip().lower() in {'abc','test','测试','null','none','n/a'}: continue
        if is_resource_reference(text,key): continue
        if not has_editable_natural_text(text): continue
        key_low=key.lower().strip()
        hinted_ini_value=(
            p.suffix.lower()=='.ini'
            and is_visible_ini_key(key_low)
            and any(ord(ch)>=128 for ch in text)
        )
        hinted_tsv_value=p.suffix.lower()=='.tsv' and lineno>1 and col in visible_tsv_columns and any(ord(ch)>=128 for ch in text)
        if not likely_translatable(text,lang) and not hinted_ini_value and not hinted_tsv_value: continue
        if ctx_b != last_ctx_raw:
            context,_,_,_=decode_best(ctx_b); last_ctx_raw=ctx_b; last_ctx_decoded=context
        else: context=last_ctx_decoded
        cat,sub=category_for(key,context,text,p.suffix.lower())
        uid=hashlib.sha1(f'{pak_name}|{p.name}|{lineno}|{col}|{text}'.encode('utf-8','replace')).hexdigest()[:16]
        rec={
            'id':uid,'category':cat,'subcategory':sub,'pak':pak_name,'hash':h,'source_file':p.name,
            'line':lineno,'column':col,'key':key,'encoding':enc,'language':lang,'confidence_score':score,
            'original':text,'translation':'','context':context[:1200],'status':'未翻译','note':'',
            # Records are emitted only after the structure-aware TSV/INI/TXT/LUA
            # filters above.  Persist that decision so the UI/export/import/build
            # chain consumes one canonical scope instead of re-guessing it.
            '_isPlayerVisible':True
        }
        records.append(rec); stats[cat]+=1; stats['lang_'+lang]+=1; stats['enc_'+enc]+=1
    return records,dict(stats)

def analyze_folder(folder:Path,pak_name:str,workers:int|None=None):
    files=[p for p in sorted(folder.rglob('*')) if is_localizable_text_path(p)]
    records=[]; stats=Counter(); wc=worker_count(workers)
    jobs=[(str(p),pak_name) for p in files]
    if wc<=1 or len(jobs)<16:
        results=map(_analyze_one_file,jobs)
    else:
        ex=ProcessPoolExecutor(max_workers=wc)
        results=ex.map(_analyze_one_file,jobs,chunksize=max(1,min(16,len(jobs)//(wc*4) or 1)))
    try:
        for recs,st in results:
            records.extend(recs); stats.update(st)
    finally:
        if 'ex' in locals(): ex.shutdown(wait=True,cancel_futures=False)
    return records,stats

def write_outputs(workspace:Path,records,stats):
    """Persist the internal localization database only.

    User-facing translation CSV files are exported on demand by Electron and contain
    exactly two columns: id,original.  Keeping the rich locator metadata internal
    avoids huge 40-50 MB exchange CSV files while preserving safe future write-back.
    """
    workspace.mkdir(parents=True,exist_ok=True)
    # Compact JSON: metadata remains available internally, but without pretty-print bloat.
    (workspace/'text_records.json').write_text(json.dumps(records,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    bypak={}
    bycat={}
    for r in records:
        bypak[r['pak']]=bypak.get(r['pak'],0)+1
        bycat[r['category']]=bycat.get(r['category'],0)+1
    report={'records':len(records),'stats':dict(stats),'paks':bypak,'categories':bycat,
            'target_extensions':sorted(TARGET_TEXT_EXTS),
            'exchange_csv_schema':['id','original'],
            'note':'TSV/INI/TXT/LUA text is shown and editable. Full PAK contents are still preserved during rebuild.'}
    (workspace/'analysis_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # Remove obsolete verbose CSV outputs from an existing workspace so users do not
    # accidentally keep working with the old oversized format.
    old=workspace/'localization_all.csv'
    if old.exists(): old.unlink()
    catdir=workspace/'categories'
    if catdir.exists():
        import shutil
        shutil.rmtree(catdir)
    return report

def main(argv):
    if len(argv)<4 or argv[1]!='analyze':
        print('Usage: localization_analyzer.py analyze <output_workspace> <unpacked_dir> [<pak_name>]')
        return 2
    out=Path(argv[2]); folder=Path(argv[3]); pak_name=argv[4] if len(argv)>4 else folder.name.replace('_unpacked','.pak')
    records,stats=analyze_folder(folder,pak_name)
    report=write_outputs(out,records,stats)
    print(json.dumps(report,ensure_ascii=False))
    return 0
if __name__=='__main__': raise SystemExit(main(sys.argv))
