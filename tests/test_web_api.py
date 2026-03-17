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


def _write_session_log(base_dir: Path, session_id: str, payload: dict) -> Path:
    path = base_dir / "logs" / "sessions" / f"{session_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(web_app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web_app, "SESSION_LOG_DIR", tmp_path / "logs" / "sessions")
    monkeypatch.setattr(web_app, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "logs" / "sessions").mkdir(parents=True, exist_ok=True)
    return TestClient(web_app.app)


def test_sessions_endpoints(client: TestClient, tmp_path: Path):
    _write_session_log(
        tmp_path,
        "20260308_120000",
        {
            "timestamp": "20260308_120000",
            "doc_name": "FC-LS.pdf",
            "query": "q",
            "total_turns": 2,
            "pages_retrieved": [1, 2],
            "answer": "a",
        },
    )

    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["sessions"][0]["id"] == "20260308_120000"

    detail = client.get("/api/sessions/20260308_120000")
    assert detail.status_code == 200
    assert detail.json()["doc_name"] == "FC-LS.pdf"


def test_conversations_group_by_context_session_id(client: TestClient, tmp_path: Path):
    _write_session_log(
        tmp_path,
        "20260308_120000",
        {
            "timestamp": "20260308_120000",
            "doc_name": "FC-LS.pdf",
            "query": "Q1",
            "answer": "A1",
            "context_session_id": "sess_1",
        },
    )
    _write_session_log(
        tmp_path,
        "20260308_120100",
        {
            "timestamp": "20260308_120100",
            "doc_name": "FC-LS.pdf",
            "query": "Q2",
            "answer": "A2",
            "title": "Merged chat",
            "context_session_id": "sess_1",
        },
    )

    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    payload = resp.json()

    assert len(payload["conversations"]) == 1
    conversation = payload["conversations"][0]
    assert conversation["id"] == "sess_1"
    assert conversation["entry_count"] == 2
    assert conversation["title"] == "Merged chat"
    assert conversation["query"] == "Q2"


def test_conversation_detail_returns_entries_in_time_order(client: TestClient, tmp_path: Path):
    _write_session_log(
        tmp_path,
        "20260308_120200",
        {
            "timestamp": "20260308_120200",
            "doc_name": "FC-LS.pdf",
            "query": "Q2",
            "answer": "A2",
            "answer_clean": "A2",
            "pages_retrieved": [8],
            "context_session_id": "sess_2",
        },
    )
    _write_session_log(
        tmp_path,
        "20260308_120100",
        {
            "timestamp": "20260308_120100",
            "doc_name": "FC-LS.pdf",
            "query": "Q1",
            "answer": "A1",
            "answer_clean": "A1",
            "pages_retrieved": [7],
            "context_session_id": "sess_2",
        },
    )

    resp = client.get("/api/conversations/sess_2")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["id"] == "sess_2"
    assert [entry["query"] for entry in payload["entries"]] == ["Q1", "Q2"]
    assert payload["entries"][0]["pages_retrieved"] == [7]
    assert payload["entries"][1]["pages_retrieved"] == [8]


def test_conversation_rename_updates_all_grouped_logs(client: TestClient, tmp_path: Path):
    _write_session_log(
        tmp_path,
        "20260308_120000",
        {
            "timestamp": "20260308_120000",
            "doc_name": "FC-LS.pdf",
            "query": "Q1",
            "answer": "A1",
            "context_session_id": "sess_rename",
        },
    )
    _write_session_log(
        tmp_path,
        "20260308_120100",
        {
            "timestamp": "20260308_120100",
            "doc_name": "FC-LS.pdf",
            "query": "Q2",
            "answer": "A2",
            "context_session_id": "sess_rename",
        },
    )

    resp = client.patch("/api/conversations/sess_rename/rename", json={"title": "Renamed"})
    assert resp.status_code == 200

    for session_id in ("20260308_120000", "20260308_120100"):
        payload = json.loads((tmp_path / "logs" / "sessions" / f"{session_id}.json").read_text("utf-8"))
        assert payload["title"] == "Renamed"


def test_conversation_delete_removes_grouped_logs(client: TestClient, tmp_path: Path):
    _write_session_log(
        tmp_path,
        "20260308_120000",
        {
            "timestamp": "20260308_120000",
            "doc_name": "FC-LS.pdf",
            "query": "Q1",
            "answer": "A1",
            "context_session_id": "sess_delete",
        },
    )
    _write_session_log(
        tmp_path,
        "20260308_120100",
        {
            "timestamp": "20260308_120100",
            "doc_name": "FC-LS.pdf",
            "query": "Q2",
            "answer": "A2",
            "context_session_id": "sess_delete",
        },
    )

    resp = client.delete("/api/conversations/sess_delete")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    assert list((tmp_path / "logs" / "sessions").glob("*.json")) == []


def test_old_session_without_context_session_id_still_forms_own_conversation(client: TestClient, tmp_path: Path):
    _write_session_log(
        tmp_path,
        "20260308_120000",
        {
            "timestamp": "20260308_120000",
            "doc_name": "FC-LS.pdf",
            "query": "Legacy",
            "answer": "Old answer",
        },
    )

    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    payload = resp.json()

    assert len(payload["conversations"]) == 1
    assert payload["conversations"][0]["id"] == "20260308_120000"
    detail = client.get("/api/conversations/20260308_120000")
    assert detail.status_code == 200
    assert detail.json()["entries"][0]["query"] == "Legacy"


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
        enable_context_reuse=None,
        context_session_id=None,
    ):
        assert context_session_id == "sess_prev"
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
                    "context_session_id": "sess_1",
                }
            )

        return RAGResponse(
            answer="a",
            answer_clean="a",
            citations=[],
            trace=[],
            pages_retrieved=[1],
            total_turns=1,
            context_session_id="sess_1",
        )

    monkeypatch.setattr(web_app, "ensure_document_ready", fake_ensure_document_ready)
    monkeypatch.setattr(web_app, "agentic_rag", fake_agentic_rag)

    with client.stream(
        "POST",
        "/api/qa/stream",
        json={"doc_name": "FC-LS.pdf", "query": "Q", "context_session_id": "sess_prev"},
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
    final_answer = next(event for event in events if event.get("type") == "final_answer")
    done_event = events[-1]
    assert final_answer["context_session_id"] == "sess_1"
    assert done_event["result"]["context_session_id"] == "sess_1"
