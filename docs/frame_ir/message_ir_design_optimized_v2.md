# MessageIR v1 设计方案（优化版 v2）

## 1. 背景与设计目标

当前系统已经能够从 RFC/PDF 中抽取 `ProtocolMessage`，并进一步生成 `.h/.c` 代码骨架。但如果目标是生成真实可运行的协议编解码代码，仅靠 `ProtocolMessage` 和代码骨架仍然不够。

原因在于：

- `ProtocolMessage` 更接近文档抽取结果，表达的是“文档里提到了什么消息或结构单元”；
- 真实工程代码需要表达的是“该消息如何布局、如何编码、如何解析、如何校验”；
- RFC 对同一消息的描述通常分散在多个章节：某处给字段表，某处补充取值语义，某处定义长度约束，某处说明某字段决定附加段是否存在。

因此，需要在 `ProtocolMessage` 与最终代码之间增加一层明确面向实现的中间表示：`MessageIR`。

目标链路定义为：

`ProtocolMessage -> MessageIR -> C Skeleton -> Real Implementation`

其中：

- `ProtocolMessage` 面向抽取；
- `MessageIR` 面向语义归一化与工程实现；
- `C Skeleton` 面向文件组织与函数入口；
- `Real Implementation` 面向真实 `pack/unpack/validate/test`。

`MessageIR` 的定位不是“再包装一层抽取结果”，而是把分散、重复、局部、不完整的文档信息收敛为一份稳定、可验证、可生成代码的消息定义。

---

## 2. 设计原则

### 2.1 单一职责原则

`MessageIR` 只负责“可编码/可解码的协议结构语义”，不负责状态机、过程逻辑、运行时交互或问答检索。

### 2.2 结构与规则分离

字段、section、顺序、位宽属于结构；
出现条件、长度约束、保留位约束、枚举合法性属于规则。

结构与规则必须分开建模，避免把工程语义全部塞进树结构中。

### 2.3 原始证据与归一化结果分离

文档中提到的原始 offset、长度、名称、别名属于“来源证据”；
真正用于 codegen 的布局、类型、顺序、规则属于“归一化结果”。

二者不能混用，否则会导致后续 merge 和 lowering 阶段语义不清。

### 2.4 codegen 只消费归一化结果

最终代码生成不能直接依赖扫描阶段的局部信息，也不能依赖模板中的隐式协议知识。

codegen 的唯一语义输入应是 normalization 完成后的 `MessageIR`。

---

## 3. MessageIR 的职责边界

### 3.1 MessageIR 负责什么

`MessageIR` 至少负责以下六项职责：

#### 3.1.1 统一消息身份

同一个协议消息可能在不同章节中以不同名称出现，例如：

- 完整名与简称；
- `Generic ... Format` 与 `... Packet`；
- `Authentication Section` 与 `Auth Section`。

`MessageIR` 需要将这些名称归并为一个稳定的工程对象，并保留来源信息。

#### 3.1.2 聚合结构信息

不同节点可能分别贡献：

- 字段列表；
- 字段顺序；
- 字段位宽；
- section 组织关系；
- 可选段存在关系。

`MessageIR` 需要将这些离散信息聚合为可实现的结构定义。

#### 3.1.3 承接布局语义

真实 `pack/unpack` 代码需要的不只是字段名，还需要：

- 最终字段顺序；
- resolved bit/byte offset；
- endianness；
- storage type；
- bitfield packing 信息；
- 变长字段长度来源。

这些信息必须在 `MessageIR` 中明确，而不能依赖模板推断。

#### 3.1.4 承接条件语义

很多结构单元并非始终存在，例如：

- 某 section 是否存在取决于某标志位；
- 某字段是否存在取决于 `auth_type`；
- 某数组长度依赖 `length` 字段。

这些条件必须结构化表示。

#### 3.1.5 承接校验语义

RFC 中大量关键信息不是新字段，而是约束，例如：

