# thesis 分支可执行重构路线图（主线收敛版）

> 这份方案以你最新补充的 thesis 分支现状为准，核心目标不是“三条 IR 并行铺开”，而是把实现收敛成一条明确、可执行、可验收的主线：
>
> **先稳 `MessageIR`，再修 `FSM skeleton`，再上 `BehaviorIR-lite`，随后让 `StateContextIR` 由行为需求倒推出最小集合，最后用 `trace verify` 建立闭环。**

---

## 1. 结论先行

当前项目的**终态架构方向可以保留**：

```text
MessageIR + StateContextIR + BehaviorIR-lite/FSMIR + trace verify
```

但这必须被理解为：

- **target architecture**：论文和系统的目标架构
- **不是当前立即并行展开的 implementation plan**

按 thesis 分支当前成熟度，更合理的实现顺序是：

```text
Phase 0  冻结当前基线
Phase 1  稳住 MessageIR 基线 + 修 FSM skeleton codegen
Phase 2  引入 BehaviorIR-lite
Phase 3  由 BehaviorIR-lite + context clues materialize StateContextIR
Phase 4  建 trace verify 闭环
Phase 5  视进度再考虑升格为 full FSMIR
```

一句话概括：

> **当前最成熟、最真实跑在主链上的仍然是 `MessageIR`；行为层才是下一阶段最该补上的可消费 IR；`StateContextIR` 应该由行为真实需求倒推出最小运行时 schema，而不是先独立扩张。**

---

## 2. 当前阶段的正确定位（as-is）

### 2.1 当前三层成熟度并不对齐

按你最新补充，当前系统更准确的状态是：

- **结构层**：已有真实主链，`MessageIR` 已进入真实消费链
- **状态层**：模型已存在，但还没进入真实消费链
- **行为层**：还停在“拓扑 + 注释级语义”

也就是：

```text
结构层：最成熟
状态层：刚起步
行为层：尚未 IR 化完成
```

所以当前不适合：

- 把 `MessageIR / StateContextIR / FSMIR` 当成同成熟度对象并行推进
- 把 thesis 讲成“完整协议栈自动生成”

更适合的表述是：

> **我们正在把协议软件生成问题收敛成“结构层 / 状态层 / 行为层分离”，并逐步建立从消息解析到行为闭环的可验证骨架。**

### 2.2 当前主线应该怎么描述

当前系统更贴切的 as-is 架构可以写成：

```text
Extraction Layer:
  ProtocolMessage / ProtocolStateMachine / ProcedureRule / TimerConfig / ErrorRule

Implementation Layer:
  MessageIR            # 已进入真实主链
  StateContextIR       # 已有模型层，尚未进入真实消费链

Behavior Layer:
  ProtocolStateMachine + condition/actions strings + FSM skeleton codegen
```

这比“当前已三层 IR 并行建设”更贴合 thesis 分支现状。

---

## 3. thesis 主线目标（重新收敛后的表述）

建议 thesis 目标从“完整协议栈自动生成”收敛为：

> **构建一个结构层、状态层、行为层分离，并能通过代表性协议样例建立可验证闭环的协议软件骨架生成架构。**

更具体一点：

```text
文档 -> 结构抽取 -> MessageIR
                 -> BehaviorIR-lite
                 -> StateContextIR
                 -> code skeletons
                 -> trace verify
                 -> 协议软件骨架闭环
```

### 3.1 论文/答辩最合适的一句话

不建议讲：

> 自动生成完整协议栈

建议讲：

> **我们构建了结构层、状态层、行为层分离的协议软件生成架构，并通过代表性协议样例实现了从消息解析、事件导出、状态转移、上下文更新到外发消息的可验证闭环。**

---

## 4. 各层职责边界（必须先固定）

这是后续不漂移的关键。

## 4.1 MessageIR

`MessageIR` 只负责：

- wire format
- frame / packet layout
- field / rule / section / tail
- bitfield / mixed layout
- option / tlv
- `pack / unpack / validate` 所需的结构语义

它回答的是：

> **报文在 wire 上长什么样？**

`MessageIR` 不负责：

- runtime state
- queue / timer / resource
- 行为语义
- 跨消息上下文

### 设计要求

