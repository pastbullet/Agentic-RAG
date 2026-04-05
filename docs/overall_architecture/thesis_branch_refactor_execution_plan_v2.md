# thesis 分支可执行重构路线图（重设计版）

> 这份方案以你刚补充的 **thesis 分支 as-is 现状** 为前提，而不是以前那种“StateContextIR/FSMIR 已全面进主链”的旧假设。
>
> 采用的基线判断是：
>
> - `MessageIR`：已经进入真实主链，且是当前最成熟的实现层 IR
> - `StateContextIR`：模型层 + 最小 normalization 已存在，且已支持 `READY / DEGRADED_READY / BLOCKED`，但还未进入真实主链
> - `FSMIR`：尚未正式成型，当前仍更接近 `ProtocolStateMachine + condition/actions strings + skeleton codegen`
>
> 因此，本方案不建议直接把 `MessageIR / StateContextIR / FSMIR` 三条线当成同成熟度工程并行推进，而是建议走：
>
> ```text
> MessageIR 稳定化
> -> 修 FSM skeleton 结构问题
> -> 引入 BehaviorIR-lite
> -> 让 StateContextIR 由行为需求倒推并与上下文 clues 合并
> -> 上 trace verify
> -> 最后再升格为 full FSMIR
> ```

---

## 1. 核心结论

### 1.1 当前最合理的路线不是“三条 IR 并行”，而是“两稳一升”

更贴合 thesis 分支当前成熟度的推进方式是：

- **稳住 MessageIR**：继续承担结构层骨架，不再无限扩张行为语义
- **稳住现有 skeleton FSM codegen**：先把当前 state/event 结构输出做成稳定骨架
- **把行为层正式 IR 化**：但不要一上来 full FSMIR，先上 `BehaviorIR-lite`
- **让 StateContextIR 成为 behavior-constrained runtime schema**：不是独立空转的大抽取线

### 1.2 最值得重设计的不是 MessageIR，而是“行为层如何进入可消费状态”

当前 MessageIR 已有：

- extractor / lowering / normalization
- codegen
- verify
- artifacts
- readiness
- BFD / TCP 上的真实验证路径

当前最薄弱的部分仍是行为层：

- `condition: str`
- `actions: list[str]`
- guard 未类型化
- runtime hooks 未统一
- `(state, event)` 多条件分支导致 skeleton codegen 不稳定

所以下一阶段的设计重心应当是：

> **让行为层先进入“可消费 typed IR”，而不是继续停留在 topology + annotation。**

---

## 2. 推荐的新方案：保持三层终态，但重排落地顺序

## 2.1 终态目标仍然保留三层

最终目标依然可以保留为：

```text
MessageIR + StateContextIR + FSMIR
```

其中：

- `MessageIR`：消息/帧结构层
- `StateContextIR`：运行时状态层
- `FSMIR`：行为/状态机层

这个终态没有问题，不需要推翻。

## 2.2 但实际落地顺序应改成下面这样

```text
Extraction Layer:
  ProtocolMessage / ProtocolStateMachine / ProcedureRule / TimerConfig / ErrorRule

Implementation Layer (当前与近期主线):
  MessageIR            # 已进入真实主链
  BehaviorIR-lite      # 下一步要补上的中间实现层
  StateContextIR       # 由行为需求 + 文档 clues 共同驱动 materialize

Behavior Runtime Layer (后续目标):
  FSMIR                # BehaviorIR-lite 稳定后再升格

Verification Layer:
  syntax / symbols / roundtrip / trace verify
```

这个顺序的好处是：

1. 不推翻已经跑通的 MessageIR 主链
2. 不让 StateContextIR 在没有 consumer 时空转
3. 不把 full FSMIR 的 guard / action / ctx / timer / emit / codegen / verify 一次性压进一个阶段

---

## 3. 新方案的关键设计原则

## 3.1 MessageIR 继续做“结构层骨架”，但停止承担行为语义膨胀

建议：

- 冻结 `MessageIR` 顶层 schema 形状
- 允许继续补：
  - 局部 layout bug
  - option/tlv 缺口
  - diagnostics/readiness 修正
  - 为 behavior/emit 真实需求补最少量字段语义
- 不再尝试把 runtime state、procedure side effect、跨消息状态等继续塞进 MessageIR

一句话：

> **MessageIR 封边界，不封修补。**

## 3.2 StateContextIR 不应被设计成独立空转大线

最推荐的方式不是先从文档里抽大量“看起来像 context 的对象”，而是：

- 一部分来自文档中的 runtime state / timer / queue / resource clues
- 更关键的一部分来自 `BehaviorIR-lite` 真正引用到的：
  - `ctx.field`
  - `ctx.timer`
  - `ctx.resource`

最后把两边 merge 成一个最小可用的 `StateContextIR`。

也就是：

> **StateContextIR 是 clue-augmented、behavior-constrained 的 runtime schema。**

