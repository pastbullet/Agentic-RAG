---
title: Step 2c 真实 API 验证节点（TCP + BFD）
date: 2026-04-05
tags:
  - validation
  - experiments
  - checkpoint
  - step2c
  - tcp
  - bfd
status: completed
---

# Step 2c 真实 API 验证节点（TCP + BFD）

## 1. 记录目的

本文档用于单独归档 Step 2c 完成后的真实 API 验证结果，作为一次独立实验节点。

与 [2026-04-05_standalone_fsm_step0_to_2c_full_record.md](/Users/zwy/毕设/Kiro/docs/validation_and_experiments/experiment_records/2026-04-05_standalone_fsm_step0_to_2c_full_record.md) 不同，本文档只记录本次 live run 的实际观测数据，不再展开方案演化过程。

## 2. 验证前说明

在本次 fresh TCP 重跑之前，发现已有的 `v1.4` TCP classify artifact 不可信：

- [node_labels.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/node_labels.json) 中曾出现 `0059 / GLOSSARY -> state_machine`
- rationale 明显是 general-description 语义，与 label 不一致

因此，本次真实 API 验证先将旧的 TCP classify / extract 产物备份到：

- [`/tmp/kiro_step2c_tcp_backup_20260405`](/tmp/kiro_step2c_tcp_backup_20260405)

随后重新执行 fresh `classify,extract`，再继续 full-chain 验证。

## 3. 运行命令

### 3.1 TCP fresh classify/extract

```bash
python3 run_extract_pipeline.py --doc rfc793-TCP.pdf --stages classify,extract
```

### 3.2 TCP full chain

```bash
python3 run_extract_pipeline.py --doc rfc793-TCP.pdf --stages classify,extract,merge,codegen,verify
```

### 3.3 BFD full chain

```bash
python3 run_extract_pipeline.py --doc rfc5880-BFD.pdf --stages classify,extract,merge,codegen,verify
```

## 4. TCP：fresh classify/extract

### 4.1 classify

- `success = True`
- `duration = 368.53s`
- `nodes = 51`
- `state_machine_sanity_downgrade_count = 8`
- `state_machine_sanity_downgrade_by_reason = {'meta_section': 1, 'call_procedure': 1, 'numbered_check': 6}`
- `fsm_segment_count = 3`
- `fsm_segment_reclassified_count = 2`
- `fsm_segment_updated_node_count = 5`
- `fsm_segment_skipped_count = 1`
- `fsm_segment_skip_reasons = {'no_parent': 0, 'single_node': 0, 'empty_targets': 0, 'over_limit': 1, 'llm_error': 0, 'invalid_response': 0}`

对应 artifact：

- [node_labels.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/node_labels.json)
- [node_labels.meta.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/node_labels.meta.json)

artifact 检查结果：

- `prompt_version = v1.4-standalone-fsm-segment-reclassification`
- `model_name = gpt-5.4`
- `classified_state_machine_count = 1`

唯一保留的 `state_machine` 节点为：

- `0025`
- 语义：`§3.5 Closing a Connection`

### 4.2 extract

- `success = True`
- `duration = 238.59s`
- `nodes = 51`
- `success_count = 29`
- `failure_count = 0`
- `empty_fsm_return_count = 0`
- `message_count = 4`
- `state_machine_count = 1`
- `state_machine_context_augmented_count = 1`

对应 artifact：

- [extract_results.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/extract_results.json)

artifact 检查结果：

- `extract_state_machine_count = 1`
- `empty_fsm_return_count = 0`
- 未发现新的 malformed FSM payload

最终唯一保留的 FSM extraction 节点为：

- `0025 / 3.5 Closing a Connection`

## 5. TCP：full chain

说明：

- 该次 full-chain run 复用了前一轮 fresh classify 产物，因此 classify 阶段耗时为 `0.00s`
- 因此，Step 2c 的效果判断以第 4 节的 fresh classify/extract 为准
- 本节主要用于记录 merge / codegen / verify 的 full-chain 结果

### 5.1 classify

- `success = True`
- `duration = 0.00s`
- `nodes = 51`
- `state_machine_sanity_downgrade_count = 8`
- `state_machine_sanity_downgrade_by_reason = {'meta_section': 1, 'call_procedure': 1, 'numbered_check': 6}`
- `fsm_segment_count = 1`
- `fsm_segment_reclassified_count = 0`
- `fsm_segment_updated_node_count = 0`
- `fsm_segment_skipped_count = 1`
- `fsm_segment_skip_reasons = {'no_parent': 0, 'single_node': 0, 'empty_targets': 0, 'over_limit': 1, 'llm_error': 0, 'invalid_response': 0}`

### 5.2 extract

