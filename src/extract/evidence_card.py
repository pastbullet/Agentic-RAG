"""Evidence-card generation and review-decision helpers."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.agent.llm_adapter import LLMAdapter
from src.tools.page_content import _load_page_data

logger = logging.getLogger("extract")

MAX_EVIDENCE_SIDE_TOKENS = 4000
_FORBIDDEN_DECISION_FIELDS = {"merge", "decision", "should_merge", "verdict", "final_decision"}

_SYSTEM_PROMPT = """You are a protocol evidence organizer.
Compare two extraction objects and produce a neutral evidence card.
You must NOT output merge decisions, verdicts, or recommendations.
Return JSON only with this schema:
{
  "common_evidence": ["..."],
  "differing_evidence": ["..."],
  "naming_relation": "...",
  "wording_vs_substance": "...",
  "llm_confidence": 0.0,
  "unresolved_conflicts": ["..."]
}
"""


class EvidenceCard(BaseModel):
    pair_id: list[int]
    object_type: str
    common_evidence: list[str] = []
    differing_evidence: list[str] = []
    naming_relation: str = ""
    wording_vs_substance: str = ""
    llm_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unresolved_conflicts: list[str] = []


def _coerce_pair(value: Any) -> list[int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        left = int(value[0])
        right = int(value[1])
        return [left, right] if left < right else [right, left]
    return None


def _clip_text_tokens(text: str, max_tokens: int = MAX_EVIDENCE_SIDE_TOKENS) -> str:
    tokens = re.findall(r"\S+", text)
    if len(tokens) <= max_tokens:
        return text
    clipped = " ".join(tokens[:max_tokens]).strip()
    return f"{clipped}\n[truncated]"


def _page_excerpt(content_dir: str, pages: list[int], max_tokens: int = MAX_EVIDENCE_SIDE_TOKENS) -> str:
    if not content_dir or not pages:
        return ""
    ordered_pages = sorted({page for page in pages if isinstance(page, int) and page > 0})
    if not ordered_pages:
        return ""
    page_data = _load_page_data(content_dir, ordered_pages)
    chunks: list[str] = []
    for page in ordered_pages:
        payload = page_data.get(page, {})
        text = str(payload.get("text", "")).strip()
        if text:
            chunks.append(f"[page {page}]\n{text}")
    if not chunks:
        return ""
    return _clip_text_tokens("\n\n".join(chunks), max_tokens=max_tokens)


def _parse_json_response(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    if "```" in raw:
        for block in raw.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if not block:
                continue
            try:
                payload = json.loads(block)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        payload = json.loads(raw[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("failed to parse JSON response")


def _fallback_card(candidate: dict[str, Any], object_type: str) -> EvidenceCard:
    pair = _coerce_pair(candidate.get("pair")) or [0, 1]
    left_name, right_name = candidate.get("names", ["", ""])
    common = [f"name_pair: {left_name} <-> {right_name}"]
    differing = []
    diff = candidate.get("diff")
    if isinstance(diff, dict):
        for key in ("states_only_left", "states_only_right", "fields_only_left", "fields_only_right"):
            values = diff.get(key)
            if isinstance(values, list) and values:
                differing.append(f"{key}: {values}")
    return EvidenceCard(
        pair_id=pair,
        object_type=object_type,
        common_evidence=common,
        differing_evidence=differing,
        naming_relation="fallback",
        wording_vs_substance="fallback_without_llm",
        llm_confidence=0.0,
        unresolved_conflicts=[],
    )


def _sanitize_card_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    for key in list(sanitized):
        if key.lower() in _FORBIDDEN_DECISION_FIELDS:
            sanitized.pop(key, None)
    return sanitized


async def _generate_single_card(
    llm: LLMAdapter | None,
    object_type: str,
    candidate: dict[str, Any],
    content_dir: str,
) -> EvidenceCard:
    pair = _coerce_pair(candidate.get("pair")) or [0, 1]
    if llm is None:
        return _fallback_card(candidate, object_type)

    left = candidate.get("left") if isinstance(candidate.get("left"), dict) else {}
    right = candidate.get("right") if isinstance(candidate.get("right"), dict) else {}
    left_excerpt = _page_excerpt(content_dir, list(left.get("source_pages", [])))
    right_excerpt = _page_excerpt(content_dir, list(right.get("source_pages", [])))
    user_prompt = (
        f"Object type: {object_type}\n"
        f"Pair: {pair}\n"
        f"Scores: {json.dumps(candidate.get('scores', {}), ensure_ascii=False)}\n"
        f"Near-miss candidate JSON:\n{json.dumps(candidate, ensure_ascii=False, indent=2)}\n\n"
        f"Left source excerpt:\n{left_excerpt}\n\n"
        f"Right source excerpt:\n{right_excerpt}\n"
    )
    try:
        response = await llm.chat_with_tools(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            [],
        )
        payload = _sanitize_card_payload(_parse_json_response(response.text or ""))
        payload["pair_id"] = pair
        payload["object_type"] = object_type
        return EvidenceCard.model_validate(payload)
    except Exception as exc:
        logger.warning("Evidence card generation failed for %s pair %s: %s", object_type, pair, exc)
        return _fallback_card(candidate, object_type)


async def generate_evidence_cards(
    llm: LLMAdapter | None,
    near_miss_report: dict[str, Any],
    content_dir: str,
) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    for object_type, key in (
        ("state_machine", "state_machine_near_misses"),
        ("message", "message_near_misses"),
    ):
        candidates = near_miss_report.get(key, [])
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, dict):
                cards.append(
                    await _generate_single_card(llm=llm, object_type=object_type, candidate=candidate, content_dir=content_dir)
                )
    return cards


def load_review_decisions(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("decisions", [])
    if not isinstance(payload, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        object_type = str(item.get("object_type", "")).strip().lower()
        decision = str(item.get("decision", "")).strip().lower()
        pair = _coerce_pair(item.get("pair") or item.get("pair_id"))
        if pair is None:
            continue
        if object_type not in {"state_machine", "message"}:
            continue
        if decision not in {"merge", "keep_separate"}:
            continue
        normalized.append(
            {
                "object_type": object_type,
                "pair": pair,
                "decision": decision,
            }
        )
    return normalized


def save_review_decisions(path: str | Path, decisions: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = load_review_decisions_from_payload(decisions)
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def load_review_decisions_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("decisions", [])
    if not isinstance(payload, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        object_type = str(item.get("object_type", "")).strip().lower()
        decision = str(item.get("decision", "")).strip().lower()
        pair = _coerce_pair(item.get("pair") or item.get("pair_id"))
        if pair is None:
            continue
        if object_type not in {"state_machine", "message"}:
            continue
        if decision not in {"merge", "keep_separate"}:
            continue
        normalized.append({"object_type": object_type, "pair": pair, "decision": decision})
    return normalized
