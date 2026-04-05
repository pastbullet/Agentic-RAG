---
title: Standalone FSM 收紧周期问题记录（Step 0 到 Step 2c）
date: 2026-04-06
tags:
  - validation
  - experiments
  - issue-log
  - fsm
  - tcp
  - bfd
  - kiro
status: completed
---

# Standalone FSM 收紧周期问题记录（Step 0 到 Step 2c）

## 1. 记录目的

本文档用于单独记录本轮 standalone FSM 收紧实验周期中暴露出的主要问题、定位过程、处置方式与当前状态。

与实验结果文档不同，本文档的重点不是展示“指标提升了多少”，而是回答以下问题：

- 本周期到底遇到了哪些结构性问题；
- 这些问题分别发生在哪个阶段；
- 哪些问题已经在 Step 1 / 2 / 2.5 / 2c 中解决；
- 哪些问题在本轮结束后仍然存在。

相关结果文档可参见：

- [2026-04-05_standalone_fsm_step0_to_2c_full_record.md](/Users/zwy/毕设/Kiro/docs/validation_and_experiments/experiment_records/2026-04-05_standalone_fsm_step0_to_2c_full_record.md)
- [2026-04-05_step2c_real_api_validation_checkpoint.md](/Users/zwy/毕设/Kiro/docs/validation_and_experiments/experiment_records/2026-04-05_step2c_real_api_validation_checkpoint.md)
- [2026-04-05_standalone_fsm_step0_2_5_validation.md](/Users/zwy/毕设/Kiro/docs/validation_and_experiments/experiment_records/2026-04-05_standalone_fsm_step0_2_5_validation.md)

## 2. 问题总览

| 编号 | 问题 | 主要暴露协议 | 影响阶段 | 当前状态 |
|------|------|--------------|----------|----------|
| P1 | TCP 上游将大量局部过程误判为 `state_machine` | TCP | classify -> extract -> merge -> codegen | 已解决 |
| P2 | BFD 在 extractor 侧出现 `ProtocolStateMachine` 校验错误回归 | BFD | extract -> merge | 已解决 |
| P3 | extractor 对“非 standalone FSM”缺少稳定空返回路径 | TCP / BFD | extract | 已解决 |
| P4 | classifier 存在对 `state_machine` 的系统性偏置 | TCP | classify | 已解决 |
| P5 | 单节点上下文不足，残余伪 FSM 仍然存在 | TCP | classify / extract | 已解决 |
| P6 | FSM segment 初版设计只向后扩展，丢失前方兄弟上下文 | TCP | classify | 已解决 |
| P7 | classify artifact 缓存导致真实验证结果不可信 | TCP | validate / accept | 已解决 |
| P8 | Step 2c 之后 TCP 进入“高精度、低召回”，关键建连 FSM 未保留 | TCP | classify / extract | 未解决 |
| P9 | `3.2 / Figure 6` 与 `3.4` 的状态机信息未被提升为完整 FSM | TCP | page index / classify / extract | 未解决 |
| P10 | TCP 字段侧存在超出 RFC793 原文的模板污染 | TCP | extract / merge | 未解决 |

## 3. 按阶段整理的问题记录

### 3.1 P1：TCP 上游把大量局部过程误判为 `state_machine`

**症状**

- TCP baseline 中：
  - `classified_state_machine_count = 25`
  - `extract_state_machine_count = 25`
  - `merge_state_machine_count = 23`
  - `raw_branch_ratio = 0.92`
  - `merge` 耗时达到 `3296s`

**典型误判对象**

- `SEND Call`
- `RECEIVE Call`
- `STATUS Call`
- numbered check 类标题
- 说明性章节中的局部状态片段

**根因**

- 原 classifier 存在“只要像状态转移就偏向 `state_machine`”的倾向；
- 单节点分类对 RFC 过程型文本过于敏感；
- extractor 会继续把这些局部 dispatch / call handler 强行组织成 FSM payload；
- downstream 不会自动纠正这种误判，反而会继续放大。

**影响**

- merge 后形成大量 fragmented FSM；
- lower 后绝大多数 branch 只能保留 raw guard / raw action；
- codegen 得到的大量逻辑不可结构化消费。

**处置**

- Step 1：收紧 `StateMachineExtractor`；
- Step 2：收紧 classifier prompt 并增加 sanity filter；
- Step 2.5：补充 outline context；
- Step 2c：引入同父全部兄弟的 segment 二次分类。

**结果**

- TCP final：
  - `classified_state_machine_count = 1`
  - `extract_state_machine_count = 1`
  - `merge_state_machine_count = 1`
  - `raw_branch_ratio = 0.3`

