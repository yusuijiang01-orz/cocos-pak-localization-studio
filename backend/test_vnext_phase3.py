#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vnext.classify import classify_source
from vnext.database import add_occurrence, ensure_project, init_knowledge_db, init_project_db, upsert_unit, utcnow
from vnext.normalize import source_key
from vnext.pipeline import run_pipeline
from vnext.qa import evaluate_translation, is_build_safe
from vnext.review import (
    approve_review,
    is_target_blocked,
    reject_review,
    review_stats,
    sync_review_queue,
)


class FakeTranslator:
    engine_name = "fake"
    model = "fake-review-1"
    prompt_hash = hashlib.sha256(b"phase3-fake-prompt").hexdigest()

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = 0

    def translate_batch(self, items, terminology):
        self.calls += 1
        return {item["id"]: self.mapping[item["source"]] for item in items}


def seed_unit(db, text: str, record_id: str = "r1"):
    project_id = ensure_project(db, "phase3-test")
    candidate = classify_source(text)
    upsert_unit(db, candidate)
    add_occurrence(
        db,
        project_id=project_id,
        unit_id=candidate.unit_id,
        record_id=record_id,
        pak_name="ui.pak",
        source_file="review.ini",
        source_fingerprint=f"phase3:{record_id}:{candidate.unit_id}",
        locator={"line": 1},
        skeleton=[{"kind": "text", "unit_id": candidate.unit_id}],
    )
    db.commit()
    return candidate


def set_target(db, unit_id: str, text: str, *, status="model", qa_status="passed", locked=0, origin="test"):
    db.execute(
        """INSERT INTO current_targets(unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(unit_id) DO UPDATE SET target_text=excluded.target_text,target_status=excluded.target_status,
             origin=excluded.origin,knowledge_ref=excluded.knowledge_ref,qa_status=excluded.qa_status,
             locked=excluded.locked,updated_at=excluded.updated_at""",
        (unit_id, text, status, origin, "", qa_status, int(locked), utcnow()),
    )
    db.commit()


