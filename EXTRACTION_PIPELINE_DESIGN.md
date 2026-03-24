# 协议提取 Pipeline 设计方案

> 毕设核心主线：协议文档 → 节点分类 → 结构化提取 → 代码生成 → 验证

---

## 一、为什么需要节点分类体系

协议规范文本中混合存在状态描述、报文定义、处理过程、定时机制以及错误处理等多种异构语义。若直接对原始节点进行统一抽取，容易导致任务边界模糊、抽取结果混杂、后续代码生成映射不清。

因此，本方案在文档结构树基础上引入**协议节点分类体系**，将叶节点按其主要实现语义划分为六类，并根据分类结果采用专用抽取器进行结构化建模。该设计在协议文档与代码生成之间建立了稳定的中间表示层，提高了抽取准确性与系统可扩展性。

---

## 二、整体 Pipeline

```
page_index.json（文档结构树）
    ↓
遍历所有叶节点
    ↓
Stage 1: 节点语义分类
    每个叶节点 → NodeSemanticLabel（label + confidence + rationale）
    结果持久化到 data/out/{doc_stem}_node_labels.json
    ↓
Stage 2: 按 label 路由到专用 Extractor
    state_machine    → StateMachineExtractor → ProtocolStateMachine
    message_format   → MessageExtractor      → ProtocolMessage
    procedure_rule   → ProcedureExtractor    → 规则列表（暂存）
    timer_rule       → TimerExtractor        → 定时器配置
    error_handling   → ErrorExtractor        → 错误处理规则
    general_description → 跳过（可选保留给 QA）
    ↓
Stage 3: 合并为 ProtocolSchema
    ↓
Stage 4: 代码生成（codegen）
    ↓
Stage 5: 验证（verify）
```

---

## 三、六类节点标签定义

### 分类原则

按**协议实现语义**分类，不按文本风格分类。分类标准是"这段文本在最终实现中应该落到什么代码或规则上"。

每个节点只选一个**主标签**。若节点同时涉及多类信息，按优先级选主标签，其余信息记入 `secondary_hints`。

### 标签定义

| 标签 | 含义 | 典型特征 | 输出目标 |
|------|------|---------|---------|
| `state_machine` | 包含状态集合、状态转移、触发事件或进入退出条件 | 出现状态名、"transition to/enter/remain in"、状态变化描述 | `ProtocolStateMachine` |
| `message_format` | 描述报文/帧/TLV/header/字段布局/bit 位定义 | 字段名、bit 长度、offset、reserved、flag、表格 | `ProtocolMessage` |
| `procedure_rule` | 描述处理流程/顺序步骤，但无完整状态结构 | 收到某报文后的处理步骤、协商流程、握手过程 | 规则列表/handler 函数 |
| `timer_rule` | 描述超时/周期发送/保活/重传等时序机制 | 定时发送、超时宣告不可达、重传间隔 | 定时器配置/timeout handler |
| `error_handling` | 描述异常条件/非法值/丢弃/错误恢复 | 字段非法则丢弃、收到未知类型时忽略 | 错误处理规则/guard 函数 |
| `general_description` | 背景介绍/术语/设计动机/非规范性建议 | 历史说明、兼容性备注、实现建议 | 跳过（保留给 QA） |

### `procedure_rule` 的排他规则（关键）

若文本能够识别出显式或隐式的**状态上下文、触发事件、行为动作、后继状态**中的大部分要素，则优先归为 `state_machine`；仅当文本只描述顺序处理流程、局部操作步骤或控制程序，且**无法稳定映射为状态转移关系**时，才归为 `procedure_rule`。

简化判断模板：若能套入 "在状态 X 下，收到事件 Y，执行动作 Z，转移到状态 W"，即使不完整，也优先标为 `state_machine`。

---

## 四、优先级配置

优先级用于冲突消解（节点同时符合多类时选哪个）。优先级是**文档相关的**，不同协议可以有不同配置。

### 默认优先级

```python
DEFAULT_LABEL_PRIORITY = [
    "state_machine",
    "message_format",
    "timer_rule",
    "error_handling",
    "procedure_rule",
    "general_description",
]
```

### 文档特定覆盖示例

```python
# BFD 用默认（状态机是核心）
BFD_LABEL_PRIORITY = DEFAULT_LABEL_PRIORITY

# FC-LS 帧格式比重更大
FC_LS_LABEL_PRIORITY = [
    "message_format",
    "state_machine",
    "timer_rule",
    "error_handling",
    "procedure_rule",
    "general_description",
]
```

---

## 五、数据结构（`src/models.py`）

```python
NodeLabelType = Literal[
    "state_machine",
    "message_format",
    "procedure_rule",
    "timer_rule",
    "error_handling",
    "general_description",
]

class NodeSemanticLabel(BaseModel):
    node_id: str
    label: NodeLabelType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rationale: str = ""
    secondary_hints: list[str] = Field(default_factory=list)

class NodeLabelMeta(BaseModel):
    source_document: str
    model_name: str
    prompt_version: str
    label_priority: list[str]
    created_at: str
```

---

## 六、持久化格式

### 分类结果

`data/out/{doc_stem}_node_labels.json`

```json
{
  "0042": {
    "node_id": "0042",
    "label": "state_machine",
    "confidence": 0.95,
    "rationale": "Describes BFD session states and transitions triggered by control packets",
    "secondary_hints": ["has_timer"]
  },
  "0043": {
    "node_id": "0043",
    "label": "message_format",
    "confidence": 0.98,
    "rationale": "Defines BFD control packet header fields and bit layout",
    "secondary_hints": []
  }
}
```

