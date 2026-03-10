"""Turn Store — manages turn_xxxx.json files within a session's turns/ directory.

Responsible for creating turn records, appending tool calls,
updating retrieval traces, and finalising turns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.context.json_io import JSON_IO


class TurnStore:
    """Manages the lifecycle of individual turn JSON files.

    Turn files live under ``<session_dir>/turns/`` and are named by their
    turn_id, e.g. ``turn_0001.json``.
    """

    def __init__(self, turns_dir: Path) -> None:
        """Store the turns directory path.

        Parameters
        ----------
        turns_dir:
            Directory where turn files are stored
            (e.g. ``data/sessions/<session_id>/turns/``).
        """
        self._turns_dir = Path(turns_dir)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _turn_path(self, turn_id: str) -> Path:
        """Return the file path for a given *turn_id*."""
        return self._turns_dir / f"{turn_id}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_turn(self, turn_id: str, user_query: str, doc_name: str) -> None:
        """Create a new turn file with initial fields.

        Parameters
        ----------
        turn_id:
            Unique turn identifier (e.g. ``turn_0001``).
        user_query:
            The user's question for this turn.
        doc_name:
            Name of the document being queried.
        """
        data = {
            "turn_id": turn_id,
            "user_query": user_query,
            "doc_name": doc_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "status": "active",
            "tool_calls": [],
            "retrieval_trace": {
                "structure_parts_seen": [],
                "history_candidate_nodes": [],
                "pages_read": [],
            },
            "answer_payload": None,
        }
        JSON_IO.save(self._turn_path(turn_id), data)

    def add_tool_call(
        self,
        turn_id: str,
        tool_name: str,
        arguments: dict,
        result_summary: str,
    ) -> None:
        """Append a tool call record to the turn's tool_calls list.

        Parameters
        ----------
        turn_id:
            The turn to update.
        tool_name:
            Name of the tool that was called.
        arguments:
            Arguments passed to the tool.
        result_summary:
            A short summary of the tool's result.
        """
        path = self._turn_path(turn_id)
        data = JSON_IO.load(path)
        if data is not None:
            record = {
                "tool_name": tool_name,
                "arguments": arguments,
                "result_summary": result_summary,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            data["tool_calls"].append(record)
            JSON_IO.save(path, data)

    def update_retrieval_trace(
        self,
        turn_id: str,
        parts_seen: list[int] | None = None,
        candidate_nodes: list[str] | None = None,
        pages_read: list[int] | None = None,
    ) -> None:
        """Update retrieval_trace fields by appending to existing lists.

        Only the provided (non-None) fields are updated. Values are
        appended to the existing lists — they never replace them.

        Parameters
        ----------
        turn_id:
            The turn to update.
        parts_seen:
            Part numbers to append to ``structure_parts_seen``.
        candidate_nodes:
            Node IDs to append to ``history_candidate_nodes``.
        pages_read:
            Page numbers to append to ``pages_read``.
        """
        path = self._turn_path(turn_id)
        data = JSON_IO.load(path)
        if data is not None:
            trace = data["retrieval_trace"]
            if parts_seen is not None:
                trace["structure_parts_seen"].extend(parts_seen)
            if candidate_nodes is not None:
                trace["history_candidate_nodes"].extend(candidate_nodes)
            if pages_read is not None:
                trace["pages_read"].extend(pages_read)
            JSON_IO.save(path, data)

    def finalize(self, turn_id: str, answer_payload: dict) -> None:
        """Finalise a turn by recording the answer and marking it completed.

        Parameters
        ----------
        turn_id:
            The turn to finalise.
        answer_payload:
            The final answer data to store.
        """
        path = self._turn_path(turn_id)
        data = JSON_IO.load(path)
        if data is not None:
            data["answer_payload"] = answer_payload
            data["status"] = "completed"
            data["finished_at"] = datetime.now(timezone.utc).isoformat()
            JSON_IO.save(path, data)