这比“纯 behavior 反推”更稳，也比“先抽一大堆 context 再说”更可执行。

## 3.3 行为层先做 AST + raw fallback，不要直接 full FSMIR

建议的行为层不是 DSL-only，也不是纯自然语言字符串，而是：

- 先做最小 typed AST
- 无法结构化的部分保留 `raw_text`
- 配合 `diagnostics` 和 `normalization_status`

这样可以做到：

- codegen 可消费 typed 部分
- verify 可针对 typed 部分做检查
- 不会因为无法 100% 结构化就整条链阻塞

## 3.4 readiness 语义统一沿用当前四态

既然 thesis 分支现在已经有：

- `DRAFT`
- `READY`
- `DEGRADED_READY`
- `BLOCKED`

那么新引入的 `BehaviorIR-lite` 也建议直接采用同一套 readiness 语义。

推荐定义如下：

- `READY`：拓扑、guard、action 都已进入可生成子集
- `DEGRADED_READY`：拓扑稳定，但 guard/action 部分仍退化为 raw/stub/comment
- `BLOCKED`：关键结构不明，无法安全生成 skeleton
- `DRAFT`：仅中间态或未 finalize

这样避免引入第二套 readiness 语言。

## 3.5 先做 trace verify，不追完整 runtime

下一步验证重点，不该是“大而全协议执行框架”，而应该是：

```text
decode message
-> derive event/predicate
-> run one transition
-> assert ctx update
-> assert emitted outgoing message / timer effects / resource effects
```

也就是先做：

> **behavior trace harness**

而不是先做完整协议 runtime。

---

## 4. 重设计后的推荐架构

## 4.1 总体架构图

```text
Protocol Document
-> classify / extract / retrieve
-> extraction objects
   - ProtocolMessage
   - ProtocolStateMachine
   - ProcedureRule
   - TimerConfig
   - ErrorRule
   - Context clues

-> normalization / lowering
   - MessageIR
   - BehaviorIR-lite
   - StateContextIR

-> codegen
   - message codec skeleton
   - context struct + helper skeleton
   - state machine / behavior skeleton

-> verify
   - syntax
   - expected symbols
   - roundtrip
   - trace verify

-> protocol software skeleton
```

## 4.2 三层之间的职责边界

### MessageIR 负责

- wire format
- fields / sections / offsets
- optional/composite tail
- option/tlv list
- pack / unpack / validate 所需结构语义

回答的是：

> 报文在线上长什么样？

### StateContextIR 负责

- `ctx.field`
- `ctx.timer`
- `ctx.resource`
- 持久状态
- queue/buffer/resource/timer 的 runtime 持有对象
- invariants / ownership / mutability 的最小表达

回答的是：

> 状态机真正操作的运行时对象是什么？

### BehaviorIR-lite / FSMIR 负责

- state
- event
- guard
- action
- next_state
- emit message
- timer/resource hooks

回答的是：

> 协议行为如何变化？

## 4.3 统一执行闭环

推荐把最终闭环明确成：

```text
收到报文
-> MessageIR decode
-> derive event / predicates
-> BehaviorIR / FSMIR transition
-> read / write StateContextIR
-> 必要时构造 outgoing MessageIR
-> encode / emit
```

也可以简写成：

```text
msg -> fsm(ctx, msg) -> ctx update -> emit msg
```

---

## 5. 行为层的重新设计：先上 BehaviorIR-lite

## 5.1 为什么不是直接 full FSMIR

因为当前 thesis 分支行为层现实起点仍然是：

- `ProtocolStateMachine`
- `ProtocolTransition`
- `condition: str`
- `actions: list[str]`

这时一步上 full FSMIR，会同时引入：

- guard 语言设计
- action 语言设计
- runtime references
- timer/resource hooks
- emit-message 结构化
- codegen
- verify

范围过大，且不利于快速稳定 skeleton。

## 5.2 推荐的 BehaviorIR-lite v1 模型

建议先引入下面这些对象。

### `EventRef`

用于统一事件入口。

建议字段：

- `event_id`
- `name`
- `canonical_name`
- `source_pages`
- `source_node_ids`
- `diagnostics`

### `ValueRef`

统一表达 guard/action 里引用的对象。

建议支持：

- `msg_field`
- `ctx_field`
- `timer`
- `resource`
- `enum`
- `const`
- `literal`
- `state`

建议字段：

- `kind`
- `name`
- `type_hint`
- `path`
- `literal_value`
- `diagnostics`

### `GuardAtom`

最小 guard 原子。

建议字段：

- `lhs: ValueRef`
- `op`
- `rhs: ValueRef | None`
- `negated: bool`
- `source_pages`
- `diagnostics`

v1 建议仅支持少量操作：

- `eq`
- `ne`
- `lt`
- `le`
- `gt`
- `ge`
- `present`
- `flag_set`
- `state_is`
- `timer_expired`

