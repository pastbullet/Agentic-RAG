"""Tests for generated-code verification."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.codegen import generate_code
from src.extract.verify import (
    VerifyReport,
    _infer_expected_symbols_from_generated_files,
    _is_gcc_available,
    verify_generated_code,
)
from src.models import (
    ProtocolField,
    ProtocolMessage,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
)


def _schema_fixture() -> ProtocolSchema:
    return ProtocolSchema(
        protocol_name="rfc5880-BFD",
        source_document="rfc5880-BFD.pdf",
        state_machines=[
            ProtocolStateMachine(
                name="BFD Session",
                states=[
                    ProtocolState(name="Down", is_initial=True),
                    ProtocolState(name="Up", is_final=True),
                ],
                transitions=[
                    ProtocolTransition(
                        from_state="Down",
                        to_state="Up",
                        event="Receive Control Packet",
                        condition="packet valid",
                        actions=["start detection timer"],
                    )
                ],
            )
        ],
        messages=[
            ProtocolMessage(
                name="BFD Control Packet",
                fields=[
                    ProtocolField(name="Version", size_bits=3, description="protocol version"),
                    ProtocolField(name="Length", size_bits=8, description="payload length"),
                ],
            )
        ],
    )


# Feature: codegen-verify, Property 6: VerifyReport 序列化 Round-Trip
@given(
    syntax_checked=st.booleans(),
    syntax_ok=st.booleans(),
    syntax_errors=st.lists(
        st.fixed_dictionaries(
            {
                "file": st.text(max_size=20),
                "line": st.integers(min_value=0, max_value=200),
                "error": st.text(max_size=40),
            }
        ),
        max_size=4,
    ),
    structural_checks=st.lists(
        st.fixed_dictionaries(
            {
                "check": st.text(max_size=20),
                "passed": st.booleans(),
                "detail": st.text(max_size=40),
            }
        ),
        max_size=4,
    ),
    test_results=st.lists(
        st.fixed_dictionaries(
            {
                "test_name": st.text(max_size=20),
                "passed": st.booleans(),
                "error": st.text(max_size=40),
            }
        ),
        max_size=4,
    ),
    coverage_summary=st.text(max_size=80),
)
@settings(max_examples=100)
def test_verify_report_round_trip(
    syntax_checked: bool,
    syntax_ok: bool,
    syntax_errors: list[dict],
    structural_checks: list[dict],
    test_results: list[dict],
    coverage_summary: str,
):
    report = VerifyReport(
        syntax_checked=syntax_checked,
        syntax_ok=syntax_ok,
        syntax_errors=syntax_errors,
        structural_checks=structural_checks,
        test_results=test_results,
        coverage_summary=coverage_summary,
    )

    assert VerifyReport.from_dict(report.to_dict()) == report


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
# Feature: codegen-verify, Property 2: 语法有效性
@given(include_state_machine=st.booleans(), include_message=st.booleans())
@settings(max_examples=100, deadline=None)
def test_verify_generated_code_reports_syntax_ok_when_gcc_available(
    include_state_machine: bool,
    include_message: bool,
):
    import tempfile

    if not include_state_machine and not include_message:
        include_message = True
    schema = ProtocolSchema(
        protocol_name="rfc5880-BFD",
        source_document="rfc5880-BFD.pdf",
        state_machines=_schema_fixture().state_machines if include_state_machine else [],
        messages=_schema_fixture().messages if include_message else [],
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        generated_dir = Path(tmpdir) / "generated"
        codegen_result = generate_code(schema, str(generated_dir))

        report = verify_generated_code(
            str(generated_dir),
            schema,
            "rfc5880-BFD.pdf",
            codegen_result.expected_symbols,
            codegen_result.generated_msg_headers,
            codegen_result.generated_msgs,
        )

        assert report.syntax_checked is True
        assert report.syntax_ok is True
        assert report.syntax_errors == []


def test_verify_generated_code_marks_syntax_skipped_when_gcc_unavailable(monkeypatch, tmp_path: Path):
    schema = _schema_fixture()
    codegen_result = generate_code(schema, str(tmp_path / "generated"))
    monkeypatch.setattr("src.extract.verify._is_gcc_available", lambda: False)

    report = verify_generated_code(
        str(tmp_path / "generated"),
        schema,
        "rfc5880-BFD.pdf",
        codegen_result.expected_symbols,
        codegen_result.generated_msg_headers,
        codegen_result.generated_msgs,
    )

    assert report.syntax_checked is False
    assert report.syntax_ok is False
    assert "gcc is unavailable" in report.coverage_summary


def test_verify_generated_code_checks_structural_completeness(tmp_path: Path):
    schema = _schema_fixture()
    codegen_result = generate_code(schema, str(tmp_path / "generated"))

    report = verify_generated_code(
        str(tmp_path / "generated"),
        schema,
        "rfc5880-BFD.pdf",
        codegen_result.expected_symbols,
        codegen_result.generated_msg_headers,
        codegen_result.generated_msgs,
    )

    assert report.structural_checks
    assert all(item["passed"] for item in report.structural_checks)
    assert report.test_results[0]["test_name"] == "test_roundtrip_stub"


def test_verify_generated_code_infers_expected_symbols_for_standalone_verify(tmp_path: Path):
    schema = _schema_fixture()
    generate_code(schema, str(tmp_path / "generated"))

    inferred = _infer_expected_symbols_from_generated_files(str(tmp_path / "generated"), schema)
    report = verify_generated_code(str(tmp_path / "generated"), schema, "rfc5880-BFD.pdf")

    assert inferred
    assert report.structural_checks
    assert all(item["passed"] for item in report.structural_checks)
    assert (tmp_path / "generated" / "test_roundtrip.c").exists()


def test_verify_generated_code_coverage_summary_mentions_component_counts(tmp_path: Path):
    schema = _schema_fixture()
    codegen_result = generate_code(schema, str(tmp_path / "generated"))

    report = verify_generated_code(
        str(tmp_path / "generated"),
        schema,
        "rfc5880-BFD.pdf",
        codegen_result.expected_symbols,
        codegen_result.generated_msg_headers,
        codegen_result.generated_msgs,
    )

    assert "state_machines=1" in report.coverage_summary
    assert "messages=1" in report.coverage_summary
    assert "syntax_checked=" in report.coverage_summary
    assert "structural=" in report.coverage_summary
