"""数据模型定义 — 所有模块的共享数据结构。

本模块不依赖 tools/ 或 agent/ 模块。
"""

from __future__ import annotations

from pydantic import BaseModel


# ── 核心模型 ──────────────────────────────────────────────


class Citation(BaseModel):
    """答案中的页码引用。"""

    doc_name: str
    page: int
    context: str = ""


class ToolCallRecord(BaseModel):
    """单次 tool call 的记录。"""

    turn: int
    tool: str
    arguments: dict
    result_summary: str = ""


class TokenUsage(BaseModel):
    """Token 用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0


class ToolCall(BaseModel):
    """统一的 tool call 表示。"""

    name: str
    arguments: dict
    id: str


class LLMResponse(BaseModel):
    """LLM Adapter 的统一响应。"""

    has_tool_calls: bool = False
    tool_calls: list[ToolCall] = []
    text: str | None = None
    usage: TokenUsage = TokenUsage()
    raw_message: dict = {}


class RAGResponse(BaseModel):
    """Agent 循环的最终输出。"""

    answer: str
    answer_clean: str = ""
    citations: list[Citation] = []
    trace: list[ToolCallRecord] = []
    pages_retrieved: list[int] = []
    total_turns: int = 0


# ── 评测模型 ──────────────────────────────────────────────


class TestCase(BaseModel):
    """评测测试用例。"""

    id: str
    doc_name: str
    query: str
    type: str
    expected_pages: list[int] = []
    key_points: list[str] = []


class EvalResult(BaseModel):
    """单个测试用例的评测结果。"""

    id: str
    query: str
    key_points_covered: int
    key_points_total: int
    citation_count: int
    citation_valid_rate: float
    total_turns: int
    pages_hit_rate: float
    answer: str


# ── 扩展预留模型 (Phase 4) ────────────────────────────────


class ProtocolState(BaseModel):
    name: str
    description: str = ""
    is_initial: bool = False
    is_final: bool = False


class ProtocolTransition(BaseModel):
    from_state: str
    to_state: str
    event: str
    condition: str = ""
    actions: list[str] = []


class ProtocolStateMachine(BaseModel):
    name: str
    states: list[ProtocolState] = []
    transitions: list[ProtocolTransition] = []
    source_pages: list[int] = []


class ProtocolField(BaseModel):
    name: str
    type: str = ""
    size_bits: int | None = None
    description: str = ""


class ProtocolMessage(BaseModel):
    name: str
    fields: list[ProtocolField] = []
    source_pages: list[int] = []


class ProtocolSchema(BaseModel):
    """协议的结构化表示 — 代码生成的输入。"""

    protocol_name: str
    state_machines: list[ProtocolStateMachine] = []
    messages: list[ProtocolMessage] = []
    constants: dict = {}
    source_document: str = ""
