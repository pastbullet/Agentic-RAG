"""State-machine similarity and clustering helpers."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from src.models import ProtocolStateMachine, ProtocolTransition

logger = logging.getLogger("extract")

_STATE_SYNONYMS = {
    "asynchronous": "async",
    "pollsequence": "poll",
}

_EVENT_SYNONYMS = {
    "expires": "expire",
    "expired": "expire",
    "receive": "receive",
    "received": "receive",
    "receiving": "receive",
    "administrative": "admin",
}

_EVENT_STOPWORDS = {
    "a",
    "an",
    "and",
    "bfd",
    "event",
    "for",
    "if",
    "in",
    "is",
    "of",
    "on",
    "or",
    "packet",
    "packets",
    "session",
    "state",
    "the",
    "that",
    "then",
    "this",
    "time",
    "to",
    "with",
    "when",
    "was",
    "were",
    "has",
    "have",
    "had",
    "been",
    "its",
}

NEAR_MISS_MIN_SCORE = 0.3


def normalize_state_name(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return _STATE_SYNONYMS.get(text, text)


def normalize_transition_key(t: ProtocolTransition) -> tuple[str, str, str]:
    from_state = normalize_state_name(t.from_state)
    to_state = normalize_state_name(t.to_state)
    raw_tokens = re.findall(r"[a-z0-9]+", (t.event or "").lower())
    normalized_tokens: list[str] = []
    for token in raw_tokens:
        token = _EVENT_SYNONYMS.get(token, token)
        if token in _EVENT_STOPWORDS:
            continue
        if token not in normalized_tokens:
            normalized_tokens.append(token)
    event_tokens = sorted(normalized_tokens)
    if not event_tokens:
        event_keyword = "none"
    else:
        event_keyword = " ".join(event_tokens)
    return from_state, to_state, event_keyword


def _normalize_name_tokens(name: str) -> set[str]:
    from src.extract.merge import normalize_name_v2

    normalized = normalize_name_v2(name, aggressive=True)
    return {token for token in normalized.split() if token}


def name_similarity(sm_a: ProtocolStateMachine, sm_b: ProtocolStateMachine) -> float:
    tokens_a = _normalize_name_tokens(sm_a.name)
    tokens_b = _normalize_name_tokens(sm_b.name)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a & tokens_b)
    jaccard = overlap / len(tokens_a | tokens_b)
    subset_ratio = 0.0
    min_tokens = min(len(tokens_a), len(tokens_b))
    if min_tokens >= 2:
        subset_ratio = overlap / min_tokens
    return max(jaccard, subset_ratio)


def state_overlap(sm_a: ProtocolStateMachine, sm_b: ProtocolStateMachine) -> float:
    states_a = {normalize_state_name(state.name) for state in sm_a.states if normalize_state_name(state.name)}
    states_b = {normalize_state_name(state.name) for state in sm_b.states if normalize_state_name(state.name)}
    if not states_a and not states_b:
        return 1.0
    if not states_a or not states_b:
        return 0.0
    return len(states_a & states_b) / len(states_a | states_b)


def transition_overlap(sm_a: ProtocolStateMachine, sm_b: ProtocolStateMachine) -> float:
    transitions_a = {normalize_transition_key(transition) for transition in sm_a.transitions}
    transitions_b = {normalize_transition_key(transition) for transition in sm_b.transitions}
    if not transitions_a and not transitions_b:
        return 1.0
    if not transitions_a or not transitions_b:
        return 0.0
    return len(transitions_a & transitions_b) / len(transitions_a | transitions_b)


def compute_sm_similarity(
    sm_a: ProtocolStateMachine,
    sm_b: ProtocolStateMachine,
) -> dict[str, float]:
    try:
        if not isinstance(sm_a, ProtocolStateMachine) or not isinstance(sm_b, ProtocolStateMachine):
            raise TypeError("state-machine inputs must be ProtocolStateMachine")
        return {
            "name": name_similarity(sm_a, sm_b),
            "states": state_overlap(sm_a, sm_b),
            "transitions": transition_overlap(sm_a, sm_b),
        }
    except Exception as exc:
        logger.warning("compute_sm_similarity failed: %s", exc)
        return {"name": 0.0, "states": 0.0, "transitions": 0.0}


SM_MERGE_THRESHOLD = 0.65


def should_merge_state_machines(
    sm_a: ProtocolStateMachine,
    sm_b: ProtocolStateMachine,
    scores: dict[str, float] | None = None,
) -> bool:
    try:
        if scores is None:
            scores = compute_sm_similarity(sm_a, sm_b)
        required = {"name", "states", "transitions"}
        if not required.issubset(scores):
            logger.warning("should_merge_state_machines received incomplete scores: %s", scores)
            return False
        name = scores["name"]
        states = scores["states"]
        transitions = scores["transitions"]
        hard_constraint_met = (
            (states >= 0.6 and name >= 0.4)
            or (transitions >= 0.5 and states >= 0.3)
            or (name >= 0.75 and states >= 0.4)
        )
        if not hard_constraint_met:
            return False
        weighted_score = 0.3 * name + 0.35 * states + 0.35 * transitions
        return weighted_score >= SM_MERGE_THRESHOLD
    except Exception as exc:
        logger.warning("should_merge_state_machines failed: %s", exc)
        return False


def _weighted_score(scores: dict[str, float]) -> float:
    return 0.3 * scores["name"] + 0.35 * scores["states"] + 0.35 * scores["transitions"]


def _unmet_hard_constraints(scores: dict[str, float]) -> list[str]:
    name = scores["name"]
    states = scores["states"]
    transitions = scores["transitions"]
    unmet: list[str] = []
    if not (states >= 0.6 and name >= 0.4):
        unmet.append("A")
    if not (transitions >= 0.5 and states >= 0.3):
        unmet.append("B")
    if not (name >= 0.75 and states >= 0.4):
        unmet.append("C")
    return unmet


def _state_diff(sm_a: ProtocolStateMachine, sm_b: ProtocolStateMachine) -> dict[str, list[str]]:
    states_a = {normalize_state_name(state.name) for state in sm_a.states if normalize_state_name(state.name)}
    states_b = {normalize_state_name(state.name) for state in sm_b.states if normalize_state_name(state.name)}
    return {
        "states_only_left": sorted(states_a - states_b),
        "states_only_right": sorted(states_b - states_a),
    }


def _transition_diff(sm_a: ProtocolStateMachine, sm_b: ProtocolStateMachine) -> dict[str, int]:
    transitions_a = {normalize_transition_key(transition) for transition in sm_a.transitions}
    transitions_b = {normalize_transition_key(transition) for transition in sm_b.transitions}
    return {
        "transitions_only_left_count": len(transitions_a - transitions_b),
        "transitions_only_right_count": len(transitions_b - transitions_a),
    }


def collect_sm_near_misses(
    state_machines: list[ProtocolStateMachine],
    clusters: list[list[int]] | None = None,
    min_score: float = NEAR_MISS_MIN_SCORE,
    ignored_pairs: set[tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    if not state_machines:
        return []

    if clusters is None:
        clusters = cluster_state_machines(state_machines)

    index_to_cluster: dict[int, int] = {}
    for cluster_idx, cluster in enumerate(clusters):
        for item_idx in cluster:
            index_to_cluster[item_idx] = cluster_idx

    near_misses: list[dict[str, Any]] = []
    ignored = ignored_pairs or set()
    count = len(state_machines)
    for i in range(count):
        for j in range(i + 1, count):
            if index_to_cluster.get(i) == index_to_cluster.get(j):
                continue
            if (i, j) in ignored:
                continue
            scores = compute_sm_similarity(state_machines[i], state_machines[j])
            weighted = _weighted_score(scores)
            if weighted < min_score:
                continue
            diff = _state_diff(state_machines[i], state_machines[j])
            diff.update(_transition_diff(state_machines[i], state_machines[j]))
            near_misses.append(
                {
                    "pair": [i, j],
                    "names": [state_machines[i].name, state_machines[j].name],
                    "left": state_machines[i].model_dump(),
                    "right": state_machines[j].model_dump(),
                    "scores": scores,
                    "weighted_score": weighted,
                    "unmet_constraints": _unmet_hard_constraints(scores),
                    "diff": diff,
                }
            )
    near_misses.sort(key=lambda item: item["weighted_score"], reverse=True)
    return near_misses


def cluster_state_machines(
    state_machines: list[ProtocolStateMachine],
    pair_decisions: dict[tuple[int, int], str] | None = None,
) -> list[list[int]]:
    try:
        count = len(state_machines)
        parents = list(range(count))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            root_left = find(left)
            root_right = find(right)
            if root_left == root_right:
                return
            if root_left < root_right:
                parents[root_right] = root_left
            else:
                parents[root_left] = root_right

        for i in range(count):
            for j in range(i + 1, count):
                decision = (pair_decisions or {}).get((i, j))
                if decision == "keep_separate":
                    continue
                if decision == "merge":
                    union(i, j)
                    continue
                scores = compute_sm_similarity(state_machines[i], state_machines[j])
                if should_merge_state_machines(state_machines[i], state_machines[j], scores):
                    union(i, j)

        clusters: dict[int, list[int]] = defaultdict(list)
        for index in range(count):
            clusters[find(index)].append(index)
        return [sorted(cluster) for _, cluster in sorted(clusters.items(), key=lambda item: min(item[1]))]
    except Exception as exc:
        logger.warning("cluster_state_machines failed, falling back to singletons: %s", exc)
        return [[index] for index in range(len(state_machines))]
