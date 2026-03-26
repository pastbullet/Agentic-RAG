"""Minimal Option/TLV IR models for structured tail parsing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OptionListDiagnostic(BaseModel):
    level: Literal["warning", "error"]
    code: str
    message: str


class OptionValueFieldIR(BaseModel):
    name: str
    canonical_name: str
    width_bits: int
    byte_offset: int
    description: str = ""


class OptionItemIR(BaseModel):
    item_id: str
    kind_value: int
    kind_name: str
    length_model: Literal["singleton", "kind_length_value", "fixed_size"]
    fixed_size_bytes: int | None = None
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    value_schema_kind: Literal["opaque_bytes", "fixed_fields"] = "opaque_bytes"
    value_fields: list[OptionValueFieldIR] = Field(default_factory=list)
    is_terminal: bool = False
    is_padding: bool = False
    allow_repeat: bool = True
    description: str = ""
    diagnostics: list[OptionListDiagnostic] = Field(default_factory=list)


class OptionListIR(BaseModel):
    list_id: str
    name: str
    canonical_name: str
    parent_message_ir_id: str
    parent_section_id: str
    span_expression: str
    fixed_prefix_bytes: int
    min_size_bytes: int = 0
    max_size_bytes: int
    item_kind: Literal["tcp_option", "tlv", "option"] = "option"
    has_explicit_terminator: bool = False
    terminator_values: list[int] = Field(default_factory=list)
    alignment_bytes: int | None = None
    padding_policy: str | None = None
    items: list[OptionItemIR] = Field(default_factory=list)
    fallback_mode: str | None = None
    fallback_triggered: bool = False
    diagnostics: list[OptionListDiagnostic] = Field(default_factory=list)


class ParsedOptionItem(BaseModel):
    kind_value: int
    kind_name: str
    encoded_length: int
    values: dict[str, int] = Field(default_factory=dict)
    raw_bytes: bytes = b""
    is_terminal: bool = False


class ParsedOptionList(BaseModel):
    items: list[ParsedOptionItem] = Field(default_factory=list)
    opaque_remainder: bytes = b""
    diagnostics: list[OptionListDiagnostic] = Field(default_factory=list)
    terminated: bool = False
    fallback_triggered: bool = False
