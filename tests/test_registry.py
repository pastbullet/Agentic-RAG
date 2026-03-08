"""注册表属性测试。

# Feature: agentic-rag-retrieval-generation, Property 1: 注册表查找返回完整配置
对任意已注册 doc_name，验证返回包含 chunks_dir（字符串）、content_dir（字符串）、total_pages（正整数）。
验证: 需求 1.1, 1.2
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from src.tools.registry import DOC_REGISTRY, get_doc_config


# ── Strategy: 从已注册文档名中采样 ────────────────────────

registered_doc_name_st = st.sampled_from(list(DOC_REGISTRY.keys()))


# ── Property 1: 注册表查找返回完整配置 ────────────────────


@given(doc_name=registered_doc_name_st)
@settings(max_examples=100)
def test_registered_doc_returns_complete_config(doc_name: str):
    """对任意已注册 doc_name，get_doc_config 返回包含 chunks_dir、content_dir、total_pages 的配置。"""
    config = get_doc_config(doc_name)

    # 不应包含 error 字段
    assert "error" not in config

    # chunks_dir 是非空字符串
    assert "chunks_dir" in config
    assert isinstance(config["chunks_dir"], str)
    assert len(config["chunks_dir"]) > 0

    # content_dir 是非空字符串
    assert "content_dir" in config
    assert isinstance(config["content_dir"], str)
    assert len(config["content_dir"]) > 0

    # total_pages 是正整数
    assert "total_pages" in config
    assert isinstance(config["total_pages"], int)
    assert config["total_pages"] > 0

# ── Strategy: 生成不在注册表中的任意字符串 ────────────────

unregistered_doc_name_st = st.text(min_size=1).filter(
    lambda s: s not in DOC_REGISTRY
)


# ── Property 2: 未注册文档名返回可用列表 ──────────────────
# Feature: agentic-rag-retrieval-generation, Property 2: 未注册文档名返回可用列表
# 验证: 需求 1.3


@given(doc_name=unregistered_doc_name_st)
@settings(max_examples=100)
def test_unregistered_doc_returns_error_with_available_list(doc_name: str):
    """对任意不在注册表中的字符串，验证返回包含 error 字段且错误信息包含所有已注册文档名。"""
    result = get_doc_config(doc_name)

    # 应包含 error 字段
    assert "error" in result
    assert isinstance(result["error"], str)

    # 错误信息应包含所有已注册文档名
    for registered_name in DOC_REGISTRY:
        assert registered_name in result["error"], (
            f"Error message should contain registered doc '{registered_name}', "
            f"got: {result['error']}"
        )

