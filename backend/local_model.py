#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, hashlib, importlib.metadata, importlib.util, json, os, re, shutil
from pathlib import Path
from collections import defaultdict
from localization_tm import (
    TOKEN_RE, exchange_text, internal_text, normalize_key, validate_tokens,
    init_db, lookup_no_touch, utcnow, queue_stats_db, _load_records, _save_records
)
from parallel_config import worker_count

MODEL_ID = 'facebook/nllb-200-distilled-600M'
SRC_LANG = 'vie_Latn'
TGT_LANG = 'zho_Hans'

RESIDUAL_REPLACEMENTS = [
    (r'\bnpc\b', 'NPC'),
    (r'(?i)Thiết Mã Băng Qua', '铁马冰河'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])(?:Chiến|ChiƠn)(?![A-Za-zÀ-ỹ])', '战'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])Liệt(?![A-Za-zÀ-ỹ])', '烈'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])Thánh(?![A-Za-zÀ-ỹ])', '圣'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])Hồng(?![A-Za-zÀ-ỹ])', '红'),
    (r'(?i)Nguyệt Cung Giai Nhân', '月宫佳人'),
    (r'(?i)Nguyệt Cung', '月宫'),
    (r'(?i)Phá Vạn Tiên Trận', '破万仙阵'),
    (r'(?i)Yêu cầu đẳng cấp', '要求等级'),
    (r'(?i)nhận nhiệm vụ tại', '在'),
    (r'(?i)nhiệm vụ', '任务'),
    (r'(?i)kỹ năng', '技能'),
    (r'(?i)chủ động hỗ trợ', '主动辅助'),
    (r'(?i)hỗ trợ', '辅助'),
    (r'(?i)công kích|tấn công', '攻击'),
    (r'(?i)phòng ngự', '防御'),
    (r'(?i)công lực', '功力'),
    (r'(?i)trang bị', '装备'),
    (r'(?i)tốc độ', '速度'),
    (r'(?i)đối phương', '对方'),
    (r'(?i)có thể xếp chồng', '可以叠加'),
    (r'(?i)Nhấn Shift \+ chuột trái để tách ra', '按 Shift + 鼠标左键拆分'),
    (r'(?i)Nguyên liệu hợp thành', '合成材料'),
    (r'(?i)Trang phục dành cho những người đang yêu', '情侣专用时装'),
    (r'(?i)Nam', '男'),
    (r'(?i)Nữ', '女'),
    (r'(?i)khóa', '绑定'),
    (r'(?i)Phủ để chỉ 1 loại búa, vậy \'Can\' là chỉ gì\?', "“Phủ”指一种斧头，那么“Can”指什么?"),
    (r'(?i)Nhóm máu', '血型'),
    (r'(?i)Trang phục', '时装'),
    (r'(?i)con trai', '之子'),
    (r'(?i)Lôi Thần', '雷神'),
    (r'(?i)được gọi là', '名为'),
    (r'(?i)Nội trong', '在'),
    (r'(?i)phút', '分钟'),
    (r'(?i)biến thành hình tượng', '变身为形象'),
    (r'(?i)Thực vật đặc thù', '特殊植物'),
    (r'(?i)Ngục Pháp Sơn', '狱法山'),
    (r'(?i)do', '由'),
    (r'(?i)chuyển hóa thành', '转化而成'),
    (r'(?i)biết tác dụng', '知道用途'),
    (r'(?i)Mỗi', '每'),
    (r'(?i)nhiều lần', '多次'),
    (r'(?i)Diệu Thủ Thần Y', '妙手神医'),
    (r'(?i)Kiến quốc', '建国'),
    (r'(?i)làm lạnh', '冷却'),
    (r'(?i)Thần Binh chế tạo từ', '神兵由'),
    (r'(?i)Thiên Giới', '天界'),
    (r'(?i)Tặng Cho', '赠予'),
    (r'(?i)Chúc Mừng Sinh Nhật', '生日快乐'),
    (r'(?i)Thần Thú', '神兽'),
    (r'(?i)của', '的'),
    (r'(?i)dành tặng', '赠送给'),
    (r'(?i)kỳ sỹ|Kỳ Sĩ', '骑士'),
    (r'(?i)sử dụng', '使用'),
    (r'(?i)để vào', '进入'),
    (r'(?i)Ghép', '合成'),
    (r'(?i)tại', '在'),
    (r'(?i)để nhận được', '获得'),
    (r'(?i)bản đồ', '地图'),
    (r'(?i)xuất hiện', '出现'),
    (r'(?i)ngẫu nhiên', '随机'),
    (r'(?i)phần thưởng', '奖励'),
    (r'(?i)hàng ngày', '每日'),
    (r'(?i)Luyện Ngục Thiểm Điện', '炼狱闪电'),
    (r'(?i)Tai người có thể nghe được tần số sóng âm ở phạm vi bao nhiêu Hz\?', '人耳能听到的声波频率范围是多少 Hz?'),
    (r'(?i)Trong chứa|Bên trong chứa', '内含'),
    (r'(?i)Thái Vân trang Mộng Nhiễu', '彩云梦绕装'),
    (r'(?i)Thái Vân trang', '彩云装'),
    (r'(?i)Đơn dược cấp', '丹药等级'),
    (r'(?i)Giảm phòng thủ Vật lý', '降低物理防御'),
    (r'(?i)yêu ma', '妖魔'),
    (r'(?i)duy trì', '持续'),
    (r'(?i)giây', '秒'),
    (r'(?i)giải trừ', '解除'),
    (r'(?i)hiệu quả', '效果'),
    (r'(?i)làm chậm', '减速'),
    (r'(?i)độc sát', '毒伤'),
    (r'(?i)Hỏa Lôi trang', '火雷装'),
    (r'(?i)Ô đặc biệt', '特殊格'),
    (r'(?i)nhận các', '获得'),
    (r'(?i)sau', '以下'),
    (r'(?i)Thủy Tinh', '水晶'),
    (r'(?i)Bảo Thạch', '宝石'),
    (r'(?i)Dung Tinh Lộ', '熔晶露'),
    (r'(?i)Túi Thuộc Tính', '属性袋'),
    (r'(?i)ngày', '天'),
    (r'(?i)Đồ phổ Phá Quân', '破军图谱'),
    (r'(?i)Nội Đơn Trung Cấp', '中级内丹'),
    (r'(?i)Thẻ', '卡'),
    (r'(?i)Trầm điện', '沉电'),
    (r'(?i)Thưởng', '奖励'),
    (r'(?i)Đồng Nhân', '铜人'),
    (r'(?i)Mảnh vải', '布片'),
    (r'(?i)Hỏa Linh Phù', '火灵符'),
    (r'(?i)Thủy Linh Phù', '水灵符'),
    (r'(?i)Phong Linh Phù', '风灵符'),
    (r'(?i)Huyễn Linh Phù', '幻灵符'),
    (r'(?i)Vạn Tiên Trận Hỏa', '万仙阵火'),
    (r'(?i)Vạn Tiên Trận Thủy', '万仙阵水'),
    (r'(?i)Vạn Tiên Trận Phong', '万仙阵风'),
    (r'(?i)Vạn Tiên Trận Thổ', '万仙阵土'),
    (r'(?i)Vạn Tiên Trận Huyễn', '万仙阵幻'),
    (r'(?i)Vạn Tiên Trận', '万仙阵'),
    (r'(?i)Xích Tùng Tử', '赤松子'),
    (r'(?i)Diêu Trì', '瑶池'),
    (r'(?i)Nhận', '领取'),
    (r'(?i)Thiên Hùng', '天雄'),
    (r'(?i)Triều Ca', '朝歌'),
    (r'(?i)Sa Mạc Chết', '死亡沙漠'),
    (r'(?i)Băng Xuyên Cực', '极冰川'),
    (r'(?i)Hạ gục Boss', '击败 Boss'),
    (r'(?i)phong phú và hấp dẫn', '丰厚诱人'),
    (r'(?i)hoặc', '或'),
    (r'(?i)Ngoài ra', '此外'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])khi(?![A-Za-zÀ-ỹ])', '当'),
    (r'(?i)Quy Vương', '龟王'),
    (r'(?i)Đao Ngư', '刀鱼'),
    (r'(?i)tên từ', '名字从'),
    (r'(?i)đến', '到'),
    (r'(?i)ký tự', '字符'),
    (r'(?i)Ngọc Bội Chất Lượng', '玉佩品质'),
    (r'(?i)Siêu Cấp', '超级'),
    (r'(?i)Đổi Tên', '改名'),
    (r'(?i)当ến', '使'),
    (r'(?i)khiến', '使'),
    (r'(?i)giảm lực', '降低'),
    (r'(?i)trong vòng', '持续'),
    (r'(?i)thi triền', '施展'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])và(?![A-Za-zÀ-ỹ])', '和'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])cấp(?![A-Za-zÀ-ỹ])', '等级'),
    (r'(?i)Đạo Sĩ', '道士'),
    (r'(?i)vòng cuồng phong', '狂风圈'),
    (r'(?i)bảo vệ chính mình', '保护自身'),
    (r'(?i)Lôi sát', '雷伤'),
    (r'(?i)Nguyên Hồn Chân Nhân', '元魂真人'),
    (r'(?i)biết công dụng', '知道用途'),
    (r'(?i)ấn Shift\+ chuột trái để tách', '按 Shift + 鼠标左键拆分'),
    (r'(?i)hạ gục', '击败'),
    (r'(?i)Đại Thạch Thần', '大石神'),
    (r'(?i)Thiết Kinh Thần', '铁金神'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])sẽ(?![A-Za-zÀ-ỹ])', '将'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])được(?![A-Za-zÀ-ỹ])', '获得'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])loại(?![A-Za-zÀ-ỹ])', '种'),
    (r'(?i)lệnh bài', '令牌'),
    (r'(?i)trong 04', '在 04'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])Thủy(?![A-Za-zÀ-ỹ])', '水'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])Hỏa(?![A-Za-zÀ-ỹ])', '火'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])Phong(?![A-Za-zÀ-ỹ])', '风'),
    (r'(?i)tương ứng', '对应'),
    (r'(?i)gia nhập Lãnh Địa', '加入领地'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])lượt(?![A-Za-zÀ-ỹ])', '次'),
    (r'(?i)từ lần 2 trở đi', '从第 2 次起'),
    (r'(?i)cần thêm', '需要额外'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])mua(?![A-Za-zÀ-ỹ])', '购买'),
    (r'(?i)Kỳ Trân Các', '奇珍阁'),
    (r'(?i)Tiền Đồng', '铜钱'),
    (r'(?i)tiền vạn', '万钱'),
    (r'(?i)xe lương', '粮车'),
    (r'(?i)hoàn trả lại', '返还'),
    (r'(?i)trả', '交还'),
    (r'(?i)thành công', '成功'),
    (r'(?i)linh hồn', '灵魂'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])thiên(?![A-Za-zÀ-ỹ])', '天'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])địa(?![A-Za-zÀ-ỹ])', '地'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])thủy(?![A-Za-zÀ-ỹ])', '水'),
    (r'(?i)(?<![A-Za-zÀ-ỹ])hỏa(?![A-Za-zÀ-ỹ])', '火'),
    (r'(?i)vật chứng', '物证'),
    (r'(?i)Đối Thoại', '对话'),
    (r'(?i)Giáng Sinh An Lành, May Mắn và Hạnh Phúc 2014', '2014 圣诞安康幸运幸福'),
    (r'(?i)Ngân Nguyen Bảo', '银元宝'),
    (r'(?i)các tiệm tạp hóa', '杂货店'),
    (r'(?i)dược điếm', '药店'),
]

