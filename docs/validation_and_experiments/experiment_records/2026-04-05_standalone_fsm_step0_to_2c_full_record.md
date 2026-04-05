---
title: Standalone FSM 收紧实验全记录（Step 0 到 Step 2c）
date: 2026-04-05
tags:
  - validation
  - experiments
  - fsm
  - tcp
  - bfd
  - kiro
status: completed
---

# Standalone FSM 收紧实验全记录（Step 0 到 Step 2c）

## 1. 实验目的

本轮实验的目标是减少 TCP 文档上的伪 FSM（fragmented state machines），并验证这一上游收紧策略是否能在不改动 lower / merge / codegen 下游主逻辑的前提下，显著改善整条 pipeline 的结构质量。

Kiro 当前的主线流程为：

`classify -> extract -> merge -> lower -> materialize -> align -> codegen -> verify`

本轮优化只作用于 `classify` 与 `extract` 上游，分为四个连续步骤：

- Step 0：记录 TCP baseline
- Step 1：收紧 `StateMachineExtractor`
- Step 2：收紧 `classifier`
- Step 2.5：为 `state_machine` 节点补充 outline context
- Step 2c：基于 `page_index` 树结构的 FSM segment 二次分类

实验核心问题是：

> 上游对 `state_machine` 的误判是否会在后续 `extract -> merge -> lower -> codegen` 中被不断放大；以及仅通过收紧 classify / extract 上游，是否就足以让 TCP 的伪 FSM 数量与后续 raw branch 噪声显著下降。

## 2. 代码改动摘要

### 2.1 Step 1：收紧 StateMachineExtractor

核心文件：

- [state_machine.py](/Users/zwy/毕设/Kiro/src/extract/extractors/state_machine.py)

核心逻辑：

- 将 extractor prompt 从“抽取一个 FSM”改为“只抽取 standalone FSM”
- 在 prompt 中明确 standalone FSM 必须满足：
  - 至少两个稳定命名状态
  - 多个事件或转移
  - 状态在文本中被反复引用
- 增加 negative few-shot，要求非 standalone 时返回空 FSM：
  - `{"name":"", "states":[], "transitions":[]}`
- 明确 ACK/SYN/FIN/RST 局部处理、单条 timeout rule、编号步骤中的局部片段都不是 standalone FSM
- 增加 payload normalization，使 extractor 能兼容 LLM 常见 alias-shaped 输出：
  - `state.name | label | id | title`
  - `transition.from_state | from | source`
  - `transition.to_state | to | target`
  - `event | trigger | on | when | input`
- 当 LLM 返回空 FSM 且 `name == ""` 时，在 `model_validate` 之前回填为 `title or node_id`，仅用于审计和 artifact 一致性
- 对明显非 standalone 的 payload 做 extractor 侧二次兜底，直接收敛为空 FSM

### 2.2 Step 2：收紧 classifier

核心文件：

- [classifier.py](/Users/zwy/毕设/Kiro/src/extract/classifier.py)

核心逻辑：

- 删除旧的 “in state X, on event Y, do Z -> prefer state_machine” 规则
- 将 `state_machine` 判定收紧为：
  - 完整
  - standalone
  - 多状态
  - 多转移
- 明确 summary 不是 `state_machine` 的主证据，避免由相邻章节摘要诱导误判
- 新增 sanity filter，对模型已判为 `state_machine` 的节点做规则降级：
  - meta / descriptive section -> `general_description`
  - local procedure / numbered check -> `procedure_rule`
- 为降级结果追加可审计标记：
  - `sanity_downgrade:meta_section`
  - `sanity_downgrade:numbered_check`
  - `sanity_downgrade:call_procedure`

### 2.3 Step 2.5：补充 outline context

核心文件：

- [pipeline.py](/Users/zwy/毕设/Kiro/src/extract/pipeline.py)
- [run_extract_pipeline.py](/Users/zwy/毕设/Kiro/run_extract_pipeline.py)

核心逻辑：

- 仅对 `state_machine` 节点，在 extract 前拼接轻量章节上下文：
  - `section_path`
  - `parent_heading`
  - `sibling_titles`
- 明确这些上下文只用于判断该节点是否是 standalone FSM，不允许凭 sibling title 虚构状态或转移
- 新增 extract 阶段观测指标：
  - `empty_fsm_return_count`
  - `state_machine_context_augmented_count`

### 2.4 Step 2c：FSM segment 二次分类

核心文件：

- [pipeline.py](/Users/zwy/毕设/Kiro/src/extract/pipeline.py)
- [classifier.py](/Users/zwy/毕设/Kiro/src/extract/classifier.py)
- [run_extract_pipeline.py](/Users/zwy/毕设/Kiro/run_extract_pipeline.py)
- [fsm_segment_reclassification_plan.md](/Users/zwy/毕设/Kiro/docs/fsm_ir/fsm_segment_reclassification_plan.md)

