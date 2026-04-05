# MessageIR 设计草案

## 1. 背景

当前系统已经能够从 RFC/PDF 中抽取 `ProtocolMessage`，并进一步生成 `.h/.c` 代码骨架。但如果目标是生成真实可运行的工程代码，仅靠 `ProtocolMessage` 和代码骨架还不够。

原因在于：

- `ProtocolMessage` 更接近文档抽取结果，表达的是“文档里提到了一个什么报文/报文段”。
- 真实工程代码需要表达的是“这个消息在内存和字节流中如何布局、如何编码、如何解析、如何校验”。
- RFC 对同一消息的描述通常分散在多个章节：有的节点给字段表，有的节点说明字段取值语义，有的节点给长度约束，有的节点说明某字段决定是否存在附加段。

因此，需要在 `ProtocolMessage` 和最终代码之间增加一层面向工程实现的中间表示，即 `MessageIR`。

它的作用不是替代代码骨架，而是为代码骨架和真实实现提供稳定、可验证、可复用的语义输入。

目标链路应为：

`ProtocolMessage -> MessageIR -> C Skeleton -> Real Implementation`

## 2. MessageIR 的职责

`MessageIR` 的职责不是“再包装一次抽取结果”，而是把分散的文档语义归并成可生成工程代码的消息定义。

它至少负责以下五件事：

### 2.1 统一消息身份

同一个协议消息可能在不同节点里以不同名称出现，例如：

- 完整名 vs 简称
- `Generic ... Format` vs `... Packet`
- `Authentication Section` vs `Auth Section`

`MessageIR` 需要把这些文档层名称归并为一个稳定的工程对象，并保留来源信息。

### 2.2 聚合结构信息

不同节点可能分别贡献：

- 字段列表
- 字段顺序
- 字段位宽
- 可选 section 的存在关系

`MessageIR` 需要把这些离散信息聚合成一个有序、可实现的结构定义。

### 2.3 承接工程语义

真实 `pack/unpack` 代码需要的不只是字段名和 `size_bits`，还需要：

- bit/byte offset
- endianness
- storage type
- bitfield packing 方式
- 可选字段出现条件
- 变长字段长度来源

这些信息应由 `MessageIR` 持有，而不是散落在模板判断中。

### 2.4 承接校验语义

很多 RFC 信息不是“新字段”，而是已有字段的工程约束，例如：

- `Auth Len` 必须等于某个值
- `Reserved` 必须为 0
- `Auth Type` 取值不同，对应不同认证格式

这些规则应在 `MessageIR` 中被结构化表示，从而支持生成 `validate()` 或在 `unpack()` 中做约束检查。

### 2.5 为代码生成和验证提供统一输入

`MessageIR` 应同时服务于：

- `.h/.c` 代码生成
- roundtrip 测试生成
- 编解码语义验证

这样，代码生成和验证都可以围绕同一份 IR 进行，而不必重复解释 `ProtocolMessage`。

## 3. MessageIR 不负责什么

边界必须收紧，否则 IR 很快会膨胀失控。

第一版 `MessageIR` 不负责：

- 完整状态机语义
- procedure 逻辑
- 跨消息的运行时行为
- 文档问答或 RAG 检索
- LLM 的自由生成决策

第一版只聚焦“可编码/可解码的协议消息结构单元”。

这类单元可以是：

- 完整报文
- 报文头
- 认证段
- 可独立解析的 section
- TLV 子结构

因此用 `MessageIR` 命名比 `FrameIR` 更稳，因为它不强制对象必须是完整 frame。

## 4. 为什么不能只用树

直觉上，消息结构似乎适合用树表示，因为消息内部可能有：

- 顶层消息
- 可选 section
- section 下的字段

这部分直觉是对的，但如果把整个 IR 设计成“纯树”，会遇到问题。

树适合表示“包含关系”，但很多工程语义不是树关系，而是交叉引用关系，例如：

- 某 section 是否存在，取决于另一个字段的值
- 某字段的长度由另一个字段决定
- 某个常量约束只在特定认证类型下成立

这些都不是简单的父子关系。

因此，`MessageIR` 更合理的表示方式是：

- 顶层以消息对象为中心
- 内部结构用有序列表或局部树表示
- 约束、条件、枚举、来源关系用规则对象和引用表示

一句话概括：

- 结构部分可以是树
- 完整 IR 不能只是树

## 5. 推荐表示形式

