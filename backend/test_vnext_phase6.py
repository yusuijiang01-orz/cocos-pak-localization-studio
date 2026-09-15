#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vnext.compatibility import adopt_legacy_targets, require_workspace_synced, workspace_sync_status
from vnext.database import init_project_db, utcnow
from vnext.workspace_ingest import ingest_workspace_records


class VNextPhase6Tests(unittest.TestCase):
    def make_workspace(self, root: Path, target: str = "Nhận phần thưởng") -> Path:
        workspace = root / "workspace"
        (workspace / "localization").mkdir(parents=True)
        (workspace / "project.json").write_text(
            json.dumps({"name": "phase6-test", "paks": [{"pak": "ui.pak"}]}),
            encoding="utf-8",
        )
        records = [{
            "id": "r1",
            "pak": "ui.pak",
            "source_file": "ui/test.ini",
            "line": 1,
            "column": 1,
            "key": "Text",
            "source_original": "Nhận phần thưởng",
            "original": target,
            "_isPlayerVisible": True,
        }]
        (workspace / "localization" / "text_records.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8"
        )
        return workspace

    def mutate_target(self, workspace: Path, target: str) -> None:
        path = workspace / "localization" / "text_records.json"
        records = json.loads(path.read_text(encoding="utf-8"))
        records[0]["original"] = target
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    def test_workspace_fingerprint_detects_legacy_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = self.make_workspace(Path(td))
            db_path = workspace / "vnext" / "project.sqlite3"
            ingest_workspace_records(workspace, db_path)
            self.assertTrue(workspace_sync_status(workspace, db_path)["synced"])
            self.mutate_target(workspace, "领取奖励")
            report = workspace_sync_status(workspace, db_path)
            self.assertFalse(report["synced"])
            self.assertEqual(report["state"], "stale")
            with self.assertRaises(ValueError):
                require_workspace_synced(workspace, db_path)

    def test_adopt_safe_legacy_translation_without_promoting_tm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = self.make_workspace(root)
            db_path = workspace / "vnext" / "project.sqlite3"
            knowledge = root / "knowledge.sqlite3"
            ingest_workspace_records(workspace, db_path)
            self.mutate_target(workspace, "领取奖励")
            report = adopt_legacy_targets(workspace, db_path, knowledge)
            self.assertEqual(report["stats"].get("adopted"), 1)
            self.assertTrue(report["sync"]["synced"])
            db = init_project_db(db_path)
            try:
                row = db.execute("SELECT * FROM current_targets").fetchone()
                self.assertEqual(row["target_text"], "领取奖励")
                self.assertEqual(row["target_status"], "legacy_candidate")
                self.assertEqual(row["origin"], "legacy:workspace")
                self.assertEqual(row["qa_status"], "passed")
            finally:
                db.close()
            import sqlite3
            kdb = sqlite3.connect(knowledge)
            try:
                self.assertEqual(kdb.execute("SELECT COUNT(*) FROM translation_memory").fetchone()[0], 0)
            finally:
                kdb.close()

    def test_adopt_rejects_mixed_unsafe_legacy_translation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = self.make_workspace(root, "领取 phần thưởng")
            report = adopt_legacy_targets(
                workspace,
                workspace / "vnext" / "project.sqlite3",
                root / "knowledge.sqlite3",
            )
            self.assertEqual(report["stats"].get("adopted", 0), 0)
            self.assertGreaterEqual(report["stats"].get("unsafe", 0), 1)

    def test_locked_vnext_target_survives_legacy_adoption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = self.make_workspace(root)
            db_path = workspace / "vnext" / "project.sqlite3"
            knowledge = root / "knowledge.sqlite3"
            ingest_workspace_records(workspace, db_path)
            db = init_project_db(db_path)
            try:
                unit = db.execute("SELECT unit_id FROM translation_units LIMIT 1").fetchone()[0]
                db.execute(
                    """INSERT INTO current_targets(
                         unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at
                       ) VALUES(?,?,'manual','review:manual','','passed',1,?)""",
                    (unit, "领取奖励", utcnow()),
                )
                db.commit()
            finally:
                db.close()
            self.mutate_target(workspace, "获得奖励")
            report = adopt_legacy_targets(workspace, db_path, knowledge, overwrite=True)
            self.assertEqual(report["stats"].get("adopted", 0), 0)
            self.assertGreaterEqual(report["stats"].get("kept_locked", 0), 1)
            db = init_project_db(db_path)
            try:
                row = db.execute("SELECT target_text,locked FROM current_targets").fetchone()
                self.assertEqual(row["target_text"], "领取奖励")
                self.assertEqual(row["locked"], 1)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
