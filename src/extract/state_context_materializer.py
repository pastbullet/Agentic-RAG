"""Phase A StateContextIR materialization from FSM refs, document clues, and patches."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.extract.state_context import normalize_state_context_ir
from src.models import (
    ContextFieldIR,
    ContextFieldPatch,
    ContextPatch,
    ContextResourceIR,
    ContextResourcePatch,
    ContextTimerIR,
    ContextTimerPatch,
    FSMIRv1,
    IRDiagnostic,
    ProtocolSchema,
    StateContextIR,
)

_FIELD_SOURCE_PRIORITY = {"fsm_ref": 0, "document_clue": 1, "manual_patch": 2}
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_NAMESPACE_PREFIXES = {"ctx", "msg"}
_CANONICAL_EXPANSIONS = {
    "irs": "initial_recv_seq",
    "iss": "initial_send_seq",
    "seg_ack": "segment_ack",
    "seg_prc": "segment_precedence",
    "seg_seq": "segment_seq",
    "seg_up": "segment_urgent_ptr",
    "snd_nxt": "send_next_seq",
    "snd_una": "send_unacked",
    "snd_up": "send_urgent_ptr",
    "snd_wnd": "send_window",
    "snd_wl1": "send_window_update_seq",
    "snd_wl2": "send_window_update_ack",
    "rcv_nxt": "recv_next_seq",
    "rcv_up": "recv_urgent_ptr",
    "rcv_wnd": "recv_window",
}
_GENERIC_STOPWORDS = {"a", "an", "the"}
_TCP_NOISE_NAMES = {"ack", "bit", "control", "timeout"}


@dataclass
class FsmRefCollection:
    ctx_fields: dict[str, ContextFieldIR] = field(default_factory=dict)
    timers: dict[str, ContextTimerIR] = field(default_factory=dict)
    has_set_state: bool = False
    state_targets: set[str] = field(default_factory=set)
    required_refs: set[str] = field(default_factory=set)


@dataclass
class DocumentClueCollection:
    timers: dict[str, ContextTimerIR] = field(default_factory=dict)
    state_values: set[str] = field(default_factory=set)
    inferred_scope: str = "session"


def _make_diag(level: str, code: str, message: str) -> IRDiagnostic:
    return IRDiagnostic(level=level, code=code, message=message)


def _protocol_slug(protocol_name: str) -> str:
    return re.sub(r"_+", "_", _TOKEN_SPLIT_RE.sub("_", protocol_name.strip().lower())).strip("_") or "protocol"


def _protocol_aliases(protocol_name: str) -> set[str]:
    aliases: set[str] = set()
    for token in _TOKEN_SPLIT_RE.split(protocol_name.lower()):
        if not token:
            continue
        if token.startswith("rfc") and token[3:].isdigit():
            continue
        if token.isdigit():
            continue
        aliases.add(token)
    return aliases


def _snake_case(text: str) -> str:
    text = _CAMEL_RE.sub(r"\1_\2", text)
    text = _TOKEN_SPLIT_RE.sub("_", text)
    return re.sub(r"_+", "_", text).strip("_").lower()


def canonicalize_context_name(raw_name: str, protocol_name: str) -> str:
    text = (raw_name or "").strip()
    if not text:
        return ""

    if "." in text:
        prefix, remainder = text.split(".", 1)
        prefix_norm = prefix.strip().lower()
        if prefix_norm in _NAMESPACE_PREFIXES or prefix_norm in _protocol_aliases(protocol_name):
            text = remainder

    canonical = _snake_case(text)
    canonical = _CANONICAL_EXPANSIONS.get(canonical, canonical)
    if canonical in _GENERIC_STOPWORDS:
        return ""
    if "tcp" in _protocol_aliases(protocol_name) and canonical in _TCP_NOISE_NAMES:
        return ""
    return canonical


def _display_name(canonical_name: str) -> str:
    return canonical_name.replace("_", " ").title() if canonical_name else "Unnamed"


def _infer_scope_from_fsm_names(fsm_irs: list[FSMIRv1], protocol_name: str) -> str:
    names = " ".join(fsm.name.lower() for fsm in fsm_irs)
    if "connection" in names:
        return "connection"
    if "session" in names:
        return "session"
    if "association" in names or "login" in names:
        return "association"
    aliases = _protocol_aliases(protocol_name)
    if "tcp" in aliases:
        return "connection"
    if "bfd" in aliases:
        return "session"
    return "session"


def _infer_field_role(canonical_name: str, type_kind: str | None) -> str | None:
    name = canonical_name.lower()
    if "state" in name and type_kind == "enum":
        return "state"
    if name == "send_next_seq":
        return "send_next_seq"
    if name == "send_unacked":
        return "send_unacked"
    if name == "send_urgent_ptr":
        return "send_urgent_ptr"
    if name == "recv_next_seq":
        return "recv_next_seq"
    if name == "recv_urgent_ptr":
        return "recv_urgent_ptr"
    if name == "send_window":
        return "send_window"
    if name == "send_window_update_seq":
        return "send_window_update_seq"
    if name == "send_window_update_ack":
        return "send_window_update_ack"
    if name == "recv_window":
        return "recv_window"
    if name == "initial_send_seq":
        return "initial_send_seq"
    if name == "initial_recv_seq":
        return "initial_recv_seq"
    if name == "segment_ack":
        return "segment_ack"
    if name == "segment_seq":
        return "segment_seq"
    if name == "segment_precedence":
        return "segment_precedence"
    if name == "segment_urgent_ptr":
        return "segment_urgent_ptr"
    return None


def _infer_timer_role(canonical_name: str) -> str | None:
    name = canonical_name.lower()
    if "retransmit" in name or "retransmission" in name:
        return "retransmission"
    if "detect" in name or "hold" in name:
        return "hold_timer"
    if "keepalive" in name or name.startswith("tx_") or name == "tx_timer":
        return "keepalive"
    return None


def _ensure_provenance(existing: list[str], new_tag: str) -> list[str]:
    if new_tag in existing:
        return existing
    return [*existing, new_tag]


def _merge_unique_strs(left: list[str], right: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in [*left, *right]:
        if item and item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def _source_priority(tags: list[str] | tuple[str, ...] | set[str] | None) -> int:
    return max((_FIELD_SOURCE_PRIORITY.get(tag, -1) for tag in tags or []), default=-1)


def _is_empty_merge_value(attr: str, value: object) -> bool:
    if attr == "type_kind":
        # "opaque" is the weakest placeholder type in Phase A. Treating it as
        # empty lets stronger evidence like FSM refs or patches refine it.
        return value in (None, "", "opaque")
    return value in (None, "")


def _append_merge_conflict(
    *,
    kind: str,
    canonical_name: str,
    attr: str,
    current_value: object,
    incoming_value: object,
    diagnostics: list[IRDiagnostic],
) -> None:
    diagnostics.append(
        _make_diag(
            "warning",
            "CTX_MERGE_CONFLICT",
            f"{kind} {canonical_name!r} has conflicting {attr}: "
            f"{current_value!r} vs {incoming_value!r}; keeping higher-priority source.",
        )
    )


def _merge_scalar_attr(
    *,
    existing: object,
    incoming: object,
    attr: str,
    existing_priority: int,
    incoming_priority: int,
    diagnostics: list[IRDiagnostic],
    kind: str,
    canonical_name: str,
) -> object:
    current_value = getattr(existing, attr)
    incoming_value = getattr(incoming, attr)

    if _is_empty_merge_value(attr, incoming_value):
        return current_value

    current_empty = _is_empty_merge_value(attr, current_value)
    if not current_empty and current_value != incoming_value:
        _append_merge_conflict(
            kind=kind,
            canonical_name=canonical_name,
            attr=attr,
            current_value=current_value,
            incoming_value=incoming_value,
            diagnostics=diagnostics,
        )

    if incoming_priority > existing_priority:
        return incoming_value
    if incoming_priority == existing_priority and current_empty:
        return incoming_value
    return current_value


def _merge_field(
    merged: dict[str, ContextFieldIR],
    incoming: ContextFieldIR,
    source_tag: str,
    diagnostics: list[IRDiagnostic],
) -> None:
    existing = merged.get(incoming.canonical_name)
    if existing is None:
        incoming.provenance = _ensure_provenance(list(incoming.provenance), source_tag)
        merged[incoming.canonical_name] = incoming
        return

    existing_priority = _source_priority(existing.provenance)
    incoming_priority = _FIELD_SOURCE_PRIORITY.get(source_tag, -1)
    existing.read_by = _merge_unique_strs(existing.read_by, incoming.read_by)
    existing.written_by = _merge_unique_strs(existing.written_by, incoming.written_by)

    for attr in (
        "type_kind",
        "width_bits",
        "semantic_role",
        "initial_value_kind",
        "initial_value_expr",
        "name",
    ):
        setattr(
            existing,
            attr,
            _merge_scalar_attr(
                existing=existing,
                incoming=incoming,
                attr=attr,
                existing_priority=existing_priority,
                incoming_priority=incoming_priority,
                diagnostics=diagnostics,
                kind="Field",
                canonical_name=incoming.canonical_name,
            ),
        )

    existing.provenance = _ensure_provenance(list(existing.provenance), source_tag)


def _merge_timer(
    merged: dict[str, ContextTimerIR],
    incoming: ContextTimerIR,
    source_tag: str,
    diagnostics: list[IRDiagnostic],
) -> None:
    existing = merged.get(incoming.canonical_name)
    if existing is None:
        incoming.provenance = _ensure_provenance(list(incoming.provenance), source_tag)
        merged[incoming.canonical_name] = incoming
        return

    existing_priority = _source_priority(existing.provenance)
    incoming_priority = _FIELD_SOURCE_PRIORITY.get(source_tag, -1)
    existing.start_actions = _merge_unique_strs(existing.start_actions, incoming.start_actions)
    existing.cancel_actions = _merge_unique_strs(existing.cancel_actions, incoming.cancel_actions)

    for attr in ("semantic_role", "duration_source_kind", "duration_expr", "triggers_event", "name"):
        setattr(
            existing,
            attr,
            _merge_scalar_attr(
                existing=existing,
                incoming=incoming,
                attr=attr,
                existing_priority=existing_priority,
                incoming_priority=incoming_priority,
                diagnostics=diagnostics,
                kind="Timer",
                canonical_name=incoming.canonical_name,
            ),
        )

    existing.provenance = _ensure_provenance(list(existing.provenance), source_tag)


def _merge_resource(
    merged: dict[str, ContextResourceIR],
    incoming: ContextResourceIR,
    source_tag: str,
    diagnostics: list[IRDiagnostic],
) -> None:
    existing = merged.get(incoming.canonical_name)
    if existing is None:
        incoming.provenance = _ensure_provenance(list(incoming.provenance), source_tag)
        merged[incoming.canonical_name] = incoming
        return

    existing_priority = _source_priority(existing.provenance)
    incoming_priority = _FIELD_SOURCE_PRIORITY.get(source_tag, -1)
    for attr in ("kind", "semantic_role", "element_kind", "name"):
        setattr(
            existing,
            attr,
            _merge_scalar_attr(
                existing=existing,
                incoming=incoming,
                attr=attr,
                existing_priority=existing_priority,
                incoming_priority=incoming_priority,
                diagnostics=diagnostics,
                kind="Resource",
                canonical_name=incoming.canonical_name,
            ),
        )

    existing.provenance = _ensure_provenance(list(existing.provenance), source_tag)


def _field_from_patch(patch: ContextFieldPatch, protocol_name: str) -> ContextFieldIR:
    canonical_name = canonicalize_context_name(patch.canonical_name, protocol_name)
    return ContextFieldIR(
        field_id="",
        name=patch.name or _display_name(canonical_name),
        canonical_name=canonical_name,
        type_kind=patch.type_kind or "opaque",
        width_bits=patch.width_bits,
        semantic_role=patch.semantic_role,
        initial_value_kind=patch.initial_value_kind,
        initial_value_expr=patch.initial_value_expr,
        optional=patch.optional,
        provenance=["manual_patch"],
    )


def _timer_from_patch(patch: ContextTimerPatch, protocol_name: str) -> ContextTimerIR:
    canonical_name = canonicalize_context_name(patch.canonical_name, protocol_name)
    return ContextTimerIR(
        timer_id="",
        name=patch.name or _display_name(canonical_name),
        canonical_name=canonical_name,
        semantic_role=patch.semantic_role,
        duration_source_kind=patch.duration_source_kind,
        duration_expr=patch.duration_expr,
        triggers_event=patch.triggers_event,
        provenance=["manual_patch"],
    )


def _resource_from_patch(patch: ContextResourcePatch, protocol_name: str) -> ContextResourceIR:
    canonical_name = canonicalize_context_name(patch.canonical_name, protocol_name)
    return ContextResourceIR(
        resource_id="",
        name=patch.name or _display_name(canonical_name),
        canonical_name=canonical_name,
        kind=patch.kind or "opaque_handle",
        semantic_role=patch.semantic_role,
        element_kind=patch.element_kind,
        provenance=["manual_patch"],
    )


def collect_fsm_refs(fsm_irs: list[FSMIRv1], protocol_name: str) -> FsmRefCollection:
    refs = FsmRefCollection()
    for fsm_ir in fsm_irs:
        for block in fsm_ir.blocks:
            for branch in block.branches:
                guard = branch.guard_typed
                if guard is not None and guard.field_ref:
                    canonical_name = canonicalize_context_name(guard.field_ref, protocol_name)
                    if guard.ref_source == "ctx" and canonical_name:
                        field = ContextFieldIR(
                            field_id="",
                            name=_display_name(canonical_name),
                            canonical_name=canonical_name,
                            type_kind="bool" if guard.kind == "flag_check" else "opaque",
                            provenance=["fsm_ref"],
                            read_by=[fsm_ir.name],
                        )
                        _merge_field(refs.ctx_fields, field, "fsm_ref", [])
                        refs.required_refs.add(canonical_name)
                    elif guard.ref_source == "timer" and canonical_name:
                        timer = ContextTimerIR(
                            timer_id="",
                            name=_display_name(canonical_name),
                            canonical_name=canonical_name,
                            triggers_event=block.event or None,
                            provenance=["fsm_ref"],
                        )
                        _merge_timer(refs.timers, timer, "fsm_ref", [])
                        refs.required_refs.add(canonical_name)

                for action in branch.actions_typed:
                    if action.kind == "set_state":
                        refs.has_set_state = True
                        if action.target:
                            refs.state_targets.add(action.target)
                        continue
                    if action.kind == "update_field" and action.ref_source == "ctx" and action.target:
                        canonical_name = canonicalize_context_name(action.target, protocol_name)
                        if canonical_name:
                            field = ContextFieldIR(
                                field_id="",
                                name=_display_name(canonical_name),
                                canonical_name=canonical_name,
                                type_kind="opaque",
                                provenance=["fsm_ref"],
                                written_by=[fsm_ir.name],
                            )
                            _merge_field(refs.ctx_fields, field, "fsm_ref", [])
                            refs.required_refs.add(canonical_name)
                    elif action.kind in {"start_timer", "cancel_timer"} and action.target:
                        canonical_name = canonicalize_context_name(action.target, protocol_name)
                        if canonical_name:
                            timer = ContextTimerIR(
                                timer_id="",
                                name=_display_name(canonical_name),
                                canonical_name=canonical_name,
                                provenance=["fsm_ref"],
                                start_actions=[action.description] if action.kind == "start_timer" else [],
                                cancel_actions=[action.description] if action.kind == "cancel_timer" else [],
                            )
                            _merge_timer(refs.timers, timer, "fsm_ref", [])
                            refs.required_refs.add(canonical_name)
    return refs


def collect_document_clues(schema: ProtocolSchema) -> DocumentClueCollection:
    clues = DocumentClueCollection(inferred_scope=_infer_scope_from_fsm_names(schema.fsm_irs, schema.protocol_name))
    for timer in schema.timers:
        canonical_name = canonicalize_context_name(timer.timer_name, schema.protocol_name)
        if not canonical_name:
            continue
        clues.timers[canonical_name] = ContextTimerIR(
            timer_id="",
            name=timer.timer_name or _display_name(canonical_name),
            canonical_name=canonical_name,
            duration_source_kind="derived" if timer.timeout_value else None,
            duration_expr=timer.timeout_value or None,
            provenance=["document_clue"],
        )
    for fsm_ir in schema.fsm_irs:
        for state in fsm_ir.states:
            if state.name:
                clues.state_values.add(state.name)
    return clues


def _apply_role_overrides(
    fields: dict[str, ContextFieldIR],
    timers: dict[str, ContextTimerIR],
    resources: dict[str, ContextResourceIR],
    role_overrides: dict[str, str | None],
    protocol_name: str,
) -> set[str]:
    overridden: set[str] = set()
    for raw_name, role in role_overrides.items():
        canonical_name = canonicalize_context_name(raw_name, protocol_name)
        if not canonical_name:
            continue
        overridden.add(canonical_name)
        if canonical_name in fields:
            fields[canonical_name].semantic_role = role
        elif canonical_name in timers:
            timers[canonical_name].semantic_role = role
        elif canonical_name in resources:
            resources[canonical_name].semantic_role = role
    return overridden


def _synthesized_state_field_name(scope: str) -> str:
    if scope == "connection":
        return "connection_state"
    if scope == "association":
        return "association_state"
    if scope == "transaction":
        return "transaction_state"
    if scope == "session":
        return "session_state"
    return "state"


def _finalize_context_ids(
    context: StateContextIR,
) -> StateContextIR:
    finalized = context.model_copy(deep=True)
    for field in finalized.fields:
        field.field_id = f"{finalized.context_id}.{field.canonical_name}"
    for timer in finalized.timers:
        timer.timer_id = f"{finalized.context_id}.{timer.canonical_name}"
    for resource in finalized.resources:
        resource.resource_id = f"{finalized.context_id}.{resource.canonical_name}"
    return finalized


def merge_sources(
    schema: ProtocolSchema,
    fsm_refs: FsmRefCollection,
    doc_clues: DocumentClueCollection,
    patch: ContextPatch | None,
) -> StateContextIR:
    diagnostics: list[IRDiagnostic] = []
    field_map: dict[str, ContextFieldIR] = {}
    timer_map: dict[str, ContextTimerIR] = {}
    resource_map: dict[str, ContextResourceIR] = {}

    for field in fsm_refs.ctx_fields.values():
        _merge_field(field_map, field.model_copy(deep=True), "fsm_ref", diagnostics)
    for timer in fsm_refs.timers.values():
        _merge_timer(timer_map, timer.model_copy(deep=True), "fsm_ref", diagnostics)
    for timer in doc_clues.timers.values():
        _merge_timer(timer_map, timer.model_copy(deep=True), "document_clue", diagnostics)

    if patch is not None:
        for extra_field in patch.extra_fields:
            _merge_field(field_map, _field_from_patch(extra_field, schema.protocol_name), "manual_patch", diagnostics)
        for extra_timer in patch.extra_timers:
            _merge_timer(timer_map, _timer_from_patch(extra_timer, schema.protocol_name), "manual_patch", diagnostics)
        for extra_resource in patch.extra_resources:
            _merge_resource(resource_map, _resource_from_patch(extra_resource, schema.protocol_name), "manual_patch", diagnostics)

    role_overrides = patch.role_overrides if patch is not None else {}
    overridden_names = _apply_role_overrides(field_map, timer_map, resource_map, role_overrides, schema.protocol_name)

    scope = patch.scope if patch is not None and patch.scope else doc_clues.inferred_scope

    if doc_clues.state_values and not any(field.semantic_role == "state" for field in field_map.values()):
        preferred_name = _synthesized_state_field_name(scope)
        state_candidates = sorted(
            (
                field
                for field in field_map.values()
                if "state" in field.canonical_name
            ),
            key=lambda field: (
                0 if field.canonical_name == preferred_name else 1,
                0 if field.canonical_name.endswith("_state") else 1,
                len(field.canonical_name),
            ),
        )
        if state_candidates:
            state_candidates[0].semantic_role = "state"
            state_candidates[0].type_kind = "enum"
        elif preferred_name not in field_map:
            _merge_field(
                field_map,
                ContextFieldIR(
                    field_id="",
                    name=_display_name(preferred_name),
                    canonical_name=preferred_name,
                    type_kind="enum",
                    semantic_role="state",
                    provenance=["document_clue"],
                ),
                "document_clue",
                diagnostics,
            )

    if fsm_refs.has_set_state and not any(field.semantic_role == "state" for field in field_map.values()):
        synthesized_name = _synthesized_state_field_name(scope)
        if synthesized_name not in field_map:
            _merge_field(
                field_map,
                ContextFieldIR(
                    field_id="",
                    name=_display_name(synthesized_name),
                    canonical_name=synthesized_name,
                    type_kind="enum",
                    semantic_role="state",
                    provenance=["fsm_ref"],
                ),
                "fsm_ref",
                diagnostics,
            )

    for canonical_name, role in role_overrides.items():
        if role != "state":
            continue
        normalized_name = canonicalize_context_name(canonical_name, schema.protocol_name)
        if normalized_name and normalized_name not in field_map:
            _merge_field(
                field_map,
                ContextFieldIR(
                    field_id="",
                    name=_display_name(normalized_name),
                    canonical_name=normalized_name,
                    type_kind="enum",
                    semantic_role="state",
                    provenance=["manual_patch"],
                ),
                "manual_patch",
                diagnostics,
            )

    for field in field_map.values():
        if field.semantic_role is None and field.canonical_name not in overridden_names:
            field.semantic_role = _infer_field_role(field.canonical_name, field.type_kind)
        if field.semantic_role == "state":
            field.type_kind = "enum"
        if field.semantic_role is None:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "CTX_ROLE_UNKNOWN",
                    f"Unable to infer semantic_role for field {field.canonical_name!r}.",
                )
            )

    for timer in timer_map.values():
        if timer.semantic_role is None and timer.canonical_name not in overridden_names:
            timer.semantic_role = _infer_timer_role(timer.canonical_name)
        if timer.semantic_role is None:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "CTX_ROLE_UNKNOWN",
                    f"Unable to infer semantic_role for timer {timer.canonical_name!r}.",
                )
            )

    state_field = next((field.canonical_name for field in field_map.values() if field.semantic_role == "state"), None)
    context_slug = _protocol_slug(schema.protocol_name)
    context = StateContextIR(
        context_id=f"{context_slug}_context",
        name=f"{schema.protocol_name} Runtime Context",
        canonical_name=f"{context_slug}_context",
        scope=scope,
        state_field=state_field,
        fields=sorted(field_map.values(), key=lambda item: item.canonical_name),
        timers=sorted(timer_map.values(), key=lambda item: item.canonical_name),
        resources=sorted(resource_map.values(), key=lambda item: item.canonical_name),
        invariants=[],
        diagnostics=diagnostics,
    )
    context = _finalize_context_ids(context)
    return normalize_state_context_ir(context, required_refs=fsm_refs.required_refs)


def materialize_protocol_state_context(
    schema: ProtocolSchema,
    patch: ContextPatch | None = None,
) -> StateContextIR:
    fsm_refs = collect_fsm_refs(schema.fsm_irs, schema.protocol_name)
    doc_clues = collect_document_clues(schema)
    return merge_sources(schema, fsm_refs, doc_clues, patch)


def materialize_all_state_contexts(
    schema: ProtocolSchema,
    patches: dict[str, ContextPatch] | None = None,
) -> list[StateContextIR]:
    patch = patches.get(schema.protocol_name) if patches else None
    return [materialize_protocol_state_context(schema, patch)]


def load_context_patch(protocol_name: str) -> ContextPatch | None:
    patch_path = Path("data") / "patches" / protocol_name / "context_patch.json"
    if not patch_path.exists():
        return None
    return ContextPatch.model_validate(json.loads(patch_path.read_text(encoding="utf-8")))


def load_context_patches(protocol_name: str) -> dict[str, ContextPatch] | None:
    patch = load_context_patch(protocol_name)
    if patch is None:
        return None
    return {protocol_name: patch}
