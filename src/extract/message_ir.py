"""MessageIR lowering, contribution merge, and normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from src.extract.merge import ExtractionRecord, normalize_field_name, normalize_name_v2
from src.extract.rule_dsl import RuleSyntaxError, analyze_rule_expression
from src.models import (
    CodegenHints,
    EnumDomain,
    EnumValue,
    FieldIR,
    IRDiagnostic,
    MessageIR,
    NormalizationStatus,
    PresenceRule,
    ProtocolField,
    ProtocolMessage,
    SectionIR,
    ValidationRule,
)


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower())
    return re.sub(r"_+", "_", text).strip("_") or "message"


def _sorted_union(values: list[list[int]]) -> list[int]:
    return sorted({item for group in values for item in group if isinstance(item, int) and item > 0})


def _ordered_union(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def _make_diag(
    level: str,
    code: str,
    message: str,
    source_pages: list[int] | None = None,
    source_node_ids: list[str] | None = None,
) -> IRDiagnostic:
    return IRDiagnostic(
        level=level,  # type: ignore[arg-type]
        code=code,
        message=message,
        source_pages=list(source_pages or []),
        source_node_ids=list(source_node_ids or []),
    )


@dataclass(frozen=True)
class FieldContribution:
    canonical_name: str
    name: str
    order_index: int
    declared_bit_width: int | None = None
    declared_bit_offset: int | None = None
    declared_byte_offset: int | None = None
    is_array: bool = False
    array_len: int | None = None
    is_variable_length: bool = False
    length_from_field: str | None = None
    optional: bool = False
    const_value: int | str | None = None
    enum_domain_id: str | None = None
    description: str | None = None
    source_pages: list[int] = field(default_factory=list)
    source_node_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SectionContribution:
    section_id: str
    name: str
    canonical_name: str
    kind: str
    parent_section_id: str | None = None
    declared_bit_offset: int | None = None
    declared_byte_offset: int | None = None
    declared_bit_width: int | None = None
    optional: bool = False
    presence_rule_ids: list[str] = field(default_factory=list)
    field_ids: list[str] = field(default_factory=list)
    source_pages: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class MessageContribution:
    identity_key: str
    protocol_name: str
    display_name: str
    source_message_name: str
    source_pages: list[int]
    source_node_ids: list[str]
    fields: list[FieldContribution] = field(default_factory=list)
    sections: list[SectionContribution] = field(default_factory=list)
    presence_rules: list[PresenceRule] = field(default_factory=list)
    validation_rules: list[ValidationRule] = field(default_factory=list)
    enum_domains: list[EnumDomain] = field(default_factory=list)
    codegen_hints: CodegenHints = field(default_factory=lambda: CodegenHints(preferred_template="message_ir_v1"))


@dataclass
class _DraftState:
    message_ir: MessageIR
    order_index: dict[str, int] = field(default_factory=dict)
    offset_conflict: bool = False


@dataclass(frozen=True)
class _FieldSpec:
    canonical_name: str
    name: str
    width_bits: int | None = None
    byte_offset: int | None = None
    is_array: bool = False
    array_len: int | None = None
    is_variable_length: bool = False
    length_from_field: str | None = None
    const_value: int | str | None = None
    enum_domain_id: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class _MessageProfile:
    identity_key: str
    display_name: str
    section_name: str
    section_canonical_name: str
    field_specs: tuple[_FieldSpec, ...]
    enum_domains: tuple[EnumDomain, ...]
    validation_specs: tuple[tuple[str, str, str, str], ...]
    min_size_bits: int | None = None
    max_size_bits: int | None = None
    total_size_bits: int | None = None


def _enum_domain(enum_id: str, field_name: str, values: list[tuple[int, str, str | None]]) -> EnumDomain:
    return EnumDomain(
        enum_id=enum_id,
        field_name=field_name,
        values=[EnumValue(value=value, name=name, description=description) for value, name, description in values],
    )


_AUTH_SECTION = "auth"
_SIMPLE_PASSWORD_PROFILE = _MessageProfile(
    identity_key="bfd_auth_simple_password",
    display_name="BFD Authentication Section (Simple Password Authentication)",
    section_name="Authentication Section",
    section_canonical_name=_AUTH_SECTION,
    field_specs=(
        _FieldSpec("auth_type", "Auth Type", width_bits=8, byte_offset=0, const_value=1, enum_domain_id="bfd_auth_type_simple", aliases=("auth type", "authentication type")),
        _FieldSpec("auth_len", "Auth Len", width_bits=8, byte_offset=1, aliases=("auth len",)),
        _FieldSpec("auth_key_id", "Auth Key ID", width_bits=8, byte_offset=2, aliases=("auth key id", "key id")),
        _FieldSpec(
            "password",
            "Password",
            width_bits=8,
            byte_offset=3,
            is_array=True,
            array_len=16,
            is_variable_length=True,
            length_from_field="auth.auth_len",
            aliases=("password",),
        ),
    ),
    enum_domains=(
        _enum_domain(
            "bfd_auth_type_simple",
            "auth.auth_type",
            [(1, "simple_password", "Simple Password authentication")],
        ),
    ),
    validation_specs=(
        ("const", "auth_type", "auth.auth_type == 1", "Auth Type MUST be 1 for Simple Password authentication."),
        ("length_min", "auth_len", "auth.auth_len >= 4", "Auth Len MUST be at least 4 bytes."),
        ("length_max", "auth_len", "auth.auth_len <= 19", "Auth Len MUST not exceed 19 bytes."),
    ),
    min_size_bits=32,
    max_size_bits=152,
)

_MD5_PROFILE = _MessageProfile(
    identity_key="bfd_auth_keyed_md5",
    display_name="Keyed MD5 and Meticulous Keyed MD5 Authentication Section",
    section_name="Authentication Section",
    section_canonical_name=_AUTH_SECTION,
    field_specs=(
        _FieldSpec("auth_type", "Auth Type", width_bits=8, byte_offset=0, enum_domain_id="bfd_auth_type_md5", aliases=("auth type", "authentication type")),
        _FieldSpec("auth_len", "Auth Len", width_bits=8, byte_offset=1, const_value=24, aliases=("auth len",)),
        _FieldSpec("auth_key_id", "Auth Key ID", width_bits=8, byte_offset=2, aliases=("auth key id", "key id")),
        _FieldSpec("reserved", "Reserved", width_bits=8, byte_offset=3, const_value=0, aliases=("reserved",)),
        _FieldSpec("sequence_number", "Sequence Number", width_bits=32, byte_offset=4, aliases=("sequence number",)),
        _FieldSpec("auth_key_digest", "Auth Key/Digest", width_bits=128, byte_offset=8, is_array=True, array_len=16, aliases=("auth key digest", "digest", "auth key/digest")),
    ),
    enum_domains=(
        _enum_domain(
            "bfd_auth_type_md5",
            "auth.auth_type",
            [
                (2, "keyed_md5", "Keyed MD5 authentication"),
                (3, "meticulous_keyed_md5", "Meticulous Keyed MD5 authentication"),
            ],
        ),
    ),
    validation_specs=(
        ("enum", "auth_type", "auth.auth_type in {2,3}", "Auth Type MUST be 2 or 3 for Keyed MD5 variants."),
        ("const", "auth_len", "auth.auth_len == 24", "Auth Len MUST be 24 for Keyed MD5 variants."),
        ("const", "reserved", "auth.reserved == 0", "Reserved MUST be zero."),
    ),
    total_size_bits=192,
)

_SHA1_PROFILE = _MessageProfile(
    identity_key="bfd_auth_keyed_sha1",
    display_name="BFD Authentication Section: Keyed SHA1 / Meticulous Keyed SHA1",
    section_name="Authentication Section",
    section_canonical_name=_AUTH_SECTION,
    field_specs=(
        _FieldSpec("auth_type", "Auth Type", width_bits=8, byte_offset=0, enum_domain_id="bfd_auth_type_sha1", aliases=("auth type", "authentication type")),
        _FieldSpec("auth_len", "Auth Len", width_bits=8, byte_offset=1, const_value=28, aliases=("auth len",)),
        _FieldSpec("auth_key_id", "Auth Key ID", width_bits=8, byte_offset=2, aliases=("auth key id", "key id")),
        _FieldSpec("reserved", "Reserved", width_bits=8, byte_offset=3, const_value=0, aliases=("reserved",)),
        _FieldSpec("sequence_number", "Sequence Number", width_bits=32, byte_offset=4, aliases=("sequence number",)),
        _FieldSpec("auth_key_hash", "Auth Key/Hash", width_bits=160, byte_offset=8, is_array=True, array_len=20, aliases=("auth key hash", "hash", "auth key/hash")),
    ),
    enum_domains=(
        _enum_domain(
            "bfd_auth_type_sha1",
            "auth.auth_type",
            [
                (4, "keyed_sha1", "Keyed SHA1 authentication"),
                (5, "meticulous_keyed_sha1", "Meticulous Keyed SHA1 authentication"),
            ],
        ),
    ),
    validation_specs=(
        ("enum", "auth_type", "auth.auth_type in {4,5}", "Auth Type MUST be 4 or 5 for Keyed SHA1 variants."),
        ("const", "auth_len", "auth.auth_len == 28", "Auth Len MUST be 28 for Keyed SHA1 variants."),
        ("const", "reserved", "auth.reserved == 0", "Reserved MUST be zero."),
    ),
    total_size_bits=224,
)

_PROFILES = (
    _SIMPLE_PASSWORD_PROFILE,
    _MD5_PROFILE,
    _SHA1_PROFILE,
)
_PROFILE_BY_KEY = {profile.identity_key: profile for profile in _PROFILES}


def _match_profile(name: str) -> _MessageProfile | None:
    raw = (name or "").strip().lower()
    normalized = normalize_name_v2(name, aggressive=True)
    if "simple password" in normalized or "simple password" in raw:
        return _SIMPLE_PASSWORD_PROFILE
    if re.search(r"\bsha\s*1\b", raw) or "sha1" in raw or "sha1" in normalized:
        return _SHA1_PROFILE
    if re.search(r"\bmd\s*5\b", raw) or "md5" in raw or "md5" in normalized:
        return _MD5_PROFILE
    return None


def _canonical_identity_for_message(name: str) -> str:
    profile = _match_profile(name)
    if profile is not None:
        return profile.identity_key
    return _slugify(normalize_name_v2(name, aggressive=True))


def _canonical_field_name(field_name: str, profile: _MessageProfile | None) -> str:
    normalized = normalize_field_name(field_name)
    if profile is not None:
        for spec in profile.field_specs:
            aliases = {normalize_field_name(spec.name), *(normalize_field_name(alias) for alias in spec.aliases)}
            if normalized in aliases:
                return spec.canonical_name
    return _slugify(normalized)


def _spec_for_field(canonical_name: str, profile: _MessageProfile | None) -> _FieldSpec | None:
    if profile is None:
        return None
    for spec in profile.field_specs:
        if spec.canonical_name == canonical_name:
            return spec
    return None


def _message_field_contribution(
    message_name: str,
    field: ProtocolField,
    index: int,
    source_pages: list[int],
    source_node_ids: list[str],
    profile: _MessageProfile | None,
) -> FieldContribution:
    canonical_name = _canonical_field_name(field.name, profile)
    spec = _spec_for_field(canonical_name, profile)
    return FieldContribution(
        canonical_name=canonical_name,
        name=spec.name if spec is not None else field.name,
        order_index=index,
        declared_bit_width=spec.width_bits if spec is not None and spec.width_bits is not None else field.size_bits,
        declared_bit_offset=spec.byte_offset * 8 if spec is not None and spec.byte_offset is not None else None,
        declared_byte_offset=spec.byte_offset if spec is not None else None,
        is_array=spec.is_array if spec is not None else False,
        array_len=spec.array_len if spec is not None else None,
        is_variable_length=spec.is_variable_length if spec is not None else False,
        length_from_field=spec.length_from_field if spec is not None else None,
        const_value=spec.const_value if spec is not None else None,
        enum_domain_id=spec.enum_domain_id if spec is not None else None,
        description=(field.description or spec.description) if spec is not None else field.description,
        source_pages=list(source_pages),
        source_node_ids=list(source_node_ids),
    )


def _profile_contribution(
    message: ProtocolMessage,
    protocol_name: str,
    source_node_ids: list[str],
    profile: _MessageProfile | None,
) -> MessageContribution:
    source_pages = list(message.source_pages)
    identity_key = _canonical_identity_for_message(message.name)
    fields = [
        _message_field_contribution(message.name, field, index, source_pages, source_node_ids, profile)
        for index, field in enumerate(message.fields)
    ]
    sections = []
    if profile is not None:
        sections.append(
            SectionContribution(
                section_id=f"{profile.identity_key}.{profile.section_canonical_name}",
                name=profile.section_name,
                canonical_name=profile.section_canonical_name,
                kind="authentication_section",
                declared_byte_offset=0,
                field_ids=[field.canonical_name for field in fields if field.canonical_name in {spec.canonical_name for spec in profile.field_specs}],
                source_pages=source_pages,
            )
        )
    else:
        sections.append(
            SectionContribution(
                section_id=f"{identity_key}.body",
                name="Message Body",
                canonical_name="body",
                kind="message_body",
                declared_byte_offset=0,
                field_ids=[field.canonical_name for field in fields],
                source_pages=source_pages,
            )
        )

    validation_rules: list[ValidationRule] = []
    enum_domains: list[EnumDomain] = []
    if profile is not None:
        enum_domains.extend(profile.enum_domains)
        for index, (kind, target_id, expression, description) in enumerate(profile.validation_specs):
            analysis = analyze_rule_expression(expression)
            validation_rules.append(
                ValidationRule(
                    rule_id=f"{identity_key}.validation.{index}",
                    target_kind="field",
                    target_id=target_id,
                    kind=kind,
                    expression=expression,
                    depends_on_fields=analysis.depends_on_fields,
                    description=description,
                )
            )

    return MessageContribution(
        identity_key=identity_key,
        protocol_name=protocol_name,
        display_name=profile.display_name if profile is not None else message.name,
        source_message_name=message.name,
        source_pages=source_pages,
        source_node_ids=list(source_node_ids),
        fields=fields,
        sections=sections,
        validation_rules=validation_rules,
        enum_domains=enum_domains,
        codegen_hints=CodegenHints(
            preferred_template="message_ir_v1",
            runtime_helpers=["pack", "unpack", "validate"],
        ),
    )


def _message_from_record(record: ExtractionRecord) -> ProtocolMessage | None:
    if record.label != "message_format":
        return None
    try:
        return ProtocolMessage.model_validate(record.payload)
    except Exception:
        return None


def build_message_contributions(
    protocol_name: str,
    messages: list[ProtocolMessage],
    extraction_records: list[ExtractionRecord] | None = None,
) -> list[MessageContribution]:
    contributions: list[MessageContribution] = []
    records = extraction_records or []

    for message in messages:
        profile = _match_profile(message.name)
        contributions.append(
            _profile_contribution(
                message=message,
                protocol_name=protocol_name,
                source_node_ids=[],
                profile=profile,
            )
        )

    for record in records:
        message = _message_from_record(record)
        if message is None:
            continue
        profile = _match_profile(message.name)
        contributions.append(
            _profile_contribution(
                message=message,
                protocol_name=protocol_name,
                source_node_ids=[record.node_id],
                profile=profile,
            )
        )
    return contributions


def _new_draft(contribution: MessageContribution) -> _DraftState:
    message_ir = MessageIR(
        ir_id=contribution.identity_key,
        protocol_name=contribution.protocol_name,
        canonical_name=contribution.identity_key,
        display_name=contribution.display_name,
        source_message_names=[contribution.source_message_name],
        source_pages=list(contribution.source_pages),
        source_node_ids=list(contribution.source_node_ids),
        sections=[],
        fields=[],
        presence_rules=[],
        validation_rules=[],
        enum_domains=[],
        codegen_hints=contribution.codegen_hints,
        normalization_status=NormalizationStatus.DRAFT,
    )
    return _DraftState(message_ir=message_ir)


def _merge_field(draft: _DraftState, contribution: FieldContribution) -> None:
    fields_by_name = {field.canonical_name: field for field in draft.message_ir.fields}
    existing = fields_by_name.get(contribution.canonical_name)
    if existing is None:
        draft.message_ir.fields.append(
            FieldIR(
                field_id=f"{draft.message_ir.ir_id}.{contribution.canonical_name}",
                name=contribution.name,
                canonical_name=contribution.canonical_name,
                declared_bit_width=contribution.declared_bit_width,
                declared_bit_offset=contribution.declared_bit_offset,
                declared_byte_offset=contribution.declared_byte_offset,
                is_array=contribution.is_array,
                array_len=contribution.array_len,
                is_variable_length=contribution.is_variable_length,
                length_from_field=contribution.length_from_field,
                optional=contribution.optional,
                const_value=contribution.const_value,
                enum_domain_id=contribution.enum_domain_id,
                description=contribution.description,
                source_pages=list(contribution.source_pages),
                source_node_ids=list(contribution.source_node_ids),
            )
        )
        draft.order_index[contribution.canonical_name] = contribution.order_index
        return

    if existing.declared_bit_width is None and contribution.declared_bit_width is not None:
        existing.declared_bit_width = contribution.declared_bit_width
    elif (
        existing.declared_bit_width is not None
        and contribution.declared_bit_width is not None
        and existing.declared_bit_width != contribution.declared_bit_width
    ):
        draft.message_ir.diagnostics.append(
            _make_diag(
                "error",
                "field_width_conflict",
                f"Field {existing.canonical_name} has conflicting widths: "
                f"{existing.declared_bit_width} vs {contribution.declared_bit_width}",
                source_pages=existing.source_pages + contribution.source_pages,
                source_node_ids=existing.source_node_ids + contribution.source_node_ids,
            )
        )

    if existing.declared_byte_offset is None and contribution.declared_byte_offset is not None:
        existing.declared_byte_offset = contribution.declared_byte_offset
    elif (
        existing.declared_byte_offset is not None
        and contribution.declared_byte_offset is not None
        and existing.declared_byte_offset != contribution.declared_byte_offset
    ):
        draft.offset_conflict = True
        draft.message_ir.diagnostics.append(
            _make_diag(
                "error",
                "field_offset_conflict",
                f"Field {existing.canonical_name} has conflicting byte offsets: "
                f"{existing.declared_byte_offset} vs {contribution.declared_byte_offset}",
                source_pages=existing.source_pages + contribution.source_pages,
                source_node_ids=existing.source_node_ids + contribution.source_node_ids,
            )
        )

    if existing.description in {"", None} and contribution.description:
        existing.description = contribution.description
    elif contribution.description and len(contribution.description) > len(existing.description or ""):
        existing.description = contribution.description

    if existing.const_value is None and contribution.const_value is not None:
        existing.const_value = contribution.const_value
    elif (
        existing.const_value is not None
        and contribution.const_value is not None
        and existing.const_value != contribution.const_value
    ):
        draft.message_ir.diagnostics.append(
            _make_diag(
                "error",
                "field_const_conflict",
                f"Field {existing.canonical_name} has conflicting constants: "
                f"{existing.const_value!r} vs {contribution.const_value!r}",
                source_pages=existing.source_pages + contribution.source_pages,
                source_node_ids=existing.source_node_ids + contribution.source_node_ids,
            )
        )

    if existing.enum_domain_id is None and contribution.enum_domain_id is not None:
        existing.enum_domain_id = contribution.enum_domain_id
    existing.is_array = existing.is_array or contribution.is_array
    existing.array_len = existing.array_len or contribution.array_len
    existing.is_variable_length = existing.is_variable_length or contribution.is_variable_length
    existing.length_from_field = existing.length_from_field or contribution.length_from_field
    existing.optional = existing.optional or contribution.optional
    existing.source_pages = _sorted_union([existing.source_pages, contribution.source_pages])
    existing.source_node_ids = _ordered_union(existing.source_node_ids + contribution.source_node_ids)
    draft.order_index[contribution.canonical_name] = min(
        draft.order_index.get(contribution.canonical_name, contribution.order_index),
        contribution.order_index,
    )


def _merge_section(draft: _DraftState, contribution: SectionContribution) -> None:
    sections_by_name = {section.canonical_name: section for section in draft.message_ir.sections}
    existing = sections_by_name.get(contribution.canonical_name)
    if existing is None:
        draft.message_ir.sections.append(
            SectionIR(
                section_id=contribution.section_id,
                name=contribution.name,
                canonical_name=contribution.canonical_name,
                kind=contribution.kind,
                parent_section_id=contribution.parent_section_id,
                declared_bit_offset=contribution.declared_bit_offset,
                declared_byte_offset=contribution.declared_byte_offset,
                declared_bit_width=contribution.declared_bit_width,
                optional=contribution.optional,
                presence_rule_ids=list(contribution.presence_rule_ids),
                field_ids=list(contribution.field_ids),
                source_pages=list(contribution.source_pages),
            )
        )
        return

    if existing.declared_byte_offset is None and contribution.declared_byte_offset is not None:
        existing.declared_byte_offset = contribution.declared_byte_offset
    elif (
        existing.declared_byte_offset is not None
        and contribution.declared_byte_offset is not None
        and existing.declared_byte_offset != contribution.declared_byte_offset
    ):
        draft.offset_conflict = True
        draft.message_ir.diagnostics.append(
            _make_diag(
                "error",
                "section_offset_conflict",
                f"Section {existing.canonical_name} has conflicting byte offsets: "
                f"{existing.declared_byte_offset} vs {contribution.declared_byte_offset}",
                source_pages=existing.source_pages + contribution.source_pages,
            )
        )
    existing.field_ids = _ordered_union(existing.field_ids + contribution.field_ids)
    existing.source_pages = _sorted_union([existing.source_pages, contribution.source_pages])


def _rule_fingerprint(rule: PresenceRule | ValidationRule) -> tuple[Any, ...]:
    return (
        rule.target_kind,
        rule.target_id,
        getattr(rule, "kind", None),
        rule.expression,
        tuple(rule.depends_on_fields),
        rule.description,
    )


def _merge_rule_list(existing: list[PresenceRule | ValidationRule], additions: list[PresenceRule | ValidationRule]) -> list[PresenceRule | ValidationRule]:
    fingerprints = {_rule_fingerprint(rule) for rule in existing}
    for rule in additions:
        fingerprint = _rule_fingerprint(rule)
        if fingerprint not in fingerprints:
            existing.append(rule)
            fingerprints.add(fingerprint)
    return existing


def _merge_enum_domains(existing: list[EnumDomain], additions: list[EnumDomain]) -> list[EnumDomain]:
    by_id = {domain.enum_id: domain for domain in existing}
    for domain in additions:
        target = by_id.get(domain.enum_id)
        if target is None:
            existing.append(domain.model_copy(deep=True))
            by_id[domain.enum_id] = existing[-1]
            continue
        values = {(item.value, item.name): item for item in target.values}
        for item in domain.values:
            values.setdefault((item.value, item.name), item)
        target.values = list(values.values())
    return existing


def build_message_ir_registry(
    protocol_name: str,
    messages: list[ProtocolMessage],
    extraction_records: list[ExtractionRecord] | None = None,
) -> dict[str, MessageIR]:
    registry: dict[str, _DraftState] = {}
    for contribution in build_message_contributions(protocol_name, messages, extraction_records):
        draft = registry.get(contribution.identity_key)
        if draft is None:
            draft = _new_draft(contribution)
            registry[contribution.identity_key] = draft
        draft.message_ir.display_name = contribution.display_name or draft.message_ir.display_name
        draft.message_ir.source_message_names = _ordered_union(
            draft.message_ir.source_message_names + [contribution.source_message_name]
        )
        draft.message_ir.source_pages = _sorted_union([draft.message_ir.source_pages, contribution.source_pages])
        draft.message_ir.source_node_ids = _ordered_union(
            draft.message_ir.source_node_ids + contribution.source_node_ids
        )
        for field in contribution.fields:
            _merge_field(draft, field)
        for section in contribution.sections:
            _merge_section(draft, section)
        draft.message_ir.presence_rules = _merge_rule_list(draft.message_ir.presence_rules, contribution.presence_rules)
        draft.message_ir.validation_rules = _merge_rule_list(
            draft.message_ir.validation_rules,
            contribution.validation_rules,
        )
        draft.message_ir.enum_domains = _merge_enum_domains(draft.message_ir.enum_domains, contribution.enum_domains)
        draft.message_ir.codegen_hints = contribution.codegen_hints
    return {key: normalize_message_ir(state.message_ir, state.order_index, state.offset_conflict) for key, state in registry.items()}


def _infer_storage_type(field: FieldIR) -> str | None:
    if field.is_array:
        return "bytes"
    width = field.resolved_bit_width
    if width is None:
        return None
    if width == 8:
        return "uint8_t"
    if width == 16:
        return "uint16_t"
    if width == 32:
        return "uint32_t"
    if width == 64:
        return "uint64_t"
    if width % 8 == 0:
        return "bytes"
    return None


def _field_order(message_ir: MessageIR, order_index: dict[str, int]) -> list[FieldIR]:
    by_offset = {
        field.canonical_name: field.declared_byte_offset
        for field in message_ir.fields
        if field.declared_byte_offset is not None
    }
    return sorted(
        message_ir.fields,
        key=lambda field: (
            by_offset.get(field.canonical_name, 10**6),
            order_index.get(field.canonical_name, 10**6),
            field.canonical_name,
        ),
    )


def _derive_layout_kind(message_ir: MessageIR) -> str:
    has_bitfield = any(field.is_bitfield for field in message_ir.fields)
    has_variable = any(field.is_variable_length for field in message_ir.fields)
    has_optional = any(field.optional for field in message_ir.fields) or bool(message_ir.presence_rules)
    if has_bitfield and not has_variable and not has_optional:
        return "bitfield_packed"
    if has_variable and not has_optional and not has_bitfield:
        return "variable_length"
    if has_optional and not has_bitfield and not has_variable:
        return "optional_section"
    if has_bitfield or has_variable or has_optional:
        return "composite"
    return "fixed_bytes"


def _field_ref_exists(message_ir: MessageIR, field_ref: str) -> bool:
    if "." in field_ref:
        section_name, canonical_name = field_ref.split(".", 1)
        if section_name not in {section.canonical_name for section in message_ir.sections}:
            return False
    else:
        canonical_name = field_ref
    return any(field.canonical_name == canonical_name for field in message_ir.fields)


def normalize_message_ir(message_ir: MessageIR, order_index: dict[str, int] | None = None, offset_conflict: bool = False) -> MessageIR:
    normalized = message_ir.model_copy(deep=True)
    positions = dict(order_index or {})
    ordered_fields = _field_order(normalized, positions)
    normalized.normalized_field_order = [field.canonical_name for field in ordered_fields]

    byte_cursor = 0
    max_total_bits = 0
    min_total_bits = 0
    for field in ordered_fields:
        width = field.declared_bit_width
        if width is None:
            normalized.diagnostics.append(
                _make_diag(
                    "error",
                    "missing_field_width",
                    f"Field {field.canonical_name} is missing declared width.",
                    source_pages=field.source_pages,
                    source_node_ids=field.source_node_ids,
                )
            )
            continue
        field.is_bitfield = width % 8 != 0
        if field.declared_byte_offset is not None and field.declared_byte_offset != byte_cursor and not field.is_variable_length:
            normalized.diagnostics.append(
                _make_diag(
                    "error",
                    "field_order_offset_mismatch",
                    f"Field {field.canonical_name} offset {field.declared_byte_offset} does not match normalized order cursor {byte_cursor}.",
                    source_pages=field.source_pages,
                    source_node_ids=field.source_node_ids,
                )
            )
        field.resolved_byte_offset = field.declared_byte_offset if field.declared_byte_offset is not None else byte_cursor
        field.resolved_bit_offset = field.resolved_byte_offset * 8 if field.resolved_byte_offset is not None else None
        field.resolved_bit_width = width
        field.storage_type = _infer_storage_type(field)
        if field.storage_type is None:
            normalized.diagnostics.append(
                _make_diag(
                    "error",
                    "unsupported_field_storage",
                    f"Field {field.canonical_name} cannot infer a supported storage type from width {width}.",
                    source_pages=field.source_pages,
                    source_node_ids=field.source_node_ids,
                )
            )
        if field.is_variable_length:
            if not field.length_from_field or not _field_ref_exists(normalized, field.length_from_field):
                normalized.diagnostics.append(
                    _make_diag(
                        "error",
                        "missing_length_dependency",
                        f"Variable-length field {field.canonical_name} is missing a valid length dependency.",
                        source_pages=field.source_pages,
                        source_node_ids=field.source_node_ids,
                    )
                )
            if field.array_len is not None:
                min_total_bits = max(min_total_bits, (field.resolved_byte_offset or 0) * 8 + 8)
                max_total_bits = max(max_total_bits, ((field.resolved_byte_offset or 0) + field.array_len) * 8)
            continue

        if width % 8 != 0:
            normalized.diagnostics.append(
                _make_diag(
                    "error",
                    "bitfield_not_supported_v1",
                    f"Field {field.canonical_name} uses non-byte-aligned width {width}, outside MessageIR v1 codegen scope.",
                    source_pages=field.source_pages,
                    source_node_ids=field.source_node_ids,
                )
            )
        field_bytes = width // 8 if width % 8 == 0 else 0
        byte_cursor = (field.resolved_byte_offset or byte_cursor) + field_bytes
        min_total_bits = max(min_total_bits, byte_cursor * 8)
        max_total_bits = max(max_total_bits, byte_cursor * 8)

    for section in normalized.sections:
        section_fields = [field for field in ordered_fields if field.field_id in section.field_ids or field.canonical_name in section.field_ids]
        if section_fields:
            section.resolved_byte_offset = min(field.resolved_byte_offset or 0 for field in section_fields)
            section.resolved_bit_offset = section.resolved_byte_offset * 8
            section.resolved_bit_width = sum(
                (field.resolved_bit_width or 0) if not field.is_variable_length else 0
                for field in section_fields
            )

    fingerprints: set[tuple[Any, ...]] = set()
    deduped_rules: list[ValidationRule] = []
    for rule in normalized.validation_rules:
        fingerprint = _rule_fingerprint(rule)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        try:
            analysis = analyze_rule_expression(rule.expression)
            rule.depends_on_fields = analysis.depends_on_fields
            for field_ref in rule.depends_on_fields:
                if not _field_ref_exists(normalized, field_ref):
                    normalized.diagnostics.append(
                        _make_diag(
                            "error",
                            "missing_rule_dependency",
                            f"Rule {rule.rule_id} references unknown field {field_ref}.",
                            source_pages=normalized.source_pages,
                            source_node_ids=normalized.source_node_ids,
                        )
                    )
        except RuleSyntaxError as exc:
            normalized.diagnostics.append(
                _make_diag(
                    "error",
                    "invalid_rule_expression",
                    f"Rule {rule.rule_id} has invalid DSL expression: {exc}",
                    source_pages=normalized.source_pages,
                    source_node_ids=normalized.source_node_ids,
                )
            )
        deduped_rules.append(rule)
    normalized.validation_rules = deduped_rules

    if offset_conflict:
        normalized.diagnostics.append(
            _make_diag(
                "error",
                "offset_conflict",
                "Conflicting declared offsets were detected during contribution merge.",
                source_pages=normalized.source_pages,
                source_node_ids=normalized.source_node_ids,
            )
        )

    profile = _PROFILE_BY_KEY.get(normalized.canonical_name)
    if profile is not None:
        normalized.total_size_bits = profile.total_size_bits
        normalized.min_size_bits = profile.min_size_bits
        normalized.max_size_bits = profile.max_size_bits
        normalized.total_size_bytes = (
            normalized.total_size_bits // 8 if normalized.total_size_bits is not None else None
        )
    else:
        if max_total_bits > 0:
            normalized.total_size_bits = max_total_bits
            normalized.total_size_bytes = max_total_bits // 8

    if normalized.total_size_bits is None:
        if normalized.min_size_bits is None:
            normalized.min_size_bits = min_total_bits or None
        if normalized.max_size_bits is None:
            normalized.max_size_bits = max_total_bits or None

    normalized.layout_kind = _derive_layout_kind(normalized)
    normalized.codegen_hints.preferred_template = normalized.layout_kind

    if profile is None and "control packet" in normalize_name_v2(normalized.display_name, aggressive=True):
        normalized.diagnostics.append(
            _make_diag(
                "error",
                "message_scope_deferred",
                "Generic BFD Control Packet is explicitly deferred to MessageIR phase 2.",
                source_pages=normalized.source_pages,
                source_node_ids=normalized.source_node_ids,
            )
        )

    blocking_errors = [diag for diag in normalized.diagnostics if diag.level == "error"]
    ready = (
        bool(normalized.canonical_name)
        and bool(normalized.normalized_field_order)
        and all(
            field.resolved_bit_width is not None and field.storage_type is not None
            for field in ordered_fields
        )
        and not offset_conflict
        and not blocking_errors
        and bool(normalized.layout_kind)
    )

    if any(field.is_variable_length or field.optional for field in ordered_fields):
        for field in ordered_fields:
            if field.is_variable_length and not (normalized.min_size_bits or normalized.max_size_bits):
                ready = False
        if any(field.optional for field in ordered_fields) and not normalized.presence_rules:
            ready = False

    normalized.normalization_status = NormalizationStatus.READY if ready else NormalizationStatus.BLOCKED
    return normalized


def lower_protocol_messages_to_message_ir(
    protocol_name: str,
    messages: list[ProtocolMessage],
    extraction_records: list[ExtractionRecord] | None = None,
) -> list[MessageIR]:
    registry = build_message_ir_registry(protocol_name, messages, extraction_records)
    return [registry[key] for key in sorted(registry)]


def ready_message_irs(message_irs: list[MessageIR]) -> list[MessageIR]:
    return [message_ir for message_ir in message_irs if message_ir.normalization_status == NormalizationStatus.READY]
