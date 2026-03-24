# 实现计划：协议提取 Pipeline（Protocol Extraction Pipeline）

## 概述

按五阶段流水线（分类 → 提取 → 合并 → 代码生成 → 验证）增量实现协议提取功能。实现顺序遵循设计文档的优先级：先跑通 Stage 1（P1），再逐步叠加提取器和编排层。代码生成采用分层递进策略：先状态机 → 再报文数据类 + pretty print → 再 parser/encoder → 最后自动测试验证。

## Tasks

- [ ] 1. 数据模型扩展与项目结构搭建
  - [ ] 1.1 扩展 `src/models.py`，新增辅助提取结果模型
    - 新增 `ProcedureStep`、`ProcedureRule`、`TimerConfig`、`ErrorRule` 模型
    - 扩展 `ProtocolSchema`，添加 `procedures: list[ProcedureRule]`、`timers: list[TimerConfig]`、`errors: list[ErrorRule]` 字段
    - 确认 `NodeSemanticLabel`、`NodeLabelMeta`、`NodeLabelType` 已存在且定义正确
    - _需求: 9.1, 9.2, 9.4_

  - [ ] 1.2 创建 `src/extract/` 目录结构和空模块文件
    - 创建 `src/extract/__init__.py`
    - 创建 `src/extract/extractors/__init__.py`
    - 创建 `tests/extract/__init__.py`
    - _需求: 13.1_

  - [ ]* 1.3 编写 ProtocolSchema 扩展后的 round-trip 属性测试
    - **Property 10: ProtocolSchema 序列化 Round-Trip**
    - **验证: 需求 10.2**
    - 在 `tests/extract/test_schema_roundtrip.py` 中用 Hypothesis 生成任意 ProtocolSchema（含 procedures/timers/errors），验证 `model_dump_json()` → `model_validate_json()` round-trip

- [ ] 2. 节点内容获取模块（content_loader.py）
  - [ ] 2.1 实现 `src/extract/content_loader.py` 的 `get_node_text` 函数
    - 若节点有非空 `text` 字段 → 直接返回
    - 否则根据 `start_index`/`end_index` 从 Content DB 加载页面，再根据 `start_line`/`end_line` 切取行范围
    - Content DB 缺失时返回 None 并记录错误日志
    - 复用 `src/tools/page_content.py` 中的 `_load_page_data` 逻辑
    - _需求: 5.1, 5.2, 5.3_

  - [ ]* 2.2 编写 content_loader 属性测试
    - **Property 7: 节点内容获取正确性**
    - **验证: 需求 5.1, 5.2**
    - 用 Hypothesis 生成带/不带 text 字段的节点，验证 get_node_text 的分支选择逻辑

- [ ] 3. 节点语义分类器（classifier.py）— Stage 1 核心
  - [ ] 3.1 实现 `src/extract/classifier.py` 的分类核心逻辑
    - 实现 `classify_node`：构造分类 prompt（含六类标签定义、优先级规则、procedure_rule 排他规则），调用 LLM，解析返回 JSON 为 NodeSemanticLabel
    - 实现 `classify_all_nodes`：遍历叶节点，逐节点调用 classify_node，单节点失败时记录错误并跳过
    - 实现优先级选择辅助函数 `resolve_priority`：从候选标签中按优先级列表选择最高优先级标签
    - 定义 `DEFAULT_LABEL_PRIORITY` 和 `PROMPT_VERSION` 常量
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 4.1, 14.1_

  - [ ] 3.2 实现分类结果持久化与缓存
    - 实现 `save_labels`、`load_labels`、`save_meta`、`load_meta` 函数
    - 实现 `load_or_classify` 带缓存入口：比较 model_name/prompt_version/label_priority 判断缓存有效性
    - _需求: 2.1, 2.2, 2.3, 2.4_

  - [ ] 3.3 实现人工修正覆盖（override）合并
    - 实现 `apply_overrides`：加载 override.json，合并覆盖到分类结果
    - override 中 node_id 不存在于分类结果时记录警告并跳过
    - general_description 节点被跳过时记录 skipped_count、skipped_node_ids、skipped_by_label 统计信息
    - _需求: 3.1, 3.2, 3.3, 8.4_

  - [ ]* 3.4 编写分类器属性测试
    - **Property 2: NodeSemanticLabel 有效性不变量**
    - **Property 3: 优先级冲突消解**
    - **Property 5: 缓存有效性判断**
    - **Property 6: Override 合并正确性**
    - **验证: 需求 1.2, 1.3, 1.5, 2.3, 2.4, 3.1, 3.3**
    - 在 `tests/extract/test_classifier.py` 中分别编写四个属性测试

  - [ ]* 3.5 编写分类数据序列化 round-trip 属性测试
    - **Property 4: 分类数据序列化 Round-Trip**
    - **验证: 需求 2.1, 2.2**
    - 在 `tests/extract/test_schema_roundtrip.py` 中验证 `dict[str, NodeSemanticLabel]` 和 `NodeLabelMeta` 的 JSON round-trip