- 冻结顶层 schema 边界
- 允许继续修 layout、diagnostics、少量结构缺口
- 不再继续吸收 runtime / behavior 语义

一句话：

> **MessageIR 是结构层骨架，不是行为层垃圾桶。**

## 4.2 StateContextIR

`StateContextIR` 只负责：

- `ctx.field`
- `ctx.timer`
- `ctx.resource`
- 持久运行时状态
- queue / buffer / timer / resource 的最小 runtime schema

它回答的是：

> **行为层真正读写的运行时对象是什么？**

`StateContextIR` 不负责：

- 定义报文结构
- 替代 `MessageIR`
- 定义完整行为逻辑

### 设计要求

- 必须独立于 `MessageIR`
- 必须由行为需求约束
- 只物化真正会被 guard/action 使用的 slot

一句话：

> **StateContextIR 是 behavior-driven 的 runtime schema，而不是先验设计的一大堆上下文名词。**

## 4.3 BehaviorIR-lite

`BehaviorIR-lite` 负责：

- event
- guard
- action
- emit
- transition

它回答的是：

> **收到什么输入、在什么条件下、做什么动作、转到什么状态？**

`BehaviorIR-lite` 允许：

- typed IR 子集
- raw fallback
- diagnostics / evidence

但不要求：

- 一开始就支持完整 DSL
- 一开始就变成 full FSMIR

一句话：

> **BehaviorIR-lite 是从注释级行为语义过渡到可消费 typed IR 的桥接层。**

## 4.4 trace verify

`trace verify` 不等于完整 runtime。它只负责验证最小闭环：

```text
decode
-> derive event / predicate
-> run one transition
-> assert ctx delta
-> assert emit message / timer effect / resource effect
```

一句话：

> **trace verify 是闭环验证器，不是完整协议执行引擎。**

---

## 5. 设计原则（必须直接采纳）

## 5.1 主线收敛：先 MessageIR，后行为，再上下文

实现顺序固定为：

1. 稳 `MessageIR`
2. 修 `FSM skeleton`
3. 上 `BehaviorIR-lite`
4. 由行为需求倒推出 `StateContextIR`
5. 用 `trace verify` 建闭环

## 5.2 FSM skeleton 稳定性优先级最高

当前最现实的阻塞不是“语义还不够多”，而是：

- skeleton codegen 本身不稳定
- 同一 `(state, event)` 下多条件分支导致重复 `case`

所以在 `BehaviorIR-lite` 之前，必须先把：

- dispatcher 结构
- transition 分组
- fallback/default path
- case emission 策略

做稳定。

## 5.3 BehaviorIR-lite 先做小闭集

v1 只建议支持少量闭集操作：

- `set_state`
- `set_ctx_field`
- `start_timer`
- `cancel_timer`
- `emit_message`
- `drop_message`
- `raise_error`

无法结构化的部分先保留：

- `raw_text`
- `evidence`
- `diagnostics`

## 5.4 StateContextIR 必须 behavior-driven

不要单独开一条“大型 context 抽取线”。

`StateContextIR` 的 materialization 应来自两部分：

1. **behavior refs**：guard/action 真正引用到的 `ctx.slot`
2. **context clues**：文档或抽取结果中明确提到的 timer/resource/state field 线索

最终只保留最小集合。

## 5.5 patch lane 升级为正式机制

事件名、ctx slot 名、timer/resource 分类、emit family 这些修正，不能继续散落在各处。

应建立正式 patch lane：

- 统一入口
- 可追踪来源
- 可审计
- 可在 lowering/materialize 阶段被复用

## 5.6 event derivation 必须单独设计

这条链里最容易被低估的是：

```text
decode -> derive event/predicate -> transition -> ctx update -> emit
```

其中 `derive event/predicate` 不能隐含在模板或测试里，必须显式设计成单独机制。

## 5.7 pipeline 边界必须硬化

- `merge` 继续负责 message/schema 主线
- `behavior lowering`
- `context materialization`
- `trace_verify`

应该成为分层阶段，不要重新塞回一个大阶段里。

---

## 6. 新的目标架构（target architecture）

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

-> lowering / normalization
   - MessageIR
   - BehaviorIR-lite
   - StateContextIR

