# 基于报文结构原型的受控抽取与统一 MessageIR 归一化方案
## Archetype-Guided Message Extraction with Unified MessageIR Lowering

## 1. 背景与问题

当前项目中的 MessageIR 已经完成了前两阶段能力建设：

- **phase 1**：支持 byte-aligned 的 fixed / variable message
- **phase 2A**：支持 packed bitfield / mixed layout
- **phase 2B**：支持 `flag + length + type-dispatch` 驱动的 optional tail composite case（以 BFD Control Packet + auth tail 为首个样例）

在此基础上，使用 `rfc793-TCP.pdf` 作为第二协议验证对象时，得到了一个具有代表性的结果：

- `TCP Header` 固定前缀可以被正确抽取和归一化
- `Data Offset / Reserved / URG / ACK / PSH / RST / SYN / FIN` 的 packed layout 已可正确推导
- 但 `Options` 和 `Padding` 因缺少稳定、显式的 `size_bits`，导致 `tcp_header` 在当前 MessageIR 中被 `BLOCKED`
- 阻塞原因不在 bitfield，而在于 **header-length-controlled tail** 尚未被上游稳定表达与下游显式建模

这一结果说明：

1. 当前 MessageIR 的 packed header 能力已经具备跨协议泛化性，并非只服务于 BFD  
2. 继续追求“从任意 message 文本直接一步 lower 到完全统一、全通用的 MessageIR”，成本高、稳定性差，并且会把上游 LLM 抽取的模糊性全部压到 normalization 阶段

因此，需要调整设计路线。

---

## 2. 核心新判断

### 2.1 不要让上游直接硬撞最终统一 IR

当前 MessageIR 的方向本身没有问题，但如果上游直接从 RFC 节点文本一步输出“最终统一的 MessageIR 级结构语义”，会遇到三个现实问题：

#### 问题 1：LLM 更擅长识别“结构模式”，不擅长直接发明最终统一抽象

对于协议报文，LLM 更容易稳定判断：

- 这是固定字段序列
- 这是 packed header
- 这是 header 中某字段决定 tail 长度
- 这是 flag 控制 optional section
- 这是 type 决定后续 body 类型
- 这是 TLV / option list

也就是说，LLM 对 **frame archetype** 的判断，通常比对“最终归一化 IR”的一步到位输出更稳定。

#### 问题 2：不同协议的 message shape 差异很大

例如：

- BFD auth section 更接近 `fixed_fields`
- BFD control packet 更接近 `packed_header + flag_optional_tail + type_dispatched_tail`
- TCP header 更接近 `packed_header + header_length_controlled_tail + derived_padding + option_sequence`
- FC-LS 更可能接近 `fixed header + repeated TLV sequence`

如果直接用一套统一抽取 schema 强行覆盖这些差异，上游输出会越来越不稳定。

#### 问题 3：统一 MessageIR 仍然必要，但应位于 lowering 之后

真正需要统一的是：

- normalization
- codegen
- verify
- diagnostics
- READY gate

也就是说：

> **统一的应该是下游工程语义层，而不是上游抽取提示词层。**

---

## 3. 方案总述

### 3.1 一句话概括

> **上游采用 archetype-guided few-shot extraction，下游仍然 lower 到统一 MessageIR。**

目标链路调整为：

```text
Document Node
-> Archetype Extraction
-> ArchetypeContribution
-> Unified MessageIR Lowering
-> codegen
-> verify
```

而不是：

```text
Document Node
-> 直接最终 MessageIR
-> codegen
```

### 3.2 这不是放弃统一 IR

新方案不是“不要统一 MessageIR”，而是：

- **上游**：按有限几类 frame archetype 做受控抽取
- **中游**：按 archetype-specific lowering 规则转成统一 MessageIR
- **下游**：继续只消费 READY 的 MessageIR

因此：

- 模板里仍然不能塞协议知识
- codegen 仍然不能自由猜测协议语义
- diagnostics / READY gate / verify 仍然由统一 MessageIR 控制

### 3.3 不是单标签分类，而是“核心 archetype + 组合 traits”

一个 message 往往不属于唯一 archetype。

例如：

- BFD Control Packet 不是单纯的 `packed_header`，还包含 `flag-controlled optional tail` 与 `type-dispatch tail`
- TCP Header 不是单纯的 `packed_header`，还包含 `header_length_controlled_tail` 与 `derived_padding`
- FC-LS 报文可能既有固定头，又有 repeated TLV list

