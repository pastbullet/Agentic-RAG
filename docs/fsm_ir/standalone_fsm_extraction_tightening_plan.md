# Standalone FSM 抽取收紧方案

## 1. 文档目的

本文档用于整理当前对 TCP 行为层“状态机过碎、lower 困难、代码骨架空洞”的原因判断，并给出一套小改动、高 ROI、可分步落地的优化方案。

这份方案不重构主线架构，不改 `lower -> materialize -> align -> codegen` 的基本顺序，重点只放在：

1. 减少伪 FSM 的产生；
2. 让真正进入 `FSM` 抽取器的节点更接近 standalone state machine；
3. 在不破坏现有 artifact/cache/provenance 的前提下，为 extractor 提供更完整上下文。

---

## 2. 当前问题判断

## 2.1 核心问题不是“LLM 不知道什么是状态机”

当前更关键的问题是三段式误差叠加：

1. `classifier` 把很多“局部行为片段”也判成了 `state_machine`；
2. `StateMachineExtractor` 一旦拿到这个 label，就被 prompt 隐式要求“必须产出一个 FSM”；
3. extract 采用单节点输入，LLM 看不到该节点只是更大流程里的局部 check / clause / numbered step，于是容易为每个节点各造一个 mini-FSM。

因此，后续 `merge` 被迫承担“把 20 多个局部碎片硬拼回几个真正 FSM”的脏活，`lower` 则继续消费这些高度碎片化、语义不完整的状态机结果。

## 2.2 这也是 TCP 行为层空洞的重要来源

结合当前 TCP 产物，可观察到：

- merge 后仍有 23 个 `state_machine`
- `fsm_ir.json` 中 23 个 FSM 全部是 `degraded_ready`
- 188 个 branch 中，只有 1 个 `ready`
- 大量 guard/action 仍停留在 raw prose

这说明问题并不是“没有抽到行为”，而是“把局部行为碎片过度状态机化了”，导致后续 typed lowering 和 codegen 的输入质量很差。

---

## 3. 目标与边界

## 3.1 目标

这轮优化只追求以下结果：

1. 减少误入 `state_machine` lane 的局部节点；
2. 让 `StateMachineExtractor` 只为 standalone FSM 产出结构；
3. 让相邻局部段落在需要时能一起提供给 extractor 作为上下文；
4. 降低 merge 对“碎片再拼装”的依赖。

## 3.2 非目标

本轮不做：

- 全文 one-shot 找所有状态机
- 重写 `FSMIR` / `StateContextIR` 架构
- 引入全局语义网络
- 修改 `lower` 主逻辑
- 修改 `codegen` 的 typed 子集定义

---

## 4. 总体方案

三步推进，按改动量从小到大排列：

| Step | 文件 | 改动性质 | 预期收益 |
|------|------|---------|---------|
| Step 1 | `src/extract/extractors/state_machine.py` | 替换 prompt，允许空 FSM | 直接减少碎片 FSM 数 |
| Step 2 | `src/extract/classifier.py` | 收紧 `state_machine` 判定规则 | 减少误分类节点进入 extractor |
| Step 3 | `src/extract/pipeline.py` 等 | 连续 `state_machine` 节点做轻量 section-window | 给 extractor 更完整上下文 |

推荐实施顺序：

1. 先做 Step 1
2. 再做 Step 2
3. 跑一次 TCP 对比
4. 若前两步收益明显，再做 Step 3

---

## 5. Step 1：StateMachineExtractor 收紧

**目标**：不再默认“只要进了 extractor，就必须产出一个 FSM”。

## 5.1 Standalone FSM 的工作定义

提取器 prompt 中应显式加入如下定义：

> standalone protocol state machine = 一个持久存在的控制组件，具有可复用的状态空间；文本中出现多个稳定命名的状态，并由多个协议事件驱动多条转移；这些状态在文本中被反复引用，而不是一次性局部条件。

也就是说，一个节点只有同时满足下列条件时，才应该被抽为 standalone FSM：

1. 至少有两个稳定命名的状态；
2. 至少有多条、由不同事件或分支触发的转移；
3. 文本把这些状态当作可持续存在的实体来引用，而不是仅在某个局部判断里提到。

## 5.2 明确排除的情况

prompt 中应明确告诉模型：以下内容不是 standalone FSM，应返回空结构：

