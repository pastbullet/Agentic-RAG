# 需求文档

## 简介

本文档定义 Schema 质量改进特性的需求，涵盖三个优先级方向：MERGE Phase 2 状态机去重合并、EXTRACT 精度修复、CODEGEN 显示优化。所有需求均从已批准的设计文档中推导，遵循 EARS 模式和 INCOSE 质量规则。

## 术语表

- **Pipeline**：协议提取流水线，从 PDF 到 schema 到代码生成的完整处理流程
- **ProtocolStateMachine**：协议状态机数据模型，包含 name、states、transitions、source_pages 字段
- **ProtocolState**：状态机中的单个状态，包含 name、description、is_initial、is_final 字段
- **ProtocolTransition**：状态机中的单个转移，包含 from_state、to_state、event、condition、actions 字段
- **ProtocolMessage**：协议报文数据模型，包含 name、fields、source_pages 字段
- **ProtocolField**：报文中的单个字段，包含 name、size_bits、description 字段
- **ProtocolSchema**：完整协议 schema，包含 state_machines、messages、timers 等列表
- **normalize_name_v2**：增强版名称归一化函数，支持 conservative 和 aggressive 两种模式
- **compute_sm_similarity**：状态机三维相似度计算函数，返回 name、states、transitions 三个分数
- **should_merge_state_machines**：硬约束合并判定函数，基于三个硬约束条件和综合分数门槛
- **cluster_state_machines**：基于 Union-Find 的状态机聚类函数
- **merge_state_machines**：状态机合并主函数，执行三步法（聚类 → 合并 → 报告）
- **merge_messages_v2**：增强版报文合并函数，支持模糊名称匹配和阻断条件
- **MSG_EXCLUSIVE_KEYWORDS**：报文互斥关键词列表，用于阻断不应合并的报文对
- **canonical_name**：schema 层的规范名称，合并后保持不变，用于回溯和调试
- **display_name**：codegen 层的显示名称，由 standardize_sm_name/standardize_msg_name 生成，仅用于文件名和 C 符号名
- **standardize_sm_name**：状态机 display name 标准化函数
- **standardize_msg_name**：报文 display name 标准化函数
- **MessageExtractor**：报文提取器，从 PDF 节点文本中提取 ProtocolMessage
- **sm_similarity 模块**：新增模块 `src/extract/sm_similarity.py`，包含相似度计算和硬约束判定逻辑
- **Jaccard 相似度**：集合相似度度量，定义为 |A ∩ B| / |A ∪ B|
- **Union-Find**：并查集数据结构，用于高效聚类
- **硬约束条件**：三个合并前置条件（条件 A/B/C），必须满足至少一个才允许合并
- **综合加权分数**：0.3 × name + 0.35 × states + 0.35 × transitions，最低门槛 0.65

## 需求

### Requirement 1: 名称归一化增强

**User Story:** 作为流水线开发者，我希望名称归一化函数支持 aggressive 去噪模式，以便状态机和报文的名称变体能被正确识别为相似。

#### Acceptance Criteria

1. WHEN normalize_name_v2 以 aggressive=False 调用时，THE normalize_name_v2 SHALL 产生与现有 normalize_name 完全一致的输出
2. WHEN normalize_name_v2 以 aggressive=True 调用时，THE normalize_name_v2 SHALL 移除括号内的 RFC 引用和章节号（如 "(RFC 5880 §6.1 Overview)"）
3. WHEN normalize_name_v2 以 aggressive=True 调用时，THE normalize_name_v2 SHALL 移除修饰词 "excerpt"、"overview"、"summary"
4. WHEN normalize_name_v2 以 aggressive=True 调用时，THE normalize_name_v2 SHALL 保留所有核心语义词（包括 state machine、session、version negotiation、administrative control、forwarding plane reset、poll、demand、failure detection、reception checks、concatenated paths、backward compatibility）
5. IF aggressive 模式剥离后名称为空字符串，THEN THE normalize_name_v2 SHALL 回退使用 conservative 模式的结果

### Requirement 2: 状态机相似度计算

**User Story:** 作为流水线开发者，我希望能计算两个状态机之间的多维相似度分数，以便为合并判定提供量化依据。

#### Acceptance Criteria

1. THE compute_sm_similarity SHALL 返回包含 "name"、"states"、"transitions" 三个键的字典，每个值在 [0.0, 1.0] 范围内
2. WHEN 计算名称相似度时，THE compute_sm_similarity SHALL 对两个名称分别调用 normalize_name_v2(aggressive=True) 后计算词集合的 Jaccard 相似度
3. WHEN 计算状态重叠度时，THE compute_sm_similarity SHALL 对两个状态机的状态名集合（归一化后）计算 Jaccard 相似度
4. WHEN 计算转移重叠度时，THE compute_sm_similarity SHALL 将每个转移归一化为 (from_state, to_state, event_keyword) 三元组后计算集合的 Jaccard 相似度
5. WHEN 两个状态集合或转移集合均为空时，THE compute_sm_similarity SHALL 对该维度返回 1.0
6. IF 状态机名称为空或 states/transitions 为 None，THEN THE compute_sm_similarity SHALL 返回全零分数 {"name": 0.0, "states": 0.0, "transitions": 0.0} 并记录 warning