因此更合适的设计不是“message 只有一个 archetype”，而是：

- 一个 `core_archetype`
- 若干 `composition_traits`
- 若干 `constraint_traits`

---

## 4. 设计原则

### 原则 1：统一的是下游工程语义层，不是上游抽取提示词层
上游允许按 archetype 分化；下游必须统一到 MessageIR。

### 原则 2：LLM 负责识别结构模式，不负责闭合所有实现语义
LLM 擅长回答“这像什么结构”；不擅长直接构造最终统一实现抽象。

### 原则 3：lowering 必须是确定性的、可测试的、可诊断的
archetype 输出只是 clue，真正进入工程层前必须经过规则化归一化。

### 原则 4：复杂结构先降级可用，再逐步细化
例如 TCP options / FC-LS TLV，先 opaque，再结构化。

### 原则 5：shape contract 必须显式化
不能只给 archetype 名称，还要定义该 archetype 至少应提供哪些最小结构承诺。

### 原则 6：低置信输入优先降级，不应一律阻断
低置信度 archetype/trait 不等于失败，应触发更保守的 lower 策略。

---

## 5. 新方案总体结构

## 5.1 上游：Archetype-Guided Extraction

上游 LLM 不直接产最终 MessageIR，而先产：

- message identity
- canonical hint
- core archetype
- composition traits
- constraint traits
- archetype-specific field metadata
- tail slot clues
- rule clues
- confidence
- source metadata

## 5.2 中间层：ArchetypeContribution

Extractor 原始输出不直接进入 MessageIR lowering，而应先进入一个标准化中间层：

```text
Extractor Output
-> ArchetypeContribution normalization
-> MessageIR lowering
```

这样可以避免 lowering 直接面对不稳定的原始 JSON。

## 5.3 下游：Unified MessageIR Lowering

无论上游 archetype 如何分化，下游仍统一 lower 为 MessageIR，用于：

- normalization
- codegen
- verify
- diagnostics
- READY gate

---

## 6. ArchetypeContribution：标准中间层设计

建议正式引入以下对象。

### 6.1 ArchetypeContribution

建议字段：

- `message_name`
- `canonical_hint`
- `core_archetype`
- `composition_traits`
- `constraint_traits`
- `fields`
- `tail_slots`
- `rule_clues`
- `source_pages`
- `source_node_ids`
- `confidence`
- `diagnostics`

### 6.2 FieldContribution

建议字段：

- `name`
- `canonical_hint`
- `width_bits`
- `bit_offset_hint`
- `byte_offset_hint`
- `description`
- `field_traits`

### 6.3 TailSlotContribution

建议字段：

- `slot_name`
- `presence_expression`
- `span_expression`
- `selector_field`
- `candidates`
- `tail_kind`
- `fallback_mode`

### 6.4 RuleClue

建议字段：

- `kind`
- `expression`
- `target`
- `confidence`

这层的作用是：

> **把 LLM 输出标准化成 lowering 可消费的最小契约。**

---

## 7. Archetype 体系（v1 固定集合）

建议 v1 固定为 4 类，不再随意扩展。

### 7.1 `fixed_fields`

适用于：

- 固定字段序列
- 固定长度认证段
- 无复杂 bitfield、无复杂 tail dispatch 的 header/body

典型例子：

- BFD Simple Password Authentication Section
- Keyed MD5 Authentication Section
- Keyed SHA1 Authentication Section

### 7.2 `packed_header`

适用于：

- 含 sub-byte bitfield
- 含 mixed packed + aligned field
- 可以用 bit offset / container grouping 表达的头部结构

典型例子：

- BFD Generic Control Packet Mandatory Section
- TCP Header 固定前缀

### 7.3 `length_prefixed_body`

适用于：

- header 中某长度字段控制 body / tail span
- span 可由 `total_length - fixed_prefix` 或 `field_length` 推导

典型例子：

- BFD full control packet
- TCP Header options tail

### 7.4 `repeated_tlv_sequence`

适用于：

- body/tail 是 TLV / option list
- 内部元素可以重复
- 每个元素有 kind/type、可选 length、可选 value

典型例子：

- TCP Options（未来）
- FC-LS TLV 区域（未来）

---

## 8. Traits 体系

建议拆成两层，而不是全部混在一个 traits 列表中。

