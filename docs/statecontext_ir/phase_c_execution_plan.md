---
title: "Phase C 执行方案：增量式 LLM Refine 路径"
date: 2026-04-04
tags:
  - statecontext-ir
  - phase-c
  - execution-plan
status: proposed
depends-on:
  - "[[phase_c_llm_assisted_typed_lowering_plan]]"
---

# Phase C 执行方案：增量式 LLM Refine 路径

## 0. 核心策略

> [!important] 风控原则
> **不动现有 sync lowering，加一条独立的 async refine 路径。**
>
> `lower_all_state_machines()` 保持同步、签名不变、行为不变。
> 新增 `refine_fsm_irs()` 作为 post-lowering 的 async 增强步骤，仅在 pipeline 中有 LLM 实例时调用。
> 这样 codegen 独立调用链（`_prepare_codegen_inputs`）完全不受影响，回归风险为零。

```text
当前链路（不动）：
  lower_all_state_machines(schema) → list[FSMIRv1]   # sync, regex-only

新增链路（pipeline 专用）：
  fsm_irs = lower_all_state_machines(schema)          # sync, regex-only
  fsm_irs = await refine_fsm_irs(fsm_irs, schema, llm)  # async, LLM fallback
```

---

## 1. 实施步骤总览

```text
Step 1  ProtocolHint 数据结构 + build_protocol_hint()
Step 2  _needs_refinement() 触发判断
Step 3  acceptance gate（_accept_llm_guard / _accept_llm_action）
Step 4  Step 1-3 的单元测试
Step 5  _llm_refine_transition() 实现
Step 6  refine_fsm_irs() 顶层 async 入口
Step 7  pipeline.py 接入
Step 8  BFD 回归
Step 9  TCP before/after 指标
```

---

## 2. Step 1：ProtocolHint 数据结构

### 文件：`src/models.py`

新增 dataclass（放在 FSMIRv1 附近）：

```python
class ProtocolHint(BaseModel):
    """Pipeline 前置阶段自动构建的协议上下文摘要。"""
    known_states: list[str] = Field(default_factory=list)
    known_timers: list[str] = Field(default_factory=list)
    known_message_names: list[str] = Field(default_factory=list)
    known_message_field_names: list[str] = Field(default_factory=list)
    observed_context_tokens: list[str] = Field(default_factory=list)
```

### 文件：`src/extract/fsm_ir.py`

新增 `build_protocol_hint()`：

```python
def build_protocol_hint(schema: ProtocolSchema) -> ProtocolHint:
    """从已有 ProtocolSchema 自动构建协议上下文摘要。"""
    states: set[str] = set()
    for sm in schema.state_machines:
        for s in sm.states:
            if s.name:
                states.add(s.name)

    timers = [t.timer_name for t in schema.timers if t.timer_name]
    message_names = [m.name for m in schema.messages if m.name]

    message_field_names: list[str] = []
    for m in schema.messages:
        for f in m.fields:
            if f.name:
                message_field_names.append(f.name)

    # 从 procedures 和 transition conditions 中提取含 "." 的 token
    context_tokens: set[str] = set()
    _DOT_TOKEN = re.compile(r'\b([A-Z][A-Z0-9]*\.[A-Z][A-Z0-9]*)\b')
    for proc in schema.procedures:
        for step in proc.steps:
            context_tokens.update(_DOT_TOKEN.findall(step))
    for sm in schema.state_machines:
        for t in sm.transitions:
            context_tokens.update(_DOT_TOKEN.findall(t.condition))
            for a in t.actions:
                context_tokens.update(_DOT_TOKEN.findall(a))

    return ProtocolHint(
        known_states=sorted(states),
        known_timers=timers,
        known_message_names=message_names,
        known_message_field_names=message_field_names,
        observed_context_tokens=sorted(context_tokens),
    )
```

### 工作量估计：~30 min

### 风险：无。纯新增代码，不改任何现有接口。

---

## 3. Step 2：触发判断

### 文件：`src/extract/fsm_ir.py`