### 运行元信息

`data/out/{doc_stem}_node_labels.meta.json`

```json
{
  "source_document": "rfc5880-BFD.pdf",
  "model_name": "gpt-4o-2024-11-20",
  "prompt_version": "v1.0",
  "label_priority": ["state_machine", "message_format", "timer_rule", "error_handling", "procedure_rule", "general_description"],
  "created_at": "2026-03-17T10:00:00"
}
```

### 人工修正覆盖

`data/out/{doc_stem}_node_labels.override.json`

```json
{
  "0055": {
    "label": "state_machine",
    "rationale": "manual override: poll sequence is modeled as state-related behavior"
  }
}
```

Pipeline 启动时，override 文件中的条目优先于自动分类结果。

---

## 七、Pipeline 缓存策略

```python
def load_or_classify(doc_stem, nodes, model, prompt_version, priority):
    labels_path = f"data/out/{doc_stem}_node_labels.json"
    meta_path   = f"data/out/{doc_stem}_node_labels.meta.json"
    override_path = f"data/out/{doc_stem}_node_labels.override.json"

    # 检查缓存是否有效
    if labels_path.exists() and meta_path.exists():
        meta = load_meta(meta_path)
        if (meta.model_name == model and
            meta.prompt_version == prompt_version and
            meta.label_priority == priority):
            labels = load_labels(labels_path)
            apply_overrides(labels, override_path)
            return labels

    # 缓存失效或不存在，重新分类
    labels = run_classification(nodes, model, priority)
    save_labels(labels, labels_path)
    save_meta(meta_path, model, prompt_version, priority)
    apply_overrides(labels, override_path)
    return labels
```

---

## 八、分类 Prompt 设计要点

分类 prompt 需要包含三部分：

1. **类别定义**：每类的含义和典型特征
2. **优先级规则**：冲突时选哪个
3. **排他规则**：尤其是 `procedure_rule` vs `state_machine` 的判断

输出格式要求 JSON，包含 `label`、`confidence`、`rationale`、`secondary_hints`。

示例 prompt 结构：

```
你是一个通信协议文档分析专家。请对以下文档节点进行语义分类。

节点标题：{title}
节点摘要：{summary}
节点原文（前500字）：{text_snippet}

分类标签（选一个主标签）：
- state_machine：包含状态名、状态转移、触发事件。判断标准：能否套入"在状态X下，收到事件Y，执行动作Z，转移到状态W"模板。
- message_format：主要定义报文/帧/字段/bit布局。
- timer_rule：主要描述超时/周期/保活/重传时序。
- error_handling：主要描述异常/非法值/丢弃/错误恢复。
- procedure_rule：描述处理流程/步骤，但无法稳定映射为状态转移。
- general_description：背景/术语/设计动机/非规范性内容。

优先级（冲突时）：{priority_list}

输出 JSON：
{
  "label": "...",
  "confidence": 0.0-1.0,
  "rationale": "一句话说明理由",
  "secondary_hints": ["可选的辅助标签，如 has_timer、mentions_field"]
}
```

---

## 九、目录结构规划

```
src/
  extract/
    __init__.py
    classifier.py       # Stage 1：节点语义分类
    extractors/
      __init__.py
      state_machine.py  # state_machine 专用提取器
      message.py        # message_format 专用提取器
      procedure.py      # procedure_rule 提取器
      timer.py          # timer_rule 提取器
      error.py          # error_handling 提取器
    pipeline.py         # 主流程编排
    codegen.py          # Stage 4：代码生成
    verify.py           # Stage 5：验证

data/out/
  {doc_stem}_node_labels.json          # 分类结果
  {doc_stem}_node_labels.meta.json     # 运行元信息
  {doc_stem}_node_labels.override.json # 人工修正（手动创建）
  {doc_stem}_protocol_schema.json      # 提取结果（ProtocolSchema）
```

---

## 十、实现优先级

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| P1 | `classifier.py`：节点分类 + 持久化 + override 加载 | 最高，先跑通 BFD |
| P2 | `extractors/state_machine.py`：状态机提取 | 高，BFD 核心 |
| P3 | `extractors/message.py`：帧结构提取 | 高，BFD + FC-LS 都需要 |
| P4 | `pipeline.py`：主流程编排 | 中，P1-P3 完成后整合 |
| P5 | `codegen.py`：代码生成 | 中 |
| P6 | `verify.py`：验证 | 中 |
| P7 | `extractors/procedure.py` 等其余提取器 | 低，先跑通主线 |
| P8 | Web UI 扩展（code preview、状态机可视化） | 最低，答辩前做 |

---

## 十一、论文表述参考

> 由于协议规范文本中混合存在状态描述、报文定义、处理过程、定时机制以及错误处理等多种异构语义，若直接对原始节点进行统一抽取，容易导致任务边界模糊、抽取结果混杂、后续代码生成映射不清。因此，本文在文档结构树基础上，引入协议节点分类体系，将叶节点按其主要实现语义划分为状态机、报文格式、处理规则、定时规则、错误处理和一般描述等类别，并根据分类结果采用专用抽取器进行结构化建模。该设计在协议文档与代码生成之间建立了稳定的中间表示层，提高了抽取准确性与系统可扩展性。
