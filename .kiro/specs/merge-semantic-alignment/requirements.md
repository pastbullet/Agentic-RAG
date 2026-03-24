# 需求文档：merge-semantic-alignment

## 问题陈述

协议提取流水线从 PDF 全文提取状态机和报文结构后，同一类对象会因为分散在不同章节而被抽成多个独立对象。这些对象之所以没有被合并，不是因为它们真的不同，而是因为它们在原文中以不同名字、不同措辞、不同缩写方式出现，导致当前基于表面文字的比较方式无法识别它们的语义一致性。

仅靠硬规则，后续会退化为补丁系统；仅靠 LLM 判定，会有边界漂移（LLM 容易把"相关"判断成"可合并"）。最稳的做法是把三者分工拉开：规则负责批量初筛，LLM 负责整理证据，人负责对少量模糊对象做最终裁决。

## 方案概述

三层架构：
1. 表示层规范化：解决"为什么同一对象看起来不像同一对象"
2. 高置信规则合并：保证真正显而易见的重复对象能自动处理
3. LLM 证据整理 + 轻量人工确认：处理规则无法稳定判断的边界样本，不要求人工通读原文

最终形成一个不依赖人工通读原文的合并流程。

---

## 第一层：表示层规范化

### REQ-1: 状态机名称包含关系匹配

RFC 里经常出现长标题和简称并存的情况。名称匹配应支持包含关系，而不是只看普通 token 重合。

验收标准：
- 当一个名称的核心 token 基本被另一个名称覆盖时，相似度应显著高于普通 Jaccard
- "BFD Session Reset and Administrative Control State Machine" 与 "BFD Administrative Control" 的名称相似度 >= 0.8
- 单 token 名称不应因包含关系而虚高（min token count < 2 时不启用包含匹配）

### REQ-2: 状态转移事件归一化

同一个状态机在不同章节里，事件描述经常只是换了一种写法。转移比较不能只靠非常字面的 event key，要先做归一化，尽量消除措辞差异，让"同一个事件的不同表达"能够对齐。

验收标准：
- "timer expires" 与 "when the timer has expired" 归一化后对齐为同一事件
- "receive BFD Control packet" 与 "BFD Control packet is received" 归一化后对齐为同一事件
- 归一化应保留全部有意义 token（去停用词、统一词形），而非截断
- 归一化后词序不影响匹配结果

### REQ-3: 报文字段名规范化

字段名规范化应处理括号、缩写、全称、连接符等写法差异，让字段重合度真正反映结构一致性，而不是反映原文表面写法是否完全一致。

验收标准：
- "Diagnostic (Diag)" 与 "Diag" 规范化后匹配
- "Vers" 与 "Version" 通过缩写映射后匹配
- "Auth Type" 与 "Authentication Type" 通过缩写映射后匹配
- 缩写映射表为可扩展结构，初始覆盖 >= 10 对高频缩写

### REQ-4: 规范化不修改原始数据

字段名规范化仅用于相似度计算，不修改提取结果中的原始字段名。

验收标准：
- 合并后的报文字段名保持原始大小写和格式
- 规范化仅在相似度计算路径中使用

---

## 第二层：高置信规则合并

### REQ-5: 保守自动合并策略

### REQ-2.2 报文合并字段结构优先

当两个报文的字段重合度极高时（field_jaccard >= 0.8）且无互斥关键词冲突，即使名称相似度未达阈值，也应视为强合并候选。

验收标准：
- field_jaccard >= 0.8 且无互斥关键词冲突时，视为通过字段结构门槛，不再要求 name_similarity 达标
- 阻断条件（MSG_EXCLUSIVE_KEYWORDS）始终优先于此规则，无例外
- BFD 的两个 SHA1 认证段变体（field_jaccard=1.0）能被自动合并）能被自动合并

当两个报文的字段几乎完全一致时，即使名字略有差异，也应被视为强合并候选。

验收标准：
- 字段重合度极高（>= 0.8）时可直接通过，不要求名称相似度达标
- 互斥关键词阻断条件仍优先于此规则

---

## 第三层：Evidence-centered HITL

### REQ-7: 模糊候选识别与诊断输出
### REQ-3.1 Near-miss 诊断输出

对未被合并但结构上接近的 pair，系统应输出诊断信息。状态机和报文使用不同的 near-miss 判定条件：

- 状态机 near-miss：weighted_score >= NEAR_MISS_MIN_SCORE（默认 0.3，可配置常量）
- 报文 near-miss：name_similarity >= 0.3 或 field_jaccard >= 0.3（任一满足即输出）