def cleanup_residual_vietnamese(text: str) -> str:
    result = str(text or '')
    for pattern, repl in RESIDUAL_REPLACEMENTS:
        result = re.sub(pattern, repl, result)
    if '\n' not in result:
        result = re.sub(r'\s+', ' ', result).strip()
    return result

VI_MARKS = set('ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵÀÁẢÃẠẰẮẲẴẶẦẤẨẪẬÈÉẺẼẸỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌỒỐỔỖỘỜỚỞỠỢÙÚỦŨỤỪỨỬỮỰỲÝỶỸỴ')

def model_status(model_dir: Path):
    model_dir = Path(model_dir)
    required_any = ['model.safetensors', 'pytorch_model.bin']
    tokenizer_files = ['tokenizer_config.json', 'sentencepiece.bpe.model', 'tokenizer.json']
    has_weights = any((model_dir / x).exists() for x in required_any)
    has_cfg = (model_dir / 'config.json').exists()
    has_tok = any((model_dir / x).exists() for x in tokenizer_files)
    deps = {}
    for name in ('torch','transformers','sentencepiece'):
        try:
            if importlib.util.find_spec(name) is None:
                raise ModuleNotFoundError(name)
            deps[name] = importlib.metadata.version(name)
        except Exception:
            deps[name] = None
    installed = model_dir.is_dir() and has_weights and has_cfg and has_tok and all(deps.values())
    size = 0
    if model_dir.exists():
        for p in model_dir.rglob('*'):
            if p.is_file():
                try: size += p.stat().st_size
                except OSError: pass
    return {
        'installed': bool(installed), 'model_id': MODEL_ID, 'model_dir': str(model_dir),
        'model_bytes': size, 'dependencies': deps,
        'message': '本地模型可用' if installed else '本地模型未完整安装；请运行 INSTALL_LOCAL_MODEL.bat'
    }

