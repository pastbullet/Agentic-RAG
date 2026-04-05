"""Minimal StateContextIR normalization helpers."""

from __future__ import annotations

import re

from src.models import (
    ContextFieldIR,
    ContextResourceIR,
    ContextRuleIR,
    ContextTimerIR,
    IRDiagnostic,
    NormalizationStatus,
    StateContextIR,
)

_SUPPORTED_SCOPES = {"connection", "session", "association", "transaction", "global"}
_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_RESERVED_TOKENS = {"and", "or", "not", "in", "true", "false", "null"}


def _normalize_atom(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[\s\-]+", "_", value.strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or None


def _make_diag(
    level: str,
    code: str,
    message: str,
    *,
    pages: list[int] | None = None,
    node_ids: list[str] | None = None,
) -> IRDiagnostic:
    return IRDiagnostic(
        level=level,  # type: ignore[arg-type]
        code=code,
        message=message,
        source_pages=list(pages or []),
        source_node_ids=list(node_ids or []),
    )


def _infer_rule_dependencies(expression: str, known_fields: set[str], known_roles: set[str]) -> list[str]:
    deps: list[str] = []
    for token in _TOKEN_RE.findall(expression or ""):
        normalized = _normalize_atom(token)
        if normalized in _RESERVED_TOKENS:
            continue
        if token.isupper():
            continue
        if normalized in known_fields or normalized in known_roles:
            if normalized not in deps:
                deps.append(normalized)
    return deps


def normalize_state_context_ir(
    context: StateContextIR,
    *,
    required_refs: set[str] | None = None,
) -> StateContextIR:
    """Normalize a StateContextIR and compute readiness.

    Args:
        context: The raw StateContextIR to normalize.
        required_refs: Optional set of canonical field/timer names that the
            FSM consumer actually references.  When provided, readiness is
            evaluated against this consumer-driven set instead of any
            hard-coded role list.  When *None*, any context with a valid
            state_field and at least one field is considered READY (no
            TCP-specific role requirement).
    """
    normalized = context.model_copy(deep=True)
    diagnostics: list[IRDiagnostic] = list(normalized.diagnostics)
    degraded = False
    blocked = False

    normalized.scope = _normalize_atom(normalized.scope) or normalized.scope
    if normalized.scope not in _SUPPORTED_SCOPES:
        diagnostics.append(
            _make_diag(
                "error",
                "invalid_scope",
                f"Unsupported state context scope {normalized.scope!r}.",
            )
        )
        blocked = True

    field_by_name: dict[str, ContextFieldIR] = {}
    field_by_role: dict[str, ContextFieldIR] = {}
    normalized.fields = []
    for field in context.fields:
        role = _normalize_atom(field.semantic_role)
        normalized_field = field.model_copy(update={"semantic_role": role}, deep=True)
        normalized.fields.append(normalized_field)
        field_name = _normalize_atom(normalized_field.canonical_name) or normalized_field.canonical_name
        field_by_name[field_name] = normalized_field
        if role:
            if role in field_by_role:
                diagnostics.append(
                    _make_diag(
                        "error",
                        "duplicate_context_role",
                        f"Context role {role!r} is assigned to multiple fields.",
                    )
                )
                blocked = True
            else:
                field_by_role[role] = normalized_field

    # Consumer-driven coverage check: verify that all refs the FSM actually
    # needs are satisfied, rather than requiring a fixed TCP-centric role set.
    timer_names = {_normalize_atom(t.canonical_name) for t in context.timers}
    all_slots = set(field_by_name) | set(field_by_role) | timer_names
    if required_refs is not None:
        normalized_required_refs = {
            _normalize_atom(ref) or ref
            for ref in required_refs
            if isinstance(ref, str) and ref.strip()
        }
        missing_refs = sorted(normalized_required_refs - all_slots)
        if missing_refs:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "missing_consumer_refs",
                    f"FSM references slots not declared in context: {', '.join(missing_refs)}.",
                )
                )
            degraded = True

    state_field = _normalize_atom(normalized.state_field) if isinstance(normalized.state_field, str) else None
    normalized.state_field = state_field
    if state_field:
        state_field_ref = field_by_name.get(state_field)
        if state_field_ref is None:
            diagnostics.append(
                _make_diag(
                    "error",
                    "invalid_state_field",
                    f"State field {state_field!r} does not match any declared context field.",
                )
            )
            blocked = True
        elif state_field_ref.semantic_role != "state":
            diagnostics.append(
                _make_diag(
                    "error",
                    "state_field_role_mismatch",
                    f"State field {state_field!r} does not carry the 'state' semantic role.",
                )
            )
            blocked = True
    else:
        state_candidates = [field for field in normalized.fields if field.semantic_role == "state"]
        if len(state_candidates) == 1:
            normalized.state_field = state_candidates[0].canonical_name
            diagnostics.append(
                _make_diag(
                    "warning",
                    "state_field_inferred",
                    f"State field was inferred as {normalized.state_field!r} from the 'state' semantic role.",
                )
            )
            degraded = True
        else:
            diagnostics.append(
                _make_diag(
                    "error",
                    "missing_state_field",
                    "State field is missing and could not be inferred unambiguously.",
                )
            )
            blocked = True

    normalized.timers = []
    for timer in context.timers:
        normalized.timers.append(
            timer.model_copy(
                update={"semantic_role": _normalize_atom(timer.semantic_role)},
                deep=True,
            )
        )

    normalized.resources = []
    for resource in context.resources:
        normalized.resources.append(
            resource.model_copy(
                update={"semantic_role": _normalize_atom(resource.semantic_role), "kind": _normalize_atom(resource.kind) or resource.kind},
                deep=True,
            )
        )

    known_fields = set(field_by_name) | set(field_by_role)
    normalized.invariants = []
    for rule in context.invariants:
        depends_on_fields = list(rule.depends_on_fields)
        if not depends_on_fields:
            depends_on_fields = _infer_rule_dependencies(rule.expression, known_fields, set(field_by_role))
        unknown_deps = [dep for dep in depends_on_fields if dep not in known_fields]
        if unknown_deps:
            diagnostics.append(
                _make_diag(
                    "error",
                    "unknown_context_rule_dependency",
                    f"Context rule {rule.rule_id} references unknown fields: {', '.join(unknown_deps)}.",
                )
            )
            blocked = True
        normalized.invariants.append(
            ContextRuleIR(
                rule_id=rule.rule_id,
                kind=rule.kind,
                expression=rule.expression,
                depends_on_fields=depends_on_fields,
                diagnostics=list(rule.diagnostics),
            )
        )

    normalized.diagnostics = diagnostics
    if blocked:
        normalized.readiness = NormalizationStatus.BLOCKED
    elif degraded:
        normalized.readiness = NormalizationStatus.DEGRADED_READY
    else:
        normalized.readiness = NormalizationStatus.READY
    return normalized


