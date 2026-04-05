"""State machine extractor."""

from __future__ import annotations

import logging
from typing import Any

from src.models import ProtocolStateMachine

from .base import BaseExtractor

logger = logging.getLogger("extract")

_SYSTEM_PROMPT = """You are extracting a standalone protocol state machine from a document node.

A STANDALONE state machine requires ALL of the following:
1. At least 2 distinct, persistently-named states.
2. Multiple transitions driven by different protocol events or branches.
3. The named states are referenced repeatedly in the text as stable entities.

DO NOT extract a state machine when the text only contains:
- A single processing check or clause.
- One timeout/reset rule that handles a specific case in a larger flow.
- Steps that describe how to process ONE type of packet or event.
- A fragment like "Third Check for SYN" that is part of a numbered procedure list.
- Any text that only covers one branch of a larger state machine.

NEGATIVE EXAMPLES — return empty for these:
- "If the RST bit is set: ... if the SYN bit is set: ..."
- "If the ACK is not acceptable, send a reset: <SEQ=SEG.ACK><CTL=RST>"
- "Timeout: When a retransmission timer expires, retransmit the segment"
- "Security Considerations: ..." or "References: ..."

The document outline context, if present, is only to help decide whether this node is
standalone. Do NOT invent states or transitions from sibling titles or section headings.

If the text does NOT describe a standalone state machine, return exactly:
{"name": "", "states": [], "transitions": []}

Return JSON only with this shape:
{
  "name": "state machine name",
  "states": [
    {"name": "Down", "description": "...", "is_initial": false, "is_final": false}
  ],
  "transitions": [
    {
      "from_state": "Down",
      "to_state": "Init",
      "event": "Receive packet",
      "condition": "...",
      "actions": ["..."]
    }
  ]
}
"""


def _string_or_empty(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_state_item(item: dict[str, Any]) -> dict[str, Any]:
    name = (
        _string_or_empty(item.get("name"))
        or _string_or_empty(item.get("label"))
        or _string_or_empty(item.get("id"))
        or _string_or_empty(item.get("title"))
    )
    description = (
        _string_or_empty(item.get("description"))
        or _string_or_empty(item.get("details"))
        or _string_or_empty(item.get("summary"))
        or ""
    )
    return {
        "name": name,
        "description": description,
        "is_initial": bool(item.get("is_initial", item.get("initial", False))),
        "is_final": bool(item.get("is_final", item.get("final", item.get("terminal", False)))),
    }


def _normalize_action_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_transition_item(item: dict[str, Any]) -> dict[str, Any]:
    from_state = (
        _string_or_empty(item.get("from_state"))
        or _string_or_empty(item.get("from"))
        or _string_or_empty(item.get("source"))
    )
    to_state = (
        _string_or_empty(item.get("to_state"))
        or _string_or_empty(item.get("to"))
        or _string_or_empty(item.get("target"))
    )
    event = (
        _string_or_empty(item.get("event"))
        or _string_or_empty(item.get("trigger"))
        or _string_or_empty(item.get("on"))
        or _string_or_empty(item.get("when"))
        or _string_or_empty(item.get("input"))
        or _string_or_empty(item.get("label"))
        or _string_or_empty(item.get("condition"))
        or "unspecified_event"
    )
    condition = (
        _string_or_empty(item.get("condition"))
        or _string_or_empty(item.get("guard"))
        or ""
    )
    return {
        "from_state": from_state,
        "to_state": to_state,
        "event": event,
        "condition": condition,
        "actions": _normalize_action_list(
            item.get("actions")
            or item.get("action")
            or item.get("effects")
            or item.get("effect")
        ),
    }


def _is_meaningful_state_item(item: dict[str, Any]) -> bool:
    return bool(item.get("name"))


def _is_meaningful_transition_item(item: dict[str, Any]) -> bool:
    return bool(item.get("from_state") and item.get("to_state"))


def _coerce_non_standalone_payload_to_empty(payload: dict[str, Any], title: str, node_id: str) -> dict[str, Any]:
    state_names = {
        item.get("name", "").strip()
        for item in payload.get("states", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item.get("name").strip()
    }
    transitions = [
        item
        for item in payload.get("transitions", [])
        if isinstance(item, dict)
    ]
    event_names = {
        item.get("event", "").strip()
        for item in transitions
        if isinstance(item.get("event"), str) and item.get("event").strip()
    }

    if len(state_names) >= 2 and len(transitions) >= 1 and len(event_names) >= 1:
        return payload

    return {
        "name": title or node_id,
        "states": [],
        "transitions": [],
    }


def _normalize_state_machine_payload(payload: dict[str, Any], title: str, node_id: str) -> dict[str, Any]:
    normalized = dict(payload)

    raw_states = normalized.get("states")
    if isinstance(raw_states, list):
        normalized["states"] = [
            _normalize_state_item(item)
            for item in raw_states
            if isinstance(item, dict)
        ]
        normalized["states"] = [
            item for item in normalized["states"] if _is_meaningful_state_item(item)
        ]
    else:
        normalized["states"] = []

    raw_transitions = normalized.get("transitions")
    if isinstance(raw_transitions, list):
        normalized["transitions"] = [
            _normalize_transition_item(item)
            for item in raw_transitions
            if isinstance(item, dict)
        ]
        normalized["transitions"] = [
            item for item in normalized["transitions"] if _is_meaningful_transition_item(item)
        ]
    else:
        normalized["transitions"] = []

    if not normalized.get("name") and not normalized.get("states") and not normalized.get("transitions"):
        normalized["name"] = title or node_id

    return _coerce_non_standalone_payload_to_empty(normalized, title, node_id)


class StateMachineExtractor(BaseExtractor):
    """Extract ProtocolStateMachine objects from nodes."""

    async def extract(
        self,
        node_id: str,
        text: str,
        title: str,
        source_pages: list[int] | None = None,
    ) -> ProtocolStateMachine:
        pages = list(source_pages or [])
        if not text.strip():
            logger.warning("State machine node %s is empty", node_id)
            return ProtocolStateMachine(name=title or node_id, source_pages=pages)

        try:
            payload = await self._invoke_json(
                _SYSTEM_PROMPT,
                f"Node ID: {node_id}\nTitle: {title}\nText:\n{text[:12000]}",
            )
            payload = _normalize_state_machine_payload(payload, title, node_id)
            if "source_pages" not in payload:
                payload["source_pages"] = pages
            return ProtocolStateMachine.model_validate(payload)
        except Exception as exc:
            logger.warning("State machine extraction failed for %s: %s", node_id, exc)
            return ProtocolStateMachine(name=title or node_id, source_pages=pages)
