"""Tests for minimal Option/TLV IR v1 parsing helpers."""

from __future__ import annotations

from src.extract.option_tlv import build_tcp_option_list_ir, parse_option_list_bytes


def _tcp_option_list():
    return build_tcp_option_list_ir(
        "tcp_header",
        "tcp_header.options_tail",
        "header.data_offset * 4 - 20",
    )


def test_parse_singleton_options_and_eol_terminator():
    option_list = _tcp_option_list()

    parsed = parse_option_list_bytes(option_list, bytes([1, 0, 0xAA, 0xBB]))

    assert [item.kind_name for item in parsed.items] == ["nop", "eol"]
    assert parsed.terminated is True
    assert parsed.opaque_remainder == bytes([0xAA, 0xBB])
    assert parsed.fallback_triggered is True
    assert parsed.diagnostics == []


def test_parse_kind_length_value_options():
    option_list = _tcp_option_list()

    parsed = parse_option_list_bytes(option_list, bytes([2, 4, 0x05, 0xB4, 3, 3, 7]))

    assert [item.kind_name for item in parsed.items] == ["mss", "window_scale"]
    assert parsed.items[0].values == {"mss_value": 1460}
    assert parsed.items[1].values == {"shift_count": 7}
    assert parsed.opaque_remainder == b""
    assert parsed.fallback_triggered is False
    assert parsed.diagnostics == []


def test_parse_eol_stops_at_terminator():
    option_list = _tcp_option_list()

    parsed = parse_option_list_bytes(option_list, bytes([2, 4, 0x05, 0xB4, 0, 0x11, 0x22]))

    assert [item.kind_name for item in parsed.items] == ["mss", "eol"]
    assert parsed.terminated is True
    assert parsed.opaque_remainder == bytes([0x11, 0x22])
    assert parsed.fallback_triggered is True


def test_parse_detects_out_of_bounds_length():
    option_list = _tcp_option_list()

    parsed = parse_option_list_bytes(option_list, bytes([2, 4, 0x05]))

    assert parsed.items == []
    assert parsed.opaque_remainder == b""
    assert parsed.fallback_triggered is True
    assert parsed.diagnostics[0].code == "option_out_of_bounds"


def test_parse_unknown_kind_falls_back_to_opaque_remainder():
    option_list = _tcp_option_list()

    parsed = parse_option_list_bytes(option_list, bytes([99, 0x10, 0x20]))

    assert parsed.items == []
    assert parsed.opaque_remainder == bytes([99, 0x10, 0x20])
    assert parsed.fallback_triggered is True
    assert parsed.diagnostics[0].code == "unknown_option_kind"