### Requirement 3: 硬约束合并判定

**User Story:** 作为流水线开发者，我希望状态机合并判定采用硬约束加综合分数的双重门槛，以防止链式误合并。

#### Acceptance Criteria

1. THE should_merge_state_machines SHALL 在以下三个硬约束条件均不满足时返回 False：条件 A（state_overlap >= 0.6 且 name_similarity >= 0.4）、条件 B（transition_overlap >= 0.5 且 state_overlap >= 0.3）、条件 C（name_similarity >= 0.75 且 state_overlap >= 0.4）
2. WHEN 至少一个硬约束条件满足时，THE should_merge_state_machines SHALL 计算综合加权分数（0.3 × name + 0.35 × states + 0.35 × transitions），若分数低于 0.65 则返回 False
3. WHEN 至少一个硬约束条件满足且综合加权分数 >= 0.65 时，THE should_merge_state_machines SHALL 返回 True
4. IF scores 参数为 None，THEN THE should_merge_state_machines SHALL 内部调用 compute_sm_similarity 计算分数
5. IF scores 字典缺少必要字段，THEN THE should_merge_state_machines SHALL 返回 False 并记录 warning

### Requirement 4: 状态机聚类

**User Story:** 作为流水线开发者，我希望状态机能基于硬约束判定结果自动聚类，以便将相似的状态机分组合并。

#### Acceptance Criteria

1. THE cluster_state_machines SHALL 对所有状态机两两对调用 compute_sm_similarity 和 should_merge_state_machines 进行判定
2. THE cluster_state_machines SHALL 仅对 should_merge_state_machines 返回 True 的状态机对执行 Union-Find 合并
3. THE cluster_state_machines SHALL 返回的簇列表覆盖所有索引 [0, len(state_machines))，每个索引恰好出现在一个簇中
4. WHEN 所有状态机两两 should_merge 均为 False 时，THE cluster_state_machines SHALL 返回每个状态机独立成簇的结果

### Requirement 5: 状态机合并

**User Story:** 作为流水线开发者，我希望同一簇内的状态机能合并为单个状态机，保留所有信息且不引入重复。

#### Acceptance Criteria

1. WHEN 合并一组状态机时，THE _merge_sm_group SHALL 选择归一化后最短名称对应的原始名称作为 canonical name
2. WHEN 合并状态时，THE _merge_sm_group SHALL 按 normalize_state_name 去重，同名状态保留 description 最长的版本
3. WHEN 合并状态时，THE _merge_sm_group SHALL 对同名状态的 is_initial 和 is_final 取逻辑或
4. WHEN 合并转移时，THE _merge_sm_group SHALL 按 normalize_transition_key 去重，同 key 转移保留 actions 列表最长和 condition 最长的版本
5. THE _merge_sm_group SHALL 将所有输入状态机的 source_pages 取并集并排序去重
6. THE merge_state_machines SHALL 调用 cluster_state_machines 获取聚类结果，对多成员簇调用 _merge_sm_group 合并，单成员簇直接保留
7. THE merge_state_machines SHALL 返回合并后的状态机列表和合并组报告列表

### Requirement 6: 报文合并增强

**User Story:** 作为流水线开发者，我希望报文合并支持模糊名称匹配和阻断条件，以便合并语义相同但名称不完全一致的报文，同时防止误合并不同类型的报文。

#### Acceptance Criteria

1. THE merge_messages_v2 SHALL 先按 normalize_name 精确分组合并，再对未合并的单独报文进行模糊匹配
2. WHEN 两个报文名称包含 MSG_EXCLUSIVE_KEYWORDS 中同一互斥组的不同关键词时，THE merge_messages_v2 SHALL 跳过该对不合并
3. WHEN 模糊匹配时，THE merge_messages_v2 SHALL 同时要求名称相似度 >= name_similarity_threshold（默认 0.7）且字段名 Jaccard 相似度 >= field_jaccard_threshold（默认 0.5）
4. WHEN name_similarity_threshold 设为 1.0 时，THE merge_messages_v2 SHALL 产生与现有 merge_messages 在报文数量和名称上一致的输出
5. THE MSG_EXCLUSIVE_KEYWORDS SHALL 包含以下互斥组：{md5, sha1}、{simple password, keyed}、{echo, control}


### Requirement 7: Echo Packet 提取修复

**User Story:** 作为流水线开发者，我希望 MessageExtractor 能正确识别 opaque/variable 报文并返回空字段列表，以避免为不定义具体字段的报文生成错误的固定字段。

