"""Tests for ContextReuseBuilder — unit tests + Hypothesis property tests.

Strategy: structure-only. Builder emits explored nodes + read page ranges.
No page summaries or evidences are injected.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context.json_io import JSON_IO
from src.context.reuse.builder import ContextReuseBuilder


# ── Helpers ─────────────────────────────────────────────────


def _prepare_session(session_dir: Path, doc_name: str = "doc.pdf") -> None:
    JSON_IO.save(
        session_dir / "documents" / doc_name / "document_state.json",
        {
            "doc_name": doc_name,
            "visited_parts": [1, 2],
            "read_pages": [7, 8],
            "total_reads": 2,
        },
    )
    JSON_IO.save(
        session_dir / "documents" / doc_name / "nodes" / "node_001.json",
        {
            "node_id": "node_001",
            "title": "Intro",
            "start_index": 1,
            "end_index": 10,
            "summary": "intro summary",
            "status": "read_complete",
        },
    )
    JSON_IO.save(
        session_dir / "documents" / doc_name / "nodes" / "node_002.json",
        {
            "node_id": "node_002",
            "title": "Discovered Section",
            "start_index": 11,
            "end_index": 20,
            "summary": "discovered summary",
            "status": "discovered",
        },
    )


# ── Unit Tests ──────────────────────────────────────────────


def test_build_summary_dict_collects_structure(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    _prepare_session(session_dir)

    builder = ContextReuseBuilder(session_dir)
    payload = builder.build_summary_dict("doc.pdf")

    assert payload["doc_name"] == "doc.pdf"
    assert payload["explored_structure"]["visited_parts"] == [1, 2]
    # Both nodes included (read_complete + discovered)
    assert len(payload["explored_structure"]["nodes"]) == 2
    assert payload["explored_structure"]["nodes"][0]["node_id"] == "node_001"
    assert payload["explored_structure"]["nodes"][1]["node_id"] == "node_002"
    assert payload["read_pages"] == [7, 8]
    assert payload["total_chars"] > 0


def test_build_summary_markdown_sections(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    _prepare_session(session_dir)

    builder = ContextReuseBuilder(session_dir)
    text = builder.build_summary("doc.pdf")

    # read_complete node → "已读节点" section
    assert "## 已读节点" in text
    assert "已读页面:" in text
    assert "total_chars" not in text
    # No content sections
    assert "## 已提取的证据" not in text
    assert "## 已读页面摘要" not in text


def test_build_summary_respects_budget(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    _prepare_session(session_dir)
    JSON_IO.save(
        session_dir / "documents" / "doc.pdf" / "nodes" / "node_003.json",
        {
            "node_id": "node_003",
            "title": "Very long node",
            "start_index": 21,
            "end_index": 40,
            "summary": "x" * 500,
            "status": "read_complete",
        },
    )

    builder = ContextReuseBuilder(session_dir, summary_char_budget=120)
    payload = builder.build_summary_dict("doc.pdf")

    assert payload["total_chars"] <= 120


def test_build_summary_dict_round_trip(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    _prepare_session(session_dir)

    builder = ContextReuseBuilder(session_dir)
    payload = builder.build_summary_dict("doc.pdf")

    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_build_summary_tolerates_bad_data_source(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    _prepare_session(session_dir)
    bad_node = session_dir / "documents" / "doc.pdf" / "nodes" / "node_bad.json"
    bad_node.write_text("{bad json", encoding="utf-8")

    builder = ContextReuseBuilder(session_dir)
    payload = builder.build_summary_dict("doc.pdf")

    assert payload["explored_structure"]["nodes"]


def test_compress_page_ranges() -> None:
    assert ContextReuseBuilder._compress_page_ranges([1, 2, 3, 5, 7, 8, 9]) == "1-3, 5, 7-9"
    assert ContextReuseBuilder._compress_page_ranges([4]) == "4"
    assert ContextReuseBuilder._compress_page_ranges([]) == ""


# ── Hypothesis Strategies ───────────────────────────────────

_node_strategy = st.fixed_dictionaries({
    "node_id": st.from_regex(r"node_\d{3}", fullmatch=True),
    "title": st.text(min_size=1, max_size=60),
    "start_index": st.integers(min_value=1, max_value=200),
    "end_index": st.integers(min_value=1, max_value=200),
    "summary": st.one_of(st.text(min_size=1, max_size=100), st.none()),
    "status": st.sampled_from(["discovered", "reading", "read_complete"]),
})


def _write_session(
    session_dir: Path,
    doc_name: str,
    visited_parts: list[int],
    read_pages: list[int],
    nodes: list[dict],
) -> None:
    JSON_IO.save(
        session_dir / "documents" / doc_name / "document_state.json",
        {"doc_name": doc_name, "visited_parts": visited_parts, "read_pages": read_pages, "total_reads": len(read_pages)},
    )
    for node in nodes:
        JSON_IO.save(session_dir / "documents" / doc_name / "nodes" / f"{node['node_id']}.json", node)


# ── Hypothesis Property Tests ───────────────────────────────


# Feature: context-reuse-enhancement, Property 1: 上下文摘要完整组装（structure-only）
@given(
    nodes=st.lists(_node_strategy, min_size=0, max_size=5),
    visited_parts=st.lists(st.integers(min_value=1, max_value=10), min_size=0, max_size=5),
    read_pages=st.lists(st.integers(min_value=1, max_value=50), min_size=0, max_size=5),
)
@settings(max_examples=100)
def test_property_1_summary_dict_complete_assembly(
    nodes: list[dict],
    visited_parts: list[int],
    read_pages: list[int],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    _write_session(session_dir, "doc.pdf", visited_parts, read_pages, nodes)

    builder = ContextReuseBuilder(session_dir, summary_char_budget=50000)
    payload = builder.build_summary_dict("doc.pdf")

    # Schema keys present
    assert "doc_name" in payload
    assert "explored_structure" in payload
    assert "read_pages" in payload
    assert "total_chars" in payload

    # All nodes with non-zero page range are included (any status)
    for node in payload["explored_structure"]["nodes"]:
        assert node["status"] in {"discovered", "reading", "read_complete"}

    # read_pages is a flat list
    assert isinstance(payload["read_pages"], list)


# Feature: context-reuse-enhancement, Property 2: Context_Summary Markdown 序列化格式（structure-only）
@given(
    nodes=st.lists(_node_strategy.filter(lambda n: n["status"] in ("reading", "read_complete")), min_size=1, max_size=3),
)
@settings(max_examples=100)
def test_property_2_markdown_serialization_format(
    nodes: list[dict],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    _write_session(session_dir, "doc.pdf", [1], [7], nodes)

    builder = ContextReuseBuilder(session_dir, summary_char_budget=50000)
    text = builder.build_summary("doc.pdf")
    d = builder.build_summary_dict("doc.pdf")

    # At least one section header should be present
    assert "## 已读节点" in text or "## 文档目录" in text
    assert "已读页面:" in text
    # No content sections
    assert "## 已提取的证据" not in text
    assert "## 已读页面摘要" not in text
    # total_chars NOT in text output, but IS in dict
    assert "total_chars" not in text
    assert "total_chars" in d
    assert isinstance(d["total_chars"], int)


# Feature: context-reuse-enhancement, Property 3: 数据源失败容错
@given(
    broken_source=st.sampled_from(["document_state", "nodes"]),
)
@settings(max_examples=100)
def test_property_3_data_source_failure_tolerance(broken_source: str) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    _write_session(
        session_dir, "doc.pdf", [1], [7],
        [{"node_id": "node_001", "title": "T", "start_index": 1, "end_index": 5, "summary": "s", "status": "read_complete"}],
    )

    if broken_source == "document_state":
        path = session_dir / "documents" / "doc.pdf" / "document_state.json"
        path.write_text("{bad", encoding="utf-8")
    elif broken_source == "nodes":
        nodes_dir = session_dir / "documents" / "doc.pdf" / "nodes"
        for f in nodes_dir.glob("*.json"):
            f.write_text("{bad", encoding="utf-8")

    builder = ContextReuseBuilder(session_dir, summary_char_budget=50000)
    payload = builder.build_summary_dict("doc.pdf")
    assert isinstance(payload, dict)
    assert "doc_name" in payload


# Feature: context-reuse-enhancement, Property 6: 摘要字符预算约束
@given(
    budget=st.integers(min_value=50, max_value=5000),
    n_nodes=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=100)
def test_property_6_summary_char_budget_constraint(
    budget: int,
    n_nodes: int,
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    nodes = [
        {"node_id": f"node_{i:03d}", "title": f"Node {i}", "start_index": i * 10, "end_index": i * 10 + 9, "summary": f"summary {i} " * 10, "status": "read_complete"}
        for i in range(n_nodes)
    ]
    _write_session(session_dir, "doc.pdf", [1, 2], [7, 8], nodes)

    builder = ContextReuseBuilder(session_dir, summary_char_budget=budget)
    text = builder.build_summary("doc.pdf")

    if text:
        assert len(text) <= budget


# Feature: context-reuse-enhancement, Property 13: 跨轮次累积状态
@given(
    n_turns=st.integers(min_value=2, max_value=4),
)
@settings(max_examples=100)
def test_property_13_cross_turn_accumulation(n_turns: int) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    all_pages: list[int] = []

    for turn in range(1, n_turns + 1):
        page_num = turn * 10
        all_pages.append(page_num)

        JSON_IO.save(
            session_dir / "documents" / "doc.pdf" / "document_state.json",
            {"doc_name": "doc.pdf", "visited_parts": list(range(1, turn + 1)), "read_pages": list(all_pages), "total_reads": len(all_pages)},
        )

    builder = ContextReuseBuilder(session_dir, summary_char_budget=50000)
    payload = builder.build_summary_dict("doc.pdf")

    assert set(all_pages) == set(payload["read_pages"])


# Feature: context-reuse-enhancement, Property 14: Context_Summary 字典 JSON 往返
@given(
    nodes=st.lists(_node_strategy.filter(lambda n: n["status"] in ("reading", "read_complete")), min_size=0, max_size=3),
)
@settings(max_examples=100)
def test_property_14_json_round_trip(
    nodes: list[dict],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    _write_session(session_dir, "doc.pdf", [1], [7], nodes)

    builder = ContextReuseBuilder(session_dir, summary_char_budget=50000)
    payload = builder.build_summary_dict("doc.pdf")

    roundtripped = json.loads(json.dumps(payload, ensure_ascii=False))
    assert roundtripped == payload
