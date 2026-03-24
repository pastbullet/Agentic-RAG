# 实现计划：Schema 质量改进（MERGE 去重 + EXTRACT 精度 + CODEGEN 显示优化）

## 概述

按优先级顺序实现三项改进：先完成 MERGE Phase 2 状态机去重与报文合并增强，再修复 EXTRACT 精度问题，最后做 CODEGEN 显示优化。每个阶段包含实现任务和对应的属性测试/单元测试子任务。

## Tasks

- [ ] 1. 实现名称归一化增强与状态机相似度模块
  - [ ] 1.1 在 `src/extract/merge.py` 中实现 `normalize_name_v2()`
    - 支持 `aggressive=False`（向后兼容，与现有 `normalize_name` 一致）和 `aggressive=True`（去噪模式）
    - aggressive 模式：移除括号内 RFC 引用/章节号、移除修饰词 excerpt/overview/summary、保留所有核心语义词
    - 空字符串回退：aggressive 剥离后为空时回退 conservative 结果
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.2 编写 `normalize_name_v2` 属性测试（Property 6）
    - **Property 6: normalize_name_v2 向后兼容**
    - 使用 Hypothesis 生成随机字符串，验证 `normalize_name_v2(s, aggressive=False) == normalize_name(s)`
    - 测试文件：`tests/extract/test_merge_enhanced.py`
    - **Validates: Requirement 1.1**

  - [ ] 1.3 创建 `src/extract/sm_similarity.py` 模块，实现相似度计算函数
    - 实现 `normalize_state_name()`：转小写、去空白、同义词映射（asynchronous→async, pollsequence→poll）
    - 实现 `normalize_transition_key()`：归一化为 (from_state, to_state, event_keyword) 三元组
    - 实现 `name_similarity()`：normalize_name_v2(aggressive=True) 后词集合 Jaccard
    - 实现 `state_overlap()`：归一化状态名集合 Jaccard（双空返回 1.0）
    - 实现 `transition_overlap()`：归一化转移三元组集合 Jaccard（双空返回 1.0）
    - 实现 `compute_sm_similarity()`：返回 {"name", "states", "transitions"} 三维分数
    - 异常处理：无效输入返回全零分数并记录 warning
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ]* 1.4 编写相似度对称性与自反性属性测试（Property 1）
    - **Property 1: 相似度对称性与自反性**
    - 使用 Hypothesis `protocol_state_machine_strategy` 生成随机状态机对
    - 验证 `compute_sm_similarity(a, b) == compute_sm_similarity(b, a)`（对称性）
    - 验证 `compute_sm_similarity(a, a)` 每个维度为 1.0（自反性）
    - 验证所有分数在 [0.0, 1.0] 范围内
    - 测试文件：`tests/extract/test_sm_similarity.py`
    - **Validates: Requirement 2.1**

- [ ] 2. 实现硬约束合并判定与聚类
  - [ ] 2.1 在 `src/extract/sm_similarity.py` 中实现 `should_merge_state_machines()`
    - 步骤 1：硬约束检查（条件 A/B/C 至少满足一个）
    - 步骤 2：综合加权分数 >= 0.65（0.3×name + 0.35×states + 0.35×transitions）
    - scores 为 None 时内部调用 compute_sm_similarity
    - scores 缺少字段时返回 False 并记录 warning
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 2.2 编写硬约束判定对称性属性测试（Property 2）
    - **Property 2: 硬约束判定对称性**
    - 使用 Hypothesis 生成随机状态机对，验证 `should_merge(a, b) == should_merge(b, a)`
    - 测试文件：`tests/extract/test_sm_similarity.py`
    - **Validates: Requirements 3.1, 3.4**

  - [ ] 2.3 在 `src/extract/sm_similarity.py` 中实现 `cluster_state_machines()`
    - Union-Find 数据结构（路径压缩）
    - 对所有两两对计算分数 + 硬约束判定
    - 仅对 should_merge == True 的 pair 执行 union
    - 返回结果：簇内索引升序、簇按最小索引排序（测试确定性）
    - 异常时回退为每个状态机独立成簇
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 2.4 编写聚类完整性与互斥性属性测试（Property 3）
    - **Property 3: 聚类完整性与互斥性**
    - 使用 Hypothesis 生成随机状态机列表，验证簇列表覆盖所有索引且无重复
    - 测试文件：`tests/extract/test_merge_state_machines.py`
    - **Validates: Requirement 4.3**

