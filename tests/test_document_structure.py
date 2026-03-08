"""结构工具属性测试 (Property 3-6)。

# Feature: agentic-rag-retrieval-generation, Property 3-6: 结构工具正确性
验证: 需求 2.1, 2.2, 2.3, 2.4, 2.5
"""

import json
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.tools.document_structure import get_document_structure
from src.tools.registry import DOC_REGISTRY


# ── Helpers ──────────────────────────────────────────────

def _get_total_parts(doc_name: str) -> int | None:
    """Read total_parts from manifest for a registered doc. Returns None if data missing."""
    config = DOC_REGISTRY.get(doc_name)
    if not config:
        return None
    manifest_path = Path(config["chunks_dir"]) / "manifest.json"
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)["total_parts"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


# Only test docs that have actual data files on disk
_DOCS_WITH_DATA = [
    name for name in DOC_REGISTRY
    if _get_total_parts(name) is not None
]

# Strategy: registered doc names that have data files
doc_with_data_st = st.sampled_from(_DOCS_WITH_DATA)


# ── Property 3: 结构工具返回完整响应 ────────────────────
# Feature: agentic-rag-retrieval-generation, Property 3: 结构工具返回完整响应
# 验证: 需求 2.1, 2.2


@given(data=st.data())
@settings(max_examples=100)
def test_valid_call_returns_complete_response(data):
    """对任意有效 (doc_name, part)，返回 structure(列表)、next_steps(字符串)、pagination。"""
    doc_name = data.draw(doc_with_data_st)
    total_parts = _get_total_parts(doc_name)
    part = data.draw(st.integers(min_value=1, max_value=total_parts))

    result = get_document_structure(doc_name, part=part)

    # No error
    assert "error" not in result, f"Unexpected error: {result}"

    # structure is a list
    assert "structure" in result
    assert isinstance(result["structure"], list)

    # next_steps is a non-empty string
    assert "next_steps" in result
    assert isinstance(result["next_steps"], str)
    assert len(result["next_steps"]) > 0

    # pagination contains current_part and total_parts
    assert "pagination" in result
    pagination = result["pagination"]
    assert "current_part" in pagination
    assert "total_parts" in pagination
    assert pagination["current_part"] == part
    assert pagination["total_parts"] == total_parts


# ── Property 4: 首次结构调用附加文档信息 ────────────────
# Feature: agentic-rag-retrieval-generation, Property 4: 首次结构调用附加文档信息
# 验证: 需求 2.3


@given(doc_name=doc_with_data_st)
@settings(max_examples=100)
def test_part_one_includes_doc_info(doc_name: str):
    """part=1 时返回 doc_info 字段，含 total_pages 和 total_parts（正整数）。"""
    result = get_document_structure(doc_name, part=1)

    assert "error" not in result
    assert "doc_info" in result, "part=1 should include doc_info"

    doc_info = result["doc_info"]
    assert "total_pages" in doc_info
    assert isinstance(doc_info["total_pages"], int)
    assert doc_info["total_pages"] > 0

    assert "total_parts" in doc_info
    assert isinstance(doc_info["total_parts"], int)
    assert doc_info["total_parts"] > 0


# ── Property 5: 结构工具越界返回错误与有效范围 ──────────
# Feature: agentic-rag-retrieval-generation, Property 5: 结构工具越界返回错误与有效范围
# 验证: 需求 2.4


@given(data=st.data())
@settings(max_examples=100)
def test_out_of_range_part_returns_error(data):
    """越界 part (< 1 或 > total_parts) 返回 error 和 valid_range。"""
    doc_name = data.draw(doc_with_data_st)
    total_parts = _get_total_parts(doc_name)

    # Generate an out-of-range part: either < 1 or > total_parts
    part = data.draw(
        st.one_of(
            st.integers(max_value=0),
            st.integers(min_value=total_parts + 1, max_value=total_parts + 1000),
        )
    )

    result = get_document_structure(doc_name, part=part)

    assert "error" in result, f"Expected error for part={part}, got: {result}"
    assert isinstance(result["error"], str)

    # Should include valid range info
    assert "valid_range" in result, f"Expected valid_range in error response, got: {result}"
    assert str(total_parts) in result["valid_range"]


# ── Property 6: 结构工具默认 part 等价于 part=1 ─────────
# Feature: agentic-rag-retrieval-generation, Property 6: 结构工具默认 part 等价于 part=1
# 验证: 需求 2.5


@given(doc_name=doc_with_data_st)
@settings(max_examples=100)
def test_default_part_equals_part_one(doc_name: str):
    """无 part 参数与 part=1 返回相同结果。"""
    result_default = get_document_structure(doc_name)
    result_explicit = get_document_structure(doc_name, part=1)

    assert result_default == result_explicit, (
        f"Default part result differs from part=1:\n"
        f"  default: {list(result_default.keys())}\n"
        f"  part=1:  {list(result_explicit.keys())}"
    )
