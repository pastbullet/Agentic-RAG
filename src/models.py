"""数据模型定义 — 所有模块的共享数据结构。

本模块不依赖 tools/ 或 agent/ 模块。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    all_pages_requested: list[int] = []
    total_turns: int = 0
    context_session_id: str | None = None


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
    duplicate_read_rate: float = 0.0
    avg_pages_per_turn: float = 0.0
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


class ProcedureStep(BaseModel):
    step_number: int
    condition: str = ""
    action: str


class ProcedureRule(BaseModel):
    name: str
    steps: list[ProcedureStep] = []
    source_pages: list[int] = []


class TimerConfig(BaseModel):
    timer_name: str
    timeout_value: str = ""
    trigger_action: str = ""
    description: str = ""
    source_pages: list[int] = []


class ErrorRule(BaseModel):
    error_condition: str
    handling_action: str
    description: str = ""
    source_pages: list[int] = []


class ProtocolSchema(BaseModel):
    """协议的结构化表示 — 代码生成的输入。"""

    protocol_name: str
    state_machines: list[ProtocolStateMachine] = []
    messages: list[ProtocolMessage] = []
    procedures: list[ProcedureRule] = []
    timers: list[TimerConfig] = []
    errors: list[ErrorRule] = []
    constants: dict = {}
    source_document: str = ""


# ── 节点语义分类模型 ──────────────────────────────────────


NodeLabelType = Literal[
    "state_machine",       # 包含状态集合、状态转移、触发事件
    "message_format",      # 描述报文/帧/TLV/字段布局
    "procedure_rule",      # 描述处理流程/顺序步骤，但无完整状态结构
    "timer_rule",          # 描述超时/周期发送/保活/重传等时序机制
    "error_handling",      # 描述异常条件/非法值/丢弃/错误恢复
    "general_description", # 背景介绍/术语/设计动机/非规范性建议
]


class NodeSemanticLabel(BaseModel):
    """单个文档树叶节点的语义分类结果。"""

    node_id: str
    label: NodeLabelType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rationale: str = ""
    secondary_hints: list[str] = Field(default_factory=list)


class NodeLabelMeta(BaseModel):
    """分类运行记录 — 用于判断缓存是否失效。"""

    source_document: str
    model_name: str
    prompt_version: str
    label_priority: list[str]
    created_at: str
