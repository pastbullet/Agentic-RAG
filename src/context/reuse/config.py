"""Configuration helpers for context reuse."""

from __future__ import annotations

import os


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {raw}")


def resolve_config(func_param, env_var_name: str, default):
    """Resolve config with priority: function param > env var > default."""
    if func_param is not None:
        return func_param

    raw = os.getenv(env_var_name)
    if raw is None or raw.strip() == "":
        return default

    if isinstance(default, bool):
        return _parse_bool(raw)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(raw)
    return raw
