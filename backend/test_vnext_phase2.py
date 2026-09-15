#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vnext.classify import classify_source
from vnext.database import add_occurrence, ensure_project, init_knowledge_db, init_project_db, put_tm, upsert_unit
from vnext.knowledge import GlossaryIndex
from vnext.pipeline import run_pipeline


class FakeTranslator:
    engine_name = "fake"
    model = "fake-1"
    prompt_hash = hashlib.sha256(b"fake-prompt").hexdigest()

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = 0
        self.last_terms = []
        self.last_items = []

    def translate_batch(self, items, terminology):
        self.calls += 1
        self.last_terms = list(terminology)
        self.last_items = list(items)
        return {item["id"]: self.mapping[item["source"]] for item in items}


def seed_unit(project_db, text: str, record_id="r1"):
    project_id = ensure_project(project_db, "phase2-test")
    candidate = classify_source(text)
    upsert_unit(project_db, candidate)
    add_occurrence(
        project_db,
        project_id=project_id,
        unit_id=candidate.unit_id,
        record_id=record_id,
        pak_name="ui.pak",
        source_file="a.ini",
        source_fingerprint=f"fp:{record_id}:{candidate.unit_id}",
        locator={"line": 1},
        skeleton=[{"kind": "text", "unit_id": candidate.unit_id}],
    )
    project_db.commit()
    return candidate


