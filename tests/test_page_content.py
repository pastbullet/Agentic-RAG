"""页面内容工具属性测试 (Property 7-12)。

# Feature: agentic-rag-retrieval-generation, Property 7-12: 页面内容工具正确性
验证: 需求 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.tools.page_content import parse_pages, get_page_content, _truncate_text
from src.tools.registry import DOC_REGISTRY


# ── Helpers ──────────────────────────────────────────────

# Only test docs whose content_dir actually exists on disk
import os as _os

_DOCS_WITH_CONTENT = [
    name for name, cfg in DOC_REGISTRY.items()
    if _os.path.isdir(cfg["content_dir"])
]

doc_with_content_st = st.sampled_from(_DOCS_WITH_CONTENT) if _DOCS_WITH_CONTENT else st.nothing()


# ── Property 7: 页码解析三种格式正确性 ──────────────────
# Feature: agentic-rag-retrieval-generation, Property 7: 页码解析三种格式正确性
# 验证: 需求 3.2


@given(n=st.integers(min_value=1, max_value=9999))
@settings(max_examples=100)
def test_single_page_format(n: int):
    """单页格式 str(n) 返回 [n]，且元素为正整数。"""
    result = parse_pages(str(n))
    assert result == [n]
    assert all(isinstance(p, int) and p > 0 for p in result)


@given(data=st.data())
@settings(max_examples=100)
def test_range_format(data):
    """范围格式 'a-b' (a <= b) 返回 [a, a+1, ..., b]，且元素均为正整数。"""
    a = data.draw(st.integers(min_value=1, max_value=500))
    b = data.draw(st.integers(min_value=a, max_value=a + 50))

    result = parse_pages(f"{a}-{b}")
    expected = list(range(a, b + 1))
    assert result == expected
    assert all(isinstance(p, int) and p > 0 for p in result)


@given(data=st.data())
@settings(max_examples=100)
def test_comma_separated_format(data):
    """逗号分隔格式返回排序后的页码列表，且元素均为正整数。"""
    pages = data.draw(
        st.lists(st.integers(min_value=1, max_value=9999), min_size=1, max_size=10, unique=True)
    )
    pages_str = ",".join(str(p) for p in pages)

    result = parse_pages(pages_str)
    assert result == sorted(pages)
    assert all(isinstance(p, int) and p > 0 for p in result)



# ── Property 8: 内容工具返回完整页面字段 ────────────────
# Feature: agentic-rag-retrieval-generation, Property 8: 内容工具返回完整页面字段
# 验证: 需求 3.1, 3.3


@given(data=st.data())
@settings(max_examples=100)
def test_content_returns_complete_page_fields(data):
    """对任意有效 (doc_name, pages) 请求（页数 ≤ 10），每个页面包含 page、text、tables、images。"""
    doc_name = data.draw(doc_with_content_st)
    total_pages = DOC_REGISTRY[doc_name]["total_pages"]

    # Draw a small valid page range (1-10 pages)
    start = data.draw(st.integers(min_value=1, max_value=total_pages))
    count = data.draw(st.integers(min_value=1, max_value=min(10, total_pages - start + 1)))
    end = start + count - 1
    pages_str = f"{start}-{end}" if count > 1 else str(start)

    result = get_page_content(doc_name, pages_str)

    assert "error" not in result, f"Unexpected error: {result}"
    assert "content" in result
    assert isinstance(result["content"], list)
    assert len(result["content"]) == count

    for page_entry in result["content"]:
        assert "page" in page_entry
        assert isinstance(page_entry["page"], int)
        assert "text" in page_entry
        assert isinstance(page_entry["text"], str)
        assert "tables" in page_entry
        assert isinstance(page_entry["tables"], list)
        assert "images" in page_entry
        assert isinstance(page_entry["images"], list)


# ── Property 9: 内容工具拒绝超过 10 页的请求 ────────────
# Feature: agentic-rag-retrieval-generation, Property 9: 内容工具拒绝超过 10 页的请求
# 验证: 需求 3.4


@given(data=st.data())
@settings(max_examples=100)
def test_content_rejects_more_than_10_pages(data):
    """超过 10 页的请求返回 error 字典，不返回 content。"""
    doc_name = data.draw(doc_with_content_st)
    total_pages = DOC_REGISTRY[doc_name]["total_pages"]

    # Generate a range of 11+ pages within valid bounds
    max_count = min(total_pages, 50)
    assume(max_count >= 11)
    count = data.draw(st.integers(min_value=11, max_value=max_count))
    start = data.draw(st.integers(min_value=1, max_value=total_pages - count + 1))
    end = start + count - 1
    pages_str = f"{start}-{end}"

    result = get_page_content(doc_name, pages_str)

    assert "error" in result, f"Expected error for {count} pages, got: {result}"
    assert "content" not in result


# ── Property 10: 内容截断策略 ───────────────────────────
# Feature: agentic-rag-retrieval-generation, Property 10: 内容截断策略
# 验证: 需求 3.5, 3.6


@given(
    extra_len=st.integers(min_value=500, max_value=6000),
)
@settings(max_examples=100)
def test_truncation_respects_limit_and_annotates(extra_len: int):
    """文本 > 4000 字符时截断至 ≤ 4000 字符附近并含截断标注。"""
    # Construct text guaranteed to exceed 4000 chars with paragraph breaks
    paragraph = "A" * 100
    num_paragraphs = (4000 + extra_len) // 102 + 1  # 100 chars + "\n\n"
    text = "\n\n".join([paragraph] * num_paragraphs)
    assert len(text) > 4000  # sanity check

    result = _truncate_text(text)
    assert "内容已截断" in result
    annotation_marker = "\n\n[内容已截断"
    idx = result.find(annotation_marker)
    assert idx != -1, "Truncation annotation not found"
    text_portion = result[:idx]
    assert len(text_portion) <= 4000


@given(text=st.text(min_size=0, max_size=4000))
@settings(max_examples=100)
def test_short_text_not_truncated(text: str):
    """文本 ≤ 4000 字符时不截断，原样返回。"""
    result = _truncate_text(text)
    assert result == text


@given(data=st.data())
@settings(max_examples=100)
def test_tables_always_complete(data):
    """表格始终完整返回，不受截断影响。"""
    doc_name = data.draw(doc_with_content_st)
    total_pages = DOC_REGISTRY[doc_name]["total_pages"]
    page_num = data.draw(st.integers(min_value=1, max_value=total_pages))

    result = get_page_content(doc_name, str(page_num))
    if "error" in result:
        return  # skip if page data not found

    for page_entry in result["content"]:
        # tables field should be a list and never contain truncation markers
        tables = page_entry["tables"]
        assert isinstance(tables, list)
        for table in tables:
            assert "内容已截断" not in str(table)


# ── Property 11: 内容工具 next_steps 包含引用格式提示 ───
# Feature: agentic-rag-retrieval-generation, Property 11: 内容工具 next_steps 包含引用格式提示
# 验证: 需求 3.7


@given(data=st.data())
@settings(max_examples=100)
def test_next_steps_contains_cite_hint(data):
    """成功的 get_page_content 调用，next_steps 包含 <cite 关键词。"""
    doc_name = data.draw(doc_with_content_st)
    total_pages = DOC_REGISTRY[doc_name]["total_pages"]
    page_num = data.draw(st.integers(min_value=1, max_value=total_pages))

    result = get_page_content(doc_name, str(page_num))

    assert "error" not in result, f"Unexpected error: {result}"
    assert "next_steps" in result
    assert isinstance(result["next_steps"], str)
    assert "<cite" in result["next_steps"]


# ── Property 12: 内容工具越界页码返回错误 ────────────────
# Feature: agentic-rag-retrieval-generation, Property 12: 内容工具越界页码返回错误
# 验证: 需求 3.8


@given(data=st.data())
@settings(max_examples=100)
def test_out_of_range_pages_returns_error(data):
    """越界页码返回 error 和 valid_range。"""
    doc_name = data.draw(doc_with_content_st)
    total_pages = DOC_REGISTRY[doc_name]["total_pages"]

    # Generate a page number beyond the document's total pages
    page = data.draw(
        st.integers(min_value=total_pages + 1, max_value=total_pages + 1000)
    )

    result = get_page_content(doc_name, str(page))

    assert "error" in result, f"Expected error for page={page}, got: {result}"
    assert "valid_range" in result
    assert str(total_pages) in result["valid_range"]
