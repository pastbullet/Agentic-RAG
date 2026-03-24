"""Focused tests for the MessageIR v1 restricted rule DSL."""

from __future__ import annotations

import pytest

from src.extract.rule_dsl import (
    RuleSyntaxError,
    analyze_rule_expression,
    render_rule_expression_as_c,
)


def test_rule_parser_extracts_depends_on_fields_for_and_and_sum():
    analysis = analyze_rule_expression(
        "auth.auth_type == 1 and auth.auth_len == auth.auth_key_id + 3"
    )

    assert analysis.depends_on_fields == [
        "auth.auth_type",
        "auth.auth_len",
        "auth.auth_key_id",
    ]


def test_rule_parser_supports_implication_and_set_membership_rendering():
    rendered = render_rule_expression_as_c(
        "auth.auth_type in {2,3} -> auth.auth_len == 24",
        lambda ref: f"msg->{ref.split('.')[-1]}",
    )

    assert rendered == "((!((msg->auth_type == 2) || (msg->auth_type == 3))) || (msg->auth_len == 24))"


def test_rule_parser_rejects_free_form_natural_language():
    with pytest.raises(RuleSyntaxError):
        analyze_rule_expression("Auth Type must be 1 when Simple Password is used")