- `Reserved` 必须为 0；
- `Auth Len` 必须等于某个值；
- 某枚举值只能出现在特定上下文中。

这些规则必须可用于生成 `validate()` 逻辑，并可在 `unpack()` 期间执行检查。

#### 3.1.6 为生成与验证提供统一输入

`MessageIR` 应同时服务于：

- `.h/.c` 代码生成；
- `pack/unpack` 实现生成；
- `validate()` 生成；
- roundtrip 测试生成；
- 语义一致性检查。

### 3.2 MessageIR 不负责什么

第一版 `MessageIR` 不负责：

- 完整状态机语义；
- process/procedure 逻辑；
- 跨消息运行时行为；
- 文档问答或 RAG 检索；
- LLM 自由决策；
- 性能优化策略。

第一版只聚焦“可编码/可解码的协议结构单元”。

这类单元可以是：

- 完整报文；
- 报文头；
- 认证段；
- 可独立解析的 section；
- TLV 子结构。

因此使用 `MessageIR` 命名比 `FrameIR` 更稳，因为对象不必是完整 frame。

---

## 4. 为什么不能只用树

树结构适合表达“包含关系”，例如：

- 顶层消息包含若干 section；
- section 包含若干字段；
- TLV 容器包含局部结构。

但协议工程语义中大量关系并不是父子包含关系，而是交叉引用关系，例如：

- 某 section 是否存在取决于另一个字段；
- 某字段长度由另一个字段决定；
- 某条约束只在特定枚举值下成立；
- 同一字段的定义和约束来自不同章节。

因此，更合理的设计是：

- 以 `MessageIR` 为中心对象；
- 用局部树或有序 section 表达结构；
- 用规则对象表达条件、约束和引用；
- 用 source metadata 表达来源证据。

结论是：

- 结构部分可以是树；
- 完整 IR 不能只是树。

---

## 5. 核心数据模型

推荐采用“主对象 + 结构节点 + 规则节点 + 来源元信息”的组合建模方式。

### 5.1 顶层对象：MessageIR

```python
from enum import Enum


class NormalizationStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    BLOCKED = "blocked"


class MessageIR(BaseModel):
    ir_id: str
    protocol_name: str
    canonical_name: str
    display_name: str

    source_message_names: list[str]
    source_pages: list[int]
    source_node_ids: list[str] = []

    layout_kind: str

    total_size_bits: int | None = None
    total_size_bytes: int | None = None
    min_size_bits: int | None = None
    max_size_bits: int | None = None

    sections: list["SectionIR"]
    fields: list["FieldIR"]
    normalized_field_order: list[str]

    presence_rules: list["PresenceRule"]
    validation_rules: list["ValidationRule"]
    enum_domains: list["EnumDomain"]

    codegen_hints: "CodegenHints"
    normalization_status: NormalizationStatus = NormalizationStatus.DRAFT
```

字段说明：

- `canonical_name`：工程内部稳定标识；
- `display_name`：用于注释、文件名、展示输出；
- `sections`：保留局部层次结构；
- `fields`：字段全集；
- `normalized_field_order`：最终 codegen 的逻辑编码顺序来源；
- `layout_kind`：归一化后推导出的布局类别；
- `normalization_status`：区分扫描中对象与可生成代码对象；
- `min_size_bits / max_size_bits`：用于表达 variable-length 或 optional section 场景下的大小边界；
- 若消息大小可变，则 `total_size_bits / total_size_bytes` 可以为 `None`。

### 5.2 结构节点：SectionIR

```python
class SectionIR(BaseModel):
    section_id: str
    name: str
    canonical_name: str
    kind: str

    parent_section_id: str | None = None

    declared_bit_offset: int | None = None
    declared_byte_offset: int | None = None
    declared_bit_width: int | None = None

    resolved_bit_offset: int | None = None
    resolved_byte_offset: int | None = None
    resolved_bit_width: int | None = None

    optional: bool = False
    presence_rule_ids: list[str] = []
    field_ids: list[str] = []
    source_pages: list[int] = []
```

