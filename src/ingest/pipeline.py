"""Document ingestion orchestration for end-to-end Agentic RAG pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.tools.registry import get_doc_config, is_document_processed, register_document

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class ProcessResult:
    """Result of a document processing run."""

    doc_name: str
    doc_stem: str
    pdf_path: str
    page_index_json: str
    chunks_dir: str
    content_dir: str
    total_pages: int
    index_built: bool = False
    structure_built: bool = False
    content_built: bool = False
    registered: bool = False


def _safe_doc_stem(stem: str) -> str:
    clean = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in stem.strip())
    return clean or "document"


def _canonical_doc_name(name_or_path: str) -> str:
    name = Path(name_or_path).name.strip()
    if not name:
        raise ValueError("Document name is empty")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def _to_registry_path(path: Path, base: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _read_total_pages_from_content_dir(content_dir: Path) -> int:
    max_page = 0
    for fp in sorted(content_dir.glob("content_*.json")):
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
            end_page = payload.get("end_page")
            if isinstance(end_page, int):
                max_page = max(max_page, end_page)
        except (json.JSONDecodeError, OSError):
            pass

        # fallback to filename pattern content_{start}_{end}.json
        parts = fp.stem.split("_")
        if len(parts) >= 3:
            try:
                max_page = max(max_page, int(parts[2]))
            except ValueError:
                pass

    if max_page <= 0:
        raise RuntimeError(f"Failed to infer total_pages from content dir: {content_dir}")
    return max_page


def _structure_ready(chunks_dir: Path) -> bool:
    manifest = chunks_dir / "manifest.json"
    if not manifest.exists():
        return False
    return any(chunks_dir.glob("part_*.json"))


def _content_ready(content_dir: Path) -> bool:
    if not content_dir.is_dir():
        return False
    return any(content_dir.glob("content_*.json"))


def _load_page_index_builder() -> Callable[..., dict[str, Any]]:
    from page_index import page_index

    return page_index


def _load_structure_helpers():
    from structure_chunker import (
        chunk_document_structure,
        load_root_from_page_index_json,
        save_parts_to_folder,
    )

    return load_root_from_page_index_json, chunk_document_structure, save_parts_to_folder


def _load_content_builder() -> Callable[..., list[Path]]:
    from build_content_db import build_content_db

    return build_content_db


def _emit_progress(progress_callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except Exception:
        logger.exception("progress_callback failed in ingest pipeline")


def resolve_pdf_for_doc(doc_name_or_path: str) -> Path:
    """Resolve a document to a concrete PDF path.

    Resolution order:
    1) existing local path
    2) data/raw/<doc_name>
    3) recursive workspace search by file name
    """

    raw_input = doc_name_or_path.strip()
    if not raw_input:
        raise ValueError("Empty document input")

    maybe_path = Path(raw_input).expanduser()
    if maybe_path.exists() and maybe_path.is_file():
        if maybe_path.suffix.lower() != ".pdf":
            raise ValueError(f"Resolved path is not a PDF: {maybe_path}")
        return maybe_path.resolve()

    doc_name = _canonical_doc_name(raw_input)

    raw_candidate = (Path.cwd() / "data" / "raw" / doc_name)
    if raw_candidate.exists() and raw_candidate.is_file():
        return raw_candidate.resolve()

    matches: list[Path] = []
    for p in Path.cwd().rglob("*.pdf"):
        if p.name.lower() == doc_name.lower() and p.is_file():
            matches.append(p.resolve())

    if len(matches) == 1:
        return matches[0]

    if not matches:
        raise FileNotFoundError(
            f"Unable to locate PDF for '{doc_name}'. Checked direct path, data/raw, and recursive workspace search."
        )

    sample = "\n".join(f"- {m}" for m in matches[:10])
    raise ValueError(
        f"Multiple PDF matches found for '{doc_name}'. Please pass --pdf explicitly.\n{sample}"
    )


def _result_from_existing(doc_name: str, pdf_path: str | None = None) -> ProcessResult:
    config = get_doc_config(doc_name)
    if "error" in config:
        raise RuntimeError(f"Document is not registered: {doc_name}")

    chunks_dir = Path(str(config["chunks_dir"]))
    content_dir = Path(str(config["content_dir"]))
    doc_stem = _safe_doc_stem(Path(doc_name).stem)
    page_index_json = Path("data/out") / f"{doc_stem}_page_index.json"

    resolved_pdf = pdf_path or str(config.get("pdf_path", ""))
    if not resolved_pdf:
        resolved_pdf = doc_name

    return ProcessResult(
        doc_name=doc_name,
        doc_stem=doc_stem,
        pdf_path=resolved_pdf,
        page_index_json=str(page_index_json),
        chunks_dir=str(chunks_dir),
        content_dir=str(content_dir),
        total_pages=int(config["total_pages"]),
        index_built=False,
        structure_built=False,
        content_built=False,
        registered=False,
    )


def process_document(
    pdf_path: str,
    force: bool = False,
    model: str | None = None,
    toc_check_pages: int | None = None,
    max_pages_per_node: int | None = None,
    max_tokens_per_node: int | None = None,
    if_add_node_id: str | None = None,
    if_add_node_summary: str | None = None,
    if_add_node_text: str | None = None,
    if_add_doc_description: str | None = None,
    structure_max_limit: int = 30000,
    content_chunk_size: int = 20,
    progress_callback: ProgressCallback | None = None,
) -> ProcessResult:
    """Process one PDF end-to-end and register it for QA."""
    source_pdf = Path(pdf_path).expanduser().resolve()
    try:
        if not source_pdf.exists() or not source_pdf.is_file():
            raise FileNotFoundError(f"PDF not found: {source_pdf}")
        if source_pdf.suffix.lower() != ".pdf":
            raise ValueError(f"Input must be a PDF: {source_pdf}")

        doc_name = _canonical_doc_name(source_pdf.name)
        _emit_progress(
            progress_callback,
            {"type": "stage_start", "stage": "ingest", "doc_name": doc_name},
        )

        # Fast path: registered and all artifacts available
        if not force and is_document_processed(doc_name):
            logger.info("Document already processed, skipping rebuild: %s", doc_name)
            _emit_progress(
                progress_callback,
                {
                    "type": "stage_done",
                    "stage": "ingest",
                    "doc_name": doc_name,
                    "skipped": True,
                },
            )
            return _result_from_existing(doc_name, pdf_path=str(source_pdf))

        project_root = Path.cwd().resolve()
        doc_stem = _safe_doc_stem(source_pdf.stem)

        page_index_json = project_root / "data" / "out" / f"{doc_stem}_page_index.json"
        chunks_dir = project_root / "data" / "out" / "chunks_3" / doc_stem
        content_root = project_root / "output" / "docs" / doc_stem
        content_dir = content_root / "json"

        index_built = False
        structure_built = False
        content_built = False

        structure_needs_build = force or not _structure_ready(chunks_dir)
        content_needs_build = force or not _content_ready(content_dir)
        index_needs_build = force or not page_index_json.exists() or structure_needs_build

        _emit_progress(progress_callback, {"type": "stage_start", "stage": "index", "doc_name": doc_name})
        if index_needs_build:
            logger.info("[ingest] Building base index: %s", source_pdf)
            page_index = _load_page_index_builder()
            kwargs: dict[str, Any] = {}
            if model:
                kwargs["model"] = model
            if toc_check_pages is not None:
                kwargs["toc_check_page_num"] = toc_check_pages
            if max_pages_per_node is not None:
                kwargs["max_page_num_each_node"] = max_pages_per_node
            if max_tokens_per_node is not None:
                kwargs["max_token_num_each_node"] = max_tokens_per_node
            if if_add_node_id is not None:
                kwargs["if_add_node_id"] = if_add_node_id
            if if_add_node_summary is not None:
                kwargs["if_add_node_summary"] = if_add_node_summary
            if if_add_node_text is not None:
                kwargs["if_add_node_text"] = if_add_node_text
            if if_add_doc_description is not None:
                kwargs["if_add_doc_description"] = if_add_doc_description
            index_result = page_index(str(source_pdf), **kwargs)
            if not isinstance(index_result, dict) or "structure" not in index_result:
                raise RuntimeError("page_index returned invalid result: missing 'structure'")

            page_index_json.parent.mkdir(parents=True, exist_ok=True)
            page_index_json.write_text(
                json.dumps(index_result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            index_built = True
        else:
            logger.info("[ingest] Reusing existing index JSON: %s", page_index_json)
        _emit_progress(
            progress_callback,
            {"type": "stage_done", "stage": "index", "doc_name": doc_name, "built": index_built},
        )

        _emit_progress(progress_callback, {"type": "stage_start", "stage": "chunk", "doc_name": doc_name})
        if structure_needs_build:
            logger.info("[ingest] Building structure chunks: %s", chunks_dir)
            (
                load_root_from_page_index_json,
                chunk_document_structure,
                save_parts_to_folder,
            ) = _load_structure_helpers()

            root, chunk_doc_name = load_root_from_page_index_json(str(page_index_json))
            parts = chunk_document_structure(
                root_node=root,
                doc_name=chunk_doc_name,
                max_limit=structure_max_limit,
            )
            save_parts_to_folder(parts, chunks_dir)
            structure_built = True
        else:
            logger.info("[ingest] Reusing existing structure chunks: %s", chunks_dir)
        _emit_progress(
            progress_callback,
            {"type": "stage_done", "stage": "chunk", "doc_name": doc_name, "built": structure_built},
        )

        _emit_progress(progress_callback, {"type": "stage_start", "stage": "content", "doc_name": doc_name})
        if content_needs_build:
            logger.info("[ingest] Building content DB: %s", content_dir)
            build_content_db = _load_content_builder()
            build_content_db(
                pdf_path=str(source_pdf),
                output_dir=str(content_root),
                chunk_size=content_chunk_size,
            )
            content_built = True
        else:
            logger.info("[ingest] Reusing existing content DB: %s", content_dir)
        _emit_progress(
            progress_callback,
            {"type": "stage_done", "stage": "content", "doc_name": doc_name, "built": content_built},
        )

        if not _structure_ready(chunks_dir):
            raise RuntimeError(f"Structure chunks are missing or invalid: {chunks_dir}")
        if not _content_ready(content_dir):
            raise RuntimeError(f"Content DB is missing or invalid: {content_dir}")

        total_pages = _read_total_pages_from_content_dir(content_dir)

        chunks_dir_for_registry = _to_registry_path(chunks_dir, project_root)
        content_dir_for_registry = _to_registry_path(content_dir, project_root)
        pdf_path_for_registry = _to_registry_path(source_pdf, project_root)

        _emit_progress(progress_callback, {"type": "stage_start", "stage": "register", "doc_name": doc_name})
        logger.info("[ingest] Registering document: %s", doc_name)
        register_document(
            doc_name=doc_name,
            chunks_dir=chunks_dir_for_registry,
            content_dir=content_dir_for_registry,
            total_pages=total_pages,
            pdf_path=pdf_path_for_registry,
            persist=True,
        )
        _emit_progress(progress_callback, {"type": "stage_done", "stage": "register", "doc_name": doc_name, "built": True})

        result = ProcessResult(
            doc_name=doc_name,
            doc_stem=doc_stem,
            pdf_path=str(source_pdf),
            page_index_json=str(page_index_json),
            chunks_dir=chunks_dir_for_registry,
            content_dir=content_dir_for_registry,
            total_pages=total_pages,
            index_built=index_built,
            structure_built=structure_built,
            content_built=content_built,
            registered=True,
        )
        _emit_progress(
            progress_callback,
            {"type": "stage_done", "stage": "ingest", "doc_name": doc_name, "skipped": False},
        )
        return result
    except Exception as exc:
        _emit_progress(
            progress_callback,
            {
                "type": "error",
                "stage": "ingest",
                "message": str(exc),
                "doc_name": source_pdf.name if source_pdf.name else "",
            },
        )
        raise


def ensure_document_ready(
    doc: str | None = None,
    pdf: str | None = None,
    force: bool = False,
    model: str | None = None,
    toc_check_pages: int | None = None,
    max_pages_per_node: int | None = None,
    max_tokens_per_node: int | None = None,
    if_add_node_id: str | None = None,
    if_add_node_summary: str | None = None,
    if_add_node_text: str | None = None,
    if_add_doc_description: str | None = None,
    structure_max_limit: int = 30000,
    content_chunk_size: int = 20,
    progress_callback: ProgressCallback | None = None,
) -> ProcessResult:
    """Ensure a document is processed and registered for QA."""

    if pdf:
        return process_document(
            pdf_path=pdf,
            force=force,
            model=model,
            toc_check_pages=toc_check_pages,
            max_pages_per_node=max_pages_per_node,
            max_tokens_per_node=max_tokens_per_node,
            if_add_node_id=if_add_node_id,
            if_add_node_summary=if_add_node_summary,
            if_add_node_text=if_add_node_text,
            if_add_doc_description=if_add_doc_description,
            structure_max_limit=structure_max_limit,
            content_chunk_size=content_chunk_size,
            progress_callback=progress_callback,
        )

    if not doc:
        raise ValueError("Either doc or pdf must be provided")

    doc_name = _canonical_doc_name(doc)
    if not force and is_document_processed(doc_name):
        logger.info("Document already processed for QA: %s", doc_name)
        _emit_progress(
            progress_callback,
            {
                "type": "stage_done",
                "stage": "ensure_document_ready",
                "doc_name": doc_name,
                "skipped": True,
            },
        )
        return _result_from_existing(doc_name)

    resolved_pdf = resolve_pdf_for_doc(doc)
    return process_document(
        pdf_path=str(resolved_pdf),
        force=force,
        model=model,
        toc_check_pages=toc_check_pages,
        max_pages_per_node=max_pages_per_node,
        max_tokens_per_node=max_tokens_per_node,
        if_add_node_id=if_add_node_id,
        if_add_node_summary=if_add_node_summary,
        if_add_node_text=if_add_node_text,
        if_add_doc_description=if_add_doc_description,
        structure_max_limit=structure_max_limit,
        content_chunk_size=content_chunk_size,
        progress_callback=progress_callback,
    )
