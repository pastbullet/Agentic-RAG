# Phase A: StateContextIR Materializer 实施方案

## 1. 问题背景

当前 FSMIRv1 已实现 typed guard/action 解析，但在真实数据上 **typed 化比例极低**：

| 协议 | FSM 数 | typed guard 比率 | typed action 比率 | ctx refs 示例 |
|------|--------|-----------------|------------------|-------------|
| BFD | 8 | 10/93 (~11%) | 82/214 (~38%) | `bfd.SessionState`, `bfd.LocalDiag`, `state` |
| TCP | 6 | 3/77 (~4%) | 23/170 (~14%) | `SND.NXT`, `SND.UNA`, `SND.UP` |

**结论**：不能只靠 FSM typed refs 做物化，必须三路并行。

## 2. 三路物化来源

```text
来源 1: FSM refs (typed guard/action 中 ref_source=ctx/timer 的引用)
   ↓ 自动提取，provenance=["fsm_ref"]
   
来源 2: document clues (schema.timers, schema.state_machines.states)
   ↓ 自动提取，provenance=["document_clue"]
   
来源 3: manual patch (protocol_context_patch.json 文件)
   ↓ 人工补充，provenance=["manual_patch"]
   
   ────────────────────
         ↓ merge
   StateContextIR (consumer-driven readiness)
```

### 来源 1: FSM refs

从 FSMIRv1 所有 block.branch 中扫描：

| 条件 | 产物 | 类型推断 |
|------|------|---------|
| `TypedGuard.ref_source == "ctx"` | `ContextFieldIR` | 从 kind 推断: flag_check→bool, field_eq→opaque |
| `TypedGuard.ref_source == "timer"` | `ContextTimerIR` | timer_fired→有 triggers_event |
| `TypedAction.kind == "set_state"` | 标记 `state_field` | type_kind="enum" |
| `TypedAction.kind == "update_field"` & ref_source=="ctx"` | `ContextFieldIR` | opaque |
| `TypedAction.kind == "start_timer"/"cancel_timer"` | `ContextTimerIR` | — |

**真实数据能拿到的**：
- BFD: `bfd.SessionState`(state), `bfd.LocalDiag`(enum), `holddown`(timer)
- TCP: `SND.NXT`(u32), `SND.UNA`(u32), `SND.UP`(u32)

### 来源 2: document clues

从 `ProtocolSchema` 已有字段中提取：

| 来源 | 产物 | 逻辑 |
|------|------|------|
| `schema.timers` (TimerConfig) | `ContextTimerIR` | 直接映射 timer_name → canonical_name |
| `schema.state_machines[].states` | state field enum domain | FSM states → state field 的合法值集 |
| `schema.state_machines[].name` | scope 推断 | "session" → session, "connection" → connection |

**真实数据能拿到的**：
- BFD: Detection Time timer, 4个 state enum values (Down/Init/Up/AdminDown)
- TCP: Retransmission Timer, RTO timer, 11个 state enum values

### 来源 3: manual patch

为每个协议准备可选的 JSON patch 文件（人工编写）：

```text
data/patches/rfc5880-BFD/context_patch.json
data/patches/rfc793-TCP/context_patch.json
```

格式：

```json
{
  "scope": "session",
  "extra_fields": [
    {
      "canonical_name": "remote_discr",
      "type_kind": "u32",
      "semantic_role": null,
      "width_bits": 32
    }
  ],
  "extra_timers": [
    {
      "canonical_name": "tx_timer",
      "semantic_role": "keepalive"
    }
  ],
  "role_overrides": {
    "bfd.SessionState": "state",
    "desired_min_tx": null
  }
}
```

**用途**：补充 FSM typed 化遗漏的重要字段，覆盖自动推断的错误 role。

## 3. 核心模块设计

### 新增: `src/extract/state_context_materializer.py`

```python
def materialize_state_context(
    fsm_ir: FSMIRv1,
    schema: ProtocolSchema,
    patch: ContextPatch | None = None,
) -> StateContextIR:
    """三路合并物化 StateContextIR。"""

def materialize_all_state_contexts(
    schema: ProtocolSchema,
    patches: dict[str, ContextPatch] | None = None,
) -> list[StateContextIR]:
    """对 schema 中每个 FSMIRv1 物化一个 StateContextIR。"""

