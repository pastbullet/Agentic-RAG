# FSM IR 优化方案

## 1. 文档目标

本文档用于回答三个问题：

1. 当前 `FSM IR` 真正的问题是什么；
2. 在进入 `StateContextIR` 之前，`FSM` 这一层应该先优化到什么程度；
3. 后续如何让 `FSM -> Behavior -> StateContext` 形成一条可执行主线。

本文档刻意不把目标设成“直接做完整 runtime 级 FSMIR”，而是聚焦于**让当前状态机抽取结果变得可消费、可生成、可验证**。

## 2. 当前问题判断

## 2.1 核心问题不是“数量多”，而是“太文档化”

当前 `FSM` 相关产物已经能表达：

- state
- event
- transition
- condition
- actions

但这些信息大多仍然以接近文档摘要的形式存在，典型表现是：

- `condition` 主要还是自然语言字符串；
- `actions` 主要还是动作文本列表；
- 同一 `(state, event)` 下的多条转移缺少统一聚合结构；
- codegen 很难区分“真正可执行动作”和“只能作为注释保留的说明”。

这意味着当前 `FSM` 更像是“提取后的说明书”，而不是“可直接驱动代码生成的中间表示”。

## 2.2 当前 codegen 的主要痛点

从现有模板与生成链看，至少存在以下问题：

1. 同一 `(from_state, event)` 被拆成多条独立分支，容易在 `switch/case` 结构里生成重复 case。
2. guard 与 action 没有 typed 表达，模板只能拼字符串或退化为注释。
3. `next_state` 能生成，但“为什么跳转”“跳转前后要做什么”无法稳定落到 helper / action skeleton。
4. `FSM` 与 `MessageIR`、上下文对象之间还是弱连接，导致状态机代码很难引用真实输入对象与上下文字段。

## 2.3 现在直接做大而全的 `StateContextIR` 风险很高

如果在 `FSM` 仍然不可消费的情况下直接扩展 `StateContextIR`，会出现两个问题：

1. `StateContextIR` 只能停留在概念设计层，没有真实 consumer；
2. 很容易把“未来也许需要的上下文字段”一次性设计过多，最终变成空转大模型。

因此，更合理的顺序是：

```text
先修 FSM 可消费性
-> 再引入最小 Behavior bridge
-> 再由行为需求反推 StateContextIR
```

## 3. 优化目标

当前阶段的 `FSM IR` 不追求完整形式化，而是达到下面四个目标：

1. **可稳定 codegen**：不再产生重复 `case`、分支结构可预测；
2. **可最小执行**：核心 guard / action 至少能部分 typed 化；
3. **可引用外部对象**：能和 `MessageIR`、context slots 建立稳定引用；
4. **可进入验证**：后续能支撑 trace verify，而不是只停在文本展示。

## 4. 推荐优化路线

## 4.1 第一阶段：先修现有 FSM skeleton 的组织方式

这一阶段不改变抽取入口，主要修消费层。

### 目标

- 让现有状态机生成结果至少结构稳定；
- 解决重复 `case` 和分支散乱问题；
- 为后续接入 typed behavior 留出位置。

### 建议改造

1. 先按 `(from_state, event)` 对 transition 分组；
2. 每组内部再按 guard 顺序生成 `if / else if / else`；
3. 没有可解析 guard 的分支先保留为注释或 `TODO` 占位；
4. 所有 action 先统一落到 helper 调用位或注释块，不直接散在模板里。

### 推荐生成结构

```text
switch (ctx->state)
  case STATE_X:
    switch (event)
      case EVENT_Y:
        if (guard_1) { action_block_1; set_state(...); }
        else if (guard_2) { action_block_2; set_state(...); }
        else { unsupported_branch_note(); }
```

这一改造的价值不是“更漂亮”，而是让 codegen 具备**稳定消费单位**：一组 `(state, event)` 成为一个生成节点。

## 4.2 第二阶段：补一个最小 `BehaviorIR-lite`

在现有 `FSM` 和完整 `FSMIR` 之间，建议插入一层轻量行为桥接，而不是直接一步到位。

### `BehaviorIR-lite` 的作用

- 把自然语言 `actions` 里最常见、最关键的一小部分动作 typed 化；
- 把 guard 从纯文本，升级成“部分可解析 + 允许 raw fallback”的形式；
- 让 `FSM` 生成不再只是“状态跳转表”，而是“带最小行为语义的转移块”。

### v1 建议先支持的 typed action

- `set_state`
- `emit_message`
- `start_timer`
- `cancel_timer`
- `update_field`

其余无法稳定识别的动作仍保留：

- `raw_text_action`

### guard 的建议策略

不追求一次性解析完整布尔表达式，v1 只支持最常见的小闭集：

- event attribute comparison
- context field comparison
- flag presence check
- timeout / timer fired

无法稳定识别的 guard 保留为：

- `raw_text_guard`

这样做的关键在于：**先把高频、通用、能消费的部分抽出来，而不是等待“完整理解后再开始”**。

