# MessageIR 新方案：Archetype-Guided Extraction + Unified Lowering

## 1. 背景

当前项目已经完成了 MessageIR 的前两阶段能力：

- phase 1：支持 byte-aligned 的 fixed / variable message；
- phase 2A：支持 packed bitfield / mixed layout；
- phase 2B：支持 `flag + length + type-dispatch` 驱动的 optional tail composite case（以 BFD Control Packet + auth tail 为首个样例）。

在这个基础上，用 `rfc793-TCP.pdf` 做第二协议验证时，出现了一个非常典型的结果：

- `TCP Header` 的固定头已经可以被正确抽取和归一化；
- `Data Offset / Reserved / URG / ACK / PSH / RST / SYN / FIN` 的 packed layout 已经能被正确推导；
- 但 `Options` 和 `Padding` 因为没有显式 `size_bits`，最终导致 `tcp_header` 在 MessageIR 中被 `BLOCKED`；
- 阻塞原因不是 bitfield 本身，而是 `header-length-controlled tail` 尚未被明确建模。

这个结果说明两件事：

1. 当前 MessageIR 的 packed header 能力已经具备跨协议泛化能力，并不只服务于 BFD；
2. 继续追求“从任意 message 文本直接一步 lower 到完全统一、全通用的 MessageIR”，成本高、稳定性差，而且会把上游 LLM 抽取的模糊性全部压到 normalization 阶段。

因此，需要调整设计路线。

---

## 2. 新判断：不要让上游直接硬撞全通用 IR

当前 MessageIR 设计的方向本身没有问题，但上游如果直接从 RFC 节点文本产出“完全统一的最终结构语义”，会遇到三个问题：

### 2.1 LLM 更擅长识别“结构模式”，不擅长直接发明最终统一抽象

对于协议报文，LLM 实际上更容易先判断：

- 这是固定字段序列；
- 这是 packed header；
- 这是 header 中某字段决定 tail 长度；
- 这是 flag 控制 optional section；
- 这是 type 决定后续 body 类型；
- 这是 TLV / option list。

也就是说，LLM 对“frame archetype”的判断通常比对“最终归一化 IR”的一步到位输出更稳定。

### 2.2 不同协议的 message shape 差异很大

例如：

- BFD auth section 更接近 `fixed_fields` 或 `fixed_length_auth_block`；
- BFD control packet 更接近 `packed_header + flag_optional_tail + type_dispatched_tail`；
- TCP header 更接近 `packed_header + header_length_controlled_tail + derived_padding + option_sequence`；
- FC-LS 更可能接近 `fixed header + repeated TLV sequence`。

如果直接用一套统一抽取 schema 强行覆盖这些差异，上游输出会越来越不稳定。

### 2.3 统一 MessageIR 仍然是必要的，但应该放在 lowering 之后

真正需要统一的是：

- normalization；
- codegen；
- verify；
- diagnostics；
- READY gate。

也就是说，**统一的应该是下游工程语义层，而不是上游抽取提示词层**。

---

## 3. 核心设计决策

### 3.1 新方案一句话概括

**上游采用 archetype-guided few-shot extraction，下游仍然 lower 到统一 MessageIR。**

目标链路调整为：

`Document Node -> Archetype/Traits Extraction -> Archetype Contribution -> MessageIR -> codegen -> verify`

而不是：

`Document Node -> 直接最终 MessageIR -> codegen`

### 3.2 这不是放弃统一 IR

这个新方案不是“不要统一 MessageIR”，而是：

- **上游**：按有限几类 frame archetype 做受控抽取；
- **中游**：按 archetype-specific lowering 规则转成统一 MessageIR；
- **下游**：继续只消费 READY 的 MessageIR。

因此：

- 模板里仍然不能塞协议知识；
- codegen 仍然不能自由猜测协议语义；
- diagnostics / READY gate / verify 仍然由统一 MessageIR 控制。

### 3.3 新方案的重点不是“单标签分类”，而是“核心 archetype + 组合 traits”

单个 message 往往不属于唯一 archetype。

例如：

- BFD Control Packet 不是单纯的 `packed_header`，它还包含 `flag-controlled optional tail` 和 `type-dispatch tail`；
- TCP Header 不是单纯的 `packed_header`，它还包含 `header-length-controlled tail` 与 `derived padding`；
- FC-LS 报文可能既有固定头，又有 repeated TLV list。

因此更合适的设计不是“一条 message 只有一个 archetype”，而是：

