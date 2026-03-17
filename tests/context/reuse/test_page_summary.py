"""Tests for PageSummaryGenerator — unit tests + Hypothesis property tests."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context.reuse.page_summary import PageSummaryGenerator

# ── Unit Tests ──────────────────────────────────────────────


def test_page_summary_generate_limits_to_half() -> None:
    text = "para1\n\npara2 is longer\n\npara3 is also here"
    summary = PageSummaryGenerator.generate(7, "doc.pdf", text, "turn_0001")

    assert summary["page_num"] == 7
    assert summary["doc_name"] == "doc.pdf"
    assert summary["source_turn_id"] == "turn_0001"
    assert summary["summary_length"] <= summary["original_length"] // 2


def test_page_summary_save_is_first_write_wins(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    first = PageSummaryGenerator.generate(3, "doc.pdf", "first paragraph\n\nsecond", "turn_0001")
    second = {**first, "summary_text": "overwritten", "summary_length": len("overwritten")}

    PageSummaryGenerator.save(session_dir, "doc.pdf", first)
    PageSummaryGenerator.save(session_dir, "doc.pdf", second)

    saved = PageSummaryGenerator.load(session_dir, "doc.pdf", 3)
    assert saved is not None
    assert saved["summary_text"] == first["summary_text"]


def test_page_summary_load_all_sorted(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    for page_num in (5, 2, 4):
        PageSummaryGenerator.save(
            session_dir,
            "doc.pdf",
            PageSummaryGenerator.generate(page_num, "doc.pdf", f"text {page_num}" * 20, "turn_0001"),
        )

    summaries = PageSummaryGenerator.load_all(session_dir, "doc.pdf")
    assert [item["page_num"] for item in summaries] == [2, 4, 5]


def test_page_summary_empty_text() -> None:
    summary = PageSummaryGenerator.generate(1, "doc.pdf", "", "turn_0001")
    assert summary["summary_text"] == ""
    assert summary["summary_length"] == 0
    assert summary["original_length"] == 0


# ── Hypothesis Property Tests ───────────────────────────────

_REQUIRED_FIELDS = {"page_num", "doc_name", "summary_text", "original_length", "summary_length", "generated_at", "source_turn_id"}


# Feature: context-reuse-enhancement, Property 8: Page_Summary 生成约束
@given(
    page_num=st.integers(min_value=1, max_value=500),
    text=st.text(min_size=1, max_size=5000),
    turn_id=st.from_regex(r"turn_\d{4}", fullmatch=True),
)
@settings(max_examples=100)
def test_property_8_page_summary_generation_constraint(page_num: int, text: str, turn_id: str) -> None:
    summary = PageSummaryGenerator.generate(page_num, "doc.pdf", text, turn_id)

    # All required fields present
    assert _REQUIRED_FIELDS <= set(summary.keys())

    # summary_text length <= 50% of original
    assert summary["summary_length"] <= summary["original_length"] // 2 or summary["original_length"] <= 1

    # Metadata consistency
    assert summary["page_num"] == page_num
    assert summary["doc_name"] == "doc.pdf"
    assert summary["source_turn_id"] == turn_id
    assert summary["original_length"] == len(text)
    assert summary["summary_length"] == len(summary["summary_text"])


# Feature: context-reuse-enhancement, Property 9: Page_Summary 首次生成不可覆盖
@given(
    page_num=st.integers(min_value=1, max_value=100),
    text_a=st.text(min_size=10, max_size=200),
    text_b=st.text(min_size=10, max_size=200),
)
@settings(max_examples=100)
def test_property_9_page_summary_first_write_wins(page_num: int, text_a: str, text_b: str) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    first = PageSummaryGenerator.generate(page_num, "doc.pdf", text_a, "turn_0001")
    second = PageSummaryGenerator.generate(page_num, "doc.pdf", text_b, "turn_0002")

    PageSummaryGenerator.save(session_dir, "doc.pdf", first)
    PageSummaryGenerator.save(session_dir, "doc.pdf", second)

    loaded = PageSummaryGenerator.load(session_dir, "doc.pdf", page_num)
    assert loaded is not None
    assert loaded["summary_text"] == first["summary_text"]
    assert loaded["source_turn_id"] == "turn_0001"
