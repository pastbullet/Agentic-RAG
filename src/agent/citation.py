"""Citation 模块 — 引用提取、验证与清理。

从 LLM 生成的答案中解析 <cite doc="..." page="N"/> 标签，
验证引用页码是否在实际检索列表中，以及去除标签返回纯文本。
"""

from __future__ import annotations

import re

from src.models import Citation

# 匹配 <cite doc="..." page="N"/> 标签的正则
_CITE_PATTERN = re.compile(r'<cite\s+doc="([^"]+)"\s+page="(\d+)"\s*/>')


def extract_citations(answer: str) -> list[Citation]:
    """从答案文本中解析所有 ``<cite doc="..." page="..."/>`` 标签。

    对每个匹配，提取标签前约 50 个字符作为 context 片段。

    Returns:
        Citation 列表，每个包含 doc_name、page 和 context。
    """
    citations: list[Citation] = []
    for match in _CITE_PATTERN.finditer(answer):
        doc_name = match.group(1)
        page = int(match.group(2))
        # 提取标签前 ~50 字符作为上下文
        start = max(0, match.start() - 50)
        context = answer[start : match.start()].strip()
        citations.append(Citation(doc_name=doc_name, page=page, context=context))
    return citations


def validate_citations(
    citations: list[Citation],
    pages_retrieved: list[int],
) -> list[str]:
    """验证每个引用的页码是否在实际检索过的页码列表中。

    Returns:
        警告字符串列表。页码在检索列表中的引用不产生警告。
    """
    retrieved_set = set(pages_retrieved)
    warnings: list[str] = []
    for c in citations:
        if c.page not in retrieved_set:
            warnings.append(f"引用了未检索的页面: page {c.page}")
    return warnings


def clean_answer(answer: str) -> str:
    """去除答案中所有 ``<cite ... />`` 标签，返回纯文本。"""
    return _CITE_PATTERN.sub("", answer)