- `success = True`
- `duration = 228.31s`
- `success_count = 29`
- `failure_count = 0`
- `empty_fsm_return_count = 0`
- `message_count = 4`
- `state_machine_count = 1`
- `state_machine_context_augmented_count = 1`

### 5.3 merge

- `success = True`
- `duration = 0.03s`
- `message_count = 3`
- `message_ir_count = 5`
- `ready_message_ir_count = 1`
- `state_machine_count = 1`
- `llm_refine_triggered_count = 0`
- `raw_branch_ratio_before = 0.3`
- `raw_branch_ratio_after = 0.3`
- `warnings = []`

对应 artifact：

- [protocol_schema.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/protocol_schema.json)
- [message_ir.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/message_ir.json)

### 5.4 codegen

- `success = True`
- `duration = 0.03s`
- `typed_action_count = 7`
- `generated_action_count = 2`
- `degraded_action_count = 5`
- `action_codegen_ratio = 0.2857142857142857`

### 5.5 verify

- `success = True`
- `duration = 2.05s`
- `syntax_ok = True`
- `test_roundtrip_stub = True`
- `test_roundtrip_runtime = True`

对应 artifact：

- [verify_report.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/verify_report.json)

### 5.6 TCP 节点结论

本次真实 API full-chain 结果显示：

- `classified_state_machine_count = 1`
- `extract_state_machine_count = 1`
- `merge_state_machine_count = 1`
- `raw_branch_ratio = 0.3`
- `generated_action_count / degraded_action_count = 2 / 5`
- `verify = True`

这说明 Step 2c 在真实 API 下已经完成收敛，且效果强于预期目标（`<= 4`）。

## 6. BFD：full chain

### 6.1 classify

- `success = True`
- `duration = 322.93s`
- `nodes = 49`
- `state_machine_sanity_downgrade_count = 5`
- `state_machine_sanity_downgrade_by_reason = {'meta_section': 3, 'numbered_check': 2}`
- `fsm_segment_count = 1`
- `fsm_segment_reclassified_count = 0`
- `fsm_segment_updated_node_count = 0`
- `fsm_segment_skipped_count = 1`
- `fsm_segment_skip_reasons = {'no_parent': 0, 'single_node': 0, 'empty_targets': 0, 'over_limit': 1, 'llm_error': 0, 'invalid_response': 0}`

对应 artifact：

- [node_labels.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.json)
- [node_labels.meta.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.meta.json)

artifact 检查结果：

- `prompt_version = v1.4-standalone-fsm-segment-reclassification`
- `model_name = gpt-5.4`
- `classified_state_machine_count = 1`

### 6.2 extract

- `success = True`
- `duration = 207.65s`
- `nodes = 49`
- `success_count = 32`
- `failure_count = 0`
- `empty_fsm_return_count = 0`
- `message_count = 7`
- `state_machine_count = 1`
- `state_machine_context_augmented_count = 1`

对应 artifact：

- [extract_results.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/extract_results.json)

artifact 检查结果：

- `extract_state_machine_count = 1`
- `empty_fsm_return_count = 0`

### 6.3 merge

- `success = True`
- `duration = 0.03s`
- `message_count = 7`
- `message_ir_count = 8`
- `ready_message_ir_count = 5`
- `state_machine_count = 1`
- `llm_refine_triggered_count = 0`
- `raw_branch_ratio_before = 0.0`
- `raw_branch_ratio_after = 0.0`
- `warnings = []`

对应 artifact：

- [protocol_schema.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/protocol_schema.json)
- [message_ir.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/message_ir.json)

### 6.4 codegen

- `success = True`
- `duration = 0.03s`
- `typed_action_count = 0`
- `generated_action_count = 0`
- `degraded_action_count = 0`
- `action_codegen_ratio = 0.0`

### 6.5 verify

- `success = True`
- `duration = 1.81s`
- `syntax_ok = True`
- `test_roundtrip_stub = True`
- `test_roundtrip_runtime = True`

对应 artifact：

- [verify_report.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/verify_report.json)

### 6.6 BFD 节点结论

本次真实 API full-chain 结果显示：

- `classified_state_machine_count = 1`
- `extract_state_machine_count = 1`
- `merge_state_machine_count = 1`
- `verify = True`
- 未出现新的 extractor validation error

说明 Step 2c 没有破坏此前已恢复的 BFD 结果。

## 7. 本次实验节点结论

此次真实 API 验证节点可以作为 Step 2c 的正式 checkpoint，结论如下：

1. TCP 已从 baseline 的大量 fragmented FSM 收敛为单一真实 standalone FSM
2. TCP 在 full-chain 下保持：
   - `merge_state_machine_count = 1`
   - `raw_branch_ratio = 0.3`
   - `verify = True`
3. BFD 全链路无回归，仍稳定保持单一真实 FSM
4. Step 2c 的核心收益已经在真实 API 下被确认，不再只是离线测试结论