-> codegen
   - message codegen           (consumes MessageIR)
   - behavior skeleton codegen (consumes BehaviorIR-lite)
   - context struct codegen    (consumes StateContextIR)

-> trace verify
   - decode
   - derive event/predicate
   - run one transition
   - assert ctx delta
   - assert emit/timer/resource effects

-> protocol software skeleton
```

### 6.1 一个明确的边界约束

不要做“大一统 codegen 黑盒”。

推荐改成：

- **message codegen** 消费 `MessageIR`
- **behavior skeleton codegen** 消费 `BehaviorIR-lite`
- **context struct codegen** 消费 `StateContextIR`
- **trace verify** 再把三者组起来

这样每一层都有清晰 consumer，便于渐进推进。

---

## 7. 可执行实施方案（五阶段）

## Phase 0：冻结当前基线

### 目标

把当前 thesis 分支的真实基线固定下来，避免边做边漂。

### 要做的事

1. 固定代表性协议样例：至少 `BFD`、`TCP`
2. 固定当前 `MessageIR` 产物基线：
   - `message_ir.json`
   - `protocol_schema.json`
   - `verify_report.json`
3. 固定现有测试状态：
   - message 相关单测
   - codegen / verify 相关单测
4. 明确两份文档：
   - `ARCHITECTURE_AS_IS.md`
   - `ARCHITECTURE_TARGET.md`
5. 写清 thesis 主线不再是“完整协议栈生成”

### 交付物

- 基线样例清单
- 现状文档与目标文档
- golden artifacts 引用路径

### 验收标准

- BFD/TCP 当前产物可稳定重放
- 文档中不再混淆 as-is 与 target

---

## Phase 1：稳住 MessageIR + 修 FSM skeleton codegen

### 目标

让结构层保持稳定，同时让当前行为骨架至少成为一个**稳定、可编译、可解释的 dispatcher skeleton**。

### 为什么这一步优先

当前行为层最大的实际阻塞不是 typed IR 不存在，而是：

- skeleton 输出不稳定
- `(state, event)` 多条件分支导致重复 `case`

### 要做的事

#### 1. MessageIR 侧

- 冻结 `MessageIR` 顶层 schema
- 只允许修：
  - layout bug
  - readiness/diagnostics bug
  - 少量结构缺口
- 不再继续把 runtime/behavior 语义塞入 message 层

#### 2. FSM skeleton 侧

把当前 `ProtocolStateMachine + condition/actions strings` 的 codegen 修到稳定：

- 先按 `(state, event)` 分组 transition
- 同一 `(state, event)` 只生成**一个 dispatcher 分支**
- 多 guard 分支在分支内部做：
  - `if / else if / else`
  - 或 predicate helper 调用
- `default` / `unhandled` 路径显式存在
- skeleton 里允许保留 comment/stub，但不能再生成重复 `case`

### 推荐生成策略

不要再直接把每个 transition 展开成一个 `case`。推荐改成：

```text
switch (state) {
  case S1:
    switch (event) {
      case E1:
        if (guard_1) { ... }
        else if (guard_2) { ... }
        else { ... }
        break;
    }
}
```

或者：

```text
case E1:
  return dispatch_state_S1_event_E1(...);
```

即：

- **case 唯一**
- **条件分支下沉**

### 交付物

- 稳定的 state/event dispatcher skeleton
- BFD/TCP 上不再出现 duplicate case value
- skeleton 的 fallback/stub 语义说明文档

### 验收标准

- BFD/TCP 的 FSM skeleton 至少能稳定生成
- 生成代码不再因重复 `case` 而失败
- 结构骨架足够稳定，能为下一步 `BehaviorIR-lite` 提供消费面

---

## Phase 2：引入 BehaviorIR-lite

### 目标

把当前行为层从：

- `condition: str`
- `actions: list[str]`

推进到：

- **最小 typed IR + raw fallback**

但暂时不追求 full FSMIR。

### 设计原则

- 不推翻 `ProtocolStateMachine`
- 从当前抽取对象 lower 出行为 IR
- typed 部分可消费
- 无法结构化的部分可保留 raw fallback

### 建议的数据模型（v1）

```text
BehaviorMachineIR
  - machine_name
  - states
  - events
  - transitions
  - normalization_status
  - diagnostics