建议将 `MessageIR` 设计为“主对象 + 结构节点 + 规则节点”的组合。

### 5.1 顶层对象：`MessageIR`

`MessageIR` 表示一个最终可生成代码的协议消息对象。

建议字段如下：

```python
class MessageIR(BaseModel):
    ir_id: str
    protocol_name: str
    canonical_name: str
    display_name: str
    source_message_names: list[str]
    source_pages: list[int]

    layout_kind: str
    total_size_bits: int | None
    total_size_bytes: int | None

    headers: list["SectionIR"]
    fields: list["FieldIR"]

    presence_rules: list["PresenceRule"]
    validation_rules: list["ValidationRule"]
    enum_domains: list["EnumDomain"]

    codegen_hints: "CodegenHints"
```

字段含义：

- `canonical_name`：工程内部稳定标识
- `display_name`：用于代码生成中的显示名、文件名、注释
- `layout_kind`：用于选择代码生成策略
- `headers`：用于表达 section/header 级结构
- `fields`：展平后的可编码字段序列
- `presence_rules`：可选段或字段的出现条件
- `validation_rules`：字段值、长度、保留位等约束
- `enum_domains`：字段枚举值语义
- `codegen_hints`：模板分发和实现偏好所需的工程提示

### 5.2 结构节点：`SectionIR`

`SectionIR` 用于表示局部结构层次，例如认证段、可选 header、TLV 容器。

```python
class SectionIR(BaseModel):
    section_id: str
    name: str
    kind: str
    bit_offset: int | None
    bit_width: int | None
    byte_offset: int | None
    optional: bool = False
    presence_rule_ids: list[str] = []
    field_ids: list[str] = []
```

这里保留轻量树结构即可，不建议把所有规则直接挂进 section。

### 5.3 核心字段节点：`FieldIR`

`FieldIR` 是最关键的部分，因为它直接服务于真实 `pack/unpack` 代码。

```python
class FieldIR(BaseModel):
    field_id: str
    name: str
    canonical_name: str

    bit_width: int | None
    bit_offset: int | None
    byte_offset: int | None

    storage_type: str
    signed: bool = False
    endianness: str | None

    is_bitfield: bool = False
    bit_lsb_index: int | None = None
    bit_msb_index: int | None = None

    is_array: bool = False
    array_len: int | None = None

    is_variable_length: bool = False
    length_from_field: str | None = None

    optional: bool = False
    presence_rule_ids: list[str] = []

    const_value: int | str | None = None
    enum_domain_id: str | None = None

    description: str | None = None
    source_pages: list[int] = []
```

第一版最核心的就是这批字段。

它们直接支撑：

- `struct` 成员选择
- `pack()` 里的写入逻辑
- `unpack()` 里的读取逻辑
- `validate()` 里的字段约束检查

### 5.4 规则节点：`PresenceRule`

用于表示字段或 section 的存在条件。

```python
class PresenceRule(BaseModel):
    rule_id: str
    kind: str
    expression: str
    depends_on_fields: list[str]
    description: str | None = None
```

示例：

- `auth_section present when control_packet.auth_present == 1`
- `password present when auth_type == SIMPLE_PASSWORD`

第一版不必做复杂 AST，可以先用受限表达式字符串加依赖字段列表。

### 5.5 规则节点：`ValidationRule`

用于表示长度、保留位、枚举值、固定常量等约束。

```python
class ValidationRule(BaseModel):
    rule_id: str
    target_field: str | None
    kind: str
    expression: str
    severity: str = "error"
    description: str | None = None
```

示例：

- `auth_len == 24 when auth_type in {2, 3}`
- `reserved == 0`
- `auth_len == 28 when auth_type in {4, 5}`

### 5.6 枚举节点：`EnumDomain`

RFC 中经常有字段值语义定义，这类信息不应只留在 description 中。

```python
class EnumValue(BaseModel):
    value: int
    name: str
    description: str | None = None


class EnumDomain(BaseModel):
    enum_id: str
    field_name: str
    values: list[EnumValue]
```

这样后续可以生成：

- `enum`
- `#define`
- 值合法性检查
- 注释更清晰的 switch/case

### 5.7 工程提示：`CodegenHints`

这部分不是协议语义，而是代码生成提示。

```python
class CodegenHints(BaseModel):
    preferred_template: str
    generate_pack: bool = True
    generate_unpack: bool = True
    generate_validate: bool = True
    runtime_helpers: list[str] = []
```