这里区分：

- `declared_*`：文档显式给出的证据；
- `resolved_*`：normalization 后真正用于实现的结果。

### 5.3 核心字段节点：FieldIR

```python
class FieldIR(BaseModel):
    field_id: str
    name: str
    canonical_name: str

    declared_bit_width: int | None = None
    declared_bit_offset: int | None = None
    declared_byte_offset: int | None = None

    resolved_bit_width: int | None = None
    resolved_bit_offset: int | None = None
    resolved_byte_offset: int | None = None

    storage_type: str | None = None
    signed: bool = False
    endianness: str | None = None

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
    source_node_ids: list[str] = []
```

关键约束如下：

- codegen 一律使用 `resolved_*` 字段，不允许直接使用 `declared_*` 字段；
- 如果 `resolved_*` 缺失，则该 `MessageIR` 不得进入代码生成阶段；
- `storage_type` 在 normalization 阶段统一推导，不允许模板临时猜测。

---

## 6. 规则模型

### 6.1 PresenceRule

用于表达字段或 section 的出现条件。

```python
class PresenceRule(BaseModel):
    rule_id: str
    target_kind: str          # field | section
    target_id: str
    expression: str
    depends_on_fields: list[str]
    description: str | None = None
```

示例：

- `auth_section present when auth_present == 1`
- `password present when auth_type == SIMPLE_PASSWORD`

### 6.2 ValidationRule

用于表达长度、保留位、常量、枚举合法性等约束。

```python
class ValidationRule(BaseModel):
    rule_id: str
    target_kind: str | None   # field | section | message
    target_id: str | None
    kind: str
    expression: str
    severity: str = "error"
    depends_on_fields: list[str] = []
    description: str | None = None
```

示例：

- `reserved == 0`
- `auth_len == 24 when auth_type in {2, 3}`
- `auth_len == 28 when auth_type in {4, 5}`

---

## 7. 表达式约束：v1 必须使用受限 DSL

第一版不建议直接引入完整 AST，但也不能允许表达式是完全自由的自然语言字符串。

因此，`PresenceRule.expression` 和 `ValidationRule.expression` 必须使用受限 DSL。

### 7.1 允许的表达式形式

v1 仅允许以下几类表达式：

1. 字段与常量比较
   - `auth_type == 1`
   - `reserved == 0`

2. 字段属于枚举集合
   - `auth_type in {2,3}`

3. 简单逻辑组合
   - `auth_present == 1 and auth_type in {2,3}`

4. 简单算术比较
   - `auth_len == 24`
   - `payload_len == header_len + data_len`

5. 条件约束等价改写
   - `auth_type in {2,3} -> auth_len == 24`

### 7.2 字段引用规范

v1 必须固定字段标识符的引用方式，否则 parser、depends_on 提取和 codegen 会不稳定。

规则如下：

1. 表达式中的字段一律使用 `canonical_name`；
2. 若需要 section 作用域，使用 `section_canonical_name.field_canonical_name`；
3. 禁止使用 display name；
4. 禁止使用原始文档字段名、表格标题名或自由别名；
5. `depends_on_fields` 中保存的也必须是同一套 canonical 引用。

示例：

- `auth.auth_type in {2,3}`
- `auth.auth_len == 24`
- `control.auth_present == 1 -> auth.reserved == 0`

### 7.3 不允许的表达式形式

v1 禁止以下内容：

- 自由自然语言描述；
- 模糊词，如 `usually`、`typically`、`should often`；
- 函数调用；
- 复杂嵌套括号与任意优先级；
- 运行时上下文依赖表达式。

### 7.4 设计原因

采用受限 DSL 的目的不是追求形式化，而是保证：

- 可以稳定解析；
- 可以翻译为 C 条件判断；
- 可以做依赖分析；
- 可以做冲突检测与规则去重。

---

## 8. 枚举域模型

