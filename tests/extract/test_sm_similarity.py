"""Tests for state-machine similarity helpers."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.merge import normalize_name, normalize_name_v2
from src.extract.sm_similarity import (
    compute_sm_similarity,
    name_similarity,
    normalize_state_name,
    normalize_transition_key,
    should_merge_state_machines,
)
from src.models import ProtocolState, ProtocolStateMachine, ProtocolTransition


NAME_CHARS = st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters=" -_()/§.")


def _non_empty_name_strategy(max_size: int = 24):
    return st.text(NAME_CHARS, min_size=1, max_size=max_size).filter(lambda text: text.strip() != "")


@st.composite
def protocol_state_machine_strategy(draw):
    name = draw(_non_empty_name_strategy())
    state_names = draw(st.lists(_non_empty_name_strategy(12), min_size=1, max_size=4, unique=True))
    states = [
        ProtocolState(name=state_name, is_initial=(idx == 0), is_final=(idx == len(state_names) - 1))
        for idx, state_name in enumerate(state_names)
    ]
    event_names = draw(st.lists(_non_empty_name_strategy(16), min_size=1, max_size=3, unique=True))
    transition_count = draw(st.integers(min_value=0, max_value=4))
    transitions = []
    for _ in range(transition_count):
        transitions.append(
            ProtocolTransition(
                from_state=draw(st.sampled_from(state_names)),
                to_state=draw(st.sampled_from(state_names)),
                event=draw(st.sampled_from(event_names)),
            )
        )
    return ProtocolStateMachine(name=name, states=states, transitions=transitions)


def test_normalize_name_v2_aggressive_examples():
    assert (
        normalize_name_v2("BFD Session State Machine (RFC 5880 §6.1 Overview)", aggressive=True)
        == "bfd session state machine"
    )
    assert (
        normalize_name_v2("BFD Backward Compatibility Version Negotiation (Auto-Versioning)", aggressive=True)
        == "bfd backward compatibility version negotiation auto versioning"
    )
    assert normalize_state_name("Asynchronous") == "async"
    assert normalize_transition_key(
        ProtocolTransition(from_state="Down", to_state="Up", event="Receive BFD Control packet")
    ) == ("down", "up", "control receive")


def test_name_similarity_supports_subset_matching_and_single_token_guard():
    left = ProtocolStateMachine(name="BFD Session Reset and Administrative Control State Machine")
    right = ProtocolStateMachine(name="BFD Administrative Control")
    assert name_similarity(left, right) >= 0.8

    single_token = ProtocolStateMachine(name="Session")
    multi_token = ProtocolStateMachine(name="BFD Session")
    # min token count < 2, subset ratio disabled -> falls back to jaccard
    assert name_similarity(single_token, multi_token) == 0.5


def test_normalize_transition_key_handles_wording_variants():
    timer_a = ProtocolTransition(from_state="Init", to_state="Up", event="timer expires")
    timer_b = ProtocolTransition(from_state="Init", to_state="Up", event="when the timer has expired")
    recv_a = ProtocolTransition(from_state="Down", to_state="Init", event="receive BFD Control packet")
    recv_b = ProtocolTransition(from_state="Down", to_state="Init", event="BFD Control packet is received")

    assert normalize_transition_key(timer_a) == normalize_transition_key(timer_b)
    assert normalize_transition_key(recv_a) == normalize_transition_key(recv_b)


@given(text=st.text(max_size=50))
@settings(max_examples=100)
def test_normalize_name_v2_is_backward_compatible_in_conservative_mode(text: str):
    assert normalize_name_v2(text, aggressive=False) == normalize_name(text)


@given(sm_a=protocol_state_machine_strategy(), sm_b=protocol_state_machine_strategy())
@settings(max_examples=100)
def test_compute_sm_similarity_is_symmetric_and_bounded(
    sm_a: ProtocolStateMachine,
    sm_b: ProtocolStateMachine,
):
    scores_ab = compute_sm_similarity(sm_a, sm_b)
    scores_ba = compute_sm_similarity(sm_b, sm_a)

    assert scores_ab == scores_ba
    assert compute_sm_similarity(sm_a, sm_a) == {"name": 1.0, "states": 1.0, "transitions": 1.0}
    assert all(0.0 <= value <= 1.0 for value in scores_ab.values())


@given(sm_a=protocol_state_machine_strategy(), sm_b=protocol_state_machine_strategy())
@settings(max_examples=100)
def test_should_merge_state_machines_is_symmetric(
    sm_a: ProtocolStateMachine,
    sm_b: ProtocolStateMachine,
):
    assert should_merge_state_machines(sm_a, sm_b) == should_merge_state_machines(sm_b, sm_a)


def test_should_merge_state_machines_accepts_real_duplicate_and_rejects_distinct_logic():
    session_a = ProtocolStateMachine(
        name="BFD Session State Machine (RFC 5880 §6.2)",
        states=[ProtocolState(name="Down"), ProtocolState(name="Init"), ProtocolState(name="Up")],
        transitions=[
            ProtocolTransition(from_state="Down", to_state="Init", event="Receive Control Packet"),
            ProtocolTransition(from_state="Init", to_state="Up", event="Receive Control Packet"),
        ],
    )
    session_b = ProtocolStateMachine(
        name="BFD Session State Machine",
        states=[ProtocolState(name="Down"), ProtocolState(name="Init"), ProtocolState(name="Up")],
        transitions=[
            ProtocolTransition(from_state="Down", to_state="Init", event="Receive BFD Control packet"),
            ProtocolTransition(from_state="Init", to_state="Up", event="Receive BFD Control packet"),
        ],
    )
    demand_mode = ProtocolStateMachine(
        name="BFD Demand Mode and Poll Sequence",
        states=[ProtocolState(name="Poll"), ProtocolState(name="Demand")],
        transitions=[
            ProtocolTransition(from_state="Poll", to_state="Demand", event="Poll Sequence Complete"),
        ],
    )

    assert should_merge_state_machines(session_a, session_b) is True
    assert should_merge_state_machines(session_a, demand_mode) is False
