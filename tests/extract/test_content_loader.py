"""Tests for the protocol extraction content loader."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.content_loader import get_node_text

LINE_TEXT_ST = st.text(
    alphabet=string.ascii_letters + string.digits + " -_",
    min_size=1,
    max_size=20,
)


# Feature: protocol-extraction-pipeline, Property 7: 节点内容获取正确性


@given(text=LINE_TEXT_ST)
@settings(max_examples=100)
def test_get_node_text_prefers_embedded_text(text: str):
    node = {
        "node_id": "n1",
        "text": text,
        "start_index": 1,
        "end_index": 1,
        "start_line": 1,
        "end_line": 1,
    }
    assert get_node_text(node, "does/not/matter") == text


@given(
    lines=st.lists(LINE_TEXT_ST, min_size=3, max_size=20),
    data=st.data(),
)
@settings(max_examples=100)
def test_get_node_text_slices_requested_lines(lines: list[str], data):
    text = "\n".join(lines)
    start_line = data.draw(st.integers(min_value=1, max_value=len(lines)))
    end_line = data.draw(st.integers(min_value=start_line, max_value=len(lines)))

    with tempfile.TemporaryDirectory() as tmp_dir:
        content_dir = Path(tmp_dir) / "json"
        content_dir.mkdir()
        payload = {"pages": [{"page_num": 7, "text": text, "tables": [], "images": []}]}
        (content_dir / "content_1_20.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        node = {
            "node_id": "n2",
            "start_index": 7,
            "end_index": 7,
            "start_line": start_line,
            "end_line": end_line,
        }
        expected = "\n".join(lines[start_line - 1 : end_line])
        assert get_node_text(node, str(content_dir)) == expected


def test_get_node_text_returns_none_when_content_missing(tmp_path):
    node = {
        "node_id": "n3",
        "start_index": 1,
        "end_index": 1,
        "start_line": 1,
        "end_line": 2,
    }
    assert get_node_text(node, str(tmp_path / "missing")) is None
