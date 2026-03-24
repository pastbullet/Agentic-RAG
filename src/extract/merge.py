"""Merge logic for protocol extraction pipeline.

Responsibilities:
- ExtractionRecord: intermediate representation carrying provenance
- normalize_name: conservative name normalization for grouping
- normalize_name_v2: aggressive-but-safe normalization for similarity
- Empty-result filters
- merge_timers: same-name dedup with source_pages union
- merge_messages: same-name dedup with field dedup
- merge_messages_v2: fuzzy message dedup with safety guards
- merge_state_machines: similarity-based state-machine dedup
- build_merge_report: pre/post/dropped statistics
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from src.extract.sm_similarity import (
    cluster_state_machines,
    collect_sm_near_misses,
    compute_sm_similarity,
    normalize_state_name,
    normalize_transition_key,
)
from src.models import (
    ErrorRule,
    ProtocolField,
    ProtocolMessage,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
    ProcedureRule,
    TimerConfig,
)


# ── Intermediate record ───────────────────────────────────────────────────────


@dataclass
class ExtractionRecord:
    """Carries a single extractor output together with its provenance."""

    node_id: str
    title: str
    label: str
    confidence: float
    source_pages: list[int]
    payload: dict  # model_dump() of the extracted pydantic object


# ── Name normalization ────────────────────────────────────────────────────────


def normalize_name(text: str) -> str:
    """Conservative normalization used only for same-name grouping.

    Operations (in order):
    1. Strip and lowercase
    2. Remove bare RFC references like "rfc 5880"
    3. Remove section numbers like 6.8.5, §6.2, (RFC 5880 §6.2), 6.8.5–6.8.6
    4. Replace non-alphanumeric runs with a single space
    5. Collapse whitespace
    """
    text = (text or "").strip().lower()
    # bare RFC references
    text = re.sub(r"\brfc\s*\d+\b", " ", text)
    # section numbers with optional § and range, optionally wrapped in parens
    text = re.sub(r"[(\uff08]?\u00a7?[\d]+(?:\.[\d]+)*(?:[\-\u2013\u2014][\d]+(?:\.[\d]+)*)?[)\uff09]?", " ", text)
    # non-alphanumeric → space
    text = re.sub(r"[^a-z0-9]+", " ", text)
    # collapse
    text = re.sub(r"\s+", " ", text).strip()
    return text


_NOISE_WORDS = {"excerpt", "overview", "summary"}

FIELD_ABBREVIATION_MAP: dict[str, str] = {
    "vers": "version",
    "ver": "version",
    "diag": "diagnostic",
    "auth": "authentication",
    "len": "length",
    "seq": "sequence",
    "num": "number",
    "addr": "address",
    "src": "source",
    "dst": "destination",
    "msg": "message",
    "pkt": "packet",
    "hdr": "header",
    "ctl": "control",
    "cfg": "configuration",
}


def _clean_bracket_content(text: str) -> str:
    cleaned = re.sub(r"\brfc\s*\d+\b", " ", text.lower())
    cleaned = re.sub(
        r"[\(（]?§?[\d]+(?:\.[\d]+)*(?:[\-\u2013\u2014][\d]+(?:\.[\d]+)*)?[\)）]?",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_name_v2(text: str, aggressive: bool = False) -> str:
    """Enhanced normalization for similarity-based grouping.

    aggressive=False is intentionally identical to normalize_name().
    aggressive=True removes reference-only parentheticals and a small set of
    non-semantic modifiers, but preserves domain words.
    """
    conservative = normalize_name(text)
    if not aggressive:
        return conservative

    raw = (text or "").strip().lower()

    def _strip_reference_parenthetical(match: re.Match[str]) -> str:
        inner = match.group(1)
        cleaned = _clean_bracket_content(inner)
        if not cleaned:
            return " "
        tokens = set(cleaned.split())
        if tokens and tokens.issubset(_NOISE_WORDS):
            return " "
        return match.group(0)

    raw = re.sub(r"[\(（]([^()\uff08\uff09]*)[\)）]", _strip_reference_parenthetical, raw)
    raw = re.sub(r"\b(?:excerpt|overview|summary)\b", " ", raw)
    normalized = normalize_name(raw)
    return normalized or conservative


# ── Empty-result predicates ───────────────────────────────────────────────────


def is_empty_state_machine(obj: ProtocolStateMachine) -> bool:
    return not obj.states and not obj.transitions


def is_empty_message(obj: ProtocolMessage) -> bool:
    return not obj.fields


def is_empty_procedure(obj: ProcedureRule) -> bool:
    return not obj.steps


def is_empty_timer(obj: TimerConfig) -> bool:
    return not (obj.timeout_value or obj.trigger_action or obj.description)


def is_empty_error(obj: ErrorRule) -> bool:
    return not (obj.handling_action or obj.description)


# ── Timer merge ───────────────────────────────────────────────────────────────


def merge_timers(timers: list[TimerConfig]) -> tuple[list[TimerConfig], list[dict]]:
    """Group timers by normalized name; merge source_pages and pick longest text fields.

    Returns:
        merged: deduplicated TimerConfig list
        groups: list of merge-group dicts for the report
    """
    groups: dict[str, list[TimerConfig]] = {}
    for t in timers:
        key = normalize_name(t.timer_name)
        groups.setdefault(key, []).append(t)

    merged: list[TimerConfig] = []
    report_groups: list[dict] = []

    for key, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        # union source_pages
        all_pages: list[int] = []
        for t in group:
            for p in t.source_pages:
                if p not in all_pages:
                    all_pages.append(p)
        all_pages.sort()

        # pick longest non-empty text fields
        description = max((t.description for t in group), key=len, default="")
        trigger_action = max((t.trigger_action for t in group), key=len, default="")

        # collect all distinct timeout_value variants for the report
        timeout_variants = list(
            dict.fromkeys(t.timeout_value for t in group if t.timeout_value)
        )
        # keep the longest as canonical
        timeout_value = max(timeout_variants, key=len) if timeout_variants else ""

        representative = group[0]
        result = TimerConfig(
            timer_name=representative.timer_name,
            timeout_value=timeout_value,
            trigger_action=trigger_action,
            description=description,
            source_pages=all_pages,
        )
        merged.append(result)
        report_groups.append(
            {
                "normalized_key": key,
                "merged_from": [t.timer_name for t in group],
                "source_pages_union": all_pages,
                "timeout_value_variants": timeout_variants,
            }
        )

    return merged, report_groups


# ── Message merge ─────────────────────────────────────────────────────────────


def _merge_fields(fields_lists: list[list[ProtocolField]]) -> list[ProtocolField]:
    """Deduplicate fields by normalized name; prefer richer entries."""
    seen: dict[str, ProtocolField] = {}
    for fields in fields_lists:
        for f in fields:
            key = normalize_name(f.name)
            if key not in seen:
                seen[key] = f
            else:
                existing = seen[key]
                # prefer non-null size_bits
                size_bits = f.size_bits if existing.size_bits is None and f.size_bits is not None else existing.size_bits
                # prefer longer description
                description = f.description if len(f.description) > len(existing.description) else existing.description
                # prefer non-empty type
                ftype = f.type if not existing.type and f.type else existing.type
                seen[key] = ProtocolField(
                    name=existing.name,  # keep original casing from first occurrence
                    type=ftype,
                    size_bits=size_bits,
                    description=description,
                )
    return list(seen.values())


def _choose_message_representative(group: list[ProtocolMessage]) -> ProtocolMessage:
    return max(
        group,
        key=lambda m: (len(m.fields), len(m.source_pages), len(m.name)),
    )


def _sorted_page_union(page_lists: list[list[int]]) -> list[int]:
    return sorted({page for pages in page_lists for page in pages})


def _message_name_tokens(name: str) -> set[str]:
    tokens = normalize_name_v2(name, aggressive=True).split()
    return {token for token in tokens if token not in {"generic", "format", "section"}}


def _message_name_similarity(name_a: str, name_b: str) -> float:
    tokens_a = _message_name_tokens(name_a)
    tokens_b = _message_name_tokens(name_b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def normalize_field_name(name: str) -> str:
    text = (name or "").strip().lower()
    if not text:
        return ""
    tokens = re.findall(r"[a-z0-9]+", text)
    if not tokens:
        return ""
    canonical_tokens = sorted({FIELD_ABBREVIATION_MAP.get(token, token) for token in tokens})
    return " ".join(canonical_tokens)


def _field_name_jaccard(msg_a: ProtocolMessage, msg_b: ProtocolMessage) -> float:
    names_a = {normalize_field_name(field.name) for field in msg_a.fields if normalize_field_name(field.name)}
    names_b = {normalize_field_name(field.name) for field in msg_b.fields if normalize_field_name(field.name)}
    if not names_a and not names_b:
        return 1.0
    if not names_a or not names_b:
        return 0.0
    return len(names_a & names_b) / len(names_a | names_b)


MSG_EXCLUSIVE_KEYWORDS = [
    {"md5", "sha1"},
    {"simple password", "keyed"},
    {"echo", "control"},
]


def _has_exclusive_keywords(name_a: str, name_b: str) -> bool:
    text_a = normalize_name_v2(name_a, aggressive=True)
    text_b = normalize_name_v2(name_b, aggressive=True)
    for group in MSG_EXCLUSIVE_KEYWORDS:
        if len(group) != 2:
            continue
        first, second = tuple(group)
        if (first in text_a and second in text_b) or (second in text_a and first in text_b):
            return True
    return False


def _normalize_pair(pair: list[int] | tuple[int, int]) -> tuple[int, int] | None:
    if len(pair) != 2:
        return None
    left, right = int(pair[0]), int(pair[1])
    if left == right:
        return None
    return (left, right) if left < right else (right, left)


def _collect_pair_decisions(
    review_decisions: list[dict[str, Any]] | None,
    object_type: str,
) -> dict[tuple[int, int], str]:
    pair_decisions: dict[tuple[int, int], str] = {}
    if not review_decisions:
        return pair_decisions
    for item in review_decisions:
        if not isinstance(item, dict):
            continue
        if item.get("object_type") != object_type:
            continue
        decision = str(item.get("decision", "")).strip().lower()
        if decision not in {"merge", "keep_separate"}:
            continue
        pair_raw = item.get("pair") or item.get("pair_id")
        if not isinstance(pair_raw, (list, tuple)):
            continue
        pair = _normalize_pair(pair_raw)
        if pair is None:
            continue
        pair_decisions[pair] = decision
    return pair_decisions


def merge_messages(messages: list[ProtocolMessage]) -> tuple[list[ProtocolMessage], list[dict]]:
    """Group messages by normalized name; merge fields and source_pages.

    Returns:
        merged: deduplicated ProtocolMessage list
        groups: list of merge-group dicts for the report
    """
    groups: dict[str, list[ProtocolMessage]] = {}
    for m in messages:
        key = normalize_name(m.name)
        groups.setdefault(key, []).append(m)

    merged: list[ProtocolMessage] = []
    report_groups: list[dict] = []

    for key, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        all_pages: list[int] = []
        for m in group:
            for p in m.source_pages:
                if p not in all_pages:
                    all_pages.append(p)
        all_pages.sort()

        merged_fields = _merge_fields([m.fields for m in group])

        # canonical name: pick the richest message deterministically
        representative = _choose_message_representative(group)
        result = ProtocolMessage(
            name=representative.name,
            fields=merged_fields,
            source_pages=all_pages,
        )
        merged.append(result)
        report_groups.append(
            {
                "normalized_key": key,
                "merged_from": [m.name for m in group],
                "source_pages_union": all_pages,
                "field_count_before": sum(len(m.fields) for m in group),
                "field_count_after": len(merged_fields),
            }
        )

    return merged, report_groups


def merge_messages_v2(
    messages: list[ProtocolMessage],
    enable_fuzzy_match: bool = True,
    name_similarity_threshold: float = 0.7,
    field_jaccard_threshold: float = 0.5,
    review_decisions: list[dict[str, Any]] | None = None,
) -> tuple[list[ProtocolMessage], list[dict], list[dict]]:
    """Enhanced message merging with conservative fuzzy matching."""
    if not enable_fuzzy_match:
        merged, groups = merge_messages(messages)
        return merged, groups, []

    exact_groups: dict[str, list[ProtocolMessage]] = defaultdict(list)
    for message in messages:
        exact_groups[normalize_name(message.name)].append(message)

    base_units: list[dict[str, Any]] = []
    initial_groups: list[dict] = []
    for key, group in exact_groups.items():
        pages = _sorted_page_union([message.source_pages for message in group])
        fields = _merge_fields([message.fields for message in group])
        representative = _choose_message_representative(group)
        merged_message = ProtocolMessage(name=representative.name, fields=fields, source_pages=pages)
        base_units.append(
            {
                "message": merged_message,
                "originals": group,
            }
        )
        if len(group) > 1:
            initial_groups.append(
                {
                    "normalized_key": key,
                    "merged_from": [item.name for item in group],
                    "source_pages_union": pages,
                    "field_count_before": sum(len(item.fields) for item in group),
                    "field_count_after": len(fields),
                }
            )

    count = len(base_units)
    parents = list(range(count))
    pair_decisions = _collect_pair_decisions(review_decisions, object_type="message")

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
            left = base_units[i]["message"]
            right = base_units[j]["message"]
            decision = pair_decisions.get((i, j))
            if decision == "keep_separate":
                continue
            if decision == "merge":
                union(i, j)
                continue
            if _has_exclusive_keywords(left.name, right.name):
                continue
            name_similarity = _message_name_similarity(left.name, right.name)
            field_jaccard = _field_name_jaccard(left, right)
            if field_jaccard >= 0.8:
                union(i, j)
                continue
            if name_similarity < name_similarity_threshold:
                continue
            if field_jaccard < field_jaccard_threshold:
                continue
            union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for index in range(count):
        clusters[find(index)].append(index)

    merged: list[ProtocolMessage] = []
    report_groups = list(initial_groups)

    for _, cluster in sorted(clusters.items(), key=lambda item: min(item[1])):
        originals = [
            original
            for unit_index in cluster
            for original in base_units[unit_index]["originals"]
        ]
        if len(cluster) == 1:
            merged.append(base_units[cluster[0]]["message"])
            continue

        pages = _sorted_page_union([message.source_pages for message in originals])
        fields = _merge_fields([message.fields for message in originals])
        representative = _choose_message_representative(originals)
        merged_message = ProtocolMessage(
            name=representative.name,
            fields=fields,
            source_pages=pages,
        )
        merged.append(merged_message)
        report_groups.append(
            {
                "normalized_key": normalize_name(representative.name),
                "merged_from": [message.name for message in originals],
                "source_pages_union": pages,
                "field_count_before": sum(len(message.fields) for message in originals),
                "field_count_after": len(fields),
            }
        )

    cluster_roots = {index: find(index) for index in range(count)}
    near_miss: list[dict[str, Any]] = []
    for i in range(count):
        for j in range(i + 1, count):
            if cluster_roots[i] == cluster_roots[j]:
                continue
            if (i, j) in pair_decisions:
                continue
            left = base_units[i]["message"]
            right = base_units[j]["message"]
            name_similarity = _message_name_similarity(left.name, right.name)
            field_jaccard = _field_name_jaccard(left, right)
            if name_similarity < 0.3 and field_jaccard < 0.3:
                continue
            left_fields = {
                normalize_field_name(field.name)
                for field in left.fields
                if normalize_field_name(field.name)
            }
            right_fields = {
                normalize_field_name(field.name)
                for field in right.fields
                if normalize_field_name(field.name)
            }
            near_miss.append(
                {
                    "pair": [i, j],
                    "names": [left.name, right.name],
                    "left": left.model_dump(),
                    "right": right.model_dump(),
                    "name_similarity": name_similarity,
                    "field_jaccard": field_jaccard,
                    "exclusive_blocked": _has_exclusive_keywords(left.name, right.name),
                    "diff": {
                        "fields_only_left": sorted(left_fields - right_fields),
                        "fields_only_right": sorted(right_fields - left_fields),
                    },
                }
            )
    near_miss.sort(
        key=lambda item: (max(item["name_similarity"], item["field_jaccard"]), item["field_jaccard"]),
        reverse=True,
    )

    return merged, report_groups, near_miss


def _choose_canonical_sm_name(group: list[ProtocolStateMachine]) -> str:
    return min(
        group,
        key=lambda sm: (
            len(normalize_name_v2(sm.name, aggressive=True)),
            len(sm.name),
            -len(set(sm.source_pages)),
        ),
    ).name


def _merge_sm_group(group: list[ProtocolStateMachine]) -> ProtocolStateMachine:
    """Merge a similarity cluster into one state machine."""
    canonical_name = _choose_canonical_sm_name(group)

    merged_states: dict[str, ProtocolState] = {}
    for state_machine in group:
        for state in state_machine.states:
            key = normalize_state_name(state.name)
            if key not in merged_states:
                merged_states[key] = ProtocolState(
                    name=state.name,
                    description=state.description,
                    is_initial=state.is_initial,
                    is_final=state.is_final,
                )
                continue
            existing = merged_states[key]
            if len(state.description) > len(existing.description):
                existing.description = state.description
            existing.is_initial = existing.is_initial or state.is_initial
            existing.is_final = existing.is_final or state.is_final

    merged_transitions: dict[tuple[str, str, str], ProtocolTransition] = {}
    for state_machine in group:
        for transition in state_machine.transitions:
            key = normalize_transition_key(transition)
            if key not in merged_transitions:
                merged_transitions[key] = ProtocolTransition(
                    from_state=transition.from_state,
                    to_state=transition.to_state,
                    event=transition.event,
                    condition=transition.condition,
                    actions=list(transition.actions),
                )
                continue
            existing = merged_transitions[key]
            if len(transition.condition) > len(existing.condition):
                existing.condition = transition.condition
            if len(transition.actions) > len(existing.actions):
                existing.actions = list(transition.actions)

    return ProtocolStateMachine(
        name=canonical_name,
        states=list(merged_states.values()),
        transitions=list(merged_transitions.values()),
        source_pages=_sorted_page_union([state_machine.source_pages for state_machine in group]),
    )


def merge_state_machines(
    state_machines: list[ProtocolStateMachine],
    review_decisions: list[dict[str, Any]] | None = None,
) -> tuple[list[ProtocolStateMachine], list[dict], list[str], list[dict]]:
    """Merge similar state machines conservatively."""
    warnings: list[str] = []
    pair_decisions = _collect_pair_decisions(review_decisions, object_type="state_machine")
    try:
        clusters = cluster_state_machines(state_machines, pair_decisions=pair_decisions)
    except Exception as exc:
        warnings.append(f"cluster_state_machines failed: {exc}")
        return state_machines, [], warnings, []

    near_miss = collect_sm_near_misses(
        state_machines,
        clusters=clusters,
        ignored_pairs=set(pair_decisions),
    )

    merged: list[ProtocolStateMachine] = []
    group_reports: list[dict] = []

    for cluster in clusters:
        group = [state_machines[index] for index in cluster]
        if len(group) == 1:
            merged.append(group[0])
            continue
        try:
            merged_group = _merge_sm_group(group)
            merged.append(merged_group)
            similarity_scores = []
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    scores = compute_sm_similarity(group[i], group[j])
                    similarity_scores.append(
                        {
                            "left": group[i].name,
                            "right": group[j].name,
                            "scores": scores,
                        }
                    )
            group_reports.append(
                {
                    "canonical_name": merged_group.name,
                    "merged_from": [item.name for item in group],
                    "similarity_scores": similarity_scores,
                    "hard_constraint_met": True,
                    "source_pages_union": merged_group.source_pages,
                    "states_before": sum(len(item.states) for item in group),
                    "states_after": len(merged_group.states),
                    "transitions_before": sum(len(item.transitions) for item in group),
                    "transitions_after": len(merged_group.transitions),
                }
            )
        except Exception as exc:
            warnings.append(
                f"merge_state_machines failed for group {[item.name for item in group]}: {exc}"
            )
            merged.extend(group)

    return merged, group_reports, warnings, near_miss


# ── Merge report ──────────────────────────────────────────────────────────────


def build_merge_report(
    pre: dict[str, int],
    dropped: dict[str, int],
    post_filter: dict[str, int],
    post: dict[str, int],
    timer_groups: list[dict],
    message_groups: list[dict],
    state_machine_groups: list[dict] | None = None,
    near_miss_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "pre_merge_counts": pre,
        "dropped_empty_counts": dropped,
        "post_filter_counts": post_filter,
        "post_merge_counts": post,
        "merged_groups": {
            "timer": timer_groups,
            "message": message_groups,
        },
    }
    if state_machine_groups is not None:
        report["merged_groups"]["state_machine"] = state_machine_groups
    if near_miss_summary is not None:
        report["near_miss_summary"] = near_miss_summary
    return report
