"""Unit tests for Updater page parsing behavior."""

from __future__ import annotations

import json
from pathlib import Path

from src.context.stores.document_store import DocumentStore
from src.context.stores.evidence_store import EvidenceStore
from src.context.stores.turn_store import TurnStore
from src.context.updater import Updater


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


def test_handle_page_content_parses_range_string(tmp_path: Path) -> None:
    updater, document_store, turn_store = _build_updater(tmp_path)
    turn_store.create_turn("turn_0001", "q", "doc.pdf")

    updater.handle_tool_call(
        turn_id="turn_0001",
        tool_name="get_page_content",
        arguments={"doc_name": "doc.pdf", "pages": "147-149"},
        result={"content": []},
        doc_id="doc.pdf",
    )

    doc_state = document_store.get_document_state("doc.pdf")
    assert doc_state is not None
    assert doc_state["read_pages"] == [147, 148, 149]
    assert doc_state["total_reads"] == 3

    turn = json.loads((tmp_path / "turns" / "turn_0001.json").read_text("utf-8"))
    assert turn["retrieval_trace"]["pages_read"] == [147, 148, 149]


def test_handle_page_content_prefers_result_pages(tmp_path: Path) -> None:
    updater, document_store, turn_store = _build_updater(tmp_path)
    turn_store.create_turn("turn_0001", "q", "doc.pdf")

    updater.handle_tool_call(
        turn_id="turn_0001",
        tool_name="get_page_content",
        arguments={"doc_name": "doc.pdf", "pages": "149"},
        result={"content": [{"page": 72, "text": "x", "tables": [], "images": []}]},
        doc_id="doc.pdf",
    )

    doc_state = document_store.get_document_state("doc.pdf")
    assert doc_state is not None
    assert doc_state["read_pages"] == [72]
    assert doc_state["total_reads"] == 1