def collect_fsm_refs(fsm_ir: FSMIRv1) -> FsmRefCollection:
    """来源 1: 从 FSMIRv1 typed guard/action 收集所有 ctx/timer 引用。"""

def collect_document_clues(
    fsm_ir: FSMIRv1,
    schema: ProtocolSchema,
) -> DocumentClueCollection:
    """来源 2: 从 schema.timers + fsm states 收集文档线索。"""

def merge_sources(
    fsm_refs: FsmRefCollection,
    doc_clues: DocumentClueCollection,
    patch: ContextPatch | None,
) -> StateContextIR:
    """三路合并，去重，设 provenance，推断 role。"""
```

### 合并规则

同一个 canonical_name 出现在多个来源时：

1. **type_kind**: manual_patch > document_clue > fsm_ref (人工最准)
2. **semantic_role**: manual_patch > 启发式推断 > None
3. **provenance**: 所有来源合并 (e.g. `["fsm_ref", "document_clue"]`)
4. **冲突**: 报 diagnostic `CTX_MERGE_CONFLICT`，取高优先级

### canonical_name 归一化

真实数据中同一字段有不同写法：
- `bfd.SessionState` vs `SessionState` vs `session_state`
- `SND.NXT` vs `send_next_seq`

归一化策略：
1. 去掉 `bfd.` / `tcp.` / `ctx.` 前缀
2. 转 lower_snake_case
3. 常见缩写展开 (可选，启发式)：`SND` → `send`, `RCV` → `recv`, `NXT` → `next`, `UNA` → `unacked`

### semantic_role 推断（启发式）

| 模式 | role |
|------|------|
| 名含 "state" 且 type_kind=="enum" | `state` |
| 名含 "seq" + "send"/"snd" | `send_next_seq` |
| 名含 "seq" + "recv"/"rcv" | `recv_next_seq` |
| 名含 "window"/"win" + "send"/"snd" | `send_window` |
| 名含 "window"/"win" + "recv"/"rcv" | `recv_window` |
| timer 名含 "retransmit" | `retransmission` |
| timer 名含 "detect"/"hold" | `hold_timer` |
| timer 名含 "keepalive"/"tx" | `keepalive` |
| 以上都不匹配 | `None` (报 `CTX_ROLE_UNKNOWN` warning) |

### scope 推断

从 FSMIRv1.name 推断：
- 含 "session" → `session`
- 含 "connection" → `connection`
- 含 "login"/"association" → `association`
- fallback → `session`

### readiness 计算（consumer-driven）

```python
required_refs = collect_fsm_refs(fsm_ir).all_canonical_names()
normalize_state_context_ir(ctx, required_refs=required_refs)
```

- 有 state_field + 所有 FSM refs 被覆盖 → `READY`
- 有 state_field + 部分 FSM refs 未覆盖 → `DEGRADED_READY`
- 无 state_field → `BLOCKED`

## 4. Pipeline 集成

```python
# pipeline.py, fsm_ir lowering 之后
from src.extract.state_context_materializer import materialize_all_state_contexts

patches = load_context_patches(doc_stem)  # 可选
schema.state_contexts = materialize_all_state_contexts(schema, patches)

# 写 artifact
ctx_path = _artifact_path(doc_stem, "state_context_ir")
_write_json(ctx_path, [ctx.model_dump() for ctx in schema.state_contexts])
```

## 5. 数据模型补充

### `ContextPatch` (新增 model)

```python
class ContextPatch(BaseModel):
    scope: str | None = None
    extra_fields: list[ContextFieldIR] = []
    extra_timers: list[ContextTimerIR] = []
    extra_resources: list[ContextResourceIR] = []
    role_overrides: dict[str, str | None] = {}
```

### 内部中间结构

```python
@dataclass
class FsmRefCollection:
    ctx_field_refs: dict[str, RefDetail]   # canonical_name → 来源详情
    timer_refs: dict[str, RefDetail]
    state_targets: set[str]                # set_state 的 target 值

@dataclass
class DocumentClueCollection:
    timers: list[ContextTimerIR]           # 从 schema.timers
    state_enum_values: list[str]           # 从 FSM states
    inferred_scope: str
