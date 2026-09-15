#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xlsx_localization import write_simple_xlsx
from vnext.classify import classify_source
from vnext.database import init_knowledge_db, init_project_db, put_tm
from vnext.ingest import ingest_full_xlsx
from vnext.models import SourceLanguage, UnitKind
from vnext.normalize import normalize_source, source_key
from vnext.protection import reconstruct, split_runtime_text
from vnext.qa import evaluate_translation, is_build_safe


class VNextPhase1Tests(unittest.TestCase):
    def test_normalization_and_key(self):
        a = "  Mỗi   ngày\r\n thử  "
        b = "Mỗi ngày\nthử"
        self.assertEqual(normalize_source(a), b)
        self.assertEqual(source_key(a), source_key(b))

    def test_classify(self):
        sentence = classify_source("Mỗi ngày ngươi có thể hoàn thành nhiệm vụ.")
        self.assertEqual(sentence.language, SourceLanguage.VI)
        self.assertEqual(sentence.kind, UnitKind.SENTENCE)
        mixed = classify_source("完成 nhiệm vụ")
        self.assertEqual(mixed.language, SourceLanguage.MIXED)

    def test_valid_vietnamese_a_circumflex_is_not_mojibake(self):
        # Real game strings contain names such as Ân Hồng/Ân Giao.  A previous
        # mojibake regex matched every standalone Â and excluded these rows from
        # the automatic translation pipeline.
        valid = classify_source("Bạn có thể đến Phong Thần đài tìm Ân Hồng bắt đầu nhiệm vụ")
        self.assertEqual(valid.language, SourceLanguage.VI)
        self.assertNotEqual(valid.kind, UnitKind.CORRUPT_SOURCE)
        self.assertNotIn("possible_mojibake", valid.risk_flags)
        broken = classify_source("TÃªn nhân vật")
        self.assertIn("possible_mojibake", broken.risk_flags)
        self.assertEqual(broken.kind, UnitKind.CORRUPT_SOURCE)

    def test_out_of_band_protection(self):
        text = "$Hoàn thành nhiệm vụ <c=green>{0}</c>"
        pieces = split_runtime_text(text)
        protected = [p.value for p in pieces if p.kind == "protected"]
        self.assertTrue(any("$" in p for p in protected))
        text_piece_count = sum(p.kind == "text" for p in pieces)
        rebuilt = reconstruct(pieces, ["任务完成"] * text_piece_count)
        self.assertIn("$", rebuilt); self.assertIn("<c=green>", rebuilt); self.assertIn("{0}", rebuilt)

    def test_qa_rejects_word_by_word(self):
        findings = evaluate_translation("Mỗi ngày ngươi có thể hoàn thành", "每 日 你 可 以 完 成")
        self.assertFalse(is_build_safe(findings))
        self.assertIn("WORD_BY_WORD_SPACED_HAN", {f.code for f in findings})

    def test_locked_tm_cannot_be_overwritten_by_unlocked(self):
        with tempfile.TemporaryDirectory() as td:
            db = init_knowledge_db(Path(td) / "knowledge.sqlite3")
            put_tm(db, source_text="Băng Hỏa Long Châu", target_text="冰火龙珠", quality="manual", locked=True)
            put_tm(db, source_text="Băng Hỏa Long Châu", target_text="冰火神龙珠", quality="model", locked=False)
            row = db.execute("SELECT target_text,locked FROM translation_memory").fetchone()
            self.assertEqual(row["target_text"], "冰火龙珠"); self.assertEqual(row["locked"], 1)
            db.close()

    def test_project_schema(self):
        with tempfile.TemporaryDirectory() as td:
            db = init_project_db(Path(td) / "project.sqlite3")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("translation_units", tables); self.assertIn("occurrences", tables)
            self.assertIn("translation_jobs", tables); self.assertIn("qa_findings", tables)
            db.close()

    def test_full_xlsx_reingest_deactivates_stale_occurrences(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            xlsx = root / "full.xlsx"
            db_path = root / "project.sqlite3"
            headers = ["id", "pak", "source_file", "text"]
            write_simple_xlsx(xlsx, [
                {"id": "a", "pak": "ui.pak", "source_file": "a.ini", "text": "Nhiệm vụ"},
                {"id": "b", "pak": "ui.pak", "source_file": "b.ini", "text": "Kỹ năng"},
            ], headers=headers)
            first = ingest_full_xlsx(xlsx, db_path, project_name="test")
            self.assertEqual(first["active_occurrences"], 2)

            write_simple_xlsx(xlsx, [
                {"id": "a", "pak": "ui.pak", "source_file": "a.ini", "text": "Nhiệm vụ"},
            ], headers=headers)
            second = ingest_full_xlsx(xlsx, db_path, project_name="test")
            self.assertEqual(second["active_occurrences"], 1)
            self.assertEqual(second["stale_occurrences"], 1)


if __name__ == "__main__":
    unittest.main()