```python
_REFINE_RAW_RATIO_THRESHOLD = 0.3
_REFINE_MIN_RAW_BRANCHES = 2

def _needs_refinement(ir: FSMIRv1) -> bool:
    total = sum(len(b.branches) for b in ir.blocks)
    if total == 0:
        return False
    raw = sum(
        1 for b in ir.blocks for br in b.branches
        if br.actions_raw or (br.guard_raw and br.guard_typed is None)
    )
    return (raw / total > _REFINE_RAW_RATIO_THRESHOLD
            and raw >= _REFINE_MIN_RAW_BRANCHES)
```

### 工作量估计：~15 min

### 风险：无。纯新增函数，不改 lowering 主路径。

---

## 4. Step 3：Acceptance Gate

### 文件：`src/extract/fsm_ir.py`

三个核心校验函数 + 两个辅助匹配函数：

```python
def _matches_known_state(target: str, hint: ProtocolHint) -> bool:
    """target 是否在 known_states 中（忽略大小写、空格、下划线差异）。"""
    normalized = target.replace(" ", "_").replace("-", "_").lower()
    return any(
        s.replace(" ", "_").replace("-", "_").lower() == normalized
        for s in hint.known_states
    )

def _matches_known_timer(target: str, hint: ProtocolHint) -> bool:
    normalized = target.replace(" ", "_").replace("-", "_").lower()
    return any(
        t.replace(" ", "_").replace("-", "_").lower() == normalized
        for t in hint.known_timers
    )

def _matches_known_context_or_message_slot(target: str, hint: ProtocolHint) -> bool:
    """检查 target 是否出现在 message fields 或 observed context tokens 中。"""
    if not target:
        return False
    candidates = set(hint.known_message_field_names) | set(hint.observed_context_tokens)
    # 也做 normalized 比较
    normalized = target.replace(" ", "_").replace("-", "_").lower()
    return any(
        c.replace(" ", "_").replace("-", "_").lower() == normalized
        for c in candidates
    ) or "." in target  # 含 "." 的 RFC 变量名（如 SND.NXT）宽容放过

def _is_acceptable_literal(value: str) -> bool:
    """只接受简单字面量：整数、布尔、枚举名。"""
    if not value:
        return False
    # 整数
    if value.lstrip("-").isdigit():
        return True
    # 布尔
    if value.lower() in {"true", "false", "0", "1"}:
        return True
    # 简单标识符（枚举名）—— 不含空格、运算符
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        return True
    return False

def _accept_llm_guard(guard: dict, hint: ProtocolHint) -> bool:
    kind = guard.get("kind")
    if kind == "unresolved" or kind is None:
        return False
    if kind not in {"context_field_eq", "context_field_ne",
                    "flag_check", "timer_fired", "always"}:
        return False
    if kind == "timer_fired":
        return _matches_known_timer(guard.get("field_ref", ""), hint)
    if kind in {"context_field_eq", "context_field_ne"}:
        if guard.get("operator") not in {"==", "!="}:
            return False
    return True

def _accept_llm_action(action: dict, hint: ProtocolHint) -> bool:
    kind = action.get("kind")
    if kind == "unresolved" or kind is None:
        return False
    if kind not in {"set_state", "start_timer", "cancel_timer", "update_field"}:
        return False
    target = action.get("target", "")
    if kind == "set_state":
        return _matches_known_state(target, hint) if target else False
    if kind in {"start_timer", "cancel_timer"}:
        return _matches_known_timer(target, hint) if target else False
    if kind == "update_field":
        if not _matches_known_context_or_message_slot(target, hint):
            return False
        value = action.get("value")
        if value and not _is_acceptable_literal(value):
            return False
    return True
```

### 工作量估计：~45 min

### 风险：无。纯新增函数。

---

## 5. Step 4：Step 1-3 单元测试

### 文件：`tests/extract/test_fsm_ir_refine.py`（新建）

测试矩阵：

