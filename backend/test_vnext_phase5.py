from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vnext.classify import classify_source
from vnext.database import add_occurrence, ensure_project, init_project_db, put_tm, upsert_unit
from vnext.ui_api import dashboard, delete_glossary, delete_tm, knowledge_list, save_glossary, save_tm


class VNextPhase5Tests(unittest.TestCase):
    def test_dashboard_handles_empty_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = dashboard(root, root / 'vnext' / 'project.sqlite3', root / 'knowledge.sqlite3')
            self.assertEqual(report['unique_units'], 0)
            self.assertEqual(report['translated_unique'], 0)
            self.assertFalse(report['has_records'])

    def test_dashboard_counts_unique_targets_and_knowledge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'localization').mkdir()
            (root / 'localization' / 'text_records.json').write_text('[]', encoding='utf-8')
            (root / 'project.json').write_text(json.dumps({'paks':[{'pak':'ui.pak','path':'C:/game/ui.pak'}]}), encoding='utf-8')
            pdb = root / 'vnext' / 'project.sqlite3'
            kdb = root / 'knowledge.sqlite3'
            db = init_project_db(pdb)
            pid = ensure_project(db, 'demo')
            candidate = classify_source('Hoàn thành nhiệm vụ')
            upsert_unit(db, candidate)
            add_occurrence(db, project_id=pid, unit_id=candidate.unit_id, record_id='r1', pak_name='ui.pak', source_file='0001.txt', source_fingerprint='f1')
            db.execute("INSERT INTO current_targets(unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at) VALUES(?,?,?,?,?,'passed',0,datetime('now'))", (candidate.unit_id, '完成任务', 'model', 'model:qwen', 'cache'))
            db.commit(); db.close()
            k = __import__('vnext.database', fromlist=['init_knowledge_db']).init_knowledge_db(kdb)
            put_tm(k, source_text='Hoàn thành nhiệm vụ', target_text='完成任务', quality='manual', locked=True, provenance='test')
            k.commit(); k.close()
            report = dashboard(root, pdb, kdb)
            self.assertEqual(report['unique_units'], 1)
            self.assertEqual(report['translated_unique'], 1)
            self.assertEqual(report['knowledge']['tm'], 1)
            self.assertEqual(report['knowledge']['tm_locked'], 1)
            self.assertEqual(report['paks'][0]['pak'], 'ui.pak')

    def test_glossary_save_list_and_delete(self):
        with tempfile.TemporaryDirectory() as td:
            kdb = Path(td) / 'knowledge.sqlite3'
            saved = save_glossary(kdb, source='Băng Hỏa Long Châu', target='冰火龙珠', term_type='item', locked=True)
            self.assertTrue(saved['ok'])
            term_id = saved['item']['term_id']
            listed = knowledge_list(kdb, kind='glossary', query='Băng Hỏa')
            self.assertEqual(listed['total'], 1)
            self.assertEqual(listed['items'][0]['target_text'], '冰火龙珠')
            deleted = delete_glossary(kdb, term_id)
            self.assertTrue(deleted['ok'])
            self.assertEqual(knowledge_list(kdb, kind='glossary')['total'], 0)

    def test_tm_save_is_manual_locked_and_reusable(self):
        with tempfile.TemporaryDirectory() as td:
            kdb = Path(td) / 'knowledge.sqlite3'
            saved = save_tm(kdb, source='Thất Quải thí luyện', target='七关试炼', locked=True)
            self.assertTrue(saved['ok'])
            self.assertEqual(saved['item']['quality'], 'manual')
            self.assertEqual(saved['item']['locked'], 1)
            listed = knowledge_list(kdb, kind='tm', query='七关')
            self.assertEqual(listed['total'], 1)
            self.assertEqual(listed['items'][0]['source_text'], 'Thất Quải thí luyện')
            self.assertTrue(delete_tm(kdb, saved['item']['tm_id'])['ok'])

    def test_reference_list_is_read_only_query_surface(self):
        with tempfile.TemporaryDirectory() as td:
            from vnext.database import init_knowledge_db, utcnow
            kdb = Path(td) / 'knowledge.sqlite3'
            db = init_knowledge_db(kdb)
            now = utcnow()
            db.execute("INSERT INTO canonical_reference(source_alias_key,source_alias,canonical_zh,entity_type,status,provenance,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", ('k1','Khương Tử Nha','姜子牙','character','approved','test','',now,now))
            db.commit(); db.close()
            listed = knowledge_list(kdb, kind='reference', query='姜子牙')
            self.assertEqual(listed['total'], 1)
            self.assertEqual(listed['items'][0]['source_alias'], 'Khương Tử Nha')


if __name__ == '__main__':
    unittest.main()