**状态**

- 已解决。

### 3.2 P2：BFD 出现 extractor 校验错误回归

**症状**

在修复早期的 BFD 实跑中，出现了大量 `ProtocolStateMachine` 校验错误，典型报错为：

- `states.*.name Field required`
- `transitions.*.from_state Field required`
- `transitions.*.to_state Field required`
- `transitions.*.event Field required`

同时伴随：

- `extract_state_machine_count = 14`
- 这些 FSM 基本全部校验失败；
- `merge_state_machine_count = 0`

**根因**

- LLM 返回的 payload 经常使用 alias-shaped 字段：
  - `id / label` 代替 `name`
  - `from / to` 代替 `from_state / to_state`
  - `trigger` 代替 `event`
- extractor 之前直接按严格 schema 校验，缺少兼容层；
- 因而真实可理解的 FSM 输出在进入 schema 前就被判为无效。

**影响**

- BFD 作为已稳定协议出现真实回归；
- merge 侧完全丢失状态机；
- 说明 Step 1 如果只改 prompt 而不改 payload normalization，会把真实协议也打坏。

**处置**

- 在 [state_machine.py](/Users/zwy/毕设/Kiro/src/extract/extractors/state_machine.py) 中增加：
  - `_normalize_state_item`
  - `_normalize_transition_item`
- 将 alias-shaped 输出统一映射到正式 schema 字段后再验证。

**结果**

- BFD 最终恢复为：
  - `classified_state_machine_count = 1`
  - `extract_state_machine_count = 1`
  - `merge_state_machine_count = 1`
  - `verify = True`

**状态**

- 已解决。

### 3.3 P3：extractor 缺少“非 standalone FSM”的稳定空返回路径

**症状**

- 在 Step 1 之前，extractor 会尝试对很多不应抽成 FSM 的节点也返回结构化 payload；
- 即使提示词要求收敛，模型仍可能返回“看起来像 FSM 但本质不是 FSM”的碎片结果；
- 在 Step 0-2.5 中，TCP 仍出现 `empty_fsm_return_count = 3`，说明有一部分伪 FSM 需要 extractor 主动拦截。

**根因**

- 原 prompt 没有把“不应抽取”为可接受的显式输出路径；
- 没有负例和空返回规范时，模型倾向于“尽量产出一个 FSM”；
- extractor 侧也缺少“最小 standalone 阈值”兜底。

**影响**

- 伪 FSM 容易继续流入 merge；
- 即使 classifier 收紧，extractor 也可能成为新的放大器。

**处置**

- 在 prompt 中明确 standalone FSM 定义；
- 增加 negative few-shot；
- 允许返回空 FSM；
- 在 extractor 内增加 `_coerce_non_standalone_payload_to_empty`。

**结果**

- BFD 和 TCP 后续都不再出现新的 malformed FSM payload 回归；
- Step 2c final 中 TCP 与 BFD 的 `empty_fsm_return_count` 都为 `0`。

**状态**

- 已解决。

### 3.4 P4：classifier 对 `state_machine` 存在系统性偏置

**症状**

- 很多明显不是 standalone FSM 的节点仍被打成 `state_machine`；
- 典型包括：
  - call procedure
  - numbered check
  - meta / descriptive section

**根因**

- 原 prompt 中存在“像状态转移就倾向 `state_machine`”的宽松规则；
- summary 容易把相邻章节中的状态词、事件词泄漏给当前节点；
- 没有在分类后做基本的规则约束。

**影响**

- classify 阶段已经把错误引入主链；
- extractor 和 merge 只能在错误输入上继续工作。

**处置**

- 删掉旧的 prefer-state-machine 规则；
- 增加 standalone FSM 的正确定义；
- 新增 sanity filter；
- 用可审计标签记录降级原因：
  - `sanity_downgrade:meta_section`
  - `sanity_downgrade:numbered_check`
  - `sanity_downgrade:call_procedure`

**结果**

- TCP fresh classify 中：
  - `state_machine_sanity_downgrade_count = 8`
  - `by_reason = {'meta_section': 1, 'call_procedure': 1, 'numbered_check': 6}`
- 说明这批误判并不是随机噪声，而是可识别、可系统拦截的模式。

**状态**

- 已解决。

### 3.5 P5：单节点上下文不足，无法清掉残余伪 FSM

**症状**

