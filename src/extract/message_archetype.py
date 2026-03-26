"""Archetype-guided standardization for extracted protocol messages."""

from __future__ import annotations

from typing import Iterable

from src.extract.merge import ExtractionRecord, normalize_field_name, normalize_name_v2
from src.extract.rule_dsl import RuleSyntaxError, analyze_rule_expression
from src.models import IRDiagnostic, ProtocolMessage

from .message_archetype_models import (
    ArchetypeConfidence,
    ArchetypeContribution,
    ArchetypeFieldContribution,
    CompositionTrait,
    ConstraintTrait,
    CoreArchetype,
    FallbackMode,
    RuleClue,
    TailKind,
    TailSlotContribution,
)


def _ordered_union(values: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def _sorted_union(values: Iterable[int]) -> list[int]:
    return sorted({item for item in values if isinstance(item, int) and item > 0})


def _make_diag(level: str, code: str, message: str, pages: list[int] | None = None, node_ids: list[str] | None = None) -> IRDiagnostic:
    return IRDiagnostic(
        level=level,  # type: ignore[arg-type]
        code=code,
        message=message,
        source_pages=list(pages or []),
        source_node_ids=list(node_ids or []),
    )


_TCP_FIELD_SPECS: tuple[ArchetypeFieldContribution, ...] = (
    ArchetypeFieldContribution(name="Source Port", canonical_hint="source_port", width_bits=16, bit_offset_hint=0),
    ArchetypeFieldContribution(name="Destination Port", canonical_hint="destination_port", width_bits=16, bit_offset_hint=16),
    ArchetypeFieldContribution(name="Sequence Number", canonical_hint="sequence_number", width_bits=32, bit_offset_hint=32),
    ArchetypeFieldContribution(name="Acknowledgment Number", canonical_hint="acknowledgment_number", width_bits=32, bit_offset_hint=64),
    ArchetypeFieldContribution(name="Data Offset", canonical_hint="data_offset", width_bits=4, bit_offset_hint=96, field_traits=["header_length_controller"]),
    ArchetypeFieldContribution(name="Reserved", canonical_hint="reserved", width_bits=6, bit_offset_hint=100, field_traits=["reserved_zero"]),
    ArchetypeFieldContribution(name="URG", canonical_hint="urg", width_bits=1, bit_offset_hint=106),
    ArchetypeFieldContribution(name="ACK", canonical_hint="ack", width_bits=1, bit_offset_hint=107),
    ArchetypeFieldContribution(name="PSH", canonical_hint="psh", width_bits=1, bit_offset_hint=108),
    ArchetypeFieldContribution(name="RST", canonical_hint="rst", width_bits=1, bit_offset_hint=109),
    ArchetypeFieldContribution(name="SYN", canonical_hint="syn", width_bits=1, bit_offset_hint=110),
    ArchetypeFieldContribution(name="FIN", canonical_hint="fin", width_bits=1, bit_offset_hint=111),
    ArchetypeFieldContribution(name="Window", canonical_hint="window", width_bits=16, bit_offset_hint=112),
    ArchetypeFieldContribution(name="Checksum", canonical_hint="checksum", width_bits=16, bit_offset_hint=128),
    ArchetypeFieldContribution(name="Urgent Pointer", canonical_hint="urgent_pointer", width_bits=16, bit_offset_hint=144),
)


def _tcp_tail_slot() -> TailSlotContribution:
    return TailSlotContribution(
        slot_name="options_tail",
        presence_expression="header.data_offset > 5",
        span_expression="header.data_offset * 4 - 20",
        tail_kind=TailKind.OPAQUE_BYTES,
        fallback_mode=FallbackMode.OPAQUE_UNTIL_TLV_IR,
        fixed_prefix_bits=160,
        max_span_bytes=40,
    )


def _message_from_record(record: ExtractionRecord) -> ProtocolMessage | None:
    if record.label != "message_format":
        return None
    try:
        return ProtocolMessage.model_validate(record.payload)
    except Exception:
        return None


def _field_map(message: ProtocolMessage) -> dict[str, tuple[str, int | None, str]]:
    mapping: dict[str, tuple[str, int | None, str]] = {}
    for field in message.fields:
        mapping[normalize_field_name(field.name)] = (field.name, field.size_bits, field.description or "")
    return mapping


def _looks_like_tcp_header(message: ProtocolMessage) -> bool:
    normalized_name = normalize_name_v2(message.name, aggressive=True)
    field_names = set(_field_map(message))
    required = {
        normalize_field_name("Source Port"),
        normalize_field_name("Destination Port"),
        normalize_field_name("Sequence Number"),
        normalize_field_name("Data Offset"),
        normalize_field_name("Options"),
    }
    return "tcp header" in normalized_name or required.issubset(field_names)


def _tcp_shape_contract(message: ProtocolMessage) -> list[IRDiagnostic]:
    diagnostics: list[IRDiagnostic] = []
    field_map = _field_map(message)
    for spec in _TCP_FIELD_SPECS:
        normalized = normalize_field_name(spec.name)
        raw = field_map.get(normalized)
        if raw is None:
            diagnostics.append(
                _make_diag(
                    "error",
                    "shape_contract_missing_field",
                    f"Packed-header archetype requires field {spec.name}.",
                    pages=message.source_pages,
                )
            )
            continue
        _, width_bits, _ = raw
        if width_bits != spec.width_bits:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "shape_contract_width_mismatch",
                    f"Expected {spec.name} width {spec.width_bits}, got {width_bits}.",
                    pages=message.source_pages,
                )
            )
    options_field = field_map.get(normalize_field_name("Options"))
    if options_field is None:
        diagnostics.append(
            _make_diag(
                "warning",
                "shape_contract_missing_tail",
                "TCP archetype expects an options tail field.",
                pages=message.source_pages,
            )
        )
    elif options_field[1] is not None:
        diagnostics.append(
            _make_diag(
                "warning",
                "tail_width_not_opaque",
                "TCP options tail provided a fixed width; lowering will still treat it as opaque tail bytes.",
                pages=message.source_pages,
            )
        )
    for expression in ("header.data_offset > 5", "header.data_offset >= 5", "header.data_offset <= 15", "header.reserved == 0"):
        try:
            analyze_rule_expression(expression)
        except RuleSyntaxError as exc:
            diagnostics.append(
                _make_diag(
                    "error",
                    "shape_contract_invalid_rule",
                    f"TCP archetype rule is outside the supported DSL: {exc}",
                    pages=message.source_pages,
                )
            )
    return diagnostics