RFC 中很多字段的取值语义不应只保留在 `description` 中，应明确为枚举域。

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

枚举域的用途包括：

- 生成 `enum` 或 `#define`；
- 生成值合法性检查；
- 生成更清晰的 switch/case；
- 辅助 rule expression 解析与校验。

---

## 9. CodegenHints 的定位

`CodegenHints` 不是协议语义，而是代码生成偏好。

```python
class CodegenHints(BaseModel):
    preferred_template: str
    generate_pack: bool = True
    generate_unpack: bool = True
    generate_validate: bool = True
    runtime_helpers: list[str] = []
```

其职责仅包括：

- 模板分派；
- 是否生成某类函数；
- 需要哪些运行时 helper。

`CodegenHints` 不得承载协议本体语义，否则会导致语义回流到模板层。

---

## 10. layout_kind 的重新定义

`layout_kind` 的作用是帮助 codegen 分派模板，但它不应被视为核心语义本体，而应视为 normalization 的派生属性。

第一版建议支持以下类别：

- `fixed_bytes`
  - 固定长度、字节对齐；
- `bitfield_packed`
  - 固定长度，但包含 bitfield；
- `optional_section`
  - 含可选 section；
- `variable_length`
  - 含变长字段或长度依赖；
- `composite`
  - 同时具备多种复杂特征。

推导原则如下：

- 如果仅固定长度且全部字节对齐，则为 `fixed_bytes`；
- 如果含 bitfield，但无可选段和变长字段，则为 `bitfield_packed`；
- 如果主要复杂性来自 presence rule，则为 `optional_section`；
- 如果存在显式长度依赖，则为 `variable_length`；
- 多种复杂特征同时出现时归为 `composite`。

因此，`layout_kind` 是分类标签，而不是建模主轴。

---

## 11. 字段顺序与布局的权威来源

这是 v1 最关键的实现约束之一。

### 11.1 权威顺序来源

最终 `pack/unpack` 的字段顺序必须以：

`MessageIR.normalized_field_order`

为唯一权威来源。

不得直接以以下任一项作为 codegen 顺序依据：

- section 中的 `field_ids` 原始顺序；
- 扫描阶段字段出现顺序；
- 文档节点遍历顺序；
- 模板中的手工排序逻辑。

### 11.2 normalized_field_order 的精确定义

`normalized_field_order` 表示逻辑编码顺序，而不是“无条件总会出现的实际字段序列”。

因此在 `optional_section` 和 `variable_length` 场景下：

- 它仍然定义字段的唯一逻辑顺序；
- 实际 `pack/unpack` 时，必须结合 `presence_rules` 过滤当前激活字段；
- codegen 不允许绕过该机制，在模板中私自添加“如果某字段不存在就跳过”的隐式特例。

可以理解为：

- `normalized_field_order` 决定顺序；
- `presence_rules` 决定是否激活；
- 二者共同决定运行时的实际编码路径。

### 11.3 SectionIR 的作用

`SectionIR.field_ids` 仅用于：

- 保留文档局部组织关系；
- 提供 traceability；
- 辅助理解来源结构。

它不是最终布局顺序的权威来源。

### 11.4 resolved offset 的作用

若 `normalized_field_order` 与 `resolved_bit_offset` 都存在，则二者必须一致；
若冲突，则视为 normalization 错误，禁止 codegen。

---

## 12. 构建流程

`MessageIR` 不应由单个节点直接生成，而应通过“全局扫描 + 增量聚合 + 归一化”构建。

### 12.1 阶段一：建立 registry

维护 `message_ir_registry`：

- key：归一化后的消息身份；
- value：正在聚合的 `MessageIR` 草稿对象。

### 12.2 阶段二：节点产出增量贡献

每个相关节点不直接输出最终 `MessageIR`，而是产出若干增量贡献，例如：

- 新字段；
- 字段位宽；
- section 信息；
- 枚举值；
- presence rule；
- validation rule；
- source metadata。