### `GuardExpr`

建议字段：

- `kind: all | any | atom | raw | true`
- `atoms`
- `raw_text`
- `source_pages`
- `diagnostics`
- `normalization_status`

### `ActionOp`

建议字段：

- `op`
- `args`
- `raw_text`
- `source_pages`
- `diagnostics`
- `normalization_status`

v1 只支持闭集操作：

- `set_state`
- `set_ctx_field`
- `start_timer`
- `cancel_timer`
- `emit_message`
- `drop_message`
- `raise_error`
- `enqueue_resource`
- `dequeue_resource`
- `call_stub`

其中：

- `call_stub` 很重要，用于给暂时无法结构化的动作留出口
- `emit_message` 只要求能表达“发什么 message / family”，先不要求完整 payload 生成

### `TransitionIR`

建议字段：

- `transition_id`
- `from_state`
- `event: EventRef`
- `guard: GuardExpr`
- `actions: list[ActionOp]`
- `to_state`
- `priority`
- `source_pages`
- `source_node_ids`
- `diagnostics`
- `normalization_status`

### `BehaviorIR`

建议字段：

- `ir_id`
- `name`
- `states`
- `events`
- `transitions`
- `source_pages`
- `diagnostics`
- `normalization_status`

## 5.3 BehaviorIR-lite 的 v1 设计原则

1. **保留 document order 作为默认 priority**
2. **typed 优先，无法结构化则 raw fallback**
3. **不要求所有 action 立即可执行**
4. **只要 topology + event + next_state 稳定，就允许 `DEGRADED_READY` 进入 skeleton 生成**

---

## 6. StateContextIR 的重新设计：从“概念层”走向“可消费层”

## 6.1 不建议重做一条全新的 StateContextIR 主线

现在最不值得做的事，是在当前基础上再做一条与行为层脱钩的大而全 context extraction pipeline。

更合理的方式是：

```text
文档中的 context clues
+ BehaviorIR-lite 中真实引用的 ctx/timer/resource
-> materialize / normalize / merge
-> StateContextIR
```

## 6.2 推荐给 StateContextIR 增加的字段

你已经有：

- `StateContextIR`
- `ContextFieldIR`
- `ContextTimerIR`
- `ContextResourceIR`
- `ContextRuleIR`

在此基础上建议补这些最小消费字段：

### 对 `ContextFieldIR`

建议增加：

- `origin: extracted | behavior_required | manual`
- `semantic_role`
- `mutability: read_only | write_only | read_write`
- `storage_kind: scalar | flag | counter | enum`
- `initial_value_hint`
- `producer_transition_ids`
- `consumer_transition_ids`

### 对 `ContextTimerIR`

建议增加：

- `origin`
- `start_transition_ids`
- `cancel_transition_ids`
- `expire_event_name`
- `timeout_source`

### 对 `ContextResourceIR`

建议增加：

- `origin`
- `resource_kind: queue | buffer | handle | channel | table`
- `producer_transition_ids`
- `consumer_transition_ids`

### 对 `StateContextIR`

建议增加：

- `context_id`
- `canonical_name`
- `associated_behavior_ids`
- `associated_message_families`
- `normalization_status`
- `diagnostics`

## 6.3 StateContextIR 的 materialization 规则

建议采用下面的优先级：

### 一级：behavior-required slots

只要 BehaviorIR-lite 里明确出现：

- `ctx.field`
- `ctx.timer`
- `ctx.resource`

就必须 materialize 到 StateContextIR。

### 二级：document-extracted clues

对于文档里明确指出的：

- persistent state
- retransmission queue
- send/receive window
- timers
- resources

作为 clue 合并进来。

### 三级：manual patches

允许通过 patch 文件补：

- 命名修正
- semantic role 修正
- initial value hints
- merge 纠偏

这非常重要，因为 thesis 阶段追求的是“可验证闭环”，不是追求零人工修正。

## 6.4 推荐加一个 patch lane

建议新加：

```text
data/manual/<doc>/behavior_patches.json
data/manual/<doc>/state_context_patches.json
```

用来：

- 修正 event 名
- 修正 ctx slot 名
- 指定 emit message family
- 修正 timer/resource 分类
- 处理少量高价值样例中的 LLM 抽取不稳问题

这比试图把所有不确定性都压到自动 normalization 上更现实。

---

## 7. 先修现有 FSM skeleton，而不是直接重写

## 7.1 当前最先该修的结构问题

当前最直接的问题不是“状态机语义不够多”，而是：

> 同一个 `(state, event)` 下如果出现多个条件分支，模板容易把它们直接展开成多个 `case`，从而让 skeleton codegen 变得不稳定。

这会直接影响：

- 编译稳定性
- verify 稳定性
- 后续 BehaviorIR-lite 的接入

## 7.2 推荐的生成结构