def _is_token(s: str) -> bool:
    return bool(TOKEN_RE.fullmatch(s or ''))

def _has_cjk(s: str) -> bool:
    return bool(re.search(r'[\u3400-\u9fff]', s or ''))

def _is_translatable(s: str) -> bool:
    if not s or not re.search(r'[A-Za-zÀ-ỹ]', s): return False
    # Pure identifiers/paths should not be sent to MT.
    if re.fullmatch(r'[A-Za-z0-9_./\\:\-]+', s.strip()): return False
    return True

def split_protected(text: str):
    """Return [('text'|'token', value)] while keeping every protected token byte-for-byte."""
    s = exchange_text(text)
    out=[]; pos=0
    for m in TOKEN_RE.finditer(s):
        if m.start()>pos: out.append(('text', s[pos:m.start()]))
        out.append(('token', m.group(0)))
        pos=m.end()
    if pos<len(s): out.append(('text', s[pos:]))
    # Real line breaks are structural too. Never send them through MT.
    final=[]
    for typ,val in out:
        if typ!='text' or '\n' not in val:
            final.append((typ,val)); continue
        pieces=val.split('\n')
        for i,piece in enumerate(pieces):
            if piece: final.append(('text',piece))
            if i<len(pieces)-1: final.append(('token','\n'))
    return final

