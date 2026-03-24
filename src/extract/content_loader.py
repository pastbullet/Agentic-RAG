"""Helpers for loading node text from page_index nodes and Content DB."""

from __future__ import annotations

import logging
from pathlib import Path

from src.tools.page_content import _load_page_data

logger = logging.getLogger("extract")


def get_node_pages(node: dict) -> list[int]:
    """Return the inclusive page span for a node."""
    start_index = node.get("start_index")
    end_index = node.get("end_index")
    if not isinstance(start_index, int) or not isinstance(end_index, int):
        return []
    if start_index <= 0 or end_index < start_index:
        return []
    return list(range(start_index, end_index + 1))


def _slice_lines(text: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Slice 1-based inclusive line ranges from a page text block."""
    if not text:
        return ""

    lines = text.splitlines()
    if not lines:
        return ""

    start = 1 if not isinstance(start_line, int) or start_line < 1 else start_line
    end = len(lines) if not isinstance(end_line, int) or end_line < 1 else end_line

    start = min(start, len(lines))
    end = min(end, len(lines))
    if end < start:
        return ""
    return "\n".join(lines[start - 1 : end])


def get_node_text(node: dict, content_dir: str) -> str | None:
    """Get the text content for a leaf node."""
    direct_text = node.get("text")
    if isinstance(direct_text, str) and direct_text != "":
        return direct_text

    page_nums = get_node_pages(node)
    if not page_nums:
        logger.error("Node %s has no valid page span", node.get("node_id", "<unknown>"))
        return None

    content_path = Path(content_dir)
    if not content_path.exists():
        logger.error(
            "Content DB missing for node %s: %s",
            node.get("node_id", "<unknown>"),
            content_dir,
        )
        return None

    page_data = _load_page_data(str(content_path), page_nums)
    missing_pages = [page_num for page_num in page_nums if page_num not in page_data]
    if missing_pages:
        logger.error(
            "Content DB pages missing for node %s: %s",
            node.get("node_id", "<unknown>"),
            missing_pages,
        )
        return None

    start_index = node["start_index"]
    end_index = node["end_index"]
    start_line = node.get("start_line")
    end_line = node.get("end_line")

    parts: list[str] = []
    for page_num in page_nums:
        text = page_data[page_num].get("text", "")
        if page_num == start_index and page_num == end_index:
            chunk = _slice_lines(text, start_line, end_line)
        elif page_num == start_index:
            chunk = _slice_lines(text, start_line, None)
        elif page_num == end_index:
            chunk = _slice_lines(text, 1, end_line)
        else:
            chunk = text
        if chunk:
            parts.append(chunk)

    if not parts:
        return None
    return "\n".join(parts)
