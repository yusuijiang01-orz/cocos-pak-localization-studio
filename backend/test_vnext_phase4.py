#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vnext.build import build_history, build_verified_paks, preflight_build, prepare_legacy_records
from vnext.database import init_project_db, utcnow
from vnext.workspace_ingest import ingest_workspace_records


class VNextPhase4Tests(unittest.TestCase):
    def _workspace(self, root: Path, source: str = "$Hoàn thành <c=green>{0}</c> nhiệm vụ") -> Path:
        ws = root / "workspace"
        (ws / "localization").mkdir(parents=True)
        record = {
            "id": "r1",
            "pak": "ui.pak",
            "source_file": "0001_AABBCCDD.ini",
            "source_original": source,
            "original": source,
            "line": 1,
            "column": 1,
            "key": "Text",
            "encoding": "utf-8",
            "_isPlayerVisible": True,
        }
        (ws / "localization" / "text_records.json").write_text(
            json.dumps([record], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ws

    def _approve_all(self, db_path: Path) -> dict[str, str]:
        db = init_project_db(db_path)
        targets = {}
        for row in db.execute("SELECT unit_id,source_text FROM translation_units ORDER BY unit_id"):
            target = "完成" if "Hoàn thành" in row["source_text"] else "任务"
            targets[row["unit_id"]] = target
            db.execute(
                """INSERT OR REPLACE INTO current_targets(
                     unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at
                   ) VALUES(?,?, 'manual','test','', 'passed',1,?)""",
                (row["unit_id"], target, utcnow()),
            )
        db.commit(); db.close()
        return targets

    def test_workspace_ingest_keeps_runtime_tokens_out_of_units(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = self._workspace(root)
            report = ingest_workspace_records(ws)
            self.assertGreaterEqual(report["active_occurrences"], 1)
            db = init_project_db(ws / "vnext" / "project.sqlite3")
            sources = [r[0] for r in db.execute("SELECT source_text FROM translation_units")]
            self.assertFalse(any("{0}" in s or "<c=green>" in s for s in sources))
            row = db.execute("SELECT skeleton_json FROM occurrences LIMIT 1").fetchone()
            skeleton = json.loads(row["skeleton_json"])
            protected = "".join(str(x.get("value") or "") for x in skeleton if x.get("kind") == "protected")
            self.assertIn("<c=green>", protected)
            self.assertIn("{0}", protected)
            db.close()

    def test_prepare_records_reconstructs_translation_without_touching_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = self._workspace(root)
            ingest_workspace_records(ws)
            self._approve_all(ws / "vnext" / "project.sqlite3")
            out = root / "staged_records.json"
            report = prepare_legacy_records(ws, ws / "vnext" / "project.sqlite3", out)
            self.assertEqual(report["changed_records"], 1)
            record = json.loads(out.read_text(encoding="utf-8"))[0]
            self.assertIn("<c=green>{0}</c>", record["original"])
            self.assertIn("完成", record["original"])
            self.assertIn("任务", record["original"])
            self.assertEqual(record["source_original"], "$Hoàn thành <c=green>{0}</c> nhiệm vụ")

    def test_preflight_rechecks_bad_target_even_if_status_claims_passed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = self._workspace(root, "Mỗi ngày hoàn thành nhiệm vụ")
            ingest_workspace_records(ws)
            db_path = ws / "vnext" / "project.sqlite3"
            db = init_project_db(db_path)
            unit = db.execute("SELECT unit_id FROM translation_units LIMIT 1").fetchone()[0]
            db.execute(
                """INSERT OR REPLACE INTO current_targets(
                     unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at
                   ) VALUES(?,?,'model','test','','passed',0,?)""",
                (unit, "每天 ngươi 完成任务", utcnow()),
            )
            db.commit(); db.close()
            report = preflight_build(db_path, root / "knowledge.sqlite3")
            self.assertFalse(report["ok"])
            self.assertTrue(any(x["code"] == "FINAL_QA_FAILED" for x in report["blockers"]))

    def test_require_translated_blocks_missing_targets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = self._workspace(root, "Hoàn thành nhiệm vụ")
            ingest_workspace_records(ws)
            report = preflight_build(
                ws / "vnext" / "project.sqlite3",
                root / "knowledge.sqlite3",
                require_translated=True,
            )
            self.assertFalse(report["ok"])
            self.assertGreater(report["untranslated_unique"], 0)

    def test_verified_build_publishes_only_after_candidate_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = self._workspace(root, "Hoàn thành nhiệm vụ")
            original = root / "ui.pak"
            original.write_bytes(b"ORIGINAL")
            extracted = root / "ui_unpacked"
            extracted.mkdir()
            (ws / "project.json").write_text(
                json.dumps({"name": "test", "paks": [{"pak": "ui.pak", "path": str(original), "extracted": str(extracted)}]}),
                encoding="utf-8",
            )
            ingest_workspace_records(ws)
            self._approve_all(ws / "vnext" / "project.sqlite3")

            def fake_materialize(_extracted, _records, _pak, modified):
                modified.mkdir(parents=True, exist_ok=True)
                (modified / "0001_AABBCCDD.ini").write_bytes(b"Text=\xe4\xbb\xbb\xe5\x8a\xa1")
                return {"skipped_count": 0, "safe_fallback_count": 0, "changed_files": ["0001_AABBCCDD.ini"], "no_changes": False, "no_safe_changes": False}

            def fake_build(_original, _extracted, _modified, candidate, workers=1, verify=True):
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(b"VERIFIED-PAK")
                candidate.with_suffix(candidate.suffix + ".build.json").write_text("{}", encoding="utf-8")
                return {"roundtrip": "pass", "verify_failed": 0, "verify_count": 1, "verify_ok": 1, "changed_files": ["0001_AABBCCDD.ini"]}

            with patch("vnext.build.materialize_records_to_modified_dir", side_effect=fake_materialize), \
                 patch("vnext.build.build_from_modified_dir", side_effect=fake_build):
                report = build_verified_paks(
                    ws,
                    knowledge_db_path=root / "knowledge.sqlite3",
                    output_dir=root / "out",
                )
            self.assertTrue(report["ok"])
            self.assertEqual((root / "out" / "ui.pak").read_bytes(), b"VERIFIED-PAK")
            history = build_history(ws / "vnext" / "project.sqlite3")
            self.assertEqual(history[0]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
