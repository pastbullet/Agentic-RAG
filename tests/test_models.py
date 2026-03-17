"""数据模型属性测试。

# Feature: agentic-rag-retrieval-generation, Property 20: 数据模型字段完整性
验证 RAGResponse、Citation、ToolCallRecord 包含设计文档中定义的所有字段。
验证: 需求 11.1, 11.2, 11.3
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from src.models import Citation, RAGResponse, TestCase, ToolCallRecord


# ── Strategies ────────────────────────────────────────────

citation_st = st.builds(
    Citation,
    doc_name=st.text(min_size=1, max_size=50),
    page=st.integers(min_value=1, max_value=9999),
    context=st.text(max_size=200),
)

tool_call_record_st = st.builds(
    ToolCallRecord,
    turn=st.integers(min_value=0, max_value=100),
    tool=st.text(min_size=1, max_size=50),
    arguments=st.fixed_dictionaries({}),
    result_summary=st.text(max_size=200),
)

rag_response_st = st.builds(
    RAGResponse,
    answer=st.text(min_size=1, max_size=500),
    answer_clean=st.text(max_size=500),
    citations=st.lists(citation_st, max_size=5),
    trace=st.lists(tool_call_record_st, max_size=5),
    pages_retrieved=st.lists(st.integers(min_value=1, max_value=9999), max_size=10),
    all_pages_requested=st.lists(st.integers(min_value=1, max_value=9999), max_size=20),
    total_turns=st.integers(min_value=0, max_value=100),
)


# ── Property 20: 数据模型字段完整性 ──────────────────────


@given(response=rag_response_st)
@settings(max_examples=100)
def test_rag_response_has_all_required_fields(response: RAGResponse):
    """RAGResponse 包含 answer、answer_clean、citations、trace、pages_retrieved、all_pages_requested、total_turns 字段。"""
    assert hasattr(response, "answer") and isinstance(response.answer, str)
    assert hasattr(response, "answer_clean") and isinstance(response.answer_clean, str)
    assert hasattr(response, "citations") and isinstance(response.citations, list)
    assert hasattr(response, "trace") and isinstance(response.trace, list)
    assert hasattr(response, "pages_retrieved") and isinstance(response.pages_retrieved, list)
    assert hasattr(response, "all_pages_requested") and isinstance(response.all_pages_requested, list)
    assert hasattr(response, "total_turns") and isinstance(response.total_turns, int)


@given(citation=citation_st)
@settings(max_examples=100)
def test_citation_has_all_required_fields(citation: Citation):
    """Citation 包含 doc_name、page、context 字段。"""
    assert hasattr(citation, "doc_name") and isinstance(citation.doc_name, str)
    assert hasattr(citation, "page") and isinstance(citation.page, int)
    assert hasattr(citation, "context") and isinstance(citation.context, str)


@given(record=tool_call_record_st)
@settings(max_examples=100)
def test_tool_call_record_has_all_required_fields(record: ToolCallRecord):
    """ToolCallRecord 包含 turn、tool、arguments、result_summary 字段。"""
    assert hasattr(record, "turn") and isinstance(record.turn, int)
    assert hasattr(record, "tool") and isinstance(record.tool, str)
    assert hasattr(record, "arguments") and isinstance(record.arguments, dict)
    assert hasattr(record, "result_summary") and isinstance(record.result_summary, str)


# ── Property 19: 测试用例加载 round-trip ─────────────────

# Feature: agentic-rag-retrieval-generation, Property 19: 测试用例加载 round-trip

test_case_st = st.builds(
    TestCase,
    id=st.text(min_size=1, max_size=30),
    doc_name=st.text(min_size=1, max_size=50),
    query=st.text(min_size=1, max_size=200),
    type=st.sampled_from(["format", "state_machine", "procedure", "definition", "cross_reference"]),
    expected_pages=st.lists(st.integers(min_value=1, max_value=9999), max_size=10),
    key_points=st.lists(st.text(min_size=1, max_size=100), max_size=10),
)


@given(cases=st.lists(test_case_st, min_size=0, max_size=5))
@settings(max_examples=100)
def test_testcase_json_round_trip(cases: list[TestCase]):
    """TestCase 列表序列化为 JSON 后再加载回来，字段值完全一致。"""
    import json

    serialized = json.dumps([c.model_dump() for c in cases], ensure_ascii=False)
    loaded = [TestCase(**raw) for raw in json.loads(serialized)]

    assert len(loaded) == len(cases)
    for original, restored in zip(cases, loaded):
        assert restored.id == original.id
        assert restored.doc_name == original.doc_name
        assert restored.query == original.query
        assert restored.type == original.type
        assert restored.expected_pages == original.expected_pages
        assert restored.key_points == original.key_points
