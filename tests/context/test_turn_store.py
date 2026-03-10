"""Unit tests for TurnStore."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.context.stores.turn_store import TurnStore


@pytest.fixture
def turns_dir(tmp_path: Path) -> Path:
    d = tmp_path / "turns"
    d.mkdir()
    return d


@pytest.fixture
def store(turns_dir: Path) -> TurnStore:
    return TurnStore(turns_dir)


class TestCreateTurn:
    def test_creates_turn_file(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "What is FC-LS?", "FC-LS.pdf")
        assert (turns_dir / "turn_0001.json").exists()

    def test_turn_contains_required_fields(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "What is FC-LS?", "FC-LS.pdf")
        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))

        assert data["turn_id"] == "turn_0001"
        assert data["user_query"] == "What is FC-LS?"
        assert data["doc_name"] == "FC-LS.pdf"
        assert data["status"] == "active"
        assert data["tool_calls"] == []
        assert data["finished_at"] is None
        assert data["answer_payload"] is None
        assert "started_at" in data

    def test_started_at_is_iso_utc(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        dt = datetime.fromisoformat(data["started_at"])
        assert dt.tzinfo is not None

    def test_retrieval_trace_initial_structure(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        trace = data["retrieval_trace"]
        assert trace["structure_parts_seen"] == []
        assert trace["history_candidate_nodes"] == []
        assert trace["pages_read"] == []


class TestAddToolCall:
    def test_appends_tool_call(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.add_tool_call("turn_0001", "get_document_structure", {"doc_name": "doc.pdf", "part": 1}, "5 nodes")

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        assert len(data["tool_calls"]) == 1
        tc = data["tool_calls"][0]
        assert tc["tool_name"] == "get_document_structure"
        assert tc["arguments"] == {"doc_name": "doc.pdf", "part": 1}
        assert tc["result_summary"] == "5 nodes"
        assert "timestamp" in tc

    def test_multiple_tool_calls_preserve_order(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.add_tool_call("turn_0001", "tool_a", {}, "result_a")
        store.add_tool_call("turn_0001", "tool_b", {"x": 1}, "result_b")
        store.add_tool_call("turn_0001", "tool_c", {"y": 2}, "result_c")

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        names = [tc["tool_name"] for tc in data["tool_calls"]]
        assert names == ["tool_a", "tool_b", "tool_c"]

    def test_tool_call_timestamp_is_iso_utc(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.add_tool_call("turn_0001", "tool", {}, "result")

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        dt = datetime.fromisoformat(data["tool_calls"][0]["timestamp"])
        assert dt.tzinfo is not None


class TestUpdateRetrievalTrace:
    def test_appends_parts_seen(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.update_retrieval_trace("turn_0001", parts_seen=[1])
        store.update_retrieval_trace("turn_0001", parts_seen=[2, 3])

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        assert data["retrieval_trace"]["structure_parts_seen"] == [1, 2, 3]

    def test_appends_candidate_nodes(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.update_retrieval_trace("turn_0001", candidate_nodes=["node_001"])
        store.update_retrieval_trace("turn_0001", candidate_nodes=["node_002", "node_003"])

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        assert data["retrieval_trace"]["history_candidate_nodes"] == ["node_001", "node_002", "node_003"]

    def test_appends_pages_read(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.update_retrieval_trace("turn_0001", pages_read=[7, 8])
        store.update_retrieval_trace("turn_0001", pages_read=[9])

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        assert data["retrieval_trace"]["pages_read"] == [7, 8, 9]

    def test_none_fields_are_not_modified(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.update_retrieval_trace("turn_0001", parts_seen=[1])
        # Only update pages_read, leave parts_seen and candidate_nodes untouched
        store.update_retrieval_trace("turn_0001", pages_read=[5])

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        trace = data["retrieval_trace"]
        assert trace["structure_parts_seen"] == [1]
        assert trace["history_candidate_nodes"] == []
        assert trace["pages_read"] == [5]


class TestFinalize:
    def test_sets_status_completed(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.finalize("turn_0001", {"answer": "FC-LS is a protocol"})

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        assert data["status"] == "completed"

    def test_records_answer_payload(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        payload = {"answer": "FC-LS is a protocol", "citations": []}
        store.finalize("turn_0001", payload)

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        assert data["answer_payload"] == payload

    def test_sets_finished_at_timestamp(self, store: TurnStore, turns_dir: Path) -> None:
        store.create_turn("turn_0001", "query", "doc.pdf")
        store.finalize("turn_0001", {"answer": "done"})

        data = json.loads((turns_dir / "turn_0001.json").read_text("utf-8"))
        assert data["finished_at"] is not None
        dt = datetime.fromisoformat(data["finished_at"])
        assert dt.tzinfo is not None
