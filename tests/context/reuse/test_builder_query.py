"""Tests for query-aware sorting in ContextReuseBuilder (structure-only)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context.json_io import JSON_IO
from src.context.reuse.builder import ContextReuseBuilder


def _prepare_query_session(session_dir: Path) -> None:
    JSON_IO.save(
        session_dir / "documents" / "doc.pdf" / "document_state.json",
        {
            "doc_name": "doc.pdf",
            "visited_parts": [1],
            "read_pages": [7, 8],
            "total_reads": 2,
        },
    )
    JSON_IO.save(
        session_dir / "documents" / "doc.pdf" / "nodes" / "node_001.json",
        {
            "node_id": "node_001",
            "title": "Error Handling",
            "start_index": 1,
            "end_index": 5,
            "summary": "Timeout retry guidance for request failure",
            "status": "read_complete",
        },
    )
    JSON_IO.save(
        session_dir / "documents" / "doc.pdf" / "nodes" / "node_002.json",
        {
            "node_id": "node_002",
            "title": "Packet Header",
            "start_index": 6,
            "end_index": 10,
            "summary": "Wire format field ordering",
            "status": "read_complete",
        },
    )


def test_query_match_moves_relevant_node_first(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    _prepare_query_session(session_dir)
    builder = ContextReuseBuilder(session_dir, summary_char_budget=10000)

    payload = builder.build_summary_dict("doc.pdf", query="timeout retry failure")

    assert payload["explored_structure"]["nodes"][0]["node_id"] == "node_001"


def test_empty_query_preserves_original_order(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess_1"
    _prepare_query_session(session_dir)
    builder = ContextReuseBuilder(session_dir, summary_char_budget=10000)

    default_payload = builder.build_summary_dict("doc.pdf")
    empty_query_payload = builder.build_summary_dict("doc.pdf", query="")

    assert empty_query_payload == default_payload


def _write_generated_session(
    session_dir: Path,
    nodes: list[dict],
) -> None:
    JSON_IO.save(
        session_dir / "documents" / "doc.pdf" / "document_state.json",
        {
            "doc_name": "doc.pdf",
            "visited_parts": [1],
            "read_pages": [],
            "total_reads": 0,
        },
    )
    for node in nodes:
        JSON_IO.save(
            session_dir / "documents" / "doc.pdf" / "nodes" / f"{node['node_id']}.json",
            node,
        )


_query_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters=(" ",),
    ),
    min_size=10,
    max_size=30,
).map(lambda text: " ".join(text.split()) or "query placeholder")

_node_strategy = st.lists(
    st.fixed_dictionaries(
        {
            "node_id": st.from_regex(r"node_\d{3}", fullmatch=True),
            "title": _query_text,
            "start_index": st.integers(min_value=1, max_value=50),
            "end_index": st.integers(min_value=51, max_value=100),
            "summary": _query_text,
            "status": st.sampled_from(["reading", "read_complete"]),
        }
    ),
    min_size=1,
    max_size=5,
    unique_by=lambda item: item["node_id"],
)


# Feature: agentic-rag-completeness, Property 7: query-aware 排序不丢失数据（structure-only）
@given(query=_query_text, nodes=_node_strategy)
@settings(max_examples=100)
def test_property_7_query_sorting_preserves_items(
    query: str,
    nodes: list[dict],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    _write_generated_session(session_dir, nodes)
    builder = ContextReuseBuilder(session_dir, summary_char_budget=100000)

    payload = builder.build_summary_dict("doc.pdf", query=query)

    assert len(payload["explored_structure"]["nodes"]) == len(nodes)
    assert {item["node_id"] for item in payload["explored_structure"]["nodes"]} == {
        item["node_id"] for item in nodes
    }


# Feature: agentic-rag-completeness, Property 8: query 为空时保持原有排序
@given(nodes=_node_strategy)
@settings(max_examples=100)
def test_property_8_empty_query_keeps_original_behavior(
    nodes: list[dict],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    _write_generated_session(session_dir, nodes)
    builder = ContextReuseBuilder(session_dir, summary_char_budget=100000)

    default_payload = builder.build_summary_dict("doc.pdf")
    none_query_payload = builder.build_summary_dict("doc.pdf", query=None)

    assert none_query_payload == default_payload