| 测试函数 | 覆盖 |
|---------|------|
| `test_build_protocol_hint_from_schema` | hint 各字段从 schema 正确提取 |
| `test_build_protocol_hint_empty_schema` | 空 schema 返回全空 hint |
| `test_build_protocol_hint_context_tokens` | 含 `SND.NXT` 的 procedure 被提取到 `observed_context_tokens` |
| `test_needs_refinement_below_threshold` | raw 率和计数都低 → False |
| `test_needs_refinement_above_combined` | 同时满足 ratio + count → True |
| `test_needs_refinement_small_fsm_one_raw` | 3 branches 中 1 个 raw → False（count 不够） |
| `test_needs_refinement_small_fsm_two_raw` | 3 branches 中 2 个 raw → True |
| `test_needs_refinement_empty_blocks` | 0 branches → False |
| `test_accept_guard_unresolved` | kind=unresolved → False |
| `test_accept_guard_unknown_kind` | kind=foo → False |
| `test_accept_guard_timer_known` | timer_fired + known timer → True |
| `test_accept_guard_timer_unknown` | timer_fired + unknown timer → False |
| `test_accept_guard_complex_operator` | operator=">" → False |
| `test_accept_guard_eq_valid` | context_field_eq + operator="==" → True |
| `test_accept_guard_always` | kind=always → True |
| `test_accept_action_unresolved` | kind=unresolved → False |
| `test_accept_action_set_state_known` | set_state + known state → True |
| `test_accept_action_set_state_unknown` | set_state + unknown state → False |
| `test_accept_action_timer_known` | start_timer + known timer → True |
| `test_accept_action_timer_unknown` | cancel_timer + unknown timer → False |
| `test_accept_action_update_field_literal` | update_field + literal value → True |
| `test_accept_action_update_field_complex_expr` | update_field + `SEG.ACK + 1` → False |
| `test_accept_action_update_field_unknown_target` | target 不在 hint 中 → False |
| `test_matches_known_state_case_insensitive` | 大小写/空格/下划线变体匹配 |
| `test_is_acceptable_literal_various` | 整数/布尔/枚举名/表达式 的判定 |

### 关键：这些测试不需要 LLM mock，全部是纯函数测试。

### 工作量估计：~1.5 h

### 风险：无。纯新增测试文件。

---

## 6. Step 5：`_llm_refine_transition()`

### 文件：`src/extract/fsm_ir.py`

```python
async def _llm_refine_transition(
    transition: ProtocolTransition,
    branch: TransitionBranch,
    llm: LLMAdapter,
    hint: ProtocolHint,
) -> TransitionBranch:
    """LLM 辅助结构化单条 transition，失败时返回原始 branch 不变。"""
    # 1. 判断这条 branch 是否需要 refine
    needs_guard = branch.guard_raw and branch.guard_typed is None
    needs_actions = len(branch.actions_raw) > 0
    if not needs_guard and not needs_actions:
        return branch  # 无需 refine

    # 2. 构建 prompt
    user_content = _build_refine_user_prompt(transition, hint)

    # 3. 调用 LLM（chat_with_tools 或简单 chat）
    try:
        response = await llm.chat(
            system=REFINE_SYSTEM_PROMPT,
            user=user_content,
        )
        payload = json.loads(response)
    except Exception:
        logger.warning("LLM refine failed for (%s, %s), keeping regex result",
                       transition.from_state, transition.event)
        return branch

    # 4. Acceptance gate
    new_guard = branch.guard_typed  # regex 结果优先
    if needs_guard and "guard" in payload:
        if _accept_llm_guard(payload["guard"], hint):
            new_guard = TypedGuard(**_sanitize_guard_dict(payload["guard"]))

    new_actions_typed = list(branch.actions_typed)  # regex 已解析的保留
    remaining_raw: list[str] = []
    if needs_actions and "actions" in payload:
        llm_actions = payload.get("actions", [])
        # 尝试将 actions_raw 中每一条与 LLM 返回的 action 对应
        for raw_text in branch.actions_raw:
            matched = _match_llm_action_for_raw(raw_text, llm_actions, hint)
            if matched is not None:
                new_actions_typed.append(matched)
            else:
                remaining_raw.append(raw_text)
    else:
        remaining_raw = list(branch.actions_raw)

    # 5. 构造新 branch
    refined = TransitionBranch(
        guard_typed=new_guard,
        guard_raw=branch.guard_raw,
        actions_typed=new_actions_typed,
        actions_raw=remaining_raw,
        next_state=branch.next_state,
        notes=branch.notes + (["llm_refined"] if new_guard != branch.guard_typed
                              or len(new_actions_typed) > len(branch.actions_typed) else []),
    )
    refined.readiness = _compute_branch_readiness(refined)
    return refined
```