- 即使完成 Step 1 + Step 2 + Step 2.5，TCP 仍有残余 false positive；
- 典型场景是 `§3.9 Event Processing` 下的一组 call handler：
  - 单看 `SEND Call` 或 `RECEIVE Call`，仍可能像局部状态机；
  - 但放回兄弟节点集合中，它们其实是并列 procedure。

**根因**

- Step 2.5 只给了轻量 outline context：
  - `section_path`
  - `parent_heading`
  - `sibling_titles`
- 这对“判断是不是 standalone FSM”有帮助，但对“区分一组并列 call handler 与真正 FSM”仍不够。

**影响**

- classify 侧剩余伪 FSM 难以继续下降；
- extractor 仍会对这类节点尝试抽 FSM。

**处置**

- 设计 Step 2c；
- 对仍为 `state_machine` 的节点，按 `parent_node_id` 聚合同父全部兄弟叶子节点做二次分类。

**结果**

- TCP fresh run 中：
  - `fsm_segment_count = 3`
  - `fsm_segment_reclassified_count = 2`
  - `fsm_segment_updated_node_count = 5`
- 最终 TCP 只剩 1 个 FSM。

**状态**

- 已解决。

### 3.6 P6：FSM segment 初版算法只向后扩展，丢失前方兄弟上下文

**症状**

在方案评审阶段发现，若 segment 仅从锚点向后扩展，那么当锚点位于兄弟列表中间时，会漏掉其前方的重要上下文。例如：

- `OPEN Call`
- `SEND Call` <- 锚点
- `RECEIVE Call`
- `CLOSE Call`

若只向后扩展，`OPEN Call` 不会进入 segment。

**根因**

- 初版算法假设“锚点后的兄弟足够解释语义”；
- 但实际 RFC 章节中，锚点前后的兄弟共同组成并列结构。

**影响**

- 二次分类看到的是半截上下文；
- 不能稳定判断这是一组 call handler。

**处置**

- 将 segment 构建改为“同父全部兄弟”而不是“向后贪心扩展”；
- 在 `FsmSegment` 中明确区分：
  - `node_ids`：全部兄弟上下文
  - `target_node_ids`：允许改写的当前 FSM 节点

**结果**

- Step 2c 实现采用的是修正版；
- 避免了因为上下文缺失造成的二次分类误差。

**状态**

- 已解决。

### 3.7 P7：缓存 artifact 使真实 API 验证结果不可信

**症状**

在 Step 2c 真实验证前，发现已有 TCP artifact 中出现：

- `0059 / GLOSSARY -> state_machine`

但其 rationale 明显是 general-description 语义，说明 classify artifact 与当前代码逻辑不一致。

**根因**

- classify 产物受缓存影响；
- 若只看终端输出，容易误把旧 artifact 当成新结果；
- 真实接受标准如果不以 artifact 为准，会造成误判。

**影响**

- 无法可靠判断 Step 2c 是否真的生效；
- 容易把缓存问题误当成模型问题或代码问题。

**处置**

- bump [classifier.py](/Users/zwy/毕设/Kiro/src/extract/classifier.py) 的 `PROMPT_VERSION`；
- TCP 先做 fresh rerun；
- 验收改为“以 artifact 为主，不以 stdout 为主”。

**结果**

- fresh TCP rerun 后：
  - `prompt_version = v1.4-standalone-fsm-segment-reclassification`
  - `classified_state_machine_count = 1`
- 后续结果与代码逻辑一致。

**状态**

- 已解决。

### 3.8 P8：Step 2c 后 TCP 进入“高精度、低召回”

**症状**

Step 2c final 后，TCP 只保留了一个 FSM：

- `0025 / 3.5 Closing a Connection`

这说明伪 FSM 问题已基本解决，但真正重要的“建连状态机”没有被保留下来。

**根因**

- 本轮所有策略都以“抑制 fragmented FSM”为主；
- classifier 和 extractor 的门槛明显变严；
- 因而系统在 TCP 上更倾向于保住最明显、最完整的 standalone FSM，而放弃边界更模糊的真实状态机来源。

**影响**

- precision 显著提升；
- recall 明显不足；
- 从协议理解角度看，TCP 最关键的建立连接状态机并未完整进入 FSM 主线。

**处置**

- 本轮未直接解决；
- 只是在审计中明确确认这是当前剩余主问题。

**状态**

- 未解决。

### 3.9 P9：`3.2 / Figure 6` 与 `3.4` 的状态机信息未被提升为完整 FSM

**症状**

最终 TCP 审计发现：

- `3.2 / Figure 6` 中确实存在 TCP Connection State Diagram；
- `3.4 Establishing a connection` 中也有三次握手、同时打开、旧 SYN 恢复、半开连接等状态性内容；
- 但两者都没有进入最终 FSM 结果。

