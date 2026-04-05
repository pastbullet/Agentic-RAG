"""FSM IR v1 lowering: ProtocolStateMachine → FSMIRv1.

Groups flat transitions into StateEventBlocks and attempts to parse
guards/actions into typed representations (BehaviorIR-lite).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from src.extract.state_context_materializer import canonicalize_context_name

from src.models import (
    FSMIRv1,
    IRDiagnostic,
    NormalizationStatus,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
    StateEventBlock,
    TransitionBranch,
    TypedAction,
    TypedGuard,
)


@dataclass
class ProtocolHint:
    known_states: list[str]
    known_timers: list[str]
    known_message_names: list[str]
    known_message_field_names: list[str]
    observed_context_tokens: list[str]


@dataclass
class RefineStats:
    triggered_count: int = 0
    accepted_guard_count: int = 0
    accepted_action_count: int = 0
    raw_branch_ratio_before: float = 0.0
    raw_branch_ratio_after: float = 0.0


# ---------------------------------------------------------------------------
# Guard parsing
# ---------------------------------------------------------------------------

# Patterns for typed guard extraction (case-insensitive).
_GUARD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "timer X expires / fired / timeout"
    (re.compile(r"\b(?:timer\s+)?(\w+)\s+(?:expires?|fired|timeout)\b", re.I), "timer_fired"),
    # "X flag is set" / "flag X is set"
    (re.compile(r"\b(?:flag\s+)?(\w+)\s+(?:flag\s+)?is\s+set\b", re.I), "flag_check"),
    # "field == value" / "field != value" / "field = value"
    (re.compile(r"\b(\w+(?:\.\w+)?)\s*(==|!=|>=|<=|>|<|=)\s*(\w+)\b"), "field_comparison"),
]

_DOTTED_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INT_LITERAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_HEX_LITERAL_RE = re.compile(r"^0[xX][0-9A-Fa-f]+$")


def _try_parse_guard(condition: str) -> TypedGuard | None:
    """Attempt to parse a natural-language condition into a TypedGuard."""
    text = condition.strip()
    if not text:
        return None

    for pattern, guard_type in _GUARD_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue

        if guard_type == "timer_fired":
            return TypedGuard(
                kind="timer_fired",
                ref_source="timer",
                field_ref=match.group(1),
                description=text,
            )
        if guard_type == "flag_check":
            return TypedGuard(
                kind="flag_check",
                ref_source="ctx",
                field_ref=match.group(1),
                description=text,
            )
        if guard_type == "field_comparison":
            field_ref = match.group(1)
            operator = match.group(2)
            value = match.group(3)
            if operator == "=":
                operator = "=="
            kind = "context_field_ne" if operator == "!=" else "context_field_eq"
            # Infer ref_source from field_ref prefix or kind
            if "." in field_ref:
                prefix = field_ref.split(".")[0].lower()
                ref_source = {"ctx": "ctx", "msg": "msg", "timer": "timer"}.get(prefix, "ctx")
            elif kind.startswith("context_"):
                ref_source = "ctx"
            else:
                ref_source = "unknown"
            return TypedGuard(
                kind=kind,
                ref_source=ref_source,
                field_ref=field_ref,
                operator=operator,
                value=value,
                description=text,
            )

    return None


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

_ACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "set state to X" / "transition to state X" / "enter state X" / "go to X state"
    (re.compile(r"\b(?:set\s+state\s+to|transition\s+to(?:\s+state)?|enter\s+(?:state\s+)?|go\s+to)\s+(\w+)", re.I), "set_state"),
    # "send X" / "emit X" / "transmit X message"
    (re.compile(r"\b(?:send|emit|transmit)\s+(?:a\s+)?(\w+(?:\s+\w+)?)\s*(?:message|packet|frame|pdu)?\b", re.I), "emit_message"),
    # "start timer X" / "restart timer X"
    (re.compile(r"\b(?:(?:re)?start|begin|initiate)\s+(?:timer\s+)?(\w+)\s*(?:timer)?\b", re.I), "start_timer"),
    # "cancel timer X" / "stop timer X"
    (re.compile(r"\b(?:cancel|stop|clear)\s+(?:timer\s+)?(\w+)\s*(?:timer)?\b", re.I), "cancel_timer"),
    # "set X to Y" / "update X to Y" / "X := Y"
    (re.compile(r"\b(?:set|update)\s+(\w+(?:\.\w+)?)\s+(?:to|=)\s+(\w+)\b", re.I), "update_field"),
]


def _try_parse_action(action_text: str) -> TypedAction | None:
    """Attempt to parse a natural-language action into a TypedAction."""
    text = action_text.strip()
    if not text:
        return None

    for pattern, action_type in _ACTION_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue

        if action_type == "set_state":
            return TypedAction(kind="set_state", ref_source="ctx", target=match.group(1), description=text)
        if action_type == "emit_message":
            return TypedAction(kind="emit_message", ref_source="msg", target=match.group(1), description=text)
        if action_type == "start_timer":
            return TypedAction(kind="start_timer", ref_source="timer", target=match.group(1), description=text)
        if action_type == "cancel_timer":
            return TypedAction(kind="cancel_timer", ref_source="timer", target=match.group(1), description=text)
        if action_type == "update_field":
            target = match.group(1)
            if "." in target:
                prefix = target.split(".")[0].lower()
                ref_source = {"ctx": "ctx", "msg": "msg"}.get(prefix, "ctx")
            else:
                ref_source = "ctx"
            return TypedAction(
                kind="update_field",
                ref_source=ref_source,
                target=target,
                value=match.group(2),
                description=text,
            )

    return None


# ---------------------------------------------------------------------------
# Branch readiness
# ---------------------------------------------------------------------------


def _collect_fsm_diagnostics(
    blocks: list[StateEventBlock],
    source_pages: list[int],
) -> list[IRDiagnostic]:
    diagnostics: list[IRDiagnostic] = []
    for block in blocks:
        for index, branch in enumerate(block.branches):
            if branch.guard_raw.strip() and branch.guard_typed is None:
                diagnostics.append(IRDiagnostic(
                    level="warning",
                    code="FSM_GUARD_UNPARSED",
                    message=f"Guard not typed in ({block.from_state}, {block.event}) branch {index}: {branch.guard_raw}",
                    source_pages=list(source_pages),
                ))
            for raw_action in branch.actions_raw:
                diagnostics.append(IRDiagnostic(
                    level="warning",
                    code="FSM_ACTION_UNPARSED",
                    message=f"Action not typed in ({block.from_state}, {block.event}) branch {index}: {raw_action}",
                    source_pages=list(source_pages),
                ))
    return diagnostics


def _compute_branch_readiness(branch: TransitionBranch) -> NormalizationStatus:
    """Compute readiness for a single branch."""
    has_typed_guard = branch.guard_typed is not None
    has_all_typed_actions = len(branch.actions_typed) > 0 and len(branch.actions_raw) == 0
    has_next_state = branch.next_state is not None

    if has_typed_guard and has_all_typed_actions and has_next_state:
        return NormalizationStatus.READY
    if has_next_state:
        return NormalizationStatus.DEGRADED_READY
    return NormalizationStatus.BLOCKED


def _compute_fsm_readiness(blocks: list[StateEventBlock]) -> NormalizationStatus:
    if not blocks:
        return NormalizationStatus.BLOCKED

    readiness_values = [branch.readiness for block in blocks for branch in block.branches]
    if all(value == NormalizationStatus.READY for value in readiness_values):
        return NormalizationStatus.READY
    if any(value == NormalizationStatus.BLOCKED for value in readiness_values):
        return NormalizationStatus.DEGRADED_READY
    return NormalizationStatus.DEGRADED_READY


# ---------------------------------------------------------------------------
# Core lowering
# ---------------------------------------------------------------------------


def _lower_transition(transition: ProtocolTransition) -> TransitionBranch:
    """Lower a single ProtocolTransition into a TransitionBranch."""
    guard_typed = _try_parse_guard(transition.condition)
    actions_typed: list[TypedAction] = []
    actions_raw: list[str] = []

    for action_text in transition.actions:
        typed = _try_parse_action(action_text)
        if typed is not None:
            actions_typed.append(typed)
        else:
            actions_raw.append(action_text)

    branch = TransitionBranch(
        guard_typed=guard_typed,
        guard_raw=transition.condition,
        actions_typed=actions_typed,
        actions_raw=actions_raw,
        next_state=transition.to_state if transition.to_state else None,
        notes=[],
    )
    branch.readiness = _compute_branch_readiness(branch)
    return branch


def lower_state_machine_to_fsm_ir(
    sm: ProtocolStateMachine,
    protocol_name: str,
) -> FSMIRv1:
    """Lower a ProtocolStateMachine into FSMIRv1 with grouped blocks."""
    # Group transitions by (from_state, event)
    groups: dict[tuple[str, str], list[ProtocolTransition]] = defaultdict(list)
    for transition in sm.transitions:
        key = (transition.from_state, transition.event)
        groups[key].append(transition)

    blocks: list[StateEventBlock] = []
    all_events: set[str] = set()

    for (from_state, event), transitions in sorted(groups.items()):
        branches = [_lower_transition(t) for t in transitions]
        blocks.append(StateEventBlock(from_state=from_state, event=event, branches=branches))
        if event:
            all_events.add(event)

    ir_id = f"fsm_{protocol_name}_{sm.name}".replace(" ", "_").replace("-", "_").lower()

    return FSMIRv1(
        ir_id=ir_id,
        name=sm.name,
        protocol_name=protocol_name,
        states=list(sm.states),
        events=sorted(all_events),
        blocks=blocks,
        source_pages=list(sm.source_pages),
        diagnostics=_collect_fsm_diagnostics(blocks, list(sm.source_pages)),
        normalization_status=_compute_fsm_readiness(blocks),
    )


def lower_all_state_machines(schema: ProtocolSchema) -> list[FSMIRv1]:
    """Lower all state machines in a schema to FSMIRv1."""
    return [
        lower_state_machine_to_fsm_ir(sm, schema.protocol_name)
        for sm in schema.state_machines
    ]


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if isinstance(value, str) and value.strip()})


def _normalized_name_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").casefold()).strip("_")


def _canonical_token(value: str | None, protocol_name: str) -> str:
    return canonicalize_context_name(value or "", protocol_name)


def _iter_observed_context_tokens(schema: ProtocolSchema, fsm_irs: list[FSMIRv1]) -> list[str]:
    observed: set[str] = set()
    protocol_name = schema.protocol_name

    for state_machine in schema.state_machines:
        for transition in state_machine.transitions:
            texts = [transition.condition, *transition.actions]
            for text in texts:
                for token in _DOTTED_TOKEN_RE.findall(text or ""):
                    canonical = _canonical_token(token, protocol_name)
                    if canonical:
                        observed.add(canonical)

    for procedure in schema.procedures:
        for step in procedure.steps:
            texts = [step.condition, step.action]
            for text in texts:
                for token in _DOTTED_TOKEN_RE.findall(text or ""):
                    canonical = _canonical_token(token, protocol_name)
                    if canonical:
                        observed.add(canonical)

    for fsm_ir in fsm_irs:
        for block in fsm_ir.blocks:
            for branch in block.branches:
                guard = branch.guard_typed
                if guard is not None and guard.ref_source == "ctx" and guard.field_ref:
                    canonical = _canonical_token(guard.field_ref, protocol_name)
                    if canonical:
                        observed.add(canonical)
                for action in branch.actions_typed:
                    if action.kind == "update_field" and action.ref_source == "ctx" and action.target:
                        canonical = _canonical_token(action.target, protocol_name)
                        if canonical:
                            observed.add(canonical)

    return sorted(observed)


def build_protocol_hint(schema: ProtocolSchema, fsm_irs: list[FSMIRv1]) -> ProtocolHint:
    known_states = _sorted_unique(
        state.name
        for state_machine in schema.state_machines
        for state in state_machine.states
    )
    if not known_states:
        known_states = _sorted_unique(
            state.name
            for fsm_ir in fsm_irs
            for state in fsm_ir.states
        )

    known_timers = _sorted_unique(timer.timer_name for timer in schema.timers)
    known_message_names = _sorted_unique(
        [message.name for message in schema.messages]
        + [message_ir.display_name for message_ir in schema.message_irs]
    )
    known_message_field_names = _sorted_unique(
        [field.name for message in schema.messages for field in message.fields]
        + [field.name for message_ir in schema.message_irs for field in message_ir.fields]
        + [field.canonical_name for message_ir in schema.message_irs for field in message_ir.fields]
    )

    return ProtocolHint(
        known_states=known_states,
        known_timers=known_timers,
        known_message_names=known_message_names,
        known_message_field_names=known_message_field_names,
        observed_context_tokens=_iter_observed_context_tokens(schema, fsm_irs),
    )


def _branch_has_unresolved_guard(branch: TransitionBranch) -> bool:
    return branch.guard_typed is None and bool(branch.guard_raw.strip())


def _branch_has_unresolved_actions(branch: TransitionBranch) -> bool:
    return bool(branch.actions_raw)


def _branch_needs_refinement(branch: TransitionBranch) -> bool:
    return _branch_has_unresolved_guard(branch) or _branch_has_unresolved_actions(branch)


def _fsm_raw_branch_counts(ir: FSMIRv1) -> tuple[int, int]:
    total = sum(len(block.branches) for block in ir.blocks)
    raw = sum(
        1
        for block in ir.blocks
        for branch in block.branches
        if _branch_needs_refinement(branch)
    )
    return raw, total


def _raw_branch_ratio(fsm_irs: list[FSMIRv1]) -> float:
    total = 0
    raw = 0
    for ir in fsm_irs:
        ir_raw, ir_total = _fsm_raw_branch_counts(ir)
        raw += ir_raw
        total += ir_total
    if total == 0:
        return 0.0
    return raw / total


def _needs_refinement(ir: FSMIRv1) -> bool:
    raw_branch_count, total_branch_count = _fsm_raw_branch_counts(ir)
    if total_branch_count == 0:
        return False
    raw_ratio = raw_branch_count / total_branch_count
    return raw_ratio > 0.3 and raw_branch_count >= 2


def _is_known_state(target: str | None, hint: ProtocolHint) -> bool:
    normalized = _normalized_name_key(target)
    if not normalized:
        return False
    return normalized in {_normalized_name_key(state) for state in hint.known_states}


def _is_known_timer(target: str | None, hint: ProtocolHint, protocol_name: str) -> bool:
    canonical = _canonical_token(target, protocol_name)
    if not canonical:
        return False
    return canonical in {
        _canonical_token(timer_name, protocol_name)
        for timer_name in hint.known_timers
    }


def _is_observed_ctx_token(field_ref: str | None, hint: ProtocolHint, protocol_name: str) -> bool:
    canonical = _canonical_token(field_ref, protocol_name)
    if not canonical:
        return False
    return canonical in set(hint.observed_context_tokens)


def _is_simple_literal(value: str | None) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if text.lower() in {"true", "false"}:
        return True
    if _INT_LITERAL_RE.fullmatch(text):
        return True
    if _HEX_LITERAL_RE.fullmatch(text):
        return True
    return False


def _is_simple_identifier(value: str | None) -> bool:
    text = (value or "").strip()
    return bool(text and _IDENTIFIER_RE.fullmatch(text))


def _is_acceptable_guard_value(value: str | None, hint: ProtocolHint) -> bool:
    if _is_simple_literal(value):
        return True
    if not _is_simple_identifier(value):
        return False
    return _is_known_state(value, hint)


def _is_acceptable_update_value(value: str | None, hint: ProtocolHint) -> bool:
    if _is_simple_literal(value):
        return True
    if not _is_simple_identifier(value):
        return False
    return _is_known_state(value, hint)


def _append_note(notes: list[str], note: str) -> list[str]:
    if note in notes:
        return notes
    return [*notes, note]


def _accept_llm_guard(
    candidate: dict[str, Any] | None,
    *,
    raw_guard_text: str,
    hint: ProtocolHint,
    protocol_name: str,
) -> TypedGuard | None:
    if not isinstance(candidate, dict):
        return None

    kind = str(candidate.get("kind", "")).strip()
    if kind == "timer_fired":
        return None
    if kind == "always":
        if raw_guard_text.strip() not in {"Always", "always"}:
            return None
        return TypedGuard(
            kind="always",
            ref_source="const",
            description=str(candidate.get("description") or raw_guard_text),
        )
    if kind not in {"context_field_eq", "context_field_ne", "flag_check"}:
        return None

    ref_source = str(candidate.get("ref_source", "")).strip()
    if ref_source != "ctx":
        return None

    field_ref = str(candidate.get("field_ref", "")).strip()
    if not _is_observed_ctx_token(field_ref, hint, protocol_name):
        return None

    description = str(candidate.get("description") or raw_guard_text)
    if kind == "flag_check":
        return TypedGuard(
            kind="flag_check",
            ref_source="ctx",
            field_ref=field_ref,
            description=description,
        )

    operator = str(candidate.get("operator", "")).strip()
    if kind == "context_field_eq":
        if operator in {"", "="}:
            operator = "=="
        if operator != "==":
            return None
    if kind == "context_field_ne":
        if operator != "!=":
            return None

    value = str(candidate.get("value", "")).strip()
    if not _is_acceptable_guard_value(value, hint):
        return None

    return TypedGuard(
        kind=kind,
        ref_source="ctx",
        field_ref=field_ref,
        operator=operator,
        value=value,
        description=description,
    )


def _accept_llm_action(
    candidate: dict[str, Any] | None,
    *,
    raw_action_text: str,
    hint: ProtocolHint,
    protocol_name: str,
) -> TypedAction | None:
    if not isinstance(candidate, dict):
        return None

    kind = str(candidate.get("kind", "")).strip()
    if kind == "emit_message":
        return None
    if kind not in {"set_state", "start_timer", "cancel_timer", "update_field"}:
        return None

    description = str(candidate.get("description") or raw_action_text)
    target = str(candidate.get("target", "")).strip()
    if not target:
        return None

    if kind == "set_state":
        if not _is_known_state(target, hint):
            return None
        return TypedAction(
            kind="set_state",
            ref_source="ctx",
            target=target,
            description=description,
        )

    if kind in {"start_timer", "cancel_timer"}:
        if not _is_known_timer(target, hint, protocol_name):
            return None
        return TypedAction(
            kind=kind,
            ref_source="timer",
            target=target,
            description=description,
        )

    ref_source = str(candidate.get("ref_source", "")).strip()
    if ref_source != "ctx":
        return None
    if not _is_observed_ctx_token(target, hint, protocol_name):
        return None

    value = str(candidate.get("value", "")).strip()
    if not _is_acceptable_update_value(value, hint):
        return None

    return TypedAction(
        kind="update_field",
        ref_source="ctx",
        target=target,
        value=value,
        description=description,
    )


def _build_refine_messages(
    fsm_name: str,
    block: StateEventBlock,
    branch: TransitionBranch,
    hint: ProtocolHint,
) -> list[dict[str, str]]:
    system_prompt = (
        "You refine protocol FSM branches into a restricted typed IR subset. "
        "Return JSON only with this schema: "
        '{"guard": {"kind": string, "ref_source": string, "field_ref": string, "operator": string, '
        '"value": string, "description": string} | null, '
        '"actions": ['
        '{"kind": string, "ref_source": string, "target": string, "value": string, "description": string} | null'
        "]}. "
        "Rules: use only guard kinds context_field_eq, context_field_ne, flag_check, always; "
        "use only action kinds set_state, start_timer, cancel_timer, update_field; "
        "never emit timer_fired or emit_message; "
        "actions must be returned in the same order and same length as raw_actions; "
        "use null when uncertain. Return JSON only."
    )
    payload = {
        "fsm_name": fsm_name,
        "from_state": block.from_state,
        "event": block.event,
        "next_state": branch.next_state,
        "raw_guard": branch.guard_raw,
        "raw_actions": list(branch.actions_raw),
        "hint": {
            "known_states": hint.known_states,
            "known_timers": hint.known_timers,
            "known_message_names": hint.known_message_names,
            "known_message_field_names": hint.known_message_field_names,
            "observed_context_tokens": hint.observed_context_tokens,
        },
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _load_json_object(text: str | None) -> dict[str, Any] | None:
    payload = (text or "").strip()
    if not payload:
        return None
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?\s*", "", payload)
        payload = re.sub(r"\s*```$", "", payload)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _llm_refine_branch(
    fsm_name: str,
    protocol_name: str,
    block: StateEventBlock,
    branch: TransitionBranch,
    llm: Any,
    hint: ProtocolHint,
) -> tuple[TransitionBranch, int, int]:
    refined = branch.model_copy(deep=True)
    if not _branch_needs_refinement(refined):
        return refined, 0, 0

    messages = _build_refine_messages(fsm_name, block, refined, hint)
    try:
        response = await llm.chat_with_tools(messages, [])
    except Exception:
        return refined, 0, 0

    payload = _load_json_object(getattr(response, "text", None))
    if payload is None:
        return refined, 0, 0

    accepted_guard_count = 0
    accepted_actions: list[TypedAction] = []

    if _branch_has_unresolved_guard(refined):
        accepted_guard = _accept_llm_guard(
            payload.get("guard"),
            raw_guard_text=refined.guard_raw,
            hint=hint,
            protocol_name=protocol_name,
        )
        if accepted_guard is not None:
            refined.guard_typed = accepted_guard
            refined.notes = _append_note(refined.notes, "llm_refined_guard")
            accepted_guard_count = 1

    action_candidates = payload.get("actions")
    if isinstance(action_candidates, list) and len(action_candidates) == len(refined.actions_raw):
        remaining_raw_actions: list[str] = []
        for raw_action, candidate in zip(refined.actions_raw, action_candidates):
            accepted_action = _accept_llm_action(
                candidate,
                raw_action_text=raw_action,
                hint=hint,
                protocol_name=protocol_name,
            )
            if accepted_action is None:
                remaining_raw_actions.append(raw_action)
                continue
            accepted_actions.append(accepted_action)
        if accepted_actions:
            refined.actions_typed = [*refined.actions_typed, *accepted_actions]
            refined.actions_raw = remaining_raw_actions
            refined.notes = _append_note(refined.notes, f"llm_refined_actions:{len(accepted_actions)}")

    refined.readiness = _compute_branch_readiness(refined)
    return refined, accepted_guard_count, len(accepted_actions)


async def refine_fsm_irs(
    fsm_irs: list[FSMIRv1],
    schema: ProtocolSchema,
    llm: Any,
) -> tuple[list[FSMIRv1], RefineStats]:
    if not fsm_irs:
        return [], RefineStats()

    hint = build_protocol_hint(schema, fsm_irs)
    refined_fsm_irs: list[FSMIRv1] = []
    stats = RefineStats(raw_branch_ratio_before=_raw_branch_ratio(fsm_irs))

    for ir in fsm_irs:
        working = ir.model_copy(deep=True)
        if not _needs_refinement(ir):
            refined_fsm_irs.append(working)
            continue

        stats.triggered_count += 1
        refined_blocks: list[StateEventBlock] = []
        for block in working.blocks:
            refined_branches: list[TransitionBranch] = []
            for branch in block.branches:
                if not _branch_needs_refinement(branch):
                    refined_branches.append(branch.model_copy(deep=True))
                    continue
                refined_branch, accepted_guard_count, accepted_action_count = await _llm_refine_branch(
                    working.name,
                    schema.protocol_name,
                    block,
                    branch,
                    llm,
                    hint,
                )
                stats.accepted_guard_count += accepted_guard_count
                stats.accepted_action_count += accepted_action_count
                refined_branches.append(refined_branch)
            refined_blocks.append(block.model_copy(update={"branches": refined_branches}, deep=True))

        working.blocks = refined_blocks
        working.diagnostics = _collect_fsm_diagnostics(working.blocks, list(working.source_pages))
        working.normalization_status = _compute_fsm_readiness(working.blocks)
        refined_fsm_irs.append(working)

    stats.raw_branch_ratio_after = _raw_branch_ratio(refined_fsm_irs)
    return refined_fsm_irs, stats
