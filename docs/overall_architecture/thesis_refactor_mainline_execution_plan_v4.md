# thesis 分支主线执行方案 V4（BFD 基线 + FC 主案例版）

> 这版 V4 在保留 V3 三层分离目标的前提下，把执行节奏进一步收敛成一条更贴合题目与当前进度的主线：
>
> **先用 BFD 跑通并稳住 pipeline 基线，再用 FC 协议族作为题目相关的主案例推进结构层、行为层和状态层的最小闭环；TCP 保留为结构挑战样例，不再承担 thesis 主主线。**

---

## 1. V4 的核心判断

V3 的方向总体正确，但对 thesis 当前阶段来说仍然偏“架构优先”。  
V4 的调整重点是：

- 保留三层终态：
  - `MessageIR`
  - `BehaviorIR-lite / FSMIR`
  - `StateContextIR`
- 但执行顺序改成：
  - **BFD 先稳住基线**
  - **FC 作为第一主案例**
  - **先打通、再抽象**
  - **先有 consumer、再扩 IR**

一句话：

> **V4 不是“先把三层 IR 都设计好”，而是“先用 BFD 把主链稳住，再让 FC 主案例按真实需求把行为和状态层接进来”。**

---

## 2. thesis 的实际目标（重新收敛）

本课题近期目标不再表述为：

- 自动生成完整协议栈
- 完整 runtime 自动生成
- full FSMIR + 完整上下文系统一次到位

而表述为：

> **构建一个从协议文档出发、能够生成结构层骨架，并逐步扩展到行为层与状态层、最终形成可验证闭环的协议软件骨架生成系统。**

更具体一点：

```text
Protocol Document
-> extraction objects
-> MessageIR
-> codegen / verify
-> minimal behavior skeleton
-> minimal state context
-> trace verify
-> protocol software skeleton
```

---

## 3. 协议角色重新定义

V4 明确不再让所有协议承担同样职责。

## 3.1 BFD：基线协议

`BFD` 的职责：

- 作为当前最稳的回归协议
- 作为 pipeline 能跑通的基线证明
- 用来保证主链修改不回退

它不再承担：

- thesis 唯一主案例
- 最复杂架构实验对象

## 3.2 FC 协议族：第一主案例

`FC 协议族` 的职责：

- 作为 **与题目最相关的主案例**
- 体现“交换机 / SAN 协议软件代码生成”的领域相关性
- 承载后续：
  - `MessageIR`
  - `Behavior bridge`
  - `StateContextIR`
  - `trace verify`

当前更合理的做法不是说“整个 FC 协议族一次做完”，而是：

- 选取 **一个代表性 FC 子协议 / 子规范** 作为主落点
- 优先从你已有材料的 `FC-LS` 或其可控子集切入
- 用它承载长协议、多消息族、上下文线索和行为线索展示

它最适合做：

> **thesis 的主展示协议族**

## 3.3 TCP：结构挑战样例

`TCP` 的职责：

- 保留其在复杂报文结构上的工程价值
- 展示：
  - packed header
  - option/tail
  - archetype-guided lowering
  - 受限 codegen / verify

它不再承担：

- thesis 主主线
- 与 FC 主案例同级的行为/状态闭环任务

一句话：

> **TCP 是结构挑战样例，不是主叙事协议。**

## 3.4 其他协议：可选泛化补充

如果后面需要补强“不是只对一个协议有效”，更稳的选择是：

- `ARP`
- `ICMP`
- `UDP`

这些协议可以用于：

- 快速复用结构主链
- 补简单协议泛化证明

而不是再引入一个新的高复杂度主案例。

---

## 4. V4 的总执行原则

## 4.1 先稳住基线，再抽象

如果一个抽象层还没有真实 consumer，就不要把它做大。

优先顺序：

1. 让 `BFD` 继续稳定承担 pipeline 基线
2. 让 `FC` 主案例逐步接入结构、行为、状态和闭环
3. 在跑通过程中暴露真正缺口
4. 再补最小 IR / consumer

## 4.2 MessageIR 继续作为最成熟主线

当前真正已进入主链的是 `MessageIR`，所以：

- 继续以它为结构层骨架
- 继续让 `codegen / verify` 消费它
- 不再把 runtime / behavior 塞回 message 层

## 4.3 Behavior 先最小化，不做 full FSMIR

V4 明确不直接追 `full FSMIR`。

先做的是：

- 可编译 dispatcher skeleton
- 最小 guard/action 占位
- 少量可类型化 action
- raw fallback