### 12.3 阶段三：增量 merge

将节点贡献 merge 到目标 `MessageIR` 草稿对象中，逐步补全：

- 名称归一化；
- 字段定义；
- section 组织；
- 约束；
- 枚举；
- 来源信息。

### 12.4 阶段四：normalization / lowering

扫描完成后执行 normalization，统一得到可供 codegen 使用的工程对象。

输出必须完成以下事项：

- 生成 `normalized_field_order`；
- 计算全部 `resolved_bit_offset` / `resolved_byte_offset`；
- 推导 `resolved_bit_width`；
- 统一 `storage_type`；
- 归并重复规则；
- 检查冲突；
- 推导 `layout_kind`；
- 标记 `normalization_status = ready`。

只有进入 `ready` 状态的对象，才允许传给 codegen。

---

## 13. merge 与冲突处理策略

这是方案从“概念合理”走向“工程可用”的关键部分。

### 13.1 merge 基本原则

同一消息中的信息 merge 时，分为三类：

#### 13.1.1 可直接合并

例如：

- 新增 source page；
- 新增别名；
- 为同一字段补充 description；
- 为同一字段补充 enum domain；
- 新增不冲突规则。

这类信息可直接并入。

#### 13.1.2 可补全但不可覆盖

例如：

- `declared_bit_width` 原为空，新节点提供值；
- `storage_type` 原为空，normalization 推导得到值；
- `description` 原为空，新节点补充。

这类信息只能在原值缺失时补全，不允许无理由覆盖。

#### 13.1.3 需要显式冲突标记

例如：

- 同一字段两个不同 bit width；
- 同一字段两个不同固定常量；
- 同一顺序位置出现不同字段；
- 两条规则逻辑互相矛盾。

这类情况必须记录为冲突，进入 `conflicts` 集合或 diagnostics 中，禁止进入 codegen。

### 13.2 冲突处理建议

建议在实现中维护 diagnostics，例如：

```python
class IRDiagnostic(BaseModel):
    level: str       # warning | error
    code: str
    message: str
    source_pages: list[int] = []
    source_node_ids: list[str] = []
```

冲突策略建议如下：

- `warning`：可继续 normalization；
- `error`：禁止进入 codegen；
- 同一对象若存在未解决 `error`，则 `normalization_status != ready`。

---

## 14. normalization 最低输出要求

当一个 `MessageIR` 被视为“可用于 codegen”时，至少必须满足：

1. `canonical_name` 已确定；
2. `normalized_field_order` 已确定；
3. 所有参与编码的字段均有 `resolved_bit_width`；
4. 所有参与编码的字段均可确定 `storage_type`；
5. 所有引用型 rule 的 `depends_on_fields` 可解析；
6. 若存在 offset，则 `resolved_offset` 无冲突；
7. 无阻塞级 diagnostics；
8. `layout_kind` 已推导；
9. `normalization_status == ready`。

对于可选字段或变长字段，还应额外满足：

10. `presence_rules` 与 `normalized_field_order` 可共同推导出激活字段序列；
11. 若总长度不可静态唯一确定，则 `total_size_bits` 可以为 `None`，但 `min_size_bits / max_size_bits` 至少应可推导其一。

---

## 15. 与现有对象的关系

### 15.1 ProtocolMessage

定位：

- 文档抽取结果；
- 偏文档层；
- 可以不完整；
- 可以保留冗余或局部描述。

### 15.2 MessageIR

定位：

- 工程归一化结果；
- 偏实现层；
- 应尽可能完整；
- 应可直接驱动代码生成与验证。

### 15.3 .h/.c Skeleton

定位：

- `MessageIR` 的工程输出形式；
- 负责文件结构、函数签名、类型组织；
- 不承担协议语义推断。

因此三者关系不是替代关系，而是流水线关系：

- `ProtocolMessage` 提供抽取内容；
- `MessageIR` 提供实现语义；
- skeleton 提供工程组织；
- real implementation 消费已归一化 IR。

