from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


SYSTEM_PROMPT = """你是越南语 MMORPG 到简体中文的专业游戏本地化译员。
你的目标是自然、简洁、符合中国大陆玩家习惯的中文，而不是逐词替换。
规则：
1. 完整理解整句语义并按中文语序重写，不得输出“每 日 能 有”这类逐词拼接中文。
2. 输入可能是 UI 短语、对白、任务说明、人名、地名、技能、装备或中越混合旧文本。
3. terminology 中出现的术语必须采用给定中文；未出现的专名结合 context 自然意译或音译。
4. 输出自然语言不得残留越南语。NPC、PVP、PK、HP、MP、EXP、ID、UI、URL 等通用缩写可以保留。
5. 不添加解释、Markdown、引号或额外说明。
6. 只返回要求的 JSON 数组，id 必须与输入逐项一致。"""


@dataclass(frozen=True)
class OllamaConfig:
    model: str = "qwen3:14b"
    base_url: str = "http://127.0.0.1:11435"
    timeout: int = 600
    temperature: float = 0.1


class OllamaEngine:
    engine_name = "ollama"

    def __init__(self, config: OllamaConfig | None = None):
        self.config = config or OllamaConfig(
            model=os.environ.get("VNEXT_OLLAMA_MODEL", "qwen3:14b"),
            base_url=os.environ.get("VNEXT_OLLAMA_BASE", "http://127.0.0.1:11435"),
        )
        self.model = self.config.model
        self.prompt_hash = hashlib.sha256(
            ("vnext-phase2\0" + SYSTEM_PROMPT).encode("utf-8")
        ).hexdigest()

    def _request(self, messages: list[dict[str, str]], count: int) -> str:
        schema: dict[str, Any] = {
            "type": "array",
            "minItems": count,
            "maxItems": count,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": self.config.temperature},
        }
        req = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"无法连接 Ollama：{exc}") from exc
        return str((body.get("message") or {}).get("content") or "")

    def translate_batch(
        self,
        items: list[dict[str, Any]],
        terminology: list[dict[str, Any]],
    ) -> dict[str, str]:
        if not items:
            return {}
        expected = [str(item["id"]) for item in items]
        user_payload = {
            "terminology": [
                {"source": term["source"], "target": term["target"]}
                for term in terminology
            ],
            "items": [
                {
                    "id": str(item["id"]),
                    "source": str(item["source"]),
                    "context": item.get("context") or {},
                }
                for item in items
            ],
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "把 items 中每个 source 翻译成自然简体中文。"
                    "严格应用 terminology。返回与 items 等长的 JSON 数组："
                    '[{"id":"原id","text":"中文译文"}]。\n'
                    + json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        ]
        raw = self._request(messages, len(items)).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama 返回的不是合法 JSON：{raw[:500]}") from exc
        if not isinstance(parsed, list) or len(parsed) != len(items):
            raise RuntimeError(
                f"Ollama 返回数量错误：期望 {len(items)}，得到 "
                f"{len(parsed) if isinstance(parsed, list) else '非数组'}"
            )
        values: dict[str, str] = {}
        for row in parsed:
            if not isinstance(row, dict):
                raise RuntimeError("Ollama 返回数组包含非对象")
            rid = str(row.get("id") or "")
            text = str(row.get("text") or "").strip()
            if rid not in expected or rid in values:
                raise RuntimeError(f"Ollama 返回 id 异常：{rid}")
            values[rid] = text
        missing = [rid for rid in expected if rid not in values]
        if missing:
            raise RuntimeError(f"Ollama 缺少结果：{missing[:5]}")
        return values
