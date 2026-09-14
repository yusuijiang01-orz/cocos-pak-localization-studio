from __future__ import annotations

import collections
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from localization_tm import validate_tokens
from ollama_batch_translate import (
    OLLAMA_BASE,
    load_translator_profile,
    ollama_chat,
    parse_json_array_block,
)
from tsv_localization import validate_translation


_CJK = re.compile(r"[\u3400-\u9fff]")
_VIETNAMESE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạắằẳẵặấầẩẫậèéẻẽẹếềểễệìíỉĩịòóỏõọốồổỗộớờởỡợùúủũụứừửữựỳỷỹýỵ]"
)
_MARKUP = re.compile(r"<[^>]+>|◈\d+◈|#d\d*|%[-+0-9.]*[a-zA-Z]")
_PUNCT = re.compile(r"[\s#，。！？：:；;、（）()\[\]{}<>《》\-—·*'\"]+")


def normalize_semantic_text(text: str) -> str:
    text = _MARKUP.sub("", str(text or ""))
    return _PUNCT.sub("", text).lower()


class PcSemanticIndex:
    """Chinese content index used only after deterministic migration misses."""

    def __init__(self, records: list[dict]):
        values: dict[str, set[str]] = collections.defaultdict(set)
        for record in records:
            target = str(record.get("original", ""))
            normalized = normalize_semantic_text(target)
            if len(normalized) >= 3 and _CJK.search(normalized):
                values[normalized].add(target)
        self.values = dict(values)
        self.grams: dict[str, set[str]] = collections.defaultdict(set)
        for normalized in self.values:
            for gram in self._grams(normalized):
                self.grams[gram].add(normalized)

    @staticmethod
    def _grams(text: str) -> set[str]:
        width = 3 if len(text) >= 6 else 2
        return {text[i:i + width] for i in range(max(0, len(text) - width + 1))}

    def _pick_value(self, normalized: str, source: str) -> str | None:
        values = self.values[normalized]
        marker = source[:1] if source[:1] in "$#=" else ""
        same_marker = [value for value in values
                       if (value[:1] if value[:1] in "$#=" else "") == marker]
        if len(same_marker) == 1:
            return same_marker[0]
        return next(iter(values)) if len(values) == 1 else None

    def match(self, query: str, source: str | None = None) -> tuple[str, float] | None:
        source = str(source if source is not None else query)
        normalized = normalize_semantic_text(query)
        if len(normalized) < 3:
            return None
        exact = self.values.get(normalized)
        if exact:
            target = self._pick_value(normalized, source)
            return (target, 1.0) if target else None

        query_grams = self._grams(normalized)
        pool: set[str] = set()
        for gram in query_grams:
            pool.update(self.grams.get(gram, ()))
        if not pool:
            return None

        ranked = []
        for candidate in pool:
            candidate_grams = self._grams(candidate)
            overlap = len(query_grams & candidate_grams) / max(1, len(query_grams | candidate_grams))
            ratio = SequenceMatcher(None, normalized, candidate).ratio()
            containment = min(len(normalized), len(candidate)) / max(len(normalized), len(candidate)) \
                if normalized in candidate or candidate in normalized else 0.0
            score = max(ratio, overlap * 0.72 + ratio * 0.28, containment)
            ranked.append((score, candidate))
        ranked.sort(reverse=True)
        best_score, best = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        threshold = 0.92 if len(normalized) < 7 else (0.86 if len(normalized) < 14 else 0.82)
        if best_score < threshold or best_score - second_score < 0.06:
            return None
        target = self._pick_value(best, source)
        return (target, round(best_score, 4)) if target else None


def _load_cache(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.get("translations", {}).items() if str(v).strip()}
    except (OSError, ValueError, TypeError):
        return {}


def _save_cache(path: Path, translations: dict[str, str], model: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps({"version": 1, "model": model, "translations": translations},
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def translate_queries(sources: list[str], cache_path: Path, progress=None, batch_size: int = 25):
    profile = load_translator_profile()
    model = str(profile.get("model") or "qwen3:14b")
    base = re.sub(r"/v1/?$", "", str(profile.get("baseUrl") or OLLAMA_BASE)).rstrip("/")
    cache = _load_cache(cache_path)
    pending = [source for source in dict.fromkeys(sources) if source not in cache]
    system = (
        "你是中国网络游戏《封神》的原文检索助手。输入是由简体中文翻译而来的越南文。"
        "请还原最可能的简体中文原文；保留全部标签、占位符、数字和前缀。只返回要求的 JSON。"
    )
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        rows = [{"id": str(i), "text": text} for i, text in enumerate(batch)]
        prompt = (
            "/no_think\n将以下越南文还原为最可能的简体中文游戏原文。"
            "只输出 JSON 数组，每项仅含 id 和 text：\n" + json.dumps(rows, ensure_ascii=False)
        )
        reply = ollama_chat([{"role": "system", "content": system},
                             {"role": "user", "content": prompt}], model=model, base=base)
        translated = {item["id"]: item["text"] for item in parse_json_array_block(reply)}
        for index, source in enumerate(batch):
            target = str(translated.get(str(index), "")).strip()
            if target and _CJK.search(target):
                cache[source] = target
        _save_cache(cache_path, cache, model)
        if progress:
            done = min(start + len(batch), len(pending))
            progress({"done": done, "total": len(pending), "message": f"语义回译 {done:,}/{len(pending):,}"})
    return cache, {"model": model, "base": base, "cached": len(sources) - len(pending), "translated": len(pending)}


def semantic_candidates(pc_records: list[dict], mobile_records: list[dict], already_selected: set[str],
                        cache_path: Path, progress=None, pak_scope: str = ""):
    eligible = []
    for record in mobile_records:
        source = str(record.get("source_original", record.get("original", "")))
        if record.get("id") in already_selected or (pak_scope and record.get("pak") != pak_scope):
            continue
        # The semantic pass is a Vietnamese recovery pass. Sending already clean
        # Chinese/internal identifiers to the model would waste hours and could
        # replace valid user work.
        if record.get("language") not in ("vi", "mixed") and not _VIETNAMESE.search(source):
            continue
        if len(normalize_semantic_text(source)) < 3:
            continue
        eligible.append(record)
    sources = [str(r.get("source_original", r.get("original", ""))) for r in eligible]
    sources = [s for s in sources if s.strip()]
    cache, model_report = translate_queries(sources, cache_path, progress=progress)
    index = PcSemanticIndex(pc_records)
    selected = {}
    rejected = collections.Counter()
    for record in eligible:
        source = str(record.get("source_original", record.get("original", "")))
        query = cache.get(source)
        if not query:
            rejected["no_back_translation"] += 1
            continue
        match = index.match(query, source)
        if not match:
            rejected["no_unique_high_confidence_match"] += 1
            continue
        target, score = match
        if not validate_translation(source, target)[0] or not validate_tokens(source, target)[0]:
            # The PC table often stores a bare name while mobile wraps the same
            # name in color/runtime tags. The model query is allowed here only
            # when its natural-language body exactly equals the indexed PC body
            # and it preserves the complete mobile token structure.
            if (normalize_semantic_text(query) == normalize_semantic_text(target)
                    and validate_translation(source, query)[0]
                    and validate_tokens(source, query)[0]):
                target = query
            else:
                rejected["structure_or_token_mismatch"] += 1
                continue
        selected[str(record["id"])] = ("semantic_pc_content", target, score, query)
    return selected, {**model_report, "eligible": len(eligible), "matched": len(selected),
                      "rejected": dict(rejected), "cache": str(cache_path.resolve())}