- [ ] 4. Checkpoint — Stage 1 验证
  - 确保所有测试通过，用 BFD（rfc5880-BFD）文档手动验证分类结果是否合理，如有问题请向用户确认。

- [ ] 5. 提取器基类与状态机提取器（P2）
  - [ ] 5.1 实现 `src/extract/extractors/base.py` 提取器基类
    - 定义 `BaseExtractor` 抽象基类，包含 `llm` 属性和 `extract` 抽象方法
    - _需求: 6, 7, 8_

  - [ ] 5.2 实现 `src/extract/extractors/state_machine.py`
    - 实现 `StateMachineExtractor.extract`：构造提取 prompt，调用 LLM，解析返回 JSON 为 ProtocolStateMachine
    - 提取 states（name, description, is_initial, is_final）和 transitions（from_state, to_state, event, condition, actions）
    - 提取失败返回空 ProtocolStateMachine 并记录警告
    - _需求: 6.1, 6.2, 6.3, 6.4, 14.2_

  - [ ]* 5.3 编写状态机提取器单元测试
    - Mock LLM 返回 BFD 状态机 JSON，验证解析为 ProtocolStateMachine 的正确性
    - 测试空文本、LLM 返回非法 JSON 等边界条件
    - _需求: 6.1, 6.2, 6.3, 6.4_

- [ ] 6. 报文格式提取器（P3）
  - [ ] 6.1 实现 `src/extract/extractors/message.py`
    - 实现 `MessageExtractor.extract`：构造提取 prompt，调用 LLM，解析返回 JSON 为 ProtocolMessage
    - 提取 fields（name, type, size_bits, description），优先从表格中提取
    - 提取失败返回空 ProtocolMessage 并记录警告
    - _需求: 7.1, 7.2, 7.3, 7.4, 14.2_

  - [ ]* 6.2 编写报文格式提取器单元测试
    - Mock LLM 返回 BFD Control Packet 字段 JSON，验证解析正确性
    - 测试含表格和不含表格的节点文本
    - _需求: 7.1, 7.2, 7.3, 7.4_

- [ ] 7. Pipeline 主流程编排（P4）
  - [ ] 7.1 实现 `src/extract/pipeline.py` 核心编排逻辑
    - 实现 `PipelineStage` 枚举和 `StageResult` 数据类
    - 实现 `_collect_leaf_nodes`：递归遍历文档树收集 is_skeleton=false 的叶节点
    - 实现 `_route_to_extractor`：根据 NodeLabelType 返回对应提取器实例，general_description 返回 None
    - 实现 `_merge_to_schema`：将所有提取结果合并为 ProtocolSchema
    - general_description 跳过时记录 skipped_count、skipped_node_ids、skipped_by_label
    - _需求: 8.4, 9.1, 9.2, 9.3, 9.4, 13.1_

  - [ ] 7.2 实现 `run_pipeline` 主入口函数
    - 从 registry 获取文档配置，加载 page_index.json
    - 按 stages 顺序执行各阶段，支持指定部分阶段
    - 任一阶段失败 → 停止后续阶段，返回已完成结果
    - 每阶段完成后记录耗时和处理节点数
    - 流水线完成后汇总报告成功/失败节点数量和失败 node_id 列表
    - _需求: 13.1, 13.2, 13.3, 13.4, 14.3_

  - [ ]* 7.3 编写 Pipeline 属性测试
    - **Property 8: 提取器路由正确性**
    - **Property 9: Schema 合并完整性**
    - **Property 13: Pipeline 阶段控制**
    - **Property 14: 故障隔离**
    - **验证: 需求 8.4, 9.1, 9.2, 9.4, 13.2, 13.3, 14.1, 14.2, 14.3**
    - 在 `tests/extract/test_pipeline.py` 中编写四个属性测试

- [ ] 8. Checkpoint — Stage 1-3 集成验证
  - 确保所有测试通过，用 BFD 文档跑通分类 → 提取 → 合并全流程，检查 protocol_schema.json 输出是否合理，如有问题请向用户确认。