不要再让模板直接把每条 transition 展成一层独立 case。

建议改成：

```c
switch (ctx->state) {
  case STATE_X:
    switch (event) {
      case EVENT_A:
        if (guard_1) {
          /* actions_1 */
          ctx->state = STATE_Y;
          return OK;
        }
        if (guard_2) {
          /* actions_2 */
          ctx->state = STATE_Z;
          return OK;
        }
        return NO_MATCH;
    }
}
```

也就是：

- 外层按 `from_state` 分组
- 内层按 `event` 分组
- 同一 `(state, event)` 内部用有序 guard 链处理
- guard 顺序默认保留 document order / extraction order

## 7.3 在 BehaviorIR-lite 未接入前的过渡策略

在 `condition/actions` 仍为字符串时：

- `condition` 先生成注释 + TODO stub
- `actions` 先生成注释 + TODO stub
- `next_state` 一律稳定生成

这样 skeleton 至少先成为：

> **稳定的行为骨架，而不是半随机的字符串拼接产物。**

---

## 8. 推荐的阶段化执行路线图

下面这部分是最重要的“可执行计划”。

---

## 阶段 0：冻结 as-is，建立可回归基线

### 目标

在继续加架构之前，先把当前 thesis 分支的“真实现状”固定下来，避免文档和代码继续漂移。

### 任务

- 新增：
  - `ARCHITECTURE_AS_IS.md`
  - `ARCHITECTURE_TARGET.md`
- 对现有样例固定基线：
  - BFD
  - TCP
- 固定当前 artifacts：
  - `protocol_schema.json`
  - `message_ir.json`
  - `verify_report.json`
- 增加 smoke tests：
  - message_ir 主链 smoke
  - state_context normalization smoke

### 涉及文件

- `README.md`
- `PROJECT_CONTEXT.md`
- `run_extract_pipeline.py`
- `tests/extract/*`

### 交付物

- as-is/target 两份架构文档
- 两个固定样例的基线 artifact
- 基线 smoke tests

### 验收标准

- 文档里不再把 target 写成 as-is
- BFD/TCP 基线运行结果可重复
- smoke tests 能保护现有主链不被误伤

---

## 阶段 1：修 FSM skeleton codegen，让它先稳定

### 目标

先解决当前 `(state, event)` 多分支下 skeleton 不稳定的问题。

### 任务

- 在 `codegen.py` 中新增 transition grouping：
  - `group by from_state`
  - `group by event`
- 模板改为：
  - state switch
  - event switch
  - ordered guard chain
- 统一 `NO_MATCH / UNHANDLED / STUB` 返回路径
- 暂时保留 raw condition/actions 注释输出

### 涉及文件

- `src/extract/codegen.py`
- `src/extract/templates/state_machine.c.j2`
- `src/extract/templates/state_machine.h.j2`
- `src/extract/verify.py`
- `tests/extract/test_codegen_*`

### 交付物

- 稳定的 FSM skeleton 输出
- 更可控的 state/event/guard 骨架

### 验收标准

- 同一 `(state, event)` 多 guard 的 skeleton 仍能稳定生成
- 至少一个 BFD 路径和一个 TCP 路径通过 syntax check
- verify 不再因为 case 结构问题波动

---

## 阶段 2：引入 BehaviorIR-lite

### 目标

让行为层从纯字符串进入“最小 typed IR”。

### 任务

- 在模型层新增：
  - `EventRef`
  - `ValueRef`
  - `GuardAtom`
  - `GuardExpr`
  - `ActionOp`
  - `TransitionIR`
  - `BehaviorIR`
- 新增 lowering：
  - `ProtocolStateMachine -> BehaviorIR-lite`
  - 吸收 `ProcedureRule / TimerConfig / ErrorRule` 中能结构化的部分
- 输出 artifact：
  - `behavior_ir.json`
- readiness 采用：
  - `DRAFT / READY / DEGRADED_READY / BLOCKED`

### 推荐实现方式

短期内为了减少 import churn，建议先直接加在：

- `src/models.py`

并新增：

- `src/extract/behavior_ir.py`

用于：

- lowering
- diagnostics
- normalization

等结构稳定后，再考虑把 IR 模型拆到 `src/ir/`。

### 涉及文件

- `src/models.py`
- `src/extract/behavior_ir.py`（新）
- `src/extract/pipeline.py`
- `run_extract_pipeline.py`
- `tests/extract/test_behavior_ir.py`（新）

### 交付物

- `BehaviorIR-lite` 模型层
- `behavior_ir.json`
- lowering unit tests

### 验收标准

- 至少一个 BFD 行为样例和一个 TCP 行为样例能生成 `BehaviorIR-lite`
- 对无法结构化部分，能落到 `raw_text + diagnostics`
- `DEGRADED_READY` 行为骨架也能继续进入 skeleton codegen

---

## 阶段 3：让 StateContextIR 成为可消费 runtime schema