### 需要的辅助函数

| 函数 | 用途 |
|------|------|
| `_build_refine_user_prompt()` | 拼接 transition + hint 为 user prompt |
| `_sanitize_guard_dict()` | 将 LLM 返回的 dict 清洗为 TypedGuard 构造参数 |
| `_match_llm_action_for_raw()` | 将 raw action 文本与 LLM 返回的 action 列表匹配，经过 acceptance gate |

### LLMAdapter 扩展

当前 `LLMAdapter` 只有 `chat_with_tools()`，需要检查是否有简单 `chat()` 方法（不需要 tool calling）。如果没有，需要补一个轻量的 `chat()` 或 `chat_json()` 方法。

```python
# 需确认 LLMAdapter 是否需要新增
async def chat_json(self, system: str, user: str) -> dict:
    """发送单轮对话，期望 JSON 返回。"""
```

### 工作量估计：~2-3 h

### 风险：**中**。这是唯一涉及 LLM 调用的步骤，但因为 acceptance gate 已经在 Step 3 写好并测过，最差情况是 LLM 全部返回 unresolved / 解析失败 → 退化为原始 branch。

---

## 7. Step 6：`refine_fsm_irs()` 顶层入口

### 文件：`src/extract/fsm_ir.py`

```python
@dataclass
class RefineStats:
    """LLM refine 阶段的统计数据。"""
    triggered_count: int = 0
    accepted_guard_count: int = 0
    accepted_action_count: int = 0
    raw_branch_ratio_before: float = 0.0
    raw_branch_ratio_after: float = 0.0

async def refine_fsm_irs(
    fsm_irs: list[FSMIRv1],
    schema: ProtocolSchema,
    llm: LLMAdapter,
) -> tuple[list[FSMIRv1], RefineStats]:
    """Post-lowering LLM 辅助结构化，不改原始 lowering 链路。"""
    hint = build_protocol_hint(schema)
    stats = RefineStats()

    # before 指标
    total_before = sum(len(b.branches) for ir in fsm_irs for b in ir.blocks)
    raw_before = sum(
        1 for ir in fsm_irs for b in ir.blocks for br in b.branches
        if br.actions_raw or (br.guard_raw and br.guard_typed is None)
    )
    stats.raw_branch_ratio_before = raw_before / total_before if total_before else 0.0

    refined_irs: list[FSMIRv1] = []
    for ir in fsm_irs:
        if not _needs_refinement(ir):
            refined_irs.append(ir)
            continue

        stats.triggered_count += 1
        new_blocks = []
        for block in ir.blocks:
            new_branches = []
            for i, branch in enumerate(block.branches):
                transition = _find_original_transition(schema, block, i)
                if transition is None:
                    new_branches.append(branch)
                    continue
                old_guard = branch.guard_typed
                old_action_count = len(branch.actions_typed)
                refined = await _llm_refine_transition(transition, branch, llm, hint)
                if refined.guard_typed is not None and old_guard is None:
                    stats.accepted_guard_count += 1
                stats.accepted_action_count += len(refined.actions_typed) - old_action_count
                new_branches.append(refined)
            new_blocks.append(StateEventBlock(
                from_state=block.from_state,
                event=block.event,
                branches=new_branches,
            ))
        refined_irs.append(ir.model_copy(update={"blocks": new_blocks}))

    # after 指标
    total_after = sum(len(b.branches) for ir in refined_irs for b in ir.blocks)
    raw_after = sum(
        1 for ir in refined_irs for b in ir.blocks for br in b.branches
        if br.actions_raw or (br.guard_raw and br.guard_typed is None)
    )
    stats.raw_branch_ratio_after = raw_after / total_after if total_after else 0.0

    return refined_irs, stats
```

### 需要的辅助函数

| 函数 | 用途 |
|------|------|
| `_find_original_transition()` | 通过 (from_state, event, index) 反查原始 ProtocolTransition |

### 关键设计点

- `refine_fsm_irs()` 接收的是 `lower_all_state_machines()` 的输出，而非替代它
- 返回 `(refined_irs, stats)`，stats 用于 pipeline stage_data
- `_find_original_transition()` 是必要的，因为 `_llm_refine_transition` 需要原始 transition 文本构建 prompt

