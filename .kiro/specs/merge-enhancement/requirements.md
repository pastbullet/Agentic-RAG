# 需求文档：MERGE 阶段增强 — Phase 1（Merge Enhancement）

## 简介

本特性增强现有协议提取流水线（`src/extract/pipeline.py`）的 MERGE 阶段。当前 MERGE 阶段（`_merge_to_schema` 函数）仅做简单的 append 操作，将所有提取结果直接收集到 ProtocolSchema 中，不做任何去重或质量过滤。以 BFD（RFC 5880）为例，当前输出存在明显质量问题：9 个定时器全部名为 "Detection Time"（明显重复）、1 个空报文（0 个字段，应被过滤）。

本 Phase 1 聚焦五项改进：中间提取结果落盘（`extract_results.json`）、空结果过滤、同名定时器合并、同名报文合并+字段去重、合并统计报告落盘（`merge_report.json`）。合并逻辑已在 `src/extract/merge.py` 中实现并通过代码审查，本需求覆盖其集成到流水线以及对应的测试。

设计原则：规则优先不上第二轮 LLM、保守合并宁可少合并也别错合并、merge 结果必须可解释可回溯、先把中间结果落盘补齐再做去重。

Phase 2（不在本需求范围内）将处理状态机保守合并和 procedure 同名合并。

## 术语表

- **Extraction_Pipeline**：协议提取流水线的主编排模块（`src/extract/pipeline.py`），负责串联 CLASSIFY → EXTRACT → MERGE → CODEGEN → VERIFY 五个阶段
- **Merge_Module**：合并逻辑模块（`src/extract/merge.py`），包含空结果过滤、同名合并、报告生成等函数
- **ExtractionRecord**：中间提取记录数据类，携带单个提取器输出及其溯源信息（node_id、title、label、confidence、source_pages、payload）
- **normalize_name**：保守名称归一化函数，仅移除章节号、RFC 引用、标点符号，不移除语义词汇（如 "state machine"、"procedure"）
- **ProtocolSchema**：协议的完整结构化表示，包含 state_machines、messages、procedures、timers、errors
- **ProtocolMessage**：报文/帧结构模型，包含 name、fields、source_pages
- **ProtocolField**：报文字段模型，包含 name、type、size_bits、description
- **TimerConfig**：定时器配置模型，包含 timer_name、timeout_value、trigger_action、description、source_pages
- **Merge_Report**：合并统计报告，记录合并前后各类对象数量、被过滤的空对象数量、合并分组详情

## 需求

### 需求 1：中间提取结果落盘

**用户故事：** 作为协议工程师，我希望 EXTRACT 阶段完成后将所有提取结果以 ExtractionRecord 格式保存到 JSON 文件，以便在 MERGE 之前可以检查和回溯每个节点的提取输出。

#### 验收标准

1. WHEN EXTRACT 阶段完成后，THE Extraction_Pipeline SHALL 将所有成功提取的结果转换为 ExtractionRecord 列表，每个 ExtractionRecord 包含 node_id、title、label、confidence、source_pages、payload（提取对象的 model_dump 输出）
2. WHEN ExtractionRecord 列表生成后，THE Extraction_Pipeline SHALL 将该列表序列化为 JSON 并保存到 `data/out/{doc_stem}_extract_results.json` 文件
3. THE Extraction_Pipeline SHALL 在 EXTRACT 阶段的 StageResult.data 中记录 extract_results_path 字段，指向保存的文件路径
4. FOR ALL 有效的 ExtractionRecord 列表，序列化为 JSON 后再反序列化 SHALL 产生与原始列表等价的 ExtractionRecord 对象（round-trip 性质）

### 需求 2：空结果过滤

**用户故事：** 作为协议工程师，我希望 MERGE 阶段能自动过滤掉空的提取结果（如 0 个字段的报文、无状态无转移的状态机），以避免无意义的对象污染最终 ProtocolSchema。

#### 验收标准

