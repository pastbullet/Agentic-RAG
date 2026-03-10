"""Document Store — manages document_state.json within a session's documents/ directory.

Responsible for tracking document access state: visited structure parts
and read pages.  Node state management (flatten_structure, upsert_node, etc.)
is handled separately.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from src.context.json_io import JSON_IO


class DocumentStore:
    """Manages document access state files.

    Document state files live under
    ``<session_dir>/documents/<doc_id>/document_state.json``.
    """

    def __init__(self, session_dir: Path) -> None:
        """Store the session directory path.

        Documents are stored under ``<session_dir>/documents/``.

        Parameters
        ----------
        session_dir:
            Root directory for this session
            (e.g. ``data/sessions/sess_20250101_120000``).
        """
        self._session_dir = Path(session_dir)
        self._documents_dir = self._session_dir / "documents"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _doc_dir(self, doc_id: str) -> Path:
        """Return the directory for a given document."""
        return self._documents_dir / doc_id

    def _state_path(self, doc_id: str) -> Path:
        """Return the path to document_state.json for a given document."""
        return self._doc_dir(doc_id) / "document_state.json"

    def _ensure_state(self, doc_id: str) -> dict:
        """Load existing document state or create a fresh one.

        Creates the document directory if it doesn't exist.
        Returns the state dict (either loaded or newly initialised).
        """
        doc_dir = self._doc_dir(doc_id)
        os.makedirs(doc_dir, exist_ok=True)

        state = JSON_IO.load(self._state_path(doc_id))
        if state is None:
            state = {
                "doc_name": doc_id,
                "visited_parts": [],
                "read_pages": [],
                "total_reads": 0,
            }
        return state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_visited_parts(self, doc_id: str, part: int) -> None:
        """Append *part* number to visited_parts (deduplicated).

        Creates the document directory and ``document_state.json`` if they
        don't exist yet.

        Parameters
        ----------
        doc_id:
            Document identifier (e.g. ``"FC-LS.pdf"``).
        part:
            The part number that was visited.
        """
        state = self._ensure_state(doc_id)
        if part not in state["visited_parts"]:
            state["visited_parts"].append(part)
        JSON_IO.save(self._state_path(doc_id), state)

    def update_read_pages(self, doc_id: str, pages: list[int]) -> None:
        """Append page numbers to read_pages (deduplicated) and increment total_reads.

        Creates the document directory and ``document_state.json`` if they
        don't exist yet.  ``total_reads`` is incremented by the number of
        pages provided (``len(pages)``), regardless of deduplication.

        Parameters
        ----------
        doc_id:
            Document identifier.
        pages:
            List of page numbers that were read.
        """
        state = self._ensure_state(doc_id)
        for page in pages:
            if page not in state["read_pages"]:
                state["read_pages"].append(page)
        state["total_reads"] += len(pages)
        JSON_IO.save(self._state_path(doc_id), state)

    def get_document_state(self, doc_id: str) -> dict | None:
        """Read and return document_state.json for *doc_id*.

        Returns ``None`` if the file does not exist.

        Parameters
        ----------
        doc_id:
            Document identifier.
        """
        return JSON_IO.load(self._state_path(doc_id))

    # ------------------------------------------------------------------
    # Node state management
    # ------------------------------------------------------------------

    def _nodes_dir(self, doc_id: str) -> Path:
        """Return the nodes/ directory for a given document."""
        return self._doc_dir(doc_id) / "nodes"

    def _node_path(self, doc_id: str, node_id: str) -> Path:
        """Return the path to a specific node JSON file."""
        return self._nodes_dir(doc_id) / f"{node_id}.json"

    def flatten_structure(
        self, structure: list[dict], parent_path: str = ""
    ) -> list[dict]:
        """Recursively flatten a nested structure tree.

        Each node in *structure* may contain a ``"children"`` key with a
        list of child nodes.  This method walks the tree depth-first and
        returns a flat list of all nodes across all levels.

        For each node the ``"parent_path"`` field is set to the current
        *parent_path*.  The child path is built as
        ``parent_path + "/" + node["title"]`` (or just ``node["title"]``
        when *parent_path* is empty).  The ``"children"`` key is removed
        from each returned node.

        Parameters
        ----------
        structure:
            Nested list of node dicts, each optionally containing
            ``"children"``.
        parent_path:
            Path prefix for the current level (empty string for root).

        Returns
        -------
        list[dict]
            Flat list of all nodes with ``parent_path`` set and
            ``children`` removed.
        """
        result: list[dict] = []
        for node in structure:
            children = node.pop("children", [])
            node["parent_path"] = parent_path

            child_path = (
                f"{parent_path}/{node['title']}" if parent_path else node["title"]
            )

            result.append(node)
            if children:
                result.extend(self.flatten_structure(children, child_path))
        return result

    @staticmethod
    def generate_provisional_id(
        doc_id: str, title: str, start: int, end: int, path: str
    ) -> str:
        """Generate a stable temporary node key.

        The key is ``tmp_`` followed by the first 12 hex characters of the
        SHA-1 hash of ``"{doc_id}|{title}|{start}|{end}|{path}"``.

        Parameters
        ----------
        doc_id:
            Document identifier.
        title:
            Node title.
        start:
            Start index of the node.
        end:
            End index of the node.
        path:
            Parent path of the node.

        Returns
        -------
        str
            A provisional ID like ``tmp_a1b2c3d4e5f6``.
        """
        raw = f"{doc_id}|{title}|{start}|{end}|{path}"
        digest = hashlib.sha1(raw.encode()).hexdigest()[:12]
        return f"tmp_{digest}"

    def upsert_node(
        self, doc_id: str, node_data: dict, turn_id: str
    ) -> str:
        """Create or update a node state file.

        Node files are stored at
        ``<session_dir>/documents/<doc_id>/nodes/<node_id>.json``.

        *node_data* must contain: ``node_id`` (may be ``None``),
        ``title``, ``start_index``, ``end_index``, ``summary`` (may be
        ``None``), ``is_skeleton`` (bool), ``parent_path``.

        Merge rules:

        * **New node** (file doesn't exist): create with all fields,
          ``status="discovered"``, ``read_count=0``.
        * **Existing + is_skeleton=true**: only update positioning fields
          (``title``, ``start_index``, ``end_index``, ``parent_path``,
          ``last_seen_turn_id``); set ``is_skeleton_latest=true``.
          Do **not** overwrite existing ``summary`` or ``fact_digest``.
        * **Existing + is_skeleton=false**: update positioning fields
          **and** overwrite ``summary``; set ``is_skeleton_latest=false``.

        Parameters
        ----------
        doc_id:
            Document identifier.
        node_data:
            Dict with node fields (see above).
        turn_id:
            Current turn identifier.

        Returns
        -------
        str
            The actual ``node_id`` used (may be a provisional ID).
        """
        node_id = node_data.get("node_id")
        is_provisional = not node_id  # None or empty string

        if is_provisional:
            node_id = self.generate_provisional_id(
                doc_id,
                node_data["title"],
                node_data["start_index"],
                node_data["end_index"],
                node_data["parent_path"],
            )

        nodes_dir = self._nodes_dir(doc_id)
        os.makedirs(nodes_dir, exist_ok=True)

        node_path = self._node_path(doc_id, node_id)
        existing = JSON_IO.load(node_path)

        if existing is None:
            # --- NEW NODE ---
            node_state = {
                "node_id": node_id,
                "title": node_data["title"],
                "start_index": node_data["start_index"],
                "end_index": node_data["end_index"],
                "summary": node_data.get("summary"),
                "parent_path": node_data["parent_path"],
                "status": "discovered",
                "read_count": 0,
                "is_skeleton_latest": node_data["is_skeleton"],
                "seen_in_parts": [],
                "first_seen_turn_id": turn_id,
                "last_seen_turn_id": turn_id,
                "is_provisional_id": is_provisional,
                "fact_digest": None,
            }
        elif node_data["is_skeleton"]:
            # --- EXISTING + SKELETON ---
            node_state = existing
            node_state["title"] = node_data["title"]
            node_state["start_index"] = node_data["start_index"]
            node_state["end_index"] = node_data["end_index"]
            node_state["parent_path"] = node_data["parent_path"]
            node_state["last_seen_turn_id"] = turn_id
            node_state["is_skeleton_latest"] = True
            # DO NOT overwrite summary or fact_digest
        else:
            # --- EXISTING + FULL NODE ---
            node_state = existing
            node_state["title"] = node_data["title"]
            node_state["start_index"] = node_data["start_index"]
            node_state["end_index"] = node_data["end_index"]
            node_state["parent_path"] = node_data["parent_path"]
            node_state["last_seen_turn_id"] = turn_id
            node_state["summary"] = node_data.get("summary")
            node_state["is_skeleton_latest"] = False

        JSON_IO.save(node_path, node_state)
        return node_id

    def update_node_read_status(
        self, doc_id: str, node_id: str
    ) -> None:
        """Increment read_count and advance the node status.

        Status transitions:

        * ``discovered`` → ``reading`` (on first read)
        * ``reading`` → ``read_complete`` (on second read)

        Parameters
        ----------
        doc_id:
            Document identifier.
        node_id:
            Node identifier.
        """
        node_path = self._node_path(doc_id, node_id)
        node_state = JSON_IO.load(node_path)
        if node_state is None:
            return

        node_state["read_count"] = node_state.get("read_count", 0) + 1

        status = node_state.get("status", "discovered")
        if status == "discovered":
            node_state["status"] = "reading"
        elif status == "reading":
            node_state["status"] = "read_complete"

        JSON_IO.save(node_path, node_state)
