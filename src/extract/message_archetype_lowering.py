"""Lower archetype-guided message contributions into unified MessageIR."""

from __future__ import annotations

from src.extract.rule_dsl import RuleSyntaxError, analyze_rule_expression
from src.extract.option_tlv import build_tcp_option_list_ir
from src.models import (
    CodegenHints,
    CompositeTailIR,
    FieldIR,
    IRDiagnostic,
    MessageIR,
    PresenceRule,
    SectionIR,
    ValidationRule,
)

from .message_archetype_models import ArchetypeContribution, CompositionTrait, TailKind


def _make_diag(level: str, code: str, message: str, pages: list[int] | None = None, node_ids: list[str] | None = None) -> IRDiagnostic:
    return IRDiagnostic(
        level=level,  # type: ignore[arg-type]
        code=code,
        message=message,
        source_pages=list(pages or []),
        source_node_ids=list(node_ids or []),
    )


def _analysis_fields(expression: str) -> list[str]:
    try:
        return analyze_rule_expression(expression).depends_on_fields
    except RuleSyntaxError:
        return []


def lower_archetype_contribution_to_message_ir(
    protocol_name: str,
    contribution: ArchetypeContribution,
) -> MessageIR:
    from src.extract.message_ir import normalize_message_ir

    order_index: dict[str, int] = {}
    fields: list[FieldIR] = []
    for index, field in enumerate(contribution.fields):
        order_index[field.canonical_hint] = index
        fields.append(
            FieldIR(
                field_id=f"{contribution.canonical_hint}.{field.canonical_hint}",
                name=field.name,
                canonical_name=field.canonical_hint,
                declared_bit_width=field.width_bits,
                declared_bit_offset=field.bit_offset_hint,
                declared_byte_offset=field.byte_offset_hint,
                const_value=0 if "reserved_zero" in field.field_traits else None,
                description=field.description,
                source_pages=list(contribution.source_pages),
                source_node_ids=list(contribution.source_node_ids),
            )
        )

    sections = [
        SectionIR(
            section_id=f"{contribution.canonical_hint}.header",
            name="Header",
            canonical_name="header",
            kind="header",
            declared_bit_offset=0,
            declared_byte_offset=0,
            field_ids=[field.canonical_name for field in fields],
            source_pages=list(contribution.source_pages),
        )
    ]
    presence_rules: list[PresenceRule] = []
    validation_rules: list[ValidationRule] = []
    composite_tails: list[CompositeTailIR] = []
    option_lists = []
    diagnostics = [item.model_copy(deep=True) for item in contribution.diagnostics]

    for index, clue in enumerate(contribution.rule_clues):
        try:
            analysis = analyze_rule_expression(clue.expression)
        except RuleSyntaxError as exc:
            diagnostics.append(
                _make_diag(
                    "error",
                    "invalid_archetype_rule",
                    f"Archetype rule {clue.expression!r} is not valid DSL: {exc}",
                    pages=contribution.source_pages,
                    node_ids=contribution.source_node_ids,
                )
            )
            continue
        validation_rules.append(
            ValidationRule(
                rule_id=f"{contribution.canonical_hint}.validation.{index}",
                target_kind="field",
                target_id=clue.target,
                kind=clue.kind,
                expression=clue.expression,
                depends_on_fields=analysis.depends_on_fields,
                description=f"Archetype-guided rule: {clue.expression}",
            )
        )

    for slot_index, tail in enumerate(contribution.tail_slots):
        section_id = f"{contribution.canonical_hint}.{tail.slot_name}"
        rule_id = f"{contribution.canonical_hint}.{tail.slot_name}.presence"
        if tail.presence_expression:
            try:
                analysis = analyze_rule_expression(tail.presence_expression)
                presence_rules.append(
                    PresenceRule(
                        rule_id=rule_id,
                        target_kind="section",
                        target_id=section_id,
                        expression=tail.presence_expression,
                        depends_on_fields=analysis.depends_on_fields,
                        description=f"{tail.slot_name} is present when {tail.presence_expression}.",
                    )
                )
            except RuleSyntaxError as exc:
                diagnostics.append(
                    _make_diag(
                        "error",
                        "invalid_tail_presence_expression",
                        f"Tail presence expression {tail.presence_expression!r} is invalid: {exc}",
                        pages=contribution.source_pages,
                        node_ids=contribution.source_node_ids,
                    )
                )
        option_list_id: str | None = None
        section_kind = "opaque_tail" if tail.tail_kind == TailKind.OPAQUE_BYTES else "optional_tail"
        tail_kind = tail.tail_kind.value
        if (
            contribution.canonical_hint == "tcp_header"
            and tail.slot_name == "options_tail"
            and tail.span_expression
        ):
            option_list = build_tcp_option_list_ir(
                contribution.canonical_hint,
                section_id,
                tail.span_expression,
                fixed_prefix_bytes=(tail.fixed_prefix_bits or 0) // 8,
                max_size_bytes=tail.max_span_bytes or 40,
            )
            option_lists.append(option_list)
            option_list_id = option_list.list_id
            section_kind = "option_list_tail"
            tail_kind = "option_list"
        sections.append(
            SectionIR(
                section_id=section_id,
                name=tail.slot_name.replace("_", " ").title(),
                canonical_name=tail.slot_name,
                kind=section_kind,
                parent_section_id=f"{contribution.canonical_hint}.header",
                declared_bit_offset=tail.fixed_prefix_bits,
                declared_byte_offset=(tail.fixed_prefix_bits // 8) if tail.fixed_prefix_bits is not None else None,
                optional=True,
                presence_rule_ids=[rule_id] if tail.presence_expression else [],
                field_ids=[],
                option_list_id=option_list_id,
                source_pages=list(contribution.source_pages),
            )
        )
        composite_tails.append(
            CompositeTailIR(
                slot_id=f"{contribution.canonical_hint}.{tail.slot_name}",
                section_id=section_id,
                name=tail.slot_name,
                tail_kind=tail_kind,
                optional=True,
                presence_rule_id=rule_id if tail.presence_expression else None,
                span_expression=tail.span_expression,
                fixed_prefix_bits=tail.fixed_prefix_bits,
                start_bit_offset=tail.fixed_prefix_bits,
                min_span_bits=0,
                max_span_bits=(tail.max_span_bytes * 8) if tail.max_span_bytes is not None else None,
                max_span_bytes=tail.max_span_bytes,
                fallback_mode=tail.fallback_mode.value if tail.fallback_mode is not None else None,
                option_list_id=option_list_id,
                candidate_message_irs=list(tail.candidates),
                dispatch_cases=[],
            )
        )
        if option_list_id is not None:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "option_list_fallback_enabled",
                    f"Tail slot {tail.slot_name} was lowered as a structured option list with opaque fallback for unsupported items.",
                    pages=contribution.source_pages,
                    node_ids=contribution.source_node_ids,
                )
            )
        else:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "opaque_tail_lowered",
                    f"Tail slot {tail.slot_name} was lowered as opaque bytes pending TLV/option IR support.",
                    pages=contribution.source_pages,
                    node_ids=contribution.source_node_ids,
                )
            )

    fixed_prefix_bits = max(((field.bit_offset_hint or 0) + (field.width_bits or 0)) for field in contribution.fields)
    max_span_bits = 0
    if composite_tails and composite_tails[0].max_span_bits is not None:
        max_span_bits = composite_tails[0].max_span_bits or 0

    lowered = MessageIR(
        ir_id=contribution.canonical_hint,
        protocol_name=protocol_name,
        canonical_name=contribution.canonical_hint,
        display_name=contribution.message_name,
        source_message_names=[contribution.message_name],
        source_pages=list(contribution.source_pages),
        source_node_ids=list(contribution.source_node_ids),
        min_size_bits=fixed_prefix_bits,
        max_size_bits=fixed_prefix_bits + max_span_bits if max_span_bits else fixed_prefix_bits,
        sections=sections,
        composite_tails=composite_tails,
        option_lists=option_lists,
        fields=fields,
        normalized_field_order=[],
        presence_rules=presence_rules,
        validation_rules=validation_rules,
        codegen_hints=CodegenHints(
            preferred_template="message_ir_v1",
            runtime_helpers=["pack", "unpack", "validate"],
        ),
        diagnostics=diagnostics,
    )
    return normalize_message_ir(lowered, order_index=order_index, offset_conflict=False)


def lower_archetype_contributions_to_message_irs(
    protocol_name: str,
    contributions: list[ArchetypeContribution],
) -> list[MessageIR]:
    message_irs: list[MessageIR] = []
    for contribution in contributions:
        if contribution.core_archetype.value != "packed_header":
            continue
        if CompositionTrait.HEADER_LENGTH_CONTROLLED_TAIL not in contribution.composition_traits:
            continue
        message_irs.append(lower_archetype_contribution_to_message_ir(protocol_name, contribution))
    return sorted(message_irs, key=lambda item: item.canonical_name)