def _trim_parts(s: str):
    m1=re.match(r'^\s*',s); m2=re.search(r'\s*$',s)
    a=m1.group(0) if m1 else ''; b=m2.group(0) if m2 else ''
    core=s[len(a):len(s)-len(b) if b else None]
    return a,core,b

class NLLBEngine:
    def __init__(self, model_dir: Path):
        try:
            import torch
            # Transformers probes optional vision packages even for text-only models.
            # A mismatched user-installed torchvision can otherwise prevent NLLB from
            # loading with an unrelated torchvision::nms error.
            import transformers.utils.import_utils as transformers_imports
            transformers_imports._torchvision_available = False
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        except Exception as e:
            raise RuntimeError('本地模型依赖未安装。请先运行 INSTALL_LOCAL_MODEL.bat') from e
        self.torch=torch
        threads=worker_count()
        try:
            torch.set_num_threads(threads)
            torch.set_num_interop_threads(1)
        except Exception: pass
        self.device='cuda' if torch.cuda.is_available() else 'cpu'
        self.tokenizer=AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, src_lang=SRC_LANG)
        dtype=torch.float16 if self.device=='cuda' else torch.float32
        try:
            self.model=AutoModelForSeq2SeqLM.from_pretrained(str(model_dir), local_files_only=True, dtype=dtype)
        except Exception as e:
            detail=str(e)
            if 'M2M100ForConditionalGeneration' in detail or 'torchvision::nms' in detail:
                raise RuntimeError('本地文本模型加载失败：检测到 PyTorch 可选依赖冲突。程序已禁用 torchvision，请完全重启软件后重试。') from e
            raise
        self.model.to(self.device); self.model.eval()
        self.bos=self.tokenizer.convert_tokens_to_ids(TGT_LANG)
        if self.bos is None or self.bos == self.tokenizer.unk_token_id:
            raise RuntimeError(f'模型 tokenizer 不包含目标语言代码 {TGT_LANG}')

    def translate_batch(self, texts, batch_size=None):
        if not texts: return []
        bs=batch_size or (16 if self.device=='cuda' else 6)
        out=[]
        for i in range(0,len(texts),bs):
            chunk=texts[i:i+bs]
            enc=self.tokenizer(chunk,return_tensors='pt',padding=True,truncation=True,max_length=384)
            enc={k:v.to(self.device) for k,v in enc.items()}
            with self.torch.inference_mode():
                gen=self.model.generate(**enc, forced_bos_token_id=self.bos, max_new_tokens=384, num_beams=1, do_sample=False)
            out.extend(self.tokenizer.batch_decode(gen,skip_special_tokens=True))
        return out

