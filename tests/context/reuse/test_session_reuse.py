"""Tests for session reuse — unit tests + Hypothesis property tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context import ContextManager


# ── Unit Tests ──────────────────────────────────────────────


def test_load_session_restores_turn_sequence(tmp_path) -> None:
    ctx = ContextManager(base_dir=str(tmp_path))
    session_id = ctx.create_session("doc.pdf")
    ctx.create_turn("q1", "doc.pdf")
    ctx.create_turn("q2", "doc.pdf")
    ctx.finalize_session()

    restored = ContextManager(base_dir=str(tmp_path))
    restored.load_session(session_id)
    turn_id = restored.create_turn("q3", "doc.pdf")

    assert turn_id == "turn_0003"
    assert restored.session_dir is not None
    assert restored.session_dir.name == session_id


def test_load_session_missing_raises(tmp_path) -> None:
    ctx = ContextManager(base_dir=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        ctx.load_session("sess_missing")


# ── Hypothesis Property Tests ───────────────────────────────


# Feature: context-reuse-enhancement, Property 15: 会话复用加载
@given(
    n_turns=st.integers(min_value=1, max_value=6),
)
@settings(max_examples=100)
def test_property_15_session_reuse_loading(n_turns: int) -> None:
    base_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    ctx = ContextManager(base_dir=str(base_dir))
    session_id = ctx.create_session("doc.pdf")

    for i in range(1, n_turns + 1):
        ctx.create_turn(f"q{i}", "doc.pdf")
    ctx.finalize_session()

    restored = ContextManager(base_dir=str(base_dir))
    restored.load_session(session_id)

    # turn_seq should be restored to n_turns
    next_turn = restored.create_turn("next_q", "doc.pdf")
    expected_id = f"turn_{n_turns + 1:04d}"
    assert next_turn == expected_id

    # session_dir should be set
    assert restored.session_dir is not None
    assert restored.session_dir.name == session_id
