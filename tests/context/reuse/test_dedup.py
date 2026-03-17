"""Tests for PageDedupWrapper — unit tests + Hypothesis property tests.

After removing content-level dedup, the wrapper is a thin pass-through that
always returns original page content with ``is_cached: False``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context.reuse.dedup import PageDedupWrapper


# ── Helpers ─────────────────────────────────────────────────


def _fake_get_page_content(doc_name: str, pages: str) -> dict:
    from src.tools.page_content import parse_pages
    nums = parse_pages(pages)
    return {
        "content": [{"page": n, "text": f"fresh {n}", "tables": [], "images": []} for n in nums],
        "next_steps": "cite",
        "total_pages": 50,
    }


# ── Unit Tests ──────────────────────────────────────────────


def test_page_dedup_passthrough_always_returns_original(tmp_path: Path) -> None:
    """Wrapper should always return original content, never cached summaries."""
    with patch("src.context.reuse.dedup.get_page_content", side_effect=_fake_get_page_content):
        wrapper = PageDedupWrapper(tmp_path / "sess_1", "doc.pdf")
        result = wrapper.get_page_content("doc.pdf", "7")

    assert len(result["content"]) == 1
    assert result["content"][0]["is_cached"] is False
    assert result["content"][0]["text"] == "fresh 7"


def test_page_dedup_multiple_pages(tmp_path: Path) -> None:
    with patch("src.context.reuse.dedup.get_page_content", side_effect=_fake_get_page_content):
        wrapper = PageDedupWrapper(tmp_path / "sess_1", "doc.pdf")
        result = wrapper.get_page_content("doc.pdf", "7,9")

    assert [item["page"] for item in result["content"]] == [7, 9]
    assert all(item["is_cached"] is False for item in result["content"])


def test_page_dedup_disable_flag_still_passthrough(tmp_path: Path) -> None:
    """enable=False should behave identically (always pass-through)."""
    with patch("src.context.reuse.dedup.get_page_content", side_effect=_fake_get_page_content):
        wrapper = PageDedupWrapper(tmp_path / "sess_1", "doc.pdf", enable=False)
        result = wrapper.get_page_content("doc.pdf", "3")

    assert result["content"][0]["is_cached"] is False
    assert result["content"][0]["text"] == "fresh 3"


# ── Hypothesis Property Tests ───────────────────────────────


# Feature: context-reuse-enhancement, Property 7: 页面去重正确性（现在所有页面都是 is_cached=False）
@given(
    page_nums=st.lists(st.integers(min_value=1, max_value=40), min_size=1, max_size=5, unique=True),
)
@settings(max_examples=100)
def test_property_7_page_dedup_always_fresh(
    page_nums: list[int],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    pages_str = ",".join(str(p) for p in page_nums)

    with patch("src.context.reuse.dedup.get_page_content", side_effect=_fake_get_page_content):
        wrapper = PageDedupWrapper(session_dir, "doc.pdf")
        result = wrapper.get_page_content("doc.pdf", pages_str)

    assert "content" in result
    for item in result["content"]:
        assert item["is_cached"] is False


# Feature: context-reuse-enhancement, Property 11: enable_page_dedup=False 直通原始工具
@given(
    page_nums=st.lists(st.integers(min_value=1, max_value=20), min_size=1, max_size=3, unique=True),
)
@settings(max_examples=100)
def test_property_11_disable_dedup_passthrough(
    page_nums: list[int],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    pages_str = ",".join(str(p) for p in page_nums)

    with patch("src.context.reuse.dedup.get_page_content", side_effect=_fake_get_page_content):
        wrapper = PageDedupWrapper(session_dir, "doc.pdf", enable=False)
        result = wrapper.get_page_content("doc.pdf", pages_str)

    for item in result["content"]:
        assert item["is_cached"] is False
