"""Stage 1 classifier for protocol extraction nodes."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent.llm_adapter import LLMAdapter
from src.extract.content_loader import get_node_text
from src.models import NodeLabelMeta, NodeSemanticLabel
from src.tools.pathing import artifact_dir_for_doc

logger = logging.getLogger("extract")

DEFAULT_LABEL_PRIORITY: list[str] = [
    "state_machine",
    "message_format",
    "timer_rule",
    "error_handling",
    "procedure_rule",
    "general_description",
]

PROMPT_VERSION = "v1.4-standalone-fsm-segment-reclassification"
_VALID_LABELS = set(DEFAULT_LABEL_PRIORITY)

_CLASSIFIER_SYSTEM_PROMPT = """You classify communication-protocol document nodes.

Return JSON only with this shape:
{
  "label": "state_machine|message_format|procedure_rule|timer_rule|error_handling|general_description",
  "candidate_labels": ["..."],
  "confidence": 0.0,
  "rationale": "one sentence",
  "secondary_hints": ["..."]
}

Rules:
- Use the node title and node text as primary evidence. Section summaries may be noisy or
  may mention neighboring content; do not classify as state_machine based on summary alone.
- state_machine: a COMPLETE, standalone state machine with MULTIPLE persistent named states
  and MULTIPLE transitions. The states must appear as stable named entities throughout the
  text. A single if-then check, one numbered processing step, or a local clause does NOT
  qualify; classify those as procedure_rule instead.
- message_format: packet/frame/header/TLV/field layout, bit width, encoding.
- procedure_rule: ordered processing behavior, numbered checks, event handling steps,
  or any behavioral rule that describes what to do in a specific case. Use this when
  the text describes processing for ONE event type or ONE condition check.
- timer_rule: timeout, interval, detection time, retry period, liveness timing.
- error_handling: invalid input, discard, exception, recovery, fault behavior.
- general_description: background, definitions, motivation, overview, non-normative text.
- Prefer state_machine ONLY when the text defines a COMPLETE standalone state machine:
  multiple named persistent states, multiple events or transitions, and states reused as
  stable entities across the text. A single "in state X, on event Y, do Z" clause should
  usually be procedure_rule.
- The following are usually NOT state_machine even if they mention state names:
  overview/introduction/design sections, security considerations, references, state-variable
  definitions, packet reception/validation procedures, administrative control rules,
  enabling/disabling features, demultiplexing rules, and backward-compatibility appendices.