### 工作量估计：~1 h

### 风险：低。仅包装 Step 5 的函数，遍历逻辑简单。

---

## 8. Step 7：Pipeline 接入

### 文件：`src/extract/pipeline.py`

**改动最小化**：只在 merge 阶段的 `lower_all_state_machines` 之后加一行。

```python
# 当前代码（line 750-751）：
fsm_irs = lower_all_state_machines(schema)
schema.fsm_irs = fsm_irs

# 改为：
from src.extract.fsm_ir import lower_all_state_machines, refine_fsm_irs

fsm_irs = lower_all_state_machines(schema)
# Phase C: LLM-assisted typed refinement
refine_stats = None
if llm is not None:
    fsm_irs, refine_stats = await refine_fsm_irs(fsm_irs, schema, llm)
schema.fsm_irs = fsm_irs
```

**stage_data 新增**（在 merge 的 data dict 中）：

```python
if refine_stats is not None:
    stage_data.update({
        "llm_refine_triggered_count": refine_stats.triggered_count,
        "llm_refine_accepted_guard_count": refine_stats.accepted_guard_count,
        "llm_refine_accepted_action_count": refine_stats.accepted_action_count,
        "raw_branch_ratio_before": round(refine_stats.raw_branch_ratio_before, 4),
        "raw_branch_ratio_after": round(refine_stats.raw_branch_ratio_after, 4),
    })
```

### `codegen.py` 的 `_prepare_codegen_inputs()` 不动

因为 `_prepare_codegen_inputs()` 自己调 `lower_all_state_machines()`，走的是 sync regex-only 路径。这恰好就是我们想要的——codegen 独立调用时不依赖 LLM，只有 pipeline 完整调用时才走 LLM refine。

如果未来需要 codegen 也用 refined 结果，让 pipeline 在调 codegen 前先把 `schema.fsm_irs` 设为 refined 版本即可（当前 pipeline line 750-751 已经这样做了）。而 `_prepare_codegen_inputs` 的逻辑是 `elif sorted_schema.fsm_irs: fsm_irs = list(sorted_schema.fsm_irs)`，所以如果 pipeline 已经设了 `fsm_irs`，codegen 会直接复用，**不会重新 lower**。

### 验证 codegen 不会重新 lower

```python
# codegen.py line 2168-2172
if sorted_schema.state_machines:
    sorted_schema.fsm_irs = lower_all_state_machines(sorted_schema)  # ← 会覆盖！
    fsm_irs = list(sorted_schema.fsm_irs)
elif sorted_schema.fsm_irs:
    fsm_irs = list(sorted_schema.fsm_irs)
```

> [!warning] 问题发现
> `_prepare_codegen_inputs` 第一个条件是 `if sorted_schema.state_machines`，只要 schema 有 state_machines（几乎总是有），就会重新 lower，**覆盖 pipeline 设置的 refined fsm_irs**。
>
> **需要小改 codegen.py**：改为优先使用已有的 `fsm_irs`。

```python
# codegen.py _prepare_codegen_inputs 修改：
if sorted_schema.fsm_irs:
    # 优先使用已有的（可能是 refined 版本）
    fsm_irs = list(sorted_schema.fsm_irs)
elif sorted_schema.state_machines:
    sorted_schema.fsm_irs = lower_all_state_machines(sorted_schema)
    fsm_irs = list(sorted_schema.fsm_irs)
```

这是一个 **2 行交换**的改动，语义清晰：有 fsm_irs 就用，没有才 lower。

### 工作量估计：~30 min

### 风险：低。pipeline 改动仅 3 行新增 + codegen 2 行顺序调换。

---

## 9. Step 8：BFD 回归

### 目标

确认 BFD 在以下两种模式下行为完全一致：
1. `llm=None`（纯 regex）
2. `llm=real_llm`（LLM 存在，但 BFD 的 raw ratio 低，不触发 refine）

### 验证方法

```bash
# 1. baseline（无 LLM）
python run_extract_pipeline.py \
  --doc rfc5880-BFD.pdf \
  --stages classify,extract,merge,codegen,verify

# 2. with LLM（确认 refine 未触发）
python run_extract_pipeline.py \
  --doc rfc5880-BFD.pdf \
  --stages classify,extract,merge,codegen,verify \
  --enable-refine

# 3. 对比
diff data/out/bfd_baseline/ data/out/bfd_with_llm/
```