## 4.4 StateContextIR 必须后置且 consumer-driven

`StateContextIR` 只有在以下条件满足后才正式进入主线：

- 行为层开始稳定引用 `ctx.field / ctx.timer / ctx.resource`
- 至少有一个 FC trace 需要这些对象

在这之前：

- 保留模型层
- 不扩大物化范围

## 4.5 trace verify 不能消失，但可以缩小

V4 不赞成现在做大型 trace framework。  
但也不赞成把 trace verify 完全并入 roundtrip。

正确做法是：

- 先做最小 trace harness
- 只验证：
  - `event derivation`
  - 单步 transition
  - `ctx delta`
  - `emit`

---

## 5. V4 的主线阶段

## Phase 0：轻量基线冻结

### 目标

不是做“大基线工程”，而是固定当前可复用的真实起点。

### 要做的事

1. 固定 BFD 当前 artifacts：
   - `protocol_schema.json`
   - `message_ir.json`
   - `verify_report.json`
2. 固定 TCP 当前 artifacts：
   - `protocol_schema.json`
   - `message_ir.json`
   - `verify_report.json`
   - 当前增强版 `tcp_msg_tcp_header.*`
3. 固定 FC 当前可用输入与文档材料：
   - 原始 PDF
   - page index / chunk / content 的已有产物（如果已有）
   - 当前人工整理或实验记录
4. 固定当前关键测试集：
   - message/codegen/verify/pipeline
5. 明确：
   - `ARCHITECTURE_AS_IS`
   - `ARCHITECTURE_TARGET`
   - `BFD baseline + FC mainline` 为当前执行策略

### 验收标准

- BFD/TCP 当前产物都可重放
- FC 输入材料与协议边界明确
- 文档层不再混淆 as-is 和 target

---

## Phase 1：BFD pipeline 基线稳固 + FC 输入链准备

### 目标

确保已有 `BFD` 主链可以持续重放，同时把 `FC` 主案例推进到稳定输入阶段。

### 要做的事

1. 保证 `BFD` 继续稳定跑通：
   - `classify`
   - `extract`
   - `merge`
   - `MessageIR`
   - `codegen`
   - `verify`
2. 明确 `FC` 主案例的协议边界：
   - 选定一个子协议 / 子规范
   - 明确输入 PDF 与已有产物
3. 让 `FC` 至少稳定跑到：
   - `index`
   - `chunk`
   - `classify`
   - `extract`
   - `merge`

### 重点

这一阶段优先解决的是：

- `BFD` 不回退
- `FC` 主案例进入可分析、可抽取、可归并的稳定输入状态

### 验收标准

- `BFD` 当前 pipeline 基线可稳定重放
- `FC` 主案例可稳定跑到 `merge`
- `FC` 至少产出：
  - `protocol_schema.json`
  - message / state / context clues 的初始结果

---

## Phase 2：FC Message 主线打通

### 目标

把 FC 主案例从：

```text
PDF -> extract -> merge -> MessageIR -> codegen -> verify
```

推进成第一条与题目强相关的结构主线。

### 要做的事

1. 让 FC 主案例的 message extraction 稳定
2. lower 到 `MessageIR`
3. 明确：
   - 公共头
   - message family
   - section / tail / variable parts
4. 接通 `codegen / verify`

### 验收标准

- 至少一个 FC message family 可 lower 成稳定 `MessageIR`
- 至少一条 FC message codegen 路径可编译
- 至少一条 FC roundtrip / verify 路径可运行

---

## Phase 3：FC / TCP 的 FSM skeleton 稳定化 + 最小 Behavior Bridge

### 目标

不是一次性上完整 `BehaviorIR-lite`，而是先让行为骨架可编译、可消费，再抽出一个**最小 bridge**：

- 能从 `ProtocolStateMachine` 中抽出少量可消费行为语义
- 能继续保留 raw fallback
- 能服务 skeleton codegen / trace verify

这一步可以优先在：

- `FC` 主案例上推进
- 必要时借助 `TCP` 的较熟悉状态机子路径做辅助验证

### 同步要做的事

1. 修 `(state, event)` 多条件分支展开问题
2. 保证 dispatcher 结构唯一
3. guard/action 先允许：
   - comment
   - stub
   - helper placeholder
4. 明确 unhandled/default 路径

### v1 仅支持的 typed 行为

- `set_state`
- `set_ctx_field`
- `emit_message`
- `start_timer`
- `cancel_timer`