```

## 6. 测试计划

### 单元测试: `tests/extract/test_state_context_materializer.py`

| 测试 | 说明 |
|------|------|
| `test_collect_fsm_refs_extracts_ctx_fields` | 构造有 typed guard/action 的 FSMIRv1，验证 ctx ref 收集 |
| `test_collect_fsm_refs_extracts_timer_refs` | 验证 timer_fired/start_timer 产生 timer ref |
| `test_collect_document_clues_timers` | 从 schema.timers 收集 ContextTimerIR |
| `test_collect_document_clues_state_enum` | 从 FSM states 收集 enum values |
| `test_merge_deduplicates_same_name` | 同名 field 从两个来源合并，provenance 合并 |
| `test_merge_patch_overrides_role` | manual patch 的 role_override 覆盖自动推断 |
| `test_materialize_bfd_like_sm` | 用 BFD-like 数据物化，验证 state_field + timer |
| `test_materialize_empty_typed_refs` | typed 化为零时仍能从 document clues 物化出最小 ctx |
| `test_canonical_name_normalization` | `bfd.SessionState` → `session_state` |
| `test_readiness_consumer_driven` | required_refs 全覆盖 → READY，部分缺失 → DEGRADED |
| `test_provenance_tagging` | 每个 field/timer 的 provenance 正确标记来源 |

### 集成测试

| 测试 | 说明 |
|------|------|
| `test_bfd_real_schema_materializes` | 加载真实 BFD protocol_schema.json，物化不报错 |
| `test_tcp_real_schema_materializes` | 加载真实 TCP protocol_schema.json，物化不报错 |
| `test_pipeline_produces_state_context_artifact` | pipeline 跑完后 state_context_ir.json 存在 |

## 7. 预期产出 (BFD 示例)

```json
{
  "context_id": "fsm_rfc5880_bfd_bfd_session_state_machine_rfc_5880_6_2",
  "name": "BFD Session State Machine (RFC 5880 §6.2)",
  "canonical_name": "bfd_session_state_machine",
  "scope": "session",
  "state_field": "session_state",
  "fields": [
    {
      "canonical_name": "session_state",
      "type_kind": "enum",
      "semantic_role": "state",
      "provenance": ["fsm_ref", "document_clue"]
    },
    {
      "canonical_name": "local_diag",
      "type_kind": "opaque",
      "semantic_role": null,
      "provenance": ["fsm_ref"]
    }
  ],
  "timers": [
    {
      "canonical_name": "detection_time",
      "semantic_role": "hold_timer",
      "duration_expr": "Detect Mult * Remote agreed transmit interval",
      "triggers_event": "declare session down",
      "provenance": ["document_clue"]
    },
    {
      "canonical_name": "holddown",
      "semantic_role": null,
      "provenance": ["fsm_ref"]
    }
  ],
  "readiness": "degraded_ready",
  "diagnostics": [
    {"code": "CTX_ROLE_UNKNOWN", "message": "Cannot infer role for field local_diag"},
    {"code": "CTX_ROLE_UNKNOWN", "message": "Cannot infer role for timer holddown"},
    {"code": "missing_timer_context", "message": "..."}
  ]
}
```

## 8. 实施步骤

```text
Step 1: ContextPatch model + FsmRefCollection/DocumentClueCollection 数据结构
Step 2: collect_fsm_refs() — 扫描 FSMIRv1 typed refs
Step 3: collect_document_clues() — 扫描 schema.timers + FSM states
Step 4: canonical_name 归一化
Step 5: merge_sources() — 三路合并 + provenance + role 推断
Step 6: materialize_state_context() — 组装 StateContextIR + normalize
Step 7: 单元测试
Step 8: pipeline 集成 + artifact 落盘
Step 9: BFD/TCP 真实数据验证
```

## 9. 不做的事

- 不改 FSMIRv1 typed 解析（那是独立改进方向）
- 不做 codegen（那是 Phase C）
- 不做 trace verify（那是 Phase C）
- 不做 FC-LS（等 FC pipeline 跑通后自然适用）
- 不强制所有 field 有 semantic_role（允许 unknown）
