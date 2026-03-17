"""Build structure-only context summaries from persisted session state.

Strategy: inject document *navigation* (explored nodes, read page ranges) so the
LLM knows where it has been.  Never inject old content (page summaries, evidence)
— the LLM should always call ``get_page_content`` to obtain original text.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.context.json_io import JSON_IO

logger = logging.getLogger(__name__)

PRECHECK_GUIDANCE = """

## Context Reuse Guidance

A navigation summary from previous turns is injected below.
It tells you which document sections and pages have already been explored — treat it as a **map**, not as content.

1. **Use the node list to orient yourself**: identify which sections are relevant to the current question.
2. **Use the read-page ranges to avoid redundant retrieval**: if you already know the answer is on a page listed as read, call `get_page_content` for that page directly instead of re-discovering it through `get_document_structure`.
3. **Always retrieve original text**: do NOT answer from the summary alone. Call `get_page_content` to get the actual page content before answering.
4. **Skip irrelevant sections**: if the summary covers a different topic, ignore it entirely.
""".rstrip()


class ContextReuseBuilder:
    """Read-only builder — emits structure-only navigation context."""

    def __init__(self, session_dir: Path, summary_char_budget: int = 4000):
        self._session_dir = Path(session_dir)
        self._summary_char_budget = max(0, int(summary_char_budget))

    # ── Public API ──────────────────────────────────────────

    def build_summary(self, doc_name: str, query: str | None = None) -> str:
        data = self.build_summary_dict(doc_name, query=query)
        total_chars = int(data.get("total_chars", 0))
        if total_chars <= 0:
            return ""
        if total_chars > self._summary_char_budget:
            return ""
        logger.info("Context summary built for %s: %s chars", doc_name, total_chars)
        return self._render_markdown(
            data["explored_structure"]["visited_parts"],
            data["explored_structure"]["nodes"],
            data["read_pages"],
        )

    def build_summary_dict(
        self,
        doc_name: str,
        query: str | None = None,
    ) -> dict[str, Any]:
        doc_state = self._read_document_state(doc_name) or {}
        node_summaries = self._read_node_summaries(doc_name)

        visited_parts = self._as_int_list(doc_state.get("visited_parts", []))
        read_pages = self._as_int_list(doc_state.get("read_pages", []))

        if not visited_parts and not read_pages and not node_summaries:
            return {
                "doc_name": doc_name,
                "explored_structure": {"visited_parts": [], "nodes": []},
                "read_pages": [],
                "total_chars": 0,
            }

        # Query-aware sorting: put relevant nodes first
        if isinstance(query, str) and query.strip():
            query_tokens = self._tokenize(query)
            if query_tokens:
                node_summaries = sorted(
                    node_summaries,
                    key=lambda item: self._score_relevance(
                        query_tokens,
                        f"{item.get('title', '')} {item.get('summary') or ''}",
                    ),
                    reverse=True,
                )

        node_summaries = self._truncate_to_budget(
            node_summaries,
            visited_parts,
            read_pages,
        )

        total_chars = len(
            self._render_markdown(visited_parts, node_summaries, read_pages)
        )
        return {
            "doc_name": doc_name,
            "explored_structure": {
                "visited_parts": visited_parts,
                "nodes": node_summaries,
            },
            "read_pages": read_pages,
            "total_chars": total_chars,
        }

    # ── Data readers ────────────────────────────────────────

    def _read_document_state(self, doc_name: str) -> dict | None:
        path = self._session_dir / "documents" / doc_name / "document_state.json"
        try:
            payload = JSON_IO.load(path)
        except Exception as exc:
            logger.warning("Failed to read document_state for %s: %s", doc_name, exc)
            return None
        return payload if isinstance(payload, dict) else None

    def _read_node_summaries(self, doc_name: str) -> list[dict]:
        nodes_dir = self._session_dir / "documents" / doc_name / "nodes"
        if not nodes_dir.exists():
            return []

        nodes: list[dict] = []
        try:
            paths = sorted(nodes_dir.glob("*.json"))
        except Exception as exc:
            logger.warning("Failed to list nodes for %s: %s", doc_name, exc)
            return []

        for path in paths:
            try:
                payload = JSON_IO.load(path)
            except Exception as exc:
                logger.warning("Failed to read node state %s: %s", path.name, exc)
                continue
            if not isinstance(payload, dict):
                continue
            status = payload.get("status", "discovered")
            # Skip skeleton nodes with no page range (useless for navigation)
            si = int(payload.get("start_index", 0))
            ei = int(payload.get("end_index", 0))
            if si == 0 and ei == 0:
                continue
            nodes.append(
                {
                    "node_id": payload.get("node_id", path.stem),
                    "title": payload.get("title", ""),
                    "start_index": si,
                    "end_index": ei,
                    "summary": payload.get("summary"),
                    "status": status,
                }
            )
        nodes.sort(key=lambda item: (item["start_index"], item["end_index"], item["node_id"]))
        return nodes

    # ── Truncation ──────────────────────────────────────────

    def _truncate_to_budget(
        self,
        node_summaries: list[dict],
        visited_parts: list[int],
        read_pages: list[int],
    ) -> list[dict]:
        if self._summary_char_budget <= 0:
            return []

        kept = list(node_summaries)

        def current_len() -> int:
            return len(self._render_markdown(visited_parts, kept, read_pages))

        # Drop discovered nodes first (least valuable — just orientation)
        while kept and current_len() > self._summary_char_budget:
            # Find last discovered node to drop
            idx = next(
                (i for i in range(len(kept) - 1, -1, -1) if kept[i]["status"] == "discovered"),
                -1,
            )
            if idx >= 0:
                kept.pop(idx)
            else:
                # No more discovered nodes; drop reading/read_complete from end
                kept.pop()

        if current_len() > self._summary_char_budget:
            return []
        return kept

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _as_int_list(value: Any) -> list[int]:
        items: list[int] = []
        if not isinstance(value, list):
            return items
        for item in value:
            if isinstance(item, int):
                items.append(item)
        return items

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        lower = str(text).lower()
        en_tokens = set(re.findall(r"[a-z0-9]{2,}", lower))
        zh_tokens = set(re.findall(r"[\u4e00-\u9fff]", str(text)))
        return en_tokens | zh_tokens

    @staticmethod
    def _score_relevance(query_tokens: set[str], text: str) -> int:
        if not query_tokens:
            return 0
        return len(query_tokens & ContextReuseBuilder._tokenize(text))

    # ── Rendering ───────────────────────────────────────────

    @staticmethod
    def _render_markdown(
        visited_parts: list[int],
        node_summaries: list[dict],
        read_pages: list[int],
    ) -> str:
        if not visited_parts and not node_summaries and not read_pages:
            return ""

        lines: list[str] = []

        # Split nodes by status
        read_nodes = [n for n in node_summaries if n["status"] in ("reading", "read_complete")]
        discovered_nodes = [n for n in node_summaries if n["status"] == "discovered"]

        # Section 1: Read/reading nodes with summaries (highest navigation value)
        if read_nodes:
            lines.append("## 已读节点")
            lines.append("")
            for node in read_nodes:
                title = node.get("title", "")
                si = node.get("start_index", 0)
                ei = node.get("end_index", 0)
                summary = str(node.get("summary") or "").strip()
                if summary:
                    lines.append(f"- {title} (pp.{si}-{ei}): {summary}")
                else:
                    lines.append(f"- {title} (pp.{si}-{ei})")
            lines.append("")

        # Section 2: Discovered nodes — compact index (title + page range only)
        if discovered_nodes:
            lines.append("## 文档目录")
            lines.append("")
            for node in discovered_nodes:
                title = node.get("title", "")
                si = node.get("start_index", 0)
                ei = node.get("end_index", 0)
                lines.append(f"- {title} (pp.{si}-{ei})")
            lines.append("")

        # Section 3: Read page ranges (compact)
        if read_pages:
            ranges = ContextReuseBuilder._compress_page_ranges(sorted(set(read_pages)))
            lines.append(f"已读页面: {ranges}")
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def _compress_page_ranges(pages: list[int]) -> str:
        """Compress [1,2,3,5,7,8,9] into '1-3, 5, 7-9'."""
        if not pages:
            return ""
        ranges: list[str] = []
        start = prev = pages[0]
        for p in pages[1:]:
            if p == prev + 1:
                prev = p
            else:
                ranges.append(f"{start}-{prev}" if start != prev else str(start))
                start = prev = p
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        return ", ".join(ranges)