其余保留：

- `raw_text`
- `evidence`
- `diagnostics`

### 为什么不直接 full FSMIR

因为当前最缺的是 consumer，不是语法丰富度。

### 验收标准

- 至少一个代表性状态机样例能 lower 出最小 behavior bridge
- 已支持 action 能被 skeleton / verify 识别
- 未识别动作不会阻塞整条链

---

## Phase 4：由 FC / TCP 行为需求倒推最小 StateContextIR

### 目标

只物化当前最小闭环真正需要的上下文对象。

### 当前建议最小集合

字段：

- `state`
- `send_next_seq`
- `recv_next_seq`
- `send_window`
- `recv_window`

timer：

- `retransmission_timer`

resource：

- `send_queue`（如果行为确实引用）

### 物化来源

1. behavior refs：
   - `ctx.field`
   - `ctx.timer`
   - `ctx.resource`
2. context clues：
   - RFC 文本里明确的 state/timer/resource 线索

### 关键约束

- 没有行为 consumer 的 slot，不要先做
- 不把它扩成完整 runtime schema

### 验收标准

- 至少一个代表性 trace 真实引用了 `StateContextIR`
- `state_context_ir.json` 能作为正式 artifact 落盘
- 不再只是模型层/样例层存在

---

## Phase 5：最小 trace verify 闭环

### 目标

建立 thesis 最重要的“第一条真正闭环”：

```text
decode msg
-> derive event/predicate
-> run one transition
-> assert ctx delta
-> assert emit msg
```

### 这一步只做最小版本

不做：

- 完整协议 runtime
- 多步执行器
- 大型调度框架

先做：

- 1 条 FC 主案例的代表性 trace
- 1 条 BFD 小 trace（推荐）
- 如有必要，可保留 1 条 TCP trace 作为结构-行为桥接样例

### 验收标准

- 至少一条代表性 trace 可跑通
- 失败能定位到：
  - decode
  - event derivation
  - transition
  - ctx delta
  - emit

---

## Phase 6：BFD 回归、TCP 结构补充与简单协议泛化

### 目标

在 `BFD + FC` 主线稳定后，补强回归与泛化展示。

1. 用 BFD 复用主链
2. 保留 TCP 的结构挑战价值
3. 视时间补一个简单协议

### 推荐协议

- BFD：回归与小闭环
- TCP：结构挑战样例
- ARP / ICMP / UDP：快速泛化证明

### 验收标准

- BFD 不因 FC 主线改造而回退
- 至少一个简单协议可复用结构主链

---

## 6. 验证策略（V4 收敛版）

V4 不采用“大而全验证工程”，而采用分层优先级：

## 必做

1. RFC 字段对照
2. pack/unpack roundtrip
3. syntax / symbol / generated code verify
4. 主案例最小 trace verify
5. BFD 回归

## 强烈建议做

1. TShark / Wireshark 字段对照
2. 与增强版 message 代码原型对照
3. 与纯 LLM 直接生成对比

## 选做

1. 开源实现差分
2. malformed / degraded 系统化测试
3. 更复杂 FC 子协议或长协议局部实验

---

## 7. patch lane 在 V4 中的定位

V4 仍然保留 patch lane，但明确它不是当前第一优先级的大工程。

建议最初做成：

- 一个统一 JSON / YAML / Python 配置入口
- 支持：
  - event alias
  - ctx slot alias
  - timer/resource classification
  - emit family mapping

等到 behavior/context 真正开始频繁用到时，再升级成更强机制。

一句话：

> **先让 patch lane 可用，再追求 patch lane 完整。**

---

## 8. V4 与 V3 的关系

V4 不是否定 V3，而是对 V3 的执行节奏做了现实化调整。

### V3 保留的部分

- 三层目标架构
- 职责边界
- patch lane / event derivation / trace verify 的必要性

### V4 调整的部分

- 不再把基线冻结做成大型前置工程
- 不直接推进完整 `BehaviorIR-lite`
- 不让 `StateContextIR` 提前扩张
- 把 `BFD baseline + FC mainline` 提到最高优先级
- 把 TCP 收敛为结构挑战样例

---

## 9. 最终一句话版

> **V4 的执行主线是：先用 BFD 稳住 pipeline 基线，再用 FC 协议族推进与题目强相关的主案例闭环；在这个过程中只引入最小必要的 behavior 与 context 抽象；TCP 保留为结构挑战样例，而不是让其他长协议在当前阶段接管主主线。**