def _tcp_contribution(
    message: ProtocolMessage,
    source_node_ids: list[str],
) -> ArchetypeContribution:
    field_map = _field_map(message)
    fields: list[ArchetypeFieldContribution] = []
    for spec in _TCP_FIELD_SPECS:
        raw_name, _, description = field_map.get(normalize_field_name(spec.name), (spec.name, spec.width_bits, ""))
        fields.append(
            ArchetypeFieldContribution(
                name=raw_name,
                canonical_hint=spec.canonical_hint,
                width_bits=spec.width_bits,
                bit_offset_hint=spec.bit_offset_hint,
                byte_offset_hint=spec.byte_offset_hint,
                description=description,
                field_traits=list(spec.field_traits),
            )
        )
    return ArchetypeContribution(
        message_name=message.name,
        canonical_hint="tcp_header",
        core_archetype=CoreArchetype.PACKED_HEADER,
        composition_traits=[
            CompositionTrait.HEADER_LENGTH_CONTROLLED_TAIL,
            CompositionTrait.DERIVED_PADDING,
        ],
        constraint_traits=[ConstraintTrait.CONST_RESERVED_FIELD],
        fields=fields,
        tail_slots=[_tcp_tail_slot()],
        rule_clues=[
            RuleClue(kind="range_min", expression="header.data_offset >= 5", target="header.data_offset", confidence=0.95),
            RuleClue(kind="range_max", expression="header.data_offset <= 15", target="header.data_offset", confidence=0.95),
            RuleClue(kind="const", expression="header.reserved == 0", target="header.reserved", confidence=0.9),
        ],
        source_pages=list(message.source_pages),
        source_node_ids=list(source_node_ids),
        confidence=ArchetypeConfidence(
            core_archetype=0.95,
            traits={
                CompositionTrait.HEADER_LENGTH_CONTROLLED_TAIL.value: 0.95,
                CompositionTrait.DERIVED_PADDING.value: 0.85,
            },
            tail_slots={"options_tail": 0.9},
            rules={
                "header.data_offset >= 5": 0.95,
                "header.data_offset <= 15": 0.95,
                "header.reserved == 0": 0.9,
            },
        ),
        diagnostics=_tcp_shape_contract(message),
    )


def build_message_archetype_contribution_from_message(
    message: ProtocolMessage,
    source_node_ids: list[str] | None = None,
) -> ArchetypeContribution | None:
    """Prefer a native archetype sidecar, falling back to TCP standardization."""
    sidecar = message.archetype_contribution
    if isinstance(sidecar, dict):
        try:
            contribution = ArchetypeContribution.model_validate(sidecar)
            if source_node_ids:
                merged_nodes = _ordered_union(contribution.source_node_ids + list(source_node_ids))
                if merged_nodes != contribution.source_node_ids:
                    contribution = contribution.model_copy(update={"source_node_ids": merged_nodes}, deep=True)
            return contribution
        except Exception:
            pass

    if not _looks_like_tcp_header(message):
        return None
    return _tcp_contribution(message, source_node_ids=list(source_node_ids or []))


def _merge_contribution(existing: ArchetypeContribution, addition: ArchetypeContribution) -> ArchetypeContribution:
    return existing.model_copy(
        update={
            "source_pages": _sorted_union(existing.source_pages + addition.source_pages),
            "source_node_ids": _ordered_union(existing.source_node_ids + addition.source_node_ids),
            "diagnostics": list(existing.diagnostics) + list(addition.diagnostics),
        },
        deep=True,
    )


def build_message_archetype_contributions(
    protocol_name: str,
    messages: list[ProtocolMessage],
    extraction_records: list[ExtractionRecord] | None = None,
) -> list[ArchetypeContribution]:
    del protocol_name
    registry: dict[str, ArchetypeContribution] = {}

    for message in messages:
        contribution = build_message_archetype_contribution_from_message(message)
        if contribution is None:
            continue
        registry[contribution.canonical_hint] = contribution

    for record in extraction_records or []:
        message = _message_from_record(record)
        if message is None:
            continue
        contribution = build_message_archetype_contribution_from_message(message, source_node_ids=[record.node_id])
        if contribution is None:
            continue
        current = registry.get(contribution.canonical_hint)
        if current is None:
            registry[contribution.canonical_hint] = contribution
        else:
            registry[contribution.canonical_hint] = _merge_contribution(current, contribution)

    return [registry[key] for key in sorted(registry)]
