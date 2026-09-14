from pathlib import Path
import json
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pak_builder import rebuild_pak


def main():
    pak_root = ROOT / "pak"
    workspace = pak_root / "new"
    staging = workspace / "recovery" / "visible_tsv_salvage"
    safe_names = sorted(path.name for path in staging.glob("*.tsv"))

    original = pak_root / "v587+" / "build_no_tsv_test" / "updatefs.pak"
    output = workspace / "build" / "updatefs.cell-safe-salvage.pak"
    info = rebuild_pak(original, staging, safe_names, output)
    print(f"output={output}")
    print(f"safe_tsv={len(safe_names)}")
    print(f"entries={info['entries']}")
    print(f"changed_entries={info['changed_entries']}")
    print(f"archive_size={info['archive_size']}")


if __name__ == "__main__":
    main()
