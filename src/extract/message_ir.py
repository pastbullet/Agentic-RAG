"""MessageIR lowering, contribution merge, and normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from src.extract.merge import ExtractionRecord, normalize_field_name, normalize_name_v2
from src.extract.rule_dsl import RuleSyntaxError, analyze_rule_expression
from src.models import (
    CodegenHints,
    CompositeDispatchCaseIR,
    CompositeTailIR,
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
    composite_tails: list[CompositeTailIR] = field(default_factory=list)
    presence_rules: list[PresenceRule] = field(default_factory=list)
    validation_rules: list[ValidationRule] = field(default_factory=list)
    enum_domains: list[EnumDomain] = field(default_factory=list)
    diagnostics: list[IRDiagnostic] = field(default_factory=list)
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
    bit_offset: int | None = None
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
    section_kind: str
    field_specs: tuple[_FieldSpec, ...]
    enum_domains: tuple[EnumDomain, ...]
    validation_specs: tuple[tuple[str, str, str, str], ...]
    min_size_bits: int | None = None
    max_size_bits: int | None = None
    total_size_bits: int | None = None
    include_only_profile_fields: bool = False
    deferred_field_aliases: tuple[str, ...] = ()
    composite_tail_specs: tuple["_CompositeTailSpec", ...] = ()


@dataclass(frozen=True)
class _CompositeDispatchCaseSpec:
    selector_values: tuple[int, ...]
    message_ir_id: str
    description: str | None = None


@dataclass(frozen=True)
class _CompositeTailSpec:
    slot_id: str
    section_name: str
    section_canonical_name: str
    presence_expression: str
    selector_field: str
    total_length_field: str
    fixed_prefix_bits: int
    dispatch_cases: tuple[_CompositeDispatchCaseSpec, ...]


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
    section_kind="authentication_section",
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
    section_kind="authentication_section",
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
    section_kind="authentication_section",
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

_HEADER_SECTION = "header"

_BFD_CONTROL_PACKET_MANDATORY_FIELD_SPECS = (
    _FieldSpec("version", "Version", width_bits=3, bit_offset=0, const_value=1, aliases=("version", "version vers", "vers")),
    _FieldSpec("diag", "Diagnostic", width_bits=5, bit_offset=3, aliases=("diagnostic", "diagnostic diag", "diag")),
    _FieldSpec("state", "State", width_bits=2, bit_offset=8, enum_domain_id="bfd_state", aliases=("state", "state sta", "sta")),
    _FieldSpec("poll", "Poll", width_bits=1, bit_offset=10, aliases=("poll", "poll p", "p")),
    _FieldSpec("final", "Final", width_bits=1, bit_offset=11, aliases=("final", "final f", "f")),
    _FieldSpec(
        "control_plane_independent",
        "Control Plane Independent",
        width_bits=1,
        bit_offset=12,
        aliases=("control plane independent", "control plane independent c", "c"),
    ),
    _FieldSpec(
        "auth_present",
        "Authentication Present",
        width_bits=1,
        bit_offset=13,
        const_value=0,
        aliases=("authentication present", "authentication present a", "a"),
    ),
    _FieldSpec("demand", "Demand", width_bits=1, bit_offset=14, aliases=("demand", "demand d", "d")),
    _FieldSpec("multipoint", "Multipoint", width_bits=1, bit_offset=15, aliases=("multipoint", "multipoint m", "m")),
    _FieldSpec("detect_mult", "Detect Mult", width_bits=8, bit_offset=16, aliases=("detect mult",)),
    _FieldSpec("length", "Length", width_bits=8, bit_offset=24, const_value=24, aliases=("length",)),
    _FieldSpec("my_discriminator", "My Discriminator", width_bits=32, bit_offset=32, aliases=("my discriminator",)),
    _FieldSpec("your_discriminator", "Your Discriminator", width_bits=32, bit_offset=64, aliases=("your discriminator",)),
    _FieldSpec(
        "desired_min_tx_interval",
        "Desired Min TX Interval",
        width_bits=32,
        bit_offset=96,
        aliases=("desired min tx interval",),
    ),
    _FieldSpec(
        "required_min_rx_interval",
        "Required Min RX Interval",
        width_bits=32,
        bit_offset=128,
        aliases=("required min rx interval",),
    ),
    _FieldSpec(
        "required_min_echo_rx_interval",
        "Required Min Echo RX Interval",
        width_bits=32,
        bit_offset=160,
        aliases=("required min echo rx interval",),
    ),
)

_BFD_CONTROL_PACKET_FULL_FIELD_SPECS = (
    _FieldSpec("version", "Version", width_bits=3, bit_offset=0, const_value=1, aliases=("version", "version vers", "vers")),
    _FieldSpec("diag", "Diagnostic", width_bits=5, bit_offset=3, aliases=("diagnostic", "diagnostic diag", "diag")),
    _FieldSpec("state", "State", width_bits=2, bit_offset=8, enum_domain_id="bfd_state", aliases=("state", "state sta", "sta")),
    _FieldSpec("poll", "Poll", width_bits=1, bit_offset=10, aliases=("poll", "poll p", "p")),
    _FieldSpec("final", "Final", width_bits=1, bit_offset=11, aliases=("final", "final f", "f")),
    _FieldSpec(
        "control_plane_independent",
        "Control Plane Independent",
        width_bits=1,
        bit_offset=12,
        aliases=("control plane independent", "control plane independent c", "c"),
    ),
    _FieldSpec(
        "auth_present",
        "Authentication Present",
        width_bits=1,
        bit_offset=13,
        aliases=("authentication present", "authentication present a", "a"),
    ),
    _FieldSpec("demand", "Demand", width_bits=1, bit_offset=14, aliases=("demand", "demand d", "d")),
    _FieldSpec("multipoint", "Multipoint", width_bits=1, bit_offset=15, aliases=("multipoint", "multipoint m", "m")),
    _FieldSpec("detect_mult", "Detect Mult", width_bits=8, bit_offset=16, aliases=("detect mult",)),
    _FieldSpec("length", "Length", width_bits=8, bit_offset=24, aliases=("length",)),
    _FieldSpec("my_discriminator", "My Discriminator", width_bits=32, bit_offset=32, aliases=("my discriminator",)),
    _FieldSpec("your_discriminator", "Your Discriminator", width_bits=32, bit_offset=64, aliases=("your discriminator",)),
    _FieldSpec(
        "desired_min_tx_interval",
        "Desired Min TX Interval",
        width_bits=32,
        bit_offset=96,
        aliases=("desired min tx interval",),
    ),
    _FieldSpec(
        "required_min_rx_interval",
        "Required Min RX Interval",
        width_bits=32,
        bit_offset=128,
        aliases=("required min rx interval",),
    ),
    _FieldSpec(
        "required_min_echo_rx_interval",
        "Required Min Echo RX Interval",
        width_bits=32,
        bit_offset=160,
        aliases=("required min echo rx interval",),
    ),
)

_BFD_CONTROL_PACKET_MANDATORY_PROFILE = _MessageProfile(
    identity_key="bfd_control_packet_mandatory",
    display_name="Generic BFD Control Packet Mandatory Section",
    section_name="Mandatory Section",
    section_canonical_name=_HEADER_SECTION,
    section_kind="mandatory_section",
    field_specs=_BFD_CONTROL_PACKET_MANDATORY_FIELD_SPECS,
    enum_domains=(
        _enum_domain(
            "bfd_state",
            "header.state",
            [
                (0, "admin_down", "AdminDown state"),
                (1, "down", "Down state"),
                (2, "init", "Init state"),
                (3, "up", "Up state"),
            ],
        ),
    ),
    validation_specs=(),
    total_size_bits=192,
    include_only_profile_fields=True,
    deferred_field_aliases=("auth type", "auth len", "authentication data"),
)

_BFD_CONTROL_PACKET_FULL_PROFILE = _MessageProfile(
    identity_key="bfd_control_packet",
    display_name="Generic BFD Control Packet",
    section_name="Mandatory Section",
    section_canonical_name=_HEADER_SECTION,
    section_kind="mandatory_section",
    field_specs=_BFD_CONTROL_PACKET_FULL_FIELD_SPECS,
    enum_domains=(
        _enum_domain(
            "bfd_state",
            "header.state",
            [
                (0, "admin_down", "AdminDown state"),
                (1, "down", "Down state"),
                (2, "init", "Init state"),
                (3, "up", "Up state"),
            ],
        ),
    ),
    validation_specs=(
        ("length_min", "length", "header.length >= 24", "Length MUST be at least the mandatory header size."),
    ),
    min_size_bits=192,
    max_size_bits=416,
    include_only_profile_fields=True,
    deferred_field_aliases=(),
    composite_tail_specs=(
        _CompositeTailSpec(
            slot_id="auth_tail",
            section_name="Authentication Tail",
            section_canonical_name="auth_tail",
            presence_expression="header.auth_present == 1",
            selector_field="auth.auth_type",
            total_length_field="header.length",
            fixed_prefix_bits=192,
            dispatch_cases=(
                _CompositeDispatchCaseSpec((1,), "bfd_auth_simple_password", "Simple Password authentication tail"),
                _CompositeDispatchCaseSpec((2, 3), "bfd_auth_keyed_md5", "Keyed MD5 authentication tail"),
                _CompositeDispatchCaseSpec((4, 5), "bfd_auth_keyed_sha1", "Keyed SHA1 authentication tail"),
            ),
        ),
    ),
)

_PROFILES = (
    _SIMPLE_PASSWORD_PROFILE,
    _MD5_PROFILE,
    _SHA1_PROFILE,
    _BFD_CONTROL_PACKET_MANDATORY_PROFILE,
    _BFD_CONTROL_PACKET_FULL_PROFILE,
)
_PROFILE_BY_KEY = {profile.identity_key: profile for profile in _PROFILES}


def _match_profile(name: str) -> _MessageProfile | None:
    raw = (name or "").strip().lower()
    normalized = normalize_name_v2(name, aggressive=True)
    if "control packet" in normalized or "control packet" in raw:
        return _BFD_CONTROL_PACKET_MANDATORY_PROFILE
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
            aliases = _spec_aliases(spec)
            if normalized in aliases:
                return spec.canonical_name
    return _slugify(normalized)


def _spec_aliases(spec: _FieldSpec) -> set[str]:
    return {normalize_field_name(spec.name), *(normalize_field_name(alias) for alias in spec.aliases)}


def _spec_for_field(canonical_name: str, profile: _MessageProfile | None) -> _FieldSpec | None:
    if profile is None:
        return None
    for spec in profile.field_specs:
        if spec.canonical_name == canonical_name:
            return spec
    return None


def _field_contribution_from_spec(
    spec: _FieldSpec,
    index: int,
    source_pages: list[int],
    source_node_ids: list[str],
    source_field: ProtocolField | None = None,
) -> FieldContribution:
    return FieldContribution(
        canonical_name=spec.canonical_name,
        name=spec.name,
        order_index=index,
        declared_bit_width=spec.width_bits if spec.width_bits is not None else (source_field.size_bits if source_field else None),
        declared_bit_offset=spec.bit_offset if spec.bit_offset is not None else (spec.byte_offset * 8 if spec.byte_offset is not None else None),
        declared_byte_offset=spec.byte_offset if spec.byte_offset is not None else (spec.bit_offset // 8 if spec.bit_offset is not None else None),
        is_array=spec.is_array,
        array_len=spec.array_len,
        is_variable_length=spec.is_variable_length,
        length_from_field=spec.length_from_field,
        const_value=spec.const_value,
        enum_domain_id=spec.enum_domain_id,
        description=(source_field.description if source_field and source_field.description else spec.description),
        source_pages=list(source_pages),
        source_node_ids=list(source_node_ids),
    )


def _composite_tail_from_spec(
    protocol_name: str,
    message_identity: str,
    spec: _CompositeTailSpec,
    source_pages: list[int] | None = None,
) -> tuple[SectionContribution, CompositeTailIR, PresenceRule]:
    section_id = f"{message_identity}.{spec.section_canonical_name}"
    rule_id = f"{message_identity}.{spec.slot_id}.presence"
    analysis = analyze_rule_expression(spec.presence_expression)
    section = SectionContribution(
        section_id=section_id,
        name=spec.section_name,
        canonical_name=spec.section_canonical_name,
        kind="optional_tail",
        declared_bit_offset=spec.fixed_prefix_bits,
        declared_byte_offset=spec.fixed_prefix_bits // 8,
        optional=True,
        presence_rule_ids=[rule_id],
        field_ids=[],
        source_pages=list(source_pages or []),
    )
    tail = CompositeTailIR(
        slot_id=f"{message_identity}.{spec.slot_id}",
        section_id=section_id,
        name=spec.section_name,
        optional=True,
        presence_rule_id=rule_id,
        selector_field=spec.selector_field,
        total_length_field=spec.total_length_field,
        fixed_prefix_bits=spec.fixed_prefix_bits,
        start_bit_offset=spec.fixed_prefix_bits,
        candidate_message_irs=[case.message_ir_id for case in spec.dispatch_cases],
        dispatch_cases=[
            CompositeDispatchCaseIR(
                case_id=f"{message_identity}.{spec.slot_id}.dispatch.{index}",
                selector_values=list(case.selector_values),
                message_ir_id=case.message_ir_id,
                description=case.description,
            )
            for index, case in enumerate(spec.dispatch_cases)
        ],
    )
    presence_rule = PresenceRule(
        rule_id=rule_id,
        target_kind="section",
        target_id=section_id,
        expression=spec.presence_expression,
        depends_on_fields=analysis.depends_on_fields,
        description=f"{spec.section_name} is present when {spec.presence_expression}.",
    )
    return section, tail, presence_rule


def _message_field_contribution(
    field: ProtocolField,
    index: int,
    source_pages: list[int],
    source_node_ids: list[str],
    profile: _MessageProfile | None,
) -> FieldContribution:
    canonical_name = _canonical_field_name(field.name, profile)
    spec = _spec_for_field(canonical_name, profile)
    if spec is not None:
        return _field_contribution_from_spec(spec, index, source_pages, source_node_ids, source_field=field)
    return FieldContribution(
        canonical_name=canonical_name,
        name=field.name,
        order_index=index,
        declared_bit_width=field.size_bits,
        declared_bit_offset=None,
        declared_byte_offset=None,
        is_array=False,
        array_len=None,
        is_variable_length=False,
        length_from_field=None,
        const_value=None,
        enum_domain_id=None,
        description=field.description,
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
    identity_key = profile.identity_key if profile is not None else _canonical_identity_for_message(message.name)
    diagnostics: list[IRDiagnostic] = []
    fields: list[FieldContribution] = []
    if profile is not None and profile.include_only_profile_fields:
        normalized_inputs = [normalize_field_name(field.name) for field in message.fields]
        matched_indices: set[int] = set()
        profile_aliases: set[str] = set()
        for spec in profile.field_specs:
            profile_aliases.update(_spec_aliases(spec))
        for index, spec in enumerate(profile.field_specs):
            source_field: ProtocolField | None = None
            spec_aliases = _spec_aliases(spec)
            for field_index, candidate in enumerate(message.fields):
                if field_index in matched_indices:
                    continue
                if normalized_inputs[field_index] in spec_aliases:
                    source_field = candidate
                    matched_indices.add(field_index)
                    break
            fields.append(
                _field_contribution_from_spec(
                    spec,
                    index,
                    source_pages,
                    source_node_ids,
                    source_field=source_field,
                )
            )

        deferred_aliases = {normalize_field_name(alias) for alias in profile.deferred_field_aliases}
        composite_tail_aliases: set[str] = set()
        for tail_spec in profile.composite_tail_specs:
            for dispatch_case in tail_spec.dispatch_cases:
                candidate_profile = _PROFILE_BY_KEY.get(dispatch_case.message_ir_id)
                if candidate_profile is None:
                    continue
                for candidate_spec in candidate_profile.field_specs:
                    composite_tail_aliases.update(_spec_aliases(candidate_spec))
        composite_tail_aliases.update(
            {
                normalize_field_name("Authentication Data"),
                normalize_field_name("Authentication Section"),
            }
        )
        deferred_fields = [
            message.fields[idx].name
            for idx, normalized_name in enumerate(normalized_inputs)
            if idx not in matched_indices and normalized_name in deferred_aliases
        ]
        if deferred_fields:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "deferred_optional_fields",
                    "Deferred unsupported optional fields in current MessageIR phase: "
                    + ", ".join(deferred_fields),
                    source_pages=source_pages,
                    source_node_ids=source_node_ids,
                )
            )
        remaining = [
            message.fields[idx].name
            for idx in range(len(message.fields))
            if idx not in matched_indices
            and normalized_inputs[idx] not in deferred_aliases
            and normalized_inputs[idx] not in profile_aliases
            and normalized_inputs[idx] not in composite_tail_aliases
        ]
        if remaining:
            diagnostics.append(
                _make_diag(
                    "warning",
                    "unmapped_profile_fields",
                    "Ignored unmapped fields while lowering profiled message: " + ", ".join(remaining),
                    source_pages=source_pages,
                    source_node_ids=source_node_ids,
                )
            )
    else:
        fields = [
            _message_field_contribution(field, index, source_pages, source_node_ids, profile)
            for index, field in enumerate(message.fields)
        ]
    sections = []
    composite_tails: list[CompositeTailIR] = []
    presence_rules: list[PresenceRule] = []
    if profile is not None:
        sections.append(
            SectionContribution(
                section_id=f"{profile.identity_key}.{profile.section_canonical_name}",
                name=profile.section_name,
                canonical_name=profile.section_canonical_name,
                kind=profile.section_kind,
                declared_bit_offset=0,
                declared_byte_offset=0,
                field_ids=[field.canonical_name for field in fields if field.canonical_name in {spec.canonical_name for spec in profile.field_specs}],
                source_pages=source_pages,
            )
        )
        for spec in profile.composite_tail_specs:
            section, tail, presence_rule = _composite_tail_from_spec(
                protocol_name,
                profile.identity_key,
                spec,
                source_pages=source_pages,
            )
            sections.append(section)
            composite_tails.append(tail)
            presence_rules.append(presence_rule)
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
        composite_tails=composite_tails,
        presence_rules=presence_rules,
        validation_rules=validation_rules,
        enum_domains=enum_domains,
        diagnostics=diagnostics,
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
        if profile is _BFD_CONTROL_PACKET_MANDATORY_PROFILE:
            contributions.append(
                _profile_contribution(
                    message=message,
                    protocol_name=protocol_name,
                    source_node_ids=[],
                    profile=_BFD_CONTROL_PACKET_FULL_PROFILE,
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
        if profile is _BFD_CONTROL_PACKET_MANDATORY_PROFILE:
            contributions.append(
                _profile_contribution(
                    message=message,
                    protocol_name=protocol_name,
                    source_node_ids=[record.node_id],
                    profile=_BFD_CONTROL_PACKET_FULL_PROFILE,
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
        composite_tails=[],
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

    if existing.declared_bit_offset is None and contribution.declared_bit_offset is not None:
        existing.declared_bit_offset = contribution.declared_bit_offset
    elif (
        existing.declared_bit_offset is not None
        and contribution.declared_bit_offset is not None
        and existing.declared_bit_offset != contribution.declared_bit_offset
    ):
        draft.offset_conflict = True
        draft.message_ir.diagnostics.append(
            _make_diag(
                "error",
                "field_bit_offset_conflict",
                f"Field {existing.canonical_name} has conflicting bit offsets: "
                f"{existing.declared_bit_offset} vs {contribution.declared_bit_offset}",
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
                option_list_id=None,
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
    if existing.declared_bit_offset is None and contribution.declared_bit_offset is not None:
        existing.declared_bit_offset = contribution.declared_bit_offset
    elif (
        existing.declared_bit_offset is not None
        and contribution.declared_bit_offset is not None
        and existing.declared_bit_offset != contribution.declared_bit_offset
    ):
        draft.offset_conflict = True
        draft.message_ir.diagnostics.append(
            _make_diag(
                "error",
                "section_bit_offset_conflict",
                f"Section {existing.canonical_name} has conflicting bit offsets: "
                f"{existing.declared_bit_offset} vs {contribution.declared_bit_offset}",
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


def _merge_composite_tails(existing: list[CompositeTailIR], additions: list[CompositeTailIR]) -> list[CompositeTailIR]:
    by_id = {tail.slot_id: tail for tail in existing}
    for tail in additions:
        if tail.slot_id not in by_id:
            existing.append(tail.model_copy(deep=True))
            by_id[tail.slot_id] = existing[-1]
            continue
        current = by_id[tail.slot_id]
        current.option_list_id = current.option_list_id or tail.option_list_id
        current.candidate_message_irs = _ordered_union(current.candidate_message_irs + tail.candidate_message_irs)
        existing_cases = {case.case_id: case for case in current.dispatch_cases}
        for case in tail.dispatch_cases:
            if case.case_id not in existing_cases:
                current.dispatch_cases.append(case.model_copy(deep=True))
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
        draft.message_ir.composite_tails = _merge_composite_tails(
            draft.message_ir.composite_tails,
            contribution.composite_tails,
        )
        draft.message_ir.presence_rules = _merge_rule_list(draft.message_ir.presence_rules, contribution.presence_rules)
        draft.message_ir.validation_rules = _merge_rule_list(
            draft.message_ir.validation_rules,
            contribution.validation_rules,
        )
        draft.message_ir.enum_domains = _merge_enum_domains(draft.message_ir.enum_domains, contribution.enum_domains)
        draft.message_ir.diagnostics.extend(contribution.diagnostics)
        draft.message_ir.codegen_hints = contribution.codegen_hints
    first_pass = {
        key: normalize_message_ir(state.message_ir, state.order_index, state.offset_conflict)
        for key, state in registry.items()
    }
    return {
        key: normalize_message_ir(
            registry[key].message_ir,
            registry[key].order_index,
            registry[key].offset_conflict,
            available_message_irs=first_pass,
        )
        for key in registry
    }


PACKED_CONTAINER_BITS = 32


@dataclass(frozen=True)
class PackedFieldLayout:
    field_id: str
    canonical_name: str
    relative_bit_offset: int
    width_bits: int
    shift_bits: int
    mask: int
    bit_lsb_index: int
    bit_msb_index: int


@dataclass(frozen=True)
class PackedContainerLayout:
    container_id: str
    start_bit_offset: int
    start_byte_offset: int
    size_bits: int
    field_names: tuple[str, ...]
    fields: tuple[PackedFieldLayout, ...]


def _declared_bit_position(field: FieldIR) -> int | None:
    if field.declared_bit_offset is not None:
        return field.declared_bit_offset
    if field.declared_byte_offset is not None:
        return field.declared_byte_offset * 8
    return None


def _infer_storage_type(field: FieldIR) -> str | None:
    if field.is_array:
        return "bytes"
    width = field.resolved_bit_width
    if width is None:
        return None
    if 1 <= width <= 8:
        return "uint8_t"
    if 9 <= width <= 16:
        return "uint16_t"
    if 17 <= width <= 32:
        return "uint32_t"
    if 33 <= width <= 64:
        return "uint64_t"
    return None


def _field_order(message_ir: MessageIR, order_index: dict[str, int]) -> list[FieldIR]:
    return sorted(
        message_ir.fields,
        key=lambda field: (
            _declared_bit_position(field) if _declared_bit_position(field) is not None else 10**9,
            order_index.get(field.canonical_name, 10**6),
            field.canonical_name,
        ),
    )


def _bit_range(field: FieldIR) -> tuple[int, int] | None:
    if field.resolved_bit_offset is None or field.resolved_bit_width is None:
        return None
    return field.resolved_bit_offset, field.resolved_bit_offset + field.resolved_bit_width


def _needs_packed_container(field: FieldIR) -> bool:
    if field.resolved_bit_offset is None or field.resolved_bit_width is None:
        return False
    return field.resolved_bit_offset % 8 != 0 or field.resolved_bit_width % 8 != 0


def _packed_mask(width_bits: int) -> int:
    return (1 << width_bits) - 1


def build_packed_containers(message_ir: MessageIR) -> tuple[list[PackedContainerLayout], list[IRDiagnostic]]:
    fixed_fields = sorted(
        [
            field
            for field in message_ir.fields
            if not field.is_array and not field.is_variable_length and field.resolved_bit_offset is not None and field.resolved_bit_width is not None
        ],
        key=lambda field: (field.resolved_bit_offset or 0, field.canonical_name),
    )
    diagnostics: list[IRDiagnostic] = []
    by_window: dict[int, list[FieldIR]] = {}
    for field in fixed_fields:
        start = field.resolved_bit_offset or 0
        end = start + (field.resolved_bit_width or 0)
        window_start = (start // PACKED_CONTAINER_BITS) * PACKED_CONTAINER_BITS
        window_end = window_start + PACKED_CONTAINER_BITS
        if end > window_end and _needs_packed_container(field):
            diagnostics.append(
                _make_diag(
                    "error",
                    "packed_field_crosses_container_boundary",
                    f"Field {field.canonical_name} crosses the {PACKED_CONTAINER_BITS}-bit packed container boundary.",
                    source_pages=field.source_pages,
                    source_node_ids=field.source_node_ids,
                )
            )
            continue
        by_window.setdefault(window_start, []).append(field)

    containers: list[PackedContainerLayout] = []
    for window_start in sorted(by_window):
        window_fields = sorted(
            by_window[window_start],
            key=lambda field: (field.resolved_bit_offset or 0, field.canonical_name),
        )
        if not any(_needs_packed_container(field) for field in window_fields):
            continue
        packed_fields: list[PackedFieldLayout] = []
        for field in window_fields:
            start = field.resolved_bit_offset or 0
            width = field.resolved_bit_width or 0
            relative = start - window_start
            if relative < 0 or relative + width > PACKED_CONTAINER_BITS:
                diagnostics.append(
                    _make_diag(
                        "error",
                        "invalid_packed_container_layout",
                        f"Field {field.canonical_name} does not fit inside its packed container window.",
                        source_pages=field.source_pages,
                        source_node_ids=field.source_node_ids,
                    )
                )
                packed_fields = []
                break
            shift = PACKED_CONTAINER_BITS - (relative + width)
            packed_fields.append(
                PackedFieldLayout(
                    field_id=field.field_id,
                    canonical_name=field.canonical_name,
                    relative_bit_offset=relative,
                    width_bits=width,
                    shift_bits=shift,
                    mask=_packed_mask(width),
                    bit_lsb_index=shift,
                    bit_msb_index=shift + width - 1,
                )
            )
        if packed_fields:
            containers.append(
                PackedContainerLayout(
                    container_id=f"{message_ir.ir_id}.packed.{window_start}",
                    start_bit_offset=window_start,
                    start_byte_offset=window_start // 8,
                    size_bits=PACKED_CONTAINER_BITS,
                    field_names=tuple(field.canonical_name for field in window_fields),
                    fields=tuple(packed_fields),
                )
            )
    return containers, diagnostics


def _derive_layout_kind(message_ir: MessageIR) -> str:
    has_bitfield = any(field.is_bitfield for field in message_ir.fields)
    has_variable = any(field.is_variable_length for field in message_ir.fields)
    has_optional = (
        any(field.optional for field in message_ir.fields)
        or bool(message_ir.presence_rules)
        or bool(message_ir.composite_tails)
    )
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


def _message_size_bounds_bits(message_ir: MessageIR) -> tuple[int | None, int | None]:
    if message_ir.total_size_bits is not None:
        return message_ir.total_size_bits, message_ir.total_size_bits
    return message_ir.min_size_bits, message_ir.max_size_bits


def normalize_message_ir(
    message_ir: MessageIR,
    order_index: dict[str, int] | None = None,
    offset_conflict: bool = False,
    available_message_irs: dict[str, MessageIR] | None = None,
) -> MessageIR:
    normalized = message_ir.model_copy(deep=True)
    positions = dict(order_index or {})
    ordered_fields = _field_order(normalized, positions)
    normalized.normalized_field_order = [field.canonical_name for field in ordered_fields]

    bit_cursor = 0
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
        declared_start = _declared_bit_position(field)
        resolved_start = declared_start if declared_start is not None else bit_cursor
        if resolved_start < bit_cursor:
            normalized.diagnostics.append(
                _make_diag(
                    "error",
                    "field_layout_overlap",
                    f"Field {field.canonical_name} overlaps a prior field in the normalized bit layout.",
                    source_pages=field.source_pages,
                    source_node_ids=field.source_node_ids,
                )
            )
        field.resolved_bit_offset = resolved_start
        field.resolved_byte_offset = resolved_start // 8
        field.resolved_bit_width = width
        field.is_bitfield = resolved_start % 8 != 0 or width % 8 != 0
        field.storage_type = _infer_storage_type(field)
        field.endianness = "network"
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
                min_total_bits = max(min_total_bits, resolved_start + width)
                max_total_bits = max(max_total_bits, resolved_start + width * field.array_len)
            bit_cursor = max(bit_cursor, resolved_start + width)
            continue

        field_end = resolved_start + width
        bit_cursor = max(bit_cursor, field_end)
        min_total_bits = max(min_total_bits, field_end)
        max_total_bits = max(max_total_bits, field_end)

    containers, container_diagnostics = build_packed_containers(normalized)
    normalized.diagnostics.extend(container_diagnostics)
    bit_indexes: dict[str, tuple[int, int]] = {}
    for container in containers:
        for packed_field in container.fields:
            bit_indexes[packed_field.canonical_name] = (
                packed_field.bit_lsb_index,
                packed_field.bit_msb_index,
            )
    for field in ordered_fields:
        indexes = bit_indexes.get(field.canonical_name)
        if indexes is not None:
            field.bit_lsb_index, field.bit_msb_index = indexes

    for section in normalized.sections:
        section_fields = [
            field for field in ordered_fields if field.field_id in section.field_ids or field.canonical_name in section.field_ids
        ]
        if section_fields:
            section_start = min(field.resolved_bit_offset or 0 for field in section_fields)
            section_end = max(
                (field.resolved_bit_offset or 0) + ((field.resolved_bit_width or 0) if not field.is_variable_length else 0)
                for field in section_fields
            )
            section.resolved_bit_offset = section_start
            section.resolved_byte_offset = section_start // 8
            section.resolved_bit_width = section_end - section_start

    has_degraded_tail = False
    if normalized.composite_tails:
        sections_by_id = {section.section_id: section for section in normalized.sections}
        presence_by_id = {rule.rule_id: rule for rule in normalized.presence_rules}
        option_lists_by_id = {option_list.list_id: option_list for option_list in normalized.option_lists}
        available = available_message_irs or {}
        for tail in normalized.composite_tails:
            if tail.presence_rule_id and tail.presence_rule_id not in presence_by_id:
                normalized.diagnostics.append(
                    _make_diag(
                        "error",
                        "missing_tail_presence_rule",
                        f"Composite tail {tail.slot_id} is missing its presence rule.",
                        source_pages=normalized.source_pages,
                        source_node_ids=normalized.source_node_ids,
                    )
                )
            if tail.total_length_field and not _field_ref_exists(normalized, tail.total_length_field):
                normalized.diagnostics.append(
                    _make_diag(
                        "error",
                        "missing_total_length_field",
                        f"Composite tail {tail.slot_id} references unknown total length field {tail.total_length_field}.",
                        source_pages=normalized.source_pages,
                        source_node_ids=normalized.source_node_ids,
                    )
                )
            if tail.fixed_prefix_bits is None:
                normalized.diagnostics.append(
                    _make_diag(
                        "error",
                        "missing_tail_prefix",
                        f"Composite tail {tail.slot_id} is missing fixed prefix size metadata.",
                        source_pages=normalized.source_pages,
                        source_node_ids=normalized.source_node_ids,
                    )
                )
                continue

            tail.start_bit_offset = tail.fixed_prefix_bits
            section = sections_by_id.get(tail.section_id)
            if section is not None:
                section.resolved_bit_offset = tail.start_bit_offset
                section.resolved_byte_offset = tail.start_bit_offset // 8
                section.optional = True
            if tail.tail_kind == "opaque_bytes":
                has_degraded_tail = True
                if not tail.span_expression:
                    normalized.diagnostics.append(
                        _make_diag(
                            "error",
                            "missing_tail_span_expression",
                            f"Opaque tail {tail.slot_id} is missing span metadata.",
                            source_pages=normalized.source_pages,
                            source_node_ids=normalized.source_node_ids,
                        )
                    )
                if tail.max_span_bits is None and tail.max_span_bytes is not None:
                    tail.max_span_bits = tail.max_span_bytes * 8
                if tail.min_span_bits is None:
                    tail.min_span_bits = 0
                if tail.max_span_bits is None:
                    normalized.diagnostics.append(
                        _make_diag(
                            "error",
                            "unknown_tail_span",
                            f"Opaque tail {tail.slot_id} could not derive any span bounds.",
                            source_pages=normalized.source_pages,
                            source_node_ids=normalized.source_node_ids,
                        )
                    )
                if section is not None and tail.max_span_bits is not None:
                    section.resolved_bit_width = tail.max_span_bits
                continue
            if tail.tail_kind == "option_list":
                option_list = option_lists_by_id.get(tail.option_list_id or "")
                if option_list is None:
                    normalized.diagnostics.append(
                        _make_diag(
                            "error",
                            "missing_option_list_binding",
                            f"Composite tail {tail.slot_id} is missing its bound option list.",
                            source_pages=normalized.source_pages,
                            source_node_ids=normalized.source_node_ids,
                        )
                    )
                    continue
                has_degraded_tail = has_degraded_tail or bool(option_list.fallback_triggered)
                if not tail.span_expression:
                    normalized.diagnostics.append(
                        _make_diag(
                            "error",
                            "missing_tail_span_expression",
                            f"Option-list tail {tail.slot_id} is missing span metadata.",
                            source_pages=normalized.source_pages,
                            source_node_ids=normalized.source_node_ids,
                        )
                    )
                if tail.max_span_bits is None and tail.max_span_bytes is not None:
                    tail.max_span_bits = tail.max_span_bytes * 8
                if tail.max_span_bits is None:
                    tail.max_span_bits = option_list.max_size_bytes * 8
                if tail.min_span_bits is None:
                    tail.min_span_bits = option_list.min_size_bytes * 8
                if section is not None and tail.max_span_bits is not None:
                    section.resolved_bit_width = tail.max_span_bits
                continue
            ready_candidates: list[MessageIR] = []
            for candidate_id in tail.candidate_message_irs:
                candidate = available.get(candidate_id)
                if candidate is None:
                    normalized.diagnostics.append(
                        _make_diag(
                            "error",
                            "missing_tail_candidate",
                            f"Composite tail {tail.slot_id} references unknown candidate {candidate_id}.",
                            source_pages=normalized.source_pages,
                            source_node_ids=normalized.source_node_ids,
                        )
                    )
                    continue
                if candidate.normalization_status != NormalizationStatus.READY:
                    normalized.diagnostics.append(
                        _make_diag(
                            "error",
                            "tail_candidate_not_ready",
                            f"Composite tail {tail.slot_id} references non-READY candidate {candidate_id}.",
                            source_pages=normalized.source_pages,
                            source_node_ids=normalized.source_node_ids,
                        )
                    )
                    continue
                ready_candidates.append(candidate)
            if not ready_candidates:
                normalized.diagnostics.append(
                    _make_diag(
                        "error",
                        "empty_tail_family",
                        f"Composite tail {tail.slot_id} does not have any READY candidate message family.",
                        source_pages=normalized.source_pages,
                        source_node_ids=normalized.source_node_ids,
                    )
                )
                continue
            min_spans = []
            max_spans = []
            for candidate in ready_candidates:
                min_bits, max_bits = _message_size_bounds_bits(candidate)
                if min_bits is not None:
                    min_spans.append(min_bits)
                if max_bits is not None:
                    max_spans.append(max_bits)
            tail.min_span_bits = min(min_spans) if min_spans else None
            tail.max_span_bits = max(max_spans) if max_spans else None
            if tail.min_span_bits is None and tail.max_span_bits is None:
                normalized.diagnostics.append(
                    _make_diag(
                        "error",
                        "unknown_tail_span",
                        f"Composite tail {tail.slot_id} could not derive any candidate span bounds.",
                        source_pages=normalized.source_pages,
                        source_node_ids=normalized.source_node_ids,
                    )
                )
            if section is not None and tail.max_span_bits is not None:
                section.resolved_bit_width = tail.max_span_bits

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
            (normalized.total_size_bits + 7) // 8 if normalized.total_size_bits is not None else None
        )
    else:
        if max_total_bits > 0 and not normalized.composite_tails and not any(field.is_variable_length for field in ordered_fields):
            normalized.total_size_bits = max_total_bits
            normalized.total_size_bytes = (max_total_bits + 7) // 8

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
            field.resolved_bit_offset is not None and field.resolved_bit_width is not None and field.storage_type is not None
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
    if normalized.composite_tails:
        option_lists_by_id = {option_list.list_id: option_list for option_list in normalized.option_lists}
        for tail in normalized.composite_tails:
            if tail.start_bit_offset is None:
                ready = False
            if tail.min_span_bits is None and tail.max_span_bits is None:
                ready = False
            if tail.tail_kind == "opaque_bytes":
                if not tail.presence_rule_id or not tail.span_expression or tail.max_span_bytes is None:
                    ready = False
                continue
            if tail.tail_kind == "option_list":
                option_list = option_lists_by_id.get(tail.option_list_id or "")
                if option_list is None:
                    ready = False
                    continue
                if (
                    not tail.presence_rule_id
                    or not tail.span_expression
                    or tail.max_span_bytes is None
                    or not tail.option_list_id
                    or not option_list.items
                ):
                    ready = False
                continue
            if not tail.presence_rule_id or not tail.selector_field or not tail.total_length_field:
                ready = False
            if not tail.candidate_message_irs or not tail.dispatch_cases:
                ready = False

    if ready:
        normalized.normalization_status = (
            NormalizationStatus.DEGRADED_READY if has_degraded_tail else NormalizationStatus.READY
        )
    else:
        normalized.normalization_status = NormalizationStatus.BLOCKED
    return normalized


def lower_protocol_messages_to_message_ir(
    protocol_name: str,
    messages: list[ProtocolMessage],
    extraction_records: list[ExtractionRecord] | None = None,
) -> list[MessageIR]:
    from src.extract.message_archetype import build_message_archetype_contributions
    from src.extract.message_archetype_lowering import lower_archetype_contributions_to_message_irs

    archetype_contributions = build_message_archetype_contributions(
        protocol_name,
        messages,
        extraction_records=extraction_records,
    )
    handled_keys = {contribution.canonical_hint for contribution in archetype_contributions}

    legacy_messages = [
        message
        for message in messages
        if _canonical_identity_for_message(message.name) not in handled_keys
    ]
    legacy_records: list[ExtractionRecord] = []
    for record in extraction_records or []:
        message = _message_from_record(record)
        if message is not None and _canonical_identity_for_message(message.name) in handled_keys:
            continue
        legacy_records.append(record)

    registry = build_message_ir_registry(protocol_name, legacy_messages, legacy_records)
    lowered_archetypes = lower_archetype_contributions_to_message_irs(protocol_name, archetype_contributions)
    combined = [registry[key] for key in sorted(registry)] + lowered_archetypes
    return sorted(combined, key=lambda item: item.canonical_name)


def ready_message_irs(message_irs: list[MessageIR]) -> list[MessageIR]:
    return [message_ir for message_ir in message_irs if message_ir.normalization_status == NormalizationStatus.READY]


def codegen_eligible_message_irs(message_irs: list[MessageIR]) -> list[MessageIR]:
    return [
        message_ir
        for message_ir in message_irs
        if message_ir.normalization_status in {NormalizationStatus.READY, NormalizationStatus.DEGRADED_READY}
    ]
