"""本地调试 Web 服务（FastAPI + 原生前端 + SSE 风格流式进度）。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.agent.loop import agentic_rag
from src.ingest.pipeline import ProcessResult, ensure_document_ready, process_document, resolve_pdf_for_doc
from src.tools.registry import get_doc_config, get_registered_documents, is_document_processed

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_LOG_DIR = PROJECT_ROOT / "data" / "sessions"
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"

ProgressEmitter = Callable[[dict[str, Any]], None]
StreamWork = Callable[[ProgressEmitter], Awaitable[dict[str, Any] | None]]


class ProcessPathRequest(BaseModel):
    pdf_path: str = Field(min_length=1)
    force: bool = False
    model: str | None = None
    toc_check_pages: int | None = None
    max_pages_per_node: int | None = None
    max_tokens_per_node: int | None = None
    if_add_node_id: str | None = None
    if_add_node_summary: str | None = None
    if_add_node_text: str | None = None
    if_add_doc_description: str | None = None
    structure_max_limit: int | None = None
    structure_chunk_max_limit: int | None = None
    content_chunk_size: int | None = None


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class QARequest(BaseModel):
    query: str = Field(min_length=1)
    doc_name: str | None = None
    pdf_path: str | None = None
    history: list[dict[str, str]] | None = None
    force: bool = False
    model: str | None = None
    prompt_file: str | None = None
    max_turns: int = 20
    toc_check_pages: int | None = None
    max_pages_per_node: int | None = None
    max_tokens_per_node: int | None = None
    if_add_node_id: str | None = None
    if_add_node_summary: str | None = None
    if_add_node_text: str | None = None
    if_add_doc_description: str | None = None
    structure_max_limit: int | None = None
    structure_chunk_max_limit: int | None = None
    content_chunk_size: int | None = None
    enable_context_reuse: bool | None = None
    context_session_id: str | None = None


def _normalize_doc_name(doc_name: str) -> str:
    name = Path(doc_name).name.strip()
    if not name:
        raise ValueError("Document name is empty")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def _to_abs(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _process_result_to_dict(result: ProcessResult) -> dict[str, Any]:
    return asdict(result)


def _rag_response_to_dict(response: Any) -> dict[str, Any]:
    return {
        "answer": response.answer,
        "answer_clean": response.answer_clean,
        "citations": [c.model_dump() for c in response.citations],
        "trace": [t.model_dump() for t in response.trace],
        "pages_retrieved": response.pages_retrieved,
        "total_turns": response.total_turns,
        "context_session_id": getattr(response, "context_session_id", None),
    }


def _session_path(session_id: str) -> Path:
    safe_id = Path(session_id).name
    if safe_id.endswith(".json"):
        safe_id = safe_id[:-5]
    return SESSION_LOG_DIR / f"{safe_id}.json"


def _load_session(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse session file: {path.name}") from exc


def _session_timestamp(session_id: str, payload: dict[str, Any]) -> str:
    value = payload.get("timestamp", session_id)
    return value if isinstance(value, str) and value else session_id


def _conversation_id_for_session(session_id: str, payload: dict[str, Any]) -> str:
    context_session_id = payload.get("context_session_id")
    if isinstance(context_session_id, str) and context_session_id.strip():
        return context_session_id.strip()
    return session_id


def _iter_session_records() -> list[dict[str, Any]]:
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for fp in sorted(SESSION_LOG_DIR.glob("*.json"), reverse=True):
        payload = _load_session(fp)
        session_id = fp.stem
        records.append(
            {
                "id": session_id,
                "path": fp,
                "payload": payload,
                "timestamp": _session_timestamp(session_id, payload),
                "conversation_id": _conversation_id_for_session(session_id, payload),
            }
        )
    return records


def _build_conversation_summary(conversation_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: item["timestamp"])
    latest = ordered[-1]
    latest_payload = latest["payload"]

    title = ""
    for item in reversed(ordered):
        value = item["payload"].get("title", "")
        if isinstance(value, str) and value.strip():
            title = value.strip()
            break

    latest_query = latest_payload.get("query", "")
    latest_doc_name = latest_payload.get("doc_name", "")
    latest_context_session_id = latest_payload.get("context_session_id")
    latest_pages = latest_payload.get("pages_retrieved", [])
    latest_turns = latest_payload.get("total_turns", 0)

    return {
        "id": conversation_id,
        "context_session_id": latest_context_session_id if isinstance(latest_context_session_id, str) else None,
        "timestamp": latest["timestamp"],
        "doc_name": latest_doc_name if isinstance(latest_doc_name, str) else "",
        "query": latest_query if isinstance(latest_query, str) else "",
        "title": title,
        "total_turns": latest_turns if isinstance(latest_turns, int) else 0,
        "pages_retrieved": latest_pages if isinstance(latest_pages, list) else [],
        "entry_count": len(ordered),
        "session_ids": [item["id"] for item in ordered],
    }


def _build_conversation_entry(record: dict[str, Any]) -> dict[str, Any]:
    payload = record["payload"]
    return {
        "id": record["id"],
        "timestamp": record["timestamp"],
        "doc_name": payload.get("doc_name", ""),
        "query": payload.get("query", ""),
        "title": payload.get("title", ""),
        "answer": payload.get("answer", ""),
        "answer_clean": payload.get("answer_clean", ""),
        "citations": payload.get("citations", []),
        "trace": payload.get("trace", []),
        "pages_retrieved": payload.get("pages_retrieved", []),
        "total_turns": payload.get("total_turns", 0),
        "context_session_id": payload.get("context_session_id"),
    }


def _get_conversation_records(conversation_id: str) -> list[dict[str, Any]]:
    safe_id = Path(conversation_id).name
    records = [
        record
        for record in _iter_session_records()
        if record["conversation_id"] == safe_id
    ]
    if not records:
        raise HTTPException(status_code=404, detail=f"Conversation not found: {safe_id}")
    return sorted(records, key=lambda item: item["timestamp"])


def _resolve_pdf_file(doc_name: str) -> Path:
    cfg = get_doc_config(doc_name)
    if "error" not in cfg:
        pdf_path = cfg.get("pdf_path")
        if isinstance(pdf_path, str) and pdf_path:
            candidate = _to_abs(pdf_path)
            if candidate.exists() and candidate.is_file():
                return candidate

    # 更稳的回退：优先按项目根目录 data/raw 定位
    raw_candidate = PROJECT_ROOT / "data" / "raw" / doc_name
    if raw_candidate.exists() and raw_candidate.is_file():
        return raw_candidate.resolve()

    try:
        return resolve_pdf_for_doc(doc_name)
    except Exception as exc:  # pragma: no cover - errors are mapped to 404
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _sse_pack(payload: dict[str, Any]) -> str:
    return f"event: message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_process_options(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}

    keys = (
        "toc_check_pages",
        "max_pages_per_node",
        "max_tokens_per_node",
        "if_add_node_id",
        "if_add_node_summary",
        "if_add_node_text",
        "if_add_doc_description",
        "structure_max_limit",
        "structure_chunk_max_limit",
        "content_chunk_size",
    )
    options: dict[str, Any] = {}
    for key in keys:
        if isinstance(payload, dict):
            value = payload.get(key)
        else:
            value = getattr(payload, key, None)
        if value is None:
            continue
        options[key] = value
    if "structure_chunk_max_limit" in options:
        options["structure_max_limit"] = options.pop("structure_chunk_max_limit")
    return options


def _parse_process_options_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid process_options_json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="process_options_json must be a JSON object")
    return _extract_process_options(payload)


async def _stream_events(work: StreamWork) -> Any:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    finished = False

    def emit(payload: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    async def runner() -> None:
        nonlocal finished
        try:
            result = await work(emit)
            emit({"type": "done", "result": result or {}})
        except Exception as exc:
            logger.exception("Stream worker failed")
            emit({"type": "error", "message": str(exc)})
            emit({"type": "done", "ok": False})
        finally:
            finished = True

    task = asyncio.create_task(runner())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.5)
            except asyncio.TimeoutError:
                if finished and queue.empty():
                    break
                yield _sse_pack({"type": "heartbeat", "ts": datetime.now().isoformat(timespec="seconds")})
                continue

            yield _sse_pack(event)
            if event.get("type") == "done":
                break
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic RAG Debug UI")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/sessions")
    async def list_sessions() -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for record in _iter_session_records():
            payload = record["payload"]
            items.append(
                {
                    "id": record["id"],
                    "timestamp": record["timestamp"],
                    "doc_name": payload.get("doc_name", ""),
                    "query": payload.get("query", ""),
                    "title": payload.get("title", ""),
                    "total_turns": payload.get("total_turns", 0),
                    "pages_retrieved": payload.get("pages_retrieved", []),
                    "context_session_id": payload.get("context_session_id"),
                }
            )
        return {"sessions": items}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        fp = _session_path(session_id)
        if not fp.exists():
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return _load_session(fp)

    @app.patch("/api/sessions/{session_id}/rename")
    async def rename_session(session_id: str, req: RenameRequest) -> dict[str, Any]:
        fp = _session_path(session_id)
        if not fp.exists():
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        payload = _load_session(fp)
        payload["title"] = req.title.strip()
        fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "id": session_id, "title": payload["title"]}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, Any]:
        fp = _session_path(session_id)
        if not fp.exists():
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        fp.unlink()
        return {"ok": True, "id": session_id}

    @app.get("/api/conversations")
    async def list_conversations() -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in _iter_session_records():
            grouped.setdefault(record["conversation_id"], []).append(record)

        conversations = [
            _build_conversation_summary(conversation_id, records)
            for conversation_id, records in grouped.items()
        ]
        conversations.sort(key=lambda item: item["timestamp"], reverse=True)
        return {"conversations": conversations}

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        records = _get_conversation_records(conversation_id)
        summary = _build_conversation_summary(records[0]["conversation_id"], records)
        summary["entries"] = [_build_conversation_entry(record) for record in records]
        return summary

    @app.patch("/api/conversations/{conversation_id}/rename")
    async def rename_conversation(conversation_id: str, req: RenameRequest) -> dict[str, Any]:
        records = _get_conversation_records(conversation_id)
        title = req.title.strip()
        for record in records:
            payload = record["payload"]
            payload["title"] = title
            record["path"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "id": Path(conversation_id).name, "title": title}

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str) -> dict[str, Any]:
        records = _get_conversation_records(conversation_id)
        for record in records:
            record["path"].unlink()
        return {"ok": True, "id": Path(conversation_id).name, "deleted": len(records)}

    @app.get("/api/docs")
    async def list_docs() -> dict[str, Any]:
        docs: list[dict[str, Any]] = []
        for doc_name, cfg in sorted(get_registered_documents().items()):
            docs.append(
                {
                    "doc_name": doc_name,
                    "chunks_dir": cfg.get("chunks_dir", ""),
                    "content_dir": cfg.get("content_dir", ""),
                    "total_pages": cfg.get("total_pages", 0),
                    "pdf_path": cfg.get("pdf_path", ""),
                    "processed": is_document_processed(doc_name),
                }
            )
        return {"docs": docs}

    @app.get("/api/pdf/{doc_name}")
    async def get_pdf(doc_name: str) -> FileResponse:
        normalized = _normalize_doc_name(doc_name)
        pdf_file = _resolve_pdf_file(normalized)
        if not pdf_file.exists() or not pdf_file.is_file():
            raise HTTPException(status_code=404, detail=f"PDF not found for {normalized}")
        return FileResponse(
            pdf_file,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline"},
        )

    @app.post("/api/process/path")
    async def process_path(req: ProcessPathRequest) -> dict[str, Any]:
        logger.info("[web] process/path start: %s", req.pdf_path)
        process_options = _extract_process_options(req)
        result = await asyncio.to_thread(
            process_document,
            pdf_path=req.pdf_path,
            force=req.force,
            model=req.model,
            **process_options,
        )
        logger.info("[web] process/path done: %s", result.doc_name)
        return {"ok": True, "result": _process_result_to_dict(result)}

    async def _save_uploaded_pdf(file: UploadFile) -> Path:
        filename = Path(file.filename or "uploaded.pdf").name
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are supported")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = UPLOAD_DIR / filename
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = UPLOAD_DIR / f"{target.stem}_{stamp}{target.suffix}"

        data = await file.read()
        target.write_bytes(data)
        return target

    @app.post("/api/process/upload")
    async def process_upload(
        file: UploadFile = File(...),
        force: bool = Form(False),
        model: str | None = Form(None),
        process_options_json: str | None = Form(None),
    ) -> dict[str, Any]:
        saved_pdf = await _save_uploaded_pdf(file)
        logger.info("[web] process/upload start: %s", saved_pdf)
        process_options = _parse_process_options_json(process_options_json)
        result = await asyncio.to_thread(
            process_document,
            pdf_path=str(saved_pdf),
            force=force,
            model=model,
            **process_options,
        )
        logger.info("[web] process/upload done: %s", result.doc_name)
        return {"ok": True, "saved_pdf": str(saved_pdf), "result": _process_result_to_dict(result)}

    @app.post("/api/qa")
    async def qa(req: QARequest) -> dict[str, Any]:
        if not req.doc_name and not req.pdf_path:
            raise HTTPException(status_code=400, detail="Either doc_name or pdf_path must be provided")

        logger.info("[web] qa start: doc=%s pdf=%s", req.doc_name, req.pdf_path)
        process_options = _extract_process_options(req)
        ready = await asyncio.to_thread(
            ensure_document_ready,
            doc=req.doc_name,
            pdf=req.pdf_path,
            force=req.force,
            model=req.model,
            **process_options,
        )
        response = await agentic_rag(
            query=req.query,
            doc_name=ready.doc_name,
            model=req.model,
            prompt_file=req.prompt_file or "qa_system.txt",
            max_turns=req.max_turns,
            history_messages=req.history,
            enable_context_reuse=req.enable_context_reuse,
            context_session_id=req.context_session_id,
        )
        logger.info("[web] qa done: doc=%s turns=%s", ready.doc_name, response.total_turns)
        return {
            "ok": True,
            "doc_name": ready.doc_name,
            "context_session_id": response.context_session_id,
            "process": _process_result_to_dict(ready),
            "response": _rag_response_to_dict(response),
        }

    @app.post("/api/process/path/stream")
    async def process_path_stream(req: ProcessPathRequest) -> StreamingResponse:
        logger.info("[web] process/path/stream start: %s", req.pdf_path)
        process_options = _extract_process_options(req)

        async def work(emit: ProgressEmitter) -> dict[str, Any]:
            result = await asyncio.to_thread(
                process_document,
                pdf_path=req.pdf_path,
                force=req.force,
                model=req.model,
                **process_options,
                progress_callback=emit,
            )
            return {"process": _process_result_to_dict(result)}

        return StreamingResponse(_stream_events(work), media_type="text/event-stream")

    @app.post("/api/process/upload/stream")
    async def process_upload_stream(
        file: UploadFile = File(...),
        force: bool = Form(False),
        model: str | None = Form(None),
        process_options_json: str | None = Form(None),
    ) -> StreamingResponse:
        saved_pdf = await _save_uploaded_pdf(file)
        logger.info("[web] process/upload/stream start: %s", saved_pdf)
        process_options = _parse_process_options_json(process_options_json)

        async def work(emit: ProgressEmitter) -> dict[str, Any]:
            emit({"type": "stage_start", "stage": "upload", "pdf_path": str(saved_pdf)})
            emit({"type": "stage_done", "stage": "upload", "pdf_path": str(saved_pdf)})
            result = await asyncio.to_thread(
                process_document,
                pdf_path=str(saved_pdf),
                force=force,
                model=model,
                **process_options,
                progress_callback=emit,
            )
            return {"saved_pdf": str(saved_pdf), "process": _process_result_to_dict(result)}

        return StreamingResponse(_stream_events(work), media_type="text/event-stream")

    @app.post("/api/qa/stream")
    async def qa_stream(req: QARequest) -> StreamingResponse:
        if not req.doc_name and not req.pdf_path:
            raise HTTPException(status_code=400, detail="Either doc_name or pdf_path must be provided")

        logger.info("[web] qa/stream start: doc=%s pdf=%s", req.doc_name, req.pdf_path)
        process_options = _extract_process_options(req)

        async def work(emit: ProgressEmitter) -> dict[str, Any]:
            ready = await asyncio.to_thread(
                ensure_document_ready,
                doc=req.doc_name,
                pdf=req.pdf_path,
                force=req.force,
                model=req.model,
                **process_options,
                progress_callback=emit,
            )
            emit({"type": "stage_done", "stage": "ensure_document_ready", "doc_name": ready.doc_name})

            response = await agentic_rag(
                query=req.query,
                doc_name=ready.doc_name,
                model=req.model,
                prompt_file=req.prompt_file or "qa_system.txt",
                max_turns=req.max_turns,
                progress_callback=emit,
                history_messages=req.history,
                enable_context_reuse=req.enable_context_reuse,
                context_session_id=req.context_session_id,
            )
            logger.info("[web] qa/stream done: doc=%s turns=%s", ready.doc_name, response.total_turns)
            return {
                "doc_name": ready.doc_name,
                "context_session_id": response.context_session_id,
                "process": _process_result_to_dict(ready),
                "response": _rag_response_to_dict(response),
            }

        return StreamingResponse(_stream_events(work), media_type="text/event-stream")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=True)