核心逻辑：

- 在 pipeline 内部扩展 `OutlineContext`，新增 `parent_node_id`
- 新增内部结构 `FsmSegment(anchor_node_id, parent_node_id, parent_heading, node_ids, target_node_ids)`
- segment 构建不再“从锚点向后贪心扩展”，而是：
  - 找到仍被标成 `state_machine` 的锚点
  - 取该锚点所在父节点下的全部兄弟叶子节点作为上下文
  - 仅将其中当前仍为 `state_machine` 的节点作为二次分类目标
- segment reclassification 的 prompt 只允许输出目标节点的新 label：
  - `state_machine`
  - `procedure_rule`
  - `general_description`
- segment reclassification 完成后，将 refined label 回写到现有 `node_labels.json`
- classify 阶段新增观测指标：
  - `fsm_segment_count`
  - `fsm_segment_reclassified_count`
  - `fsm_segment_updated_node_count`
  - `fsm_segment_skipped_count`
  - `fsm_segment_skip_reasons`

## 3. 数据对比

### 3.1 TCP（rfc793-TCP.pdf）

| 指标 | Baseline (04-04) | Step 1+2+2.5 后 | Step 2c 后 (final) |
|------|------------------|-----------------|--------------------|
| classified_state_machine_count | 25 | 6 | 1 |
| extract_state_machine_count | 25 | 12 | 1 |
| merge_state_machine_count | 23 | 5 | 1 |
| empty_fsm_return_count | — | 3 | 0 |
| raw_branch_ratio | 0.92 | — | 0.3 |
| generated_action_count | ~2 | — | 2 |
| degraded_action_count | 大量 | — | 5 |
| merge 耗时 | 3296s | — | 预期 <10s |
| verify | True | — | True |

Step 2c 的 segment 统计：

- `fsm_segment_count = 3`
- `fsm_segment_reclassified_count = 2`
- `fsm_segment_updated_node_count = 5`

Step 2c 后最终唯一保留的 FSM 为：

- `0025 / §3.5 Closing a Connection`

该节点对应真正的 standalone FSM，而不是局部 call handler 或 numbered check。

### 3.2 BFD（rfc5880-BFD.pdf）

| 指标 | 修复前（回归状态） | Step 1+2 后 | Step 2c 后 |
|------|--------------------|-------------|------------|
| classified_state_machine_count | 14+ | 1 | 1 |
| extract_state_machine_count | 14（全部校验错误） | 1 | 1 |
| merge_state_machine_count | 0（回归） | 1 | 1 |
| verify | broken | True | True |

结论上，BFD 在 Step 1+2 已完成回归修复，而 Step 2c 没有引入新的回归。

## 4. 关键发现

### 4.1 上游 classify 精度对整条 pipeline 有决定性影响

本轮实验最重要的发现是：`classify` 阶段对 `state_machine` 的过宽判定，会在后续阶段被持续放大，而不会自然被下游“自动修正”。

原因在于：

- 被误判为 `state_machine` 的节点会被发送到 `StateMachineExtractor`
- extractor 会尝试为这些局部 procedure / numbered check / call handler 构造 FSM payload
- 这些 payload 进入 merge 后，会进一步形成 fragmented FSM 集合
- fragmented FSM 会继续进入 lower / materialize / align / codegen
- 最终表现为：
  - `merge_state_machine_count` 异常偏高
  - `raw_branch_ratio` 偏高
  - generated code 中大量分支只能保留 raw guard / raw action

TCP baseline 中：

- `classified_state_machine_count = 25`
- `merge_state_machine_count = 23`
- `raw_branch_ratio = 0.92`

这说明在 baseline 状态下，几乎整个 FSM 主线都被伪 FSM 噪声占据。相反，当上游 `state_machine` 数量被压缩到真正的 standalone FSM 后，后续 merge / lower / codegen 的结构质量会同步改善。

### 4.2 Step 2c 的原理是“同父全部兄弟”上下文重判，而不是继续堆标题 heuristic

Step 2c 的出发点是：单节点分类已经足以消除大量明显伪 FSM，但剩余模糊 case 往往需要一点局部章节结构才能看清。

典型例子是 TCP `§3.9 Event Processing`：

- `OPEN Call`
- `SEND Call`
- `RECEIVE Call`
- `CLOSE Call`
- `ABORT Call`
- `STATUS Call`

单看 `SEND Call` 或 `RECEIVE Call`，很容易被误判为 `state_machine`。但如果把它们放回同一个父章节，与全部兄弟节点一起看，LLM 更容易识别出：

- 这是一组并列的 call handler
- 它们是 procedure / dispatch 片段
- 它们不是独立的 standalone FSM

