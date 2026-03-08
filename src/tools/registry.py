"""Document registry for mapping document names to data file paths."""

from typing import TypedDict


class DocConfig(TypedDict):
    """Configuration for a registered document."""
    chunks_dir: str
    content_dir: str
    total_pages: int


# Document registry mapping doc_name to data paths
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


def get_doc_config(doc_name: str) -> dict:
    """
    Get configuration for a registered document.
    
    Args:
        doc_name: Document name, e.g., "FC-LS.pdf"
    
    Returns:
        DocConfig dictionary if document is registered,
        or error dictionary with available documents list if not found.
    """
    if doc_name in DOC_REGISTRY:
        return DOC_REGISTRY[doc_name]
    
    available_docs = list(DOC_REGISTRY.keys())
    return {
        "error": f"Unknown document: {doc_name}. Available documents: {', '.join(available_docs)}"
    }
