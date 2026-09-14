import csv, json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from merged_csv import merge_csv_tree, split_merged_csv


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "text", "placeholders"], lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def test_merge_and_restore_ids():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); source = root / "before"; source.mkdir()
        write_csv(source / "1661_EE5FAD05.csv", [{"id":"ec0530147d0fa78f","text":"原文一","placeholders":""},{"id":"abc","text":"原文二","placeholders":"%s"}])
        write_csv(source / "1662.csv", [{"id":"def","text":"原文三","placeholders":""}])
        merged = root / "merged.csv"; mapping = root / "mapping.json"; output = root / "after"
        report = merge_csv_tree(source, merged, mapping)
        assert report["rows"] == 3
        with merged.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [row["id"] for row in rows] == ["1", "2", "3"]
        assert json.loads(mapping.read_text(encoding="utf-8"))["mapping"][0]["original_id"] == "ec0530147d0fa78f"
        rows[0]["text"] = "翻译一"
        with merged.open("w", encoding="utf-8-sig", newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=["id","text","placeholders"],lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
        split_merged_csv(merged, mapping, output)
        with (output / "1661_EE5FAD05.csv").open(encoding="utf-8-sig", newline="") as stream:
            restored = list(csv.DictReader(stream))
        assert restored[0]["id"] == "ec0530147d0fa78f" and restored[0]["text"] == "翻译一"
        assert restored[1]["id"] == "abc"


def test_sparse_merge_only_patches_untranslated_rows():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); source=root/"after"; source.mkdir()
        write_csv(source/"part.csv",[{"id":"zh","text":"已经翻译","placeholders":""},{"id":"vi","text":"Nhận nhiệm vụ","placeholders":""}])
        merged=root/"sparse.csv"; mapping=root/"sparse.json"
        report=merge_csv_tree(source,merged,mapping,{"part.csv"},untranslated_only=True)
        assert report["rows"]==1 and report["mode"]=="sparse"
        with merged.open(encoding="utf-8-sig",newline="") as stream:
            rows=list(csv.DictReader(stream))
        assert rows[0]["id"]=="1" and rows[0]["text"]=="Nhận nhiệm vụ"
        rows[0]["text"]="领取任务"
        with merged.open("w",encoding="utf-8-sig",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=["id","text","placeholders"],lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
        split_merged_csv(merged,mapping,source)
        with (source/"part.csv").open(encoding="utf-8-sig",newline="") as stream:
            restored=list(csv.DictReader(stream))
        assert restored[0]["text"]=="已经翻译" and restored[1]["id"]=="vi" and restored[1]["text"]=="领取任务"


if __name__ == "__main__":
    test_merge_and_restore_ids(); test_sparse_merge_only_patches_untranslated_rows(); print("OK")
