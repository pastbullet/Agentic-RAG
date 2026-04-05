# 当前架构方向评估（贴合 thesis 分支现状）

## 1. 结论先行

当前项目想走向的终态架构：

```text
MessageIR + StateContextIR + FSMIR
```

这个方向本身是**可行的**，而且也是合理的。

但是，按 thesis 分支当前真实代码状态，它更适合被理解成：

**target architecture**

而不是下一步就能按三条主线并行落地的：

**as-is implementation plan**

更准确地说，当前仓库的成熟度并不是三层并行，而是：

- `MessageIR`：已经进入真实主链
- `StateContextIR`：已经有模型层与最小 normalization，但还没进入真实主链
- `FSMIR`：还没有正式成型，当前更接近 `ProtocolStateMachine + skeleton codegen`

因此，当前最合理的推进方式不是三层同时展开，而是：

1. 稳住 `MessageIR`
2. 补一层 `BehaviorIR-lite`
3. 再让 `StateContextIR` 由行为需求倒推出最小集合
4. 最后再把行为层提升成正式 `FSMIR`

---

## 2. 当前 thesis 分支已经真实落地的部分

### 2.1 协议提取主链已经成立

当前仓库的协议提取主线已经在 [src/extract/pipeline.py](/Users/zwy/毕设/Kiro/src/extract/pipeline.py) 中稳定存在：

```text
classify -> extract -> merge -> codegen -> verify
```

其中，`merge` 阶段已经会：

- lower message 到 `MessageIR`
- 输出 `message_ir.json`
- 统计 `ready_message_ir_count`
- 继续进入 `codegen / verify`

这意味着：

**MessageIR 不是概念，而是已经进入真实流水线。**

### 2.2 MessageIR 已经是当前系统最成熟的实现层 IR

当前 [src/models.py](/Users/zwy/毕设/Kiro/src/models.py) 和 [src/extract/message_ir.py](/Users/zwy/毕设/Kiro/src/extract/message_ir.py) 已经支撑：

- 固定字段
- packed bitfield
- mixed layout
- variable length
- optional / composite tail
- `ArchetypeContribution -> MessageIR`
- 最小 `Option/TLV IR v1`

而且 `MessageIR` 已经接入：

- [src/extract/codegen.py](/Users/zwy/毕设/Kiro/src/extract/codegen.py)
- [src/extract/verify.py](/Users/zwy/毕设/Kiro/src/extract/verify.py)

对 BFD 和 TCP 都已经有真实验证产物。

### 2.3 `DEGRADED_READY` 现在已经是现实，不再是目标设计

当前 [src/models.py](/Users/zwy/毕设/Kiro/src/models.py) 中的 `NormalizationStatus` 已经是四态：

- `DRAFT`
- `READY`
- `DEGRADED_READY`
- `BLOCKED`

因此，任何还在假设“系统仍然只有三态或没有 degraded path”的架构评估，都已经有一点落后于当前代码。

### 2.4 StateContextIR 已存在，但只在模型层

当前 thesis 分支里已经存在：

- [src/models.py](/Users/zwy/毕设/Kiro/src/models.py)
  - `StateContextIR`
  - `ContextFieldIR`
  - `ContextTimerIR`
  - `ContextResourceIR`
  - `ContextRuleIR`
  - `ProtocolSchema.state_contexts`
- [src/extract/state_context.py](/Users/zwy/毕设/Kiro/src/extract/state_context.py)
  - 最小 normalization
  - `READY / DEGRADED_READY / BLOCKED`
  - TCP 和 generic 示例

但它当前还没有进入：

- extraction
- merge
- codegen
- verify

也就是说：

**StateContextIR 已存在，但还不是系统真实产物。**

---

## 3. 当前最薄弱的地方不是 MessageIR，而是行为层

当前的行为线实际还是：

- `ProtocolStateMachine`
- `ProtocolTransition`
- `condition: str`
- `actions: list[str]`

也就是说，现在的行为层本质上还是：

**topology + annotation**

而不是：

- typed guard
- typed action
- typed runtime reference
- 可执行的 `FSMIR`

对应代码可见：

- [src/models.py](/Users/zwy/毕设/Kiro/src/models.py)
- [src/extract/codegen.py](/Users/zwy/毕设/Kiro/src/extract/codegen.py)
- [src/extract/templates/state_machine.c.j2](/Users/zwy/毕设/Kiro/src/extract/templates/state_machine.c.j2)

这也是为什么当前 FSM codegen 还不稳定。

例如现在已经暴露出一个很直接的问题：

- 同一 `state + event` 下的多个条件分支会被模板直接展开成多个 `case`
- 结果在 TCP/BFD 上都能出现 `duplicate case value`

所以当前最该补的不是“给 FSM 再加更多注释信息”，而是：

**行为层需要正式进入 IR 化。**

---

## 4. 为什么不建议现在三条 IR 线并行推进

如果现在把：

- `MessageIR`
- `StateContextIR`
- `FSMIR`

当成同成熟度、同优先级的工程并行推进，会有几个明显风险。

### 4.1 MessageIR 已经是可消费的真实主线

它已经有：

- extractor 支撑
- lowering
- normalization
- codegen
- verify
- artifacts
- tests

所以它不是最值得“重新抽象”的地方。

### 4.2 StateContextIR 目前还没有 consumer

现在它最大的问题不是“模型不够漂亮”，而是：

**没有真实 consumer。**

