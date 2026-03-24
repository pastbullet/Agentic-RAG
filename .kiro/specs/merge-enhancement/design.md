# 设计文档：MERGE 阶段增强 — Phase 1（Merge Enhancement）

## 概述

本设计描述协议提取流水线 MERGE 阶段的 Phase 1 增强方案。改动集中在两个文件：`src/extract/merge.py`（已实现）和 `src/extract/pipeline.py`（需集成）。不修改 `src/models.py`，不新增模块文件。

核心改动：
1. EXTRACT 阶段末尾新增 ExtractionRecord 构建与 `extract_results.json` 落盘
2. MERGE 阶段从简单 append 改为：空结果过滤 → timer/message 同名合并 → 构建 ProtocolSchema → 落盘 `merge_report.json`

设计原则：改动面最小化，不影响 CLASSIFY/EXTRACT 的已有逻辑，不改变 ProtocolSchema 数据模型。

### 数据流变更

```
EXTRACT 阶段（现有）                    EXTRACT 阶段（改后）
─────────────────────                  ─────────────────────
节点 → 提取器 → 结果列表               节点 → 提取器 → 结果列表
                                                         ↓
                                       构建 ExtractionRecord 列表
                                                         ↓
                                       落盘 extract_results.json

MERGE 阶段（现有）                      MERGE 阶段（改后）
──────────────────                     ──────────────────
结果列表 → _merge_to_schema            结果列表
    → append 到 ProtocolSchema              ↓
    → 落盘 protocol_schema.json        ① 空结果过滤（5 种类型）
                                            ↓
                                       ② merge_timers（同名合并）
                                       ③ merge_messages（同名合并+字段去重）
                                       ④ state_machines/procedures/errors 直通
                                            ↓
                                       ⑤ 构建 ProtocolSchema
                                       ⑥ build_merge_report
                                            ↓
                                       落盘 protocol_schema.json
                                       落盘 merge_report.json
```

## 架构

### 文件变更范围

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/extract/merge.py` | 已完成 | ExtractionRecord、normalize_name、空结果过滤、merge_timers、merge_messages、build_merge_report |
| `src/extract/pipeline.py` | 需修改 | EXTRACT 阶段增加 record 构建+落盘；MERGE 阶段替换为调用 merge.py |
| `tests/extract/test_merge.py` | 新增 | merge.py 的单元测试和属性测试 |

### 模块依赖关系

```
pipeline.py
    ├── merge.py（新增依赖）
    │     ├── ExtractionRecord
    │     ├── is_empty_*（5 个过滤函数）
    │     ├── merge_timers
    │     ├── merge_messages
    │     └── build_merge_report
    ├── classifier.py（不变）
    ├── extractors/（不变）
    └── models.py（不变）
```

## 组件与接口

### 1. merge.py — 已实现的合并模块

以下接口已在 `src/extract/merge.py` 中实现，此处仅记录签名供 pipeline 集成参考。

```python
@dataclass
class ExtractionRecord:
    node_id: str
    title: str
    label: str
    confidence: float
    source_pages: list[int]
    payload: dict  # model_dump() of the extracted pydantic object

def normalize_name(text: str) -> str:
    """保守归一化：去章节号、去 RFC 引用、去标点、小写。
    不删除 state machine / procedure / overview 等语义词汇。"""

def is_empty_state_machine(obj: ProtocolStateMachine) -> bool: ...
def is_empty_message(obj: ProtocolMessage) -> bool: ...
def is_empty_procedure(obj: ProcedureRule) -> bool: ...
def is_empty_timer(obj: TimerConfig) -> bool: ...
def is_empty_error(obj: ErrorRule) -> bool: ...

def merge_timers(timers: list[TimerConfig]) -> tuple[list[TimerConfig], list[dict]]:
    """按 normalize_name(timer_name) 分组合并。
    返回 (merged_list, report_groups)。"""