- [ ] 9. 代码生成模块 — 分层递进（P5）
  - [ ] 9.1 实现 `src/extract/codegen.py` 骨架与状态机代码生成
    - 实现 `generate_code` 主入口函数
    - 实现 `_generate_state_machine`：为每个 ProtocolStateMachine 生成 Python 代码（状态枚举 + 转移函数 + 事件处理）
    - 输出到 `data/out/{doc_stem}_generated/` 目录
    - _需求: 11.1, 11.3, 11.4_

  - [ ] 9.2 实现报文数据类 + pretty printer 代码生成
    - 实现 `_generate_message_parser` 第一层：为每个 ProtocolMessage 生成数据类定义和 pretty print 函数
    - _需求: 11.2, 11.5_

  - [ ] 9.3 实现报文 parser + encoder 代码生成
    - 在 `_generate_message_parser` 中添加 parse（从字节解析）和 encode（序列化为字节）函数
    - _需求: 11.2, 11.6_

  - [ ]* 9.4 编写代码生成属性测试
    - **Property 11: 生成代码语法有效性**
    - **Property 12: 报文解析/格式化 Round-Trip**
    - **验证: 需求 11.1, 11.2, 11.3, 11.5, 11.6, 12.1**
    - 在 `tests/extract/test_codegen.py` 中用 Hypothesis 生成 ProtocolSchema，验证生成代码可被 ast.parse 解析
    - 验证 parse → pretty print → parse 的 round-trip 性质

- [ ] 10. Checkpoint — 代码生成验证
  - 确保所有测试通过，用 BFD 的 ProtocolSchema 生成代码并检查输出质量，如有问题请向用户确认。

- [ ] 11. 验证模块（P6）
  - [ ] 11.1 实现 `src/extract/verify.py`
    - 实现 `VerifyReport` 数据类
    - 实现 `verify_generated_code` 主入口：语法检查（ast.parse）+ 自动生成单元测试 + 执行测试 + 收集结果
    - 实现 `_check_syntax`、`_generate_tests`、`_run_tests` 辅助函数
    - 支持测试向量验证（如协议文档中有示例数据）
    - 输出 `data/out/{doc_stem}_verify_report.json`
    - _需求: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [ ]* 11.2 编写验证模块单元测试
    - 测试语法检查对合法/非法 Python 代码的判断
    - 测试自动生成测试用例的覆盖范围
    - _需求: 12.1, 12.2, 12.5_

- [ ] 12. 辅助提取器（P7）
  - [ ] 12.1 实现 `src/extract/extractors/procedure.py`
    - 实现 `ProcedureExtractor.extract`：提取处理步骤列表（condition + action）
    - _需求: 8.1_

  - [ ] 12.2 实现 `src/extract/extractors/timer.py`
    - 实现 `TimerExtractor.extract`：提取定时器配置（timer_name, timeout_value, trigger_action）
    - _需求: 8.2_

  - [ ] 12.3 实现 `src/extract/extractors/error.py`
    - 实现 `ErrorExtractor.extract`：提取错误处理规则（error_condition, handling_action）
    - _需求: 8.3_

  - [ ]* 12.4 编写辅助提取器单元测试
    - Mock LLM 返回预设 JSON，验证三个辅助提取器的解析逻辑
    - _需求: 8.1, 8.2, 8.3_

- [ ] 13. 全流程集成与最终 Checkpoint
  - [ ] 13.1 将辅助提取器接入 Pipeline 路由和 Schema 合并
    - 确保 `_route_to_extractor` 正确路由 procedure_rule、timer_rule、error_handling
    - 确保 `_merge_to_schema` 将辅助提取结果写入 ProtocolSchema 的 procedures/timers/errors 字段
    - _需求: 8.1, 8.2, 8.3, 9.1, 9.2_

  - [ ] 13.2 最终 Checkpoint — 全流程端到端验证
    - 确保所有测试通过，用 BFD 文档跑通完整五阶段流水线（分类 → 提取 → 合并 → 代码生成 → 验证），检查 verify_report.json 输出，如有问题请向用户确认。

## Notes

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用了对应的需求编号，确保需求可追溯
- Checkpoint 任务用于增量验证，确保每个阶段稳定后再推进
- 代码生成（任务 9）严格按分层递进：状态机 → 数据类+pretty print → parser/encoder → 自动测试
- 属性测试使用 Hypothesis 库，每个属性至少 100 次迭代
- 实现语言为 Python，与现有项目一致