def translate_preserving_tokens(engine: NLLBEngine, db, sources):
    """Translate many complete source strings while never sending control tokens to the model."""
    plans=[]; fragments=[]; fragment_index={}
    for source in sources:
        parts=split_protected(source); plan=[]
        for typ,val in parts:
            if typ=='token': plan.append(('token',val)); continue
            lead,core,trail=_trim_parts(val)
            if not core or not _is_translatable(core):
                plan.append(('raw',val)); continue
            # Exact TM/glossary fragments get priority and never reach the model.
            cached,_kind=lookup_no_touch(db,core)
            if cached is not None:
                plan.append(('translated',lead+cached+trail)); continue
            key=normalize_key(core)
            if key not in fragment_index:
                fragment_index[key]=len(fragments); fragments.append(core)
            plan.append(('fragment',fragment_index[key],lead,trail))
        plans.append(plan)
    translated=engine.translate_batch(fragments)
    results=[]
    for plan in plans:
        pieces=[]
        for item in plan:
            if item[0] in ('token','raw','translated'): pieces.append(item[1])
            else:
                _,idx,lead,trail=item; pieces.append(lead+translated[idx]+trail)
        results.append(''.join(pieces))
    return results

def _file_sha256(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def _write_json_atomic(path:Path, value)->None:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,path)