### 目标

把现在主要停留在模型/normalization 层的 StateContextIR 推进到“可被行为和 codegen 真正消费”。

### 任务

- 扩展现有 `StateContextIR` 字段：
  - `origin`
  - `mutability`
  - `storage_kind`
  - `producer/consumer transition ids`
- 新增 materialization 逻辑：
  - 从 `BehaviorIR-lite` 收集 `ctx/timer/resource` 引用
  - 与现有 context clues 合并
  - 产出最终 `StateContextIR`
- 输出 artifact：
  - `state_context_ir.json`
- 新增 patch lane：
  - `behavior_patches.json`
  - `state_context_patches.json`

### 推荐实现方式

先复用你已有的：

- `src/extract/state_context.py`

在里面新增两类入口：

- `normalize_state_context_clues(...)`
- `materialize_state_context_from_behavior(...)`

再通过 merge 函数得到最终 `StateContextIR`。

### 涉及文件

- `src/models.py`
- `src/extract/state_context.py`
- `src/extract/pipeline.py`
- `run_extract_pipeline.py`
- `tests/extract/test_state_context_integration.py`（新）

### 交付物

- `state_context_ir.json`
- 行为驱动的 context materialization
- manual patch lane

### 验收标准

- BehaviorIR-lite 中被引用到的 ctx/timer/resource，100% 能 materialize 到 StateContextIR
- StateContextIR 对 TCP/BFD 至少能给出一个最小可消费上下文
- `DEGRADED_READY` 的 state context 也能进入 codegen skeleton

---

## 阶段 4：把 codegen 升级成三层骨架消费，而不是只吃 MessageIR

### 目标

让 codegen 从“消息主导”升级成“消息 + 状态 + 行为”的骨架生成器。

### 任务

#### 4.1 Message 层

继续保持：

- pack
- unpack
- validate

#### 4.2 Context 层

新增生成：

- `ctx struct`
- `timer/resource` declarations
- `ctx init/reset` skeleton
- `ctx access helpers`（可选）

#### 4.3 Behavior 层

新增消费 `BehaviorIR-lite`：

- event enum
- transition skeleton
- guard stub
- action stub
- emit-message stub
- timer/resource stub

### 推荐生成接口（示意）

```c
int protocol_transition(
    protocol_ctx *ctx,
    protocol_event event,
    const void *in_msg,
    protocol_runtime *rt,
    protocol_outbox *outbox
);
```

其中：

- `ctx`：来自 StateContextIR
- `in_msg`：来自 MessageIR decode 结果
- `rt`：承接 timer/resource/runtime hooks
- `outbox`：承接 outgoing message emit

### 涉及文件

- `src/extract/codegen.py`
- `src/extract/templates/*.j2`
- `src/extract/pipeline.py`
- `tests/extract/test_codegen_behavior.py`（新）

### 交付物

- context header/source skeleton
- behavior skeleton
- emit/timer/resource stubs

### 验收标准

- BFD/TCP 代表样例均能生成三层骨架
- syntax/symbol verify 覆盖到新的 ctx/behavior 产物
- 不要求 full runtime 行为正确，但要求 skeleton 编译稳定

---

## 阶段 5：新增 trace verify，建立行为闭环验证

### 目标

让 verify 从“语法 + roundtrip”扩到“单步行为闭环”。

### 任务

新增 trace verify：

```text
fixture
-> build/init ctx
-> construct input msg / or decoded msg view
-> derive event / predicates
-> run one transition
-> assert next_state
-> assert ctx delta
-> assert timer/resource effects
-> assert emitted outgoing message
```

### 推荐 fixture 结构

```json
{
  "name": "tcp_syn_received_ack_path",
  "initial_state": "SYN_RECEIVED",
  "event": "RECV_ACK",
  "ctx_init": {
    "snd_nxt": 100,
    "rcv_nxt": 200
  },
  "msg_fixture": {
    "ack": 101
  },
  "expected": {
    "next_state": "ESTABLISHED",
    "ctx_delta": {
      "snd_una": 101
    },
    "timers_started": [],
    "timers_cancelled": ["retransmission_timer"],
    "emit_messages": []
  }
}
```

### 涉及文件

- `src/extract/verify.py`
- `src/extract/trace_verify.py`（可新建）
- `run_extract_pipeline.py`
- `tests/extract/test_trace_verify.py`（新）
- `data/eval/behavior_trace_cases/*.json`（新）

### 交付物

- trace verify harness
- `trace_verify_report.json`

### 验收标准

- 至少一个 BFD trace 和一个 TCP trace 能通过
- trace verify 能报告：
  - next_state
  - ctx delta
  - timers/resource side effects
  - emit results
- trace verify 失败时能输出可追踪 diagnostics

---

## 阶段 6：在 BehaviorIR-lite 稳定后，再升格到 full FSMIR

### 目标

