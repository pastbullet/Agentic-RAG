"""Procedure extractor."""

from __future__ import annotations

import logging

from src.models import ProcedureRule

from .base import BaseExtractor

logger = logging.getLogger("extract")

_SYSTEM_PROMPT = """Extract a procedure rule from the node.

Return JSON only with this shape:
{
  "name": "procedure name",
  "steps": [
    {"step_number": 1, "condition": "...", "action": "..."}
  ]
}
"""


class ProcedureExtractor(BaseExtractor):
    async def extract(
        self,
        node_id: str,
        text: str,
        title: str,
        source_pages: list[int] | None = None,
    ) -> ProcedureRule:
        pages = list(source_pages or [])
        if not text.strip():
            logger.warning("Procedure node %s is empty", node_id)
            return ProcedureRule(name=title or node_id, source_pages=pages)

        try:
            payload = await self._invoke_json(
                _SYSTEM_PROMPT,
                f"Node ID: {node_id}\nTitle: {title}\nText:\n{text[:12000]}",
            )
            if "source_pages" not in payload:
                payload["source_pages"] = pages
            return ProcedureRule.model_validate(payload)
        except Exception as exc:
            logger.warning("Procedure extraction failed for %s: %s", node_id, exc)
            return ProcedureRule(name=title or node_id, source_pages=pages)
