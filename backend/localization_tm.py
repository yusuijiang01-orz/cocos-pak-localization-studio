#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, re, sqlite3, unicodedata
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

RESOURCE_EXTENSIONS = r'spr|bmp|png|jpe?g|gif|dds|tga|wav|mp3|ogg|mid|ani|cur|ico|ttf|fnt|otf|woff2?|ini|lua|txt|tsv|csv|xml|json|pak|dat|bin|plist|csb|astc|pvr|pkm|ktx|mp4|webm'
# Resource names are not necessarily ASCII.  Vietnamese/Chinese text inside a
# path is still an identifier and must never be sent to a translator.  Accept
# relative paths, drive paths, and Lua's doubled backslashes.
RESOURCE_PATH_PATTERN = (
    r'(?:(?:[A-Za-z]:)?[\\/]+|[A-Za-z0-9_.@#$-]+[\\/]+)'
    r'(?:[^\s<>"\'|,;()\[\]{}]+[\\/]+)*'
    r'[^\s<>"\'|,;()\[\]{}]+\.(?:' + RESOURCE_EXTENSIONS + r')\b'
)

TOKEN_RE = re.compile(
    r'((?:' + RESOURCE_PATH_PATTERN + r')'
    r'|<[^<>\r\n]{1,160}>'
    r'|</?[A-Za-z][A-Za-z0-9_:-]*(?:=[^>\r\n]*)?>'
    r'|%%|%[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlLzjt]*[diuoxXfFeEgGaAcspn]'
    r'|\\[nrt0\\"\']'
    r'|\$\{[^{}]+\}'
    r'|\{(?:\d+|[A-Za-z_][^{}]*)\}'
    r'|#[A-Za-z]\d+[-+]?'
    r'|[@$#]'
    r'|(?<!\d)\d+\s*/\s*\d+(?!\d))'
)

QUEUE_ACTIVE = ('pending','tm_ready','failed','review')
TRUSTED_TM_QUALITIES = ('manual', 'reviewed', 'approved', 'seed')
API_CACHE_QUALITIES = TRUSTED_TM_QUALITIES + ('api-auto',)

def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def exchange_text(value:str)->str:
    s=str(value or '').replace('\r\n','\n').replace('\r','\n')
    if s.startswith('$'): s=s[1:]
    return re.sub(r'<\\n>', '\n', s, flags=re.I)

def internal_text(value:str, source_template:str)->str:
    s=str(value or '').replace('\r\n','\n').replace('\r','\n').replace('\n','<\\n>')
    src=str(source_template or '')
    if src.startswith('$') and not s.startswith('$'): s='$'+s
    return s

def normalize_key(value:str)->str:
    s=unicodedata.normalize('NFC', exchange_text(value))
    s='\n'.join(re.sub(r'[ \t]+',' ',line).strip() for line in s.split('\n'))
    return s.strip()

def protected_tokens(value:str):
    # Validation must see leading resource markers too. exchange_text() removes
    # a leading '$' for translation-memory matching, which is correct for keys
    # but unsafe for structural validation.
    s=str(value or '').replace('\r\n','\n').replace('\r','\n')
    s=re.sub(r'<\\n>', '\n', s, flags=re.I)
    return TOKEN_RE.findall(s)

def validate_tokens(source:str,target:str):
    a=protected_tokens(source); b=protected_tokens(target)
    return a==b, a, b