TransitionIR
  - source_state
  - event
  - guard
  - actions
  - target_state
  - evidence
  - normalization_status
  - diagnostics

GuardExpr
  - kind
  - lhs
  - op
  - rhs
  - raw_text

ActionOp
  - kind
  - target
  - value
  - message_ref
  - raw_text
```

### GuardExpr v1 推荐支持的 kind

- `always`
- `msg_field_cmp`
- `ctx_field_cmp`
- `timer_expired`
- `resource_nonempty`
- `raw`

### ActionOp v1 推荐支持的 kind

- `set_state`
- `set_ctx_field`
- `start_timer`
- `cancel_timer`
- `emit_message`
- `drop_message`
- `raise_error`
- `raw`

### readiness 建议

这里不建议为了形式统一，马上把 behavior/context 的 degraded path 复杂化。

推荐做法：

- enum 层可沿用现有体系
- 但真正的 consumer gating 只对已实现路径负责
- 在 trace/codegen 真正消费 degraded 之前，不要为了“统一”引入额外复杂度

也就是：

> **有 degraded consumer 再消费 degraded；没有就先别把语义复杂化。**

### 交付物

- `BehaviorIR-lite` 模型
- 从 `ProtocolStateMachine` lowering 到 `BehaviorIR-lite` 的逻辑
- 最小 typed IR + raw fallback
- 一批代表性 transition 样例

### 验收标准

- 至少一条 BFD/TCP 行为线能 lower 出 `BehaviorIR-lite`
- 已支持的小闭集 action 能被 codegen/verify 识别
- 未识别动作会落到 raw fallback，而不是导致整条链阻塞

---

## Phase 3：由 BehaviorIR-lite + context clues materialize StateContextIR

### 目标

不再把 `StateContextIR` 当成独立大抽取线，而是：

- 从行为真实引用反推
- 再用文档/context clues 做补强
- 最终产出最小 runtime schema

### materialization 规则

`StateContextIR` 的 slot 来源分两类：

#### A. 行为驱动来源

来自 guard/action 的真实引用：

- `ctx.field`
- `ctx.timer`
- `ctx.resource`

这些是**必须 materialize** 的。

#### B. clues 驱动来源

来自文档或抽取结果里明确存在的运行时线索：

- state variable
- queue/buffer
- timer
- retransmission slot
- sequence/window style field

这些可以作为候选补强，但不能无限扩张。

### 最小 StateContextIR v1 应包含

```text
StateContextIR
  - fields
  - timers
  - resources
  - rules/invariants (minimal)
  - normalization_status
  - diagnostics
