"""Timer extractor."""

from __future__ import annotations

import logging

from src.models import TimerConfig

from .base import BaseExtractor

logger = logging.getLogger("extract")

_SYSTEM_PROMPT = """Extract a timer configuration from the node.

Return JSON only with this shape:
{
  "timer_name": "Detection Time",
  "timeout_value": "multiplier * interval",
  "trigger_action": "declare session down",
  "description": "..."
}
"""


class TimerExtractor(BaseExtractor):
    async def extract(
        self,
        node_id: str,
        text: str,
        title: str,
        source_pages: list[int] | None = None,
    ) -> TimerConfig:
        pages = list(source_pages or [])
        if not text.strip():
            logger.warning("Timer node %s is empty", node_id)
            return TimerConfig(timer_name=title or node_id, source_pages=pages)

        try:
            payload = await self._invoke_json(
                _SYSTEM_PROMPT,
                f"Node ID: {node_id}\nTitle: {title}\nText:\n{text[:12000]}",
            )
            if "source_pages" not in payload:
                payload["source_pages"] = pages
            return TimerConfig.model_validate(payload)
        except Exception as exc:
            logger.warning("Timer extraction failed for %s: %s", node_id, exc)
            return TimerConfig(timer_name=title or node_id, source_pages=pages)