等 behavior 已经有：

- typed guard
- typed action
- ctx/timer/resource references
- trace verify

再升格为正式 `FSMIR`。

### 升格条件

建议至少满足以下条件后再做：

- `BehaviorIR-lite` 已被 codegen 真正消费
- `StateContextIR` 已能稳定提供被引用的 runtime slots
- trace verify 已经稳定跑通至少两个代表协议路径
- 大部分关键 transition 不再依赖 raw-only fallback

### 到 full FSMIR 时再做的内容

- richer guard language
- richer action language
- more explicit emit hooks
- timer semantics 完善
- procedure integration 完善

### 不建议提前做的内容

- 完整协议执行引擎
- 高复杂度调度/runtime framework
- 全量自动行为补全
- 一次性引入所有 protocol family 的全覆盖

---

## 9. 推荐的 pipeline 调整方式

## 9.1 新的阶段顺序

推荐把主线改为：

```text
process
-> classify
-> extract
-> merge
-> behavior
-> context
-> codegen
-> verify
-> trace_verify
```

说明：

- `merge`：继续负责 extraction objects 收敛 + MessageIR 主线
- `behavior`：负责从 `ProtocolStateMachine / ProcedureRule / TimerConfig / ErrorRule` 产出 `BehaviorIR-lite`
- `context`：负责把 `context clues + behavior refs` 物化为 `StateContextIR`
- `trace_verify`：独立于 `verify`，避免一开始把所有验证混在一起

## 9.2 不建议把所有逻辑塞回 merge

`merge` 当前已经承担了很多工作。

如果把：

- behavior lowering
- state context materialization
- trace verification preparation

继续都塞在 `merge` 里，后面会很难维护。

建议：

- `merge` 保留为 schema/message 收敛层
- `behavior/context` 作为显式后续阶段

---

## 10. 推荐的文件级重构清单

## 10.1 先不做大搬家，优先做低风险增量改造

### 保持不动或少动的文件

- `src/extract/message_ir.py`
- 现有 MessageIR 相关 tests
- 现有 message verify 主链

### 优先修改的文件

- `src/models.py`
- `src/extract/pipeline.py`
- `src/extract/state_context.py`
- `src/extract/codegen.py`
- `src/extract/verify.py`
- `run_extract_pipeline.py`

### 新增文件建议

- `src/extract/behavior_ir.py`
- `src/extract/trace_verify.py`
- `tests/extract/test_behavior_ir.py`
- `tests/extract/test_state_context_integration.py`
- `tests/extract/test_trace_verify.py`

## 10.2 等结构稳定后再拆目录

当 behavior/context 稳定后，再考虑把：

```text
src/models.py
```

按 IR 类型拆成：

```text
src/ir/message.py
src/ir/context.py
src/ir/behavior.py
```

当前不建议现在就拆，因为会引入大量 import churn，且对 thesis 阶段价值不大。

---

## 11. 推荐的产物与报告规范

为了避免后面 artifact 继续各写各的，建议从现在开始统一下列输出。

## 11.1 artifacts

每个文档输出：

```text
protocol_schema.json
message_ir.json
behavior_ir.json
state_context_ir.json
verify_report.json
trace_verify_report.json
```

## 11.2 merge / behavior / context summary

建议 summary 至少统一包含：

```json
{
  "message_ir_count": 0,
  "ready_message_ir_count": 0,
  "degraded_message_ir_count": 0,
  "state_context_count": 0,
  "ready_state_context_count": 0,
  "degraded_state_context_count": 0,
  "behavior_ir_count": 0,
  "ready_behavior_ir_count": 0,
  "degraded_behavior_ir_count": 0,
  "warnings": []
}
```

## 11.3 diagnostics 规范

建议所有新 IR 统一使用：

- `level`
- `code`
- `message`
- `source_pages`
- `source_node_ids`

不要让 behavior/context 线另起一套 diagnostics 风格。

---

## 12. 你现在最不应该做的几件事

### 12.1 不要把 MessageIR 和 StateContextIR 合并

因为它们解决的是不同层级问题：

- `MessageIR` = `msg.field`
- `StateContextIR` = `ctx.field`

一旦合并，后续行为层很难清晰区分：

- message-local field
- runtime-persistent field

### 12.2 不要直接做 full FSMIR

当前直接上 full FSMIR 的代价太高。

建议先做 `BehaviorIR-lite`，让行为层先可消费。

### 12.3 不要继续把行为语义无限塞进 MessageIR

那样会让 MessageIR 重新膨胀，并把边界打乱。

### 12.4 不要为了“全自动”而拒绝 patch lane

thesis 阶段更需要：

- 代表样例能跑通
- artifacts 可解释
- verify 闭环成立

允许少量 patch 是完全合理的。

### 12.5 不要让 target 文档继续替代 as-is 文档

从现在开始必须区分：