1. WHEN MERGE 阶段开始处理提取结果时，THE Merge_Module SHALL 使用以下判定规则识别空结果：状态机无 states 且无 transitions 为空、报文无 fields 为空、procedure 无 steps 为空、定时器无 timeout_value 且无 trigger_action 且无 description 为空、错误规则无 handling_action 且无 description 为空
2. WHEN 识别到空结果时，THE Merge_Module SHALL 将该对象从后续合并流程中移除，并记录被移除对象的类型和数量
3. THE Merge_Module SHALL 在 Merge_Report 的 dropped_empty_counts 字段中按类型记录被过滤的空对象数量（state_machine、message、procedure、timer、error）
4. WHEN 所有某一类型的提取结果均为空时，THE Merge_Module SHALL 将该类型的合并结果设为空列表，继续处理其他类型

### 需求 3：同名定时器合并

**用户故事：** 作为协议工程师，我希望 MERGE 阶段能将名称归一化后相同的定时器合并为一个，以消除因不同章节重复描述同一定时器而产生的冗余。

#### 验收标准

1. WHEN 处理定时器列表时，THE Merge_Module SHALL 使用 normalize_name 函数对每个定时器的 timer_name 进行归一化，并按归一化后的名称分组
2. WHEN 同一归一化名称下存在多个定时器时，THE Merge_Module SHALL 将它们合并为一个定时器：source_pages 取所有定时器的并集并排序、description 取最长的非空值、trigger_action 取最长的非空值、timeout_value 取所有不同值中最长的作为规范值
3. WHEN 同一归一化名称下仅有一个定时器时，THE Merge_Module SHALL 保留该定时器不做修改
4. THE Merge_Module SHALL 在 Merge_Report 的 merged_groups.timer 中记录每个合并组的详情：归一化键名、合并来源名称列表、source_pages 并集、timeout_value 变体列表
5. FOR ALL 定时器列表，合并后的定时器数量 SHALL 小于或等于合并前的数量
6. FOR ALL 合并后的定时器，其 source_pages SHALL 包含合并前所有同名定时器的 source_pages 的并集

### 需求 4：同名报文合并与字段去重

**用户故事：** 作为协议工程师，我希望 MERGE 阶段能将名称归一化后相同的报文合并为一个，并对字段进行去重，以消除因不同章节重复描述同一报文而产生的冗余。

#### 验收标准

1. WHEN 处理报文列表时，THE Merge_Module SHALL 使用 normalize_name 函数对每个报文的 name 进行归一化，并按归一化后的名称分组
2. WHEN 同一归一化名称下存在多个报文时，THE Merge_Module SHALL 将它们合并为一个报文：source_pages 取所有报文的并集并排序、name 取字段数最多的报文的原始名称
3. WHEN 合并报文的字段时，THE Merge_Module SHALL 按 normalize_name 对字段名去重：保留首次出现的字段名原始大小写、size_bits 优先取非 null 值、description 优先取较长值、type 优先取非空值
4. WHEN 同一归一化名称下仅有一个报文时，THE Merge_Module SHALL 保留该报文不做修改
5. THE Merge_Module SHALL 在 Merge_Report 的 merged_groups.message 中记录每个合并组的详情：归一化键名、合并来源名称列表、source_pages 并集、合并前总字段数、合并后字段数
6. FOR ALL 报文列表，合并后的报文数量 SHALL 小于或等于合并前的数量
7. FOR ALL 合并后的报文，其 source_pages SHALL 包含合并前所有同名报文的 source_pages 的并集

### 需求 5：合并统计报告落盘

**用户故事：** 作为协议工程师，我希望 MERGE 阶段完成后生成一份详细的合并统计报告并保存到文件，以便我能审查合并过程的每一步决策，确保合并结果可解释、可回溯。

#### 验收标准

