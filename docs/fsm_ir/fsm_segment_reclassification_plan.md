# 基于同父“全部兄弟”的 FSM Segment 二次分类方案

## 1. 文档目的

本文档用于记录 `standalone FSM 收紧 + classifier sanity filter` 之后的下一步优化方向，并明确为什么本轮优先做 classify 侧的 segment reclassification，而不是直接进入 extractor batching。

当前已有结果：

1. `StateMachineExtractor` 已只抽取 standalone FSM；
2. `classifier` 已完成 standalone FSM 收紧与通用 sanity filter；
3. `outline context` 已能给 state_machine extractor 提供轻量章节上下文；
4. TCP 的 `classified_state_machine_count` 已从 `25 -> 6`；
5. TCP 的 `merge_state_machine_count` 已从 `23 -> 5`；
6. BFD 已恢复为 `classified_state_machine_count = 1`、`merge_state_machine_count = 1`。

这说明：

- 单节点分类 + 局部 heuristic 已能消除大量明显伪 FSM；
- 剩余误判主要集中在“同一父章节下的一组并列兄弟节点”；
- 再继续堆标题 heuristic，收益会快速递减。

因此，下一步最合理的路径是：

> 基于 `page_index` 的父子结构，对剩余 `state_machine` 锚点所在父章节做一次保守的 segment 二次分类。

---

## 2. 为什么不是滑动窗口或 batching

### 2.1 滑动窗口的问题

滑动窗口的问题是：

1. 它是盲扫；
2. 大量窗口没有 FSM 节点；
3. 会浪费 LLM 调用；
4. 不能天然复用文档树结构。

### 2.2 直接做 extractor batching 的问题

segment 直接喂给 `StateMachineExtractor` 虽然可能进一步压低伪 FSM，但它会直接触碰：

- `ExtractionRecord`
- hydrate/provenance
- merge-only 重跑语义
- batched payload 的 artifacts 写回策略

这已经接近 Step 3 batching，不适合作为当前的小批次 follow-up。

### 2.3 为什么 classifier 二次分类更合适

segment 只用于 classifier 二次分类的好处是：

1. 第一遍 classify 和 extract 主流程保持不变；
2. extract 仍按节点逐个执行；
3. 不改 `ExtractionRecord`、merge、hydrate、provenance；
4. 只对少量模糊 segment 额外增加 LLM 调用；
5. 与现有 sanity filter 可自然叠加。

---

## 3. 核心策略

新的 classify 流程应为：

```text
第一遍 classify（逐节点，现有）
-> sanity filter（现有）
-> 基于剩余 state_machine 构建 FSM segments（新增）
-> 对多节点 segment 做二次分类（新增）
-> 仅更新 target_node_ids 中当前仍是 state_machine 的节点（新增）
-> 将最终标签回写 node_labels.json（新增）
-> extract（仍逐节点，不改）
```

本轮目标：

1. 在不进入 batching 的前提下继续降低 TCP 伪 FSM；
2. 保持 BFD 不回归；
3. 让 `classify -> extract-only` 保持一致，即二次分类后的标签能够被后续独立 `EXTRACT` 复用。

本轮非目标：

- segment 级 extractor batching
- `ExtractionRecord` 结构修改
- merge/provenance/cache 总体设计调整
- 并发化 segment reclassification

---

## 4. 数据结构

## 4.1 `OutlineContext`

在 pipeline 内部扩展：

```python
@dataclass(frozen=True)
class OutlineContext:
    section_path: list[str]
    parent_heading: str = ""
    parent_node_id: str = ""
    sibling_titles: list[str] = field(default_factory=list)
```

约束：

1. `parent_node_id` 仅用于 pipeline 内部分组；
2. 不写入 public artifact；
3. 根节点叶子没有父节点时，`parent_node_id = ""`。

## 4.2 `FsmSegment`

在 `pipeline.py` 中新增 classify 私有 dataclass：

```python
@dataclass(frozen=True)
class FsmSegment:
    anchor_node_id: str
    parent_node_id: str
    parent_heading: str
    node_ids: list[str]
    target_node_ids: list[str]
```

含义：

- `node_ids`：该父节点下的全部兄弟叶子节点，按文档顺序保存，包含锚点前后的节点；
- `target_node_ids`：其中当前仍被标成 `state_machine` 的节点，也是二次分类唯一允许修改的节点。

---

## 5. Segment 构建规则

### 5.1 关键修正：不是“向后扩展”，而是“收集全部兄弟”

旧版本方案中最大的结构性问题，是从 FSM 锚点只向后扩展。这会丢掉锚点前方的关键兄弟上下文。

例如 TCP 的 `§3.9`：

- `OPEN Call`
- `SEND Call`
- `RECEIVE Call`
- `CLOSE Call`
- `ABORT Call`
- `STATUS Call`

若锚点是 `SEND Call`，只向后扩展会丢掉 `OPEN Call`，而 `OPEN Call` 恰恰是“这一组是并列 call handler”最强的结构信号。

因此，segment 必须改为：

> 一旦发现某个节点当前仍为 `state_machine`，就按其 `parent_node_id` 收集该父节点下的全部兄弟叶子节点。

### 5.2 构建原则

1. 先从 leaf nodes 建立 `parent_node_id -> sibling leaf node_ids` 索引；
2. 顺序扫描 leaf nodes；
3. 遇到当前仍为 `state_machine` 的节点时，视为锚点；
4. 若该锚点有 `parent_node_id` 且该父节点尚未建 segment，则为该父节点构建一个 segment；
5. `node_ids` 直接取该父节点下的全部兄弟叶子节点；
6. `target_node_ids` 取这些兄弟中当前仍为 `state_machine` 的子集；
7. 同一个 `parent_node_id` 只生成一个 segment。