验收标准：
- 独立输出 `near_miss_report.json`（路径 `data/out/<doc>/near_miss_report.json`）
- merge_report.json 新增 `near_miss_summary` 字段（计数摘要）
- 每条状态机记录包含：pair 索引、三维分数、weighted_score、未满足的硬约束条件、差异摘要
- 每条报文记录包含：pair 索引、name_similarity、field_jaccard、仅在一方出现的字段名
- 诊断输出不影响合并结果
### REQ-8: LLM 证据卡生成
验收标准：
- 证据卡包含：共同状态/字段列表、差异状态/字段列表、名称包含/缩写关系分析、措辞差异 vs 本质差异的判断、LLM 置信度
- LLM prompt 中包含两个对象的 JSON dump 及其 source_pages 对应的原文片段（每边 <= 4000 tokens，超出按页码顺序截断）
- 证据卡输出为固定 JSON schema，可序列化存储
- 证据卡不得输出最终 merge/no-merge 判决（不含 decision / should_merge 等字段）
- 证据卡不得引入输入对象和原文片段之外的新事实
- 证据卡输出为固定 JSON schema，包含且仅包含以下字段：common_evidence、differing_evidence、naming_relation、wording_vs_substance、llm_confidence、unresolved_conflicts
- 证据卡不得包含 decision / should_merge / recommendation 等最终判决字段
- 证据卡不得引入原文中不存在的新事实（仅整理和对比已有信息）
- LLM prompt 中包含两个对象的 JSON dump 及其 source_pages 对应的原文片段
- 原文片段输入应限制长度（每边 <= 4000 token），超长时按 source_pages 截取最相关页
### REQ-3.3 人工轻量确认接口

系统应提供人工审核接口，让用户基于证据卡做最终裁决。

验收标准：
- 裁决选项为二选一：合并 / 不合并（第一版不支持 related/parent-child）
- 人工裁决结果可持久化存储，pipeline 重跑时自动应用
- 人工裁决优先级高于自动规则结果（裁决覆盖规则判定）
验收标准：
- enable_hitl=true 且存在 near-miss 候选时，MERGE 阶段返回 success 但附带 pending_review=true 标记（不是 failure，不是异常中断），不继续执行 CODEGEN/VERIFY
- 人工写入 review_decisions.json 后重跑，pipeline 加载裁决并应用到 MERGE 阶段，继续后续阶段
- 人工裁决优先级高于自动规则结果
- 无 near-miss 候选时，pipeline 行为与当前完全一致间状态并在人工审核后继续执行。

验收标准：
- MERGE 阶段完成后，若存在 near-miss 候选且 HITL 启用，MERGE 阶段返回成功但附带 pending_review=true 状态，不继续执行 CODEGEN/VERIFY
- 人工审核完成后，pipeline 可从 MERGE 结果继续执行 CODEGEN/VERIFY
- 无 near-miss 候选时，pipeline 行为与当前完全一致
- "暂停"不是阶段失败，也不是抛异常，而是正常返回但标记待审核
- 有模糊候选且 HITL 启用时，输出证据卡并暂停
- 人工审核完成后，pipeline 可从中间状态继续
- 无模糊候选或 HITL 未启用时，pipeline 行为与当前完全一致

---

## 向后兼容与可选性

### REQ-11: 现有接口向后兼容

所有新增功能不破坏现有接口，现有测试全部通过。
验收标准：
- enable_hitl 采用"参数优先，config 兜底"模式：pipeline 函数参数优先；未传时读取 config.yaml 默认值（默认 false）
- 不启用 HITL 时，near-miss 诊断仍然输出到 near_miss_report.json
- enable_hitl=false 时，不得产生任何额外 LLM 调用（包括证据卡生成）
第三层（LLM 证据卡 + 人工确认）为可选功能，不启用时系统行为与第二层完全一致。

验收标准：
- enable_hitl 支持 pipeline 函数参数传入（优先）和 config.yaml 配置（兜底），默认不启用
- 不启用 HITL 时，near-miss 诊断仍然输出到 near_miss_report.json
- 不启用 HITL 时，不调用 LLM，不生成 review_cards.json，不触发任何额外 LLM 开销（表示层 + 规则增强 + 诊断输出 + 向后兼容）
- P1：REQ-8 ~ REQ-10, REQ-12（LLM 证据卡 + 人工确认 + 断点续跑 + HITL 可选）

P0 做完即可验证 BFD 改善效果并获得 near-miss 诊断数据。
P1 基于 P0 的 near-miss 数据设计 evidence card prompt 和 HITL 流程。