---

## 16. BFD 上的 v1 最小落地范围

这一节必须与当前真实上游 schema 保持一致，否则 lowering 实现会在“合并 message”还是“拆分 message”之间摇摆。

### 16.1 v1 的对象粒度决策

对于当前项目，`MessageIR v1` 明确采用：

**沿用当前真实 schema 中的合并后 message 作为 lowering 目标对象，不在 `Schema -> MessageIR` 阶段再拆成 5 个更细粒度 message。**

也就是说：

- 上游若已经将 `Keyed MD5 / Meticulous Keyed MD5` 合并建模；
- 上游若已经将 `Keyed SHA1 / Meticulous Keyed SHA1` 合并建模；
- 则 `MessageIR v1` 保持同样粒度。

差异化语义通过以下方式表达：

- `enum_domains` 中的 `auth_type` 值域；
- `presence_rules`；
- `validation_rules`；
- 必要时通过 optional fields / optional sections 表达。

### 16.2 为什么 v1 不拆分更细粒度对象

原因如下：

- 与当前 schema 一致，降低 lowering 实现复杂度；
- 避免在 v1 阶段引入二次对象拆分策略；
- 让“对象归并”和“条件约束表达”分别解决，不互相耦合；
- 更符合当前先打通真实 `pack/unpack/validate` 的目标。

### 16.3 后续扩展空间

若未来发现：

- 合并对象导致规则过多；
- codegen 分派显著复杂；
- 测试样例组织困难；

再考虑在 v2 中引入“schema 对象拆分为多个 MessageIR 派生对象”的机制。

但这不是 v1 的目标。

### 16.4 v1 建议覆盖范围

第一版建议聚焦 BFD 认证段的合并后对象，优先打通：

- `Simple Password Authentication Section`；
- 合并后的 `Keyed MD5 Authentication Section`；
- 合并后的 `Keyed SHA1 Authentication Section`。

`Generic BFD Control Packet` 放在第二阶段更合理，因为它涉及更多 bitfield 与组合布局问题。

---

## 17. 第一版验收标准

若要判断 `MessageIR v1` 是否设计合理，建议以以下标准验收：

1. 能从现有 `ProtocolMessage` 稳定 lowering 到 `MessageIR`；
2. lowering 输出对象粒度与当前真实 schema 保持一致；
3. 能覆盖 BFD 认证段的真实字段布局；
4. 能表达 `Auth Type`、`Auth Len`、`Reserved` 等约束；
5. 能通过 `normalized_field_order + presence_rules` 表达条件字段顺序；
6. 能生成真实 `pack/unpack`；
7. 能生成至少一组 roundtrip 测试；
8. codegen 不再依赖模板中的协议特例硬编码；
9. 出现冲突时可产生 diagnostics，而不是静默覆盖；
10. 只有 `normalization_status == ready` 的对象才能进入 codegen。

---

## 18. 结论

`MessageIR` 不是对现有消息对象的简单重命名，而是一层明确面向工程实现的中间表示。

它的核心价值在于：

- 将分散在多个章节中的消息语义重新聚合；
- 将文档层消息对象转化为实现层消息对象；
- 为真实 `pack/unpack`、`validate`、roundtrip test 提供稳定输入；
- 让 codegen 依赖统一、可检查、可追踪的 IR，而不是模板中的隐式知识。

在表示形式上，最合理的方案不是“纯树”，而是：

- 以 `MessageIR` 为中心；
- 用局部树或 section 表达结构；
- 用规则对象表达条件与约束；
- 用 normalization 产物作为 codegen 的唯一权威输入。

对于当前项目，最务实的推进路线是：

先实现 `MessageIR v1`，沿用当前 schema 的合并后 message 粒度，只覆盖 BFD 认证段，打通真实 `pack/unpack/validate/test` 流程；在该链路稳定后，再扩展到更复杂的控制报文与组合布局场景。
