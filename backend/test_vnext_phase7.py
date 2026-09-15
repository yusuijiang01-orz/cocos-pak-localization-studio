#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from localization_analyzer import decode_best
from pak_builder import nrv2b_compress
from pak_core import extract_one
from vnext.build import build_verified_paks, preflight_build
from vnext.database import init_knowledge_db, init_project_db, utcnow
from vnext.workspace_ingest import ingest_workspace_records


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_single_entry_pak(path: Path, raw: bytes, hid: int) -> None:
    packed = nrv2b_compress(raw)
    offset = 32
    index_offset = offset + len(packed)
    header = bytearray(32)
    struct.pack_into('<4sIIII', header, 0, b'PACK', 1, index_offset, 0, 0)
    entry = bytearray()
    entry.extend(struct.pack('<III', hid, offset, len(raw)))
    entry.extend(len(packed).to_bytes(3, 'little'))
    entry.append(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + packed + bytes(entry))


class Phase7EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix='vnext_phase7_')
        self.root = Path(self.td.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir(parents=True)
        (self.workspace / 'localization').mkdir(parents=True)

        specs = [
            {
                'pak': 'settings.pak',
                'hid': 0x12345678,
                'raw': '[UI]\r\nText=Hoàn thành nhiệm vụ\r\n'.encode('utf-8'),
                'file': '0000_12345678.ini',
                'record': {
                    'id': 'phase7_settings_1', 'line': 2, 'column': 1, 'key': 'Text',
                    'source': 'Hoàn thành nhiệm vụ', 'target': '完成任务',
                },
            },
            {
                'pak': 'updatefs.pak',
                'hid': 0x23456789,
                'raw': 'id\tname\r\n1\tBăng Hỏa Long Châu\r\n'.encode('utf-8'),
                'file': '0000_23456789.tsv',
                'record': {
                    'id': 'phase7_updatefs_1', 'line': 2, 'column': 2, 'key': '',
                    'source': 'Băng Hỏa Long Châu', 'target': '冰火龙珠',
                },
            },
            {
                'pak': 'ui.pak',
                'hid': 0x3456789A,
                'raw': 'Nhận thưởng\r\n'.encode('utf-8'),
                'file': '0000_3456789A.txt',
                'record': {
                    'id': 'phase7_ui_1', 'line': 1, 'column': 1, 'key': '',
                    'source': 'Nhận thưởng', 'target': '领取奖励',
                },
            },
        ]

        project_paks = []
        records = []
        self.original_shas = {}
        self.specs = specs
        for spec in specs:
            original = self.root / spec['pak']
            make_single_entry_pak(original, spec['raw'], spec['hid'])
            self.original_shas[spec['pak']] = sha256(original)
            extracted = self.root / f"{Path(spec['pak']).stem}_unpacked"
            out, count, ok, fail, methods, _types = extract_one(original, extracted, workers=1)
            self.assertEqual(out, extracted)
            self.assertEqual((count, ok, fail), (1, 1, 0))
            self.assertEqual(methods, {1: 1})
            self.assertTrue((extracted / spec['file']).is_file())
            project_paks.append({'pak': spec['pak'], 'path': str(original), 'extracted': str(extracted)})
            rec = spec['record']
            records.append(
                {
                    'id': rec['id'],
                    'pak': spec['pak'],
                    'source_file': spec['file'],
                    'line': rec['line'],
                    'column': rec['column'],
                    'key': rec['key'],
                    'encoding': 'utf-8',
                    'original': rec['source'],
                    'source_original': rec['source'],
                    '_isPlayerVisible': True,
                }
            )

        (self.workspace / 'localization' / 'text_records.json').write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        (self.workspace / 'project.json').write_text(
            json.dumps({'workspace': str(self.workspace), 'paks': project_paks}, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        self.project_db = self.workspace / 'vnext' / 'project.sqlite3'
        self.knowledge_db = self.root / 'knowledge.sqlite3'
        init_knowledge_db(self.knowledge_db).close()
        report = ingest_workspace_records(self.workspace, self.project_db, project_name='phase7 synthetic')
        self.assertEqual(report['active_unique_units'], 3)
        self.assertEqual(report['active_occurrences'], 3)

    def tearDown(self):
        self.td.cleanup()

    def _set_target_for_source(self, source: str, target: str) -> str:
        db = init_project_db(self.project_db)
        try:
            row = db.execute('SELECT unit_id FROM translation_units WHERE source_text=?', (source,)).fetchone()
            self.assertIsNotNone(row, source)
            unit_id = row['unit_id']
            db.execute(
                """INSERT OR REPLACE INTO current_targets(
                     unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at
                   ) VALUES(?,?,'manual','phase7:test','','passed',1,?)""",
                (unit_id, target, utcnow()),
            )
            db.commit()
            return unit_id
        finally:
            db.close()

    def _set_all_safe_targets(self) -> None:
        for spec in self.specs:
            rec = spec['record']
            self._set_target_for_source(rec['source'], rec['target'])

    def test_three_pak_materialize_rebuild_reextract_roundtrip(self):
        self._set_all_safe_targets()
        gate = preflight_build(
            self.project_db,
            self.knowledge_db,
            require_translated=True,
            fail_on_warnings=True,
        )
        self.assertTrue(gate['ok'], gate)
        self.assertEqual(gate['translated_unique'], 3)

        output_dir = self.workspace / 'build-vnext'
        report = build_verified_paks(
            self.workspace,
            project_db_path=self.project_db,
            knowledge_db_path=self.knowledge_db,
            output_dir=output_dir,
            pak_names=['settings.pak', 'updatefs.pak', 'ui.pak'],
            workers=1,
            require_translated=True,
            fail_on_warnings=True,
        )
        self.assertTrue(report['ok'], report)

        for spec in self.specs:
            original = self.root / spec['pak']
            built = output_dir / spec['pak']
            self.assertTrue(built.is_file(), spec['pak'])
            self.assertEqual(sha256(original), self.original_shas[spec['pak']], 'original PAK must remain byte-identical')
            self.assertNotEqual(sha256(built), self.original_shas[spec['pak']], 'translated candidate must differ from original')
            verify_dir = self.root / f"verify_{Path(spec['pak']).stem}"
            _out, count, ok, fail, _methods, _types = extract_one(built, verify_dir, workers=1)
            self.assertEqual((count, ok, fail), (1, 1, 0))
            rebuilt_resource = (verify_dir / spec['file']).read_bytes()
            decoded = decode_best(rebuilt_resource)[0]
            self.assertIn(spec['record']['target'], decoded)
            self.assertNotIn(spec['record']['source'], decoded)

        db = init_project_db(self.project_db)
        try:
            snap = db.execute('SELECT status,verification_json FROM build_snapshots ORDER BY created_at DESC LIMIT 1').fetchone()
            self.assertIsNotNone(snap)
            self.assertEqual(snap['status'], 'verified')
            verification = json.loads(snap['verification_json'])
            self.assertTrue(verification['preflight']['ok'])
            statuses = {item['pak']: item['status'] for item in verification['paks']}
            self.assertEqual(statuses, {'settings.pak': 'verified', 'updatefs.pak': 'verified', 'ui.pak': 'verified'})
        finally:
            db.close()

    def test_final_gate_blocks_structure_damaging_target(self):
        self._set_all_safe_targets()
        self._set_target_for_source('Hoàn thành nhiệm vụ', '完成{P1}任务')
        gate = preflight_build(
            self.project_db,
            self.knowledge_db,
            require_translated=True,
            fail_on_warnings=True,
        )
        self.assertFalse(gate['ok'])
        self.assertGreater(gate['blocker_count'], 0)


if __name__ == '__main__':
    unittest.main()
