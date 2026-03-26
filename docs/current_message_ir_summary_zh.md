# 当前 MessageIR 总结

## 1. 当前定位

当前仓库中的 `MessageIR` 已经不是最早那种“字段列表的中间产物”，而是一层**面向实现**的统一消息结构表示。

它的职责是：

- 表达报文/帧的结构语义
- 表达字段顺序、位宽、偏移、分段、条件出现、校验规则
- 为后续 `codegen` 和 `verify` 提供直接输入

它**不负责**：

- `FSM`
- procedure 运行逻辑
- 跨消息行为
- `RAG`
- `LLM` 的自由决策

一句话说，当前 `MessageIR` 的定位是：

**把文档中的帧结构语义，收敛成可生成 `pack / unpack / validate` 代码的统一表示。**

---

## 2. 当前主链路

当前消息主链路已经不是“文档节点直接一步生成最终 `MessageIR`”，而是：

```text
Document Node
-> ProtocolMessage
-> ArchetypeContribution
-> MessageIR
-> codegen
-> verify
```

其中：

- `ProtocolMessage`：抽取阶段得到的原始消息结构
- `ArchetypeContribution`：上游的结构模式中间层
- `MessageIR`：下游统一实现表示

也就是说：

- 上游更偏“识别结构模式”
- 下游更偏“统一落地实现”

---

## 3. 当前数据形态

当前 `MessageIR` 的核心由这些部分组成：

- `fields`
  - 字段级信息，包含位宽、偏移、类型、是否是 bitfield、是否可变长等
- `sections`
  - 段级信息，用于表达固定前缀、尾部段、可选段等
- `composite_tails`
  - 复合尾部，用于表达“固定前缀 + 条件尾部”
- `option_lists`
  - 结构化选项列表，目前是 `Option/TLV IR v1`
- `presence_rules`
  - 条件出现规则
- `validation_rules`
  - 校验规则
- `normalized_field_order`
  - 归一化后的唯一字段顺序
- `layout_kind`
  - 当前消息布局类型
- `diagnostics`
  - 诊断信息
- `normalization_status`
  - 当前可用状态

当前状态值已经是三态：

- `READY`
- `DEGRADED_READY`
- `BLOCKED`

这意味着系统已经不再是简单的“能不能生成”二分法，而是允许：

- 外层结构已稳定，但局部仍保守降级

---

## 4. 当前已支持的结构能力

按已经打通的阶段来看，当前 `MessageIR` 已支持下面几类消息结构。

### 4.1 固定长度与按字节对齐的结构

已支持：

- 固定字段序列
- 定长消息
- 基于长度字段控制的简单变长消息

这部分最早用于 BFD 的认证段。

### 4.2 packed bitfield / mixed layout

已支持：

- bit-level offset
- packed container
- host storage 与 wire layout 分离
- 基于 mask/shift 的 `pack / unpack`

这部分已经用于 BFD Control Packet 的固定头，也已经验证到 TCP 固定前缀头。

### 4.3 conditional tail composition

已支持：

- 某个字段控制尾部是否存在
- 由前缀长度推导尾部起点
- 由总长度推导尾部跨度
- 受限的 family dispatch

这部分先在 BFD 的认证尾部中打通。

### 4.4 archetype-guided lowering

已支持：

- 上游先做 `ArchetypeContribution`
- 下游再统一 lower 到 `MessageIR`

当前 TCP Header 已经走通这条路径。

### 4.5 Option/TLV IR v1

已接入最小版本的 `OptionListIR`，目前只覆盖 TCP options 的最小子集：

- `EOL`
- `NOP`
- `MSS`
- `Window Scale`

它仍然是附着在外层 `MessageIR` 之下的尾部内部表示，不取代 `MessageIR`。

---

## 5. 当前已经打通的两个代表性对象

### 5.1 BFD 路径

当前 BFD 路径已经验证了：

- 认证段的固定/变长结构
- packed header
- 条件尾部
- family dispatch

所以 BFD 是当前 `MessageIR` 主链最完整的验证对象。

### 5.2 TCP Header 路径

当前 TCP Header 已经从最初的 `BLOCKED` 变成可以稳定 lower 的状态。

当前仓库语义上，TCP Header 已经能表达：

- 固定前缀 20 字节头
- packed bitfield
- `data_offset` 控制的 header 尾部
- `options_tail`
- 最小结构化 option list

