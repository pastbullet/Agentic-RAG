"""数据模型定义 — 所有模块的共享数据结构。

本模块不依赖 tools/ 或 agent/ 模块。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from src.extract.option_tlv_models import OptionListIR


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
    archetype_contribution: dict | None = None


class NormalizationStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    DEGRADED_READY = "degraded_ready"
    BLOCKED = "blocked"


class IRDiagnostic(BaseModel):
    level: Literal["warning", "error"]
    code: str
    message: str
    source_pages: list[int] = []
    source_node_ids: list[str] = []


class EnumValue(BaseModel):
    value: int
    name: str
    description: str | None = None


class EnumDomain(BaseModel):
    enum_id: str
    field_name: str
    values: list[EnumValue] = []


class PresenceRule(BaseModel):
    rule_id: str
    target_kind: str
    target_id: str
    expression: str
    depends_on_fields: list[str] = []
    description: str | None = None


class ValidationRule(BaseModel):
    rule_id: str
    target_kind: str | None = None
    target_id: str | None = None
    kind: str
    expression: str
    severity: Literal["warning", "error"] = "error"
    depends_on_fields: list[str] = []
    description: str | None = None


class CodegenHints(BaseModel):
    preferred_template: str
    generate_pack: bool = True
    generate_unpack: bool = True
    generate_validate: bool = True
    runtime_helpers: list[str] = []


class SectionIR(BaseModel):
    section_id: str
    name: str
    canonical_name: str
    kind: str
    parent_section_id: str | None = None
    declared_bit_offset: int | None = None
    declared_byte_offset: int | None = None
    declared_bit_width: int | None = None
    resolved_bit_offset: int | None = None
    resolved_byte_offset: int | None = None
    resolved_bit_width: int | None = None
    optional: bool = False
    presence_rule_ids: list[str] = []
    field_ids: list[str] = []
    option_list_id: str | None = None
    source_pages: list[int] = []


class CompositeDispatchCaseIR(BaseModel):
    case_id: str
    selector_values: list[int] = []
    message_ir_id: str
    description: str | None = None


class CompositeTailIR(BaseModel):
    slot_id: str
    section_id: str
    name: str
    tail_kind: str = "message_family"
    optional: bool = False
    presence_rule_id: str | None = None
    selector_field: str | None = None
    total_length_field: str | None = None
    span_expression: str | None = None
    fixed_prefix_bits: int | None = None
    start_bit_offset: int | None = None
    min_span_bits: int | None = None
    max_span_bits: int | None = None
    max_span_bytes: int | None = None
    fallback_mode: str | None = None
    option_list_id: str | None = None
    candidate_message_irs: list[str] = []
    dispatch_cases: list[CompositeDispatchCaseIR] = []


class FieldIR(BaseModel):
    field_id: str
    name: str
    canonical_name: str
    declared_bit_width: int | None = None
    declared_bit_offset: int | None = None
    declared_byte_offset: int | None = None
    resolved_bit_width: int | None = None
    resolved_bit_offset: int | None = None
    resolved_byte_offset: int | None = None
    storage_type: str | None = None
    signed: bool = False
    endianness: str | None = None
    is_bitfield: bool = False
    bit_lsb_index: int | None = None
    bit_msb_index: int | None = None
    is_array: bool = False
    array_len: int | None = None
    is_variable_length: bool = False
    length_from_field: str | None = None
    optional: bool = False
    presence_rule_ids: list[str] = []
    const_value: int | str | None = None
    enum_domain_id: str | None = None
    description: str | None = None
    source_pages: list[int] = []
    source_node_ids: list[str] = []


class MessageIR(BaseModel):
    ir_id: str
    protocol_name: str
    canonical_name: str
    display_name: str
    source_message_names: list[str] = []
    source_pages: list[int] = []
    source_node_ids: list[str] = []
    layout_kind: str = ""
    total_size_bits: int | None = None
    total_size_bytes: int | None = None
    min_size_bits: int | None = None
    max_size_bits: int | None = None
    sections: list[SectionIR] = []
    composite_tails: list[CompositeTailIR] = []
    option_lists: list[OptionListIR] = []
    fields: list[FieldIR] = []
    normalized_field_order: list[str] = []
    presence_rules: list[PresenceRule] = []
    validation_rules: list[ValidationRule] = []
    enum_domains: list[EnumDomain] = []
    codegen_hints: CodegenHints = CodegenHints(preferred_template="message_ir_v1")
    diagnostics: list[IRDiagnostic] = []
    normalization_status: NormalizationStatus = NormalizationStatus.DRAFT


class ContextFieldIR(BaseModel):
    field_id: str
    name: str
    canonical_name: str
    type_kind: str = "opaque"
    width_bits: int | None = None
    semantic_role: str | None = None
    initial_value_kind: str | None = None
    initial_value_expr: str | None = None
    read_only: bool = False
    optional: bool = False
    read_by: list[str] = []
    written_by: list[str] = []
    diagnostics: list[IRDiagnostic] = []


class ContextTimerIR(BaseModel):
    timer_id: str
    name: str
    canonical_name: str
    semantic_role: str | None = None
    duration_source_kind: str | None = None
    duration_expr: str | None = None
    triggers_event: str | None = None
    start_actions: list[str] = []
    cancel_actions: list[str] = []
    diagnostics: list[IRDiagnostic] = []


class ContextResourceIR(BaseModel):
    resource_id: str
    name: str
    canonical_name: str
    kind: str = "opaque_handle"
    semantic_role: str | None = None
    element_kind: str | None = None
    diagnostics: list[IRDiagnostic] = []


class ContextRuleIR(BaseModel):
    rule_id: str
    kind: str
    expression: str
    depends_on_fields: list[str] = []
    diagnostics: list[IRDiagnostic] = []


class StateContextIR(BaseModel):
    context_id: str
    name: str
    canonical_name: str
    scope: str = "global"
    state_field: str | None = None
    fields: list[ContextFieldIR] = []
    timers: list[ContextTimerIR] = []
    resources: list[ContextResourceIR] = []
    invariants: list[ContextRuleIR] = []
    diagnostics: list[IRDiagnostic] = []
    readiness: NormalizationStatus = NormalizationStatus.DRAFT


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
    state_contexts: list[StateContextIR] = []
    messages: list[ProtocolMessage] = []
    message_irs: list[MessageIR] = []
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