### 预期结果

- `llm_refine_triggered_count = 0`
- 所有 FSM IR JSON 完全一致
- codegen 产物完全一致
- verify 结果一致

### 工作量估计：~30 min（包括等运行时间）

---

## 10. Step 9：TCP Before/After 指标

### 目标

在 TCP 上验证 LLM refine 的实际收益。

### 验证方法

```bash
# 1. before（纯 regex）
python run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --stages classify,extract,merge,codegen,verify

# 2. after（with LLM refine）
python run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --stages classify,extract,merge,codegen,verify \
  --enable-refine
```

### 关注指标

| 指标 | before（参考值） | after（期望方向） |
|------|---------------|----------------|
| `raw_branch_ratio` | ~0.7+ | ↓ 显著下降 |
| `accepted_guard_count` | 0 | ↑ |
| `accepted_action_count` | 0 | ↑ |
| `generated_action_count`（codegen 统计） | ~20 lines | ↑ |
| `degraded_action_count`（codegen 统计） | 大量 | ↓ |
| `noise_ref_count` | 5 | 不应增加 |
| `coverage_ratio`（alignment） | 1.0 | 保持或轻微下降均可接受 |

### 结果记录

将对比结果写入：
`docs/validation_and_experiments/experiment_records/2026-04-XX_tcp_phase_c_before_after.md`

### 工作量估计：~1 h（包括运行 + 分析 + 记录）

---

## 11. 文件改动清单

| 文件 | 改动性质 | 改动量 |
|------|---------|-------|
| `src/models.py` | **新增** ProtocolHint | ~10 行 |
| `src/extract/fsm_ir.py` | **新增** 函数（不改现有） | ~200 行新增 |
| `src/extract/pipeline.py` | **小改** merge 阶段 | ~8 行新增 |
| `src/extract/codegen.py` | **小改** `_prepare_codegen_inputs` 条件顺序 | 2 行交换 |
| `src/agent/llm_adapter.py` | **可能新增** `chat_json()` 方法 | ~15 行 |
| `tests/extract/test_fsm_ir_refine.py` | **新建** | ~300 行 |
| `tests/extract/test_pipeline.py` | **小增** refine 集成测试 | ~30 行 |

> [!success] 改动边界
> - `lower_all_state_machines()` 签名和行为 **完全不变**
> - `_try_parse_guard()` / `_try_parse_action()` **完全不变**
> - `state_context_materializer.py` **不动**
> - `state_context_alignment.py` **不动**
> - `codegen.py` 主逻辑 **不动**，仅调换 `_prepare_codegen_inputs` 中一个 if/elif 顺序

---

## 12. 时间线

| 日期 | 任务 | 预估 |
|------|------|------|
| Day 1 上午 | Step 1-3（ProtocolHint + 触发 + gate） | 1.5 h |
| Day 1 下午 | Step 4（单元测试，跑通所有 gate 测试） | 1.5 h |
| Day 2 上午 | Step 5（`_llm_refine_transition` + LLMAdapter 适配） | 2-3 h |
| Day 2 下午 | Step 6-7（`refine_fsm_irs` + pipeline 接入） | 1.5 h |
| Day 3 上午 | Step 8（BFD 回归） | 0.5 h |
| Day 3 下午 | Step 9（TCP before/after + 实验记录） | 1-1.5 h |

**总计：~8-10 h，约 2.5 个工作日。**

---

## 13. 回退方案

如果 Phase C 效果不佳或 LLM 调用不稳定：

1. **最小回退**：pipeline 中 `if llm is not None` 改为 `if False`，一行禁用
2. **完全回退**：删除 `refine_fsm_irs()` 调用，恢复 pipeline 原样。因为 `lower_all_state_machines()` 没改，回退是零成本的
3. **codegen 回退**：`_prepare_codegen_inputs` 的 if/elif 交换是安全的改进，即使回退 Phase C 也可以保留

> [!important] 这就是"保留 sync lowering + 加 async refine 路径"策略的核心优势
> 回退成本为零，不需要 revert 任何现有函数的签名或行为。
