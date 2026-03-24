"""Verification for generated protocol code."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.extract.codegen import (
    GENERATOR_NAME,
    _build_message_context,
    _load_templates,
    _protocol_prefix,
    _to_lower_snake,
    standardize_msg_name,
)
from src.models import ProtocolMessage, ProtocolSchema


@dataclass
class VerifyReport:
    syntax_checked: bool = False
    syntax_ok: bool = False
    syntax_errors: list[dict] = field(default_factory=list)
    structural_checks: list[dict] = field(default_factory=list)
    test_results: list[dict] = field(default_factory=list)
    coverage_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VerifyReport":
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})


def _is_gcc_available() -> bool:
    return shutil.which("gcc") is not None


def _check_syntax(file_path: str, include_dir: str | None = None) -> list[dict]:
    command = ["gcc", "-fsyntax-only", "-Wall"]
    if include_dir:
        command.extend(["-I", include_dir])
    command.append(file_path)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return []
    errors: list[dict] = []
    for line in completed.stderr.splitlines():
        match = re.match(r"^(.*?):(\d+):(\d+:)?\s*(fatal error|error|warning):\s*(.*)$", line)
        if match:
            errors.append(
                {
                    "file": match.group(1),
                    "line": int(match.group(2)),
                    "error": match.group(5).strip(),
                }
            )
    if not errors and completed.stderr.strip():
        errors.append({"file": file_path, "line": 0, "error": completed.stderr.strip()})
    return errors


def _check_structural_completeness(generated_dir: str, expected_symbols: list[dict]) -> list[dict]:
    content = []
    for path in sorted(Path(generated_dir).glob("*.[ch]")):
        content.append(path.read_text(encoding="utf-8"))
    text = "\n".join(content)
    checks = []
    for item in expected_symbols:
        symbol = item["symbol"]
        passed = symbol in text
        checks.append(
            {
                "check": f"{item['kind']}:{symbol}",
                "passed": passed,
                "detail": "found" if passed else f"missing symbol from {item['source']}",
            }
        )
    return checks


def _infer_expected_symbols_from_generated_files(
    generated_dir: str,
    schema: ProtocolSchema,
) -> list[dict]:
    generated_path = Path(generated_dir)
    prefix = _protocol_prefix(schema.protocol_name)
    symbols: list[dict] = []
    for header in sorted(generated_path.glob(f"{prefix}_sm_*.h")):
        stem = header.stem
        component = stem[len(f"{prefix}_sm_"):]
        symbols.extend(
            [
                {"symbol": f"{prefix}_{component}_state", "kind": "enum", "source": component},
                {"symbol": f"{prefix}_{component}_event", "kind": "enum", "source": component},
                {"symbol": f"{prefix}_{component}_transition", "kind": "function", "source": component},
            ]
        )
    for header in sorted(generated_path.glob(f"{prefix}_msg_*.h")):
        stem = header.stem
        component = stem[len(f"{prefix}_msg_"):]
        symbols.extend(
            [
                {"symbol": f"{prefix}_{component}", "kind": "struct", "source": component},
                {"symbol": f"{prefix}_{component}_pack", "kind": "function", "source": component},
                {"symbol": f"{prefix}_{component}_unpack", "kind": "function", "source": component},
            ]
        )
    return symbols


def _generate_roundtrip_stub(
    generated_msg_headers: list[str],
    generated_msgs: list[ProtocolMessage],
    output_dir: str,
    protocol_prefix: str,
    source_document: str,
) -> str:
    env = _load_templates()
    template = env.get_template("test_roundtrip.c.j2")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    contexts = []
    for message in generated_msgs:
        header_name = f"{protocol_prefix}_msg_{_to_lower_snake(standardize_msg_name(message.name))}.h"
        contexts.append(
            _build_message_context(
                protocol_prefix,
                ProtocolSchema(protocol_name=protocol_prefix, source_document=source_document),
                message,
                header_name,
            )
        )
    payload = template.render(
        generator_name=GENERATOR_NAME,
        source_document=source_document,
        generated_headers=[Path(path).name for path in generated_msg_headers],
        messages=contexts,
    )
    stub_path = output_path / "test_roundtrip.c"
    stub_path.write_text(payload, encoding="utf-8")
    return str(stub_path)


def verify_generated_code(
    generated_dir: str,
    schema: ProtocolSchema,
    doc_name: str,
    expected_symbols: list[dict] | None = None,
    generated_msg_headers: list[str] | None = None,
    generated_msgs: list[ProtocolMessage] | None = None,
) -> VerifyReport:
    generated_path = Path(generated_dir)
    if not generated_path.exists():
        raise FileNotFoundError(f"Generated directory not found: {generated_dir}")

    prefix = _protocol_prefix(schema.protocol_name)
    if expected_symbols is None:
        expected_symbols = _infer_expected_symbols_from_generated_files(generated_dir, schema)

    if generated_msg_headers is None:
        generated_msg_headers = [str(path) for path in sorted(generated_path.glob(f"{prefix}_msg_*.h"))]
    if generated_msgs is None:
        header_names = {Path(path).name for path in generated_msg_headers}
        generated_msgs = []
        for message in sorted(schema.messages, key=lambda item: item.name):
            header_name = f"{prefix}_msg_{_to_lower_snake(standardize_msg_name(message.name))}.h"
            if header_name in header_names:
                generated_msgs.append(message)

    report = VerifyReport()
    generated_state_machine_count = len(list(generated_path.glob(f"{prefix}_sm_*.h")))
    c_files = sorted(str(path) for path in generated_path.glob("*.c") if path.name != "test_roundtrip.c")

    if _is_gcc_available():
        report.syntax_checked = True
        for file_path in c_files:
            report.syntax_errors.extend(_check_syntax(file_path, include_dir=generated_dir))
    else:
        report.coverage_summary = "syntax check skipped because gcc is unavailable"

    report.structural_checks = _check_structural_completeness(generated_dir, expected_symbols)
    stub_path = _generate_roundtrip_stub(
        generated_msg_headers,
        generated_msgs,
        generated_dir,
        prefix,
        schema.source_document or doc_name,
    )

    if report.syntax_checked:
        report.syntax_errors.extend(_check_syntax(stub_path, include_dir=generated_dir))
        stub_passed = not any(error["file"] == stub_path for error in report.syntax_errors)
    else:
        stub_passed = True

    report.test_results = [
        {
            "test_name": "test_roundtrip_stub",
            "passed": stub_passed,
            "error": "" if stub_passed else "test_roundtrip.c has syntax errors",
        }
    ]
    report.syntax_ok = report.syntax_checked and not report.syntax_errors
    if not report.syntax_checked:
        report.syntax_ok = False

    passed_structural = sum(1 for item in report.structural_checks if item["passed"])
    total_structural = len(report.structural_checks)
    message = (
        f"checked state machines/messages from generated artifacts; "
        f"state_machines={generated_state_machine_count}, messages={len(generated_msgs)}, "
        f"syntax_checked={report.syntax_checked}, "
        f"syntax_ok={report.syntax_ok}, structural={passed_structural}/{total_structural}"
    )
    if report.coverage_summary:
        report.coverage_summary = f"{report.coverage_summary}; {message}"
    else:
        report.coverage_summary = message
    return report
