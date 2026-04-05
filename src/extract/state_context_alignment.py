"""Phase B FSMIRv1 ↔ StateContextIR alignment validation."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from src.extract.state_context_materializer import canonicalize_context_name
from src.models import (
    AlignmentFSMReport,
    AlignmentReport,
    AlignmentSummary,
    ContextFieldIR,
    ContextTimerIR,
    FSMIRv1,
    IRDiagnostic,
    StateContextIR,
)

RefKind = Literal["guard_ctx", "guard_timer", "update_field", "timer_action", "set_state"]


def _make_diag(level: Literal["warning", "error"], code: str, message: str) -> IRDiagnostic:
    return IRDiagnostic(level=level, code=code, message=message)


def _context_field_map(context: StateContextIR, protocol_name: str) -> dict[str, ContextFieldIR]:
    fields: dict[str, ContextFieldIR] = {}
    for field in context.fields:
        canonical_name = canonicalize_context_name(field.canonical_name, protocol_name)
        if canonical_name:
            fields[canonical_name] = field
    return fields


def _context_timer_map(context: StateContextIR, protocol_name: str) -> dict[str, ContextTimerIR]:
    timers: dict[str, ContextTimerIR] = {}
    for timer in context.timers:
        canonical_name = canonicalize_context_name(timer.canonical_name, protocol_name)
        if canonical_name:
            timers[canonical_name] = timer
    return timers


def _normalized_state_field(context: StateContextIR, protocol_name: str) -> str | None:
    if not isinstance(context.state_field, str) or not context.state_field.strip():
        return None
    return canonicalize_context_name(context.state_field, protocol_name) or None


def _valid_state_field_name(
    context: StateContextIR,
    field_map: dict[str, ContextFieldIR],
    protocol_name: str,
) -> str | None:
    state_field = _normalized_state_field(context, protocol_name)
    if not state_field:
        return None
    field = field_map.get(state_field)
    if field is None or field.semantic_role != "state":
        return None
    return state_field


def _iter_typed_refs(
    fsm_ir: FSMIRv1,
    protocol_name: str,
) -> list[tuple[RefKind, str | None]]:
    refs: list[tuple[RefKind, str | None]] = []
    for block in fsm_ir.blocks:
        for branch in block.branches:
            guard = branch.guard_typed
            if guard is not None and guard.field_ref:
                canonical_name = canonicalize_context_name(guard.field_ref, protocol_name)
                if guard.ref_source == "ctx" and canonical_name:
                    refs.append(("guard_ctx", canonical_name))
                elif guard.ref_source == "timer" and canonical_name:
                    refs.append(("guard_timer", canonical_name))

            for action in branch.actions_typed:
                if action.kind == "set_state":
                    refs.append(("set_state", None))
                elif action.kind == "update_field" and action.ref_source == "ctx" and action.target:
                    canonical_name = canonicalize_context_name(action.target, protocol_name)
                    if canonical_name:
                        refs.append(("update_field", canonical_name))
                elif action.kind in {"start_timer", "cancel_timer"} and action.target:
                    canonical_name = canonicalize_context_name(action.target, protocol_name)
                    if canonical_name:
                        refs.append(("timer_action", canonical_name))
    return refs


def validate_fsm_context_alignment(
    fsm_ir: FSMIRv1,
    context: StateContextIR,
) -> AlignmentFSMReport:
    protocol_name = fsm_ir.protocol_name
    field_map = _context_field_map(context, protocol_name)
    timer_map = _context_timer_map(context, protocol_name)
    valid_state_field = _valid_state_field_name(context, field_map, protocol_name)

    missing_guard_fields: Counter[str] = Counter()
    missing_guard_timers: Counter[str] = Counter()
    missing_update_fields: Counter[str] = Counter()
    missing_timer_actions: Counter[str] = Counter()
    missing_state_count = 0

    typed_ref_count = 0
    resolved_ref_count = 0

    for ref_kind, canonical_name in _iter_typed_refs(fsm_ir, protocol_name):
        typed_ref_count += 1

        if ref_kind == "guard_ctx":
            if canonical_name in field_map:
                resolved_ref_count += 1
            elif canonical_name:
                missing_guard_fields[canonical_name] += 1
            continue

        if ref_kind == "guard_timer":
            if canonical_name in timer_map:
                resolved_ref_count += 1
            elif canonical_name:
                missing_guard_timers[canonical_name] += 1
            continue

        if ref_kind == "update_field":
            if canonical_name in field_map:
                resolved_ref_count += 1
            elif canonical_name:
                missing_update_fields[canonical_name] += 1
            continue

        if ref_kind == "timer_action":
            if canonical_name in timer_map:
                resolved_ref_count += 1
            elif canonical_name:
                missing_timer_actions[canonical_name] += 1
            continue

        if ref_kind == "set_state":
            if valid_state_field is not None:
                resolved_ref_count += 1
            else:
                missing_state_count += 1

    diagnostics: list[IRDiagnostic] = []
    for field_name, count in sorted(missing_guard_fields.items()):
        diagnostics.append(
            _make_diag(
                "error",
                "FSM_CTX_FIELD_MISSING",
                f"FSM {fsm_ir.name!r} references context field {field_name!r} in guard {count} time(s), "
                "but it is not declared in StateContextIR.",
            )
        )
    for timer_name, count in sorted(missing_guard_timers.items()):
        diagnostics.append(
            _make_diag(
                "error",
                "FSM_CTX_TIMER_MISSING",
                f"FSM {fsm_ir.name!r} references timer {timer_name!r} in guard {count} time(s), "
                "but it is not declared in StateContextIR.",
            )
        )
    for field_name, count in sorted(missing_update_fields.items()):
        diagnostics.append(
            _make_diag(
                "error",
                "FSM_CTX_UPDATE_MISSING",
                f"FSM {fsm_ir.name!r} updates context field {field_name!r} {count} time(s), "
                "but it is not declared in StateContextIR.",
            )
        )
    for timer_name, count in sorted(missing_timer_actions.items()):
        diagnostics.append(
            _make_diag(
                "error",
                "FSM_CTX_TIMER_ACTION_MISSING",
                f"FSM {fsm_ir.name!r} starts/cancels timer {timer_name!r} {count} time(s), "
                "but it is not declared in StateContextIR.",
            )
        )
    if missing_state_count:
        state_field = _normalized_state_field(context, protocol_name)
        if state_field is None:
            detail = "context has no state_field"
        elif state_field not in field_map:
            detail = f"context state_field {state_field!r} is not declared"
        else:
            detail = f"context state_field {state_field!r} does not carry semantic_role 'state'"
        diagnostics.append(
            _make_diag(
                "error",
                "CTX_STATE_FIELD_MISSING",
                f"FSM {fsm_ir.name!r} uses set_state {missing_state_count} time(s), but {detail}.",
            )
        )

    error_count = sum(diag.level == "error" for diag in diagnostics)
    warning_count = sum(diag.level == "warning" for diag in diagnostics)
    return AlignmentFSMReport(
        fsm_ir_id=fsm_ir.ir_id,
        fsm_name=fsm_ir.name,
        error_count=error_count,
        warning_count=warning_count,
        typed_ref_count=typed_ref_count,
        resolved_ref_count=resolved_ref_count,
        diagnostics=diagnostics,
    )


def _collect_context_diagnostics(
    fsm_irs: list[FSMIRv1],
    context: StateContextIR,
    protocol_name: str,
) -> list[IRDiagnostic]:
    field_map = _context_field_map(context, protocol_name)
    referenced_fields: set[str] = set()
    referenced_timers: set[str] = set()
    state_field = _normalized_state_field(context, protocol_name)
    valid_state_field = _valid_state_field_name(context, field_map, protocol_name)
    if valid_state_field is not None:
        referenced_fields.add(valid_state_field)

    for fsm_ir in fsm_irs:
        for ref_kind, canonical_name in _iter_typed_refs(fsm_ir, protocol_name):
            if ref_kind in {"guard_ctx", "update_field"} and canonical_name:
                referenced_fields.add(canonical_name)
            elif ref_kind in {"guard_timer", "timer_action"} and canonical_name:
                referenced_timers.add(canonical_name)
            elif ref_kind == "set_state" and state_field:
                referenced_fields.add(state_field)

    diagnostics: list[IRDiagnostic] = []
    for field_name in sorted(field_map):
        if field_name not in referenced_fields:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "CTX_FIELD_UNREFERENCED",
                    f"StateContextIR field {field_name!r} is declared but not referenced by any FSM typed ref.",
                )
            )
    for timer_name in sorted(_context_timer_map(context, protocol_name)):
        if timer_name not in referenced_timers:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "CTX_TIMER_UNREFERENCED",
                    f"StateContextIR timer {timer_name!r} is declared but not referenced by any FSM typed ref.",
                )
            )
    return diagnostics


def validate_all_fsm_context_alignments(
    fsm_irs: list[FSMIRv1],
    context: StateContextIR,
) -> AlignmentReport:
    protocol_name = fsm_irs[0].protocol_name if fsm_irs else (context.name or context.context_id)
    fsm_reports = [validate_fsm_context_alignment(fsm_ir, context) for fsm_ir in fsm_irs]
    context_diagnostics = _collect_context_diagnostics(fsm_irs, context, protocol_name)

    error_count = sum(report.error_count for report in fsm_reports)
    warning_count = sum(report.warning_count for report in fsm_reports) + sum(
        diag.level == "warning" for diag in context_diagnostics
    )
    typed_ref_count = sum(report.typed_ref_count for report in fsm_reports)
    resolved_ref_count = sum(report.resolved_ref_count for report in fsm_reports)
    coverage_ratio = (resolved_ref_count / typed_ref_count) if typed_ref_count else 0.0

    return AlignmentReport(
        protocol_name=protocol_name,
        context_id=context.context_id,
        summary=AlignmentSummary(
            fsm_count=len(fsm_irs),
            error_count=error_count,
            warning_count=warning_count,
            aligned_fsm_count=sum(report.error_count == 0 for report in fsm_reports),
            typed_ref_count=typed_ref_count,
            resolved_ref_count=resolved_ref_count,
            coverage_ratio=coverage_ratio,
        ),
        context_diagnostics=context_diagnostics,
        fsm_reports=fsm_reports,
    )
