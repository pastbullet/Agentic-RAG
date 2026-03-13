from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src.ingest.pipeline import ProcessResult  # noqa: E402
from src.models import RAGResponse  # noqa: E402

web_app = importlib.import_module("src.web.app")


def _parse_sse(raw: str) -> list[dict]:
    raw = raw.replace("\r\n", "\n")
    events: list[dict] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        data_lines = [line[5:].strip() for line in lines if line.startswith("data:")]
        if not data_lines:
            continue
        try:
            events.append(json.loads("\n".join(data_lines)))
        except json.JSONDecodeError:
            continue
    return events


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_app, "SESSION_LOG_DIR", tmp_path / "logs" / "sessions")
    monkeypatch.setattr(web_app, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "logs" / "sessions").mkdir(parents=True, exist_ok=True)
    return TestClient(web_app.app)


def test_sessions_endpoints(client: TestClient, tmp_path: Path):
    fp = tmp_path / "logs" / "sessions" / "20260308_120000.json"
    fp.write_text(
        json.dumps(
            {
                "timestamp": "20260308_120000",
                "doc_name": "FC-LS.pdf",
                "query": "q",
                "total_turns": 2,
                "pages_retrieved": [1, 2],
                "answer": "a",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["sessions"][0]["id"] == "20260308_120000"

    detail = client.get("/api/sessions/20260308_120000")
    assert detail.status_code == 200
    assert detail.json()["doc_name"] == "FC-LS.pdf"


def test_process_path_sync(client: TestClient, monkeypatch):
    def fake_process_document(pdf_path: str, force: bool = False, model: str | None = None):
        return ProcessResult(
            doc_name="new.pdf",
            doc_stem="new",
            pdf_path=pdf_path,
            page_index_json="data/out/new_page_index.json",
            chunks_dir="data/out/chunks_3/new",
            content_dir="output/docs/new/json",
            total_pages=3,
            index_built=True,
            structure_built=True,
            content_built=True,
            registered=True,
        )

    monkeypatch.setattr(web_app, "process_document", fake_process_document)

    resp = client.post("/api/process/path", json={"pdf_path": "/tmp/new.pdf"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["doc_name"] == "new.pdf"


def test_process_path_stream_event_order(client: TestClient, monkeypatch):
    def fake_process_document(
        pdf_path: str,
        force: bool = False,
        model: str | None = None,
        progress_callback=None,
    ):
        if progress_callback:
            progress_callback({"type": "stage_start", "stage": "index", "doc_name": "new.pdf"})
            progress_callback({"type": "stage_done", "stage": "index", "doc_name": "new.pdf"})
        return ProcessResult(
            doc_name="new.pdf",
            doc_stem="new",
            pdf_path=pdf_path,
            page_index_json="data/out/new_page_index.json",
            chunks_dir="data/out/chunks_3/new",
            content_dir="output/docs/new/json",
            total_pages=3,
            index_built=True,
            structure_built=True,
            content_built=True,
            registered=True,
        )

    monkeypatch.setattr(web_app, "process_document", fake_process_document)

    with client.stream(
        "POST",
        "/api/process/path/stream",
        json={"pdf_path": "/tmp/new.pdf"},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())

    events = _parse_sse(raw)
    types = [e.get("type") for e in events]
    assert "stage_start" in types
    assert "stage_done" in types
    assert types[-1] == "done"


def test_qa_stream_emits_turn_and_final(client: TestClient, monkeypatch):
    def fake_ensure_document_ready(
        doc: str | None = None,
        pdf: str | None = None,
        force: bool = False,
        model: str | None = None,
        progress_callback=None,
    ):
        return ProcessResult(
            doc_name=doc or "FC-LS.pdf",
            doc_stem="FC-LS",
            pdf_path="/tmp/FC-LS.pdf",
            page_index_json="data/out/FC-LS_page_index.json",
            chunks_dir="data/out/chunks_3/FC-LS",
            content_dir="output/docs/FC-LS/json",
            total_pages=210,
            index_built=False,
            structure_built=False,
            content_built=False,
            registered=False,
        )

    async def fake_agentic_rag(
        query: str,
        doc_name: str,
        model: str | None = None,
        prompt_file: str = "qa_system.txt",
        max_turns: int = 15,
        progress_callback=None,
        history_messages=None,
    ):
        if progress_callback:
            progress_callback({"type": "turn_start", "turn": 1, "max_turns": max_turns, "doc_name": doc_name})
            progress_callback(
                {
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "get_page_content",
                    "arguments": {"doc_name": doc_name, "pages": "1"},
                    "result_summary": "ok",
                }
            )
            progress_callback(
                {
                    "type": "final_answer",
                    "doc_name": doc_name,
                    "answer": "a",
                    "answer_clean": "a",
                    "citations": [],
                    "trace": [],
                    "pages_retrieved": [1],
                    "total_turns": 1,
                }
            )

        return RAGResponse(
            answer="a",
            answer_clean="a",
            citations=[],
            trace=[],
            pages_retrieved=[1],
            total_turns=1,
        )

    monkeypatch.setattr(web_app, "ensure_document_ready", fake_ensure_document_ready)
    monkeypatch.setattr(web_app, "agentic_rag", fake_agentic_rag)

    with client.stream(
        "POST",
        "/api/qa/stream",
        json={"doc_name": "FC-LS.pdf", "query": "Q"},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())

    events = _parse_sse(raw)
    types = [e.get("type") for e in events]
    assert "stage_done" in types
    assert "turn_start" in types
    assert "tool_call" in types
    assert "final_answer" in types
    assert types[-1] == "done"
