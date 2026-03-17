"""Tests for page-to-node resolution and node status advancement."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context.json_io import JSON_IO
from src.context.stores.document_store import DocumentStore
from src.context.stores.evidence_store import EvidenceStore
from src.context.stores.turn_store import TurnStore
from src.context.updater import Updater


def _write_node(
    session_dir: Path,
    doc_id: str,
    node_id: str,
    start: int,
    end: int,
    *,
    status: str = "discovered",
    read_count: int = 0,
) -> None:
    JSON_IO.save(
        session_dir / "documents" / doc_id / "nodes" / f"{node_id}.json",
        {
            "node_id": node_id,
            "title": node_id,
            "start_index": start,
            "end_index": end,
            "summary": f"{node_id} summary",
            "status": status,
            "read_count": read_count,
        },
    )


def _build_updater(session_dir: Path) -> tuple[Updater, DocumentStore, TurnStore]:
    turns_dir = session_dir / "turns"
    evidences_dir = session_dir / "evidences"
    turns_dir.mkdir(parents=True, exist_ok=True)
    evidences_dir.mkdir(parents=True, exist_ok=True)

    document_store = DocumentStore(session_dir)
    turn_store = TurnStore(turns_dir)
    evidence_store = EvidenceStore(evidences_dir)
    updater = Updater(
        document_store=document_store,
        turn_store=turn_store,
        evidence_store=evidence_store,
    )
    return updater, document_store, turn_store


def test_find_nodes_covering_pages_single_node_hit(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    _write_node(tmp_path, "doc.pdf", "node_001", 1, 5)
    _write_node(tmp_path, "doc.pdf", "node_002", 6, 10)

    assert store.find_nodes_covering_pages("doc.pdf", [7]) == ["node_002"]


def test_handle_page_content_advances_across_multiple_nodes(tmp_path: Path) -> None:
    updater, document_store, turn_store = _build_updater(tmp_path)
    _write_node(tmp_path, "doc.pdf", "node_001", 1, 4)
    _write_node(tmp_path, "doc.pdf", "node_002", 5, 8)
    turn_store.create_turn("turn_0001", "q", "doc.pdf")

    updater.handle_tool_call(
        turn_id="turn_0001",
        tool_name="get_page_content",
        arguments={"doc_name": "doc.pdf", "pages": "4-5"},
        result={
            "content": [
                {"page": 4, "text": "left", "tables": [], "images": []},
                {"page": 5, "text": "right", "tables": [], "images": []},
            ]
        },
        doc_id="doc.pdf",
    )

    node_1 = JSON_IO.load(tmp_path / "documents" / "doc.pdf" / "nodes" / "node_001.json")
    node_2 = JSON_IO.load(tmp_path / "documents" / "doc.pdf" / "nodes" / "node_002.json")
    assert node_1 is not None and node_1["status"] == "reading"
    assert node_2 is not None and node_2["status"] == "reading"

    doc_state = document_store.get_document_state("doc.pdf")
    assert doc_state is not None
    assert doc_state["read_pages"] == [4, 5]


def test_handle_page_content_with_no_matching_node_keeps_states(tmp_path: Path) -> None:
    updater, document_store, turn_store = _build_updater(tmp_path)
    _write_node(tmp_path, "doc.pdf", "node_001", 1, 5)
    turn_store.create_turn("turn_0001", "q", "doc.pdf")

    updater.handle_tool_call(
        turn_id="turn_0001",
        tool_name="get_page_content",
        arguments={"doc_name": "doc.pdf", "pages": "9"},
        result={"content": [{"page": 9, "text": "miss", "tables": [], "images": []}]},
        doc_id="doc.pdf",
    )

    node = JSON_IO.load(tmp_path / "documents" / "doc.pdf" / "nodes" / "node_001.json")
    assert node is not None
    assert node["status"] == "discovered"

    doc_state = document_store.get_document_state("doc.pdf")
    assert doc_state is not None
    assert doc_state["read_pages"] == [9]


def test_find_nodes_covering_pages_returns_empty_when_nodes_dir_missing(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    assert store.find_nodes_covering_pages("doc.pdf", [1, 2, 3]) == []


_node_range_strategy = st.lists(
    st.tuples(
        st.integers(min_value=1, max_value=40),
        st.integers(min_value=1, max_value=15),
    ),
    min_size=1,
    max_size=6,
)

_pages_strategy = st.lists(st.integers(min_value=1, max_value=60), min_size=0, max_size=8)


# Feature: agentic-rag-completeness, Property 3: 页码范围反查节点覆盖正确性
@given(ranges=_node_range_strategy, pages=_pages_strategy)
@settings(max_examples=100)
def test_property_3_page_range_reverse_lookup_matches_manual(
    ranges: list[tuple[int, int]],
    pages: list[int],
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    store = DocumentStore(session_dir)
    doc_id = "doc.pdf"

    expected: list[str] = []
    for index, (start, length) in enumerate(ranges, start=1):
        end = start + length - 1
        node_id = f"node_{index:03d}"
        _write_node(session_dir, doc_id, node_id, start, end)
        if any(start <= page <= end for page in pages):
            expected.append(node_id)

    assert store.find_nodes_covering_pages(doc_id, pages) == expected


_status_strategy = st.sampled_from(["discovered", "reading", "read_complete"])


# Feature: agentic-rag-completeness, Property 4: 节点状态推进三态转换正确性
@given(
    status=_status_strategy,
    initial_read_count=st.integers(min_value=0, max_value=5),
    reads=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=100)
def test_property_4_node_status_progression_is_monotonic(
    status: str,
    initial_read_count: int,
    reads: int,
) -> None:
    session_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    store = DocumentStore(session_dir)
    _write_node(
        session_dir,
        "doc.pdf",
        "node_001",
        1,
        10,
        status=status,
        read_count=initial_read_count,
    )

    for _ in range(reads):
        store.update_node_read_status("doc.pdf", "node_001")

    node = JSON_IO.load(session_dir / "documents" / "doc.pdf" / "nodes" / "node_001.json")
    assert node is not None
    assert node["read_count"] == initial_read_count + reads

    expected_status = status
    if status == "discovered":
        if reads >= 2:
            expected_status = "read_complete"
        elif reads >= 1:
            expected_status = "reading"
    elif status == "reading" and reads >= 1:
        expected_status = "read_complete"

    assert node["status"] == expected_status
