"""Archetype-guided message extraction intermediate models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from src.models import IRDiagnostic


class CoreArchetype(str, Enum):
    FIXED_FIELDS = "fixed_fields"
    PACKED_HEADER = "packed_header"
    LENGTH_PREFIXED_BODY = "length_prefixed_body"
    REPEATED_TLV_SEQUENCE = "repeated_tlv_sequence"


class CompositionTrait(str, Enum):
    FLAG_OPTIONAL_TAIL = "flag_optional_tail"
    HEADER_LENGTH_CONTROLLED_TAIL = "header_length_controlled_tail"
    TYPE_DISPATCHED_TAIL = "type_dispatched_tail"
    DERIVED_PADDING = "derived_padding"


class ConstraintTrait(str, Enum):
    ENUM_CONSTRAINED_FIELD = "enum_constrained_field"
    CONST_RESERVED_FIELD = "const_reserved_field"


class TailKind(str, Enum):
    OPAQUE_BYTES = "opaque_bytes"
    MESSAGE_FAMILY = "message_family"


class FallbackMode(str, Enum):
    OPAQUE_UNTIL_TLV_IR = "opaque_until_tlv_ir"


class ArchetypeConfidence(BaseModel):
    core_archetype: float | None = None
    traits: dict[str, float] = Field(default_factory=dict)
    tail_slots: dict[str, float] = Field(default_factory=dict)
    rules: dict[str, float] = Field(default_factory=dict)


class ArchetypeFieldContribution(BaseModel):
    name: str
    canonical_hint: str
    width_bits: int | None = None
    bit_offset_hint: int | None = None
    byte_offset_hint: int | None = None
    description: str = ""
    field_traits: list[str] = Field(default_factory=list)


class TailSlotContribution(BaseModel):
    slot_name: str
    presence_expression: str | None = None
    span_expression: str | None = None
    selector_field: str | None = None
    candidates: list[str] = Field(default_factory=list)
    tail_kind: TailKind = TailKind.OPAQUE_BYTES
    fallback_mode: FallbackMode | None = None
    fixed_prefix_bits: int | None = None
    max_span_bytes: int | None = None


class RuleClue(BaseModel):
    kind: str
    expression: str
    target: str
    confidence: float | None = None


class ArchetypeContribution(BaseModel):
    message_name: str
    canonical_hint: str
    core_archetype: CoreArchetype
    composition_traits: list[CompositionTrait] = Field(default_factory=list)
    constraint_traits: list[ConstraintTrait] = Field(default_factory=list)
    fields: list[ArchetypeFieldContribution] = Field(default_factory=list)
    tail_slots: list[TailSlotContribution] = Field(default_factory=list)
    rule_clues: list[RuleClue] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    source_node_ids: list[str] = Field(default_factory=list)
    confidence: ArchetypeConfidence = Field(default_factory=ArchetypeConfidence)
    diagnostics: list[IRDiagnostic] = Field(default_factory=list)
