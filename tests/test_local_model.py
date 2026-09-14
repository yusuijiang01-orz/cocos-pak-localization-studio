import csv, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import local_model
from local_model import translate_preserving_tokens, model_status, model_translate_csv_tree
from localization_tm import add_tm, init_db, lookup_no_touch

class FakeEngine:
    def translate_batch(self,texts,batch_size=None):
        mp={'Nhận nhiệm vụ':'领取任务',' điểm':' 点','điểm':'点','Dòng một':'第一行','Dòng hai':'第二行'}
        return [mp.get(x,x+'[中]') for x in texts]

def test_tokens_and_newline_preserved():
    src='Nhận nhiệm vụ <c=g>0/1<c> %d\nDòng hai'
    with tempfile.TemporaryDirectory() as td:
        db=init_db(Path(td)/'x.db')
        out=translate_preserving_tokens(FakeEngine(),db,[src])[0]
        assert '<c=g>0/1<c> %d\n' in out
        assert out.startswith('领取任务')
        assert out.endswith('第二行')
        db.close()

def test_status_missing_model_is_safe():
    with tempfile.TemporaryDirectory() as td:
        st=model_status(Path(td)/'no-model')
        assert st['installed'] is False

def test_combined_csv_pipeline_and_checkpoint():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); source_dir=root/'before'; output_dir=root/'after'; source_dir.mkdir()
        with (source_dir/'part.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['id','text']); writer.writeheader()
            writer.writerows([{'id':'1','text':'TM source'},{'id':'2','text':'Script source'},{'id':'3','text':'Model source'}])
        script=root/'translate.py'
        script.write_text("def translate_string(text):\n    return {'Script source':'词典译文'}.get(text,text)\n",encoding='utf-8')
        db_path=root/'tm.db'; db=init_db(db_path); add_tm(db,'TM source','记忆译文'); db.commit(); db.close()
        calls=[]
        class PipelineEngine:
            def __init__(self,_path): calls.append('load')
            def translate_batch(self,texts,batch_size=None): return ['模型译文' if x=='Model source' else x for x in texts]
        old_status,old_engine=local_model.model_status,local_model.NLLBEngine
        local_model.model_status=lambda _path:{'installed':True,'message':'ok'}
        local_model.NLLBEngine=PipelineEngine
        try:
            events=[]
            report=model_translate_csv_tree(source_dir,output_dir,root/'model',db_path,progress=events.append,script_path=script)
            assert (report['tm_hits'],report['script_hits'],report['model_hits'])==(1,1,1)
            assert report['learned_tm']==2 and calls==['load']
            assert any(event.get('total_rows')==3 for event in events)
            assert any('3' in event.get('upcoming_ids',[]) for event in events)
            assert any(any(update.get('id')=='3' and update.get('text')=='模型译文' for update in event.get('updates',[])) for event in events)
            db=init_db(db_path)
            assert lookup_no_touch(db,'Script source')[0]=='词典译文'
            assert lookup_no_touch(db,'Model source')[0]=='模型译文'
            db.close()
            calls.clear()
            resumed=model_translate_csv_tree(source_dir,output_dir,root/'model',db_path,script_path=script)
            assert resumed['skipped_files']==1 and calls==[]
        finally:
            local_model.model_status,local_model.NLLBEngine=old_status,old_engine

if __name__=='__main__':
    test_tokens_and_newline_preserved();test_status_missing_model_is_safe();test_combined_csv_pipeline_and_checkpoint();print('OK')
