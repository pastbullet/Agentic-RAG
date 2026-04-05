# TCP FSM 召回改进方案

## 1. 文档目的

本文档记录 TCP（RFC 793）在 standalone FSM 收紧 + segment reclassification 完成后的剩余召回问题，分析根因，并给出分步改进方案。

当前已有结果：

1. TCP `classified_state_machine_count` 已从 `25 -> 1`；
2. TCP `merge_state_machine_count` 已从 `23 -> 1`；
3. BFD 稳定在 `classified = 1`、`merge = 1`。

唯一保留下来的 TCP FSM 是 `§3.5 Closing a Connection`。

但 TCP 的核心连接状态机（Figure 6 / §3.4 Establishing a Connection）未被召回。这不是 classifier 或 extractor 的问题，而是上游 indexing/chunking 层的结构性缺陷。

---

## 2. 召回缺口分析

### 2.1 缺口 A：§3.2 Terminology 中的 Figure 6

**现象**：RFC 793 的 Figure 6（TCP Connection State Diagram）是一张 ASCII 艺术图，定义了完整的 11 状态连接 FSM。该图嵌在 `§3.2 Terminology` 中。

**根因链条**：

1. `page_index` 构建时，`§3.2 Terminology` 被识别为一个叶子节点（node 0022），跨 pages 25-29；
2. `process_large_node_recursively()` 只按 heading 模式拆分子节点，而 §3.2 内部没有子标题；
3. 因此 Figure 6 与 TCB 变量定义、序列号空间说明等内容混合在同一个未拆分的叶子节点中；
4. classifier 看到的是一个以术语定义为主的混合文本，合理地将其标记为 `general_description`；
5. Figure 6 的 ASCII 图没有任何检测或独立解析机制，被当作普通文本一并跳过。

**关键事实**：

- Node 0022 跨 4 页，内容包括：TCB 数据结构、Send/Receive Sequence Variables、Current Segment Variables、Connection States 列表、Figure 6 ASCII 图
- `build_content_db.py` 的图像提取仅处理 PyMuPDF 可识别的嵌入式图像，不适用于 ASCII 艺术图
- 即使 classifier 把 node 0022 标为 `state_machine`，extractor 面对的仍然是一个以术语为主、FSM 图只是其中一部分的混合文本

### 2.2 缺口 B：§3.4 Establishing a Connection

**现象**：§3.4 描述 TCP 三次握手的建立过程，包含从 CLOSED 到 ESTABLISHED 的多条转移路径。

**根因**：

1. §3.4 的文本风格是 handshake 过程描述（"The principal reason for the three-way handshake..."），而不是 standalone FSM 定义；
2. 它确实引用了 LISTEN、SYN-SENT、SYN-RECEIVED、ESTABLISHED 等状态名，但文本组织方式更像 procedure；
3. classifier 将其标为 `procedure_rule` 是合理的，因为从单节点视角看它确实在描述一个过程；
4. 但从 FSM 完整性角度，§3.4 中的转移信息是 Figure 6 FSM 的重要补充。

**关键判断**：

§3.4 的问题本质上是"跨节点信息分布"——RFC 写作风格将 FSM 定义（§3.2 Figure 6）与详细行为规则（§3.4、§3.5、§3.9）分散在不同章节。这与 BFD 中 §6.2（FSM 定义）和 §6.8.6（AdminDown 规则）的分布模式一致。

---

## 3. 根因总结

| 缺口 | 层级 | 根因 | 当前机制是否可覆盖 |
|------|------|------|-------------------|
| Figure 6 ASCII 图 | indexing/chunking | 无 ASCII 图检测，混合节点未拆分 | 否 |
| §3.2 混合节点 | indexing/chunking | `process_large_node_recursively()` 只按 heading 拆分 | 否 |
| §3.4 分类边界 | classify | 单节点视角下 procedure 判定合理 | 需要上游提供 hint |
| 跨节点信息分布 | 架构性 | pipeline 逐节点处理，无回溯机制 | 否 |

**核心结论**：

> 剩余召回缺口的根因不在 classifier 或 extractor，而在 indexing/chunking 层。继续在 classify 侧堆逻辑不会解决这些问题。

---

## 4. 改进方案

按优先级排列为四个方向，建议按 B → C → A → D 顺序实施。

### 4.1 方向 B（P0）：ASCII 图检测与独立解析

**目标**：在 `page_index` 构建或 `build_content_db` 阶段，检测 ASCII 艺术图并将其作为独立节点或附件处理。

#### 4.1.1 检测策略

ASCII 状态机图的典型特征：

