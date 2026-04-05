# 长协议选型与验证方案总结

## 1. 文档目的

本文档用于总结当前 thesis 主线中的两个关键决策：

1. **主案例协议应该选什么**
2. **系统应该如何验证**

目标不是简单证明“系统可以读取长协议文档”，而是证明：

- 系统能够从**长协议规范**中提取**消息结构**
- 能够组织出**状态对象**
- 能够逐步形成**行为闭环**
- 并通过**外部工具、开源实现与脚本化测试**进行验证

---

## 2. 研究场景与验证目标

当前系统的目标并不是“完整自动生成协议栈”，而是构建一个：

- **结构层（MessageIR）**
- **状态层（StateContextIR）**
- **行为层（BehaviorIR-lite / FSMIR）**
- **trace verify**

分离但可闭环的协议软件骨架生成框架。

因此，验证不能只停留在“生成了一个类”或者“Wireshark 能解码”这种单点检查，而应拆成四层：

### 2.1 结构正确性
验证字段、位布局、长度约束、可选尾部、TLV/Option 等是否符合协议规范。

### 2.2 行为正确性
验证输入消息是否能导出正确的 `event / predicate`，并触发正确的 transition。

### 2.3 状态正确性
验证 `ctx.field / ctx.timer / ctx.resource` 是否按预期更新。

### 2.4 互操作正确性
验证现有实现发出的报文能否被系统正确解析，系统生成的报文能否被现有实现接受。

---

## 3. 主案例协议推荐

## 3.1 首选：OSPFv2

最推荐作为 thesis 主案例的是 **OSPFv2（RFC 2328）**。

原因不是单纯因为文档长，而是因为它同时具备以下几个条件：

### （1）长度足够，适合体现“长协议”
OSPFv2 规范属于两百页级别，足够体现系统对长协议文档的处理能力。

### （2）结构层很丰富
OSPF 不只有一个简单报文头，而是包含：

- OSPF 公共头
- Hello
- Database Description
- Link State Request
- Link State Update
- Link State Acknowledgment
- 多类 LSA 结构

这非常适合展示：

- 多消息族抽取
- 统一 `MessageIR`
- 报文头与 payload/子结构分层
- 长文档中的结构化表示

### （3）行为层明确
OSPF 不是只有 packet format，它还有明显的协议过程，例如：

- 邻接建立
- 数据库交换
- 数据库同步
- LSA 请求/更新/确认
- 邻居状态推进

这意味着它天然适合用来展示：

- `event derivation`
- `transition`
- `ctx update`
- `emit message`

### （4）状态层天然丰富
OSPF 天然存在大量运行时状态对象，例如：

- neighbor state
- DR/BDR 角色
- DD sequence
- retransmission lists
- LSDB
- timer / aging 相关状态

因此它非常适合作为 `StateContextIR` 的代表性场景。

### （5）验证生态成熟
OSPF 有很强的现成验证生态：

- Wireshark / TShark dissector
- 开源实现（如 FRRouting 的 `ospfd`）
- 现成抓包样本
- 明确 RFC 定义

这使它非常适合做：

- 结构字段对照
- pcap 回放
- 开源实现差分
- 行为闭环测试

---

## 3.2 为什么不建议一开始把 TCP 作为主验证对象

TCP 虽然经典，但不建议作为主案例，原因在于：

- 行为复杂度过高
- 重传、拥塞控制、窗口、计时器等交织严重
- payload、options、checksum、时序细节太多
- 很容易把 thesis 从“协议文档到结构/状态/行为骨架生成”拖成“完整网络协议实现”

因此，TCP 更适合作为：

- MessageIR 层的局部结构样例
- option/tail/bitfield 的补充例子

而不是唯一的主案例。

---

## 3.3 备选协议

### BFD
适合做一个较小但结构与状态都清楚的辅助案例，优点是：

- 报文结构明确
- 状态机清楚
- Wireshark 有支持
- FRRouting 有实现

适合作为：

- 小规模闭环验证
- 行为 trace harness 的先行实验对象

### BGP
BGP 也有明确的 FSM 和较成熟的开源实现，适合作为：

- 第二案例
- 状态机导向的补充验证对象

### Diameter
如果想强调“长文档 + 多命令 + AVP 体系 + 可扩展结构”，Diameter 很好；
但它在论文中的行为闭环表达通常不如 OSPF 直观。

---