- 一个 `core_archetype`
- 若干 `composition_traits`

---

## 4. 新方案总体结构

## 4.1 上游：Archetype-Guided Extraction

上游 LLM 不直接产最终 MessageIR，而先产：

- message identity
- core archetype
- composition traits
- archetype-specific field metadata
- rule clues
- source metadata

### 4.1.1 core archetype（核心结构类型）

建议第一版限定为以下几类：

1. `fixed_fields`
2. `packed_header`
3. `length_prefixed_body`
4. `repeated_tlv_sequence`

其中：

- `fixed_fields` 表示顺序固定、字段宽度相对明确；
- `packed_header` 表示存在 sub-byte bitfield 或 mixed packed layout；
- `length_prefixed_body` 表示报文总长度/某段长度由前置字段控制；
- `repeated_tlv_sequence` 表示 body/tail 是 TLV/option 列表。

### 4.1.2 composition traits（组合语义特征）

建议第一版显式支持以下 traits：

1. `flag_optional_tail`
2. `header_length_controlled_tail`
3. `type_dispatched_tail`
4. `derived_padding`
5. `enum_constrained_field`
6. `const_reserved_field`

这些 trait 的作用不是替代 MessageIR，而是给 lowering 提供“该 message 属于哪类结构模式”的稳定信号。

---

## 5. 建议的 archetype 体系

## 5.1 Core Archetype

### A. `fixed_fields`

适用对象：

- 固定长度认证段
- 固定字段报文
- 无复杂 bitfield、无复杂 tail dispatch 的 header/body

典型例子：

- BFD Simple Password Authentication Section
- Keyed MD5 Authentication Section
- Keyed SHA1 Authentication Section

### B. `packed_header`

适用对象：

- 含 bitfield
- 含 mixed packed + aligned field
- 可以用 bit offset / container grouping 表达的头部结构

典型例子：

- BFD Generic Control Packet Mandatory Section
- TCP Header 固定前缀

### C. `length_prefixed_body`

适用对象：

- header 中某长度字段控制 body / tail span
- span 可由 `total_length - fixed_prefix` 或 `field_length` 推导

典型例子：

- BFD full control packet
- TCP Header options tail

### D. `repeated_tlv_sequence`

适用对象：

- body/tail 是 TLV / option list
- 内部元素可以重复
- 每个元素有 kind/type、可选 length、可选 value

典型例子：

- TCP Options（未来）
- FC-LS TLV 区域（未来）

## 5.2 Composition Traits

### Trait 1. `flag_optional_tail`

表达：

- 某 tail section 是否存在，由前置 flag 决定。

例子：

- `A == 1 -> auth tail present`

### Trait 2. `header_length_controlled_tail`

表达：

- tail 的总 span 由 header 中字段控制。

例子：

- `tail_bytes = header.length - fixed_prefix_bytes`
- `tail_bytes = data_offset * 4 - 20`

### Trait 3. `type_dispatched_tail`

表达：

- tail 的具体 message family 由某 selector 字段决定。

例子：

- `auth_type in {1,2,3,4,5}` 决定 auth section 类型

### Trait 4. `derived_padding`

表达：

- padding 不是独立抽取目标，而是从总长度与对齐规则派生得到。

例子：

- TCP header padding
- TLV 区域尾部对齐 padding

### Trait 5. `enum_constrained_field`

表达：

- 字段值只能来自受限枚举集。

### Trait 6. `const_reserved_field`

表达：

- 字段必须为常量或 reserved = 0。

---

## 6. 上游结构化输出的新建议

建议不要让 LLM 直接输出最终 MessageIR，而是输出一份更适合 archetype few-shot 的中间结构，例如：

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

对于 BFD full control packet，则更接近：

