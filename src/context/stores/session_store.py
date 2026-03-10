"""Session Store — manages session.json within a session directory.

Responsible for creating the session directory structure, writing the
initial session.json, tracking turns, and finalising the session.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from src.context.json_io import JSON_IO


class SessionStore:
    """Manages the lifecycle of a single session's metadata file.

    The session directory layout created by *create_session*::

        <session_dir>/
        ├── session.json
        ├── turns/
        ├── documents/
        ├── evidences/
        └── topics/
    """

    # Sub-directories that must exist inside every session directory.
    _SUBDIRS = ("turns", "documents", "evidences", "topics")

    def __init__(self, session_dir: Path) -> None:
        """Store the session directory path.

        Parameters
        ----------
        session_dir:
            Root directory for this session
            (e.g. ``data/sessions/sess_20250101_120000``).
        """
        self._session_dir = Path(session_dir)
        self._session_path = self._session_dir / "session.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(self, session_id: str, doc_name: str) -> None:
        """Create the session directory tree and write the initial *session.json*.

        Parameters
        ----------
        session_id:
            Unique session identifier (``sess_YYYYMMDD_HHMMSS``).
        doc_name:
            Name of the document being queried in this session.
        """
        # Ensure the session root and all required sub-directories exist.
        for subdir in self._SUBDIRS:
            os.makedirs(self._session_dir / subdir, exist_ok=True)

        data = {
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "doc_name": doc_name,
            "status": "active",
            "turns": [],
        }
        JSON_IO.save(self._session_path, data)

    def add_turn(self, turn_id: str) -> None:
        """Append *turn_id* to the ``turns`` list in session.json."""
        data = JSON_IO.load(self._session_path)
        if data is not None:
            data["turns"].append(turn_id)
            JSON_IO.save(self._session_path, data)

    def finalize(self) -> None:
        """Mark the session as completed by setting ``status`` to ``"completed"``."""
        data = JSON_IO.load(self._session_path)
        if data is not None:
            data["status"] = "completed"
            JSON_IO.save(self._session_path, data)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_dir(self) -> Path:
        """Return the session directory path."""
        return self._session_dir