因此，Step 2c 的核心不是增加更多协议特定关键词，而是利用 `page_index` 树结构做一次保守的 classifier 二次分类。实际结果表明：

- `fsm_segment_count = 3`
- `fsm_segment_reclassified_count = 2`
- `fsm_segment_updated_node_count = 5`

这说明仅通过三组同父 segment 的局部上下文，就把 TCP 剩余的多处伪 FSM 再次收敛，最终只保留 1 个真正的 standalone FSM。

### 4.3 `raw_branch_ratio` 从 0.92 降到 0.3，表示下游处理对象已经从“噪声主导”转向“结构主导”

`raw_branch_ratio` 表示 lower 后仍保留 raw guard / raw action 的分支占比。

在 TCP baseline 中：

- `raw_branch_ratio = 0.92`

这意味着绝大多数分支无法被 lower 成受限 typed IR，反映的是：

- 上游抽入了大量局部 procedure 片段
- 这些片段的 guards / actions 缺乏稳定的状态机结构
- downstream 只能把它们原样保留为 raw branch

在 Step 2c final 中：

- `raw_branch_ratio = 0.3`

这表明当前保留下来的 FSM 集合已经明显更加“干净”，lower 面对的是更少但更完整的 standalone FSM，因而 typed IR 的可结构化比例大幅提高。换言之，从 `0.92 -> 0.3` 的下降，不只是 FSM 数量减少，更代表：

> downstream 正在从“处理大量伪 FSM 噪声”转向“处理少量真实 FSM 结构”。

### 4.4 Phase C refine 未触发，是因为 `raw_branch_ratio_after = 0.3` 不满足触发阈值

在 Step 2c 后的 TCP final full-chain run 中：

- `llm_refine_triggered_count = 0`
- `raw_branch_ratio_before = 0.3`
- `raw_branch_ratio_after = 0.3`

其原因不是 refine 逻辑失效，而是当前 `raw_branch_ratio` 已经降到阈值边界，不再满足 `> 0.3` 的触发条件，因此 Phase C refine 不会启动。

这说明两点：

- Step 2c 已经通过上游收紧，把 lower 前的噪声压到了 refine 触发门槛以下
- 当前阶段的主要收益来自上游分类与抽取质量改善，而不是依赖后置 refine 去“补救”

从实验设计角度看，这一结果也支持了本轮策略选择：优先修上游 classify / extract，而不是先在下游增加更重的后处理。

## 5. 真实 API 验收摘要

本轮在真实 API 下完成了两次关键验收：

### 5.1 TCP fresh run

执行顺序：

- `classify,extract`
- `classify,extract,merge,codegen,verify`

确认结果：

- `prompt_version = v1.4-standalone-fsm-segment-reclassification`
- `classified_state_machine_count = 1`
- `extract_state_machine_count = 1`
- `merge_state_machine_count = 1`
- `empty_fsm_return_count = 0`
- `generated_action_count = 2`
- `degraded_action_count = 5`
- `verify = True`

最终唯一保留的 FSM 为：

- `0025 / 3.5 Closing a Connection`

### 5.2 BFD fresh run

执行顺序：

- `classify,extract,merge,codegen,verify`

确认结果：

- `classified_state_machine_count = 1`
- `extract_state_machine_count = 1`
- `merge_state_machine_count = 1`
- `verify = True`
- 未出现新的 extractor validation error

## 6. 结论

本轮实验表明，standalone FSM 收紧策略在 `Step 0 -> Step 2c` 的完整推进后，已经能够稳定完成以下目标：

1. 在不修改 lower / merge / codegen 主逻辑的前提下，大幅压缩 TCP 上的伪 FSM
2. 将 TCP 的 `classified_state_machine_count` 从 `25` 降到 `1`
3. 将 TCP 的 `merge_state_machine_count` 从 `23` 降到 `1`
4. 将 TCP 的 `raw_branch_ratio` 从 `0.92` 降到 `0.3`
5. 保持 BFD 从回归状态恢复后的稳定性，不引入新回归

因此，本轮结果支持如下判断：

> 对于当前 Kiro pipeline，优先收紧上游 `classify + extract` 的 `state_machine` 判定，能够以较小改动带来整条 FSM 主线的结构性改善；而且在 TCP 上，这种改善已经足以把 downstream 的 raw branch 噪声压到 refine 触发阈值以下。

从后续工作安排看，Step 2c 之后应优先评估：

- 是否仍有必要进入 Step 3 batching
- 以及在当前 `merge_state_machine_count = 1`、`raw_branch_ratio = 0.3` 的条件下，Step 3 的边际收益是否仍然足够高

如果仅从本轮实验结果出发，答案更接近：

> 上游 standalone FSM 收紧已经解决了主要问题，Step 3 batching 的优先级可以显著后移。
