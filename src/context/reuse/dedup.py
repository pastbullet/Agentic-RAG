"""Deduplicating wrapper around get_page_content.

NOTE: Content-level dedup (replacing page text with cached summaries) has been
intentionally removed.  When the LLM calls get_page_content it explicitly needs
the original text (precise definitions, tables, etc.).  Returning a lossy summary
instead silently degrades answer quality.

The wrapper is kept as a thin pass-through so that:
  - The ``is_cached`` flag is always present (downstream code expects it).
  - Future structure-level caching can be added here without touching loop.py.
"""

from __future__ import annotations

from src.tools.page_content import get_page_content


class PageDedupWrapper:
    """Thin pass-through wrapper — always returns original page content."""

    def __init__(self, session_dir=None, doc_name: str = "", enable: bool = True):
        # session_dir / doc_name / enable kept for API compatibility
        pass

    @staticmethod
    def get_page_content(doc_name: str, pages: str) -> dict:
        result = get_page_content(doc_name, pages)
        content = result.get("content")
        if isinstance(content, list):
            result = {
                **result,
                "content": [
                    {**item, "is_cached": False}
                    for item in content
                    if isinstance(item, dict)
                ],
            }
        return result