### 8.1 Composition Traits

用于描述 layout / composition 语义：

- `flag_optional_tail`
- `header_length_controlled_tail`
- `type_dispatched_tail`
- `derived_padding`

### 8.2 Constraint Traits

用于描述字段约束语义：

- `enum_constrained_field`
- `const_reserved_field`

这样 lowering 时可以明确区分：

- composition trait 改变 section/tail/span/dispatch
- constraint trait 改变 validation/enum/reserved/const

---

## 9. Shape Contract：为每个 archetype 定义最小结构承诺

不能只知道某条 message 属于哪种 archetype，还要知道该 archetype 至少要提供哪些信息，否则 lowering 不知道是否应该接收。

### 9.1 `fixed_fields` 的最小 contract

至少能提供：

- ordered fields
- 每个 field 的 width clue
- 至少部分 offset/order clue

### 9.2 `packed_header` 的最小 contract

至少能提供：

- packed field sequence
- 每个 field 的 width clue
- 至少一个 container-level order clue

### 9.3 `length_prefixed_body` 的最小 contract

至少能提供：

- length controller field
- fixed prefix size clue
- tail span clue

### 9.4 `repeated_tlv_sequence` 的最小 contract

至少能提供：

- sequence exists
- sequence start/span clue
- item kind clue（至少是 TLV-like / option-like）

如果 contract 不满足：

- 不应直接一律 `BLOCKED`
- 应优先尝试降级 lower
- 无法降级时再进入 `BLOCKED`

---

## 10. 置信度与降级策略

建议 archetype 输出中显式增加：

- `confidence.core_archetype`
- `confidence.traits`
- `confidence.tail_slots`
- `confidence.rules`

并定义降级策略。

### 10.1 降级策略原则

低置信度不等于失败，而是触发更保守的 lowering。

例如：

- `packed_header` 高置信
- `header_length_controlled_tail` 中置信
- `derived_padding` 低置信

则可采用：

- header 先 fully structured lower
- tail 先 lower 为 opaque bytes
- padding 不独立建 field，而作为 derived note / hint

### 10.2 降级的意义

这使得系统能在信息不完全时仍尽量产出“可用但保守”的 MessageIR，而不是过早 BLOCK。

---

## 11. 上游输出建议格式

建议 LLM 输出的是 archetype-oriented 中间结构，而不是最终 MessageIR。

### 11.1 TCP Header 示例

```json
{
  "message_name": "TCP Header",
  "canonical_hint": "tcp_header",
  "core_archetype": "packed_header",
  "composition_traits": [
    "header_length_controlled_tail",
    "derived_padding"
  ],
  "fixed_fields": [
    {"name": "Source Port", "width_bits": 16},
    {"name": "Destination Port", "width_bits": 16},
    {"name": "Sequence Number", "width_bits": 32},
    {"name": "Acknowledgment Number", "width_bits": 32},
    {"name": "Data Offset", "width_bits": 4},
    {"name": "Reserved", "width_bits": 6},
    {"name": "URG", "width_bits": 1},
    {"name": "ACK", "width_bits": 1},
    {"name": "PSH", "width_bits": 1},
    {"name": "RST", "width_bits": 1},
    {"name": "SYN", "width_bits": 1},
    {"name": "FIN", "width_bits": 1},
    {"name": "Window", "width_bits": 16},
    {"name": "Checksum", "width_bits": 16},
    {"name": "Urgent Pointer", "width_bits": 16}
  ],
  "tail_slots": [
    {
      "slot_name": "options_tail",
      "presence": "header.data_offset > 5",
      "span_expression": "header.data_offset * 4 - 20",
      "tail_kind": "opaque_bytes",
      "padding_kind": "derived_padding"
    }
  ]
}
```

### 11.2 BFD Full Control Packet 示例

```json
{
  "message_name": "Generic BFD Control Packet",
  "canonical_hint": "bfd_control_packet",
  "core_archetype": "packed_header",
  "composition_traits": [
    "flag_optional_tail",
    "type_dispatched_tail"
  ],
  "tail_slots": [
    {
      "slot_name": "auth_tail",
      "presence": "header.auth_present == 1",
      "span_expression": "header.length - 24",
      "selector_field": "auth.auth_type",
      "candidates": [
        "bfd_auth_simple_password",
        "bfd_auth_keyed_md5",
        "bfd_auth_keyed_sha1"
      ]
    }
  ]
}
```