- Respect the provided priority order for tie breaking.
- Put non-primary relevant labels into secondary_hints.
"""

_GENERAL_DESCRIPTION_TITLE_HINTS = (
    "overview",
    "introduction",
    "security considerations",
    "references",
    "reference",
    "conventions",
    "terminology",
    "background",
    "design",
    "state variables",
    "non-normative",
)

_PROCEDURE_TITLE_HINTS = (
    "reception of",
    "processing",
    "detecting failures",
    "administrative control",
    "forwarding plane reset",
    "holding down sessions",
    "enabling or disabling",
    "demultiplexing",
    "discriminator fields",
    "check the",
    "check for",
)

_SANITY_DOWNGRADE_PREFIX = "sanity_downgrade:"
_CALL_INVOCATION_HINTS = (
    "user call",
    "the user issues",
    "user issues",
    "application requests",
    "user requests",
    "request to",
)
_NUMBERED_CHECK_TITLE_RE = re.compile(
    r"^\s*\d+(?:\.\d+)*\s+"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+)?"
    r"check\b"
)


def resolve_priority(candidate_labels: list[str], label_priority: list[str]) -> str:
    """Resolve the highest-priority label from candidates."""
    valid_candidates = [label for label in candidate_labels if label in _VALID_LABELS]
    if not valid_candidates:
        return "general_description"

    priority_map = {label: idx for idx, label in enumerate(label_priority)}
    return min(valid_candidates, key=lambda label: priority_map.get(label, len(priority_map)))


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _extract_json_payload(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM returned empty classification response")

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    fenced = text
    if "```" in text:
        parts = text.split("```")
        for block in parts:
            block = block.strip()
            if not block:
                continue
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                payload = json.loads(block)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue

    start = fenced.find("{")
    end = fenced.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = fenced[start : end + 1]
        payload = json.loads(snippet)
        if isinstance(payload, dict):
            return payload

    raise ValueError("Failed to parse classifier JSON payload")


def coerce_node_label(
    node_id: str,
    payload: dict[str, Any],
    label_priority: list[str],
) -> NodeSemanticLabel:
    """Normalize raw LLM payload into a valid NodeSemanticLabel."""
    primary = payload.get("label")
    raw_candidates = payload.get("candidate_labels", [])
    raw_secondary = payload.get("secondary_hints", [])

    candidates: list[str] = []
    if isinstance(primary, str):
        candidates.append(primary)
    if isinstance(raw_candidates, list):
        candidates.extend(label for label in raw_candidates if isinstance(label, str))
    if isinstance(raw_secondary, list):
        candidates.extend(label for label in raw_secondary if isinstance(label, str))

    label = primary if isinstance(primary, str) and primary in _VALID_LABELS else None
    if label is None:
        label = resolve_priority(candidates, label_priority)
    elif label not in _VALID_LABELS:
        label = resolve_priority(candidates, label_priority)

    secondary_hints: list[str] = []
    seen = {label}
    for candidate in candidates:
        if candidate in _VALID_LABELS and candidate not in seen:
            secondary_hints.append(candidate)
            seen.add(candidate)

    rationale = str(payload.get("rationale") or "").strip()
    if not rationale:
        rationale = f"Classified as {label} based on the node content."

    return NodeSemanticLabel(
        node_id=node_id,
        label=label,
        confidence=_coerce_confidence(payload.get("confidence", 0.0)),
        rationale=rationale,
        secondary_hints=secondary_hints,
    )


def _apply_state_machine_sanity_filter(
    label: NodeSemanticLabel,
    title: str,
    summary: str,
    text_snippet: str,
) -> NodeSemanticLabel:
    if label.label != "state_machine":
        return label

    title_lower = (title or "").strip().lower()
    summary_lower = (summary or "").strip().lower()
    text_lower = (text_snippet or "").strip().lower()
    combined = " ".join(part for part in (title_lower, summary_lower, text_lower[:2000]) if part)
    existing_hints = [
        hint for hint in label.secondary_hints if isinstance(hint, str) and hint.strip()
    ]

    def _downgrade(target_label: str, reason: str, message: str) -> NodeSemanticLabel:
        marker = f"{_SANITY_DOWNGRADE_PREFIX}{reason}"
        hints = [*existing_hints]
        if marker not in hints:
            hints.append(marker)
        return label.model_copy(
            update={
                "label": target_label,
                "rationale": f"Downgraded from state_machine ({reason}): {message}",
                "secondary_hints": hints,
            }
        )

    if any(hint in title_lower for hint in _GENERAL_DESCRIPTION_TITLE_HINTS) or "non-normative" in combined:
        return _downgrade(
            "general_description",
            "meta_section",
            f"'{title or '<untitled>'}' is a meta/descriptive section, not a standalone FSM.",
        )

    if any(hint in title_lower for hint in _PROCEDURE_TITLE_HINTS):
        return _downgrade(
            "procedure_rule",
            "numbered_check",
            f"'{title or '<untitled>'}' matches a local check/procedure title pattern rather than a standalone FSM.",
        )

    if _NUMBERED_CHECK_TITLE_RE.match(title_lower):
        return _downgrade(
            "procedure_rule",
            "numbered_check",
            f"'{title or '<untitled>'}' is a numbered check section rather than a standalone FSM.",
        )

    if title_lower.endswith(" call") and any(hint in combined for hint in _CALL_INVOCATION_HINTS):
        return _downgrade(
            "procedure_rule",
            "call_procedure",
            f"'{title or '<untitled>'}' describes an invocation-style procedure rather than a standalone FSM.",
        )

    return label


async def classify_node(
    node_id: str,
    title: str,
    summary: str,
    text_snippet: str,
    label_priority: list[str],
    llm: LLMAdapter,
) -> NodeSemanticLabel:
    """Classify a single leaf node with the shared LLM adapter."""
    user_prompt = (
        f"Node ID: {node_id}\n"
        f"Title: {title or '<empty>'}\n"
        f"Priority: {' > '.join(label_priority)}\n"
        f"Text:\n{text_snippet[:6000]}"
    )
    response = await llm.chat_with_tools(
        [
            {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        [],
    )
    payload = _extract_json_payload(response.text or "")
    label = coerce_node_label(node_id=node_id, payload=payload, label_priority=label_priority)
    return _apply_state_machine_sanity_filter(
        label=label,
        title=title,
        summary=summary,
        text_snippet=text_snippet,
    )


async def classify_all_nodes(
    nodes: list[dict],
    content_dir: str,
    llm: LLMAdapter,
    label_priority: list[str] = DEFAULT_LABEL_PRIORITY,
) -> dict[str, NodeSemanticLabel]:
    """Classify all leaf nodes, isolating failures per node."""
    results: dict[str, NodeSemanticLabel] = {}
    for node in nodes:
        node_id = str(node.get("node_id", ""))
        if not node_id:
            logger.warning("Skipping node without node_id during classification")
            continue

        try:
            text = get_node_text(node, content_dir)
            if not text:
                logger.warning("Node %s has no text available for classification", node_id)
                continue
            label = await classify_node(
                node_id=node_id,
                title=str(node.get("title", "")),
                summary=str(node.get("summary", "")),
                text_snippet=text,
                label_priority=label_priority,
                llm=llm,
            )
            results[node_id] = label
        except Exception as exc:
            logger.warning("Node %s classification failed: %s", node_id, exc)
    return results


def save_labels(labels: dict[str, NodeSemanticLabel], path: str) -> None:
    """Persist node labels to JSON."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {node_id: label.model_dump() for node_id, label in sorted(labels.items())}
    file_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_labels(path: str) -> dict[str, NodeSemanticLabel]:
    """Load node labels from JSON."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid node labels payload")
    return {
        node_id: NodeSemanticLabel.model_validate(raw)
        for node_id, raw in payload.items()
    }


def save_meta(path: str, meta: NodeLabelMeta) -> None:
    """Persist label metadata to JSON."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")