- [ ] 3. 实现状态机合并逻辑
  - [ ] 3.1 在 `src/extract/merge.py` 中实现 `_merge_sm_group()` 和 `merge_state_machines()`
    - `_merge_sm_group`：canonical name 选择（最短归一化名称；若仍并列，优先 source_pages 覆盖更多者）、状态 union 去重、转移 union 去重、source_pages 并集
    - `merge_state_machines`：调用 cluster → 对多成员簇调用 _merge_sm_group → 构建报告
    - 单个组合并失败时保留原始状态机并记录 warning
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 3.2 编写合并不丢失信息属性测试（Property 4）
    - **Property 4: 合并不丢失信息**
    - 使用 Hypothesis 生成随机状态机组（len >= 2），验证合并后状态/转移/source_pages 为输入并集的超集
    - 测试文件：`tests/extract/test_merge_state_machines.py`
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.5**

  - [ ]* 3.3 编写合并幂等性属性测试（Property 5）
    - **Property 5: 合并幂等性**
    - 使用 Hypothesis 生成随机状态机列表，验证 `merge_state_machines(merge_state_machines(sms)[0])` 不再进一步合并
    - 测试文件：`tests/extract/test_merge_state_machines.py`
    - **Validates: Requirement 5.6**

- [ ] 4. 实现报文合并增强
  - [ ] 4.1 在 `src/extract/merge.py` 中实现 `merge_messages_v2()` 及辅助函数
    - 实现 `_message_name_similarity()`：词集合 Jaccard
    - 实现 `_field_name_jaccard()`：字段名集合 Jaccard
    - 实现 `_has_exclusive_keywords()`：遍历 MSG_EXCLUSIVE_KEYWORDS 检查互斥
    - 定义 `MSG_EXCLUSIVE_KEYWORDS`：{md5, sha1}、{simple password, keyed}、{echo, control}
    - `merge_messages_v2`：先精确分组 → 再模糊匹配（阻断条件 + 双重门槛）→ Union-Find 聚类
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 4.2 编写报文合并向后兼容属性测试（Property 7）
    - **Property 7: 报文合并增强向后兼容**
    - `merge_messages_v2` 新增参数 `enable_fuzzy_match: bool = True`；当 `enable_fuzzy_match=False` 时跳过模糊匹配分支（阻断条件、field_jaccard、name_similarity 均不执行），仅做精确分组合并
    - 使用 Hypothesis 生成随机报文列表，验证 `merge_messages_v2(msgs, enable_fuzzy_match=False)` 与 `merge_messages(msgs)` 输出在数量和名称上一致
    - 测试文件：`tests/extract/test_merge_enhanced.py`
    - **Validates: Requirement 6.4**

  - [ ]* 4.3 编写报文互斥关键词阻断属性测试（Property 8）
    - **Property 8: 报文互斥关键词阻断**
    - 构造包含互斥关键词的报文对（md5 vs sha1、simple password vs keyed、echo vs control），验证不被合并
    - 测试文件：`tests/extract/test_merge_enhanced.py`
    - **Validates: Requirement 6.2**

- [ ] 5. Checkpoint — MERGE 阶段验证
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. 实现合并报告扩展与 Pipeline 集成
  - [ ] 6.1 扩展 `src/extract/merge.py` 中的 `build_merge_report()`
    - 新增可选参数 `state_machine_groups: list[dict] | None = None`
    - None 时不在报告中包含该字段（向后兼容）
    - 非 None 时包含 canonical_name、merged_from、similarity_scores、hard_constraint_met、source_pages_union、states_before/after、transitions_before/after
    - _Requirements: 11.1, 11.2, 11.3_

  - [ ] 6.2 更新 `src/extract/pipeline.py` MERGE 阶段
    - 调用 `merge_state_machines(filtered_state_machines)` 替代直接传递
    - 将 sm_groups 传递给 `build_merge_report(state_machine_groups=sm_groups)`
    - 异常回退：merge_state_machines 失败时回退为不合并，记录 warning
    - _Requirements: 12.1, 12.2, 12.3_

  - [ ]* 6.3 编写 build_merge_report 向后兼容单元测试
    - 验证不传 state_machine_groups 时报告格式不变
    - 验证传入 state_machine_groups 时报告包含所有必要字段
    - 测试文件：`tests/extract/test_merge_state_machines.py`
    - _Requirements: 11.1, 11.2, 11.3_