- 现在已经实现了什么
- 计划下一步实现什么

否则很容易在论文、README、进度记录里相互打架。

---

## 13. 我给你的最终建议：采用“演进式重构”，不要大爆炸

最推荐的执行策略是：

### 先做

1. 固定 as-is 基线
2. 修 FSM skeleton 结构问题
3. 引入 BehaviorIR-lite
4. 让 StateContextIR 由 behavior + clues materialize
5. 上 trace verify

### 再做

6. 让 behavior/context/codegen 三层真正联动
7. 再把 BehaviorIR-lite 升格为 full FSMIR

这个顺序的优点是：

- 不推翻 thesis 分支现有 MessageIR 主线
- 不浪费已建立的 StateContextIR 模型层
- 不让 FSMIR 一步过重
- 每一阶段都有清晰 artifact 和验收标准
- 非常适合 thesis 阶段做“架构演进 + 可验证样例”展示

---

## 14. 一句话版本

**当前 thesis 分支最稳的路线，不是立刻三条 IR 并行大推进，而是沿着已经跑通的 MessageIR 主线，先把行为层升级成 `BehaviorIR-lite`，再让 StateContextIR 从“概念层”进入“可消费 runtime schema”，最后通过 trace verify 把 `msg -> fsm(ctx, msg) -> ctx update -> emit msg` 的闭环真正跑起来。**

---

## 15. 最后给你的落地建议（按优先级排序）

### P0

- [ ] 写清 `ARCHITECTURE_AS_IS.md`
- [ ] 修 FSM skeleton 的 `(state, event)` 分组生成
- [ ] 固定 BFD/TCP 基线 artifact

### P1

- [ ] 引入 `BehaviorIR-lite` 模型
- [ ] 输出 `behavior_ir.json`
- [ ] 让 `DEGRADED_READY` 行为骨架也能进入 codegen

### P2

- [ ] 扩 StateContextIR 的 consumer-facing 字段
- [ ] 做 behavior-driven materialization
- [ ] 增加 patch lane

### P3

- [ ] 让 codegen 同时消费 MessageIR + BehaviorIR-lite + StateContextIR
- [ ] 增加 ctx/timer/resource skeleton

### P4

- [ ] 上 trace verify
- [ ] 先跑通一条 BFD trace + 一条 TCP trace

### P5

- [ ] 再考虑 full FSMIR
- [ ] 再考虑更丰富的 guard/action language


---

## 16. 按文件拆分的实施清单（便于直接开工）

下面这部分是为了把路线图再压缩成“能直接动手改文件”的层级。

### 16.1 `src/models.py`

#### 新增/扩展内容

- 为行为层新增：
  - `EventRef`
  - `ValueRef`
  - `GuardAtom`
  - `GuardExpr`
  - `ActionOp`
  - `TransitionIR`
  - `BehaviorIR`
- 为 `StateContextIR` 相关模型补 consumer-facing 字段：
  - `origin`
  - `mutability`
  - `storage_kind`
  - `producer_transition_ids`
  - `consumer_transition_ids`
- 若尚未统一，补全新 IR 的：
  - `diagnostics`
  - `normalization_status`
  - `source_pages`
  - `source_node_ids`

#### 设计要求

- 保持 Pydantic 风格与当前模型一致
- 不要在这里实现 lowering 逻辑，只放模型和极薄的 helper
- 新 IR 的字段命名尽量对齐 `MessageIR` / `StateContextIR` 既有风格

### 16.2 `src/extract/behavior_ir.py`（新）

#### 建议放的函数

- `lower_state_machine_to_behavior_ir(...)`
- `normalize_guard_expr(...)`
- `normalize_action_ops(...)`
- `collect_behavior_context_refs(...)`
- `apply_behavior_patches(...)`
- `serialize_behavior_ir(...)`

#### 输出目标

- `BehaviorIR` 对象
- `behavior_ir.json`
- diagnostics 列表
- 行为所需的 ctx/timer/resource 引用集合

### 16.3 `src/extract/state_context.py`

#### 建议新增/补强的函数

- `normalize_state_context_clues(...)`
- `materialize_state_context_from_behavior(...)`
- `merge_state_context_candidates(...)`
- `apply_state_context_patches(...)`
- `finalize_state_context_ir(...)`

#### 输出目标

- 最终 `StateContextIR`
- `state_context_ir.json`
- 行为引用到的 slot 到最终 context slot 的映射

### 16.4 `src/extract/pipeline.py`

#### 建议新增的阶段入口

- `run_behavior_stage(...)`
- `run_context_stage(...)`
- `run_trace_verify_stage(...)`

#### 阶段依赖顺序

- `merge` 结束后拿到：
  - `ProtocolSchema`
  - `MessageIR`
  - extraction 产物
- `behavior` 消费：
  - `state_machines`
  - `procedures`
  - `timers`
  - `errors`
- `context` 消费：
  - context clues
  - behavior refs