```

并给每个 slot 加最小 provenance：

- `derived_from_behavior`
- `derived_from_clue`
- `patched`

### patch lane 在这里正式接入

建议建立统一 patch lane，用于：

- event canonicalization
- ctx slot canonicalization
- timer/resource typing
- emit family mapping
- behavior/context alias merge

### 交付物

- `StateContextIR` materialization 逻辑
- `behavior refs + clues -> state_contexts` 合并规则
- 正式 patch lane 入口

### 验收标准

- 至少一个代表性协议的 `StateContextIR` 可由行为真实需求驱动产生
- context slot 不再“凭感觉无限增长”
- patch 不再零散散落在各处

---

## Phase 4：建立 trace verify 闭环

### 目标

建立 thesis 最关键的闭环验证：

```text
msg
-> decode
-> derive event/predicate
-> run one transition
-> ctx update
-> emit msg / timer effect / resource effect
```

### 这一步的价值

这不是完整 runtime，但它能证明：

- `MessageIR` 有实际消费价值
- `BehaviorIR-lite` 不是空模型
- `StateContextIR` 真进入了真实闭环

### 要做的事

#### 1. 单独设计 event derivation

新增显式层：

```text
DecodedMessage -> EventDerivation -> DerivedEvent + Predicates
```

不要把 event derivation 混在模板、测试或注释里。

#### 2. transition runner / harness

新增单步行为执行器：

```text
input: decoded msg + current ctx
output: next state + ctx delta + emitted effects
```

#### 3. trace assertions

最小断言包括：

- 派生出的 event 是否正确
- guard 命中是否正确
- target_state 是否正确
- `ctx delta` 是否符合预期
- 是否 emit 正确 message
- timer/resource effect 是否正确

### 交付物

- `event derivation` 机制
- `trace verify harness`
- BFD/TCP 代表性 trace case

### 验收标准

- 至少能跑通 1~2 条代表性闭环 trace
- trace 的失败原因能定位到：
  - decode
  - event derivation
  - guard
  - action
  - context
  - emit

---

## Phase 5：视进度再升格为 full FSMIR（可选）

### 目标

当以下条件成立时，再考虑从 `BehaviorIR-lite` 升格：

- dispatcher skeleton 稳定
- typed guard/action 已有基本闭集
- `StateContextIR` 已有真实 consumer
- trace verify 已经能跑代表样例

### 再考虑补的内容

- richer guard language
- richer action language
- timer hooks 深化
- emit hooks 深化
- 多步 trace
- 更正式的 FSMIR

### 注意

这一步不应作为 thesis 主线前置条件。

也就是：

> **thesis 主线闭环成立，并不等于 full FSMIR 已经成熟。**

---

## 8. 建议的模块与文件改造方案

下面是一个尽量低侵入、但边界清晰的模块切分方案。

## 8.1 保留并继续使用的核心文件

- `src/models.py`
- `src/extract/pipeline.py`
- `src/extract/codegen.py`
- `src/extract/verify.py`
- `src/extract/templates/state_machine.c.j2`
- `src/extract/templates/state_machine.h.j2`

## 8.2 建议新增模块

### A. 行为 lowering

- `src/extract/behavior_ir.py`
  - `BehaviorMachineIR / TransitionIR / GuardExpr / ActionOp`
- `src/extract/behavior_lowering.py`
  - `ProtocolStateMachine -> BehaviorIR-lite`

### B. event derivation

- `src/extract/event_derivation.py`
  - `DecodedMessage -> DerivedEvent + Predicates`

### C. context materialization

- `src/extract/state_context_materialize.py`
  - `Behavior refs + context clues -> StateContextIR`

### D. patch lane

- `src/extract/patch_lane.py`
  - event / slot / timer / resource / emit family canonicalization

### E. trace verify

- `src/extract/trace_verify.py`
  - 单步 transition 闭环验证 harness

## 8.3 建议拆分 codegen consumer（不必一开始拆文件，但要拆消费边界）

即使暂时不拆成多个 codegen 文件，也建议在职责上分开：

- `generate_message_code(...)`       ← 消费 `MessageIR`
- `generate_behavior_skeleton(...)`  ← 消费 `BehaviorIR-lite`
- `generate_context_struct(...)`     ← 消费 `StateContextIR`

然后：

- `trace verify` 再把三者组装起来验证

---

## 9. pipeline 改造建议（边界硬化）

不建议把所有东西继续塞回一个巨大的 `merge` 或 `verify` 阶段。建议改成：

```text
classify
-> extract
-> merge                  # 继续负责 message/schema 主线
-> behavior_lower         # 新阶段
-> context_materialize    # 新阶段
-> codegen                # 结构/行为/上下文分别消费
-> trace_verify           # 新阶段
-> verify                 # 继续保留 syntax/symbol/roundtrip 等
```

### 9.1 解释

- `merge`：继续负责 message/schema 汇总与归一，不回收行为逻辑
- `behavior_lower`：专门把注释级行为转为最小 typed IR
- `context_materialize`：专门从 behavior refs + clues 产出 `StateContextIR`
- `trace_verify`：专门验证行为闭环
- `verify`：继续保留现有 syntax/symbol/roundtrip 检查

这样可以避免：

- 语义混层
- 责任漂移
- “修一个阶段，影响整个大阶段”的耦合问题

---

## 10. patch lane 的正式机制设计

## 10.1 为什么要正式化

目前 event 名、ctx slot 名、timer/resource 分类、emit family 这些修正如果继续零散存在，会出现：

- 同义项重复修
- 不同 lowering 阶段结果不一致
- 难以追踪为什么某个名字被改写

## 10.2 正式 patch lane 应有的能力

### patch 输入类型

- `event_alias_patch`
- `ctx_slot_alias_patch`
- `timer_classification_patch`
- `resource_classification_patch`
- `emit_family_patch`

### patch 元数据

每条 patch 至少带：

- `source`
- `reason`
- `evidence`
- `scope`
- `applies_to_stage`

### patch 使用位置

- `behavior_lowering`
- `state_context_materialize`
- `event_derivation`

### 原则

- patch 是**显式机制**，不是临时字符串替换
- patch 要能被测试覆盖
- patch 结果要能进 diagnostics / provenance

---

## 11. 每阶段的验收清单（面向 thesis）

## Phase 0 完成的标志

- as-is / target 文档分离
- BFD/TCP 基线样例与产物固定

## Phase 1 完成的标志

- `MessageIR` 顶层边界稳定
- FSM skeleton 不再生成 duplicate case value
- dispatcher skeleton 可稳定重放

## Phase 2 完成的标志

- `BehaviorIR-lite` 已可从现有 state machine lowering 出来
- 小闭集 action 已有 typed IR
- raw fallback 可落地、不阻塞主链

## Phase 3 完成的标志

- `StateContextIR` 可由行为真实引用 materialize
- context 不再靠手工想象扩张
- patch lane 已正式接入

## Phase 4 完成的标志

- 至少一个协议样例能跑通：
  - decode
  - derive event
  - run one transition
  - assert ctx delta
  - assert emit / timer / resource effect

## Phase 5 完成的标志（可选）

- `BehaviorIR-lite` 已具备升格 full FSMIR 的必要前提
- 不是 thesis 主线必需条件

---

## 12. 推荐的 PR 切分方式

## PR1：基线冻结 + 文档收敛

内容：

- `ARCHITECTURE_AS_IS.md`
- `ARCHITECTURE_TARGET.md`
- 样例协议清单
- 当前 artifacts/testing 基线固定

## PR2：FSM skeleton 稳定化

内容：

- 解决 duplicate case value
- 重构 `(state, event)` 分组 dispatch
- 生成稳定 dispatcher skeleton

## PR3：BehaviorIR-lite 模型与 lowering

内容：

- 新增 `BehaviorMachineIR / TransitionIR / GuardExpr / ActionOp`
- 从 `ProtocolStateMachine` lowering
- raw fallback / diagnostics

## PR4：StateContextIR materialization + patch lane

内容：

- 行为驱动的 context materialization
- context clues merge
- patch lane 正式接入

## PR5：codegen consumer 分层

内容：

- message / behavior / context 三个 consumer 明确化
- skeleton 生成边界清晰化

## PR6：event derivation + trace verify

内容：

- 显式 event derivation
- 单步 transition harness
- 代表性 trace cases

这个切分的好处是：

- 每个 PR 都有清晰目标
- 每个 PR 都有独立验收标准
- 不需要 big-bang 式一次性重写

---

## 13. 明确哪些事情暂时不要做

下面这些事情当前阶段不建议提前展开：

1. 不追完整协议栈自动生成
2. 不先做 full FSMIR DSL
3. 不单独开一条大型 context 抽取线
4. 不把 runtime/behavior 继续塞进 MessageIR
5. 不做“大一统 codegen 黑盒”
6. 不把所有新逻辑重新塞回 `merge`
7. 不把 `DEGRADED_READY` 机械性推广到没有真实 consumer 的阶段

---

## 14. 方案压缩版（一段话）

> **以已经跑通的 `MessageIR` 作为稳定结构层，优先修复 `FSM skeleton` 的生成稳定性；随后引入只支持小闭集操作的 `BehaviorIR-lite`；再由行为真实需求与 context clues 共同倒推出最小 `StateContextIR`；最后通过 `trace verify` 建立 `msg -> event/predicate -> transition -> ctx update -> emit msg` 的可验证闭环。**

---

## 15. 最终建议

如果只保留一句决策建议，那就是：

> **方向保留三层终态，但实现必须收敛成一条主线：先稳 MessageIR，再修 FSM skeleton，再上 BehaviorIR-lite，随后让 StateContextIR 由行为需求倒推出最小集合，最后用 trace verify 建闭环。**

这条主线既贴合 thesis 分支当前代码成熟度，也能最大化提升论文叙事、工程可执行性和闭环验证强度。
