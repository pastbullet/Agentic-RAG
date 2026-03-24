# 设计文档：merge-semantic-alignment

## 方法论

本方案的核心逻辑是三层分工：

**第一层 — 表示层规范化**：解决"为什么同一对象看起来不像同一对象"。状态机名称支持包含关系匹配；状态转移做归一化消除措辞差异；报文字段名做规范化处理括号、缩写、全称、连接符。改进的是"相似度是怎么计算出来的"。

**第二层 — 高置信规则合并**：改进的是"合并是怎么判定的"。只有结构证据非常强的 pair 才自动合并，明显不同的直接跳过，中间那一小部分"差一点"的模糊候选留给第三层。保证自动合并的 precision，不因追求召回搞乱后续结构。

**第三层 — Evidence-centered HITL**：对模糊候选，规则系统先把疑似重复但不够确定的 pair 送出来，LLM 不做 yes/no 判决而是整理成结构化证据卡，人基于证据卡做轻量裁决。人工角色从"重新阅读 RFC 找答案"变成"审核系统整理好的证据并拍板"。

### 整体数据流

```
EXTRACT 产出的 protocol_schema.json（含冗余重复对象）
                    |
    =====================================
    |       第一层：表示层规范化          |
    |  名称包含关系 + 转移归一化 + 字段规范化  |
    =====================================
                    |  相似度分数更真实
                    v
    =====================================
    |       第二层：高置信规则合并        |
    |  结构证据优先，保守自动合并         |
    =====================================
                    |
          +---------+---------+
          |                   |
     已合并对象           未合并对象
     (auto-merged)       (remaining)
          |                   |
          |         +---------+---------+
          |         |                   |
          |    明显不同              模糊候选
          |    (skip)            (near-miss)
          |                         |
          |         =====================================
          |         |       第三层：HITL                 |
          |         |  LLM 证据卡 -> 人工轻量确认        |
          |         =====================================
          |                         |
          +----------+--------------+
                     |
              最终合并结果
              -> CODEGEN / VERIFY
```

### 产物文件

| 文件 | 产出阶段 | 说明 |
|------|----------|------|
| protocol_schema.json | 自动合并后 | 合并后的 schema |
| merge_report.json | 自动合并后 | 合并报告 + near-miss 摘要 |
| near_miss_report.json | 诊断输出 | 完整模糊候选列表（始终输出，P0） |
| review_cards.json | HITL | LLM 证据卡（仅 enable_hitl=true，P1） |
| review_decisions.json | HITL | 人工裁决结果（P1） |

---

## 第一层设计：表示层规范化

### 1.1 名称包含关系匹配

**问题**：RFC 里经常出现"全称 vs 简称""章节标题 vs 图标题"的现象。普通 Jaccard 对"长名称 vs 简称"得分偏低。

**方案**：在 Jaccard 基础上增加子集覆盖率 subset_ratio = |A∩B| / min(|A|, |B|)，取 max(jaccard, subset_ratio)。当 min(|A|, |B|) < 2 时不启用（防止单 token 虚高）。

**变更点**：`src/extract/sm_similarity.py :: name_similarity()`

**实现边界**：subset_ratio 只作用于 name token 集合的比较，不改变 normalize_name_v2() 的输出。
- "BFD Session Reset and Administrative Control State Machine"（7 tokens）vs "BFD Administrative Control"（3 tokens）
- Jaccard = 3/7 = 0.43（偏低）
- subset_ratio = 3/3 = 1.0（后者完全被前者覆盖）
- 最终 = max(0.43, 1.0) = 1.0

### 1.2 转移事件归一化

**问题**：当前 normalize_transition_key() 只取前 2 个有意义 token（[:2] 截断），导致语义相同但措辞不同的事件无法对齐。

**方案**：
1. 去掉 [:2] 截断，保留全部有意义 token
2. 仅对 event phrase 内的 token 排序，消除词序差异（不跨 from_state/to_state 维度；排序本身不改变 from_state/to_state 的值）
3. 扩展停用词表：加入 has/have/had/been/was/were/when/then/that/this/its

**变更点**：`src/extract/sm_similarity.py :: normalize_transition_key()`, `_EVENT_STOPWORDS`

