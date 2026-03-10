"""Evidence Store — manages ev_xxxxxx.json files within a session's evidences/ directory.

Responsible for creating evidence records, tracking cross-turn usage,
and querying evidences by source document and page.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.context.json_io import JSON_IO


class EvidenceStore:
    """Manages evidence JSON files.

    Evidence files live under ``<evidences_dir>/`` and are named by their
    evidence_id, e.g. ``ev_000001.json``.
    """

    def __init__(self, evidences_dir: Path) -> None:
        """Store the evidences directory path and initialise the sequence counter.

        Parameters
        ----------
        evidences_dir:
            Directory where evidence files are stored
            (e.g. ``data/sessions/<session_id>/evidences/``).
        """
        self._evidences_dir = Path(evidences_dir)
        self._next_seq = 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _evidence_path(self, evidence_id: str) -> Path:
        """Return the file path for a given *evidence_id*."""
        return self._evidences_dir / f"{evidence_id}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_evidence(
        self,
        evidence_id: str,
        source_doc: str,
        source_page: int,
        content: str,
        turn_id: str,
    ) -> None:
        """Create a new evidence file.

        Parameters
        ----------
        evidence_id:
            Unique evidence identifier (e.g. ``ev_000001``).
        source_doc:
            Name of the source document.
        source_page:
            Page number from which the evidence was extracted.
        content:
            The evidence text content.
        turn_id:
            The turn in which this evidence was extracted.
        """
        os.makedirs(self._evidences_dir, exist_ok=True)

        data = {
            "evidence_id": evidence_id,
            "source_doc": source_doc,
            "source_page": source_page,
            "content": content,
            "extracted_in_turn": turn_id,
            "used_in_turns": [],
        }
        JSON_IO.save(self._evidence_path(evidence_id), data)

    def add_usage(self, evidence_id: str, turn_id: str) -> None:
        """Append *turn_id* to the evidence's used_in_turns list.

        Parameters
        ----------
        evidence_id:
            The evidence to update.
        turn_id:
            The turn that referenced this evidence.
        """
        path = self._evidence_path(evidence_id)
        data = JSON_IO.load(path)
        if data is not None:
            data["used_in_turns"].append(turn_id)
            JSON_IO.save(path, data)

    def query_by_source(self, source_doc: str, source_page: int) -> list[dict]:
        """Query evidences matching both *source_doc* and *source_page*.

        Scans all evidence files in the directory and returns those that
        match both criteria.

        Parameters
        ----------
        source_doc:
            Document name to filter by.
        source_page:
            Page number to filter by.

        Returns
        -------
        list[dict]
            List of matching evidence dicts. Empty list if no matches
            or the directory doesn't exist.
        """
        if not self._evidences_dir.exists():
            return []

        results: list[dict] = []
        for file_path in sorted(self._evidences_dir.iterdir()):
            if not file_path.suffix == ".json":
                continue
            data = JSON_IO.load(file_path)
            if data is None:
                continue
            if data.get("source_doc") == source_doc and data.get("source_page") == source_page:
                results.append(data)
        return results