## 4. 推荐的验证总路线

验证不应只依赖一种手段，而应采用组合式方案：

```text
RFC conformance
+ Wireshark/TShark 字段对照
+ 开源实现差分/互操作
+ trace harness
+ malformed / degraded 测试
```

---

## 5. 结构层验证方案

## 5.1 RFC 对照验证
首先应把协议规范中的结构信息转成表驱动测试，包括：

- 字段名
- 宽度
- 偏移
- 枚举域
- presence rule
- section/tail 结构
- 长度与合法性约束

这一步验证的是：

**MessageIR 是否真正符合协议文档的结构定义。**

## 5.2 Wireshark / TShark 字段对照
Wireshark 非常适合做结构层验证，但推荐的用法不是人工观察界面，而是：

- 用系统 `serialize()` 生成报文
- 写入 pcap
- 用 `tshark -T fields` 导出关键字段
- 和系统内部字段值自动对照

再做反向流程：

- 用真实 pcap 输入系统 `parse()`
- 将解析结果与 TShark 导出的字段逐项比较

因此，Wireshark 在这里应该作为：

**自动字段 oracle**

而不是仅作为可视化工具。

## 5.3 合成 pcap
仅依赖真实抓包通常无法覆盖边界情况，因此需要自己合成测试样本，重点包括：

- 最短合法包
- 最大合法包
- 非法长度
- 非法 bitfield 组合
- 缺失可选 section
- 错误 tail / option / auth 组合
- composite case 边界值

合成样本的价值在于：

- 能系统覆盖边界情况
- 能验证 `READY / DEGRADED_READY / BLOCKED` 路径
- 能补齐真实流量中不易覆盖的组合

---

## 6. 行为层验证方案

## 6.1 不建议等待“现成状态机数据集”
对本课题来说，很难找到一个公开数据集能同时提供：

- 完整 message fields
- 明确 event/predicate 标注
- transition ground truth
- ctx delta
- emit ground truth

因此，行为层验证不应依赖“单一现成数据集”，而应由系统自己构建：

**trace harness**

## 6.2 trace verify 的最小闭环
推荐的行为验证目标是：

```text
decode message
-> derive event / predicate
-> run one transition
-> assert ctx delta
-> assert emit message / timer effect
```

这比追求“完整协议运行时”更适合 thesis，也更贴近当前系统架构。

## 6.3 trace case 设计
建议把行为用脚本化方式组织，例如 YAML / JSON / DSL：

- initial state
- initial ctx
- input message
- expected event
- expected predicate
- expected next state
- expected ctx delta
- expected emitted message
- expected timer effect

这样可以把验证做成：

- 可重复
- 可比较
- 可回归
- 可统计覆盖率

---

## 7. 状态层验证方案

当前 `StateContextIR` 还没有全面进入真实主链，因此状态层验证应聚焦于：

- `ctx.field` 是否正确 materialize
- `ctx.timer` 是否正确分类和更新
- `ctx.resource` 是否被正确引用
- guard/action 是否引用了正确的上下文字段

状态层不应该单独被验证成“大而全运行时框架”，而应该通过行为闭环间接验证：

- 当输入消息触发某个 transition 时
- 是否能得到正确的 `ctx delta`

换句话说，状态层验证应嵌入 trace verify，而不是独立成为一个庞大的系统测试框架。

---

## 8. 开源实现验证方案

推荐先把系统产物包装成一个独立的 reference library，而不是直接接入完整协议栈。

推荐的能力接口可以是：

- `parse(bytes) -> message object`
- `serialize(message object) -> bytes`
- `derive_event(message, ctx) -> event/predicate`
- `step(state, event, predicate, ctx) -> next_state + actions + ctx_delta + emits`

这样做的好处是：

- 易于单元测试
- 易于差分验证
- 易于和 Wireshark / 开源实现对照
- 不会过早把问题扩大到完整系统集成

对于 OSPF，可考虑后续和 FRRouting 的 `ospfd` 做互操作验证：

- 开源实现发出的报文能否被系统正确解析
- 系统生成的报文在字段层面是否被 TShark 正确识别
- 简化场景下系统输出是否与开源实现行为一致

---

## 9. 数据源与数据集建议

## 9.1 可以直接使用的外部来源
### （1）RFC 文档
这是结构与行为规则的第一真值来源。

### （2）Wireshark SampleCaptures
适合做：

