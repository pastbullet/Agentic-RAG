"""Agent Loop 核心 — 纯转发模式，所有检索决策由 LLM 驱动。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

# ── Session log 目录 ─────────────────────────────────────
SESSION_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sessions"


def _save_session(query: str, doc_name: str, messages: list[dict], response: "RAGResponse", context_session_id: str | None = None) -> Path:
    """将完整的 messages 历史和最终答案保存到 data/sessions/<timestamp>.json。"""
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SESSION_LOG_DIR / f"{ts}.json"
    payload = {
        "timestamp": ts,
        "doc_name": doc_name,
        "query": query,
        "total_turns": response.total_turns,
        "pages_retrieved": response.pages_retrieved,
        "all_pages_requested": response.all_pages_requested,
        "answer": response.answer,
        "answer_clean": response.answer_clean,
        "citations": [c.model_dump() for c in response.citations],
        "trace": [t.model_dump() for t in response.trace],
        "messages": messages,
    }
    if context_session_id is not None:
        payload["context_session_id"] = context_session_id
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Session saved → {path}")
    return path

from src.models import RAGResponse, ToolCallRecord
from src.tools.document_structure import get_document_structure
from src.tools.page_content import get_page_content
from src.tools.schemas import get_tool_schemas
from src.agent.llm_adapter import LLMAdapter
from src.agent.citation import extract_citations, validate_citations, clean_answer
from src.context import ContextManager
from src.context.reuse import (
    ContextReuseBuilder,
    PRECHECK_GUIDANCE,
)
from src.context.reuse.config import resolve_config

load_dotenv()

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, Any]], None]

# prompts 目录路径
PROMPTS_DIR = Path(__file__).parent / "prompts"

# ── Tool 路由表 ──────────────────────────────────────────
# 新增工具只需在此字典中添加映射，无需修改循环核心逻辑 (Req 12.2)
TOOL_REGISTRY: dict[str, callable] = {
    "get_document_structure": get_document_structure,
    "get_page_content": get_page_content,
}


def load_system_prompt(prompt_file: str = "qa_system.txt") -> str:
    """从 src/agent/prompts/ 目录加载 system prompt 文件。

    Args:
        prompt_file: prompt 文件名，默认 "qa_system.txt"

    Returns:
        prompt 文本内容
    """
    path = PROMPTS_DIR / prompt_file
    return path.read_text(encoding="utf-8")


def execute_tool(name: str, arguments: dict) -> dict:
    """Tool 路由器 — 将 tool call 分发到对应函数。

    未知工具名返回 {"error": "Unknown tool: {name}"}，不抛异常。
    新增工具只需在 TOOL_REGISTRY 中添加映射即可 (Req 12.2)。

    Args:
        name: 工具名称
        arguments: 调用参数字典

    Returns:
        工具执行结果字典
    """
    func = TOOL_REGISTRY.get(name)
    if func is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return func(**arguments)
    except Exception as exc:
        return {"error": f"Tool execution failed: {exc}"}


def _make_result_summary(result: dict, max_len: int = 200) -> str:
    """将工具结果截断为简短摘要。"""
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# get_page_content 单页最大字符数（约 2000 token）
_PAGE_CONTENT_MAX_CHARS = 8000
# get_document_structure 最大字符数
_STRUCTURE_MAX_CHARS = 12000
_STREAM_RESULT_MAX_CHARS = 20000
_MAX_HISTORY_MESSAGES = 8
_MAX_HISTORY_CHARS = 4000

_TABLE_REF_PATTERN = re.compile(r"(?:\btable|表)\s*#?\s*(\d{1,4})", re.IGNORECASE)
_EXPLICIT_PAGE_PATTERN = re.compile(r"(?:第\s*\d+\s*页|\bpage\s*\d+\b)", re.IGNORECASE)
_FOLLOWUP_INVITE_PATTERN = re.compile(
    r"(?is)"
    r"(如果你|如需|需要的话|我可以继续|你还可以|你可以继续|欢迎继续提问|"
    r"if you (?:want|need|would like)|i can continue|feel free to ask|let me know if)"
)


def _truncate_tool_result(result: dict, tool_name: str) -> dict:
    """对 tool 结果做上下文友好的截断，防止 context 无限膨胀。

    - get_page_content：每页内容截断到 _PAGE_CONTENT_MAX_CHARS 字符
    - get_document_structure：整体截断到 _STRUCTURE_MAX_CHARS 字符
    - 其他工具：不截断
    """
    if tool_name == "get_page_content" and "content" in result:
        truncated_pages = []
        for page_item in result["content"]:
            text = page_item.get("text", "")
            if len(text) > _PAGE_CONTENT_MAX_CHARS:
                text = text[:_PAGE_CONTENT_MAX_CHARS] + "\n...[truncated]"
            truncated_pages.append({**page_item, "text": text})
        return {**result, "content": truncated_pages}

    if tool_name == "get_document_structure":
        raw = json.dumps(result, ensure_ascii=False)
        if len(raw) > _STRUCTURE_MAX_CHARS:
            # structure 截断后仍是合法 JSON（直接截字符串会破坏结构，改为截 children）
            # 简单策略：返回原始 dict 但标注已截断
            return {**result, "_truncated": True, "_note": f"Structure truncated from {len(raw)} chars"}
        return result

    return result


def _truncate_result_for_stream(result: dict) -> dict:
    """裁剪流式事件中的 tool 结果，避免 SSE 包过大。"""
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) <= _STREAM_RESULT_MAX_CHARS:
        return result

    content = result.get("content")
    if isinstance(content, list):
        preview_items: list[dict[str, Any]] = []
        for item in content[:2]:
            if not isinstance(item, dict):
                continue
            item_copy = dict(item)
            text = item_copy.get("text")
            if isinstance(text, str) and len(text) > 2000:
                item_copy["text"] = text[:2000] + "\n...[truncated for stream]"
            preview_items.append(item_copy)
        return {
            "_truncated_for_stream": True,
            "_original_chars": len(raw),
            "content": preview_items,
            "next_steps": result.get("next_steps", ""),
        }

    return {
        "_truncated_for_stream": True,
        "_original_chars": len(raw),
        "preview": raw[:_STREAM_RESULT_MAX_CHARS] + "...",
    }


def _normalize_history_messages(
    history_messages: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Normalize and clamp history messages for LLM input."""
    if not history_messages:
        return []

    normalized: list[dict[str, str]] = []
    for item in history_messages[-_MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        if len(text) > _MAX_HISTORY_CHARS:
            text = text[:_MAX_HISTORY_CHARS] + "\n...[history truncated]"
        normalized.append({"role": role, "content": text})
    return normalized


def _strip_followup_invites(answer: str) -> str:
    """去掉答案结尾的“继续追问引导”段落，保持一次性总结风格。"""
    text = (answer or "").strip()
    if not text:
        return text

    parts = re.split(r"\n\s*\n", text)
    changed = False
    while parts:
        tail = parts[-1].strip()
        if not tail:
            parts.pop()
            changed = True
            continue
        if not _FOLLOWUP_INVITE_PATTERN.search(tail):
            break
        parts.pop()
        changed = True

    if not changed:
        return text

    cleaned = "\n\n".join(p for p in parts if p.strip()).strip()
    return cleaned or text


def _augment_query_with_disambiguation(query: str) -> str:
    """Add a lightweight hint when query mentions table IDs without explicit page intent."""
    if not _TABLE_REF_PATTERN.search(query):
        return query
    if _EXPLICIT_PAGE_PATTERN.search(query):
        return query
    return (
        f"{query}\n\n"
        "[Disambiguation: references like '表149' / 'Table 149' are table IDs, "
        "not page numbers, unless the user explicitly says '第149页' or 'page 149'. "
        "Locate the table first, then use its actual source page(s).]"
    )


def _detect_provider(model: str) -> str:
    """根据环境变量或模型名称推断 LLM provider。"""
    env_provider = os.getenv("PROTOCOL_TWIN_LLM_PROVIDER", "").strip().lower()
    if env_provider in ("openai", "anthropic"):
        return env_provider
    # 回退：根据模型名称推断
    if model and model.lower().startswith("claude"):
        return "anthropic"
    return "openai"


def _resolve_model(model: str | None) -> str:
    """解析最终使用的模型名称。"""
    if model:
        return model
    provider = _detect_provider("")
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL_NAME", "claude-sonnet-4-20250514")
    return os.getenv("OPENAI_MODEL_NAME", "gpt-4o")


def _extract_pages_from_result(result: dict) -> list[int]:
    """从 get_page_content 的返回结果中提取页码列表。"""
    pages: list[int] = []
    for item in result.get("content", []):
        page = item.get("page")
        if isinstance(page, int):
            pages.append(page)
    return pages


def _append_context_reuse_message(
    messages: list[dict],
    system_prompt: str,
    context_text: str,
) -> tuple[list[dict], str]:
    if not context_text:
        return messages, system_prompt

    updated_prompt = system_prompt.rstrip() + PRECHECK_GUIDANCE
    updated_messages = list(messages)
    updated_messages[0] = {"role": "system", "content": updated_prompt}
    updated_messages.insert(
        1,
        {
            "role": "system",
            "content": f"[Context from previous turns]\n{context_text}",
        },
    )
    return updated_messages, updated_prompt


async def agentic_rag(
    query: str,
    doc_name: str,
    model: str | None = None,
    max_turns: int = 15,
    prompt_file: str = "qa_system.txt",
    system_prompt_override: str | None = None,
    progress_callback: ProgressCallback | None = None,
    history_messages: list[dict[str, str]] | None = None,
    enable_context_reuse: bool | None = None,
    context_session_id: str | None = None,
    summary_char_budget: int | None = None,
    enable_page_dedup: bool | None = None,
) -> RAGResponse:
    """核心 Agent 循环。

    代码只做三件事：
    1. 组装 query + system_prompt + tools 发给 LLM
    2. LLM 返回 tool_call → 执行 tool → 结果喂回
    3. LLM 返回 text → 作为最终答案返回

    代码不做任何检索决策，所有导航完全由 LLM 通过 tool call 驱动 (Req 6.6)。
    切换 prompt_file 即可改变 LLM 行为模式 (Req 12.1)。

    Args:
        query: 用户问题
        doc_name: 文档名称
        model: LLM 模型名称（可选，默认从配置读取）
        max_turns: 最大轮次（安全阀），默认 15
        prompt_file: system prompt 文件名，默认 "qa_system.txt"

    Returns:
        RAGResponse 包含答案、trace、pages_retrieved 等
    """
    enable_context_reuse_resolved = resolve_config(
        enable_context_reuse,
        "CONTEXT_REUSE_ENABLED",
        True,
    )
    summary_char_budget_resolved = resolve_config(
        summary_char_budget,
        "CONTEXT_REUSE_CHAR_BUDGET",
        4000,
    )

    # 解析模型和 provider
    resolved_model = _resolve_model(model)
    provider = _detect_provider(resolved_model)

    # 初始化 LLM 适配器
    adapter = LLMAdapter(provider=provider, model=resolved_model)

    def emit(payload: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(payload)
        except Exception:
            logger.exception("progress_callback failed in agentic_rag")

    # ── Context Management Sidecar ──
    ctx: ContextManager | None = None
    ctx_session_id: str | None = None
    ctx_turn_id: str | None = None
    session_dir: Path | None = None
    try:
        ctx = ContextManager()
        if context_session_id:
            try:
                ctx_session_id = ctx.load_session(context_session_id)
            except Exception:
                logger.warning("Failed to load context session %s; creating a new one", context_session_id)
                ctx_session_id = ctx.create_session(doc_name)
        else:
            ctx_session_id = ctx.create_session(doc_name)
        session_dir = ctx.session_dir
        ctx_turn_id = ctx.create_turn(query, doc_name)
    except Exception:
        logger.exception("Context manager initialization failed")
        ctx = None
        ctx_session_id = None
        ctx_turn_id = None
        session_dir = None

    # 加载 system prompt 和 tool schemas
    override = (system_prompt_override or "").strip()
    if override:
        system_prompt = override
    else:
        system_prompt = load_system_prompt(prompt_file)
    tools = get_tool_schemas()
    normalized_history = _normalize_history_messages(history_messages)
    query_for_llm = _augment_query_with_disambiguation(query)

    base_messages: list[dict] = [{"role": "system", "content": system_prompt}]
    base_messages.extend(normalized_history)
    base_messages.append(
        {
            "role": "user",
            "content": (
                f"Target document: {doc_name}\n"
                f"User question: {query_for_llm}"
            ),
        }
    )

    messages = base_messages
    if enable_context_reuse_resolved and session_dir is not None:
        try:
            builder = ContextReuseBuilder(session_dir, summary_char_budget_resolved)
            context_text = builder.build_summary(doc_name, query=query)
            if context_text:
                messages, system_prompt = _append_context_reuse_message(
                    base_messages,
                    system_prompt,
                    context_text,
                )
        except Exception as exc:
            logger.exception("Context reuse build failed")
            emit(
                {
                    "type": "context_reuse_error",
                    "stage": "build_summary",
                    "message": str(exc),
                    "doc_name": doc_name,
                }
            )

    trace: list[ToolCallRecord] = []
    pages_retrieved: list[int] = []
    all_pages_requested: list[int] = []
    turn = 0

    # Agent 循环
    while turn < max_turns:
        turn += 1
        logger.info(f"Turn {turn}/{max_turns}")
        emit(
            {
                "type": "turn_start",
                "turn": turn,
                "max_turns": max_turns,
                "doc_name": doc_name,
            }
        )

        try:
            # 调用 LLM
            response = await adapter.chat_with_tools(messages, tools)
        except Exception as exc:
            emit(
                {
                    "type": "error",
                    "stage": "qa",
                    "message": str(exc),
                    "doc_name": doc_name,
                    "turn": turn,
                }
            )
            raise

        if response.has_tool_calls:
            # LLM 返回 tool_call → 执行工具并追加结果 (Req 6.2)
            # 先追加 assistant 的 raw_message（含 tool_calls）
            messages.append(response.raw_message)
            note = (response.text or "").strip()
            if note:
                emit(
                    {
                        "type": "assistant_note",
                        "turn": turn,
                        "content": note,
                    }
                )

            # 支持并行 tool call (Req 5.7)
            for tc in response.tool_calls:
                try:
                    result = execute_tool(tc.name, tc.arguments)
                except Exception as exc:
                    emit(
                        {
                            "type": "error",
                            "stage": "tool_execution",
                            "message": str(exc),
                            "doc_name": doc_name,
                            "turn": turn,
                        }
                    )
                    logger.exception("Tool execution failed")
                    result = {"error": str(exc)}
                stream_result = _truncate_result_for_stream(result)
                result_summary = _make_result_summary(result)

                # 追踪检索的页码
                if tc.name == "get_page_content":
                    requested_pages = _extract_pages_from_result(result)
                    pages_retrieved.extend(requested_pages)
                    all_pages_requested.extend(requested_pages)

                # 记录 ToolCallRecord (Req 6.4)
                trace.append(
                    ToolCallRecord(
                        turn=turn,
                        tool=tc.name,
                        arguments=tc.arguments,
                        result_summary=result_summary,
                    )
                )
                emit(
                    {
                        "type": "tool_call",
                        "turn": turn,
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "result_summary": result_summary,
                        "result": stream_result,
                    }
                )

                # Context sidecar: record tool call
                if ctx is not None:
                    try:
                        ctx.record_tool_call(ctx_turn_id, tc.name, tc.arguments, result, doc_id=doc_name)
                    except Exception:
                        logger.exception("Context manager record_tool_call failed")

                # 构造 tool result 消息并追加
                # 截断过长的 tool 结果，避免 context 膨胀（get_page_content 结果可能很大）
                result_for_msg = _truncate_tool_result(result, tc.name)
                tool_msg = adapter.make_tool_result_message(tc.id, result_for_msg)
                messages.append(tool_msg)
        else:
            # LLM 返回纯文本 → 最终答案 (Req 6.3)
            answer = _strip_followup_invites(response.text or "")
            # 去重 pages_retrieved
            unique_pages = sorted(set(pages_retrieved))

            # 引用处理 (Req 8.4, 8.5, 8.6)
            citations = extract_citations(answer)
            answer_clean = clean_answer(answer)
            warnings = validate_citations(citations, unique_pages)
            for w in warnings:
                logger.warning(w)

            rag_response = RAGResponse(
                answer=answer,
                answer_clean=answer_clean,
                citations=citations,
                trace=trace,
                pages_retrieved=unique_pages,
                all_pages_requested=all_pages_requested,
                total_turns=turn,
                context_session_id=ctx_session_id,
            )
            emit(
                {
                    "type": "final_answer",
                    "doc_name": doc_name,
                    "answer": rag_response.answer,
                    "answer_clean": rag_response.answer_clean,
                    "citations": [c.model_dump() for c in rag_response.citations],
                    "trace": [t.model_dump() for t in rag_response.trace],
                    "pages_retrieved": rag_response.pages_retrieved,
                    "total_turns": rag_response.total_turns,
                    "context_session_id": rag_response.context_session_id,
                }
            )
            # Context sidecar: finalize
            if ctx is not None:
                try:
                    answer_payload = {"answer": rag_response.answer, "citations": [c.model_dump() for c in rag_response.citations]}
                    ctx.finalize_turn(ctx_turn_id, answer_payload)
                    ctx.finalize_session()
                except Exception:
                    logger.exception("Context manager finalize failed")
            _save_session(query, doc_name, messages, rag_response, context_session_id=ctx_session_id)
            return rag_response

    # 达到 max_turns 限制 (Req 6.5)
    answer = "[达到最大轮次限制]"
    unique_pages = sorted(set(pages_retrieved))

    # 引用处理 (Req 8.4, 8.5, 8.6)
    citations = extract_citations(answer)
    answer_clean = clean_answer(answer)
    warnings = validate_citations(citations, unique_pages)
    for w in warnings:
        logger.warning(w)

    rag_response = RAGResponse(
        answer=answer,
        answer_clean=answer_clean,
        citations=citations,
        trace=trace,
        pages_retrieved=unique_pages,
        all_pages_requested=all_pages_requested,
        total_turns=turn,
        context_session_id=ctx_session_id,
    )
    emit(
        {
            "type": "final_answer",
            "doc_name": doc_name,
            "answer": rag_response.answer,
            "answer_clean": rag_response.answer_clean,
            "citations": [c.model_dump() for c in rag_response.citations],
            "trace": [t.model_dump() for t in rag_response.trace],
            "pages_retrieved": rag_response.pages_retrieved,
            "total_turns": rag_response.total_turns,
            "context_session_id": rag_response.context_session_id,
        }
    )
    # Context sidecar: finalize
    if ctx is not None:
        try:
            answer_payload = {"answer": rag_response.answer, "citations": [c.model_dump() for c in rag_response.citations]}
            ctx.finalize_turn(ctx_turn_id, answer_payload)
            ctx.finalize_session()
        except Exception:
            logger.exception("Context manager finalize failed")
    _save_session(query, doc_name, messages, rag_response, context_session_id=ctx_session_id)
    return rag_response
