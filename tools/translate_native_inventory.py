import argparse
import json
import re
import time
import urllib.request
from pathlib import Path


SKIP_RE = re.compile(r"(?i)(?:^|[/\\])ui[/\\]|\.(?:png|jpg|plist|txt|lua|json)$|^[A-Za-z_][A-Za-z0-9_]*(?:\|\d+)?$")
HAS_CJK = re.compile(r"[\u3400-\u9fff]")


def request_translation(url: str, model: str, rows: list[dict]) -> dict[str, str]:
    prompt = (
        "你是资深越南语游戏本地化译员。把输入数组中的越南语 MMORPG 界面文本翻译成自然、精简、统一的简体中文。"
        "保留所有占位符、数字、换行、标点和格式代码；术语：thuộc tính=属性，sát thương=伤害，"
        "bạo kích/b. kích=暴击，phòng thủ/phòng ngự=防御，kháng=抗性，công lực=战力，"
        "nội lực=内力，sinh lực=生命，lãnh địa=领地，trang bị=装备，nhiệm vụ=任务。"
        "只返回完整 JSON 数组，每项格式为 {\"id\":\"原id\",\"text\":\"中文\"}，不得遗漏。\n输入："
        + json.dumps(rows, ensure_ascii=False)
    )
    body = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result["message"]["content"].strip()
    start, end = content.find("["), content.rfind("]")
    if start < 0 or end < start:
        raise ValueError("response does not contain a JSON array")
    parsed = json.loads(content[start : end + 1])
    return {str(item["id"]): str(item["text"]) for item in parsed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://localhost:11435")
    parser.add_argument("--model", default="gemma4:latest")
    parser.add_argument("--batch", type=int, default=12)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))["strings"]
    candidates = [row for row in inventory if not SKIP_RE.search(row["text"])]
    saved = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    pending = [row for row in candidates if row["text"] not in saved]
    for index in range(0, len(pending), args.batch):
        chunk = pending[index : index + args.batch]
        request_rows = [{"id": str(i), "text": row["text"]} for i, row in enumerate(chunk)]
        for attempt in range(3):
            try:
                translated = request_translation(args.url, args.model, request_rows)
                if len(translated) != len(chunk):
                    raise ValueError(f"expected {len(chunk)} rows, got {len(translated)}")
                for i, row in enumerate(chunk):
                    value = translated[str(i)].strip()
                    if not HAS_CJK.search(value):
                        raise ValueError(f"translation is not Chinese: {row['text']!r} -> {value!r}")
                    saved[row["text"]] = value
                args.output.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"translated {min(index + len(chunk), len(pending))}/{len(pending)}", flush=True)
                break
            except Exception as exc:
                if attempt == 2:
                    raise
                print(f"retry {attempt + 1}: {exc}", flush=True)
                time.sleep(2)
    print(json.dumps({"candidates": len(candidates), "translated": len(saved)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