- 单个 check / step / clause
- ACK / SYN / FIN / RST 的某一局部处理段
- timeout handling 的单条规则
- “If ACK is not acceptable, send reset” 这种单一条件动作句
- “Third Check for SYN” 这类编号流程中的局部片段
- 任何只描述更大状态机中的一个局部分支的文本

## 5.3 负例比正例更重要

建议加入 2 到 3 个 negative few-shot，例如：

- `If the RST bit is set: ... if the SYN bit is set: ...`
- `If the ACK is not acceptable, send a reset`
- `Timeout: when the retransmission timer expires, retransmit the segment`

这些例子都应要求返回空 FSM。

## 5.4 空 FSM 设计

建议 prompt 明确要求在“非 standalone FSM”时返回：

```json
{"name": "", "states": [], "transitions": []}
```

这是安全的，因为当前 `is_empty_state_machine()` 只看 `states` 和 `transitions`，空结构会在 merge 前被过滤。

但为了审计更友好，建议 extractor 在接收到空结构时：

- 允许 `states=[]`、`transitions=[]`
- 若 `name` 为空，则在写入 record 前补成 `title` 或 `node_id`

这样可以保证：

- merge 过滤逻辑不变
- `extract_results.json` 仍然可审计，不会出现大量匿名空壳

---

## 6. Step 2：Classifier 收紧

**目标**：减少“局部行为片段”被错误打成 `state_machine`。

## 6.1 当前问题

现有 classifier 中这条规则会放大误分类风险：

> If the text fits "in state X, on event Y, do Z and move to W", prefer state_machine over procedure_rule.

对于 RFC793 这类大量局部检查、编号步骤、分节处理的文本，这条规则会把许多局部行为片段都推入 `state_machine` lane。

## 6.2 收紧后的分类原则

应把 `state_machine` 的定义改为：

- 必须是一个完整、standalone 的状态机；
- 必须包含多个持久状态；
- 必须包含多个事件或多条转移；
- 文本中这些状态应被当作稳定命名实体反复引用。

应把如下内容归为 `procedure_rule`：

- 单个“in state X, on event Y, do Z”子句；
- 某个具体 event type 的局部处理逻辑；
- 某条 numbered step；
- 某个局部检查或异常处理分支。

也就是说，旧规则应被反向替换为：

> 单个 `in state X, on event Y` 子句默认应更接近 `procedure_rule`，除非文本同时定义了完整的状态空间和多条转移。

---

## 7. Step 3：Section-window 轻量聚合

**目标**：不做全文 one-shot，只给 extractor 增加一点局部连续上下文。

## 7.1 为什么不建议全文 one-shot

全文 one-shot 的主要问题有：

1. 上下文过大，输出不稳定；
2. 去重、漏抽、重抽都更难控制；
3. 很难复用现有 extract artifact / merge / stage cache；
4. 一旦出错，问题定位粒度太粗。

因此，更稳妥的方式是做 section-window，而不是全文 one-shot。

## 7.2 v1 建议只 batch `state_machine`

当前主问题是 FSM 过碎，不是 `procedure_rule` 过碎。

因此 Step 3 的 v1 版本建议：

- 只对连续的 `state_machine` 节点做 segment batching
- 暂不对 `procedure_rule` 做 batching

这样风险最小，也更方便判断收益。

## 7.3 最轻实现：先补局部上下文，不立即改批处理

真正进入节点聚合前，可以先做一个更轻的版本：

- 给 `StateMachineExtractor` 附带 `parent heading`
- 附带 `section path`
- 附带 `sibling titles`

如果这一步已经明显减少碎片 FSM，就不必立刻进入更复杂的 segment batching。

## 7.4 若做 segment batching，设计必须修正

原始的 batching 直觉是对的，但如果直接把 segment 结果“复制写回到每个原始节点的 `ExtractionRecord`”，会破坏当前 cache / hydrate 逻辑。

原因是当前 `_hydrate_components_from_records()` 会对每条 record 都重新恢复出一个对象。若同一个 batched result 被写入多条 record，则在 merge-only 重跑时会被重复 hydrate，尤其会污染：

- `state_machine`
- `procedure_rule`

因此 Step 3 真正实现时必须满足以下约束。

### 约束 A：不要为 batched payload 重复写多条等价 record

建议扩展 `ExtractionRecord`，新增：

- `source_node_ids: list[str]`
- 可选 `segment_id: str`

并让每个 batched segment 只写一条 record。

### 约束 B：保留节点级 provenance，但不要靠重复 payload 实现

