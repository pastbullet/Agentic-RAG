"""Unit tests for SessionStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.context.stores.session_store import SessionStore


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    return tmp_path / "sess_20250101_120000"


class TestCreateSession:
    def test_creates_subdirectories(self, session_dir: Path) -> None:
        store = SessionStore(session_dir)
        store.create_session("sess_20250101_120000", "FC-LS.pdf")

        for subdir in ("turns", "documents", "evidences", "topics"):
            assert (session_dir / subdir).is_dir()

    def test_writes_session_json(self, session_dir: Path) -> None:
        store = SessionStore(session_dir)
        store.create_session("sess_20250101_120000", "FC-LS.pdf")

        data = json.loads((session_dir / "session.json").read_text("utf-8"))
        assert data["session_id"] == "sess_20250101_120000"
        assert data["doc_name"] == "FC-LS.pdf"
        assert data["status"] == "active"
        assert data["turns"] == []
        assert "created_at" in data

    def test_created_at_is_iso_utc(self, session_dir: Path) -> None:
        store = SessionStore(session_dir)
        store.create_session("sess_20250101_120000", "FC-LS.pdf")

        data = json.loads((session_dir / "session.json").read_text("utf-8"))
        # Should be parseable and contain UTC offset info
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(data["created_at"])
        assert dt.tzinfo is not None


class TestAddTurn:
    def test_appends_turn_id(self, session_dir: Path) -> None:
        store = SessionStore(session_dir)
        store.create_session("sess_20250101_120000", "FC-LS.pdf")

        store.add_turn("turn_0001")
        store.add_turn("turn_0002")

        data = json.loads((session_dir / "session.json").read_text("utf-8"))
        assert data["turns"] == ["turn_0001", "turn_0002"]


class TestFinalize:
    def test_sets_status_completed(self, session_dir: Path) -> None:
        store = SessionStore(session_dir)
        store.create_session("sess_20250101_120000", "FC-LS.pdf")

        store.finalize()

        data = json.loads((session_dir / "session.json").read_text("utf-8"))
        assert data["status"] == "completed"