1. WHEN MERGE 阶段完成后，THE Merge_Module SHALL 生成 Merge_Report，包含以下统计信息：pre_merge_counts（合并前各类型对象数量）、dropped_empty_counts（被过滤的空对象数量）、post_merge_counts（合并后各类型对象数量）、merged_groups（定时器和报文的合并分组详情）
2. THE Extraction_Pipeline SHALL 将 Merge_Report 序列化为 JSON 并保存到 `data/out/{doc_stem}_merge_report.json` 文件
3. THE Extraction_Pipeline SHALL 在 MERGE 阶段的 StageResult.data 中记录 merge_report_path 字段，指向保存的文件路径
4. FOR ALL 有效的 Merge_Report，pre_merge_counts 中每个类型的数量 SHALL 等于 dropped_empty_counts 中对应类型的数量加上进入合并流程的对象数量
5. FOR ALL 有效的 Merge_Report，post_merge_counts 中每个类型的数量 SHALL 小于或等于 pre_merge_counts 中对应类型的数量减去 dropped_empty_counts 中对应类型的数量

### 需求 6：Pipeline MERGE 阶段集成

**用户故事：** 作为协议工程师，我希望流水线的 MERGE 阶段调用 Merge_Module 中的合并函数替代当前的简单 append 逻辑，以使去重和过滤功能在正常流水线运行中自动生效。

#### 验收标准

1. WHEN MERGE 阶段执行时，THE Extraction_Pipeline SHALL 调用 Merge_Module 的空结果过滤函数对所有五种类型的提取结果进行过滤
2. WHEN 空结果过滤完成后，THE Extraction_Pipeline SHALL 调用 Merge_Module 的 merge_timers 函数对定时器列表进行同名合并
3. WHEN 空结果过滤完成后，THE Extraction_Pipeline SHALL 调用 Merge_Module 的 merge_messages 函数对报文列表进行同名合并与字段去重
4. WHEN 合并完成后，THE Extraction_Pipeline SHALL 调用 Merge_Module 的 build_merge_report 函数生成合并统计报告
5. THE Extraction_Pipeline SHALL 使用合并后的结果（而非原始提取结果）构建最终的 ProtocolSchema
6. WHEN MERGE 阶段执行时，THE Extraction_Pipeline SHALL 对 state_machines、procedures、errors 仅执行空结果过滤，不执行同名合并（Phase 1 范围限制）

### 需求 7：ExtractionRecord 序列化与反序列化（Round-Trip）

**用户故事：** 作为系统开发者，我希望 ExtractionRecord 的 JSON 序列化和反序列化是无损的，以确保中间提取结果落盘后能被完整还原用于调试和回溯。

#### 验收标准

1. THE Extraction_Pipeline SHALL 使用 JSON 格式序列化 ExtractionRecord 列表，每个 ExtractionRecord 的所有字段（node_id、title、label、confidence、source_pages、payload）均完整保留
2. FOR ALL 有效的 ExtractionRecord 列表，序列化为 JSON 后再反序列化 SHALL 产生与原始列表等价的 ExtractionRecord 对象（round-trip 性质）
3. THE ExtractionRecord 的 payload 字段 SHALL 包含提取对象的完整 model_dump 输出，保留所有嵌套结构

### 需求 8：merge.py 单元测试

**用户故事：** 作为系统开发者，我希望 merge.py 中的所有函数都有对应的单元测试和属性测试覆盖，以确保合并逻辑的正确性和鲁棒性。

#### 验收标准

1. THE 测试套件 SHALL 在 `tests/extract/test_merge.py` 中为 normalize_name 函数编写测试，覆盖章节号移除、RFC 引用移除、标点符号移除、语义词汇保留等场景
2. THE 测试套件 SHALL 为五个空结果判定函数（is_empty_state_machine、is_empty_message、is_empty_procedure、is_empty_timer、is_empty_error）编写测试，覆盖空对象和非空对象的判定
3. THE 测试套件 SHALL 为 merge_timers 函数编写测试，覆盖单个定时器不合并、多个同名定时器合并、source_pages 并集、timeout_value 变体选择等场景
4. THE 测试套件 SHALL 为 merge_messages 函数编写测试，覆盖单个报文不合并、多个同名报文合并、字段去重（size_bits 优先非 null、description 优先较长、type 优先非空）等场景
5. THE 测试套件 SHALL 为 build_merge_report 函数编写测试，验证报告结构的完整性和数值一致性
6. THE 测试套件 SHALL 使用 Hypothesis 属性测试验证：合并后定时器数量小于等于合并前数量、合并后报文数量小于等于合并前数量、合并后 source_pages 为合并前的超集
