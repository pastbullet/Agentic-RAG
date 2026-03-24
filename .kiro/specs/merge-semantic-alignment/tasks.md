# 实现计划：merge-semantic-alignment

## P0 — 表示层规范化 + 高置信规则增强 + 诊断输出

### 第一层：表示层规范化

- [ ] 1. 名称包含关系匹配
  - [ ] 1.1 修改 `sm_similarity.py :: name_similarity()`：增加 subset_ratio，取 max(jaccard, subset_ratio)；min token count < 2 时不启用
    - _REQ-1.1_
  - [ ] 1.2 测试：长名称 vs 简称 >= 0.8；单 token 不启用；对称性
    - `tests/extract/test_sm_similarity.py`
    - _REQ-1.1_

- [ ] 2. 转移事件归一化
  - [ ] 2.1 修改 `sm_similarity.py :: normalize_transition_key()`：去掉 [:2] 截断，保留全部有意义 token，排序
    - _REQ-1.2_
  - [ ] 2.2 扩展 `_EVENT_STOPWORDS`：加入 has/have/had/been/was/were/when/then/that/this/its
    - _REQ-1.2_
  - [ ] 2.3 测试："timer expires" == "when the timer has expired"；"receive BFD Control packet" == "BFD Control packet is received"
    - `tests/extract/test_sm_similarity.py`
    - _REQ-1.2_

- [ ] 3. 字段名规范化
  - [ ] 3.1 新增 `merge.py :: FIELD_ABBREVIATION_MAP`（>= 10 对）和 `normalize_field_name()`
    - _REQ-1.3_
  - [ ] 3.2 修改 `merge.py :: _field_name_jaccard()` 使用 normalize_field_name
    - _REQ-1.3, REQ-1.4_
  - [ ] 3.3 测试："Diagnostic (Diag)" == "Diag"；"Vers" == "Version"；合并后 field.name 不变
    - `tests/extract/test_merge_enhanced.py`
    - _REQ-1.3, REQ-1.4_

- [ ] 4. Checkpoint — 表示层验证
  - 运行全部现有测试确保零回归

### 第二层：高置信规则增强

- [ ] 5. 报文 field_jaccard 强候选规则
  - [ ] 5.1 修改 `merge.py :: merge_messages_v2()` 模糊匹配循环：阻断条件后加入 field_jaccard >= 0.8 且无互斥关键词冲突时视为强候选，通过字段结构门槛
    - _REQ-2.2_
  - [ ] 5.2 测试：field_jaccard >= 0.8 时合并；互斥关键词阻断优先
    - `tests/extract/test_merge_enhanced.py`
    - _REQ-2.2_

### 第三层（P0 部分）：诊断输出

- [ ] 6. 模糊候选识别与 near-miss 诊断
  - [ ] 6.1 新增 `sm_similarity.py :: collect_sm_near_misses()`：对不在同一簇且 weighted >= NEAR_MISS_MIN_SCORE 的 pair 输出诊断
    - _REQ-3.1_
  - [ ] 6.2 修改 `merge.py :: merge_state_machines()` 返回值增加 near-miss 列表
    - _REQ-3.1_
  - [ ] 6.3 在 `merge.py :: merge_messages_v2()` 中收集报文 near-miss（注意：报文 near-miss 不使用 weighted score，而是使用 name_similarity >= 0.3 或 field_jaccard >= 0.3 双阈值独立判定）
    - _REQ-3.1_
  - [ ] 6.4 修改 `merge.py :: build_merge_report()` 新增 near_miss_summary 参数
    - _REQ-3.1, REQ-4.1_
  - [ ] 6.5 更新 `pipeline.py`：输出 `data/out/<doc>/near_miss_report.json`，summary 传入 merge_report
    - _REQ-3.1_
  - [ ] 6.6 测试：weighted >= NEAR_MISS_MIN_SCORE 出现在诊断中；已合并 pair 不出现；诊断不影响合并结果；已被强制合并的 pair 不重复出现在 near-miss；无候选时 near_miss_report.json 仍有稳定格式（空列表 + summary 全零）
    - `tests/extract/test_merge_state_machines.py`
    - _REQ-3.1_