节点级 provenance 应通过：

- `source_node_ids`
- `source_pages`
- 可选 diagnostics / notes

来保留，而不是通过“多条完全相同 payload 的 record”保留。

### 约束 C：统计仍保持 node-based，可新增 segment 指标

`success_count` 等历史字段建议继续保持 node 语义，避免破坏实验对比。

若需要增加 batching 可观测性，可新增：

- `segment_count`
- `segment_success_count`
- `batched_node_count`

但不要把既有字段改成 segment 语义。

### 约束 D：batched extract 失败时回退到 per-node extract

为避免一个 segment 失败拖死整段，建议采用：

1. 先尝试 batched extract
2. 若失败，再逐节点 fallback

这样最稳，也方便回归测试。

### 约束 E：拼接文本不要只用 `---`

建议将 segment 文本显式组织为：

```text
[Node 1]
Node ID: ...
Title: ...
Pages: ...
Text:
...

[Node 2]
Node ID: ...
Title: ...
Pages: ...
Text:
...
```

这比简单的分隔线更容易让模型理解“这些是相邻节点，而不是一段已经融合的连续 prose”。

---

## 8. 代码级修改建议

## 8.1 Step 1

文件：

- `src/extract/extractors/state_machine.py`

建议：

1. 替换当前 `_SYSTEM_PROMPT`
2. 加入 standalone FSM 定义
3. 加入 negative few-shot
4. 明确非 standalone 时允许返回空 FSM
5. 在空 FSM 且 `name=""` 时补上 `title` 或 `node_id`，以便审计

## 8.2 Step 2

文件：

- `src/extract/classifier.py`

建议：

1. 删除“单条 in state X / on event Y 优先判 state_machine”的规则
2. 新增“只有完整 standalone FSM 才判为 state_machine”的规则
3. 把局部 check / clause / numbered step 的默认归类收紧到 `procedure_rule`

## 8.3 Step 3

文件：

- `src/extract/pipeline.py`
- `src/extract/merge.py`

必要修改：

1. segment 构建逻辑
2. batched extract 调度逻辑
3. `ExtractionRecord` 结构扩展
4. `_load_cached_extraction_records()` 与 `_hydrate_components_from_records()` 同步更新
5. 新增 batching 统计字段
6. batched 失败时 per-node fallback

结论：Step 3 不是单文件改动，至少会波及 `pipeline.py + merge.py`。

---

## 9. 验证指标

建议在 TCP 上记录下列 before / after 指标：

- classify 后 `state_machine` 标签数
- extract 后 `state_machine_count`
- merge 后 `state_machine_count`
- singleton FSM ratio
- avg transitions per FSM
- lower 后 `raw_branch_ratio`
- `generated_action_count`
- `degraded_action_count`

若优化有效，应看到：

1. `state_machine` 标签数下降
2. merge 前后的 FSM 数量更接近
3. 每个 FSM 的 transition 更“厚”
4. `raw_branch_ratio` 有所下降
5. codegen 的 degraded 比例下降

---

## 10. 推荐实施顺序

建议采用如下顺序：

### Day 1 上午

先做 Step 1：

- 替换 `StateMachineExtractor` prompt
- 加 standalone FSM 定义
- 加 negative few-shot
- 允许空 FSM

先跑 BFD / 小样本 TCP 回归，观察 extract 后 FSM 数量变化。

### Day 1 下午

做 Step 2：

- 收紧 classifier 中 `state_machine` 判定

重新 classify + extract TCP，观察 `state_machine` 标签数和 merge 后 FSM 数量变化。

### Day 2

只有在 Step 1/2 已证明“收紧边界有效”后，再做 Step 3：

- 先补 parent heading / section path / sibling titles
- 再决定是否进入真正的 batched section-window

若进入 batching，实现时必须使用修正版 provenance/cache 设计，而不能直接复制旧 payload 到多条 record。

---

## 11. 最终结论

这轮优化的核心不是“让 LLM 更会推理状态机”，而是：

> 先把 standalone FSM 的边界讲清楚，减少伪 FSM 的产生，再在必要时给 extractor 一点连续上下文。

对应到实现上，最优顺序是：

1. 收紧 `StateMachineExtractor`
2. 收紧 `classifier`
3. 在不破坏 cache / provenance 的前提下，引入轻量 section-window

不建议一开始就做全文 one-shot，也不建议先把压力继续推给 `merge` 或 `lower`。
