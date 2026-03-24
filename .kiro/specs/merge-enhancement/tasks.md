# 实现计划：MERGE 阶段增强 — Phase 1（Merge Enhancement）

## 概述

本计划基于已完成的 `src/extract/merge.py` 模块，将其集成到 `src/extract/pipeline.py` 的 EXTRACT/MERGE 阶段，并补充完整的测试覆盖。实现顺序：先补测试确保 merge.py 逻辑正确 → 再改 pipeline.py 集成 → 最后端到端验证。

当前测试状态：`tests/extract/` 下 23/23 测试通过，`test_merge.py` 尚未创建。

## Tasks

- [ ] 1. merge.py 单元测试
  - [ ] 1.1 创建 `tests/extract/test_merge.py`，编写 normalize_name 测试
    - 测试章节号移除：`"6.8.1 BFD State Machine"` → `"bfd state machine"`
    - 测试 RFC 引用移除：`"RFC 5880 Detection Time"` → `"detection time"`
    - 测试语义词汇保留：`"state machine"`、`"procedure"`、`"overview"` 不被删除
    - 测试空字符串和 None 输入
    - _需求: 8.1_

  - [ ] 1.2 编写五个空结果判定函数的测试
    - 每个 `is_empty_*` 函数测试空对象返回 True、非空对象返回 False
    - 覆盖边界情况：仅有 description 但无 handling_action 的 ErrorRule 不为空
    - _需求: 8.2_

  - [ ] 1.3 编写 merge_timers 测试
    - 单个定时器不合并，返回原对象
    - 多个同名定时器合并：source_pages 取并集、description 取最长、timeout_value 取最长
    - 验证 report_groups 中的 timeout_value_variants 记录
    - 不同名定时器各自独立
    - _需求: 8.3_

  - [ ] 1.4 编写 merge_messages 测试
    - 单个报文不合并
    - 多个同名报文合并：source_pages 取并集、name 取字段最多的原始名称
    - 字段去重：size_bits 优先非 null、description 优先较长、type 优先非空
    - 验证 report_groups 中的 field_count_before/after
    - _需求: 8.4_

  - [ ] 1.5 编写 build_merge_report 测试
    - 验证返回字典包含 pre_merge_counts、dropped_empty_counts、post_merge_counts、merged_groups
    - 验证 merged_groups 包含 timer 和 message 子键
    - _需求: 8.5_

- [ ] 2. merge.py 属性测试
  - [ ] 2.1 编写 Hypothesis 属性测试（在 `tests/extract/test_merge.py` 中追加）
    - **Property 2: 同名合并单调递减** — merge_timers 和 merge_messages 合并后数量 ≤ 合并前
    - **Property 3: source_pages 并集完整性** — 合并后 source_pages 包含所有原始 source_pages
    - **Property 6: ExtractionRecord round-trip** — JSON 序列化后反序列化等价
    - 每个属性测试 `@settings(max_examples=100)`
    - _需求: 8.6_

- [ ] 3. Pipeline EXTRACT 阶段集成 — ExtractionRecord 构建与落盘
  - [ ] 3.1 修改 `src/extract/pipeline.py` EXTRACT 阶段
    - 在 EXTRACT 阶段开头初始化 `records: list[ExtractionRecord] = []`
    - 在每次成功提取后，构建 ExtractionRecord 并追加到 records
    - EXTRACT 循环结束后，将 records 序列化为 JSON 写入 `data/out/{doc_stem}_extract_results.json`
    - 在 stage_data 中添加 `extract_results_path` 字段
    - _需求: 1.1, 1.2, 1.3_

- [ ] 4. Pipeline MERGE 阶段集成 — 空结果过滤与同名合并
  - [ ] 4.1 修改 `src/extract/pipeline.py` MERGE 阶段
    - 导入 merge.py 的过滤和合并函数
    - 记录 pre_merge_counts
    - 对五种类型执行空结果过滤，计算 dropped_counts
    - 调用 merge_timers 对过滤后的定时器列表合并
    - 调用 merge_messages 对过滤后的报文列表合并
    - state_machines / procedures / errors 仅过滤不合并（Phase 1 限制）
    - 用合并后的结果调用 _merge_to_schema 构建 ProtocolSchema
    - 调用 build_merge_report 生成报告
    - 将 merge_report 写入 `data/out/{doc_stem}_merge_report.json`
    - 在 StageResult.data 中添加 merge_report_path 和更新后的各类型计数
    - _需求: 2.1, 2.2, 2.3, 3.1, 3.2, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [ ] 5. 确保现有测试不被破坏
  - [ ] 5.1 运行 `tests/extract/` 全部测试，确认 23/23 仍然通过
    - 如有失败，分析原因并修复（MERGE 阶段的 StageResult.data 结构变化可能影响已有断言）
    - _需求: 6.1-6.6（隐含）_

- [ ] 6. Checkpoint — 端到端验证
  - 运行全部测试确认通过
  - 用 BFD（rfc5880-BFD）文档跑一遍完整 pipeline（CLASSIFY → EXTRACT → MERGE）
  - 检查三个输出文件：extract_results.json、merge_report.json、protocol_schema.json
  - 验证 BFD 的 9 个 Detection Time 定时器合并为 1 个
  - 验证空报文被过滤
  - 如有问题请向用户确认

## Notes

- merge.py 已实现并通过代码审查，任务 1-2 是补测试，不改实现代码
- 任务 3-4 是 pipeline.py 的集成改动，改动集中在 EXTRACT 和 MERGE 两个 stage 分支内
- Phase 1 明确不做：state_machine 合并、procedure 语义合并、跨名称 message 合并、第二轮 LLM merge
- 属性测试使用 Hypothesis 库，每个属性至少 100 次迭代