但 TCP 当前仍然不是完整实现意义上的 fully structured message。

原因是：

- 只支持一小部分 option
- payload/data 还不在当前 message 主线上
- checksum 语义还不在当前消息实现层闭环里

所以 TCP 这条线更适合表述为：

**外层头部已基本结构化，尾部 options 已开始结构化，但还不是完整 TCP message 实现。**

---

## 6. 当前 `READY / DEGRADED_READY / BLOCKED` 的含义

### 6.1 `READY`

表示：

- 结构已经足够稳定
- 字段顺序、偏移、位宽、尾部跨度都可确定
- 不依赖保守降级路径
- 可以按完整路径进入 `codegen / verify`

### 6.2 `DEGRADED_READY`

表示：

- 外层结构已经稳定
- 但局部仍保留保守降级
- 可以进入受限 `codegen / verify`

这对 TCP 很重要，因为它允许：

- 先把 header 做出来
- 再逐步细化 tail

### 6.3 `BLOCKED`

表示：

- 结构关键要素仍无法确定
- 无法安全 lower 或生成代码

例如：

- 缺关键位宽
- 偏移冲突
- 段跨度无法解析

---

## 7. 当前 codegen 能做到什么

当前 `codegen` 已经不再只是空骨架。

对于 `MessageIR`，现在已经能生成：

- `struct`
- `validate`
- `pack`
- `unpack`
- 基础 roundtrip 测试

对 message 来说，这一层是**规则化生成**，不是 `LLM` 现场自由生成。

这意味着：

- 结构层更稳定
- 回归测试更可靠
- 错误更容易定位

---

## 8. 当前 MessageIR 的主要边界

当前 `MessageIR` 已经很适合做“帧结构实现层”，但它不适合继续无限扩张。

主要边界有：

- 不适合承载完整 `FSM`
- 不适合承载 procedure 运行语义
- 不适合承载复杂行为驱动的实现细节
- 不适合把所有协议差异都继续压成 message 层补丁

尤其对 TCP 来说，很多“还缺的内容”并不是单纯继续补 message 就能解决，而是：

- 要看 `FSM`
- 要看 procedure
- 要看具体 action/guard 到底需要哪些字段和 option

所以当前最合理的定位是：

**MessageIR 负责线格式与结构语义，行为层的完善应逐步转移到 FSM / procedure 主线。**

---

## 9. 为什么现在不应该继续只靠 MessageIR 打补丁

从当前项目状态看，继续只靠 `MessageIR` 打补丁，会越来越接近两个问题：

### 9.1 结构层和行为层混在一起

例如：

- 某个 flag 是否重要
- 某个 option 是否必须结构化
- 某个字段是否真要参与实现

这些往往不是 message 层自己能决定的，而是由 `FSM` 和 procedure 决定。

### 9.2 越来越偏向“为单协议补特例”

如果继续沿当前方向细补：

- TCP 会不断要求更复杂的 option 结构
- 其他协议又会带来新的 tail / TLV / nested 变体

这样会让 message 层越来越重，却不一定更接近最终实现目标。

---

## 10. 当前最合理的下一步

当前 `MessageIR` 已经足够支撑“结构层骨架”这件事。

因此下一步最合理的主线不是继续无限扩 `MessageIR`，而是：

### 10.1 先做 `FSM` / procedure 骨架

先把：

- states
- events
- transitions
- guard slots
- action slots

做成更稳定的结构骨架。

### 10.2 再由 `FSM` 反推 message 需求

由行为层来回答：

- 需要哪些 header 字段
- 需要哪些 flag
- 需要哪些 option
- 哪些 option 真的值得继续结构化

### 10.3 最后再补 message

也就是从：

**“先把 message 做全，再看行为怎么用”**

转成：

**“先把行为骨架立住，再按行为需要补 message”**

这对 TCP 尤其重要。

---

## 11. 当前一句话结论

当前仓库中的 `MessageIR` 已经从“字段抽取结果”演进成了**面向实现的统一帧结构表示**，并且已经打通了：

- 固定字段
- packed header
- 条件尾部
- archetype-guided lowering
- 最小 `Option/TLV IR v1`

它现在已经足够承担**结构层骨架生成**的职责；但对于 TCP 这类协议，后续真正决定 message 还要补什么的，应该逐步转移到 `FSM / procedure` 主线来驱动。
