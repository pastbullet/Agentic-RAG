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
SESSION_LOG_DIR = PROJECT_ROOT / "logs" / "sessions"
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"

ProgressEmitter = Callable[[dict[str, Any]], None]
StreamWork = Callable[[ProgressEmitter], Awaitable[dict[str, Any] | None]]


class ProcessPathRequest(BaseModel):
    pdf_path: str = Field(min_length=1)
    force: bool = False
    model: str | None = None


class QARequest(BaseModel):
    query: str = Field(min_length=1)
    doc_name: str | None = None
    pdf_path: str | None = None
    history: list[dict[str, str]] | None = None
    force: bool = False
    model: str | None = None
    max_turns: int = 15


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
        SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        for fp in sorted(SESSION_LOG_DIR.glob("*.json"), reverse=True):
            payload = _load_session(fp)
            items.append(
                {
                    "id": fp.stem,
                    "timestamp": payload.get("timestamp", fp.stem),
                    "doc_name": payload.get("doc_name", ""),
                    "query": payload.get("query", ""),
                    "total_turns": payload.get("total_turns", 0),
                    "pages_retrieved": payload.get("pages_retrieved", []),
                }
            )
        return {"sessions": items}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        fp = _session_path(session_id)
        if not fp.exists():
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return _load_session(fp)

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
        result = await asyncio.to_thread(
            process_document,
            pdf_path=req.pdf_path,
            force=req.force,
            model=req.model,
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
    ) -> dict[str, Any]:
        saved_pdf = await _save_uploaded_pdf(file)
        logger.info("[web] process/upload start: %s", saved_pdf)
        result = await asyncio.to_thread(
            process_document,
            pdf_path=str(saved_pdf),
            force=force,
            model=model,
        )
        logger.info("[web] process/upload done: %s", result.doc_name)
        return {"ok": True, "saved_pdf": str(saved_pdf), "result": _process_result_to_dict(result)}

    @app.post("/api/qa")
    async def qa(req: QARequest) -> dict[str, Any]:
        if not req.doc_name and not req.pdf_path:
            raise HTTPException(status_code=400, detail="Either doc_name or pdf_path must be provided")

        logger.info("[web] qa start: doc=%s pdf=%s", req.doc_name, req.pdf_path)
        ready = await asyncio.to_thread(
            ensure_document_ready,
            doc=req.doc_name,
            pdf=req.pdf_path,
            force=req.force,
            model=req.model,
        )
        response = await agentic_rag(
            query=req.query,
            doc_name=ready.doc_name,
            model=req.model,
            max_turns=req.max_turns,
            history_messages=req.history,
        )
        logger.info("[web] qa done: doc=%s turns=%s", ready.doc_name, response.total_turns)
        return {
            "ok": True,
            "doc_name": ready.doc_name,
            "process": _process_result_to_dict(ready),
            "response": _rag_response_to_dict(response),
        }

    @app.post("/api/process/path/stream")
    async def process_path_stream(req: ProcessPathRequest) -> StreamingResponse:
        logger.info("[web] process/path/stream start: %s", req.pdf_path)

        async def work(emit: ProgressEmitter) -> dict[str, Any]:
            result = await asyncio.to_thread(
                process_document,
                pdf_path=req.pdf_path,
                force=req.force,
                model=req.model,
                progress_callback=emit,
            )
            return {"process": _process_result_to_dict(result)}

        return StreamingResponse(_stream_events(work), media_type="text/event-stream")

    @app.post("/api/process/upload/stream")
    async def process_upload_stream(
        file: UploadFile = File(...),
        force: bool = Form(False),
        model: str | None = Form(None),
    ) -> StreamingResponse:
        saved_pdf = await _save_uploaded_pdf(file)
        logger.info("[web] process/upload/stream start: %s", saved_pdf)

        async def work(emit: ProgressEmitter) -> dict[str, Any]:
            emit({"type": "stage_start", "stage": "upload", "pdf_path": str(saved_pdf)})
            emit({"type": "stage_done", "stage": "upload", "pdf_path": str(saved_pdf)})
            result = await asyncio.to_thread(
                process_document,
                pdf_path=str(saved_pdf),
                force=force,
                model=model,
                progress_callback=emit,
            )
            return {"saved_pdf": str(saved_pdf), "process": _process_result_to_dict(result)}

        return StreamingResponse(_stream_events(work), media_type="text/event-stream")

    @app.post("/api/qa/stream")
    async def qa_stream(req: QARequest) -> StreamingResponse:
        if not req.doc_name and not req.pdf_path:
            raise HTTPException(status_code=400, detail="Either doc_name or pdf_path must be provided")

        logger.info("[web] qa/stream start: doc=%s pdf=%s", req.doc_name, req.pdf_path)

        async def work(emit: ProgressEmitter) -> dict[str, Any]:
            ready = await asyncio.to_thread(
                ensure_document_ready,
                doc=req.doc_name,
                pdf=req.pdf_path,
                force=req.force,
                model=req.model,
                progress_callback=emit,
            )
            emit({"type": "stage_done", "stage": "ensure_document_ready", "doc_name": ready.doc_name})

            response = await agentic_rag(
                query=req.query,
                doc_name=ready.doc_name,
                model=req.model,
                max_turns=req.max_turns,
                progress_callback=emit,
                history_messages=req.history,
            )
            logger.info("[web] qa/stream done: doc=%s turns=%s", ready.doc_name, response.total_turns)
            return {
                "doc_name": ready.doc_name,
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