```json
{
  "message_name": "Generic BFD Control Packet",
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

## 7. 下游 lowering 方案

## 7.1 基本原则

- archetype extraction 输出的是“结构模式信号”
- MessageIR 仍然是唯一 codegen 输入
- lowering 必须是确定性、可测试、可诊断的

## 7.2 各 archetype 对应 lowering

### 7.2.1 `fixed_fields -> MessageIR`

直接 lower 为：

- ordered fields
- resolved offsets / widths
- const / enum / validation rules

### 7.2.2 `packed_header -> MessageIR`

lower 为：

- bit offsets
- packed containers
- resolved bit widths
- host storage types

### 7.2.3 `header_length_controlled_tail -> MessageIR`

lower 为：

- optional/composite tail section
- `total_length_field`
- `fixed_prefix_bits`
- `min_span_bits / max_span_bits`
- 若暂时无法解析内部结构，可先 lower 为 `opaque tail bytes`

### 7.2.4 `type_dispatched_tail -> MessageIR`

lower 为：

- composite tail slot
- dispatch cases
- candidate submessage family

### 7.2.5 `repeated_tlv_sequence -> MessageIR`

第一阶段不要立即要求完整 TLV codegen。

可以先分两步：

#### Step A：先当成 opaque sequence

只保证：

- total span 可确定
- READY gate 可通过
- 不要求内部 option item 全部结构化

#### Step B：再引入 Option/TLV IR

当需要真正生成 option/TLV item 级别的 pack/unpack 时，再增加：

- `OptionListIR`
- `OptionItemIR`
- 或更通用的 `TLVIR`

也就是说：

**TLV/Option IR 是下一阶段演化目标，而不是 TCP unblock 的前置条件。**

---

## 8. 为什么这个方案比“直接全通用 MessageIR”更适合当前项目

## 8.1 与当前仓库结构更兼容

当前仓库已经有：

- `ProtocolMessage`
- `MessageIR`
- normalization
- codegen
- verify

因此最稳的方案不是重写 IR，而是：

- 在 [src/extract/extractors/message.py](/Users/zwy/毕设/Kiro/src/extract/extractors/message.py) 或其输出 schema 中加入 archetype / traits
- 在 [src/extract/message_ir.py](/Users/zwy/毕设/Kiro/src/extract/message_ir.py) 做 archetype-specific lowering
- 保持 [src/extract/codegen.py](/Users/zwy/毕设/Kiro/src/extract/codegen.py) 继续只依赖统一 MessageIR

## 8.2 与论文叙事更一致

对于毕业设计而言，这个方案更容易讲清楚：

- 为什么上游需要 LLM few-shot；
- 为什么中游需要 archetype-guided lowering；
- 为什么下游必须保持确定性；
- 为什么系统不是“端到端黑盒生成”，而是“LLM 识别结构模式 + 工程规则归一化”。

## 8.3 与实验设计更匹配

如果后续要做实验，最自然的评估问题会变成：

1. archetype 识别是否正确；
2. archetype-specific contribution 是否完整；
3. lowering 到 MessageIR 后 READY 率是否提高；
4. codegen / verify 是否因此提升。

这比直接评估“统一 MessageIR 一步抽取准确率”更稳定，也更容易分析错误来源。

---

## 9. 新方案下的阶段路线

## 9.1 短期目标：先 unblock TCP

短期不引入完整 TLV IR，而是：

1. 为 TCP 增加 `packed_header + header_length_controlled_tail + derived_padding` 的 archetype few-shot；
2. 让 `Options` 先作为 `opaque tail bytes` 存在；
3. 让 `Padding` 从派生规则中得到，而不是作为必须显式给宽度的 field；
4. 让 `tcp_header` 先从 `BLOCKED` 变成 `READY`。

## 9.2 中期目标：引入 Option/TLV IR

当需要解析：

- TCP Options
- FC-LS TLV
- 其他 option sequence

再引入真正的：

- `OptionListIR`
- `OptionItemIR`
- 或统一 `TLVIR`

## 9.3 长期目标：扩到更多协议

当 archetype-guided extraction 稳定后，再用：

- TCP
- FC-LS
- UDP/ICMP 的简单 header

做第二、第三协议验证，证明 MessageIR 的通用性来自“archetype + lowering”而不是某一个协议的 profile 特判。

---

## 10. 对当前 MessageIR 设计的最终修正

因此，当前 MessageIR 的设计不应被理解为：

> 试图一开始用一套全通用 schema 直接吃掉所有协议帧结构。

更合理的理解应该是：

> MessageIR 是统一的下游工程语义层；  
> 上游应通过 archetype-guided few-shot extraction 先识别常见 frame pattern，再稳定地 lower 到这套统一 IR。

这也是当前项目下一阶段最合理的路线。

---

## 11. 推荐命名

如果要在论文或代码设计中表述这条路线，建议统一使用以下说法：

- 中文：**“基于报文结构原型的受控抽取与统一 MessageIR 归一化方案”**
- 英文：**Archetype-Guided Message Extraction with Unified MessageIR Lowering**

这个命名能够同时强调：

- 上游不是完全自由抽取；
- 下游不是模板硬编码；
- LLM 和工程规则分别承担不同职责。
