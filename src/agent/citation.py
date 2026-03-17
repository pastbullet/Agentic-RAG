"""Citation 模块 — 引用提取、验证与清理。

从 LLM 生成的答案中解析 <cite doc="..." page="N"/> 标签，
验证引用页码是否在实际检索列表中，以及去除标签返回纯文本。
"""

from __future__ import annotations

import re

from src.models import Citation

# 匹配 <cite doc="..." page="N"/> 标签的正则
_CITE_PATTERN = re.compile(r'<cite\s+doc="([^"]+)"\s+page="(\d+)"\s*/>')


def _extract_sentence_context(answer: str, cite_start: int, max_len: int = 200) -> str:
    """Extract the sentence or clause that a citation supports.

    Walks backward from *cite_start* to find a sentence boundary
    (period / newline / list marker), then forward to the cite tag.
    Falls back to the last *max_len* characters if no boundary is found.
    Strips any embedded ``<cite …/>`` tags from the extracted text so
    that evidence content is clean prose, not markup fragments.
    """
    # Search backward for a sentence boundary
    search_start = max(0, cite_start - 500)
    region = answer[search_start:cite_start]

    # Find the last sentence-ending boundary
    best = -1
    for pattern in (r"\n", r"。", r"\.\s", r"；", r";\s", r"- \*\*"):
        for m in re.finditer(pattern, region):
            if m.end() > best:
                best = m.end()

    if best >= 0:
        raw = region[best:].strip()
    else:
        raw = region[-max_len:].strip()

    # Truncate if still too long
    if len(raw) > max_len:
        raw = raw[-max_len:]

    # Remove any embedded cite tags so evidence is clean text
    clean = _CITE_PATTERN.sub("", raw).strip()
    return clean


def extract_citations(answer: str) -> list[Citation]:
    """从答案文本中解析所有 ``<cite doc="..." page="..."/>`` 标签。

    对每个匹配，提取标签所支持的语句作为 context 片段（去除内嵌 cite 标签）。

    Returns:
        Citation 列表，每个包含 doc_name、page 和 context。
    """
    citations: list[Citation] = []
    for match in _CITE_PATTERN.finditer(answer):
        doc_name = match.group(1)
        page = int(match.group(2))
        context = _extract_sentence_context(answer, match.start())
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
