"""Tests for config resolution — unit tests + Hypothesis property tests."""

from __future__ import annotations

import os
from unittest.mock import patch

import hypothesis.strategies as st
from hypothesis import given, settings

from src.context.reuse.config import resolve_config


# ── Unit Tests ──────────────────────────────────────────────


def test_resolve_config_prefers_function_param(monkeypatch) -> None:
    monkeypatch.setenv("CONTEXT_REUSE_ENABLED", "false")
    assert resolve_config(True, "CONTEXT_REUSE_ENABLED", False) is True


def test_resolve_config_uses_env_then_default(monkeypatch) -> None:
    monkeypatch.setenv("CONTEXT_REUSE_CHAR_BUDGET", "1234")
    assert resolve_config(None, "CONTEXT_REUSE_CHAR_BUDGET", 4000) == 1234
    monkeypatch.delenv("CONTEXT_REUSE_CHAR_BUDGET")
    assert resolve_config(None, "CONTEXT_REUSE_CHAR_BUDGET", 4000) == 4000


# ── Hypothesis Property Tests ───────────────────────────────


# Feature: context-reuse-enhancement, Property 10: 配置优先级
@given(
    func_param=st.one_of(st.just(None), st.booleans()),
    env_value=st.one_of(st.just(None), st.sampled_from(["true", "false"])),
    default=st.booleans(),
)
@settings(max_examples=100)
def test_property_10_config_priority(
    func_param: bool | None,
    env_value: str | None,
    default: bool,
) -> None:
    env_var = "_TEST_PROP10_VAR"
    env_patch = {env_var: env_value} if env_value is not None else {}
    # Build a clean environ without the test var, then add it if needed
    clean_env = {k: v for k, v in os.environ.items() if k != env_var}
    clean_env.update(env_patch)

    with patch.dict(os.environ, clean_env, clear=True):
        result = resolve_config(func_param, env_var, default)

    if func_param is not None:
        assert result == func_param
    elif env_value is not None:
        assert result == (env_value == "true")
    else:
        assert result == default