如果继续先扩 `StateContextIR` 的字段、timer、resource，而不先定义行为层怎么消费它，很容易进入空转。

### 4.3 FSMIR 还没有正式对象层

当前行为层还停留在 `string condition / string actions` 阶段。

如果现在直接推进 full `FSMIR`，会同时面对：

- guard 语言
- action 语言
- ctx 引用
- timer hook
- emit hook
- codegen
- verify

范围会过大。

所以更现实的选择是：

**先做一层中间的 BehaviorIR-lite。**

---

## 5. 更适合当前代码状态的推进路线

### 5.1 第一阶段：保持 MessageIR 稳定

当前建议是：

- 冻结 `MessageIR` 的接口形状
- 继续补少量关键结构缺口
- 但不要再试图把所有协议行为都继续塞进 message 层

换句话说：

**MessageIR 应该继续承担“结构层骨架”职责，但不要继续无限扩张。**

### 5.2 第二阶段：引入 BehaviorIR-lite

这里不是一步做完整 `FSMIR`，而是先把当前 `ProtocolTransition` 升成一个更接近实现、但仍允许 fallback 的中间层。

建议对象可以先非常克制，例如：

- `EventRef`
- `GuardExpr`
- `ActionOp`
- `TransitionIR`

其中 `ActionOp` 一开始只支持少量闭集操作，例如：

- `set_state`
- `set_ctx_field`
- `start_timer`
- `cancel_timer`
- `emit_message`
- `drop_message`
- `raise_error`

无法结构化的动作先保留：

- `raw_text`
- `evidence`
- `diagnostics`

这样做的好处是：

- 不推翻当前 `ProtocolStateMachine`
- 又能让行为层进入“可消费的 typed IR”阶段

### 5.3 第三阶段：让 StateContextIR 由行为需求倒推

这一点是当前最值得吸收的架构判断：

不要先做一条独立的大型上下文抽取线，
而是：

- 先做 `BehaviorIR-lite`
- 再看 guard / action 真正引用了哪些：
  - `ctx.field`
  - `ctx.timer`
  - `ctx.resource`
- 只把这些 slot materialize 成 `StateContextIR`

这样得到的 `StateContextIR` 是：

**由行为需求倒逼出来的最小 runtime schema**

而不是“看上去完整，但暂时没人用”的大对象。

### 5.4 第四阶段：做 trace verify，而不是完整 runtime

当前 message 这条线之所以稳，是因为有：

- `syntax check`
- `expected symbols`
- `roundtrip`

行为层下一步也应该走类似策略，而不是直接追求完整协议执行框架。

建议下一层验证形式是：

```text
decode message
-> derive event / predicates
-> run one transition
-> assert ctx update
-> assert emitted outgoing message
```

也就是先做：

**behavior trace harness**

而不是直接做“大而全 FSM runtime”。

---

## 6. 为什么 MessageIR 不该和 StateContextIR 合并

这件事当前代码已经给出很清楚的答案。

`MessageIR` 当前负责的是：

- wire format
- fields
- sections
- layout
- validation
- composite tail
- option list

而 `StateContextIR` 想承载的是：

- runtime state
- queues
- timers
- cross-message persistent fields

这两者不是一个层级的问题。

如果把它们并在一起，会出现两个后果：

1. message 结构语义和 runtime 语义混在一起
2. 后续 `FSMIR` 无法清晰地区分：
   - `msg.field`
   - `ctx.field`

所以：

**StateContextIR 必须独立于 MessageIR。**

---

## 7. 当前最准确的 as-is 架构表述

如果按 thesis 分支真实代码状态来描述当前系统，更准确的说法应该是：

```text
Extraction Layer:
  ProtocolMessage / ProtocolStateMachine / ProcedureRule / TimerConfig / ErrorRule

Implementation Layer:
  MessageIR            # 已进入真实主链
  StateContextIR       # 已有模型层和样例层，尚未进入真实主链

Behavior Layer:
  ProtocolStateMachine + condition/actions strings + FSM skeleton codegen
```

这比直接说“当前已经三层 IR 并行建设”更贴合现状。

---

## 8. 当前最合理的后续主线

综合当前代码状态，我建议后续顺序是：

1. **MessageIR 稳定化**
   - 继续做结构层骨架
   - 不再无限扩 message 行为语义

2. **修 FSM codegen 的结构问题**
   - 先解决 `(state, event)` 多条件分支导致的重复 `case`
   - 让 FSM skeleton 至少成为稳定的结构骨架

3. **引入 BehaviorIR-lite**
   - 让 `condition / actions` 从纯字符串升级成最小 typed IR

4. **让 StateContextIR 由 BehaviorIR-lite 倒推**
   - 只 materialize 真正被 guard / action 使用的上下文字段

5. **做 trace verify**
   - 跑单步 transition
   - 校验 `ctx update`
   - 校验 `emit message`

6. **最后再考虑 full FSMIR**
   - richer guard language
   - richer action language
   - timer hooks
   - emit hooks

---

## 9. 一句话结论

当前 thesis 分支最成熟的实现层仍然是 `MessageIR`。  
`StateContextIR` 已经开始建立，但目前还只到模型层和样例层。  
因此，最稳的路线不是现在直接三条 IR 线并行展开，而是：

**沿着已经跑通的 MessageIR 主线，先补一层 BehaviorIR-lite，再让 StateContextIR 由行为需求倒推出最小可用集合。**
