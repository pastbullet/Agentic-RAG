"""Compatibility tests for ContextManager without TopicStore wiring."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context import ContextManager


def test_context_manager_flow_works_without_topic_store(tmp_path: Path) -> None:
    ctx = ContextManager(base_dir=str(tmp_path))
    session_id = ctx.create_session("doc.pdf")
    turn_id = ctx.create_turn("what is this?", "doc.pdf")

    ctx.finalize_turn(turn_id, {"answer": "ok", "citations": []})
    ctx.finalize_session()

    session_dir = tmp_path / session_id
    assert session_dir.exists()
    assert (session_dir / "topics").exists()
    assert (session_dir / "turns" / f"{turn_id}.json").exists()


def test_topics_directory_is_still_created(tmp_path: Path) -> None:
    ctx = ContextManager(base_dir=str(tmp_path))
    session_id = ctx.create_session("doc.pdf")

    assert (tmp_path / session_id / "topics").is_dir()


# Feature: agentic-rag-completeness, Property 9: TopicStore 移除后 ContextManager 正常工作
@given(n_turns=st.integers(min_value=1, max_value=8))
@settings(max_examples=100)
def test_property_9_context_manager_multi_turn_flow_without_topic_store(
    n_turns: int,
) -> None:
    base_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    ctx = ContextManager(base_dir=str(base_dir))
    session_id = ctx.create_session("doc.pdf")

    for turn in range(1, n_turns + 1):
        turn_id = ctx.create_turn(f"q{turn}", "doc.pdf")
        ctx.finalize_turn(turn_id, {"answer": f"a{turn}", "citations": []})

    ctx.finalize_session()

    session_dir = base_dir / session_id
    assert (session_dir / "topics").is_dir()
    assert len(list((session_dir / "turns").glob("turn_*.json"))) == n_turns
