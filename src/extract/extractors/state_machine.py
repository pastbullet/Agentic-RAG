"""State machine extractor."""

from __future__ import annotations

import logging

from src.models import ProtocolStateMachine

from .base import BaseExtractor

logger = logging.getLogger("extract")

_SYSTEM_PROMPT = """Extract a protocol state machine from the given document node.

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
            if "source_pages" not in payload:
                payload["source_pages"] = pages
            return ProtocolStateMachine.model_validate(payload)
        except Exception as exc:
            logger.warning("State machine extraction failed for %s: %s", node_id, exc)
            return ProtocolStateMachine(name=title or node_id, source_pages=pages)
