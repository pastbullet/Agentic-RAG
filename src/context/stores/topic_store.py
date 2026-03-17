"""DEPRECATED: Topic Store — manages topic_xxxx.json files within a session's topics/ directory.

Responsible for creating topic snapshots, tracking related turns,
and updating topic associations (nodes, evidences, open gaps).
"""

from __future__ import annotations

import os
from pathlib import Path

from src.context.json_io import JSON_IO


class TopicStore:
    """Manages topic JSON files.

    Topic files live under ``<topics_dir>/`` and are named by their
    topic_id, e.g. ``topic_0001.json``.
    """

    def __init__(self, topics_dir: Path) -> None:
        """Store the topics directory path.

        Parameters
        ----------
        topics_dir:
            Directory where topic files are stored
            (e.g. ``data/sessions/<session_id>/topics/``).
        """
        self._topics_dir = Path(topics_dir)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _topic_path(self, topic_id: str) -> Path:
        """Return the file path for a given *topic_id*."""
        return self._topics_dir / f"{topic_id}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_topic(
        self,
        topic_id: str,
        turn_id: str,
        node_ids: list[str],
        evidence_ids: list[str],
        open_gaps: list[str],
    ) -> None:
        """Create a new topic file.

        Parameters
        ----------
        topic_id:
            Unique topic identifier (e.g. ``topic_0001``).
        turn_id:
            The turn in which this topic was first identified.
        node_ids:
            List of related node IDs.
        evidence_ids:
            List of core evidence IDs supporting this topic.
        open_gaps:
            List of identified information gaps for this topic.
        """
        os.makedirs(self._topics_dir, exist_ok=True)

        data = {
            "topic_id": topic_id,
            "related_turn_ids": [turn_id],
            "related_node_ids": node_ids,
            "core_evidence_ids": evidence_ids,
            "open_gaps": open_gaps,
        }
        JSON_IO.save(self._topic_path(topic_id), data)

    def add_turn_to_topic(self, topic_id: str, turn_id: str) -> None:
        """Append *turn_id* to the topic's related_turn_ids list.

        Parameters
        ----------
        topic_id:
            The topic to update.
        turn_id:
            The turn that relates to this topic.
        """
        path = self._topic_path(topic_id)
        data = JSON_IO.load(path)
        if data is not None:
            data["related_turn_ids"].append(turn_id)
            JSON_IO.save(path, data)

    def update_topic(
        self,
        topic_id: str,
        node_ids: list[str],
        evidence_ids: list[str],
        open_gaps: list[str],
    ) -> None:
        """Update a topic's associations (replace, not append).

        Parameters
        ----------
        topic_id:
            The topic to update.
        node_ids:
            New list of related node IDs (replaces existing).
        evidence_ids:
            New list of core evidence IDs (replaces existing).
        open_gaps:
            New list of open gaps (replaces existing).
        """
        path = self._topic_path(topic_id)
        data = JSON_IO.load(path)
        if data is not None:
            data["related_node_ids"] = node_ids
            data["core_evidence_ids"] = evidence_ids
            data["open_gaps"] = open_gaps
            JSON_IO.save(path, data)
