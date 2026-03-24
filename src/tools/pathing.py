"""Canonical paths for persisted project data."""

from __future__ import annotations

from pathlib import Path


DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
UPLOAD_DIR = DATA_DIR / "uploads"
SESSIONS_DIR = DATA_DIR / "sessions"
OUT_DIR = DATA_DIR / "out"
CHUNK_ROOT_DIR = OUT_DIR / "chunk"
CONTENT_ROOT_DIR = OUT_DIR / "content"
RUNTIME_REGISTRY_PATH = OUT_DIR / "doc_registry.runtime.json"
PAGE_INDEX_FILENAME = "page_index.json"


def safe_doc_stem(stem_or_name: str) -> str:
    stem = Path(stem_or_name).stem if stem_or_name.lower().endswith(".pdf") else stem_or_name
    clean = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in stem.strip())
    return clean or "document"


def chunk_dir_for_doc(stem_or_name: str) -> Path:
    return CHUNK_ROOT_DIR / safe_doc_stem(stem_or_name)


def page_index_path_for_doc(stem_or_name: str, chunks_dir: str | Path | None = None) -> Path:
    if chunks_dir is not None:
        return Path(chunks_dir) / PAGE_INDEX_FILENAME
    return chunk_dir_for_doc(stem_or_name) / PAGE_INDEX_FILENAME


def content_root_for_doc(stem_or_name: str) -> Path:
    return CONTENT_ROOT_DIR / safe_doc_stem(stem_or_name)


def content_dir_for_doc(stem_or_name: str) -> Path:
    return content_root_for_doc(stem_or_name) / "json"


def artifact_dir_for_doc(stem_or_name: str) -> Path:
    return OUT_DIR / safe_doc_stem(stem_or_name)