class VNextPhase3Tests(unittest.TestCase):
    def test_numeric_mismatch_is_build_blocker(self):
        findings = evaluate_translation("Nhận 10 vật phẩm", "领取20个道具")
        self.assertFalse(is_build_safe(findings))
        self.assertIn("NUMBER_MISMATCH", {item.code for item in findings})

    def test_no_chinese_target_is_build_blocker(self):
        findings = evaluate_translation("Hoàn thành nhiệm vụ", "Complete mission")
        self.assertFalse(is_build_safe(findings))
        self.assertIn("NO_CHINESE_TARGET", {item.code for item in findings})

    def test_review_sync_queues_rejected_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            db = init_project_db(pdb)
            unit = seed_unit(db, "Mỗi ngày hoàn thành nhiệm vụ")
            set_target(db, unit.unit_id, "每天 ngươi 完成任务", status="rejected", qa_status="failed")
            db.close()

            report = sync_review_queue(pdb, kdb)
            self.assertEqual(report["pending"], 1)
            db = init_project_db(pdb)
            row = db.execute("SELECT state,reason_code,severity FROM review_items WHERE unit_id=?", (unit.unit_id,)).fetchone()
            self.assertEqual(row["state"], "pending")
            self.assertEqual(row["reason_code"], "TARGET_REJECTED")
            self.assertEqual(row["severity"], "error")
            db.close()

    def test_mixed_source_is_reviewed_even_before_translation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            db = init_project_db(pdb)
            unit = seed_unit(db, "使用 Mảnh Hoàng Thuỷ Tinh")
            db.close()

            sync_review_queue(pdb, kdb)
            db = init_project_db(pdb)
            row = db.execute("SELECT state,reason_code FROM review_items WHERE unit_id=?", (unit.unit_id,)).fetchone()
            self.assertEqual(row["state"], "pending")
            self.assertEqual(row["reason_code"], "MIXED_SOURCE_REVIEW")
            db.close()

    def test_manual_edit_approval_promotes_locked_tm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            source = "Tìm Băng Hỏa Long Châu"
            db = init_project_db(pdb)
            unit = seed_unit(db, source)
            set_target(db, unit.unit_id, "寻找冰火神龙珠")
            db.close()

            result = approve_review(
                pdb,
                kdb,
                unit.unit_id,
                target_text="寻找冰火龙珠",
                note="人工校正专名",
                lock=True,
            )
            self.assertEqual(result["quality"], "manual")
            self.assertTrue(result["locked"])

            knowledge = init_knowledge_db(kdb)
            row = knowledge.execute(
                "SELECT target_text,quality,locked FROM translation_memory WHERE source_key=?",
                (source_key(source),),
            ).fetchone()
            self.assertEqual(row["target_text"], "寻找冰火龙珠")
            self.assertEqual(row["quality"], "manual")
            self.assertEqual(row["locked"], 1)
            knowledge.close()

            db = init_project_db(pdb)
            review = db.execute("SELECT state FROM review_items WHERE unit_id=?", (unit.unit_id,)).fetchone()
            target = db.execute("SELECT target_text,target_status,locked FROM current_targets WHERE unit_id=?", (unit.unit_id,)).fetchone()
            self.assertEqual(review["state"], "approved")
            self.assertEqual(target["target_text"], "寻找冰火龙珠")
            self.assertEqual(target["target_status"], "manual")
            self.assertEqual(target["locked"], 1)
            db.close()

    def test_unsafe_manual_target_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            db = init_project_db(pdb)
            unit = seed_unit(db, "Mỗi ngày hoàn thành nhiệm vụ")
            db.close()
            with self.assertRaises(ValueError):
                approve_review(pdb, kdb, unit.unit_id, target_text="每天 nhiệm vụ")

    def test_rejected_translation_is_blocked_from_reappearing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            source = "Mỗi ngày người chơi hoàn thành nhiệm vụ"
            target = "玩家每天完成任务"
            db = init_project_db(pdb)
            unit = seed_unit(db, source)
            db.close()

            first_engine = FakeTranslator({source: target})
            first = run_pipeline(pdb, knowledge_db_path=kdb, translator=first_engine, batch_size=1)
            self.assertEqual(first["item_status"].get("model"), 1)
            self.assertEqual(first_engine.calls, 1)

            rejected = reject_review(pdb, kdb, unit.unit_id, note="语义不准确")
            self.assertTrue(rejected["blocked_target"])
            db = init_project_db(pdb)
            self.assertTrue(is_target_blocked(db, unit.unit_id, target))
            db.close()

            second_engine = FakeTranslator({source: target})
            second = run_pipeline(pdb, knowledge_db_path=kdb, translator=second_engine, batch_size=1)
            self.assertEqual(second_engine.calls, 1)
            self.assertEqual(second["item_status"].get("failed"), 1)
            db = init_project_db(pdb)
            current = db.execute("SELECT target_status,qa_status FROM current_targets WHERE unit_id=?", (unit.unit_id,)).fetchone()
            self.assertEqual(current["target_status"], "rejected")
            self.assertEqual(current["qa_status"], "rejected")
            db.close()

    def test_approved_locked_item_stays_approved_after_sync(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdb = root / "project.sqlite3"
            kdb = root / "knowledge.sqlite3"
            source = "Hoàn thành nhiệm vụ"
            db = init_project_db(pdb)
            unit = seed_unit(db, source)
            set_target(db, unit.unit_id, "完成任务")
            db.close()

            approve_review(pdb, kdb, unit.unit_id, lock=True)
            sync_review_queue(pdb, kdb)
            db = init_project_db(pdb)
            row = db.execute("SELECT state FROM review_items WHERE unit_id=?", (unit.unit_id,)).fetchone()
            self.assertEqual(row["state"], "approved")
            db.close()

            stats = review_stats(pdb)
            self.assertEqual(stats["by_state"].get("approved"), 1)


if __name__ == "__main__":
    unittest.main()