这类输出比“直接最终 MessageIR”更接近 LLM 的自然能力边界。

---

## 12. Unified MessageIR Lowering 设计

### 12.1 基本原则

- archetype extraction 输出的是“结构模式信号”
- MessageIR 仍然是唯一 codegen 输入
- lowering 必须是确定性的、可测试的、可诊断的

### 12.2 各 archetype 对应 lowering

#### 12.2.1 `fixed_fields -> MessageIR`

直接 lower 为：

- ordered fields
- resolved offsets / widths
- const / enum / validation rules

#### 12.2.2 `packed_header -> MessageIR`

lower 为：

- bit offsets
- packed containers
- resolved bit widths
- host storage types

#### 12.2.3 `header_length_controlled_tail -> MessageIR`

lower 为：

- optional/composite tail section
- `total_length_field`
- `fixed_prefix_bits`
- `min_span_bits / max_span_bits`
- 若暂时无法解析内部结构，可先 lower 为 `opaque tail bytes`

#### 12.2.4 `type_dispatched_tail -> MessageIR`

lower 为：

- composite tail slot
- dispatch cases
- candidate submessage family

#### 12.2.5 `repeated_tlv_sequence -> MessageIR`

第一阶段不要立即要求完整 TLV codegen。

##### Step A：先当成 opaque sequence

只保证：

- total span 可确定
- READY gate 可通过
- 不要求内部 option item 全部结构化

##### Step B：再引入 Option/TLV IR

当需要真正生成 option/TLV item 级别的 pack/unpack 时，再增加：

- `OptionListIR`
- `OptionItemIR`
- 或更通用的 `TLVIR`

因此：

> **TLV/Option IR 是下一阶段演化目标，而不是 TCP unblock 的前置条件。**

---

## 13. Readiness 设计

建议从两态扩展为三态：

- `READY`
- `DEGRADED_READY`
- `BLOCKED`

### 13.1 `READY`

表示：

- fully structured
- 可以完整 codegen / verify

### 13.2 `DEGRADED_READY`

表示：

- 外层结构已稳定
- 某些 tail / sequence 暂以 opaque 表示
- 允许进入受限 codegen / verify
- 必须伴随 diagnostics

这非常适合 TCP header 的阶段性状态。

### 13.3 `BLOCKED`

表示：

- archetype contract 不满足
- span 无法推导
- offsets / widths 冲突
- selector / tail binding 无法确定
- 存在阻塞级 diagnostics

---

## 14. 代码生成策略

统一原则保持不变：

- codegen 只消费 MessageIR
- 不直接消费 archetype output
- 不把协议知识塞进模板

### 14.1 对 `DEGRADED_READY` 的处理

允许：

- 生成 outer struct
- 生成 main header codec
- opaque tail 作为 bytes/span 表示

不要求：

- 立即生成 item-level TLV / option codec

这样可以让系统在复杂 tail 尚未完全结构化时，仍然进入可用状态。

---

## 15. 当前仓库中的推荐落点

为了与当前项目结构兼容，最稳的方案不是重写 IR，而是新增 archetype 层，并保持 codegen 继续只依赖 MessageIR。

推荐落点：

- 在 `src/extract/extractors/message.py` 或其输出 schema 中加入 archetype / traits 抽取
- 在新的模块中引入 `ArchetypeContribution`
- 在 `src/extract/message_ir.py` 中做 archetype-specific lowering
- 保持 `src/extract/codegen.py` 继续只依赖统一 MessageIR

推荐新增模块示例：

```text
src/extract/message_archetype.py
src/extract/message_archetype_models.py
src/extract/message_archetype_lowering.py
```

---

## 16. 短期路线：先 unblock TCP

短期不引入完整 TLV IR，而是：

1. 为 TCP 增加 `packed_header + header_length_controlled_tail + derived_padding` 的 archetype few-shot
2. 让 `Options` 先作为 `opaque tail bytes`
3. 让 `Padding` 从派生规则中得到，而不是作为必须显式给宽度的 field
4. 让 `tcp_header` 先从 `BLOCKED` 变成 `DEGRADED_READY` 或 `READY`

### 16.1 TCP 的近期目标

重点不是完整解析 TCP options，而是：

> **先让 `tcp_header` 不再 BLOCKED。**

---

