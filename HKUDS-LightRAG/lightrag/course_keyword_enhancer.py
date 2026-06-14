import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache, partial
from pathlib import Path
from typing import Any

import json_repair

from lightrag.utils import logger, remove_think_tags


@lru_cache(maxsize=1)
def _dotenv_values() -> dict[str, str]:
    env_path = Path(os.getenv("LIGHTRAG_ENV_FILE", "/app/.env"))
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    try:
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception as exc:
        logger.warning("Failed to read LightRAG env file %s: %s", env_path, exc)
    return values


def _config_value(name: str, default: str | None = None) -> str | None:
    return os.getenv(name) or _dotenv_values().get(name) or default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _config_value(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = _config_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r, using %s", name, value, default)
        return default


def _env_float(name: str, default: float) -> float:
    value = _config_value(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%r, using %s", name, value, default)
        return default


def _display_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("*", "")).strip()


def _clean_text(text: str) -> str:
    text = str(text or "").strip().replace("*", "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text.lower()


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    text = _clean_text(text)
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _unique_keep_order(items: list[str], limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _display_text(item)
        key = _clean_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


@dataclass
class OutlineNode:
    course: str
    node_id: str
    text: str
    path: list[str]
    children: list[str] = field(default_factory=list)
    siblings: list[str] = field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def path_text(self) -> str:
        return " > ".join(self.path)


def _flatten_outline(path: Path) -> list[OutlineNode]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    root = raw.get("root")
    if not root:
        return []

    nodes: list[OutlineNode] = []

    def walk(node: dict[str, Any], path_parts: list[str], sibling_texts: list[str]) -> None:
        data = node.get("data") or {}
        text = _display_text(data.get("text", ""))
        if not text:
            return

        current_path = path_parts + [text]
        children = node.get("children") or []
        child_texts = [
            _display_text((child.get("data") or {}).get("text", ""))
            for child in children
        ]
        child_texts = [item for item in child_texts if item]

        nodes.append(
            OutlineNode(
                course=current_path[0],
                node_id=str(data.get("id", "")),
                text=text,
                path=current_path,
                children=child_texts,
                siblings=[item for item in sibling_texts if item and item != text],
            )
        )

        for child in children:
            walk(child, current_path, child_texts)

    walk(root, [], [])
    return nodes


def _split_terms(
    query: str,
    hl_keywords: list[str],
    ll_keywords: list[str],
) -> list[str]:
    terms = [_display_text(query), *hl_keywords, *ll_keywords]
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_+\-]*|[\u4e00-\u9fff]{2,}", query):
        terms.append(_display_text(token))
    return _unique_keep_order(terms)


def _score_node(node: OutlineNode, terms: list[str]) -> tuple[float, list[str]]:
    node_text = _clean_text(node.text)
    path_text = _clean_text(node.path_text)
    score = 0.0
    reasons: list[str] = []

    for term in terms:
        term_text = _clean_text(term)
        if not term_text:
            continue

        if term_text == node_text:
            score += 120
            reasons.append(f"exact:{term}")
        elif term_text in node_text:
            score += 70
            reasons.append(f"text_match:{term}")
        elif len(node_text) >= 4 and node_text in term_text:
            score += 55
            reasons.append(f"text_match:{term}")
            if term_text.startswith(node_text):
                score += 25
                reasons.append(f"query_prefix_match:{term}")
        elif len(node_text) >= 2 and node_text in term_text:
            score += 6
            reasons.append(f"weak_short_text_match:{term}")
        elif term_text in path_text:
            score += 35
            reasons.append(f"path_match:{term}")

        text_sim = _jaccard(_char_ngrams(term_text), _char_ngrams(node_text))
        path_sim = _jaccard(_char_ngrams(term_text), _char_ngrams(path_text))
        if text_sim >= 0.25:
            score += text_sim * 30
            reasons.append(f"text_sim:{term}:{text_sim:.2f}")
        if path_sim >= 0.18:
            score += path_sim * 18
            reasons.append(f"path_sim:{term}:{path_sim:.2f}")

    if score > 0:
        score += min(len(node.path), 8) * 0.8
    return score, reasons[:8]


class CourseKeywordEnhancer:
    def __init__(self) -> None:
        self.enabled = _env_bool("ENABLE_COURSE_KEYWORD_ENHANCER", False)
        self.selector = (_config_value("COURSE_OUTLINE_SELECTOR", "llm") or "llm").lower()
        self.candidate_k = _env_int("COURSE_OUTLINE_CANDIDATE_K", 20)
        self.selected_k = _env_int("COURSE_OUTLINE_SELECTED_K", 3)
        self.min_score_ratio = _env_float("COURSE_OUTLINE_MIN_SCORE_RATIO", 0.12)
        files = _config_value(
            "COURSE_OUTLINE_FILES",
            "/app/course_outlines/计算机系统基础1.json,/app/course_outlines/计算机系统基础2.json",
        )
        self.outline_paths = [Path(item.strip()) for item in files.split(",") if item.strip()]
        self.nodes: list[OutlineNode] = []
        if self.enabled:
            self.nodes = self._load_nodes()
            logger.info(
                "Course keyword enhancer enabled: selector=%s, files=%s, nodes=%d",
                self.selector,
                [str(path) for path in self.outline_paths],
                len(self.nodes),
            )

    def _load_nodes(self) -> list[OutlineNode]:
        nodes: list[OutlineNode] = []
        for path in self.outline_paths:
            if not path.exists():
                logger.warning("Course outline file not found: %s", path)
                continue
            try:
                nodes.extend(_flatten_outline(path))
            except Exception as exc:
                logger.warning("Failed to load course outline %s: %s", path, exc)
        return nodes

    async def enhance(
        self,
        query: str,
        hl_keywords: list[str],
        ll_keywords: list[str],
        query_param: Any,
        global_config: dict[str, Any],
    ) -> tuple[list[str], list[str], dict[str, Any] | None]:
        if not self.enabled or not self.nodes:
            return hl_keywords, ll_keywords, None

        candidates = self._recall_candidates(query, hl_keywords, ll_keywords)
        if not candidates:
            return hl_keywords, ll_keywords, None

        selected = candidates[:1]
        selector_status = "lexical_fallback"
        selector_error = None
        if self.selector == "llm":
            try:
                selected = await self._select_with_llm(
                    query, hl_keywords, ll_keywords, candidates, query_param, global_config
                )
                selector_status = "llm"
            except Exception as exc:
                selector_error = str(exc)
                logger.warning("Course outline LLM selector failed, fallback to lexical: %s", exc)
        elif self.selector == "lexical":
            selected = self._select_with_lexical(candidates)
            selector_status = "lexical"

        selected = selected[: self.selected_k] or candidates[:1]
        course_keywords = self._build_course_keywords(selected)
        final_hl = _unique_keep_order(
            [*hl_keywords, *course_keywords["high_level"]], limit=24
        )
        final_ll = _unique_keep_order(
            [*ll_keywords, *course_keywords["low_level"]], limit=36
        )

        metadata = {
            "enabled": True,
            "selector": self.selector,
            "selector_status": selector_status,
            "selector_error": selector_error,
            "original_keywords": {
                "high_level": hl_keywords,
                "low_level": ll_keywords,
            },
            "course_nodes": [self._node_to_dict(node) for node in selected],
            "course_keywords": course_keywords,
            "final_keywords": {
                "high_level": final_hl,
                "low_level": final_ll,
            },
            "candidate_nodes": [self._node_to_dict(node) for node in candidates[:10]],
        }
        return final_hl, final_ll, metadata

    def _recall_candidates(
        self, query: str, hl_keywords: list[str], ll_keywords: list[str]
    ) -> list[OutlineNode]:
        terms = _split_terms(query, hl_keywords, ll_keywords)
        scored: list[OutlineNode] = []
        for node in self.nodes:
            score, reasons = _score_node(node, terms)
            if score <= 0:
                continue
            scored.append(
                OutlineNode(
                    course=node.course,
                    node_id=node.node_id,
                    text=node.text,
                    path=node.path,
                    children=node.children,
                    siblings=node.siblings,
                    score=round(score, 4),
                    reasons=reasons,
                )
            )
        scored.sort(key=lambda item: (-item.score, -len(item.path), item.path_text))
        return scored[: self.candidate_k]

    def _select_with_lexical(self, candidates: list[OutlineNode]) -> list[OutlineNode]:
        if not candidates:
            return []
        threshold = candidates[0].score * self.min_score_ratio
        return [node for node in candidates if node.score >= threshold][: self.selected_k]

    async def _select_with_llm(
        self,
        query: str,
        hl_keywords: list[str],
        ll_keywords: list[str],
        candidates: list[OutlineNode],
        query_param: Any,
        global_config: dict[str, Any],
    ) -> list[OutlineNode]:
        if query_param.model_func:
            use_model_func = query_param.model_func
        else:
            use_model_func = partial(global_config["llm_model_func"], _priority=5)

        candidate_lines = [
            f"{index}. {node.path_text}" for index, node in enumerate(candidates, 1)
        ]
        prompt = (
            "你是计算机系统课程知识点路由器。请根据用户问题和 LightRAG 已抽取的关键词，"
            "从候选课程知识路径中选择最适合辅助检索的 1 到 "
            f"{self.selected_k} 个路径。只返回合法 JSON，不要解释。\n\n"
            "返回格式：{\"selected_indices\": [1, 2], \"reason\": \"简短原因\"}\n\n"
            f"用户问题：{query}\n"
            f"high_level_keywords：{json.dumps(hl_keywords, ensure_ascii=False)}\n"
            f"low_level_keywords：{json.dumps(ll_keywords, ensure_ascii=False)}\n\n"
            "候选课程路径：\n"
            + "\n".join(candidate_lines)
        )
        result = await use_model_func(prompt, keyword_extraction=True)
        result = remove_think_tags(result)
        parsed = json_repair.loads(result)
        selected_indices = parsed.get("selected_indices", [])

        selected: list[OutlineNode] = []
        for item in selected_indices:
            try:
                index = int(item) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(candidates):
                selected.append(candidates[index])
            if len(selected) >= self.selected_k:
                break
        return selected or candidates[:1]

    def _build_course_keywords(self, selected: list[OutlineNode]) -> dict[str, list[str]]:
        high: list[str] = []
        low: list[str] = []
        for node in selected:
            inner_path = node.path[1:]
            high.extend(inner_path[-3:] if len(inner_path) >= 2 else inner_path)
            low.extend(node.children[:8])
            low.extend(node.siblings[:6])
        return {
            "high_level": _unique_keep_order(high, limit=12),
            "low_level": _unique_keep_order(low, limit=18),
        }

    def _node_to_dict(self, node: OutlineNode) -> dict[str, Any]:
        return {
            "course": node.course,
            "node_id": node.node_id,
            "text": node.text,
            "path": node.path_text,
            "score": node.score,
            "reasons": node.reasons,
        }


@lru_cache(maxsize=1)
def get_course_keyword_enhancer() -> CourseKeywordEnhancer:
    return CourseKeywordEnhancer()