def load_meta(path: str) -> NodeLabelMeta:
    """Load label metadata from JSON."""
    return NodeLabelMeta.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _artifact_paths(doc_stem: str) -> tuple[Path, Path, Path]:
    base = artifact_dir_for_doc(doc_stem)
    return (
        base / "node_labels.json",
        base / "node_labels.meta.json",
        base / "node_labels.override.json",
    )


def _legacy_artifact_paths(doc_stem: str) -> tuple[Path, Path, Path]:
    base = Path("data/out")
    return (
        base / f"{doc_stem}_node_labels.json",
        base / f"{doc_stem}_node_labels.meta.json",
        base / f"{doc_stem}_node_labels.override.json",
    )


def _is_cache_valid(cached: NodeLabelMeta, current: NodeLabelMeta) -> bool:
    return (
        cached.model_name == current.model_name
        and cached.prompt_version == current.prompt_version
        and cached.label_priority == current.label_priority
    )


def apply_overrides(
    labels: dict[str, NodeSemanticLabel],
    override_path: str,
) -> dict[str, NodeSemanticLabel]:
    """Apply manual label overrides from JSON if present."""
    path = Path(override_path)
    if not path.exists():
        return labels

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load override file %s: %s", override_path, exc)
        return labels

    if not isinstance(payload, dict):
        logger.warning("Invalid override file format: %s", override_path)
        return labels

    merged = dict(labels)
    for node_id, raw in payload.items():
        if node_id not in merged:
            logger.warning("Override node_id %s not found in labels", node_id)
            continue
        if not isinstance(raw, dict):
            logger.warning("Invalid override payload for node %s", node_id)
            continue
        label = raw.get("label")
        rationale = str(raw.get("rationale") or "Manual override").strip()
        if not isinstance(label, str) or label not in _VALID_LABELS:
            logger.warning("Invalid override label for node %s: %r", node_id, label)
            continue
        merged[node_id] = merged[node_id].model_copy(
            update={"label": label, "rationale": rationale}
        )
    return merged