#### Acceptance Criteria

1. WHEN 报文被描述为 opaque、implementation-specific 或 "not defined by this specification" 时，THE MessageExtractor SHALL 返回空的 fields 列表
2. IF 提取后报文名含 "echo" 且字段数 <= 1，THEN THE Pipeline SHALL 在后处理中清空该报文的 fields 列表

### Requirement 8: Simple Password 字段修复

**User Story:** 作为流水线开发者，我希望可变长度字段被正确标记为 size_bits=None，以避免为可变长度字段分配错误的固定大小。

#### Acceptance Criteria

1. WHEN 字段长度被描述为 "variable"、"1 to N bytes" 或 "up to X bytes" 时，THE MessageExtractor SHALL 设置 size_bits=None 并在 description 中包含长度约束
2. IF 提取后字段名含 "password" 且 description 含 "variable"，THEN THE Pipeline SHALL 在后处理中将该字段的 size_bits 置为 None

### Requirement 9: Auth 段边界抑制

**User Story:** 作为流水线开发者，我希望每个 PDF 节点只提取其主对象的字段，不混入其他对象的字段，以保持报文边界清晰。

#### Acceptance Criteria

1. WHEN 节点主要描述认证段/格式时，THE MessageExtractor SHALL 仅提取认证字段，不包含主报文字段
2. WHEN 节点主要描述主控制报文时，THE MessageExtractor SHALL 仅提取主报文字段，不包含可选认证字段
3. THE MessageExtractor SHALL 保持单节点单报文的返回类型不变（返回单个 ProtocolMessage）

### Requirement 10: CODEGEN Display Name 标准化

**User Story:** 作为流水线开发者，我希望 codegen 层能从 schema canonical name 生成简洁的 display name 用于文件名和 C 符号名，同时不修改 schema 中的原始名称。

#### Acceptance Criteria

1. THE standardize_sm_name SHALL 移除 canonical name 中的括号内 RFC 引用和章节号，移除修饰词，保留核心语义词
2. THE standardize_msg_name SHALL 移除 "Generic" 前缀、括号内 RFC 引用、"Format" 后缀
3. THE standardize_sm_name 和 standardize_msg_name SHALL 仅生成 display name，不修改 ProtocolSchema 中的 name 字段
4. WHEN display name 经过 _sanitize_c_identifier 处理后，THE codegen SHALL 产生合法的 C 标识符（匹配 ^[a-zA-Z_][a-zA-Z0-9_]*$）

### Requirement 11: 合并报告扩展

**User Story:** 作为流水线开发者，我希望合并报告包含状态机合并信息，同时保持向后兼容。

#### Acceptance Criteria

1. THE build_merge_report SHALL 接受可选参数 state_machine_groups（类型为 list[dict] | None，默认 None）
2. WHEN state_machine_groups 为 None 时，THE build_merge_report SHALL 不在报告中包含该字段（向后兼容）
3. WHEN state_machine_groups 非 None 时，THE build_merge_report SHALL 在报告中包含每个合并组的 canonical_name、merged_from、similarity_scores、hard_constraint_met、source_pages_union、states_before、states_after、transitions_before、transitions_after

### Requirement 12: Pipeline MERGE 阶段集成

**User Story:** 作为流水线开发者，我希望 pipeline.py 的 MERGE 阶段调用新的状态机合并逻辑，以实现端到端的状态机去重。

#### Acceptance Criteria

1. THE Pipeline SHALL 在 MERGE 阶段调用 merge_state_machines 对过滤后的状态机列表进行合并
2. THE Pipeline SHALL 将 merge_state_machines 返回的 sm_groups 传递给 build_merge_report 的 state_machine_groups 参数
3. IF merge_state_machines 整体失败，THEN THE Pipeline SHALL 回退为不合并（直接传递 filtered_state_machines），MERGE 阶段仍标记为成功但在 warning 中注明状态机未合并

### Requirement 13: 错误处理与回退

**User Story:** 作为流水线开发者，我希望所有新增模块在异常时安全回退，不中断整体流水线。

#### Acceptance Criteria

1. IF 相似度计算发生异常，THEN THE compute_sm_similarity SHALL 返回全零分数并记录 warning，不中断流程
2. IF 硬约束判定发生异常，THEN THE should_merge_state_machines SHALL 返回 False 并记录 warning
3. IF 聚类过程发生异常，THEN THE cluster_state_machines SHALL 回退为每个状态机独立成簇
4. IF 单个组合并失败，THEN THE merge_state_machines SHALL 保留该组所有原始状态机，记录 warning，继续处理其他组
5. IF 转移归一化产生冲突（两个语义不同的转移归一化为相同 key），THEN THE _merge_sm_group SHALL 保留 actions 更多的版本并记录 warning