def merge_messages(messages: list[ProtocolMessage]) -> tuple[list[ProtocolMessage], list[dict]]:
    """按 normalize_name(name) 分组合并，字段按名称去重。
    返回 (merged_list, report_groups)。"""

def build_merge_report(
    pre: dict[str, int],
    dropped: dict[str, int],
    post: dict[str, int],
    timer_groups: list[dict],
    message_groups: list[dict],
) -> dict[str, Any]:
    """生成合并统计报告字典。"""
```

### 2. pipeline.py — EXTRACT 阶段变更

#### 2.1 ExtractionRecord 构建

在 EXTRACT 阶段的提取循环中，每次成功提取后，除了将结果追加到对应类型列表外，同时构建一个 ExtractionRecord 并追加到 `records` 列表。

```python
# 在 EXTRACT 阶段循环内，每次成功提取后追加：
from src.extract.merge import ExtractionRecord

record = ExtractionRecord(
    node_id=node_id,
    title=str(node.get("title", "")),
    label=label.label,
    confidence=label.confidence,
    source_pages=get_node_pages(node),
    payload=result.model_dump(),
)
records.append(record)
```

#### 2.2 extract_results.json 落盘

EXTRACT 阶段循环结束后、构建 StageResult 之前，将 records 序列化并写入文件：

```python
import dataclasses, json

extract_results_path = Path("data/out") / f"{doc_stem}_extract_results.json"
extract_results_path.parent.mkdir(parents=True, exist_ok=True)
extract_results_path.write_text(
    json.dumps([dataclasses.asdict(r) for r in records], ensure_ascii=False, indent=2),
    encoding="utf-8",
)
# 在 stage_data 中记录路径
stage_data["extract_results_path"] = str(extract_results_path)
```

### 3. pipeline.py — MERGE 阶段变更

#### 3.1 替换 _merge_to_schema 调用

当前 MERGE 阶段直接调用 `_merge_to_schema` 做 append。改为以下流程：

```python
from src.extract.merge import (
    is_empty_state_machine, is_empty_message, is_empty_procedure,
    is_empty_timer, is_empty_error,
    merge_timers, merge_messages, build_merge_report,
)

# ① 记录合并前数量
pre_counts = {
    "state_machine": len(state_machines),
    "message": len(messages),
    "procedure": len(procedures),
    "timer": len(timers),
    "error": len(errors),
}

# ② 空结果过滤
filtered_sm = [s for s in state_machines if not is_empty_state_machine(s)]
filtered_msg = [m for m in messages if not is_empty_message(m)]
filtered_proc = [p for p in procedures if not is_empty_procedure(p)]
filtered_tmr = [t for t in timers if not is_empty_timer(t)]
filtered_err = [e for e in errors if not is_empty_error(e)]

dropped_counts = {
    "state_machine": len(state_machines) - len(filtered_sm),
    "message": len(messages) - len(filtered_msg),
    "procedure": len(procedures) - len(filtered_proc),
    "timer": len(timers) - len(filtered_tmr),
    "error": len(errors) - len(filtered_err),
}

# ③ 同名合并（Phase 1 仅 timer + message）
merged_timers, timer_groups = merge_timers(filtered_tmr)
merged_messages, message_groups = merge_messages(filtered_msg)

# ④ state_machines / procedures / errors 直通（Phase 1 不做同名合并）
merged_sm = filtered_sm
merged_proc = filtered_proc
merged_err = filtered_err

# ⑤ 构建 ProtocolSchema
schema = _merge_to_schema(
    doc_stem=doc_stem,
    source_document=doc_name,
    state_machines=merged_sm,
    messages=merged_messages,
    procedures=merged_proc,
    timers=merged_timers,
    errors=merged_err,
)

# ⑥ 合并报告
post_counts = {
    "state_machine": len(merged_sm),
    "message": len(merged_messages),
    "procedure": len(merged_proc),
    "timer": len(merged_timers),
    "error": len(merged_err),
}
merge_report = build_merge_report(
    pre=pre_counts,
    dropped=dropped_counts,
    post=post_counts,
    timer_groups=timer_groups,
    message_groups=message_groups,
)

