"""Context Manager — the single high-level interface for the context management system.

Coordinates all stores and the updater to manage session, turn, document,
evidence, and topic state throughout an agentic RAG conversation.
"""

from __future__ import annotations

from pathlib import Path

from src.context import id_gen
from src.context.stores.session_store import SessionStore
from src.context.stores.turn_store import TurnStore
from src.context.stores.document_store import DocumentStore
from src.context.stores.evidence_store import EvidenceStore
from src.context.stores.topic_store import TopicStore
from src.context.updater import Updater


class ContextManager:
    """Context manager — the only high-level interface exposed externally.

    Coordinates the various stores to create, update, and query
    context state throughout a multi-turn conversation.
    """

    def __init__(self, base_dir: str = "data/sessions") -> None:
        self._base_dir = Path(base_dir)

        # Internal sequence counters
        self._turn_seq = 0
        self._evidence_seq = 0
        self._topic_seq = 0

        # Stores and updater are initialised in create_session
        self.session_store: SessionStore | None = None
        self.turn_store: TurnStore | None = None
        self.document_store: DocumentStore | None = None
        self.evidence_store: EvidenceStore | None = None
        self.topic_store: TopicStore | None = None
        self.updater: Updater | None = None

        self._session_id: str | None = None
        self._session_dir: Path | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(self, doc_name: str) -> str:
        """Create a new session and initialise all stores.

        Parameters
        ----------
        doc_name:
            Name of the document being queried.

        Returns
        -------
        str
            The generated session_id.
        """
        self._session_id = id_gen.generate_session_id()
        self._session_dir = self._base_dir / self._session_id

        self.session_store = SessionStore(self._session_dir)
        self.turn_store = TurnStore(self._session_dir / "turns")
        self.document_store = DocumentStore(self._session_dir)
        self.evidence_store = EvidenceStore(self._session_dir / "evidences")
        self.topic_store = TopicStore(self._session_dir / "topics")

        self.updater = Updater(
            document_store=self.document_store,
            turn_store=self.turn_store,
            evidence_store=self.evidence_store,
            topic_store=self.topic_store,
        )

        self.session_store.create_session(self._session_id, doc_name)
        return self._session_id

    def create_turn(self, user_query: str, doc_name: str) -> str:
        """Create a new turn within the current session.

        Parameters
        ----------
        user_query:
            The user's question for this turn.
        doc_name:
            Name of the document being queried.

        Returns
        -------
        str
            The generated turn_id.
        """
        self._turn_seq += 1
        turn_id = id_gen.generate_turn_id(self._turn_seq)
        self.turn_store.create_turn(turn_id, user_query, doc_name)
        self.session_store.add_turn(turn_id)
        return turn_id

    def record_tool_call(
        self,
        turn_id: str,
        tool_name: str,
        arguments: dict,
        result: dict,
        doc_id: str | None = None,
    ) -> None:
        """Record a tool call and delegate state updates to the Updater.

        Parameters
        ----------
        turn_id:
            The turn in which the tool was called.
        tool_name:
            Name of the tool that was called.
        arguments:
            Arguments passed to the tool.
        result:
            Result returned by the tool.
        doc_id:
            Document identifier (optional).
        """
        result_summary = str(result)[:200]
        self.turn_store.add_tool_call(turn_id, tool_name, arguments, result_summary)
        self.updater.handle_tool_call(turn_id, tool_name, arguments, result, doc_id)

    def add_evidences(
        self, turn_id: str, doc_id: str, evidence_items: list[dict]
    ) -> list[str]:
        """Add evidence items extracted from a document.

        Parameters
        ----------
        turn_id:
            The turn in which the evidences were extracted.
        doc_id:
            Source document identifier.
        evidence_items:
            List of dicts, each with ``"source_page"`` and ``"content"`` keys.

        Returns
        -------
        list[str]
            List of generated evidence_ids.
        """
        evidence_ids: list[str] = []
        for item in evidence_items:
            self._evidence_seq += 1
            evidence_id = id_gen.generate_evidence_id(self._evidence_seq)
            self.evidence_store.add_evidence(
                evidence_id,
                doc_id,
                item["source_page"],
                item["content"],
                turn_id,
            )
            evidence_ids.append(evidence_id)
        return evidence_ids

    def finalize_turn(self, turn_id: str, answer_payload: dict) -> None:
        """Finalise a turn: record the answer and trigger post-turn updates.

        Parameters
        ----------
        turn_id:
            The turn to finalise.
        answer_payload:
            The final answer data.
        """
        self.turn_store.finalize(turn_id, answer_payload)
        self.updater.handle_final_answer(turn_id, answer_payload)

    def finalize_session(self) -> None:
        """Finalise the session by marking it as completed."""
        self.session_store.finalize()
