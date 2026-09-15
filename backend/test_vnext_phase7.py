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


def make_single_entry_pak(path: Path, raw: bytes, hid: int = 0x12345678) -> None:
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
        self.original = self.root / 'settings.pak'
        self.raw = '[UI]\r\nText=Hoàn thành nhiệm vụ\r\n'.encode('utf-8')
        make_single_entry_pak(self.original, self.raw)
        self.original_sha = sha256(self.original)

        self.extracted = self.root / 'settings_unpacked'
        out, count, ok, fail, methods, types = extract_one(self.original, self.extracted, workers=1)
        self.assertEqual(out, self.extracted)
        self.assertEqual((count, ok, fail), (1, 1, 0))
        self.assertEqual(methods, {1: 1})
        self.assertTrue((self.extracted / '0000_12345678.ini').is_file())

        (self.workspace / 'localization').mkdir(parents=True)
        records = [
            {
                'id': 'phase7_record_1',
                'pak': 'settings.pak',
                'source_file': '0000_12345678.ini',
                'line': 2,
                'column': 1,
                'key': 'Text',
                'encoding': 'utf-8',
                'original': 'Hoàn thành nhiệm vụ',
                'source_original': 'Hoàn thành nhiệm vụ',
                '_isPlayerVisible': True,
            }
        ]
        (self.workspace / 'localization' / 'text_records.json').write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        (self.workspace / 'project.json').write_text(
            json.dumps(
                {
                    'workspace': str(self.workspace),
                    'paks': [
                        {
                            'pak': 'settings.pak',
                            'path': str(self.original),
                            'extracted': str(self.extracted),
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        self.project_db = self.workspace / 'vnext' / 'project.sqlite3'
        self.knowledge_db = self.root / 'knowledge.sqlite3'
        init_knowledge_db(self.knowledge_db).close()
        report = ingest_workspace_records(self.workspace, self.project_db, project_name='phase7 synthetic')
        self.assertEqual(report['active_unique_units'], 1)
        self.assertEqual(report['active_occurrences'], 1)

    def tearDown(self):
        self.td.cleanup()

    def _set_safe_target(self, target: str = '完成任务') -> str:
        db = init_project_db(self.project_db)
        try:
            row = db.execute('SELECT unit_id FROM translation_units LIMIT 1').fetchone()
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

    def test_full_vnext_materialize_rebuild_reextract_roundtrip(self):
        self._set_safe_target('完成任务')
        gate = preflight_build(
            self.project_db,
            self.knowledge_db,
            require_translated=True,
            fail_on_warnings=True,
        )
        self.assertTrue(gate['ok'], gate)

        output_dir = self.workspace / 'build-vnext'
        report = build_verified_paks(
            self.workspace,
            project_db_path=self.project_db,
            knowledge_db_path=self.knowledge_db,
            output_dir=output_dir,
            pak_names=['settings.pak'],
            workers=1,
            require_translated=True,
            fail_on_warnings=True,
        )
        self.assertTrue(report['ok'], report)
        built = output_dir / 'settings.pak'
        self.assertTrue(built.is_file())
        self.assertEqual(sha256(self.original), self.original_sha, 'original PAK must remain byte-identical')
        self.assertNotEqual(sha256(built), self.original_sha, 'translated candidate must differ from original')

        verify_dir = self.root / 'verify-final'
        _out, count, ok, fail, _methods, _types = extract_one(built, verify_dir, workers=1)
        self.assertEqual((count, ok, fail), (1, 1, 0))
        rebuilt_resource = (verify_dir / '0000_12345678.ini').read_bytes()
        decoded = decode_best(rebuilt_resource)[0]
        self.assertIn('Text=', decoded)
        self.assertIn('完成任务', decoded)
        self.assertNotIn('Hoàn thành nhiệm vụ', decoded)

        db = init_project_db(self.project_db)
        try:
            snap = db.execute('SELECT status,verification_json FROM build_snapshots ORDER BY created_at DESC LIMIT 1').fetchone()
            self.assertIsNotNone(snap)
            self.assertEqual(snap['status'], 'verified')
            verification = json.loads(snap['verification_json'])
            self.assertTrue(verification['preflight']['ok'])
        finally:
            db.close()

    def test_final_gate_blocks_structure_damaging_target(self):
        self._set_safe_target('完成{P1}任务')
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