### 验证

- [ ] 7. 向后兼容
  - [ ] 7.1 全部现有测试零回归
    - _REQ-4.1_
  - [ ] 7.2 enable_fuzzy_match=False 行为不变
    - _REQ-4.1_

- [ ] 8. BFD 端到端
  - 运行完整 pipeline，检查：状态机 <= 4、报文 <= 5、near_miss_report.json 格式正确
  - 若未达目标，分析 near-miss 数据确定调优方向（为 P1 提供输入）

---

## P1 — LLM 证据卡 + 人工确认 + 断点续跑

### 第三层（P1 部分）：Evidence-centered HITL

- [ ] 9. LLM 证据卡生成
  - [ ] 9.1 创建 `src/extract/evidence_card.py`：EvidenceCard 数据模型
    - _REQ-3.2_
  - [ ] 9.2 实现证据卡 LLM prompt：system prompt 定义 LLM 为证据整理器；user prompt 包含两个对象 JSON + 原文片段 + 规则分数
    - _REQ-3.2_
  - [ ] 9.3 实现 generate_evidence_cards()：对每个 near-miss 加载对象数据和原文，调用 LLM 生成证据卡。原文片段输入采用 token 截断策略：每边 source_pages 对应原文 <= 4000 tokens，超出时按页码顺序截断并标注 [truncated]
    - _REQ-3.2_
  - [ ] 9.4 测试：输出包含所有必要字段（pair_id, object_type, common_evidence, differing_evidence, naming_relation, wording_vs_substance, llm_confidence, unresolved_conflicts）；不包含 merge / decision / should_merge 之类最终裁决字段
    - `tests/extract/test_evidence_card.py`
    - _REQ-3.2_

- [ ] 10. 人工轻量确认
  - [ ] 10.1 实现裁决读写：load_review_decisions / save_review_decisions
    - _REQ-3.3_
  - [ ] 10.2 实现裁决应用：merge 裁决强制 union，keep_separate 裁决强制拆分
    - _REQ-3.3_
  - [ ] 10.3 测试：裁决正确应用；裁决幂等性；无裁决文件时行为不变
    - `tests/extract/test_review_decisions.py`
    - _REQ-3.3_

- [ ] 11. Pipeline 断点续跑
  - [ ] 11.1 enable_hitl 支持"参数优先，config 兜底"：pipeline 函数参数 enable_hitl 优先；未传时读取 config.yaml 中的默认值（默认 false）
    - _REQ-4.2_
  - [ ] 11.2 修改 pipeline.py：enable_hitl=true 且有 near-miss 时，MERGE 阶段产出 review_cards.json 后返回成功但标记 pending_review=true，不继续执行 CODEGEN/VERIFY 后续阶段。断言：pending_review=true 时不得写出 CODEGEN/VERIFY 阶段产物
    - _REQ-3.4_
  - [ ] 11.3 修改 pipeline.py：启动时加载 review_decisions.json 并应用到 MERGE 阶段
    - _REQ-3.4_
  - [ ] 11.4 测试：enable_hitl=false 正常运行且无 LLM 调用；enable_hitl=true 有 near-miss 时暂停；裁决后重跑应用裁决
    - `tests/extract/test_pipeline.py`
    - _REQ-3.4, REQ-4.2_

- [ ] 12. P1 端到端验证
  - 对 BFD 运行 enable_hitl=true 完整流程
  - 验证证据卡质量：是否包含足够信息让人快速判断
  - 验证裁决应用后 schema 质量进一步提升
  - 验证幂等性：人工裁决应用后再次运行，不应重复生成冲突裁决，结果应稳定