# ⑦ 落盘
schema_path = Path("data/out") / f"{doc_stem}_protocol_schema.json"
schema_path.parent.mkdir(parents=True, exist_ok=True)
schema_path.write_text(schema.model_dump_json(indent=2), encoding="utf-8")

report_path = Path("data/out") / f"{doc_stem}_merge_report.json"
report_path.write_text(
    json.dumps(merge_report, ensure_ascii=False, indent=2), encoding="utf-8"
)
```

## 数据模型

### 不修改现有模型

本次改动不修改 `src/models.py`。所有新增数据结构（ExtractionRecord、merge report dict）均在 `merge.py` 内部定义或以普通 dict 形式存在。

### 持久化文件格式

| 文件 | 路径 | 格式 | 新增/修改 |
|------|------|------|----------|
| 中间提取结果 | `data/out/{doc_stem}_extract_results.json` | `[ExtractionRecord.asdict()]` | 新增 |
| 合并统计报告 | `data/out/{doc_stem}_merge_report.json` | merge report dict | 新增 |
| 协议 Schema | `data/out/{doc_stem}_protocol_schema.json` | `ProtocolSchema.model_dump_json()` | 不变（内容质量提升） |

### extract_results.json 示例

```json
[
  {
    "node_id": "1.2.3",
    "title": "6.8.1 State Variables",
    "label": "state_machine",
    "confidence": 0.95,
    "source_pages": [28, 29],
    "payload": {
      "name": "BFD State Machine",
      "states": [...],
      "transitions": [...],
      "source_pages": [28, 29]
    }
  }
]
```

### merge_report.json 示例

```json
{
  "pre_merge_counts": {
    "state_machine": 11,
    "message": 9,
    "procedure": 7,
    "timer": 9,
    "error": 0
  },
  "dropped_empty_counts": {
    "state_machine": 0,
    "message": 1,
    "procedure": 0,
    "timer": 0,
    "error": 0
  },
  "post_merge_counts": {
    "state_machine": 11,
    "message": 8,
    "procedure": 7,
    "timer": 1,
    "error": 0
  },
  "merged_groups": {
    "timer": [
      {
        "normalized_key": "detection time",
        "merged_from": ["Detection Time", "Detection Time", ...],
        "source_pages_union": [10, 15, 20, 28, 30, 35, 38, 40, 42],
        "timeout_value_variants": ["bfd.DetectTime", "..."]
      }
    ],
    "message": []
  }
}
```

## 正确性属性（Correctness Properties）

### Property 1: 空结果过滤不丢失有效对象

*For any* 提取结果列表，空结果过滤后保留的对象集合应是原始列表的子集，且每个被过滤的对象都满足对应类型的空判定条件（如 message 的 fields 为空列表）。

**Validates: 需求 2.1, 2.2**

### Property 2: 同名合并单调递减

*For any* TimerConfig 列表或 ProtocolMessage 列表，合并后的列表长度应小于或等于合并前的列表长度。

**Validates: 需求 3.5, 4.6**

### Property 3: source_pages 并集完整性

*For any* 被合并的同名对象组，合并后对象的 source_pages 应包含组内所有原始对象的 source_pages 的并集。

**Validates: 需求 3.6, 4.7**

### Property 4: 字段去重保留首次出现

*For any* 同名报文组的字段合并，去重后每个归一化字段名仅出现一次，且保留首次出现的原始大小写。

**Validates: 需求 4.3**

### Property 5: merge report 数值一致性

*For any* 有效的 merge 执行，`pre_merge_counts[type] == dropped_empty_counts[type] + 进入合并的对象数`，且 `post_merge_counts[type] <= pre_merge_counts[type] - dropped_empty_counts[type]`。

**Validates: 需求 5.4, 5.5**

### Property 6: ExtractionRecord round-trip

*For any* 有效的 ExtractionRecord 列表，`json.loads(json.dumps([asdict(r) for r in records]))` 后重建的 ExtractionRecord 列表应与原始列表等价。

**Validates: 需求 7.1, 7.2**

### Property 7: normalize_name 保守性

*For any* 输入字符串，normalize_name 的输出不应删除 "state machine"、"procedure"、"overview" 等语义词汇，仅移除章节号、RFC 引用和标点符号。

**Validates: 需求 3.1, 4.1（隐含）**

## 错误处理

### 错误分类与处理策略

| 错误类型 | 触发场景 | 处理方式 |
|---------|---------|---------|
| extract_results.json 写入失败 | 磁盘空间不足、权限问题 | 记录警告日志，不阻断 EXTRACT 阶段（落盘是辅助功能） |
| merge_report.json 写入失败 | 磁盘空间不足、权限问题 | 记录警告日志，不阻断 MERGE 阶段 |
| 所有提取结果均为空 | 文档内容质量极差 | MERGE 阶段正常完成，输出空 ProtocolSchema，merge_report 记录全部 dropped |

### 日志规范

```python
# 空结果过滤
logger.info("Merge: dropped %d empty objects (%s)", total_dropped, dropped_counts)

