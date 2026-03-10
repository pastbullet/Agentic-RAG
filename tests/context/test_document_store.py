"""Unit tests for DocumentStore core functionality (document_state.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.context.json_io import JSON_IO
from src.context.stores.document_store import DocumentStore


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    return tmp_path / "sess_20250101_120000"


@pytest.fixture
def store(session_dir: Path) -> DocumentStore:
    return DocumentStore(session_dir)


class TestUpdateVisitedParts:
    def test_creates_directory_and_file(self, store: DocumentStore, session_dir: Path) -> None:
        store.update_visited_parts("FC-LS.pdf", 1)

        state_path = session_dir / "documents" / "FC-LS.pdf" / "document_state.json"
        assert state_path.exists()

    def test_initial_state_fields(self, store: DocumentStore, session_dir: Path) -> None:
        store.update_visited_parts("FC-LS.pdf", 1)

        data = json.loads(
            (session_dir / "documents" / "FC-LS.pdf" / "document_state.json").read_text("utf-8")
        )
        assert data["doc_name"] == "FC-LS.pdf"
        assert data["visited_parts"] == [1]
        assert data["read_pages"] == []
        assert data["total_reads"] == 0

    def test_appends_parts(self, store: DocumentStore) -> None:
        store.update_visited_parts("doc.pdf", 1)
        store.update_visited_parts("doc.pdf", 2)
        store.update_visited_parts("doc.pdf", 3)

        state = store.get_document_state("doc.pdf")
        assert state["visited_parts"] == [1, 2, 3]

    def test_deduplicates_parts(self, store: DocumentStore) -> None:
        store.update_visited_parts("doc.pdf", 1)
        store.update_visited_parts("doc.pdf", 2)
        store.update_visited_parts("doc.pdf", 1)  # duplicate

        state = store.get_document_state("doc.pdf")
        assert state["visited_parts"] == [1, 2]


class TestUpdateReadPages:
    def test_creates_directory_and_file(self, store: DocumentStore, session_dir: Path) -> None:
        store.update_read_pages("FC-LS.pdf", [7, 8])

        state_path = session_dir / "documents" / "FC-LS.pdf" / "document_state.json"
        assert state_path.exists()

    def test_appends_pages_deduplicated(self, store: DocumentStore) -> None:
        store.update_read_pages("doc.pdf", [7, 8])
        store.update_read_pages("doc.pdf", [8, 9, 10])

        state = store.get_document_state("doc.pdf")
        assert state["read_pages"] == [7, 8, 9, 10]

    def test_increments_total_reads(self, store: DocumentStore) -> None:
        store.update_read_pages("doc.pdf", [7, 8])
        store.update_read_pages("doc.pdf", [8, 9])  # page 8 is duplicate but still counts

        state = store.get_document_state("doc.pdf")
        assert state["total_reads"] == 4  # 2 + 2

    def test_empty_pages_list(self, store: DocumentStore) -> None:
        store.update_read_pages("doc.pdf", [])

        state = store.get_document_state("doc.pdf")
        assert state["read_pages"] == []
        assert state["total_reads"] == 0


class TestGetDocumentState:
    def test_returns_none_when_not_exists(self, store: DocumentStore) -> None:
        result = store.get_document_state("nonexistent.pdf")
        assert result is None

    def test_returns_state_after_update(self, store: DocumentStore) -> None:
        store.update_visited_parts("doc.pdf", 1)
        store.update_read_pages("doc.pdf", [5, 6])

        state = store.get_document_state("doc.pdf")
        assert state is not None
        assert state["doc_name"] == "doc.pdf"
        assert state["visited_parts"] == [1]
        assert state["read_pages"] == [5, 6]
        assert state["total_reads"] == 2


class TestMultipleDocuments:
    def test_independent_document_states(self, store: DocumentStore) -> None:
        store.update_visited_parts("doc_a.pdf", 1)
        store.update_visited_parts("doc_b.pdf", 2)
        store.update_read_pages("doc_a.pdf", [10])
        store.update_read_pages("doc_b.pdf", [20, 21])

        state_a = store.get_document_state("doc_a.pdf")
        state_b = store.get_document_state("doc_b.pdf")

        assert state_a["visited_parts"] == [1]
        assert state_a["read_pages"] == [10]
        assert state_b["visited_parts"] == [2]
        assert state_b["read_pages"] == [20, 21]


# ------------------------------------------------------------------
# Tests for node state management (Task 5.2)
# ------------------------------------------------------------------


class TestFlattenStructure:
    def test_flat_list_no_children(self, store: DocumentStore) -> None:
        structure = [
            {"title": "Chapter 1", "node_id": "n1", "start_index": 1, "end_index": 10},
            {"title": "Chapter 2", "node_id": "n2", "start_index": 11, "end_index": 20},
        ]
        result = store.flatten_structure(structure)
        assert len(result) == 2
        assert result[0]["parent_path"] == ""
        assert result[1]["parent_path"] == ""
        assert "children" not in result[0]

    def test_nested_children(self, store: DocumentStore) -> None:
        structure = [
            {
                "title": "Chapter 1",
                "node_id": "n1",
                "start_index": 1,
                "end_index": 20,
                "children": [
                    {"title": "Section 1.1", "node_id": "n2", "start_index": 1, "end_index": 10},
                    {"title": "Section 1.2", "node_id": "n3", "start_index": 11, "end_index": 20},
                ],
            },
        ]
        result = store.flatten_structure(structure)
        assert len(result) == 3
        assert result[0]["parent_path"] == ""
        assert result[0]["title"] == "Chapter 1"
        assert result[1]["parent_path"] == "Chapter 1"
        assert result[1]["title"] == "Section 1.1"
        assert result[2]["parent_path"] == "Chapter 1"

    def test_deeply_nested(self, store: DocumentStore) -> None:
        structure = [
            {
                "title": "Root",
                "node_id": "r",
                "start_index": 1,
                "end_index": 100,
                "children": [
                    {
                        "title": "Child",
                        "node_id": "c",
                        "start_index": 1,
                        "end_index": 50,
                        "children": [
                            {"title": "Grandchild", "node_id": "gc", "start_index": 1, "end_index": 25},
                        ],
                    },
                ],
            },
        ]
        result = store.flatten_structure(structure)
        assert len(result) == 3
        assert result[2]["parent_path"] == "Root/Child"
        assert result[2]["title"] == "Grandchild"

    def test_empty_structure(self, store: DocumentStore) -> None:
        result = store.flatten_structure([])
        assert result == []

    def test_children_key_removed(self, store: DocumentStore) -> None:
        structure = [
            {"title": "A", "node_id": "a", "children": []},
        ]
        result = store.flatten_structure(structure)
        assert "children" not in result[0]


class TestGenerateProvisionalId:
    def test_returns_tmp_prefix(self) -> None:
        pid = DocumentStore.generate_provisional_id("doc", "title", 1, 10, "")
        assert pid.startswith("tmp_")

    def test_stable_output(self) -> None:
        pid1 = DocumentStore.generate_provisional_id("doc", "title", 1, 10, "path")
        pid2 = DocumentStore.generate_provisional_id("doc", "title", 1, 10, "path")
        assert pid1 == pid2

    def test_different_inputs_different_ids(self) -> None:
        pid1 = DocumentStore.generate_provisional_id("doc", "title", 1, 10, "path")
        pid2 = DocumentStore.generate_provisional_id("doc", "other", 1, 10, "path")
        assert pid1 != pid2

    def test_length(self) -> None:
        pid = DocumentStore.generate_provisional_id("doc", "title", 1, 10, "")
        # "tmp_" (4) + 12 hex chars = 16
        assert len(pid) == 16


class TestUpsertNode:
    def _make_node_data(self, **overrides: object) -> dict:
        defaults = {
            "node_id": "node_001",
            "title": "Chapter 1",
            "start_index": 1,
            "end_index": 15,
            "summary": "Intro chapter",
            "is_skeleton": False,
            "parent_path": "",
        }
        defaults.update(overrides)
        return defaults

    def test_new_node_creates_file(self, store: DocumentStore, session_dir: Path) -> None:
        node_data = self._make_node_data()
        nid = store.upsert_node("doc.pdf", node_data, "turn_0001")

        assert nid == "node_001"
        node_path = session_dir / "documents" / "doc.pdf" / "nodes" / "node_001.json"
        assert node_path.exists()

    def test_new_node_fields(self, store: DocumentStore) -> None:
        node_data = self._make_node_data()
        store.upsert_node("doc.pdf", node_data, "turn_0001")

        state = json.loads(
            (store._node_path("doc.pdf", "node_001")).read_text("utf-8")
        )
        assert state["node_id"] == "node_001"
        assert state["title"] == "Chapter 1"
        assert state["start_index"] == 1
        assert state["end_index"] == 15
        assert state["summary"] == "Intro chapter"
        assert state["parent_path"] == ""
        assert state["status"] == "discovered"
        assert state["read_count"] == 0
        assert state["is_skeleton_latest"] is False
        assert state["seen_in_parts"] == []
        assert state["first_seen_turn_id"] == "turn_0001"
        assert state["last_seen_turn_id"] == "turn_0001"
        assert state["is_provisional_id"] is False
        assert state["fact_digest"] is None

    def test_provisional_id_when_node_id_none(self, store: DocumentStore) -> None:
        node_data = self._make_node_data(node_id=None)
        nid = store.upsert_node("doc.pdf", node_data, "turn_0001")

        assert nid.startswith("tmp_")
        state = json.loads(
            (store._node_path("doc.pdf", nid)).read_text("utf-8")
        )
        assert state["is_provisional_id"] is True

    def test_provisional_id_when_node_id_empty(self, store: DocumentStore) -> None:
        node_data = self._make_node_data(node_id="")
        nid = store.upsert_node("doc.pdf", node_data, "turn_0001")

        assert nid.startswith("tmp_")
        state = json.loads(
            (store._node_path("doc.pdf", nid)).read_text("utf-8")
        )
        assert state["is_provisional_id"] is True

    def test_skeleton_preserves_summary(self, store: DocumentStore) -> None:
        # First create a full node with summary
        node_data = self._make_node_data(summary="Original summary")
        store.upsert_node("doc.pdf", node_data, "turn_0001")

        # Now upsert with skeleton — summary should be preserved
        skeleton_data = self._make_node_data(
            is_skeleton=True, summary="Should be ignored", title="Updated Title"
        )
        store.upsert_node("doc.pdf", skeleton_data, "turn_0002")

        state = json.loads(
            (store._node_path("doc.pdf", "node_001")).read_text("utf-8")
        )
        assert state["summary"] == "Original summary"
        assert state["title"] == "Updated Title"
        assert state["is_skeleton_latest"] is True
        assert state["last_seen_turn_id"] == "turn_0002"

    def test_skeleton_preserves_fact_digest(self, store: DocumentStore) -> None:
        # Create node, then manually set fact_digest
        node_data = self._make_node_data()
        store.upsert_node("doc.pdf", node_data, "turn_0001")

        node_path = store._node_path("doc.pdf", "node_001")
        state = json.loads(node_path.read_text("utf-8"))
        state["fact_digest"] = "important digest"
        JSON_IO.save(node_path, state)

        # Skeleton upsert should not overwrite fact_digest
        skeleton_data = self._make_node_data(is_skeleton=True)
        store.upsert_node("doc.pdf", skeleton_data, "turn_0002")

        state = json.loads(node_path.read_text("utf-8"))
        assert state["fact_digest"] == "important digest"

    def test_full_node_overwrites_summary(self, store: DocumentStore) -> None:
        # Create initial node
        node_data = self._make_node_data(summary="Old summary")
        store.upsert_node("doc.pdf", node_data, "turn_0001")

        # Full node upsert overwrites summary
        full_data = self._make_node_data(summary="New summary")
        store.upsert_node("doc.pdf", full_data, "turn_0002")

        state = json.loads(
            (store._node_path("doc.pdf", "node_001")).read_text("utf-8")
        )
        assert state["summary"] == "New summary"
        assert state["is_skeleton_latest"] is False
        assert state["last_seen_turn_id"] == "turn_0002"

    def test_skeleton_updates_positioning_fields(self, store: DocumentStore) -> None:
        node_data = self._make_node_data(start_index=1, end_index=10)
        store.upsert_node("doc.pdf", node_data, "turn_0001")

        skeleton_data = self._make_node_data(
            is_skeleton=True, start_index=5, end_index=20, parent_path="new/path"
        )
        store.upsert_node("doc.pdf", skeleton_data, "turn_0002")

        state = json.loads(
            (store._node_path("doc.pdf", "node_001")).read_text("utf-8")
        )
        assert state["start_index"] == 5
        assert state["end_index"] == 20
        assert state["parent_path"] == "new/path"


class TestUpdateNodeReadStatus:
    def _create_node(self, store: DocumentStore, doc_id: str = "doc.pdf") -> str:
        node_data = {
            "node_id": "node_001",
            "title": "Chapter 1",
            "start_index": 1,
            "end_index": 15,
            "summary": None,
            "is_skeleton": False,
            "parent_path": "",
        }
        return store.upsert_node(doc_id, node_data, "turn_0001")

    def test_discovered_to_reading(self, store: DocumentStore) -> None:
        self._create_node(store)
        store.update_node_read_status("doc.pdf", "node_001")

        state = json.loads(
            (store._node_path("doc.pdf", "node_001")).read_text("utf-8")
        )
        assert state["status"] == "reading"
        assert state["read_count"] == 1

    def test_reading_to_read_complete(self, store: DocumentStore) -> None:
        self._create_node(store)
        store.update_node_read_status("doc.pdf", "node_001")
        store.update_node_read_status("doc.pdf", "node_001")

        state = json.loads(
            (store._node_path("doc.pdf", "node_001")).read_text("utf-8")
        )
        assert state["status"] == "read_complete"
        assert state["read_count"] == 2

    def test_read_complete_stays(self, store: DocumentStore) -> None:
        self._create_node(store)
        store.update_node_read_status("doc.pdf", "node_001")
        store.update_node_read_status("doc.pdf", "node_001")
        store.update_node_read_status("doc.pdf", "node_001")  # third read

        state = json.loads(
            (store._node_path("doc.pdf", "node_001")).read_text("utf-8")
        )
        assert state["status"] == "read_complete"
        assert state["read_count"] == 3

    def test_nonexistent_node_is_noop(self, store: DocumentStore) -> None:
        # Should not raise
        store.update_node_read_status("doc.pdf", "nonexistent")