它的目的是避免把模板选择逻辑散落在外部判断中。

## 6. layout_kind 建议

`MessageIR` 本身应通用，但代码生成不应只有一个万能模板。建议在 IR 中加入 `layout_kind`，用于将消息映射到少量结构模式。

第一版建议支持以下类型：

- `fixed_bytes`
  - 固定长度、字节对齐字段
- `bitfield_packed`
  - 固定长度，但包含多个 bitfield
- `optional_section`
  - 存在可选段，由其他字段控制是否出现
- `variable_length`
  - 含变长字段或长度依赖关系
- `composite`
  - 同时包含 bitfield、可选段、变长字段

这样可以做到：

- IR 保持统一
- 模板按结构类型分派
- 避免为每个协议单独做模板

## 7. 推荐构建流程

`MessageIR` 不应由单个节点直接生成，而应通过“全局扫描 + 增量聚合”构建。

建议流程如下：

### 7.1 建立 registry

维护一个 `message_ir_registry`：

- key：归一化后的消息身份
- value：当前聚合中的 `MessageIR`

### 7.2 节点贡献增量信息

每扫描一个消息相关节点，不是直接输出最终代码对象，而是产出增量信息，例如：

- 新字段
- 字段位宽
- 枚举值
- 约束规则
- section 存在条件

### 7.3 增量合并到目标 MessageIR

将节点贡献 merge 到对应 `MessageIR`，逐步补全：

- 结构
- 规则
- 约束
- 来源信息

### 7.4 扫描完成后做 lowering/normalization

统一得到可生成代码的工程对象：

- 确定字段顺序
- 计算 offsets
- 确定 storage types
- 归并重复约束
- 推断 `layout_kind`

这一步结束后，`MessageIR` 才进入 codegen。

## 8. 与现有对象的关系

### 8.1 `ProtocolMessage`

定位：

- 抽取结果
- 偏文档层
- 可以不完整

### 8.2 `MessageIR`

定位：

- 工程归一化结果
- 偏实现层
- 应尽可能完整、可生成代码

### 8.3 `.h/.c` 代码骨架

定位：

- `MessageIR` 的工程输出形式
- 负责文件结构、函数签名、类型组织
- 不是语义来源本身

因此，正确关系不是“用不用代码骨架”，而是：

- `ProtocolMessage` 提供抽取内容
- `MessageIR` 提供实现语义
- 代码骨架提供工程组织形式

## 9. BFD 上的最小落地范围

第一版不要追求所有消息一次打通，建议只针对 BFD 中最容易落地的几类消息：

- `BFD Authentication Section (Simple Password Authentication)`
- `Keyed MD5 and Meticulous Keyed MD5 Authentication Section`
- `BFD Authentication Section: Keyed SHA1 / Meticulous Keyed SHA1`

原因：

- 结构比较稳定
- 字段相对清晰
- 长度约束明确
- 更容易先做成真实 `pack/unpack`

`Generic BFD Control Packet` 可以放在第二步处理，因为它包含更多 bitfield 和组合布局问题。

## 10. 第一版验收标准

如果要判断第一版 `MessageIR` 是否设计合理，建议用以下标准验收：

- 能从现有 `ProtocolMessage` 稳定 lowering 到 `MessageIR`
- 能覆盖 BFD 三种认证段的真实字段布局
- 能表达 `Auth Type`、`Auth Len`、`Reserved` 等约束
- 能基于 `MessageIR` 生成真实 `pack/unpack`
- 能生成至少一组 roundtrip 测试
- 代码生成不再依赖在模板中硬编码协议特殊规则

## 11. 结论

`MessageIR` 不是对现有消息对象的简单重命名，而是一层明确面向工程代码生成的中间表示。

它的核心价值在于：

- 把分散在不同章节中的消息语义重新聚合
- 把文档层消息对象转化为实现层消息对象
- 为真实 `pack/unpack`、`validate` 和测试生成提供稳定输入

在表示形式上，最合理的方案不是“纯树”，而是：

- 以 `MessageIR` 为中心
- 用局部树或有序列表表达结构
- 用规则对象表达条件、约束和枚举

对于当前项目，最务实的路线是先实现 `MessageIR v1`，只覆盖 BFD 的认证段消息，先把真实 `pack/unpack` 生成打通，再扩展到更复杂的控制报文。