# 合并统计
logger.info("Merge: timers %d→%d, messages %d→%d",
            pre_counts["timer"], post_counts["timer"],
            pre_counts["message"], post_counts["message"])

# 落盘
logger.info("Saved extract_results to %s", extract_results_path)
logger.info("Saved merge_report to %s", report_path)
```

## 测试策略

### 测试文件

```
tests/extract/
    test_merge.py          # merge.py 的单元测试 + 属性测试（新增）
    test_pipeline.py       # pipeline.py 的集成测试（已有，需补充 MERGE 阶段测试）
```

### 单元测试（test_merge.py）

| 测试函数 | 覆盖需求 | 说明 |
|---------|---------|------|
| test_normalize_name_removes_section_numbers | 8.1 | 验证 "6.8.1 BFD State Machine" → "bfd state machine" |
| test_normalize_name_removes_rfc_references | 8.1 | 验证 "RFC 5880 Detection Time" → "detection time" |
| test_normalize_name_preserves_semantic_words | 8.1 | 验证 "state machine" / "procedure" / "overview" 保留 |
| test_is_empty_* (5 个) | 8.2 | 每个空判定函数的空/非空两种情况 |
| test_merge_timers_single_no_merge | 8.3 | 单个定时器不合并 |
| test_merge_timers_same_name_merge | 8.3 | 多个同名定时器合并，验证 source_pages 并集 |
| test_merge_timers_timeout_variants | 8.3 | 验证 timeout_value_variants 记录 |
| test_merge_messages_single_no_merge | 8.4 | 单个报文不合并 |
| test_merge_messages_same_name_merge | 8.4 | 多个同名报文合并 |
| test_merge_messages_field_dedup | 8.4 | 字段去重：size_bits 优先非 null、description 优先较长 |
| test_build_merge_report_structure | 8.5 | 报告结构完整性 |

### 属性测试（test_merge.py，使用 Hypothesis）

| 属性测试 | 对应 Property | 说明 |
|---------|-------------|------|
| test_prop_merge_timers_monotonic | Property 2 | 合并后数量 ≤ 合并前 |
| test_prop_merge_messages_monotonic | Property 2 | 合并后数量 ≤ 合并前 |
| test_prop_merge_timers_source_pages_superset | Property 3 | source_pages 并集完整 |
| test_prop_merge_messages_source_pages_superset | Property 3 | source_pages 并集完整 |
| test_prop_extraction_record_roundtrip | Property 6 | JSON round-trip |

### 属性测试配置

- 每个属性测试至少 100 次迭代：`@settings(max_examples=100)`
- 每个测试用注释标注对应的设计属性：`# Feature: merge-enhancement, Property N: {title}`
