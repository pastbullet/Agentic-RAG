#!/usr/bin/env python3
"""
Structured-output gateway checker for OpenAI-compatible chat endpoints.

What it tests:
1) Plain text control request
2) response_format={"type":"json_object"}
3) response_format={"type":"json_schema"}
4) tools + forced tool_choice

This script is intentionally stdlib-only so it can run in constrained envs.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_ENV_PATH = Path("/Users/zwy/毕设/Kiro/.env")


def load_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalize_base_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def http_json(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: int,
    *,
    insecure: bool = False,
    ca_bundle: str = "",
) -> tuple[int, str]:
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, method=method, headers=headers, data=data)
    if insecure:
        context = ssl._create_unverified_context()
    elif ca_bundle:
        context = ssl.create_default_context(cafile=ca_bundle)
    else:
        context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def extract_message(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    return message if isinstance(message, dict) else None


def extract_content_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        return "\n".join(text_parts)
    return ""


def extract_tool_calls(message: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        return [item for item in tool_calls if isinstance(item, dict)]
    return []


def parse_tool_arguments(tool_call: dict[str, Any]) -> Any | None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    arguments = function.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        return parse_json(arguments)
    return None


def short_snippet(text: str, limit: int = 600) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def print_case_result(
    *,
    name: str,
    status: int,
    body: str,
    passed: bool,
    detail: str,
) -> None:
    print(f"\n== {name} ==")
    print(f"status: {status}")
    print(f"pass:   {passed}")
    print(f"detail: {detail}")
    print("body:")
    print(short_snippet(body) if body else "<empty>")


def run_control_case(base_url: str, headers: dict[str, str], model: str, timeout: int, *, insecure: bool, ca_bundle: str) -> bool:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply with exactly OK."},
            {"role": "user", "content": "Reply now."},
        ],
        "temperature": 0,
    }
    status, body = http_json(
        "POST",
        f"{base_url}/chat/completions",
        headers,
        payload,
        timeout,
        insecure=insecure,
        ca_bundle=ca_bundle,
    )
    parsed = parse_json(body)
    message = extract_message(parsed)
    content = extract_content_text(message).strip()
    passed = status == 200 and "OK" in content
    print_case_result(
        name="control",
        status=status,
        body=body,
        passed=passed,
        detail=f"content={content!r}",
    )
    return passed


def run_json_object_case(base_url: str, headers: dict[str, str], model: str, timeout: int, *, insecure: bool, ca_bundle: str) -> bool:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {
                "role": "user",
                "content": (
                    'Return a JSON object with exactly two keys: '
                    '"status" = "ok" and "value" = 1.'
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    status, body = http_json(
        "POST",
        f"{base_url}/chat/completions",
        headers,
        payload,
        timeout,
        insecure=insecure,
        ca_bundle=ca_bundle,
    )
    parsed = parse_json(body)
    message = extract_message(parsed)
    content = extract_content_text(message).strip()
    content_json = parse_json(content)
    passed = (
        status == 200
        and isinstance(content_json, dict)
        and content_json.get("status") == "ok"
        and content_json.get("value") == 1
    )
    print_case_result(
        name="json_object",
        status=status,
        body=body,
        passed=passed,
        detail=f"parsed_content={content_json!r}",
    )
    return passed


def run_json_schema_case(base_url: str, headers: dict[str, str], model: str, timeout: int, *, insecure: bool, ca_bundle: str) -> bool:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {
                "role": "user",
                "content": "Return an object with status='ok' and value=1.",
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output_test",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {"type": "string", "enum": ["ok"]},
                        "value": {"type": "integer", "enum": [1]},
                    },
                    "required": ["status", "value"],
                },
            },
        },
        "temperature": 0,
    }
    status, body = http_json(
        "POST",
        f"{base_url}/chat/completions",
        headers,
        payload,
        timeout,
        insecure=insecure,
        ca_bundle=ca_bundle,
    )
    parsed = parse_json(body)
    message = extract_message(parsed)
    content = extract_content_text(message).strip()
    content_json = parse_json(content)
    passed = (
        status == 200
        and isinstance(content_json, dict)
        and content_json == {"status": "ok", "value": 1}
    )
    print_case_result(
        name="json_schema",
        status=status,
        body=body,
        passed=passed,
        detail=f"parsed_content={content_json!r}",
    )
    return passed


def run_tools_case(base_url: str, headers: dict[str, str], model: str, timeout: int, *, insecure: bool, ca_bundle: str) -> bool:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Use the provided tool to return the result."},
            {
                "role": "user",
                "content": "Call emit_result with status='ok' and value=1.",
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "emit_result",
                    "description": "Return the structured output test result.",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "status": {"type": "string"},
                            "value": {"type": "integer"},
                        },
                        "required": ["status", "value"],
                    },
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {"name": "emit_result"},
        },
        "temperature": 0,
    }
    status, body = http_json(
        "POST",
        f"{base_url}/chat/completions",
        headers,
        payload,
        timeout,
        insecure=insecure,
        ca_bundle=ca_bundle,
    )
    parsed = parse_json(body)
    message = extract_message(parsed)
    tool_calls = extract_tool_calls(message)
    parsed_args = parse_tool_arguments(tool_calls[0]) if tool_calls else None
    passed = (
        status == 200
        and isinstance(parsed_args, dict)
        and parsed_args.get("status") == "ok"
        and parsed_args.get("value") == 1
    )
    print_case_result(
        name="tools_forced",
        status=status,
        body=body,
        passed=passed,
        detail=f"parsed_tool_arguments={parsed_args!r}",
    )
    return passed


def parse_case_tokens(raw: str) -> list[str]:
    tokens = [token.strip().lower() for token in raw.split(",") if token.strip()]
    if not tokens or "all" in tokens:
        return ["control", "json_object", "json_schema", "tools"]
    valid = {"control", "json_object", "json_schema", "tools"}
    invalid = [token for token in tokens if token not in valid]
    if invalid:
        raise ValueError(f"Unsupported cases: {', '.join(invalid)}")
    return tokens


def main() -> int:
    parser = argparse.ArgumentParser(description="Check structured-output support on an OpenAI-compatible gateway.")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH, help=f"Path to .env (default: {DEFAULT_ENV_PATH})")
    parser.add_argument("--base-url", default="", help="Override OPENAI_BASE_URL")
    parser.add_argument("--api-key", default="", help="Override OPENAI_API_KEY")
    parser.add_argument("--model", default="", help="Override OPENAI_MODEL_NAME")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds")
    parser.add_argument("--cases", default="all", help="Comma-separated: control,json_object,json_schema,tools,all")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification (diagnosis only)")
    parser.add_argument("--ca-bundle", default="", help="Path to CA bundle file")
    args = parser.parse_args()

    env_values = load_env_file(args.env)
    api_key = args.api_key or os.getenv("OPENAI_API_KEY") or env_values.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.getenv("OPENAI_BASE_URL") or env_values.get("OPENAI_BASE_URL", "")
    model = args.model or os.getenv("OPENAI_MODEL_NAME") or env_values.get("OPENAI_MODEL_NAME", "gpt-5.2")

    if not api_key:
        print("ERROR: missing OPENAI_API_KEY (or --api-key)")
        return 2
    if not base_url:
        print("ERROR: missing OPENAI_BASE_URL (or --base-url)")
        return 2

    base_url = normalize_base_url(base_url)
    cases = parse_case_tokens(args.cases)

    print("Config:")
    print(f"- env_file: {args.env}")
    print(f"- base_url: {base_url}")
    print(f"- model:    {model}")
    print(f"- cases:    {', '.join(cases)}")
    print(f"- key_tail: ...{api_key[-6:] if len(api_key) >= 6 else '******'}")
    if args.insecure:
        print("- tls:      insecure (verification disabled)")
    elif args.ca_bundle:
        print(f"- tls:      custom CA bundle ({args.ca_bundle})")
    else:
        print("- tls:      system trust store")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    runners = {
        "control": run_control_case,
        "json_object": run_json_object_case,
        "json_schema": run_json_schema_case,
        "tools": run_tools_case,
    }

    results: dict[str, bool] = {}
    for case in cases:
        results[case] = runners[case](
            base_url,
            headers,
            model,
            args.timeout,
            insecure=args.insecure,
            ca_bundle=args.ca_bundle,
        )

    print("\n== Summary ==")
    for case in cases:
        print(f"{case:<12} {'PASS' if results[case] else 'FAIL'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