def init_db(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    db.executescript('''
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=NORMAL;
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS translation_memory(
      source_key TEXT PRIMARY KEY,
      source_text TEXT NOT NULL,
      target_text TEXT NOT NULL,
      source_lang TEXT DEFAULT 'vi',
      target_lang TEXT DEFAULT 'zh-CN',
      quality TEXT DEFAULT 'manual',
      usage_count INTEGER DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS glossary(
      source TEXT PRIMARY KEY,
      target TEXT NOT NULL,
      mode TEXT DEFAULT 'exact',
      note TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS protected_patterns(
      name TEXT PRIMARY KEY,
      pattern TEXT NOT NULL,
      priority INTEGER DEFAULT 100
    );
    CREATE TABLE IF NOT EXISTS translation_queue(
      pak_name TEXT NOT NULL,
      source_key TEXT NOT NULL,
      source_text TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      occurrence_count INTEGER NOT NULL DEFAULT 1,
      target_text TEXT DEFAULT '',
      engine TEXT DEFAULT '',
      attempts INTEGER NOT NULL DEFAULT 0,
      error TEXT DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY(pak_name,source_key)
    );
    CREATE INDEX IF NOT EXISTS idx_queue_pak_status ON translation_queue(pak_name,status);
    ''')
    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','2')")
    db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('created_at',?)",(utcnow(),))
    patterns=[
      ('angle_tag',r'<[^>\\r\\n]+>',10),('printf',r'%[-+ #0]*(?:\\d+|\\*)?(?:\\.(?:\\d+|\\*))?[hlLzjt]*[diuoxXfFeEgGaAcspn%]',20),
      ('escape',r'\\\\[nrt0\\\\\"\']',30),('template',r'\\$\\{[^{}]+\\}|\\{(?:\\d+|[A-Za-z_][^{}]*)\\}',40),('counter',r'(?<!\\d)\\d+\\s*/\\s*\\d+(?!\\d)',50)
    ]
    db.executemany('INSERT OR REPLACE INTO protected_patterns(name,pattern,priority) VALUES(?,?,?)',patterns)
    db.commit(); return db

def add_tm(db, source:str, target:str, quality='manual'):
    src=exchange_text(source); tgt=exchange_text(target)
    ok,a,b=validate_tokens(src,tgt)
    if not ok:
        raise ValueError(f'Protected token mismatch: source={a!r}, target={b!r}')
    key=normalize_key(src)
    if not key: raise ValueError('Empty source text')
    if normalize_key(tgt)==key: raise ValueError('Source and target are identical')
    now=utcnow()
    db.execute('''INSERT INTO translation_memory(source_key,source_text,target_text,quality,usage_count,created_at,updated_at)
      VALUES(?,?,?,?,0,?,?)
      ON CONFLICT(source_key) DO UPDATE SET source_text=excluded.source_text,target_text=excluded.target_text,
      quality=excluded.quality,updated_at=excluded.updated_at''',(key,src,tgt,quality,now,now))

def lookup_no_touch(db, source:str):
    key=normalize_key(source)
    placeholders=','.join('?' for _ in TRUSTED_TM_QUALITIES)
    row=db.execute(f'SELECT target_text,quality FROM translation_memory WHERE source_key=? AND quality IN ({placeholders})',(key,*TRUSTED_TM_QUALITIES)).fetchone()
    if row: return row['target_text'],f'tm:{row["quality"]}'
    row=db.execute('SELECT target FROM glossary WHERE source=? AND mode="exact"',(key,)).fetchone()
    if row: return row['target'],'glossary'
    return None,None

def lookup(db, source:str):
    key=normalize_key(source)
    placeholders=','.join('?' for _ in TRUSTED_TM_QUALITIES)
    row=db.execute(f'SELECT target_text,quality FROM translation_memory WHERE source_key=? AND quality IN ({placeholders})',(key,*TRUSTED_TM_QUALITIES)).fetchone()
    if row:
        db.execute('UPDATE translation_memory SET usage_count=usage_count+1,updated_at=? WHERE source_key=?',(utcnow(),key)); db.commit()
        return row['target_text'],f'tm:{row["quality"]}'
    row=db.execute('SELECT target FROM glossary WHERE source=? AND mode="exact"',(key,)).fetchone()
    if row: return row['target'],'glossary'
    return None,None

def lookup_api_cache(db, source:str):
    key=normalize_key(source)
    placeholders=','.join('?' for _ in API_CACHE_QUALITIES)
    row=db.execute(f'SELECT target_text,quality FROM translation_memory WHERE source_key=? AND quality IN ({placeholders})',(key,*API_CACHE_QUALITIES)).fetchone()
    if row:
        db.execute('UPDATE translation_memory SET usage_count=usage_count+1,updated_at=? WHERE source_key=?',(utcnow(),key)); db.commit()
        return row['target_text'],f'tm:{row["quality"]}'
    row=db.execute('SELECT target FROM glossary WHERE source=? AND mode="exact"',(key,)).fetchone()
    if row: return row['target'],'glossary'
    return None,None

def seed_db(path:Path):
    db=init_db(path)
    seed={
      'Mỉm cười':'微笑','Cười lớn':'大笑','Giật mình':'吃惊','Nhăn mặt 1':'皱眉1','Nhăn mặt 2':'皱眉2',
      'Bị đánh':'被击','Nước miếng':'口水','Đứng':'站立','Ngồi':'坐下','Đi bộ':'行走','Chạy':'奔跑','Nhảy':'跳跃',
      'Tấn công':'攻击','Phòng thủ':'防御','Vũ khí':'武器','Trang bị':'装备','Nhiệm vụ':'任务','Kỹ năng':'技能','Vật phẩm':'道具',
      'Đóng':'关闭','Mở':'打开','Đồng ý':'确定','Hủy':'取消','Hủy bỏ':'取消','Quay lại':'返回','Thoát':'退出',
      'Bắt đầu':'开始','Kết thúc':'结束','Xác nhận':'确认','Mua':'购买','Bán':'出售','Sử dụng':'使用','Nhận':'领取',
      'Phần thưởng':'奖励','Cấp':'等级','Kinh nghiệm':'经验','Máu':'生命','Nội lực':'内力','Tốc độ':'速度','Thành công':'成功','Thất bại':'失败'
    }
    for s,t in seed.items():
        try: add_tm(db,s,t,'seed')
        except ValueError: pass
    glossary={
      'nhiệm vụ':'任务','kỹ năng':'技能','vật phẩm':'道具','trang bị':'装备','vũ khí':'武器','phần thưởng':'奖励',
      'kinh nghiệm':'经验','tấn công':'攻击','phòng thủ':'防御','cấp':'等级'
    }
    db.executemany('INSERT OR IGNORE INTO glossary(source,target,mode,note) VALUES(?, ?, "exact", ?)',[(k,v,'基础术语；仅整句精确匹配') for k,v in glossary.items()])
    db.commit(); db.close()

def _load_records(records_path:Path):
    return json.loads(records_path.read_text(encoding='utf-8'))

def _save_records(records_path:Path, records):
    records_path.write_text(json.dumps(records,ensure_ascii=False,separators=(',',':')),encoding='utf-8')

def batch_translate(records_path:Path,pak_name:str,db_path:Path):
    records=_load_records(records_path); db=init_db(db_path)
    hit=0; skipped=0; risks=0; unmatched=0
    for r in records:
        if r.get('pak')!=pak_name: continue
        src=r.get('source_original',r.get('original','')); cur=r.get('original','')
        if cur!=src or r.get('language')=='zh': skipped+=1; continue
        tgt,kind=lookup(db,src)
        if tgt is None: unmatched+=1; continue
        ok,a,b=validate_tokens(exchange_text(src),tgt)
        if not ok:
            r['status']='格式风险'; r['note']=f'词库标记不匹配 source={a} target={b}'; risks+=1; continue
        r['source_original']=src; r['original']=internal_text(tgt,src); r['status']='TM匹配'; r['note']=f'local:{kind}'; hit+=1
    _save_records(records_path,records); db.close()
    return {'translated':hit,'skipped':skipped,'unmatched':unmatched,'risks':risks,'pak':pak_name}

def learn_record(records_path:Path,record_id:str,db_path:Path):
    records=_load_records(records_path); rec=next((r for r in records if r.get('id')==record_id),None)
    if not rec: raise KeyError(f'Record not found: {record_id}')
    src=rec.get('source_original',rec.get('original','')); tgt=rec.get('original','')
    if tgt==src: raise ValueError('当前记录还没有译文')
    db=init_db(db_path); add_tm(db,src,tgt,'manual'); db.commit(); db.close()
    return {'id':record_id,'source':exchange_text(src),'target':exchange_text(tgt)}

def learn_modified(records_path:Path,pak_name:str,db_path:Path,quality='manual'):
    records=_load_records(records_path); db=init_db(db_path)
    unique={}; duplicate_occurrences=0; risks=[]
    for r in records:
        if r.get('pak')!=pak_name: continue
        src=r.get('source_original',r.get('original','')); tgt=r.get('original','')
        if exchange_text(tgt)==exchange_text(src): continue
        key=normalize_key(src)
        if key in unique:
            duplicate_occurrences+=1
            # Conflicting translations for the same source must not silently overwrite each other.
            if normalize_key(unique[key][1])!=normalize_key(tgt):
                risks.append({'id':r.get('id'),'error':'同一原文存在多个不同译文，未自动写入 TM'})
            continue
        unique[key]=(src,tgt,r.get('id'))
    learned=0
    for src,tgt,rid in unique.values():
        try: add_tm(db,src,tgt,quality); learned+=1
        except ValueError as e: risks.append({'id':rid,'error':str(e)})
    db.commit(); db.close()
    return {'learned':learned,'unique_modified':len(unique),'duplicate_occurrences':duplicate_occurrences,'risks':risks[:100],'risk_count':len(risks),'pak':pak_name}

def prepare_queue(records_path:Path,pak_name:str,db_path:Path):
    records=_load_records(records_path); db=init_db(db_path); now=utcnow()
    groups={}
    for r in records:
        if r.get('pak')!=pak_name: continue
        src=r.get('source_original',r.get('original',''))
        key=normalize_key(src)
        if not key: continue
        g=groups.setdefault(key,{'source':src,'count':0,'translated_count':0,'zh':True})
        g['count']+=1
        if r.get('original','')!=src: g['translated_count']+=1
        if r.get('language')!='zh': g['zh']=False
    db.execute('DELETE FROM translation_queue WHERE pak_name=?',(pak_name,))
    counts=Counter()
    for key,g in groups.items():
        target=''; engine=''; error=''
        if g['zh']:
            status='source_zh'
        elif g['translated_count']>=g['count']:
            status='completed'
        else:
            target,kind=lookup_no_touch(db,g['source'])
            if target is not None:
                ok,a,b=validate_tokens(g['source'],target)
                if ok: status='tm_ready'; engine=kind or 'tm'
                else: status='review'; error=f'控制标记不一致 source={a} target={b}'
            else: status='pending'
        counts[status]+=1
        db.execute('''INSERT INTO translation_queue(pak_name,source_key,source_text,status,occurrence_count,target_text,engine,attempts,error,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(pak_name,key,exchange_text(g['source']),status,g['count'],target or '',engine,0,error,now,now))
    db.commit(); result=queue_stats_db(db,pak_name); db.close(); return result

def queue_stats_db(db,pak_name:str):
    rows=db.execute('SELECT status,COUNT(*) n,COALESCE(SUM(occurrence_count),0) occ FROM translation_queue WHERE pak_name=? GROUP BY status',(pak_name,)).fetchall()
    by_status={r['status']:{'unique':r['n'],'occurrences':r['occ']} for r in rows}
    total=db.execute('SELECT COUNT(*) n,COALESCE(SUM(occurrence_count),0) occ FROM translation_queue WHERE pak_name=?',(pak_name,)).fetchone()
    pending=sum(by_status.get(s,{}).get('unique',0) for s in ('pending','tm_ready','failed','review'))
    completed=sum(by_status.get(s,{}).get('unique',0) for s in ('completed','source_zh'))
    return {'pak':pak_name,'unique_total':total['n'],'occurrence_total':total['occ'],'completed_unique':completed,'remaining_unique':pending,'by_status':by_status}

def queue_stats(db_path:Path,pak_name:str):
    db=init_db(db_path); result=queue_stats_db(db,pak_name); db.close(); return result

def queue_list(db_path:Path,pak_name:str,status='pending',limit=500):
    db=init_db(db_path)
    if status=='all':
        rows=db.execute('SELECT source_key,source_text,status,occurrence_count,target_text,engine,error FROM translation_queue WHERE pak_name=? ORDER BY occurrence_count DESC,source_text LIMIT ?', (pak_name,limit)).fetchall()
    else:
        rows=db.execute('SELECT source_key,source_text,status,occurrence_count,target_text,engine,error FROM translation_queue WHERE pak_name=? AND status=? ORDER BY occurrence_count DESC,source_text LIMIT ?', (pak_name,status,limit)).fetchall()
    out=[dict(r) for r in rows]; db.close(); return {'pak':pak_name,'status':status,'items':out}

def apply_tm_queue_batch(records_path:Path,pak_name:str,db_path:Path,batch_size=200):
    records=_load_records(records_path); db=init_db(db_path)
    # Re-evaluate pending rows because CSV/manual learning may have added new TM entries after queue creation.
    pending=db.execute('SELECT source_key,source_text FROM translation_queue WHERE pak_name=? AND status="pending"',(pak_name,)).fetchall()
    for row in pending:
        tgt,kind=lookup_no_touch(db,row['source_text'])
        if tgt is None: continue
        ok,a,b=validate_tokens(row['source_text'],tgt)
        if ok:
            db.execute('UPDATE translation_queue SET status="tm_ready",target_text=?,engine=?,error="",updated_at=? WHERE pak_name=? AND source_key=?',(tgt,kind or 'tm',utcnow(),pak_name,row['source_key']))
        else:
            db.execute('UPDATE translation_queue SET status="review",target_text=?,engine=?,error=?,updated_at=? WHERE pak_name=? AND source_key=?',(tgt,kind or 'tm',f'控制标记不一致 source={a} target={b}',utcnow(),pak_name,row['source_key']))
    rows=db.execute('SELECT source_key,source_text,target_text,engine FROM translation_queue WHERE pak_name=? AND status="tm_ready" ORDER BY occurrence_count DESC LIMIT ?', (pak_name,int(batch_size))).fetchall()
    if not rows:
        db.commit(); result={'processed_unique':0,'applied_records':0,'remaining_ready':0,'stats':queue_stats_db(db,pak_name)}; db.close(); return result
    by_key=defaultdict(list)
    for r in records:
        if r.get('pak')==pak_name:
            src=r.get('source_original',r.get('original','')); by_key[normalize_key(src)].append(r)
    processed=0; applied=0; risks=0
    for row in rows:
        key=row['source_key']; tgt=row['target_text']; src=row['source_text']; ok,a,b=validate_tokens(src,tgt)
        if not ok:
            db.execute('UPDATE translation_queue SET status="review",attempts=attempts+1,error=?,updated_at=? WHERE pak_name=? AND source_key=?',(f'控制标记不一致 source={a} target={b}',utcnow(),pak_name,key)); risks+=1; continue
        for r in by_key.get(key,[]):
            source_internal=r.get('source_original',r.get('original',''))
            if r.get('original','')==source_internal:
                r['original']=internal_text(tgt,source_internal); r['status']='TM匹配'; r['note']=f'local:{row["engine"] or "tm"}'; applied+=1
        db.execute('UPDATE translation_queue SET status="completed",attempts=attempts+1,error="",updated_at=? WHERE pak_name=? AND source_key=?',(utcnow(),pak_name,key)); processed+=1
    _save_records(records_path,records); db.commit()
    remaining=db.execute('SELECT COUNT(*) n FROM translation_queue WHERE pak_name=? AND status="tm_ready"',(pak_name,)).fetchone()['n']
    result={'processed_unique':processed,'applied_records':applied,'risks':risks,'remaining_ready':remaining,'stats':queue_stats_db(db,pak_name)}
    db.close(); return result

def stats(db_path:Path):
    db=init_db(db_path)
    tm=db.execute('SELECT COUNT(*) FROM translation_memory').fetchone()[0]
    gl=db.execute('SELECT COUNT(*) FROM glossary').fetchone()[0]
    q=db.execute('SELECT COUNT(*) FROM translation_queue').fetchone()[0]
    db.close(); return {'tm':tm,'glossary':gl,'queue':q}