**`3.2` 的根因**

- `Figure 6` 的文本确实进入了内容仓；
- 但它被 page index 放进了 `0022 / 3.2 Terminology` 这个混合节点；
- 该节点同时包含：
  - TCB 变量
  - sequence space 说明
  - current segment variables
  - state list
  - state diagram overview
- classifier 将其按 `meta_section` 降级为 `general_description`，因此根本没有进入 FSM extractor。

**`3.4` 的根因**

- `0024 / 3.4 Establishing a connection` 被判成 `procedure_rule`；
- extract 只留下了粗粒度的 procedure summary；
- 没有提升为状态集合与转移集合。

**影响**

- 当前 TCP FSM 结果只保留“关闭连接子图”；
- 真正的完整连接状态机没有形成。

**处置**

- 本轮仅完成审计与定性定位；
- 尚未实现新的召回路径。

**下一步方向**

- 对 mixed section 中的图级子片段做 diagram-aware / figure-aware 再抽取；
- 对 `procedure_rule` 中的建连过程做 stateful lifting，而不是只保留 summary。

**状态**

- 未解决。

### 3.10 P10：TCP 字段侧存在超出 RFC793 原文的模板污染

**症状**

TCP 字段产物整体可用，但在 options 部分出现了超出 RFC793 原文的内容：

- RFC793 原文在当前位置只定义 `EOL / NOP / MSS`；
- 当前 `message_ir` 中出现了 `window_scale`。

**根因**

- 选项尾部建模复用了 TCP option 模板；
- 模板词表并未严格限制在 RFC793 文本证据范围内。

**影响**

- 字段覆盖率看起来较高，但语义纯度不足；
- 若作为论文中的“严格从 RFC793 提取”的证据，需要单独说明这一偏差。

**处置**

- 本轮未处理；
- 问题是在 TCP 审计阶段发现的旁路问题，不属于本轮 standalone FSM 收紧主线。

**状态**

- 未解决。

## 4. 本周期最关键的经验

### 4.1 真正的主问题不是“FSM 太少”，而是“伪 FSM 太多”

本轮最初暴露的问题并不是模型完全不会抽状态机，而是：

- 它会把大量局部行为片段过度状态机化；
- 然后把这些错误一路传递给 downstream。

因此，本轮收益最大的动作不是增加更多 FSM，而是先把错误的 FSM 删掉。

### 4.2 上游误判会在整条 pipeline 中被持续放大

本轮验证表明，`classify` 的误差不是局部误差，而是系统性误差。  
一旦节点在 classify 阶段被错误标成 `state_machine`，后续 `extract -> merge -> lower -> codegen` 并不会自然纠正，反而会不断把局部片段包装成更复杂的中间表示。

### 4.3 泛化优先比协议特定补丁更稳

本轮真正有效的改动主要来自：

- standalone FSM 定义收紧；
- extractor payload normalization；
- generic sanity filter；
- same-parent sibling context；

而不是继续增加协议特定关键词。这说明在当前阶段，优先构造跨协议通用规则，比堆协议私有规则更稳。

### 4.4 “高精度”并不等于“任务完成”

Step 2c 之后 TCP 指标已经明显收敛，但审计表明：

- 精度问题已大体解决；
- 召回问题开始成为主要矛盾。

因此，本周期的终点不是“TCP 只剩 1 个 FSM 就完全成功”，而是：

> 系统已经从“噪声主导”切换到“召回不足主导”。

## 5. 当前结论

截至 Step 2c 完成并通过真实 API 验证，本周期的问题可分为两类：

**已解决的问题**

- TCP fragmented FSM 泛滥
- BFD extractor 校验错误回归
- non-standalone FSM 缺少空返回路径
- classifier 对 `state_machine` 的系统性偏置
- 单节点上下文不足
- FSM segment 初版算法缺陷
- classify artifact 缓存污染验证结果

**仍未解决的问题**

- TCP 真正的建连状态机召回不足
- `3.2 / Figure 6` 所在 mixed section 缺少图级召回路径
- `3.4 Establishing a connection` 只被保留为 procedure summary
- TCP 字段侧仍存在少量模板污染

如果后续继续推进，本周期结束后最值得优先进入的新问题，不再是“如何继续压缩伪 FSM”，而是：

> 如何在不重新引入 fragmented FSM 噪声的前提下，把 `3.2 / Figure 6` 和 `3.4` 中真正有价值的 TCP 建连状态机重新召回出来。