- `codegen` 消费：
  - `MessageIR`
  - `StateContextIR`
  - `BehaviorIR-lite`

### 16.5 `src/extract/codegen.py`

#### 建议新增/改造的函数

- `group_transitions_by_state_event(...)`
- `build_behavior_context(...)`
- `build_state_context_codegen_context(...)`
- `build_emit_stub_context(...)`
- `build_runtime_hook_context(...)`

#### 当前优先改造点

- 先把 FSM skeleton 从“按 transition 直铺”改成“按 state/event 分组 + guard chain”
- 在 BehaviorIR-lite 进入前，允许 raw condition/actions 作为注释输出
- BehaviorIR-lite 进入后，typed action/guard 优先生成，raw 部分退化为 stub/comment

### 16.6 `src/extract/templates/state_machine.c.j2`

#### 必改目标

- 外层按 `state`
- 内层按 `event`
- `(state, event)` 内部 guard 链按顺序展开
- 为 raw guard/action 预留 TODO/stub
- 为 timer/resource/emit 预留 hook/stub

### 16.7 `src/extract/verify.py` / `src/extract/trace_verify.py`

#### 建议拆分

- `verify.py`：继续负责 syntax / symbols / roundtrip
- `trace_verify.py`：专门负责行为闭环验证

#### trace verify 建议函数

- `load_trace_cases(...)`
- `build_trace_fixture(...)`
- `run_single_transition_trace(...)`
- `assert_trace_expected(...)`
- `write_trace_verify_report(...)`

### 16.8 `run_extract_pipeline.py`

#### 需要支持的新 stage

- `behavior`
- `context`
- `trace_verify`

#### CLI 行为建议

- 允许单独跑 `behavior`
- 允许单独跑 `context`
- 允许已有产物上直接跑 `trace_verify`
- summary 中输出三条 IR 的 ready/degraded/blocked 统计

### 16.9 `tests/`

#### 最少新增

- `tests/extract/test_behavior_ir.py`
- `tests/extract/test_state_context_integration.py`
- `tests/extract/test_trace_verify.py`
- `tests/extract/test_fsm_codegen_grouping.py`

#### 建议测试维度

- model/schema roundtrip
- lowering correctness
- patch merge correctness
- readiness 传播
- skeleton codegen 稳定性
- trace verify fixture correctness

---

## 17. 推荐的最小 PR 切分方式

为了降低回归风险，建议不要一次性开大 PR，而是按下面的最小切分推进。

### PR-1：文档与基线冻结

包含：

- `ARCHITECTURE_AS_IS.md`
- `ARCHITECTURE_TARGET.md`
- BFD/TCP baseline artifacts
- smoke tests

### PR-2：FSM skeleton 分组修复

包含：

- `codegen.py` 分组逻辑
- `state_machine.c.j2` 重写
- 对应 verify/test 修正

### PR-3：BehaviorIR-lite 模型 + lowering

包含：

- `src/models.py`
- `src/extract/behavior_ir.py`
- `behavior_ir.json`
- unit tests

### PR-4：StateContextIR consumer integration

包含：

- `state_context.py` 扩展
- behavior-driven materialization
- patch lane
- context integration tests

### PR-5：三层 codegen 接入

包含：

- context skeleton codegen
- behavior skeleton codegen
- emit/timer/resource stubs

### PR-6：trace verify

包含：

- `trace_verify.py`
- fixtures
- `trace_verify_report.json`
- 行为闭环测试

这个切法的好处是：

- 每个 PR 都有独立验收标准
- MessageIR 主链不会被一次性大改拖垮
- 如果某个阶段卡住，也不会阻塞全部进度

---

## 18. 最小可展示里程碑（适合 thesis 展示/答辩）

如果你需要的是“可讲、可跑、可验证”的阶段成果，建议把里程碑定义成下面三个。

### 里程碑 A：MessageIR + 稳定 FSM skeleton

展示点：

- MessageIR 已能对 BFD/TCP 生成结构层骨架
- FSM skeleton 不再因 `(state, event)` 多分支而不稳定
- syntax / roundtrip 仍成立

### 里程碑 B：BehaviorIR-lite + StateContextIR 联动

展示点：

- 行为层已从字符串升级成最小 typed IR
- StateContextIR 不再只是模型层，而是由 behavior + clues 共同 materialize
- codegen 已能同时生成 message/context/behavior 三层骨架

### 里程碑 C：trace verify 闭环

展示点：

- 至少一条 BFD trace 跑通
- 至少一条 TCP trace 跑通
- 能清晰展示：
  - 输入 message
  - event/predicate
  - transition
  - ctx 更新
  - timer/resource side effect
  - outgoing message

这个里程碑体系很适合论文里说明：

> 不是“全自动生成完整协议栈”，而是“建立了结构、状态、行为三层分离并可验证的协议软件生成架构”。
