"""Message-format extractor."""

from __future__ import annotations

import logging
import re

from src.extract.message_archetype import build_message_archetype_contribution_from_message
from src.models import ProtocolField, ProtocolMessage

from .base import BaseExtractor

logger = logging.getLogger("extract")

ECHO_PACKET_RULE = """
If the message is described as opaque, implementation-specific,
or "not defined by this specification", return an EMPTY fields list.
Do NOT invent fields for opaque or variable-content packets.
"""

VARIABLE_LENGTH_RULE = """
If a field length is variable, bounded by a byte range, or described as
"1 to N bytes", set size_bits=null and put the length constraint in the
description. Do NOT force a fixed bit width for variable-length fields.
"""

AUTH_BOUNDARY_RULE = """
Extract ONLY the primary message described in this section.
- A control-packet section should include only control-packet fields.
- An authentication-section section should include only authentication fields.
- One node equals one message; do not mix fields from different message types.
"""

_SYSTEM_PROMPT = f"""Extract a protocol message or frame definition.

Prioritize field tables when present.
{ECHO_PACKET_RULE.strip()}
{VARIABLE_LENGTH_RULE.strip()}
{AUTH_BOUNDARY_RULE.strip()}
Return JSON only with this shape:
{{
  "name": "message name",
  "fields": [
    {{"name": "Version", "type": "uint", "size_bits": 3, "description": "..."}}
  ]
}}
"""


def _post_process_message(message: ProtocolMessage) -> ProtocolMessage:
    normalized_name = (message.name or "").strip().lower()
    fields = list(message.fields)

    if "echo" in normalized_name and len(fields) <= 1:
        fields = []

    normalized_fields: list[ProtocolField] = []
    for field in fields:
        description = field.description or ""
        size_bits = field.size_bits
        if "password" in (field.name or "").lower() and re.search(
            r"\bvariable(?:-length)?\b|\b\d+\s*(?:to|-)\s*\d+\s*bytes?\b",
            description,
            flags=re.IGNORECASE,
        ):
            size_bits = None
        normalized_fields.append(
            ProtocolField(
                name=field.name,
                type=field.type,
                size_bits=size_bits,
                description=description,
            )
        )

    return ProtocolMessage(
        name=message.name,
        fields=normalized_fields,
        source_pages=list(message.source_pages),
        archetype_contribution=message.archetype_contribution,
    )


class MessageExtractor(BaseExtractor):
    """Extract ProtocolMessage objects from nodes."""

    async def extract(
        self,
        node_id: str,
        text: str,
        title: str,
        source_pages: list[int] | None = None,
    ) -> ProtocolMessage:
        pages = list(source_pages or [])
        if not text.strip():
            logger.warning("Message node %s is empty", node_id)
            return ProtocolMessage(name=title or node_id, source_pages=pages)

        try:
            payload = await self._invoke_json(
                _SYSTEM_PROMPT,
                f"Node ID: {node_id}\nTitle: {title}\nText:\n{text[:12000]}",
            )
            if "source_pages" not in payload:
                payload["source_pages"] = pages
            message = _post_process_message(ProtocolMessage.model_validate(payload))
            archetype = build_message_archetype_contribution_from_message(message, source_node_ids=[node_id])
            if archetype is not None and message.archetype_contribution is None:
                message = message.model_copy(
                    update={"archetype_contribution": archetype.model_dump()},
                    deep=True,
                )
            return message
        except Exception as exc:
            logger.warning("Message extraction failed for %s: %s", node_id, exc)
            return ProtocolMessage(name=title or node_id, source_pages=pages)
