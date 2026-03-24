"""Tests for review-decision persistence and application."""

from __future__ import annotations

import json

from src.extract.evidence_card import load_review_decisions, save_review_decisions
from src.extract.merge import merge_messages_v2, merge_state_machines
from src.models import ProtocolField, ProtocolMessage, ProtocolState, ProtocolStateMachine, ProtocolTransition


def test_load_review_decisions_returns_empty_on_missing_file(tmp_path):
    path = tmp_path / "missing.json"
    assert load_review_decisions(path) == []


def test_save_and_load_review_decisions_round_trip(tmp_path):
    path = tmp_path / "review_decisions.json"
    decisions = [
        {"object_type": "message", "pair": [1, 0], "decision": "merge"},
        {"object_type": "state_machine", "pair": [2, 3], "decision": "keep_separate"},
    ]

    save_review_decisions(path, decisions)
    loaded = load_review_decisions(path)

    assert loaded == [
        {"object_type": "message", "pair": [0, 1], "decision": "merge"},
        {"object_type": "state_machine", "pair": [2, 3], "decision": "keep_separate"},
    ]
    assert json.loads(path.read_text(encoding="utf-8")) == loaded


def test_review_decisions_can_force_message_merge_and_keep_separate():
    messages = [
        ProtocolMessage(
            name="BFD Echo Packet",
            fields=[ProtocolField(name="Opaque Payload", size_bits=None)],
            source_pages=[1],
        ),
        ProtocolMessage(
            name="BFD Control Packet",
            fields=[ProtocolField(name="Version", size_bits=3)],
            source_pages=[2],
        ),
    ]
    merged_default, _, _ = merge_messages_v2(messages, enable_fuzzy_match=True)
    assert len(merged_default) == 2

    merged_forced, _, near_miss_forced = merge_messages_v2(
        messages,
        enable_fuzzy_match=True,
        review_decisions=[{"object_type": "message", "pair": [0, 1], "decision": "merge"}],
    )
    assert len(merged_forced) == 1
    assert near_miss_forced == []

    merged_keep, _, near_miss_keep = merge_messages_v2(
        [
            ProtocolMessage(
                name="Generic BFD Control Packet Format",
                fields=[ProtocolField(name="Version", size_bits=3), ProtocolField(name="Length", size_bits=8)],
                source_pages=[3],
            ),
            ProtocolMessage(
                name="BFD Control Packet",
                fields=[ProtocolField(name="Version", size_bits=3), ProtocolField(name="Length", size_bits=8)],
                source_pages=[4],
            ),
        ],
        enable_fuzzy_match=True,
        review_decisions=[{"object_type": "message", "pair": [0, 1], "decision": "keep_separate"}],
    )
    assert len(merged_keep) == 2
    assert near_miss_keep == []


def test_review_decisions_for_state_machine_are_idempotent():
    sms = [
        ProtocolStateMachine(
            name="Session A",
            states=[ProtocolState(name="Down"), ProtocolState(name="Init"), ProtocolState(name="Up")],
            transitions=[ProtocolTransition(from_state="Down", to_state="Init", event="Receive packet")],
            source_pages=[1],
        ),
        ProtocolStateMachine(
            name="Session B",
            states=[ProtocolState(name="Down"), ProtocolState(name="Init"), ProtocolState(name="Up")],
            transitions=[ProtocolTransition(from_state="Down", to_state="Init", event="Receive packet")],
            source_pages=[2],
        ),
    ]
    decisions = [{"object_type": "state_machine", "pair": [0, 1], "decision": "keep_separate"}]

    merged_once, _, _, _ = merge_state_machines(sms, review_decisions=decisions)
    merged_twice, _, _, _ = merge_state_machines(sms, review_decisions=decisions)

    assert [item.model_dump() for item in merged_once] == [item.model_dump() for item in merged_twice]
