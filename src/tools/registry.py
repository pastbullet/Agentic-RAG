"""Document registry for mapping document names to data file paths.

Supports both built-in static entries and runtime-persisted registrations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict


class DocConfig(TypedDict, total=False):
    """Configuration for a registered document."""

    chunks_dir: str
    content_dir: str
    total_pages: int
    pdf_path: str


# Built-in registry entries (kept for backward compatibility)
DOC_REGISTRY: dict[str, DocConfig] = {
    "FC-LS.pdf": {
        "chunks_dir": "data/out/chunks_3/FC-LS",
        "content_dir": "output/json",
        "total_pages": 210,
    },
    "rfc5880-BFD.pdf": {
        "chunks_dir": "data/out/chunks_3/BFD",
        "content_dir": "output_bfd/json",
        "total_pages": 49,
    },
}


RUNTIME_REGISTRY_PATH = Path("data/out/doc_registry.runtime.json")
_REQUIRED_KEYS = ("chunks_dir", "content_dir", "total_pages")


def _normalize_entry(raw: dict) -> DocConfig | None:
    """Validate and normalize a registry entry loaded from JSON."""
    if not isinstance(raw, dict):
        return None

    if any(key not in raw for key in _REQUIRED_KEYS):
        return None

    chunks_dir = raw.get("chunks_dir")
    content_dir = raw.get("content_dir")
    total_pages = raw.get("total_pages")
    pdf_path = raw.get("pdf_path")

    if not isinstance(chunks_dir, str) or not chunks_dir:
        return None
    if not isinstance(content_dir, str) or not content_dir:
        return None

    try:
        total_pages_int = int(total_pages)
    except (TypeError, ValueError):
        return None
    if total_pages_int <= 0:
        return None

    entry: DocConfig = {
        "chunks_dir": chunks_dir,
        "content_dir": content_dir,
        "total_pages": total_pages_int,
    }
    if isinstance(pdf_path, str) and pdf_path:
        entry["pdf_path"] = pdf_path
    return entry


def _load_runtime_registry() -> dict[str, DocConfig]:
    """Load runtime registry from disk; invalid entries are ignored."""
    path = RUNTIME_REGISTRY_PATH
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(payload, dict):
        return {}

    result: dict[str, DocConfig] = {}
    for doc_name, raw_entry in payload.items():
        if not isinstance(doc_name, str) or not doc_name:
            continue
        entry = _normalize_entry(raw_entry)
        if entry is not None:
            result[doc_name] = entry
    return result


def _save_runtime_registry(registry: dict[str, DocConfig]) -> None:
    """Persist runtime registry to disk."""
    RUNTIME_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    serializable = {name: dict(config) for name, config in sorted(registry.items())}
    RUNTIME_REGISTRY_PATH.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_registered_documents() -> dict[str, DocConfig]:
    """Return merged registry view: runtime entries override built-in entries."""
    merged: dict[str, DocConfig] = dict(DOC_REGISTRY)
    merged.update(_load_runtime_registry())
    return merged


def register_document(
    doc_name: str,
    chunks_dir: str,
    content_dir: str,
    total_pages: int,
    pdf_path: str | None = None,
    persist: bool = True,
) -> DocConfig:
    """Register a document for QA and optionally persist to runtime registry."""
    entry: DocConfig = {
        "chunks_dir": str(chunks_dir),
        "content_dir": str(content_dir),
        "total_pages": int(total_pages),
    }
    if pdf_path:
        entry["pdf_path"] = str(pdf_path)

    if persist:
        runtime_registry = _load_runtime_registry()
        runtime_registry[doc_name] = entry
        _save_runtime_registry(runtime_registry)

    return entry


def get_doc_config(doc_name: str) -> dict:
    """Get configuration for a registered document.

    Returns:
        DocConfig dictionary if document is registered,
        or error dictionary with available documents list if not found.
    """
    merged = get_registered_documents()
    if doc_name in merged:
        return merged[doc_name]

    available_docs = sorted(merged.keys())
    return {
        "error": (
            f"Unknown document: {doc_name}. "
            f"Available documents: {', '.join(available_docs)}"
        )
    }


def is_document_processed(doc_name: str) -> bool:
    """Check whether registered document artifacts exist and look usable."""
    config = get_doc_config(doc_name)
    if "error" in config:
        return False

    chunks_dir = Path(str(config["chunks_dir"]))
    content_dir = Path(str(config["content_dir"]))

    manifest = chunks_dir / "manifest.json"
    if not manifest.exists():
        return False

    part_files = list(chunks_dir.glob("part_*.json"))
    if not part_files:
        return False

    if not content_dir.exists() or not content_dir.is_dir():
        return False
    if not list(content_dir.glob("content_*.json")):
        return False

    return True