1. 连续多行包含 `---`、`|`、`+`、`>`、`\`、`/` 等框线字符；
2. 行内有居中对齐的短文本（状态名、事件名）；
3. 通常以 `Figure N` 或空行作为起止边界；
4. 非表格：没有规则的列对齐模式。

建议检测方式：

```text
1. 在 content chunk 中扫描连续行
2. 如果连续 N 行（N >= 5）中 box-drawing 字符占比 > 阈值，标记为候选 ASCII 图区域
3. 向前查找最近的 "Figure" 标题行，向后查找空行或新标题，确定边界
4. 将检测到的区域标记为 `ascii_figure` 类型
```

Box-drawing 字符集：`- | + > < \ / * = V ^`

#### 4.1.2 解析策略

检测到 ASCII 图后，有两条路径：

**路径 1（推荐）**：将 ASCII 图作为独立节点

- 在 `page_index` 中为该图创建一个虚拟子节点
- 节点类型标记为 `ascii_figure`
- classifier 遇到 `ascii_figure` 类型时，直接给予 `state_machine` 或 `diagram` 标签
- extractor 用专门的 prompt 解析 ASCII 图中的状态和转移

**路径 2**：LLM 辅助结构化

- 将 ASCII 图文本直接发给 LLM，要求输出结构化 FSM
- 不创建新节点，而是在 extract 阶段作为额外输入
- 缺点是与现有 per-node pipeline 耦合更深

#### 4.1.3 涉及文件

- `page_index.py`：在 `process_large_node_recursively()` 中新增 ASCII 图检测
- `build_content_db.py`：可选，在内容入库时标记图区域
- `src/extract/pipeline.py`：处理新的 `ascii_figure` 节点类型
- `src/extract/extractors/state_machine.py`：新增 ASCII 图专用 prompt 分支

#### 4.1.4 风险

- 误检：表格、代码块也可能包含大量特殊字符
- 需要与现有 heading-based 拆分逻辑协调
- ASCII 图格式在不同 RFC 中差异较大

#### 4.1.5 验证标准

- TCP node 0022 中的 Figure 6 被成功检测并独立解析
- 解析出的 FSM 包含 ≥ 8 个状态（CLOSED, LISTEN, SYN-SENT, SYN-RECEIVED, ESTABLISHED, FIN-WAIT-1, FIN-WAIT-2, CLOSING, TIME-WAIT, CLOSE-WAIT, LAST-ACK）
- BFD 不受影响（BFD 的 FSM 定义是文本而非 ASCII 图）

---

### 4.2 方向 C（P1）：混合节点内容级拆分

**目标**：对包含多种语义内容的大型叶子节点，在 heading 拆分之外增加内容级拆分能力。

#### 4.2.1 问题定义

当前 `process_large_node_recursively()` 只在发现子标题时拆分。对于 §3.2 Terminology 这类节点，内部没有子标题，但内容涵盖：

1. TCB 数据结构定义
2. Send Sequence Variables
3. Receive Sequence Variables
4. Current Segment Variables
5. Connection States 枚举
6. Figure 6 状态机图

这些在语义上是完全不同的内容块，应该被拆分成独立的处理单元。

#### 4.2.2 拆分策略

在 heading-based 拆分失败后（即节点仍然很大但没有子标题），尝试内容级拆分：

```text
1. 检测 ASCII 图边界（复用方向 B 的检测逻辑）
2. 检测明显的语义分隔模式：
   - 连续空行 + 变量/定义列表开始
   - "Figure N:" 标记
   - 缩进级别突变
