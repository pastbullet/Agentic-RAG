"""Tests for evidence writeback — verifying it is intentionally disabled.

Evidence writeback was removed because injecting citation-derived fragments
as "evidence" into context summaries degraded answer quality.  The context
reuse layer now follows a structure-only strategy.

These tests confirm that the agent loop no longer writes evidence files,
and that the response is still returned correctly.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.agent import loop
from src.context import ContextManager
from src.models import LLMResponse, RAGResponse, TokenUsage


def _make_adapter(answer: str):
    class _FakeAdapter:
        def __init__(self, provider: str, model: str):
            pass

        async def chat_with_tools(self, messages, tools):
            return LLMResponse(
                has_tool_calls=False,
                tool_calls=[],
                text=answer,
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                raw_message={"role": "assistant", "content": answer},
            )

        def make_tool_result_message(self, tool_call_id: str, result: dict) -> dict:
            return {}

    return _FakeAdapter


def _run_agent(answer: str, manager_factory) -> RAGResponse:
    with patch.object(loop, "ContextManager", manager_factory), patch.object(
        loop, "LLMAdapter", _make_adapter(answer)
    ), patch.object(loop, "load_system_prompt", lambda *_: "system"), patch.object(
        loop, "get_tool_schemas", lambda: []
    ), patch.object(loop, "_save_session", lambda *args, **kwargs: None):
        test_loop = asyncio.new_event_loop()
        try:
            return test_loop.run_until_complete(
                loop.agentic_rag(
                    query="What is the answer?",
                    doc_name="doc.pdf",
                    enable_context_reuse=False,
                )
            )
        finally:
            test_loop.close()
            asyncio.set_event_loop(asyncio.new_event_loop())


def test_no_evidence_files_written_after_citation(tmp_path: Path) -> None:
    """Evidence writeback is disabled — no evidence files should be created."""
    created: list[ContextManager] = []

    def manager_factory():
        manager = ContextManager(base_dir=str(tmp_path))
        created.append(manager)
        return manager

    answer = 'Alpha evidence <cite doc="doc.pdf" page="5"/>'
    response = _run_agent(answer, manager_factory)

    assert response.answer == answer
    session_dir = created[0].session_dir
    assert session_dir is not None
    # No evidence files should exist
    evidence_dir = session_dir / "evidences"
    if evidence_dir.exists():
        assert list(evidence_dir.glob("ev_*.json")) == []


def test_response_still_returned_without_writeback(tmp_path: Path) -> None:
    created: list[ContextManager] = []

    def manager_factory():
        manager = ContextManager(base_dir=str(tmp_path))
        created.append(manager)
        return manager

    answer = 'Stable answer <cite doc="doc.pdf" page="9"/>'
    response = _run_agent(answer, manager_factory)

    assert response.answer == answer
    assert len(response.citations) == 1
    assert response.citations[0].page == 9


# Feature: agentic-rag-completeness, Property 1: 无证据回写 — 响应正常返回
@given(
    page=st.integers(min_value=1, max_value=200),
    content=st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters=(" ",),
        ),
        min_size=3,
        max_size=20,
    ).map(lambda text: " ".join(text.split()) or "context"),
)
@settings(max_examples=100)
def test_property_1_no_evidence_writeback_response_ok(page: int, content: str) -> None:
    base_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    created: list[ContextManager] = []

    def manager_factory():
        manager = ContextManager(base_dir=str(base_dir))
        created.append(manager)
        return manager

    answer = f'{content} <cite doc="doc.pdf" page="{page}"/>'
    response = _run_agent(answer, manager_factory)

    assert isinstance(response, RAGResponse)
    assert response.answer == answer

    # No evidence files
    session_dir = created[0].session_dir
    if session_dir is not None:
        evidence_dir = session_dir / "evidences"
        if evidence_dir.exists():
            assert list(evidence_dir.glob("ev_*.json")) == []


# Feature: agentic-rag-completeness, Property 2: 无引用时响应也正常
@given(
    answer_text=st.text(min_size=1, max_size=50).map(lambda t: t.strip() or "ok"),
)
@settings(max_examples=100)
def test_property_2_no_citations_response_ok(answer_text: str) -> None:
    base_dir = Path(tempfile.mkdtemp()) / str(uuid4())

    def manager_factory():
        return ContextManager(base_dir=str(base_dir))

    response = _run_agent(answer_text, manager_factory)

    assert isinstance(response, RAGResponse)
    assert response.answer == answer_text
