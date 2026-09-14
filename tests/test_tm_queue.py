#!/usr/bin/env python3
from pathlib import Path
import json, tempfile, sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from localization_tm import init_db, add_tm, prepare_queue, apply_tm_queue_batch, learn_modified, queue_list

with tempfile.TemporaryDirectory() as td:
    td=Path(td); rp=td/'records.json'; dbp=td/'loc.db'
    records=[
      {'id':'a','pak':'ui.pak','language':'vi','original':'Mỉm cười','source_original':'Mỉm cười'},
      {'id':'b','pak':'ui.pak','language':'vi','original':'Mỉm cười','source_original':'Mỉm cười'},
      {'id':'c','pak':'ui.pak','language':'vi','original':'Nhận <c=g>0/1<c>','source_original':'Nhận <c=g>0/1<c>'},
      {'id':'d','pak':'ui.pak','language':'zh','original':'任务','source_original':'任务'},
    ]
    rp.write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
    db=init_db(dbp); add_tm(db,'Mỉm cười','微笑','test'); db.commit(); db.close()
    st=prepare_queue(rp,'ui.pak',dbp)
    assert st['unique_total']==3, st
    assert st['by_status']['tm_ready']['unique']==1, st
    assert st['by_status']['tm_ready']['occurrences']==2, st
    out=apply_tm_queue_batch(rp,'ui.pak',dbp,50)
    assert out['processed_unique']==1 and out['applied_records']==2, out
    rr=json.loads(rp.read_text(encoding='utf-8'))
    assert rr[0]['original']=='微笑' and rr[1]['original']=='微笑'
    rr[2]['original']='领取<c=g>0/1<c>'
    rp.write_text(json.dumps(rr,ensure_ascii=False),encoding='utf-8')
    learned=learn_modified(rp,'ui.pak',dbp,'csv-import')
    assert learned['risk_count']==0, learned
    assert learned['learned']>=2, learned
    pending=queue_list(dbp,'ui.pak','pending',10)
    assert isinstance(pending['items'],list)
print('TM queue tests: PASS')
