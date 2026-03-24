"""Tests for enhanced message merge behavior."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.merge import merge_messages, merge_messages_v2, normalize_field_name
from src.models import ProtocolField, ProtocolMessage


NAME_CHARS = st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters=" -_/")


def _name_strategy():
    return st.text(NAME_CHARS, min_size=1, max_size=30).filter(lambda text: text.strip() != "")


@st.composite
def protocol_message_strategy(draw):
    field_names = draw(st.lists(_name_strategy(), min_size=1, max_size=4, unique=True))
    fields = [
        ProtocolField(name=name, size_bits=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=64))))
        for name in field_names
    ]
    return ProtocolMessage(
        name=draw(_name_strategy()),
        fields=fields,
        source_pages=draw(st.lists(st.integers(min_value=1, max_value=30), min_size=1, max_size=4, unique=True)),
    )


@given(messages=st.lists(protocol_message_strategy(), min_size=0, max_size=6))
@settings(max_examples=100)
def test_merge_messages_v2_matches_phase1_when_fuzzy_matching_is_disabled(
    messages: list[ProtocolMessage],
):
    merged_v1, groups_v1 = merge_messages(messages)
    merged_v2, groups_v2, near_miss = merge_messages_v2(messages, enable_fuzzy_match=False)

    assert [item.model_dump() for item in merged_v2] == [item.model_dump() for item in merged_v1]
    assert groups_v2 == groups_v1
    assert near_miss == []


def test_normalize_field_name_handles_abbreviation_and_parentheses():
    assert normalize_field_name("Diagnostic (Diag)") == "diagnostic"
    assert normalize_field_name("Diag") == "diagnostic"
    assert normalize_field_name("Vers") == "version"
    assert normalize_field_name("Authentication Type") == normalize_field_name("Auth Type")


def test_merge_messages_v2_blocks_exclusive_keyword_pairs():
    messages = [
        ProtocolMessage(
            name="BFD Simple Password Authentication Section",
            fields=[
                ProtocolField(name="Auth Type", size_bits=8),
                ProtocolField(name="Password", size_bits=None, description="1-16 bytes, variable length"),
            ],
            source_pages=[10],
        ),
        ProtocolMessage(
            name="BFD Keyed MD5 Authentication Section",
            fields=[
                ProtocolField(name="Auth Type", size_bits=8),
                ProtocolField(name="Digest", size_bits=128),
            ],
            source_pages=[11],
        ),
        ProtocolMessage(
            name="BFD Echo Packet",
            fields=[ProtocolField(name="Opaque Payload", size_bits=None)],
            source_pages=[12],
        ),
        ProtocolMessage(
            name="BFD Control Packet",
            fields=[ProtocolField(name="Version", size_bits=3)],
            source_pages=[13],
        ),
    ]

    merged, _, near_miss = merge_messages_v2(messages)

    assert len(merged) == len(messages)
    assert {item.name for item in merged} == {item.name for item in messages}
    assert any(item["exclusive_blocked"] is True for item in near_miss)


def test_merge_messages_v2_fuzzily_merges_control_packet_name_variants():
    messages = [
        ProtocolMessage(
            name="Generic BFD Control Packet Format",
            fields=[
                ProtocolField(name="Version", size_bits=3),
                ProtocolField(name="Length", size_bits=8),
            ],
            source_pages=[7, 8],
        ),
        ProtocolMessage(
            name="BFD Control Packet",
            fields=[
                ProtocolField(name="Version", size_bits=3),
                ProtocolField(name="Length", size_bits=8),
                ProtocolField(name="Detect Mult", size_bits=8),
            ],
            source_pages=[9],
        ),
    ]

    merged, groups, _ = merge_messages_v2(messages)

    assert len(merged) == 1
    assert merged[0].name == "BFD Control Packet"
    assert merged[0].source_pages == [7, 8, 9]
    assert len(merged[0].fields) == 3
    assert any(group["field_count_after"] == 3 for group in groups)


def test_merge_messages_v2_allows_high_field_overlap_even_with_lower_name_similarity():
    messages = [
        ProtocolMessage(
            name="Auth Section Variant Alpha",
            fields=[
                ProtocolField(name="Diagnostic (Diag)", size_bits=5),
                ProtocolField(name="Vers", size_bits=3),
                ProtocolField(name="Auth Type", size_bits=8),
                ProtocolField(name="Length", size_bits=8),
                ProtocolField(name="My Discriminator", size_bits=32),
            ],
            source_pages=[1],
        ),
        ProtocolMessage(
            name="Authentication Block Beta",
            fields=[
                ProtocolField(name="Diag", size_bits=5),
                ProtocolField(name="Version", size_bits=3),
                ProtocolField(name="Authentication Type", size_bits=8),
                ProtocolField(name="Len", size_bits=8),
                ProtocolField(name="My Discriminator", size_bits=32),
            ],
            source_pages=[2],
        ),
    ]

    merged, _, _ = merge_messages_v2(messages, name_similarity_threshold=0.95, field_jaccard_threshold=0.95)

    assert len(merged) == 1
    # original field names stay unchanged (first occurrence preserved)
    assert [field.name for field in merged[0].fields][:3] == ["Diagnostic (Diag)", "Vers", "Auth Type"]