### 5.3 不生成或跳过的情况

以下情况不进入二次分类：

1. `parent_node_id` 为空：记 `no_parent`
2. `len(node_ids) <= 1`：记 `single_node`
3. `target_node_ids` 为空：记 `empty_targets`
4. 渲染后的 segment 文本超过长度上限：记 `over_limit`

其中第 1-3 条属于 segment 构建期跳过，第 4 条属于发送前跳过。

---

## 6. 二次分类设计

## 6.1 输入内容

segment 渲染时只放：

- `Parent heading`
- 每个 sibling 的 `Node ID`
- `Title`
- `Current label`
- `Text`

不放 summary，避免把噪声重新带回分类链路。

### 6.2 输出约束

系统 prompt 只允许输出：

```json
{
  "updates": [
    {
      "node_id": "0031",
      "label": "procedure_rule",
      "confidence": 0.91,
      "rationale": "..."
    }
  ]
}
```

限制：

1. 只能返回 `target_node_ids` 中的节点；
2. `label` 只允许：
   - `state_machine`
   - `procedure_rule`
   - `general_description`
3. 不允许新增节点；
4. 不允许修改非目标节点。

### 6.3 更新策略

二次分类输出应用到标签时：

1. 只处理目标列表中的节点；
2. 非法 `node_id` 忽略并记 warning；
3. 非法 `label` 忽略并记 warning；
4. 未出现在 `updates` 中的目标节点保持原样；
5. 若返回的 label 与原 label 相同，视为 no-op，不计入 `fsm_segment_updated_node_count`。

### 6.4 失败策略

以下情况都不应让 classify stage 失败：

- segment 文本超限
- LLM 调用失败
- JSON 解析失败
- 返回结构不合法

策略统一为：保留原标签，并将原因计入 `fsm_segment_skip_reasons`。

---

## 7. 缓存与落盘

这轮虽然第一遍 classifier prompt 本身不变，但 classify 的最终语义已经变化，所以必须 bump `classifier.py` 的 `PROMPT_VERSION`。

另外，segment reclassification 完成后：

- 若标签实际发生变化，需要将最终标签回写到 `node_labels.json`
- 同时写回对应的 `node_labels.meta.json`

这样才能保证：

> 先跑一次 `CLASSIFY`，再单独跑 `EXTRACT` 时，extract-only 读到的是 refine 后标签，而不是第一遍 classify 的旧结果。

本轮不新增新的 public artifact schema；仍复用现有 `node_labels.json`。

---

## 8. 可观测性

classify stage data 新增以下统计：

- `fsm_segment_count`
- `fsm_segment_reclassified_count`
- `fsm_segment_updated_node_count`
- `fsm_segment_skipped_count`
- `fsm_segment_skip_reasons`

定义：

- `fsm_segment_count`：成功构建出的 segment 数
- `fsm_segment_reclassified_count`：真正发给 LLM 的 segment 数
- `fsm_segment_updated_node_count`：最终 label 实际发生变化的目标节点数
- `fsm_segment_skipped_count`：未进入或未完成二次分类的次数
- `fsm_segment_skip_reasons`：按 `no_parent / single_node / empty_targets / over_limit / llm_error / invalid_response` 聚合

CLI 需要像当前其它 classify 指标一样直接打印这些字段。

---

## 9. 测试重点

建议锁住以下场景：

1. `OutlineContext` 能正确恢复 `parent_node_id`
2. segment 构建按“同父全部兄弟”工作，而不是只拿锚点后的节点
3. 锚点位于兄弟列表中间时，`node_ids` 仍包含前后兄弟，`target_node_ids` 只含当前 FSM 目标
4. 同一个父节点下多个 FSM 目标只生成一个 segment
5. 二次分类只更新 `target_node_ids`
6. 同 label 返回视为 no-op，不计 update
7. 单节点父章节不触发二次分类
8. segment 超限、LLM 错误、非法返回都保留原标签并记录 skip reason
9. classify 产物回写后，后续 `extract-only` 读取的是 refine 后标签
10. CLI 输出包含新增的 segment 统计字段

---

## 10. 验证顺序与门槛

建议验证顺序：

1. 跑 `pytest tests/extract/test_pipeline.py tests/extract/test_classifier.py tests/test_run_extract_pipeline.py`
2. 先跑 TCP `classify,extract`
3. 若 TCP `classified_state_machine_count <= 4` 且 `extract_state_machine_count <= 4`，再跑 TCP 全链路
4. 最后回跑 BFD 全链路

接受标准：

- TCP：
  - `classified_state_machine_count <= 4`
  - `extract_state_machine_count <= 4`
  - `merge_state_machine_count <= 4`
- BFD：
  - `classified_state_machine_count = 1`
  - `merge_state_machine_count = 1`
  - 不出现新的 extractor validation error

若本轮后 TCP 仍明显高于 `6` 个 FSM，则停止继续在 classify 侧堆逻辑，重新评估 Step 3 batching。

---

## 11. 结论

在 standalone FSM 收紧和通用 sanity filter 已验证有效的前提下，下一步最合理的优化不是继续堆 heuristic，也不是直接进入 batching，而是：

> 利用 `page_index` 的父子结构，对剩余 FSM 锚点所在父章节的全部兄弟叶子节点做一次保守的 classifier 二次分类。

这条路径的优点是：

1. 有锚点，只处理少量真正模糊的节点组；
2. 低风险，不触碰 extractor/merge/provenance；
3. 高解释性，把“为什么这些节点应该一起看”直接落在文档树结构上。
