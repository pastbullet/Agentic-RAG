"""Unit tests for EvidenceStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.context.stores.evidence_store import EvidenceStore


@pytest.fixture
def evidences_dir(tmp_path: Path) -> Path:
    return tmp_path / "evidences"


class TestAddEvidence:
    def test_creates_evidence_file(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        store.add_evidence("ev_000001", "FC-LS.pdf", 7, "Some evidence text", "turn_0001")

        data = json.loads((evidences_dir / "ev_000001.json").read_text("utf-8"))
        assert data["evidence_id"] == "ev_000001"
        assert data["source_doc"] == "FC-LS.pdf"
        assert data["source_page"] == 7
        assert data["content"] == "Some evidence text"
        assert data["extracted_in_turn"] == "turn_0001"
        assert data["used_in_turns"] == []

    def test_creates_directory_if_missing(self, evidences_dir: Path) -> None:
        assert not evidences_dir.exists()
        store = EvidenceStore(evidences_dir)
        store.add_evidence("ev_000001", "doc.pdf", 1, "content", "turn_0001")
        assert evidences_dir.is_dir()

    def test_multiple_evidences(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        store.add_evidence("ev_000001", "doc.pdf", 1, "first", "turn_0001")
        store.add_evidence("ev_000002", "doc.pdf", 2, "second", "turn_0001")

        assert (evidences_dir / "ev_000001.json").exists()
        assert (evidences_dir / "ev_000002.json").exists()


class TestAddUsage:
    def test_appends_turn_id(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        store.add_evidence("ev_000001", "doc.pdf", 1, "content", "turn_0001")

        store.add_usage("ev_000001", "turn_0002")
        store.add_usage("ev_000001", "turn_0003")

        data = json.loads((evidences_dir / "ev_000001.json").read_text("utf-8"))
        assert data["used_in_turns"] == ["turn_0002", "turn_0003"]

    def test_noop_for_missing_evidence(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        # Should not raise
        store.add_usage("ev_999999", "turn_0001")


class TestQueryBySource:
    def test_returns_matching_evidences(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        store.add_evidence("ev_000001", "FC-LS.pdf", 7, "evidence A", "turn_0001")
        store.add_evidence("ev_000002", "FC-LS.pdf", 7, "evidence B", "turn_0001")
        store.add_evidence("ev_000003", "FC-LS.pdf", 8, "evidence C", "turn_0001")
        store.add_evidence("ev_000004", "other.pdf", 7, "evidence D", "turn_0001")

        results = store.query_by_source("FC-LS.pdf", 7)
        assert len(results) == 2
        ids = {r["evidence_id"] for r in results}
        assert ids == {"ev_000001", "ev_000002"}

    def test_returns_empty_for_no_match(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        store.add_evidence("ev_000001", "FC-LS.pdf", 7, "content", "turn_0001")

        results = store.query_by_source("FC-LS.pdf", 99)
        assert results == []

    def test_returns_empty_for_missing_directory(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        results = store.query_by_source("FC-LS.pdf", 7)
        assert results == []

    def test_requires_both_doc_and_page_match(self, evidences_dir: Path) -> None:
        store = EvidenceStore(evidences_dir)
        store.add_evidence("ev_000001", "FC-LS.pdf", 7, "content", "turn_0001")

        # Wrong doc
        assert store.query_by_source("other.pdf", 7) == []
        # Wrong page
        assert store.query_by_source("FC-LS.pdf", 8) == []
        # Both match
        assert len(store.query_by_source("FC-LS.pdf", 7)) == 1
