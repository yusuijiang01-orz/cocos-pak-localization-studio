import struct
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from pak_builder import _read_index, nrv2b_compress, rebuild_pak
from pak_core import ENTRY_SIZE, HEADER_SIZE, nrv2b


def _make_pak(path: Path):
    raw_a = b'unchanged-map-or-sprite-data' * 8
    raw_b = b'old localized text' * 12
    packed_a = nrv2b_compress(raw_a)
    packed_b = nrv2b_compress(raw_b)
    blob = bytearray(HEADER_SIZE)
    blob[:4] = b'PACK'
    off_a = len(blob)
    blob.extend(packed_a)
    gap = b'PHYSICAL-GAP-MUST-STAY'
    blob.extend(gap)
    off_b = len(blob)
    blob.extend(packed_b)
    idx = len(blob)
    entries = [
        (0x11111111, off_a, len(raw_a), len(packed_a), 1),
        # Alias the same untouched payload to exercise duplicate index entries.
        (0x11111111, off_a, len(raw_a), len(packed_a), 1),
        (0x22222222, off_b, len(raw_b), len(packed_b), 1),
    ]
    for hid, off, real, packed, method in entries:
        blob.extend(struct.pack('<III', hid, off, real))
        blob.extend(packed.to_bytes(3, 'little'))
        blob.append(method)
    struct.pack_into('<I', blob, 4, len(entries))
    struct.pack_into('<I', blob, 8, idx)
    path.write_bytes(blob)
    return raw_a, raw_b, gap


def test_rebuild_is_compact_and_preserves_untouched_payloads(tmp_path):
    source = tmp_path / 'source.pak'
    raw_a, _raw_b, gap = _make_pak(source)
    modified = tmp_path / 'modified'
    modified.mkdir()
    replacement = ('任务属性已经安全汉化。' * 10).encode('utf-8')
    (modified / '0002_22222222.ini').write_bytes(replacement)
    output = tmp_path / 'rebuilt.pak'

    report = rebuild_pak(source, modified, ['0002_22222222.ini'], output)
    source_blob = source.read_bytes()
    rebuilt = output.read_bytes()
    count, idx, entries = _read_index(rebuilt)

    assert report['rebuild_mode'] == 'compact-physical-order-v1'
    assert count == 3
    assert len(rebuilt) == idx + count * ENTRY_SIZE
    assert rebuilt[entries[0]['offset']:entries[0]['offset'] + entries[0]['packed']] == source_blob[HEADER_SIZE:HEADER_SIZE + entries[0]['packed']]
    assert entries[0]['offset'] == entries[1]['offset']
    assert nrv2b(rebuilt[entries[0]['offset']:entries[0]['offset'] + entries[0]['packed']], entries[0]['real']) == raw_a
    assert nrv2b(rebuilt[entries[2]['offset']:entries[2]['offset'] + entries[2]['packed']], entries[2]['real']) == replacement
    assert gap in rebuilt[:idx]
    # The former append-style algorithm was always source + changed payload + index.
    assert len(rebuilt) < len(source_blob) + len(nrv2b_compress(replacement)) + count * ENTRY_SIZE


def test_rebuild_refuses_to_relabel_an_unsupported_compression_method(tmp_path):
    source = tmp_path / 'source.pak'
    _make_pak(source)
    blob = bytearray(source.read_bytes())
    _count, idx, _entries = _read_index(blob)
    # Third entry starts at index + 2 * 16; change only its method marker.
    blob[idx + 2 * ENTRY_SIZE + 15] = 17
    source.write_bytes(blob)
    modified = tmp_path / 'modified'
    modified.mkdir()
    (modified / '0002_22222222.ini').write_bytes('安全文本'.encode('utf-8'))

    with pytest.raises(ValueError, match='compression method 17'):
        rebuild_pak(source, modified, ['0002_22222222.ini'], tmp_path / 'rebuilt.pak')