def build_tcp_connection_state_context() -> StateContextIR:
    context = StateContextIR(
        context_id="tcp_connection_context",
        name="TCP Connection Context",
        canonical_name="tcp_connection_context",
        scope="connection",
        state_field="connection_state",
        fields=[
            ContextFieldIR(
                field_id="tcp_connection_context.connection_state",
                name="Connection State",
                canonical_name="connection_state",
                type_kind="enum",
                semantic_role="state",
                initial_value_kind="derived",
                initial_value_expr="CLOSED",
            ),
            ContextFieldIR(
                field_id="tcp_connection_context.send_next_seq",
                name="Send Next Sequence",
                canonical_name="send_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="send_next_seq",
            ),
            ContextFieldIR(
                field_id="tcp_connection_context.recv_next_seq",
                name="Receive Next Sequence",
                canonical_name="recv_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="recv_next_seq",
            ),
            ContextFieldIR(
                field_id="tcp_connection_context.send_window",
                name="Send Window",
                canonical_name="send_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="send_window",
            ),
            ContextFieldIR(
                field_id="tcp_connection_context.recv_window",
                name="Receive Window",
                canonical_name="recv_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="recv_window",
            ),
        ],
        timers=[
            ContextTimerIR(
                timer_id="tcp_connection_context.retransmission_timer",
                name="Retransmission Timer",
                canonical_name="retransmission_timer",
                semantic_role="retransmission_timer",
                duration_source_kind="derived",
                duration_expr="retransmission_timeout",
                triggers_event="retransmission_timeout_expires",
            )
        ],
        resources=[
            ContextResourceIR(
                resource_id="tcp_connection_context.send_queue",
                name="Send Queue",
                canonical_name="send_queue",
                kind="queue",
                semantic_role="send_queue",
                element_kind="segment_ref",
            )
        ],
        invariants=[],
    )
    return normalize_state_context_ir(context)


def build_generic_session_state_context() -> StateContextIR:
    context = StateContextIR(
        context_id="generic_session_context",
        name="Generic Session Context",
        canonical_name="generic_session_context",
        scope="session",
        state_field=None,
        fields=[
            ContextFieldIR(
                field_id="generic_session_context.session_state",
                name="Session State",
                canonical_name="session_state",
                type_kind="enum",
                semantic_role="state",
                initial_value_kind="derived",
                initial_value_expr="IDLE",
            ),
            ContextFieldIR(
                field_id="generic_session_context.send_next_seq",
                name="Send Next Sequence",
                canonical_name="send_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="send_next_seq",
            ),
            ContextFieldIR(
                field_id="generic_session_context.recv_next_seq",
                name="Receive Next Sequence",
                canonical_name="recv_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="recv_next_seq",
            ),
            ContextFieldIR(
                field_id="generic_session_context.send_window",
                name="Send Window",
                canonical_name="send_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="send_window",
            ),
            ContextFieldIR(
                field_id="generic_session_context.recv_window",
                name="Receive Window",
                canonical_name="recv_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="recv_window",
            ),
        ],
        timers=[],
        resources=[],
        invariants=[],
    )
    return normalize_state_context_ir(context)