def summarize_labels(labels: dict[str, NodeSemanticLabel]) -> dict[str, Any]:
    """Build summary statistics for classified labels."""
    label_counts = {label: 0 for label in DEFAULT_LABEL_PRIORITY}
    skipped_node_ids: list[str] = []
    sanity_downgrade_count = 0
    sanity_downgrade_by_reason: dict[str, int] = {}

    for node_id, label in labels.items():
        label_counts[label.label] = label_counts.get(label.label, 0) + 1
        if label.label == "general_description":
            skipped_node_ids.append(node_id)
        for hint in label.secondary_hints:
            if not isinstance(hint, str) or not hint.startswith(_SANITY_DOWNGRADE_PREFIX):
                continue
            sanity_downgrade_count += 1
            reason = hint.split(":", 1)[1] if ":" in hint else "unknown"
            sanity_downgrade_by_reason[reason] = sanity_downgrade_by_reason.get(reason, 0) + 1

    skipped_count = len(skipped_node_ids)
    return {
        "total_labeled": len(labels),
        "label_counts": label_counts,
        "skipped_count": skipped_count,
        "skipped_node_ids": skipped_node_ids,
        "skipped_by_label": {"general_description": skipped_count},
        "state_machine_sanity_downgrade_count": sanity_downgrade_count,
        "state_machine_sanity_downgrade_by_reason": sanity_downgrade_by_reason,
    }


async def load_or_classify_async(
    doc_stem: str,
    nodes: list[dict],
    content_dir: str,
    llm: LLMAdapter,
    label_priority: list[str] = DEFAULT_LABEL_PRIORITY,
) -> dict[str, NodeSemanticLabel]:
    """Load cached labels when valid, otherwise classify and persist."""
    labels_path, meta_path, override_path = _artifact_paths(doc_stem)
    legacy_labels_path, legacy_meta_path, legacy_override_path = _legacy_artifact_paths(doc_stem)

    current_meta = NodeLabelMeta(
        source_document=doc_stem,
        model_name=llm.model,
        prompt_version=PROMPT_VERSION,
        label_priority=list(label_priority),
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    cache_labels_path = labels_path
    cache_meta_path = meta_path
    cache_override_path = override_path
    if not (cache_labels_path.exists() and cache_meta_path.exists()) and (
        legacy_labels_path.exists() and legacy_meta_path.exists()
    ):
        cache_labels_path = legacy_labels_path
        cache_meta_path = legacy_meta_path
        cache_override_path = legacy_override_path

    if cache_labels_path.exists() and cache_meta_path.exists():
        try:
            cached_meta = load_meta(str(cache_meta_path))
            if _is_cache_valid(cached_meta, current_meta):
                cached_labels = load_labels(str(cache_labels_path))
                if nodes and not cached_labels:
                    logger.warning(
                        "Ignoring empty classifier cache for %s because the document still has %d nodes.",
                        doc_stem,
                        len(nodes),
                    )
                else:
                    return apply_overrides(cached_labels, str(cache_override_path))
        except Exception as exc:
            logger.warning("Ignoring invalid classifier cache for %s: %s", doc_stem, exc)

    labels = await classify_all_nodes(
        nodes=nodes,
        content_dir=content_dir,
        llm=llm,
        label_priority=label_priority,
    )
    if nodes and not labels:
        logger.warning(
            "No labels produced for %s; skipping cache write to avoid persisting an empty classification result.",
            doc_stem,
        )
        return labels
    save_labels(labels, str(labels_path))
    save_meta(str(meta_path), current_meta)
    return apply_overrides(labels, str(override_path))


def load_or_classify(
    doc_stem: str,
    nodes: list[dict],
    content_dir: str,
    llm: LLMAdapter,
    label_priority: list[str] = DEFAULT_LABEL_PRIORITY,
) -> dict[str, NodeSemanticLabel]:
    """Synchronous wrapper for loading or running classification."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            load_or_classify_async(
                doc_stem=doc_stem,
                nodes=nodes,
                content_dir=content_dir,
                llm=llm,
                label_priority=label_priority,
            )
        )
    raise RuntimeError("Use load_or_classify_async inside an active event loop")