- 字段回放
- golden pcap
- 真实协议片段解析验证

### （3）开源实现
例如 FRRouting，可作为：

- 报文对照来源
- 行为对照来源
- 互操作验证对象

## 9.2 不应高估的“现成公共数据集”
像 MAWI、CAIDA 这类公开流量集更适合：

- 工具链健壮性验证
- 流量形态 sanity check
- 低层头部解析验证

但它们通常不适合作为：

- 高层 message field 真值
- 状态机 ground truth
- 行为闭环 ground truth

因此，在 thesis 中不应把它们当作核心验证依据。

## 9.3 对行为层更有启发的数据源
对行为逻辑和状态覆盖，更值得借鉴的是：

- packetdrill 风格的脚本化协议测试
- stateful protocol fuzzing benchmark 的方法论
- TTCN-3 式 stimulus / expected verdict 组织方式

但这些更多是“方法参考”，而不是能直接拿来用的完整真值数据集。

---

## 10. 覆盖率设计建议

不建议只报告代码覆盖率。对本课题，更合适的覆盖率指标至少包括以下几类：

### 10.1 字段覆盖率（Field Coverage）
覆盖：

- 字段
- 枚举值
- flags/bitfield 组合
- 长度规则
- optional section
- tail case
- option/TLV case

### 10.2 状态转移覆盖率（Transition Coverage）
覆盖：

- 状态边
- 主要事件
- 关键 guard 分支

### 10.3 动作覆盖率（Action Coverage）
覆盖：

- `set_state`
- `set_ctx_field`
- `start_timer`
- `cancel_timer`
- `emit_message`
- `drop_message`
- `raise_error`

### 10.4 闭环路径覆盖率（Trace Coverage）
覆盖：

- `decode -> derive -> transition -> ctx update -> emit`

### 10.5 异常输入覆盖率（Malformed / Degraded Coverage）
覆盖：

- 非法长度
- 非法字段组合
- 缺失 section
- 歧义输入
- 部分可恢复输入
- `DEGRADED_READY / BLOCKED` 路径

---

## 11. 推荐的实际执行方案

## Phase A：先用 BFD 跑通小闭环
先用 BFD 做一个较小规模的验证对象，完成：

- RFC 对照
- Wireshark/TShark 字段比对
- serializer/parser differential test
- 最小 trace harness
- malformed 输入测试

目的不是让 BFD 成为 thesis 唯一主案例，而是：

**先跑通验证工具链。**

## Phase B：用 OSPF 作为长协议主案例
在 BFD 工具链跑通后，用 OSPF 做 thesis 主案例，重点展示：

- 长文档结构抽取
- 多消息族统一建模
- 邻居相关行为逻辑
- `StateContextIR` 的最小 materialization
- trace verify 闭环

## Phase C：必要时补一个辅助案例
若需要证明方法可迁移，可补：

- BGP（偏状态机）
- Diameter（偏复杂结构）
- TCP（偏消息结构/option/tail）

但不建议把第二案例做得和主案例一样重。

---

## 12. 最终推荐结论

如果只选一个长协议作为 thesis 主案例，最推荐：

**OSPFv2**

因为它最能同时体现：

- 长协议文档处理能力
- 结构层建模能力
- 行为层建模能力
- 状态层建模能力
- 可验证闭环能力

如果只看验证路线，最合理的方案是：

```text
RFC conformance
+ Wireshark/TShark 字段对照
+ 开源实现差分 / 互操作
+ trace harness
+ malformed / degraded 测试
```

如果把协议选择与验证策略放在一起，最稳的执行方式是：

1. 先用 **BFD** 跑通验证链路  
2. 再用 **OSPFv2** 展示长协议主案例  
3. 必要时用 **BGP / Diameter / TCP** 做补充对照

---

## 13. 适合论文中的表述

一个更适合论文与答辩的表述方式是：

> 本文不试图直接自动生成完整协议栈，而是面向长协议规范构建结构层、状态层与行为层分离的协议软件骨架生成框架。  
> 在验证方面，本文采用 RFC 对照、Wireshark/TShark 字段比对、开源实现差分、脚本化 trace harness 以及 malformed 输入测试等多层手段进行综合验证。  
> 在案例选择方面，本文以 OSPFv2 作为长协议主案例，以 BFD 作为小规模闭环验证对象，从而验证方法在长文档、多消息族、显式状态机与运行时上下文场景下的有效性。