## 17. 中期路线：引入 Option/TLV IR

当需要解析：

- TCP Options
- FC-LS TLV
- 其他 option sequence

再引入真正的：

- `OptionListIR`
- `OptionItemIR`
- 或统一 `TLVIR`

这时再逐步把 `opaque sequence` 提升为 fully structured sequence。

---

## 18. 长期路线：扩到更多协议

当 archetype-guided extraction 稳定后，再用：

- TCP
- FC-LS
- UDP / ICMP 的简单 header

做第二、第三协议验证，证明 MessageIR 的通用性来自“archetype + lowering”，而不是某一个协议的 profile 特判。

推荐形成的验证路径：

- **BFD**：验证 fixed / packed / composite
- **TCP**：验证 packed header + header_length_controlled_tail + derived_padding
- **FC-LS**：验证 fixed header + repeated_tlv_sequence

---

## 19. 实验设计建议

建议围绕这套新方案直接定义实验。

### 实验 1：Archetype 识别质量

评估：

- core archetype accuracy
- trait extraction accuracy

### 实验 2：Lowering 质量

评估：

- contribution completeness
- MessageIR ready rate
- degraded-ready rate
- blocked rate
- blocked reason distribution

### 实验 3：Codegen / Verify 质量

评估：

- compile success
- validate generation success
- roundtrip success

### 实验 4：协议泛化能力

评估：

- BFD / TCP / FC-LS 子集迁移情况
- 新协议需要新增的 archetype-specific rule / profile 数量
- 不同 archetype 对 lowering 难度和 ready rate 的影响

---

## 20. 论文叙事建议

这一方案更适合毕业设计叙事。

推荐表述为：

> 直接从协议文档一步抽取统一实现语义，容易把结构模糊性全部压到最终 IR。为此，本文提出一种“基于报文结构原型的受控抽取与统一 MessageIR 归一化方案”：上游 LLM 先识别有限的报文 archetype 与组合特征，中游采用确定性的 lowering 将其归一化为统一 MessageIR，下游继续由统一 MessageIR 驱动代码生成与验证。

这一表述能够清楚说明：

- 为什么上游需要 LLM few-shot
- 为什么中游需要 archetype-guided lowering
- 为什么下游必须保持确定性
- 为什么系统不是“端到端黑盒生成”，而是“LLM 识别结构模式 + 工程规则归一化”

---

## 21. 推荐命名

建议在论文和代码设计中统一使用：

- 中文：**基于报文结构原型的受控抽取与统一 MessageIR 归一化方案**
- 英文：**Archetype-Guided Message Extraction with Unified MessageIR Lowering**

这个命名能够同时强调：

- 上游不是完全自由抽取
- 下游不是模板硬编码
- LLM 和工程规则分别承担不同职责

---

## 22. 最终定稿版（v1）

### 22.1 方案名称

**基于报文结构原型的受控抽取与统一 MessageIR 归一化方案**  
**Archetype-Guided Message Extraction with Unified MessageIR Lowering**

### 22.2 核心链路

```text
Document Node
-> Archetype Extraction
-> ArchetypeContribution
-> Unified MessageIR Lowering
-> codegen
-> verify
```

### 22.3 v1 固定集合

#### Core Archetype
- `fixed_fields`
- `packed_header`
- `length_prefixed_body`
- `repeated_tlv_sequence`

#### Composition Traits
- `flag_optional_tail`
- `header_length_controlled_tail`
- `type_dispatched_tail`
- `derived_padding`

#### Constraint Traits
- `enum_constrained_field`
- `const_reserved_field`

### 22.4 Readiness
- `READY`
- `DEGRADED_READY`
- `BLOCKED`

### 22.5 落地优先级

1. **TCP**：`packed_header + header_length_controlled_tail + derived_padding`
2. 让 TCP header 先达到 `DEGRADED_READY`
3. `Options` 先 opaque
4. 后续再做 `Option/TLV IR`
5. 再扩到 FC-LS 子集

---

## 23. 总结

当前 MessageIR 的设计不应被理解为：

> 试图一开始用一套全通用 schema 直接吃掉所有协议帧结构。

更合理的理解应是：

> **MessageIR 是统一的下游工程语义层；上游通过 archetype-guided few-shot extraction 先识别常见 frame pattern，再稳定地 lower 到这套统一 IR。**

这也是当前项目下一阶段最合理的路线。