class VNextPhase2Tests(unittest.TestCase):
    def test_trusted_tm_skips_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            project = init_project_db(pdb)
            unit = seed_unit(project, "Băng Hỏa Long Châu")
            project.close()

            knowledge = init_knowledge_db(kdb)
            put_tm(
                knowledge,
                source_text="Băng Hỏa Long Châu",
                target_text="冰火龙珠",
                quality="manual",
                locked=True,
            )
            knowledge.commit()
            knowledge.close()

            engine = FakeTranslator({"Băng Hỏa Long Châu": "错误模型译文"})
            report = run_pipeline(pdb, knowledge_db_path=kdb, translator=engine, batch_size=4)
            self.assertEqual(report["item_status"].get("tm"), 1)
            self.assertEqual(engine.calls, 0)

            project = init_project_db(pdb)
            target = project.execute(
                "SELECT target_text,locked FROM current_targets WHERE unit_id=?",
                (unit.unit_id,),
            ).fetchone()
            self.assertEqual(target["target_text"], "冰火龙珠")
            self.assertEqual(target["locked"], 1)
            project.close()

    def test_model_result_is_cached_and_reused_in_second_project(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            kdb = root / "knowledge.sqlite3"
            text = "Mỗi ngày người chơi hoàn thành nhiệm vụ"
            target = "玩家每天完成任务"

            first_db = root / "p1.sqlite3"
            project = init_project_db(first_db)
            seed_unit(project, text)
            project.close()
            engine = FakeTranslator({text: target})
            first = run_pipeline(first_db, knowledge_db_path=kdb, translator=engine, batch_size=2)
            self.assertEqual(first["item_status"].get("model"), 1)
            self.assertEqual(engine.calls, 1)

            second_db = root / "p2.sqlite3"
            project = init_project_db(second_db)
            seed_unit(project, text)
            project.close()
            second_engine = FakeTranslator({text: "不应调用"})
            second = run_pipeline(second_db, knowledge_db_path=kdb, translator=second_engine, batch_size=2)
            self.assertEqual(second["item_status"].get("cache"), 1)
            self.assertEqual(second_engine.calls, 0)

    def test_bad_mixed_model_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            text = "Mỗi ngày ngươi có thể hoàn thành nhiệm vụ"
            project = init_project_db(pdb)
            unit = seed_unit(project, text)
            project.close()

            engine = FakeTranslator({text: "每天 ngươi 可以完成任务"})
            report = run_pipeline(pdb, knowledge_db_path=kdb, translator=engine, batch_size=1)
            self.assertEqual(report["item_status"].get("rejected"), 1)

            project = init_project_db(pdb)
            row = project.execute(
                "SELECT qa_status,target_status FROM current_targets WHERE unit_id=?",
                (unit.unit_id,),
            ).fetchone()
            self.assertEqual(row["qa_status"], "failed")
            self.assertEqual(row["target_status"], "rejected")
            codes = {
                finding["code"]
                for finding in project.execute(
                    "SELECT code FROM qa_findings WHERE unit_id=? AND resolved=0",
                    (unit.unit_id,),
                )
            }
            self.assertTrue({"ZH_VI_MIXED", "ZH_LATIN_MIXED"} & codes)
            project.close()

    def test_only_approved_glossary_is_sent_and_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            text = "Tìm Băng Hỏa Long Châu"
            project = init_project_db(pdb)
            seed_unit(project, text)
            project.close()

            knowledge = init_knowledge_db(kdb)
            now = "2026-01-01T00:00:00+00:00"
            from vnext.normalize import source_key
            knowledge.execute(
                """INSERT INTO glossary_terms(
                   source_key,source_text,target_text,term_type,scope,status,priority,locked,
                   provenance,note,created_at,updated_at
                   ) VALUES(?,?,?,'item','global','approved',100,1,'test','',?,?)""",
                (source_key("Băng Hỏa Long Châu"), "Băng Hỏa Long Châu", "冰火龙珠", now, now),
            )
            knowledge.execute(
                """INSERT INTO glossary_terms(
                   source_key,source_text,target_text,term_type,scope,status,priority,locked,
                   provenance,note,created_at,updated_at
                   ) VALUES(?,?,?,'general','global','candidate',100,0,'test','',?,?)""",
                (source_key("Tìm"), "Tìm", "寻找", now, now),
            )
            knowledge.commit()
            knowledge.close()

            engine = FakeTranslator({text: "寻找冰火龙珠"})
            report = run_pipeline(pdb, knowledge_db_path=kdb, translator=engine, batch_size=1)
            self.assertEqual(report["item_status"].get("model"), 1)
            self.assertEqual(
                [(x["source"], x["target"]) for x in engine.last_terms],
                [("Băng Hỏa Long Châu", "冰火龙珠")],
            )
            self.assertEqual(
                [(x["source"], x["target"]) for x in engine.last_items[0]["terminology"]],
                [("Băng Hỏa Long Châu", "冰火龙珠")],
            )

    def test_ignored_locked_glossary_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            text = "Tìm Băng Hỏa Long Châu"
            project = init_project_db(pdb)
            seed_unit(project, text)
            project.close()

            knowledge = init_knowledge_db(kdb)
            from vnext.normalize import source_key
            now = "2026-01-01T00:00:00+00:00"
            knowledge.execute(
                """INSERT INTO glossary_terms(
                   source_key,source_text,target_text,term_type,scope,status,priority,locked,
                   provenance,note,created_at,updated_at
                   ) VALUES(?,?,?,'item','global','locked',100,1,'test','',?,?)""",
                (source_key("Băng Hỏa Long Châu"), "Băng Hỏa Long Châu", "冰火龙珠", now, now),
            )
            knowledge.commit()
            knowledge.close()

            engine = FakeTranslator({text: "寻找冰火神龙珠"})
            report = run_pipeline(pdb, knowledge_db_path=kdb, translator=engine, batch_size=1)
            self.assertEqual(report["item_status"].get("rejected"), 1)

    def test_glossary_index_respects_word_boundaries(self):
        rows = [
            {
                "source_text": "Cấp",
                "target_text": "等级",
                "term_type": "general",
                "scope": "global",
                "priority": 100,
                "locked": 1,
            },
            {
                "source_text": "Băng Hỏa Long Châu",
                "target_text": "冰火龙珠",
                "term_type": "item",
                "scope": "global",
                "priority": 200,
                "locked": 1,
            },
        ]
        index = GlossaryIndex(rows)
        found = index.find("Tìm Băng Hỏa Long Châu cấp 10")
        self.assertEqual({x["target"] for x in found}, {"冰火龙珠", "等级"})
        self.assertEqual(index.find("abcCấpxyz"), [])


if __name__ == "__main__":
    unittest.main()