## 4.3 第三阶段：由行为需求倒推 `StateContextIR`

`StateContextIR` 不应该脱离行为层独立设计，而应从下列来源物化：

1. `BehaviorIR-lite` 中被动作或 guard 引用的 context slot；
2. 文档中明确出现的连接变量、计时器、资源句柄；
3. 必要的人工 patch。

这样得到的 `StateContextIR` 更有约束，也更接近真实 consumer 需求。

## 5. 建议的数据边界

## 5.1 当前阶段不建议直接追求的能力

以下能力可以保留到更后阶段：

- 完整层次化状态机
- 并发 region / orthogonal state
- 完整 AST 级 guard expression
- 可执行 runtime scheduler
- 完整自动互操作闭环

这些能力都不是当前 thesis 主线的必要前置条件。

## 5.2 当前阶段必须优先具备的能力

### A. transition grouping

最小单位应从“单条 transition 直接模板展开”升级为：

```text
StateEventBlock {
  from_state,
  event,
  branches[]
}
```

其中 `branches[]` 保存：

- guard
- actions
- next_state
- diagnostics

### B. typed / raw 双通道

guard 和 action 不应只有一种表达方式，而应同时保留：

- typed lane：供 codegen / verify 消费；
- raw lane：保留原文信息与审计线索。

### C. diagnostics 与 readiness

每个 branch 或 block 应显式记录：

- 是否成功 typed 化；
- 哪些动作只能降级为注释；
- 哪些 guard 无法稳定解析；
- 当前结果是 `READY`、`DEGRADED_READY` 还是 `BLOCKED`。

这和当前 `MessageIR` 的思路应保持一致。

## 6. 推荐的 FSM IR v1 结构

下面不是最终 class 定义，而是 v1 的推荐形态：

```text
FSMIRv1
  states[]
  events[]
  blocks[]
  diagnostics[]
  normalization_status

StateEventBlock
  from_state
  event
  branches[]

TransitionBranch
  guard_typed?
  guard_raw?
  actions_typed[]
  actions_raw[]
  next_state?
  notes[]
  readiness
```

这个结构比当前“transition 平铺 + string condition/actions”更适合后续三件事：

1. codegen；
2. trace verify；
3. context materialization。

## 7. 与现有三层架构的关系

## 7.1 和 `MessageIR` 的关系

`FSM IR` 不负责表达报文字节布局，也不应再把消息结构语义塞回来。

它只需要能做到：

- 引用事件关联消息；
- 引用动作涉及的消息发送或接收；
- 给 codegen 提供流程控制骨架。

## 7.2 和 `StateContextIR` 的关系

`FSM IR` 不拥有上下文对象，但会引用上下文。

也就是说：

- `FSM` 负责“什么时候检查 / 修改什么”；
- `StateContextIR` 负责“被检查 / 被修改的对象是什么”。

## 7.3 和 verify 的关系

后续 trace verify 的最小执行单元，更适合基于 `StateEventBlock` 与 `TransitionBranch` 来做，而不是基于散乱字符串。

## 8. 推荐实施顺序

## Phase 1：修 FSM codegen 结构

### 任务

- 引入 `(state, event)` 分组；
- 修复重复 `case`；
- 统一 branch 生成逻辑；
- 为 typed guard/action 预留模板接口。

### 交付物

- 更稳定的 `state_machine.c` skeleton；
- 最小回归测试；
- 生成结果对比样例。

## Phase 2：引入 `BehaviorIR-lite`

### 任务

- 增加 typed action / raw fallback；
- 增加 typed guard / raw fallback；
- 在 lowering 过程中生成 `StateEventBlock`。

### 交付物

- `behavior_ir` 模块；
- 新的 lowering 流程；
- 带 diagnostics 的行为产物。

## Phase 3：最小 `StateContextIR` 物化

### 任务

- 从 guard/action 引用中抽取 context slot；
- 合并文档 clues；
- 建立最小 `ctx` schema。

### 交付物

- `StateContextIR v1`；
- context codegen skeleton；
- `FSM -> context` 引用对齐结果。

## Phase 4：接入 trace verify

### 任务

- 基于 `StateEventBlock` 跑最小 trace；
- 验证状态变化、动作触发、字段更新；
- 记录降级分支。

### 交付物

- trace fixtures；
- verify runner；
- 基础行为闭环。

## 9. 当前最推荐的决策

基于当前仓库状态，**不建议立刻先做大而全的 `StateContextIR` 扩展**。

更合理的顺序是：

1. 先把 `FSM skeleton` 从“字符串堆叠”修成“可消费 block”；
2. 再引入最小 `BehaviorIR-lite`；
3. 最后由行为需求倒推出 `StateContextIR v1`。

一句话概括就是：

> 当前 FSM 的主要问题不是“太多”，而是“太文档化、太字符串化、太难消费”；因此下一步最应该做的是先把它变成可生成、可验证、可衔接上下文的中间层，而不是直接扩一个还没有 consumer 的大状态上下文模型。