def model_translate_csv_tree(input_dir:Path, output_dir:Path, model_dir:Path, db_path:Path, batch_size=48, progress=None, script_path:Path|None=None):
    """Translate localization CSVs as complete sentences with one shared model instance."""
    status=model_status(model_dir)
    if not status['installed']: raise RuntimeError(status['message'])
    from script_translator import incomplete_translation, csv_text_from_tm_target, default_translate_script, load_translate_function, translate_cell
    from tsv_localization import restore_template, stable_cell_id
    input_dir=Path(input_dir); output_dir=Path(output_dir)
    script_path=Path(script_path) if script_path else default_translate_script()
    script_hash=_file_sha256(script_path) if script_path.exists() else ''
    files=sorted((p for p in input_dir.rglob('*.csv') if p.is_file() and not p.name.startswith('_')),key=lambda p:(p.stat().st_size,p.as_posix()))
    index_path=input_dir/'_tsv_localization_index.json'
    index=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    export_report_path=input_dir/'_tsv_localization_export_report.json'
    try: pak_name=json.loads(export_report_path.read_text(encoding='utf-8')).get('pak','') if export_report_path.exists() else ''
    except Exception: pak_name=''
    output_dir.mkdir(parents=True,exist_ok=True)
    checkpoint_path=output_dir/'_model_translation_checkpoint.json'
    try: checkpoint=json.loads(checkpoint_path.read_text(encoding='utf-8'))
    except Exception: checkpoint={}
    if checkpoint.get('version')!=4 or checkpoint.get('model_id')!=MODEL_ID or checkpoint.get('script_sha256')!=script_hash:
        checkpoint={'version':4,'model_id':MODEL_ID,'script_sha256':script_hash,'files':{}}
    pending_files=[]; reports=[]; skipped_files=0; total_rows=0; completed_rows=0
    def compat_update_ids(row_id:str, meta:dict):
        ids=[row_id]
        try:
            row=int(meta.get('row') or 0)
            col=int(meta.get('column') or 0)
            source=meta.get('source') or ''
            for alt_col in dict.fromkeys([col,0,1]):
                alt=stable_cell_id(pak_name, meta.get('source_file') or '', row, alt_col, source)
                if alt and alt not in ids:
                    ids.append(alt)
        except Exception:
            pass
        return ids
    def csv_ui_updates(csv_path:Path):
        updates=[]
        if not csv_path.exists(): return updates
        with csv_path.open('r',encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if not reader.fieldnames or 'id' not in reader.fieldnames or 'text' not in reader.fieldnames:
                return updates
            for row in reader:
                row_id=row.get('id','')
                meta=index.get(row_id,{})
                text=row.get('text','')
                source=meta.get('source') or ''
                export_text=meta.get('export_text') or source
                if not row_id or not text or text==source or text==export_text:
                    continue
                value,error=restore_template(text,meta)
                for update_id in compat_update_ids(row_id,meta):
                    updates.append({'id':update_id,'text':text if error else value})
        return updates
    if progress:
        progress({'phase':'model-csv','percent':0,'current_rows':0,'total_rows':0,'message':f'正在检查 CSV 和断点：0/{len(files)} 个文件'})
    for file_index,src in enumerate(files,1):
        rel=src.relative_to(input_dir); rel_key=rel.as_posix(); dst=output_dir/rel
        source_hash=_file_sha256(src); saved=checkpoint['files'].get(rel_key,{})
        if saved.get('source_sha256')==source_hash and dst.exists() and saved.get('output_sha256')==_file_sha256(dst):
            saved_report=saved.get('report',{}); row_count=int(saved_report.get('rows',0)); reports.append(saved_report); skipped_files+=1; completed_rows+=row_count
            if progress:
                updates=csv_ui_updates(dst)
                for offset in range(0,len(updates),250):
                    progress({'phase':'model-csv','percent':0,'current_rows':completed_rows,'total_rows':total_rows,'current':file_index,'total':len(files),'skipped':skipped_files,'file':rel.as_posix(),'updates':updates[offset:offset+250],'message':f'断点跳过 {rel.name}，正在同步已有译文到界面'})
        else:
            with src.open('r',encoding='utf-8-sig',newline='') as f:
                row_count=sum(1 for _ in csv.DictReader(f))
            pending_files.append((src,rel,dst,source_hash,row_count))
        total_rows+=row_count
        if progress and (file_index==len(files) or file_index%5==0):
            progress({'phase':'model-csv','percent':0,'current_rows':completed_rows,'total_rows':total_rows,'current':file_index,'total':len(files),'message':f'正在检查 CSV 和断点：{file_index}/{len(files)} 个文件，已统计 {total_rows:,} 条'})
    total_files=len(files); completed_files=skipped_files
    if progress:
        progress({'phase':'model-csv','percent':round(completed_rows*100/max(1,total_rows),2),'current_rows':completed_rows,'total_rows':total_rows,'current':completed_files,'total':total_files,'skipped':skipped_files,'message':f'已处理 {completed_rows:,} / {total_rows:,} 条；断点完成 {skipped_files}/{total_files} 个 CSV'})
    db=engine=translate_fn=None; cache={}; cache_kind={}
    try:
        if pending_files:
            if progress:
                progress({'phase':'model-csv','percent':round(completed_rows*100/max(1,total_rows),2),'current_rows':completed_rows,'total_rows':total_rows,'message':f'已统计 {total_rows:,} 条；正在打开翻译记忆库和词典'})
            db=init_db(db_path); translate_fn=load_translate_function(script_path)
        for src,rel,dst,source_hash,row_count in pending_files:
            dst.parent.mkdir(parents=True,exist_ok=True)
            with src.open('r',encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f); fields=reader.fieldnames or []; data=list(reader)
            if 'id' not in fields or 'text' not in fields:
                tmp=dst.with_name(dst.name+'.tmp'); shutil.copy2(src,tmp); os.replace(tmp,dst)
                file_report={'source':str(src),'output':str(dst),'rows':0,'changed':0,'tm_hits':0,'script_hits':0,'model_hits':0,'rejected':0,'learned_tm':0,'unique_model_inputs':0}
                reports.append(file_report); completed_files+=1; completed_rows+=row_count
                checkpoint['files'][rel.as_posix()]={'source_sha256':source_hash,'output_sha256':_file_sha256(dst),'report':file_report}
                _write_json_atomic(checkpoint_path,checkpoint)
                continue
            originals=[row.get('text','') for row in data]
            pending_map={}; file_tm=file_script=file_model=file_rejected=file_learned=0; immediate_updates=[]
            def ui_update(row):
                row_id=row.get('id','')
                meta=index.get(row_id,{})
                value,error=restore_template(row.get('text',''),meta)
                text=row.get('text','') if error else value
                ids=compat_update_ids(row_id,meta)
                return {'id':ids[0],'alt_ids':ids[1:],'text':text}
            for i,row in enumerate(data):
                source=row.get('text','')
                meta=index.get(row.get('id',''),{})
                source_for_tm=meta.get('source') or source
                hit,_kind=lookup_no_touch(db,source_for_tm)
                if hit is not None:
                    row['text']=csv_text_from_tm_target(hit,meta); file_tm+=1
                elif source in cache:
                    row['text']=cache[source]
                    if cache[source]==source: file_rejected+=1
                    elif cache_kind.get(source)=='script': file_script+=1
                    else: file_model+=1
                else:
                    candidate=cleanup_residual_vietnamese(translate_cell(source,translate_fn))
                    if candidate!=source and not incomplete_translation(candidate):
                        row['text']=candidate; cache[source]=candidate; cache_kind[source]='script'; file_script+=1
                    else:
                        pending_map.setdefault(source,[]).append(i)
                if row.get('text','')!=source:
                    immediate_updates.append(ui_update(row))
            pending=list(pending_map)
            pending_rows=sum(len(indices) for indices in pending_map.values())
            immediate_processed=len(data)-pending_rows
            if progress and immediate_updates:
                for offset in range(0,len(immediate_updates),250):
                    current_rows=completed_rows+immediate_processed
                    progress({'phase':'model-csv','percent':round(current_rows*100/max(1,total_rows),2),'current_rows':current_rows,'total_rows':total_rows,'current':completed_files,'total':total_files,'file':rel.as_posix(),'updates':immediate_updates[offset:offset+250],'message':f'已处理 {current_rows:,} / {total_rows:,} 条；正在更新 {rel.name}'})
            batch_total=max(1,(len(pending)+int(batch_size)-1)//int(batch_size))
            model_processed=0
            for start in range(0,len(pending),int(batch_size)):
                sources=pending[start:start+int(batch_size)]
                if engine is None:
                    if progress:
                        current_rows=completed_rows+immediate_processed
                        progress({'phase':'model-csv','percent':round(current_rows*100/max(1,total_rows),2),'current_rows':current_rows,'total_rows':total_rows,'current':completed_files,'total':total_files,'file':rel.as_posix(),'message':f'已处理 {current_rows:,} / {total_rows:,} 条；正在加载 CUDA 模型'})
                    engine=NLLBEngine(model_dir)
                    if progress:
                        current_rows=completed_rows+immediate_processed
                        device=getattr(engine,'device','unknown')
                        progress({'phase':'model-csv','percent':round(current_rows*100/max(1,total_rows),2),'current_rows':current_rows,'total_rows':total_rows,'current':completed_files,'total':total_files,'skipped':skipped_files,'file':rel.as_posix(),'device':device,'message':f'已处理 {current_rows:,} / {total_rows:,} 条；模型已加载到 {device.upper()}'})
                batch_current=min(batch_total,start//int(batch_size)+1)
                if progress:
                    current_rows=completed_rows+immediate_processed+model_processed
                    upcoming_ids=[]
                    for source in sources:
                        upcoming_ids.extend(data[index_in_file].get('id','') for index_in_file in pending_map[source])
                        if len(upcoming_ids)>=250: break
                    progress({'phase':'model-csv','percent':round(current_rows*100/max(1,total_rows),2),'current_rows':current_rows,'total_rows':total_rows,'current':completed_files,'total':total_files,'file':rel.as_posix(),'batch_current':batch_current,'batch_total':batch_total,'upcoming_ids':upcoming_ids[:250],'message':f'已处理 {current_rows:,} / {total_rows:,} 条；正在计算 {rel.name} 批次 {batch_current}/{batch_total}'})
                targets=translate_preserving_tokens(engine,db,sources)
                live_updates=[]; batch_rows=0
                for source,target in zip(sources,targets):
                    batch_rows+=len(pending_map[source])
                    target=cleanup_residual_vietnamese(target)
                    if incomplete_translation(target):
                        fallback=cleanup_residual_vietnamese(source)
                        if fallback!=source and not incomplete_translation(fallback):
                            target=fallback; file_script+=len(pending_map[source])
                        else:
                            target=source; file_rejected+=len(pending_map[source])
                    else:
                        file_model+=len(pending_map[source])
                    cache[source]=target; cache_kind[source]='model'
                    for index_in_file in pending_map[source]:
                        data[index_in_file]['text']=target
                        live_updates.append(ui_update(data[index_in_file]))
                db.commit()
                if progress:
                    model_processed+=batch_rows; current_rows=completed_rows+immediate_processed+model_processed
                    percent=current_rows*100/max(1,total_rows)
                    update_chunks=[live_updates[offset:offset+250] for offset in range(0,len(live_updates),250)] or [[]]
                    for update_chunk in update_chunks:
                        progress({'phase':'model-csv','percent':round(percent,2),'current_rows':current_rows,'total_rows':total_rows,'current':completed_files,'total':total_files,'skipped':skipped_files,'file':rel.as_posix(),'batch_current':batch_current,'batch_total':batch_total,'updates':update_chunk,'message':f'已处理 {current_rows:,} / {total_rows:,} 条；{rel.name} 批次 {batch_current}/{batch_total}，文件 {completed_files+1}/{total_files}'})
            file_changed=sum(1 for before,row in zip(originals,data) if row.get('text','')!=before)
            tmp=dst.with_name(dst.name+'.tmp')
            with tmp.open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n'); writer.writeheader(); writer.writerows(data)
            os.replace(tmp,dst)
            db.commit()
            file_report={'source':str(src),'output':str(dst),'rows':len(data),'changed':file_changed,'tm_hits':file_tm,'script_hits':file_script,'model_hits':file_model,'rejected':file_rejected,'learned_tm':file_learned,'unique_model_inputs':len(pending)}
            reports.append(file_report); completed_files+=1; completed_rows+=len(data)
            checkpoint['files'][rel.as_posix()]={'source_sha256':source_hash,'output_sha256':_file_sha256(dst),'report':file_report}
            _write_json_atomic(checkpoint_path,checkpoint)
            if progress:
                progress({'phase':'model-csv','percent':round(completed_rows*100/max(1,total_rows),2),'current_rows':completed_rows,'total_rows':total_rows,'current':completed_files,'total':total_files,'skipped':skipped_files,'file':rel.as_posix(),'message':f'已处理 {completed_rows:,} / {total_rows:,} 条；完成文件 {completed_files}/{total_files}'})
        for name in ('_tsv_localization_index.json','_tsv_localization_export_report.json'):
            src=input_dir/name
            if src.exists(): output_dir.mkdir(parents=True,exist_ok=True); shutil.copy2(src,output_dir/name)
        rows=sum(r.get('rows',0) for r in reports); changed=sum(r.get('changed',0) for r in reports)
        tm_hits=sum(r.get('tm_hits',0) for r in reports); script_hits=sum(r.get('script_hits',0) for r in reports); model_hits=sum(r.get('model_hits',0) for r in reports); rejected=sum(r.get('rejected',0) for r in reports); learned_tm=sum(r.get('learned_tm',0) for r in reports)
        report={'input_dir':str(input_dir),'output_dir':str(output_dir),'rows':rows,'changed':changed,'tm_hits':tm_hits,'script_hits':script_hits,'model_hits':model_hits,'rejected':rejected,'learned_tm':learned_tm,'unique_model_inputs':sum(r.get('unique_model_inputs',0) for r in reports),'translated_files':len(pending_files),'skipped_files':skipped_files,'csv_files':total_files,'files':reports}
        _write_json_atomic(output_dir/'_model_translation_report.json',report)
        return report
    finally:
        if db is not None: db.close()

def local_translate_queue_batch(records_path:Path,pak_name:str,db_path:Path,model_dir:Path,batch_size=24):
    status=model_status(model_dir)
    if not status['installed']: raise RuntimeError(status['message'])
    records=_load_records(records_path); db=init_db(db_path)
    rows=db.execute('''SELECT source_key,source_text,occurrence_count FROM translation_queue
        WHERE pak_name=? AND status='pending' ORDER BY occurrence_count DESC LIMIT ?''',(pak_name,int(batch_size))).fetchall()
    if not rows:
        result={'processed_unique':0,'applied_records':0,'failed':0,'review':0,'stats':queue_stats_db(db,pak_name)}
        db.close(); return result
    engine=NLLBEngine(model_dir)
    sources=[r['source_text'] for r in rows]
    targets=translate_preserving_tokens(engine,db,sources)
    by_key=defaultdict(list)
    for r in records:
        if r.get('pak')==pak_name:
            src=r.get('source_original',r.get('original','')); by_key[normalize_key(src)].append(r)
    applied=processed=failed=review=0
    for row,tgt in zip(rows,targets):
        key=row['source_key']; src=row['source_text']
        try:
            ok,a,b=validate_tokens(src,tgt)
            if not ok:
                db.execute('UPDATE translation_queue SET status="review",target_text=?,engine="nllb",attempts=attempts+1,error=?,updated_at=? WHERE pak_name=? AND source_key=?',
                    (tgt,f'控制标记不一致 source={a} target={b}',utcnow(),pak_name,key)); review+=1; continue
            if not normalize_key(tgt) or normalize_key(tgt)==normalize_key(src):
                db.execute('UPDATE translation_queue SET status="failed",target_text=?,engine="nllb",attempts=attempts+1,error=?,updated_at=? WHERE pak_name=? AND source_key=?',
                    (tgt,'模型未产生有效中文变化',utcnow(),pak_name,key)); failed+=1; continue
            for rec in by_key.get(key,[]):
                source_internal=rec.get('source_original',rec.get('original',''))
                if rec.get('original','')==source_internal:
                    rec['original']=internal_text(tgt,source_internal); rec['status']='本地模型'; rec['note']='local:nllb'; applied+=1
            db.execute('UPDATE translation_queue SET status="completed",target_text=?,engine="nllb",attempts=attempts+1,error="",updated_at=? WHERE pak_name=? AND source_key=?',
                (tgt,utcnow(),pak_name,key)); processed+=1
        except Exception as e:
            db.execute('UPDATE translation_queue SET status="failed",engine="nllb",attempts=attempts+1,error=?,updated_at=? WHERE pak_name=? AND source_key=?',
                (str(e),utcnow(),pak_name,key)); failed+=1
    _save_records(records_path,records); db.commit()
    result={'processed_unique':processed,'applied_records':applied,'failed':failed,'review':review,'engine_device':engine.device,'stats':queue_stats_db(db,pak_name)}
    db.close(); return result