3. 按检测到的边界拆分为子节点
4. 每个子节点继承父节点的 section_path，title 使用检测到的内容标签或自动生成
```

#### 4.2.3 保守原则

- 只对超过一定长度阈值（如 > 3000 字符）的叶子节点尝试内容级拆分
- 拆分失败时保持原样，不丢失内容
- 生成的子节点 ID 使用父节点 ID + 后缀（如 `0022_fig6`、`0022_vars`）
- 不影响已经成功 heading 拆分的节点

#### 4.2.4 涉及文件

- `page_index.py`：`process_large_node_recursively()` 新增 content-level 拆分分支
- 可能需要新增 `content_splitter.py` 工具模块

#### 4.2.5 验证标准

- TCP node 0022 被拆分为至少 2 个子节点
- Figure 6 所在的子节点可以独立进入 classify → extract 流程
- 其余子节点的 `general_description` 标签不受影响

---

### 4.3 方向 A（P1）：索引阶段分类 Hint 预置

**目标**：在 `page_index` 构建或 summary 生成阶段，为节点预置轻量分类 hint，帮助 classifier 做出更准确的判断。

#### 4.3.1 动机

当前 classifier 只看到单节点的 title + text + summary。对于 §3.4 这类节点，文本风格是 procedure，但内容实际上包含 FSM 转移信息。如果 indexing 阶段能检测到"该节点引用了多个已知状态名"，这个 hint 可以帮助 classifier 做出更准确的判断。

#### 4.3.2 Hint 类型

建议预置以下 hint：

1. **`contains_state_references`**：节点文本中出现 ≥ 3 个 RFC 定义的状态名（如 CLOSED, LISTEN, SYN-SENT 等）
2. **`contains_transition_pattern`**：节点文本中出现 "move to state X" / "enter X state" / "transition to X" 等模式
3. **`contains_ascii_diagram`**：节点包含 ASCII 图（复用方向 B 的检测结果）
4. **`sibling_context`**：同父兄弟节点的标题列表（已有，通过 `OutlineContext.sibling_titles`）

#### 4.3.3 实现方式

**方案 1（轻量）**：在 summary 生成时附带 hint

- 在 `page_index.py` 的 summary 生成 prompt 中，要求额外输出 `content_hints` 字段
- hint 存入 `page_index.json` 的节点元数据
- classifier 读取 hint 作为辅助输入

**方案 2（更轻量）**：在 pipeline 的 classify 阶段用 regex 检测

- 不修改 indexing 层
- 在 `pipeline.py` 的 classify 前，对每个节点做轻量 regex 扫描
- 检测到 hint 后附加到 classifier 的输入文本中

建议先用方案 2 验证效果，若有效再考虑方案 1。

#### 4.3.4 涉及文件

- 方案 2：`src/extract/pipeline.py`，在 classify 前新增 hint 检测
- 方案 1：`page_index.py` + `src/extract/pipeline.py`

#### 4.3.5 验证标准

- §3.4 在有 hint 的情况下被 classifier 标为 `state_machine` 或保留为 `procedure_rule` 但在 extract 阶段得到特殊处理
- 不引入新的假阳性（其它 procedure_rule 节点不应被误升级为 state_machine）

#### 4.3.6 风险

- 状态名检测的 false positive：某些 procedure 节点可能大量引用状态名但不定义转移
- hint 过强可能逆转 segment reclassification 的收紧效果
- 需要 RFC-specific 的状态名列表，泛化性待评估

---

### 4.4 方向 D（P2）：双向召回补充

**目标**：利用 pipeline 已积累的协议知识，在 extract 完成后做一轮反向召回，检查是否有被漏掉的 FSM 相关节点。

#### 4.4.1 设计思路

这是一个"自举式协议知识积累"（Bootstrapped Protocol Knowledge Accumulation）的思路：

```text
第一遍 classify + extract
-> 得到初步 FSM 结构（状态列表、转移列表）
-> 用这些知识构建 "protocol knowledge snapshot"
-> 扫描所有 non-state_machine 节点
-> 检测是否有节点引用了已知状态名 / 转移事件 / 协议关键词
-> 对匹配节点做二次 classify 或直接 extract
-> 将新发现的转移合并到已有 FSM
```

#### 4.4.2 具体步骤

1. **知识构建**：从第一遍 extract 结果中提取所有已知状态名、事件名、动作名
2. **候选筛选**：扫描所有 `procedure_rule` 和 `general_description` 节点，统计已知实体出现次数
3. **阈值过滤**：只有引用 ≥ 3 个已知状态名且包含转移模式的节点才进入候选
4. **二次抽取**：对候选节点用 FSM 补充 prompt 抽取，而非完整 FSM prompt
5. **合并**：将新抽取的转移合并到最匹配的已有 FSM

#### 4.4.3 这对 §3.4 的帮助

- 第一遍从 §3.5 抽到 Closing FSM，已知状态包括 FIN-WAIT-1, FIN-WAIT-2, CLOSING, TIME-WAIT, CLOSE-WAIT, LAST-ACK, CLOSED
- 若 Figure 6 也被成功抽取（依赖方向 B），已知状态会更完整
- §3.4 引用了 CLOSED, LISTEN, SYN-SENT, SYN-RECEIVED, ESTABLISHED，全部是已知状态
- 因此 §3.4 会被双向召回命中，其中的 three-way handshake 转移可以被补充到主 FSM

#### 4.4.4 涉及文件

- `src/extract/pipeline.py`：新增 recall pass 阶段
- `src/extract/merge.py`：扩展合并逻辑以接受补充转移
- 可能需要新增 `src/extract/recall.py`

#### 4.4.5 风险

- 二次抽取可能引入新的假阳性
- 增加 LLM 调用次数
- 与现有 cache/provenance 机制的兼容性需要仔细设计
- 实现复杂度较高

#### 4.4.6 验证标准

- §3.4 中的 establishment 转移（CLOSED → LISTEN, CLOSED → SYN-SENT, SYN-SENT → SYN-RECEIVED, SYN-RECEIVED → ESTABLISHED 等）被成功召回
- 不引入大量假阳性转移
- BFD 不回归

---

## 5. 实施计划

### 5.1 推荐顺序

```text
Phase 1: 方向 B（ASCII 图检测）+ 方向 C（混合节点拆分）
  -> 解决 Figure 6 的召回问题
  -> 这是最高 ROI 的改动，因为 Figure 6 本身就定义了完整的 TCP FSM

