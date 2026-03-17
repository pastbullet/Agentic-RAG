"""Tests for agent efficiency evaluation metrics."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import hypothesis.strategies as st
from hypothesis import given, settings

from src.evaluate import (
    _compute_avg_pages_per_turn,
    _compute_duplicate_read_rate,
    evaluate_single,
)
from src.models import Citation, RAGResponse, TestCase


def test_evaluate_single_computes_duplicate_rate_and_avg_pages() -> None:
    test_case = TestCase(
        id="dup-01",
        doc_name="doc.pdf",
        query="q",
        type="definition",
        expected_pages=[1, 2, 3],
        key_points=["alpha"],
    )
    response = RAGResponse(
        answer='alpha <cite doc="doc.pdf" page="1"/>',
        citations=[Citation(doc_name="doc.pdf", page=1)],
        pages_retrieved=[1, 2, 3],
        all_pages_requested=[1, 2, 3, 1, 2],
        total_turns=2,
    )

    with patch("src.evaluate.agentic_rag", new_callable=AsyncMock, return_value=response):
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_single(test_case, model=None)
        )

    assert result.duplicate_read_rate == 0.4
    assert result.avg_pages_per_turn == 1.5


def test_metric_helpers_handle_empty_and_zero_turns() -> None:
    assert _compute_duplicate_read_rate([]) == 0.0
    assert _compute_avg_pages_per_turn([], 3) == 0.0
    assert _compute_avg_pages_per_turn([1, 2, 3], 0) == 0.0


_pages_strategy = st.lists(st.integers(min_value=1, max_value=30), min_size=0, max_size=20)


# Feature: agentic-rag-completeness, Property 5: duplicate_read_rate 计算正确性
@given(pages=_pages_strategy)
@settings(max_examples=100)
def test_property_5_duplicate_read_rate_bounds_and_formula(pages: list[int]) -> None:
    rate = _compute_duplicate_read_rate(pages)

    assert 0.0 <= rate <= 1.0
    if not pages:
        assert rate == 0.0
    else:
        assert rate == 1.0 - len(set(pages)) / len(pages)
        if len(set(pages)) == len(pages):
            assert rate == 0.0


# Feature: agentic-rag-completeness, Property 6: avg_pages_per_turn 计算正确性
@given(
    pages=_pages_strategy,
    total_turns=st.integers(min_value=0, max_value=20),
)
@settings(max_examples=100)
def test_property_6_avg_pages_per_turn_is_non_negative_and_consistent(
    pages: list[int],
    total_turns: int,
) -> None:
    avg = _compute_avg_pages_per_turn(pages, total_turns)

    assert avg >= 0.0
    if total_turns == 0:
        assert avg == 0.0
    else:
        assert avg == len(set(pages)) / total_turns