- [ ] 7. 实现 EXTRACT 精度修复
  - [ ] 7.1 修改 `src/extract/extractors/message.py` 提取提示词
    - 添加 ECHO_PACKET_RULE：opaque/implementation-specific 报文返回空 fields
    - 添加 VARIABLE_LENGTH_RULE：可变长度字段设 size_bits=None
    - 添加 AUTH_BOUNDARY_RULE：单节点只抽主对象，不混入其他对象字段
    - 保持 MessageExtractor 返回类型不变（单个 ProtocolMessage）
    - _Requirements: 7.1, 8.1, 9.1, 9.2, 9.3_

  - [ ] 7.2 在 `src/extract/extractors/message.py` 的提取结果后处理中添加 Echo/Password 安全网检查
    - Echo：报文名含 "echo" 且字段数 <= 1 时清空 fields
    - Password：字段名含 "password" 且 description 含 "variable" 时置 size_bits=None
    - 职责归属：message 语义修复，不放 pipeline 编排层
    - _Requirements: 7.2, 8.2_

  - [ ]* 7.3 编写 EXTRACT 修复单元测试
    - 验证 Echo Packet 修复后 fields 为空
    - 验证 Password 字段修复后 size_bits 为 None
    - 验证 Auth 边界抑制规则存在于提示词中
    - 测试文件：`tests/extract/test_extract_fixes.py`
    - _Requirements: 7.1, 7.2, 8.1, 8.2, 9.1, 9.2_

- [ ] 8. Checkpoint — EXTRACT 修复验证
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. 实现 CODEGEN 显示优化
  - [ ] 9.1 在 `src/extract/codegen.py` 中实现 `standardize_sm_name()` 和 `standardize_msg_name()`
    - `standardize_sm_name`：移除括号内 RFC 引用/章节号、移除修饰词、保留核心语义词
    - `standardize_msg_name`：移除 "Generic" 前缀、括号内 RFC 引用、"Format" 后缀
    - 仅生成 display name，不修改 schema 对象
    - _Requirements: 10.1, 10.2, 10.3_

  - [ ] 9.2 更新 `src/extract/codegen.py` 文件名生成流程
    - 从 schema 获取 canonical name → 调用 standardize 函数获取 display name → _to_lower_snake 生成文件名
    - 确保 display name 经 `_sanitize_c_identifier` 后为合法 C 标识符
    - _Requirements: 10.4_

  - [ ]* 9.3 编写标识符合法性属性测试（Property 9）
    - **Property 9: 标识符合法性保持**
    - 使用 Hypothesis 生成随机 canonical name，验证经 standardize + _sanitize_c_identifier 后匹配 `^[a-zA-Z_][a-zA-Z0-9_]*$`
    - 测试文件：`tests/extract/test_codegen_naming.py`
    - **Validates: Requirement 10.4**

  - [ ]* 9.4 编写 schema canonical name 不可变性测试（Property 10）
    - **Property 10: schema canonical name 不可变性**
    - 构造 ProtocolSchema，调用 codegen 流程后验证 state_machines[i].name 和 messages[i].name 未被修改
    - 测试文件：`tests/extract/test_codegen_naming.py`
    - **Validates: Requirement 10.3**

- [ ] 10. 错误处理与回退路径完善
  - [ ] 10.1 确保所有新增模块的异常安全回退
    - compute_sm_similarity 异常 → 全零分数 + warning
    - should_merge_state_machines 异常 → False + warning
    - cluster_state_machines 异常 → 每个状态机独立成簇
    - merge_state_machines 单组失败 → 保留原始状态机 + warning
    - pipeline merge_state_machines 整体失败 → 回退不合并 + warning
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

- [ ] 11. Final checkpoint — 全部测试通过
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用具体需求编号以确保可追溯性
- 属性测试使用 Hypothesis 库，每个 property 对应设计文档中的一个正确性属性
- 优先级顺序：MERGE（任务 1-6）→ EXTRACT（任务 7-8）→ CODEGEN（任务 9）→ 错误处理（任务 10）
- build_merge_report 的 state_machine_groups 参数默认 None，保持向后兼容