Phase 2: 方向 A（分类 hint）
  -> 用 regex 方案验证 §3.4 能否被召回
  -> 若 Phase 1 已经把 Figure 6 抽出来，§3.4 的转移可能通过方向 D 自动补充

Phase 3: 方向 D（双向召回）
  -> 只有在 Phase 1-2 仍不够时才进入
  -> 或者作为通用的 recall-improvement 机制留到后续迭代
```

### 5.2 Phase 1 细化

#### Step 1：ASCII 图检测器

文件：`page_index.py` 或新建 `src/extract/ascii_figure_detector.py`

1. 实现 `detect_ascii_figures(text: str) -> list[AsciiDiagramRegion]`
2. 返回每个检测到的 ASCII 图的起止行号、可能的 Figure 标题
3. 单元测试覆盖 TCP Figure 6 和至少一个非图文本

#### Step 2：混合节点拆分

文件：`page_index.py`

1. 在 `process_large_node_recursively()` 的 heading 拆分失败分支后，尝试 content-level 拆分
2. 利用 Step 1 的 ASCII 图检测结果作为拆分边界之一
3. 生成子节点，继承父节点的 section_path

#### Step 3：ASCII 图节点的 classify + extract

文件：`src/extract/classifier.py`、`src/extract/extractors/state_machine.py`

1. classifier 对 `ascii_figure` 类型节点给予特殊处理
2. extractor 对 ASCII 图使用专门的 prompt（不套用 standalone FSM 的 negative examples）

#### Step 4：验证

1. 跑 TCP `classify,extract,merge`
2. 检查 Figure 6 是否被召回
3. 检查 `classified_state_machine_count` 和 `merge_state_machine_count`
4. 回跑 BFD 确认不回归

### 5.3 预期指标

Phase 1 完成后：

- TCP `classified_state_machine_count` 预期 `2`（§3.5 Closing + Figure 6）
- TCP `merge_state_machine_count` 预期 `1-2`（可能合并为一个完整 FSM）
- TCP 合并后的 FSM 应包含 ≥ 8 个状态
- BFD 保持 `classified = 1`、`merge = 1`

Phase 1 + Phase 2 完成后：

- TCP `classified_state_machine_count` 预期 `2-3`（+ §3.4）
- 合并后的 FSM 应覆盖 establishment + closing + 主状态图

---

## 6. 与现有方案的关系

| 已完成方案 | 关系 |
|-----------|------|
| Standalone FSM 收紧（Step 1-2） | 互补，本方案不修改 classifier/extractor 的现有 standalone 判定逻辑 |
| Segment reclassification | 互补，ASCII 图拆分出来后可能进入 segment 分组 |
| Phase C typed lowering | 无关，本方案只影响 classify/extract 上游 |
| Outline context | 复用，ASCII 图节点也会获得 outline context |

---

## 7. 本轮非目标

- 修改 `ExtractionRecord` 结构
- 修改 merge/lower/codegen 逻辑
- 全文 one-shot FSM 抽取
- 并发化或性能优化
- 其它 RFC 的 ASCII 图支持（但设计上应保持泛化）

---

## 8. 结论

TCP FSM 召回的核心瓶颈已经从 classifier 假阳性转移到了 indexing/chunking 层的结构性缺陷：

1. ASCII 艺术图没有检测和独立解析机制；
2. 混合内容节点没有 heading 以外的拆分能力；
3. 跨节点分布的 FSM 信息没有回溯召回机制。

最高 ROI 的改进是 Phase 1（ASCII 图检测 + 混合节点拆分），因为 Figure 6 本身就是 TCP 连接 FSM 的权威定义，成功召回它将直接解决 TCP 的核心覆盖率问题。
