from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pak_builder import rebuild_pak


def main():
    pak_root = ROOT / "pak"
    workspace = pak_root / "new"
    official = pak_root / "v587+"
    modified = workspace / "modified" / "updatefs"
    changed_files = [
        path.name
        for path in sorted(modified.iterdir())
        if path.is_file()
        and path.suffix.lower() in {".lua", ".ini", ".txt"}
        and re.match(r"^\d+_[0-9A-Fa-f]+\.", path.name)
    ]
    output = workspace / "build" / "updatefs.all-official-tsv.pak"
    info = rebuild_pak(
        official / "original_paks" / "updatefs.pak",
        modified,
        changed_files,
        output,
    )
    print(f"output={output}")
    print(f"entries={info['entries']}")
    print(f"changed_entries={info['changed_entries']}")
    print(f"archive_size={info['archive_size']}")


if __name__ == "__main__":
    main()
