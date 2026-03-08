"""Document structure tool for retrieving document index trees."""

import json
from pathlib import Path
from typing import Any

from .registry import get_doc_config


def get_document_structure(doc_name: str, part: int = 1) -> dict[str, Any]:
    """
    Get the document structure (index tree) for a specific part.
    
    Args:
        doc_name: Document name, e.g., "FC-LS.pdf"
        part: Part number (1-indexed), defaults to 1
    
    Returns:
        Dictionary containing:
        - structure: List of tree nodes with title, summary, start_index, end_index, children
        - next_steps: Navigation guidance for the LLM
        - pagination: Current part and total parts info
        - doc_info: (only when part=1) Document metadata with total_pages and total_parts
        
        Or error dictionary if part is out of range or document not found.
    """
    # Get document configuration
    config = get_doc_config(doc_name)
    if "error" in config:
        return config
    
    chunks_dir = config["chunks_dir"]
    total_pages = config["total_pages"]
    
    # Read manifest to get total_parts
    manifest_path = Path(chunks_dir) / "manifest.json"
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        total_parts = manifest["total_parts"]
    except FileNotFoundError:
        return {"error": f"Manifest file not found: {manifest_path}"}
    except (json.JSONDecodeError, KeyError) as e:
        return {"error": f"Invalid manifest file: {e}"}
    
    # Validate part range
    if part < 1 or part > total_parts:
        return {
            "error": f"Part {part} out of range",
            "valid_range": f"1-{total_parts}"
        }
    
    # Load the part file
    part_file = Path(chunks_dir) / f"part_{part:04d}.json"
    try:
        with open(part_file, "r", encoding="utf-8") as f:
            part_data = json.load(f)
    except FileNotFoundError:
        return {"error": f"Part file not found: {part_file}"}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid part file: {e}"}
    
    # Build response
    response = {
        "structure": part_data.get("structure", []),
        "next_steps": (
            "Read the summaries to identify sections relevant to your question. "
            "Use get_page_content() to retrieve specific pages from relevant sections "
            "(use start_index/end_index from nodes)."
        ),
        "pagination": {
            "current_part": part,
            "total_parts": total_parts
        }
    }
    
    # Add doc_info only for part=1
    if part == 1:
        response["doc_info"] = {
            "total_pages": total_pages,
            "total_parts": total_parts
        }
    
    return response
