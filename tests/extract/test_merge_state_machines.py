"""Tests for state-machine merge behavior."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.merge import build_merge_report, merge_state_machines
from src.extract.sm_similarity import cluster_state_machines
from src.models import ProtocolState, ProtocolStateMachine, ProtocolTransition


def _session_variant(name: str, pages: list[int], condition: str = "", actions: list[str] | None = None):
    return ProtocolStateMachine(
        name=name,
        states=[
            ProtocolState(name="Down", is_initial=True),
            ProtocolState(name="Init"),
            ProtocolState(name="Up", is_final=True),
        ],
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="Receive Control Packet",
                condition=condition,
                actions=actions or [],
            ),
            ProtocolTransition(
                from_state="Init",
                to_state="Up",
                event="Receive Control Packet",
                actions=["advance session"],
            ),
        ],
        source_pages=pages,
    )


@st.composite
def clusterable_state_machine_list(draw):
    count = draw(st.integers(min_value=2, max_value=4))
    names = [
        "BFD Session State Machine",
        "BFD Session State Machine (RFC 5880 §6.2)",
        "BFD Session State Machine (RFC 5880 §6.1 Overview)",
        "BFD Session State Machine (RFC 5880 §6.8.5 excerpt)",
    ]
    machines = []
    for idx in range(count):
        pages = draw(st.lists(st.integers(min_value=1, max_value=40), min_size=1, max_size=3, unique=True))
        condition = draw(st.text(max_size=20))
        actions = draw(st.lists(st.text(max_size=20), max_size=2))
        machines.append(_session_variant(names[idx], pages, condition=condition, actions=actions))
    return machines


@given(state_machines=clusterable_state_machine_list())
@settings(max_examples=100)
def test_cluster_state_machines_partitions_indices(state_machines: list[ProtocolStateMachine]):
    clusters = cluster_state_machines(state_machines)
    flattened = [index for cluster in clusters for index in cluster]

    assert flattened == sorted(flattened)
    assert flattened == list(range(len(state_machines)))
    assert len(flattened) == len(set(flattened))


@given(group=clusterable_state_machine_list())
@settings(max_examples=100)
def test_merge_state_machines_does_not_lose_state_transition_or_page_coverage(
    group: list[ProtocolStateMachine],
):
    merged, reports, warnings, near_miss = merge_state_machines(group)

    assert warnings == []
    assert len(merged) == 1
    assert near_miss == []
    merged_sm = merged[0]
    expected_pages = sorted({page for item in group for page in item.source_pages})
    expected_states = {
        state.name.lower()
        for item in group
        for state in item.states
    }
    expected_transitions = {
        (transition.from_state, transition.to_state, transition.event)
        for item in group
        for transition in item.transitions
    }

    assert merged_sm.source_pages == expected_pages
    assert expected_states.issubset({state.name.lower() for state in merged_sm.states})
    assert expected_transitions.issubset(
        {
            (transition.from_state, transition.to_state, transition.event)
            for transition in merged_sm.transitions
        }
    )
    assert reports[0]["states_after"] <= reports[0]["states_before"]
    assert reports[0]["transitions_after"] <= reports[0]["transitions_before"]


@given(group=clusterable_state_machine_list())
@settings(max_examples=100)
def test_merge_state_machines_is_idempotent(group: list[ProtocolStateMachine]):
    merged_once, _, _, _ = merge_state_machines(group)
    merged_twice, _, _, _ = merge_state_machines(merged_once)

    assert [item.model_dump() for item in merged_twice] == [item.model_dump() for item in merged_once]


def test_merge_state_machines_collects_near_miss_and_excludes_merged_pairs():
    session_like = _session_variant("BFD Session State Machine", [1])
    admin_related = ProtocolStateMachine(
        name="BFD Administrative Session Control",
        states=[ProtocolState(name="Down"), ProtocolState(name="AdminDown"), ProtocolState(name="Up")],
        transitions=[
            ProtocolTransition(from_state="Down", to_state="AdminDown", event="admin disable session"),
            ProtocolTransition(from_state="AdminDown", to_state="Up", event="admin enable session"),
        ],
        source_pages=[2],
    )
    demand = ProtocolStateMachine(
        name="BFD Demand Mode",
        states=[ProtocolState(name="Poll"), ProtocolState(name="Demand")],
        transitions=[ProtocolTransition(from_state="Poll", to_state="Demand", event="poll sequence complete")],
        source_pages=[3],
    )
    merged, _, _, near_miss = merge_state_machines([session_like, admin_related, demand])

    assert len(merged) >= 2
    assert any(item["pair"] == [0, 1] for item in near_miss)
    assert all(item["pair"] != [0, 2] for item in near_miss)


def test_merge_state_machines_near_miss_ignores_reviewed_pairs():
    session_a = _session_variant("BFD Session State Machine A", [1])
    session_b = _session_variant("BFD Session State Machine B", [2])
    merged, _, _, near_miss = merge_state_machines(
        [session_a, session_b],
        review_decisions=[{"object_type": "state_machine", "pair": [0, 1], "decision": "keep_separate"}],
    )

    assert len(merged) == 2
    assert near_miss == []


def test_build_merge_report_is_backward_compatible_and_can_include_state_machine_groups():
    base_report = build_merge_report(
        pre={"state_machine": 2},
        dropped={"state_machine": 0},
        post_filter={"state_machine": 2},
        post={"state_machine": 1},
        timer_groups=[],
        message_groups=[],
    )
    assert "state_machine" not in base_report["merged_groups"]

    report = build_merge_report(
        pre={"state_machine": 2},
        dropped={"state_machine": 0},
        post_filter={"state_machine": 2},
        post={"state_machine": 1},
        timer_groups=[],
        message_groups=[],
        state_machine_groups=[
            {
                "canonical_name": "BFD Session State Machine",
                "merged_from": ["A", "B"],
                "similarity_scores": [],
                "hard_constraint_met": True,
                "source_pages_union": [1, 2],
                "states_before": 6,
                "states_after": 3,
                "transitions_before": 4,
                "transitions_after": 2,
            }
        ],
    )

    assert report["merged_groups"]["state_machine"][0]["canonical_name"] == "BFD Session State Machine"
    report_with_near_miss = build_merge_report(
        pre={"state_machine": 2},
        dropped={"state_machine": 0},
        post_filter={"state_machine": 2},
        post={"state_machine": 1},
        timer_groups=[],
        message_groups=[],
        near_miss_summary={"sm_count": 1, "msg_count": 0},
    )
    assert report_with_near_miss["near_miss_summary"]["sm_count"] == 1
