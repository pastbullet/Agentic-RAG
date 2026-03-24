"""Error-handling extractor."""

from __future__ import annotations

import logging

from src.models import ErrorRule

from .base import BaseExtractor

logger = logging.getLogger("extract")

_SYSTEM_PROMPT = """Extract an error-handling rule from the node.

Return JSON only with this shape:
{
  "error_condition": "invalid field value",
  "handling_action": "discard the packet",
  "description": "..."
}
"""


class ErrorExtractor(BaseExtractor):
    async def extract(
        self,
        node_id: str,
        text: str,
        title: str,
        source_pages: list[int] | None = None,
    ) -> ErrorRule:
        pages = list(source_pages or [])
        if not text.strip():
            logger.warning("Error-handling node %s is empty", node_id)
            return ErrorRule(
                error_condition=title or node_id,
                handling_action="",
                source_pages=pages,
            )

        try:
            payload = await self._invoke_json(
                _SYSTEM_PROMPT,
                f"Node ID: {node_id}\nTitle: {title}\nText:\n{text[:12000]}",
            )
            if "source_pages" not in payload:
                payload["source_pages"] = pages
            return ErrorRule.model_validate(payload)
        except Exception as exc:
            logger.warning("Error extraction failed for %s: %s", node_id, exc)
            return ErrorRule(
                error_condition=title or node_id,
                handling_action="",
                source_pages=pages,
            )
