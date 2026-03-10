"""ID generation utilities for the Context Management System.

Provides deterministic, format-consistent ID generators for sessions,
turns, evidences, and topics.
"""

from datetime import datetime, timezone


def generate_session_id() -> str:
    """Generate a session ID in ``sess_YYYYMMDD_HHMMSS`` format.

    Uses the current UTC time.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("sess_%Y%m%d_%H%M%S")


def generate_turn_id(seq: int) -> str:
    """Generate a turn ID with 4-digit zero-padded sequence number.

    Example: ``generate_turn_id(1)`` → ``'turn_0001'``
    """
    return f"turn_{seq:04d}"


def generate_evidence_id(seq: int) -> str:
    """Generate an evidence ID with 6-digit zero-padded sequence number.

    Example: ``generate_evidence_id(1)`` → ``'ev_000001'``
    """
    return f"ev_{seq:06d}"


def generate_topic_id(seq: int) -> str:
    """Generate a topic ID with 4-digit zero-padded sequence number.

    Example: ``generate_topic_id(1)`` → ``'topic_0001'``
    """
    return f"topic_{seq:04d}"