**示例**：
- "timer expires" -> tokens: [expire] -> key: "expire"
- "when the timer has expired" -> tokens: [expire] -> key: "expire" (相同)
- "receive BFD Control packet" -> tokens: [control, receive] -> key: "control receive"
- "BFD Control packet is received" -> tokens: [control, receive] -> key: "control receive" (相同)

### 1.3 字段名规范化

**问题**：字段名有括号、缩写、全称混用，直接比较 Jaccard 偏低。

**方案**：新增 normalize_field_name() 函数和 FIELD_ABBREVIATION_MAP 缩写映射表。步骤：提取括号内缩写 -> 去括号 -> 转小写 -> 统一连字符/空格 -> 应用缩写映射 -> 排序去重。

**括号处理规则**：若字段名含括号（如 "Diagnostic (Diag)"），提取括号内文本作为额外 token 来源，然后去掉括号。所有 token（主名称 + 括号内）统一经过缩写映射后取 canonical form。最终 canonical form 是映射后 token 的排序集合。例如 "Diagnostic (Diag)" -> tokens: [diagnostic, diag] -> 映射后: [diagnostic, diagnostic] -> 去重: [diagnostic] -> canonical: "diagnostic"。

**变更点**：`src/extract/merge.py`（新增 normalize_field_name, FIELD_ABBREVIATION_MAP；修改 _field_name_jaccard 使用新函数）

**缩写映射表**（初始 >= 10 对，可扩展）：
```
vers/ver -> version, diag -> diagnostic, auth -> authentication,
len -> length, seq -> sequence, num -> number, addr -> address,
src -> source, dst -> destination, msg -> message, pkt -> packet,
hdr -> header, ctl -> control, cfg -> configuration
```

**关键约束**：normalize_field_name 仅在 _field_name_jaccard 计算路径中使用，合并后的 field.name 保持原始值。canonical form 不缓存回原始 schema。

---

## 第二层设计：高置信规则合并

### 2.1 结构证据优先

现有硬约束 A/B/C 和综合加权分数框架不变。P0 阶段只增强表示层，不调整 SM_MERGE_THRESHOLD（保持 0.65）。先跑 BFD 端到端看表示层增强的效果。若 BFD 结果仍不达标，在下一迭代中再考虑微调阈值（范围 0.50~0.65）。

核心原则：结构证据（状态集合、转移、字段）足够强时，名称分数略低可容忍；名称再像但结构支撑不足时，不轻易合并。

### 2.2 报文 field_jaccard 强候选规则

**变更点**：`src/extract/merge.py :: merge_messages_v2()` 模糊匹配循环

在阻断条件检查之后、现有双重门槛之前，加入：field_jaccard >= 0.8 且无互斥关键词冲突时，视为通过字段结构门槛，不再要求 name_similarity 达标。阻断条件始终优先，无例外。

---

## 第三层设计：Evidence-centered HITL

这是本方案的核心创新，分 P0（诊断输出）和 P1（LLM 证据卡 + 人工确认）两阶段。

### 3.1 模糊候选识别与诊断输出（P0）

**目的**：把"差一点"的对象显式暴露出来，附上没有通过的具体原因。这一步非常重要，因为它能帮助判断剩余问题到底来自名称、事件还是字段规范化不够，而不是盲目再加一轮更宽松的 merge。

**变更点**：
- `src/extract/sm_similarity.py`：新增 collect_sm_near_misses()
- `src/extract/merge.py`：merge_state_machines() 和 merge_messages_v2() 收集 near-miss
- `src/extract/pipeline.py`：输出 near_miss_report.json

**状态机 near-miss 收集逻辑**：
- 对不在同一簇的 pair，计算 weighted_score
- weighted >= NEAR_MISS_MIN_SCORE（默认 0.3，可配置常量）的输出诊断
- 每条包含：pair 索引、原始名称、三维分数、weighted_score、未满足的硬约束条件、差异摘要（仅在一方出现的状态名/转移 key）

**报文 near-miss 收集逻辑**：
- 对未合并的 pair，满足以下任一条件即输出诊断：
  - name_similarity >= 0.3
  - field_jaccard >= 0.3
- 注意：报文 near-miss 不使用 weighted score，而是使用 name_similarity / field_jaccard 双阈值独立判定
- 每条包含：pair 索引、原始名称、name_similarity、field_jaccard、仅在一方出现的字段名

