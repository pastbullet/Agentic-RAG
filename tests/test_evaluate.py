"""评测脚本单元测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from src.evaluate import load_test_cases, evaluate_single, evaluate_all
from src.models import TestCase, EvalResult, RAGResponse, Citation


# ── load_test_cases ──────────────────────────────────────


def test_load_test_cases_from_json():
    """验证从 JSON 文件正确加载 TestCase 列表。"""
    data = [
        {
            "id": "t-01",
            "doc_name": "test.pdf",
            "query": "What is X?",
            "type": "definition",
            "expected_pages": [1, 2],
            "key_points": ["point A", "point B"],
        },
        {
            "id": "t-02",
            "doc_name": "test.pdf",
            "query": "How does Y work?",
            "type": "procedure",
            "expected_pages": [],
            "key_points": [],
        },
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        cases = load_test_cases(f.name)

    assert len(cases) == 2
    assert cases[0].id == "t-01"
    assert cases[0].key_points == ["point A", "point B"]
    assert cases[1].expected_pages == []


def test_load_test_cases_real_file():
    """验证能加载真实的测试集文件。"""
    cases = load_test_cases("data/eval/test_questions.json")
    assert len(cases) > 0
    for c in cases:
        assert c.id
        assert c.doc_name
        assert c.query
        assert c.type


# ── evaluate_single ──────────────────────────────────────


def test_evaluate_single_full_coverage():
    """key_points 全部覆盖、引用全部有效、页码全部命中。"""
    tc = TestCase(
        id="test-01",
        doc_name="doc.pdf",
        query="What is X?",
        type="definition",
        expected_pages=[5, 6],
        key_points=["Alpha", "Beta"],
    )
    mock_response = RAGResponse(
        answer='Alpha is defined here <cite doc="doc.pdf" page="5"/> and Beta is also here <cite doc="doc.pdf" page="6"/>',
        answer_clean="Alpha is defined here and Beta is also here",
        citations=[
            Citation(doc_name="doc.pdf", page=5),
            Citation(doc_name="doc.pdf", page=6),
        ],
        pages_retrieved=[5, 6],
        total_turns=3,
    )
    with patch("src.evaluate.agentic_rag", new_callable=AsyncMock, return_value=mock_response):
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_single(tc, model=None)
        )

    assert result.id == "test-01"
    assert result.key_points_covered == 2
    assert result.key_points_total == 2
    assert result.citation_valid_rate == 1.0
    assert result.pages_hit_rate == 1.0
    assert result.total_turns == 3


def test_evaluate_single_partial_coverage():
    """key_points 部分覆盖、引用部分有效。"""
    tc = TestCase(
        id="test-02",
        doc_name="doc.pdf",
        query="What is Y?",
        type="format",
        expected_pages=[1, 2, 3],
        key_points=["Gamma", "Delta", "Epsilon"],
    )
    mock_response = RAGResponse(
        answer='Gamma is here <cite doc="doc.pdf" page="1"/> and something <cite doc="doc.pdf" page="99"/>',
        citations=[
            Citation(doc_name="doc.pdf", page=1),
            Citation(doc_name="doc.pdf", page=99),
        ],
        pages_retrieved=[1, 2],
        total_turns=5,
    )
    with patch("src.evaluate.agentic_rag", new_callable=AsyncMock, return_value=mock_response):
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_single(tc, model=None)
        )

    assert result.key_points_covered == 1  # only "Gamma"
    assert result.key_points_total == 3
    assert result.citation_valid_rate == 0.5  # 1 valid out of 2
    assert abs(result.pages_hit_rate - 2 / 3) < 1e-9  # pages 1,2 hit out of 1,2,3


def test_evaluate_single_no_citations():
    """无引用时 citation_valid_rate 应为 1.0。"""
    tc = TestCase(
        id="test-03",
        doc_name="doc.pdf",
        query="What?",
        type="definition",
        expected_pages=[1],
        key_points=["X"],
    )
    mock_response = RAGResponse(
        answer="X is something without citations",
        citations=[],
        pages_retrieved=[1],
        total_turns=2,
    )
    with patch("src.evaluate.agentic_rag", new_callable=AsyncMock, return_value=mock_response):
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_single(tc, model=None)
        )

    assert result.citation_valid_rate == 1.0
    assert result.citation_count == 0


def test_evaluate_single_case_insensitive_key_points():
    """key_points 匹配应为 case-insensitive。"""
    tc = TestCase(
        id="test-04",
        doc_name="doc.pdf",
        query="Fields?",
        type="format",
        expected_pages=[],
        key_points=["Version", "STATE"],
    )
    mock_response = RAGResponse(
        answer="The version field and the state field are important.",
        citations=[],
        pages_retrieved=[],
        total_turns=1,
    )
    with patch("src.evaluate.agentic_rag", new_callable=AsyncMock, return_value=mock_response):
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_single(tc, model=None)
        )

    assert result.key_points_covered == 2  # both matched case-insensitively


# ── evaluate_all ─────────────────────────────────────────


def test_evaluate_all_aggregation(capsys):
    """验证 evaluate_all 正确汇总指标。"""
    data = [
        {
            "id": "a-01",
            "doc_name": "doc.pdf",
            "query": "Q1",
            "type": "definition",
            "expected_pages": [1],
            "key_points": ["X"],
        },
        {
            "id": "a-02",
            "doc_name": "doc.pdf",
            "query": "Q2",
            "type": "format",
            "expected_pages": [2],
            "key_points": ["Y"],
        },
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        path = f.name

    mock_response = RAGResponse(
        answer="X and Y are here",
        citations=[],
        pages_retrieved=[1, 2],
        total_turns=4,
    )
    with patch("src.evaluate.agentic_rag", new_callable=AsyncMock, return_value=mock_response):
        results = asyncio.get_event_loop().run_until_complete(
            evaluate_all(path, model=None)
        )

    assert len(results) == 2
    # Verify output contains summary
    captured = capsys.readouterr()
    assert "汇总指标" in captured.out
    assert "key_points 覆盖率" in captured.out