**near_miss_report.json 格式**：
```json
{
  "doc_name": "rfc5880-BFD",
  "state_machine_near_misses": [
    {
      "pair": [2, 7],
      "names": ["BFD Session State Machine", "BFD Administrative Control"],
      "scores": {"name": 0.43, "states": 0.4, "transitions": 0.1},
      "weighted_score": 0.304,
      "unmet_constraints": ["A", "B", "C"],
      "diff": {
        "states_only_left": ["init", "up"],
        "states_only_right": [],
        "transitions_only_left_count": 5,
        "transitions_only_right_count": 2
      }
    }
  ],
  "message_near_misses": [...],
  "summary": {"sm_count": 3, "msg_count": 2}
}
```

### 3.2 LLM 证据卡生成（P1）

**新增文件**：`src/extract/evidence_card.py`

**LLM 角色定义**：协议证据整理器，不是裁决者。把原本分散在两个提取对象里的信息压缩成一个人能快速判断的单元。

**证据卡内容**：
- common_evidence：共同状态/字段列表
- differing_evidence：差异状态/字段列表
- naming_relation：名称包含/缩写关系分析
- wording_vs_substance：哪些差异只是措辞不同，哪些是本质不同
- llm_confidence：LLM 对"描述同一事物"的置信度（0.0~1.0）
- unresolved_conflicts：LLM 无法判断的冲突点

**最小 JSON schema**：
```json
{
  "pair_id": [2, 7],
  "object_type": "state_machine",
  "common_evidence": ["shared states: Init, Up", "both describe BFD session lifecycle"],
  "differing_evidence": ["left has 5 transitions, right has 2", "right lacks Down state"],
  "naming_relation": "right name is subset of left name (containment)",
  "wording_vs_substance": "transition event differences are wording-only (same semantics after normalization)",
  "llm_confidence": 0.82,
  "unresolved_conflicts": ["right includes AdminDown state not present in left"]
}
```

**LLM prompt 输入**：
- 两个对象的完整 JSON dump
- 两个对象 source_pages 对应的原文片段（通过 content_loader 获取）
- 规则层的分数和未满足条件

**关键约束**：LLM 不输出合并/不合并的最终判决。

### 3.3 人工轻量确认（P1）

**裁决输入**：规则分数 + 对象摘要 + LLM 证据卡
**裁决选项**：merge / keep_separate（存储格式预留 related 扩展）
**裁决优先级**：人工裁决结果优先级高于自动规则结果。若人工裁决为 keep_separate，即使规则判定可合并也强制拆分；反之亦然。
**裁决存储**：`data/out/<doc>/review_decisions.json`
**裁决应用**：pipeline 重跑时自动加载，merge 裁决强制 union，keep_separate 裁决强制拆分

### 3.4 Pipeline 断点续跑（P1）

- enable_hitl 采用"参数优先，config 兜底"模式：pipeline 函数参数 enable_hitl 优先；未传时读取 config.yaml 中的默认值
- enable_hitl=true 且有模糊候选时：MERGE 阶段产出 review_cards.json 后返回成功，但附带 pending_review=true 标记，不继续执行 CODEGEN/VERIFY 后续阶段
- 人工写入 review_decisions.json 后重跑：加载裁决，应用到 MERGE 阶段，继续后续阶段
- enable_hitl=false 或无模糊候选时：正常运行，仅输出 near_miss_report.json 供离线分析；不触发任何 LLM 调用
- review_cards.json 和 review_decisions.json 路径均为 `data/out/<doc>/`（与 near_miss_report.json 同目录）

---

## 正确性属性

1. 名称包含匹配对称性：name_similarity(a, b) == name_similarity(b, a)
2. 单 token 保护：min(|tokens|) < 2 时 name_similarity 等于纯 Jaccard
3. 转移归一化词序无关：相同有意义 token 集合 -> 相同 key
4. 字段规范化不修改原始数据：合并后 field.name 保持原始值
5. field_jaccard 硬约束阻断优先：互斥关键词阻断优先于 field_jaccard >= 0.8
6. 诊断不影响合并：near-miss 收集不改变自动合并结果
7. 向后兼容：enable_fuzzy_match=False 时行为不变
8. 裁决幂等性（P1）：同一裁决多次应用结果一致
