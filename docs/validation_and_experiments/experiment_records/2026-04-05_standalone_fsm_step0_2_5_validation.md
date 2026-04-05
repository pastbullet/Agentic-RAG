# Standalone FSM 收紧验证记录（2026-04-05）

## 1. 实验目的

验证 `Step 0-2.5` 的 standalone FSM 收紧方案是否达到预期：

- 在 extract 上游拦住明显的伪 FSM
- 修复 BFD 上的 `ProtocolStateMachine` 校验错误回归
- 观察 TCP 相对 2026-04-04 baseline 的分类 / 抽取规模变化

本次记录对应的修复批次不包含：

- Step 3 section-window batching
- Phase C refine 并发化
- `ExtractionRecord` / merge schema 结构调整

## 2. 代码范围

本次涉及的核心文件：

- [src/extract/extractors/state_machine.py](/Users/zwy/毕设/Kiro/src/extract/extractors/state_machine.py)
- [src/extract/classifier.py](/Users/zwy/毕设/Kiro/src/extract/classifier.py)
- [src/extract/pipeline.py](/Users/zwy/毕设/Kiro/src/extract/pipeline.py)
- [run_extract_pipeline.py](/Users/zwy/毕设/Kiro/run_extract_pipeline.py)

对应新增/更新测试：

- [tests/extract/test_extractors.py](/Users/zwy/毕设/Kiro/tests/extract/test_extractors.py)
- [tests/extract/test_classifier.py](/Users/zwy/毕设/Kiro/tests/extract/test_classifier.py)
- [tests/extract/test_pipeline.py](/Users/zwy/毕设/Kiro/tests/extract/test_pipeline.py)
- [tests/test_run_extract_pipeline.py](/Users/zwy/毕设/Kiro/tests/test_run_extract_pipeline.py)

## 3. 改动摘要

### 3.1 StateMachineExtractor

- prompt 从“抽取任意 FSM”改为“只抽取 standalone FSM”
- 非 standalone 允许返回空 FSM
- 增加 outline context 约束，禁止从 sibling / heading 凭空补造状态
- 增加 payload normalization，兼容 LLM 常见别名字段：
  - state: `name | label | id | title`
  - transition: `from_state | from | source`，`to_state | to | target`
  - event: `event | trigger | on | when | input | label`
  - actions: `actions | action | effects | effect`
- 对明显非 standalone 的 payload 做 extractor 侧二次收敛，回退为空 FSM

### 3.2 Classifier

- `PROMPT_VERSION` 升级为 `v1.2-standalone-fsm-sanity`
- 去掉旧的 “in state X, on event Y -> prefer state_machine” 倾向
- classifier prompt 改为：
  - 只在“完整、standalone、多状态、多转移”的情况下判 `state_machine`
  - summary 只作辅助手段，不作为 `state_machine` 的主证据
- 增加轻量 sanity filter：
  - 标题命中 `overview / design / security considerations / references / state variables / non-normative` 时，降级为 `general_description`
  - 标题命中 `reception of / processing / administrative control / enabling or disabling / demultiplexing` 等局部过程段时，降级为 `procedure_rule`

### 3.3 Pipeline / CLI

- 仅对 `state_machine` 节点前缀拼接 outline context：
  - `section_path`
  - `parent_heading`
  - `sibling_titles`
- extract 阶段新增最小观测指标：
  - `empty_fsm_return_count`
  - `state_machine_context_augmented_count`
- CLI 输出新增这些指标，便于回归对比

## 4. 测试执行

### 4.1 定向测试

执行命令：

```bash
pytest tests/extract/test_extractors.py tests/extract/test_classifier.py
pytest tests/extract/test_pipeline.py tests/test_run_extract_pipeline.py
python3 -m py_compile src/extract/extractors/state_machine.py src/extract/classifier.py
python3 -m py_compile src/extract/pipeline.py run_extract_pipeline.py
```

结果：

- `tests/extract/test_extractors.py` + `tests/extract/test_classifier.py`：`19 passed`
- `tests/extract/test_pipeline.py` + `tests/test_run_extract_pipeline.py`：`21 passed`
- 相关 Python 文件 `py_compile` 通过

本批新增覆盖了两个关键风险点：

- alias-shaped FSM payload 不再因 `id/label/from/to` 直接校验失败
- 明显标题型误判会被 classifier sanity filter 降级

## 5. BFD 回归结果

### 5.1 修复前观察到的回归

在 2026-04-05 的修复前 BFD 实跑中，曾出现以下问题：

- 多个节点被误标为 `state_machine`
- extractor 收到大量 alias-shaped payload，触发 `ProtocolStateMachine` 校验错误
- extract 阶段统计为：
  - `empty_fsm_return_count = 14`
  - `state_machine_count = 14`
- merge 阶段统计为：
  - `state_machine_count = 0`

也就是说，BFD 在修复前已经出现“分类过宽 + extractor 过脆”叠加导致的真实回归。

### 5.2 修复后 fresh run

运行命令：

```bash
python3 run_extract_pipeline.py \
  --doc rfc5880-BFD.pdf \
  --stages classify,extract,merge,codegen,verify \
  --show-message-irs
```

运行环境：

- 模型：`gpt-5.4`
- classifier prompt version：`v1.2-standalone-fsm-sanity`

#### 5.2.1 classify

`node_labels.json` 更新时间：`2026-04-05T01:20:46`

标签分布：

- `general_description = 17`
- `procedure_rule = 14`
- `message_format = 8`
- `state_machine = 1`
- `timer_rule = 9`

唯一保留下来的 `state_machine` 节点为：

- `0018` `6.2 BFD State Machine`

#### 5.2.2 extract

`extract_results.json` 更新时间：`2026-04-05T01:24:04`

CLI 输出：

- `success_count = 32`
- `failure_count = 0`
- `empty_fsm_return_count = 0`
- `message_count = 8`
- `state_machine_count = 1`
- `state_machine_context_augmented_count = 1`

说明：

- 这次没有再出现成串的 `ProtocolStateMachine` 校验错误
- extractor normalization 已经能稳定接住真实 FSM 输出

#### 5.2.3 merge / codegen / verify

CLI 输出：

- `merge.state_machine_count = 1`
- `merge.message_count = 7`
- `merge.message_ir_count = 8`
- `merge.ready_message_ir_count = 5`
- `merge.raw_branch_ratio_before = 0.0`
- `merge.raw_branch_ratio_after = 0.0`
- `codegen.success = True`
- `verify.success = True`
- `verify.syntax_ok = True`

`merge_report.json` 中的统计：

- `pre_merge_counts.state_machine = 1`
- `post_merge_counts.state_machine = 1`
- `near_miss_summary = {"sm_count": 0, "msg_count": 6}`

#### 5.2.4 BFD 结论

本批修复已经把 BFD 从“merge 后 0 个 FSM 的回归状态”恢复到：

- classify 仅保留 1 个真正的 standalone FSM
- extract 不再因 alias schema 报错
- merge 后保留 1 个 FSM
- codegen / verify 全链路通过

结论：

> BFD 回归已修复，本批改动在 BFD 上通过验证。

## 6. TCP 对比结果

### 6.1 对照 baseline

基线来自 [2026-04-04_rfc793_tcp_full_pipeline_record.md](/Users/zwy/毕设/Kiro/docs/validation_and_experiments/experiment_records/2026-04-04_rfc793_tcp_full_pipeline_record.md) 和对应运行记录：

- `classified_state_machine_count = 25`
- `extract_state_machine_count = 25`
- `merge_state_machine_count = 23`
- `singleton_fsm_ratio = 0.0435`
- `avg_transitions_per_fsm = 8.1739`
- `raw_branch_ratio = 0.9202`

### 6.2 本次 fresh run 范围

运行命令：

```bash
python3 run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --stages classify,extract,merge,codegen,verify \
  --show-message-irs
```

说明：

- classify / extract 的新产物已成功落盘
- merge 进入长时间 refine 后被人工中断
- 因此本次 **只有 classify / extract 可以作为新的有效结果**
- 现有 `protocol_schema.json` / `message_ir.json` / `verify_report.json` 仍是 2026-04-04 的旧产物，不能拿来当作本次新结果

### 6.3 本次 classify / extract 新结果

#### 6.3.1 classify

`node_labels.json` 更新时间：`2026-04-05T01:29:50`

标签分布：

- `general_description = 24`
- `message_format = 3`
- `procedure_rule = 9`
- `state_machine = 12`
- `error_handling = 1`
- `timer_rule = 2`

相对 baseline：

- `classified_state_machine_count: 25 -> 12`
- 绝对减少 `13`
- 相对下降约 `52%`

#### 6.3.2 extract

`extract_results.json` 更新时间：`2026-04-05T01:34:16`

从新 `extract_results.json` 统计得到：

- `extract_state_machine_count = 12`
- `empty_fsm_return_count = 3`

相对 baseline：

- `extract_state_machine_count: 25 -> 12`
- 绝对减少 `13`
- 相对下降约 `52%`

本次 extract 后仍为非空 FSM 的标题主要有：

- `3.5 Closing a Connection`
- `3.9.2 SEND Call`
- `3.9.3 RECEIVE Call`
- `3.9.4 CLOSE Call`
- `3.9.5 ABORT Call`
- `1.3.3 third check the security and precedence`
- `1.4.2 second check the RST bit,`
- `1.4.3 third check security and precedence`
- `1.4.8 eighth, check the FIN bit,`

被 extractor 主动判空的 `state_machine` 节点包括：

- `3.9.6 STATUS Call`
- `1.4.4 fourth, check the SYN bit,`
- `1.4.6 sixth, check the URG bit,`

### 6.4 TCP 当前判断

本批改动对 TCP 已经产生明显收益：

- `classified_state_machine_count` 明显下降
- `extract_state_machine_count` 明显下降
- `empty_fsm_return_count > 0`，说明 extractor 已开始主动拦截伪 FSM

但当前 TCP 仍未完全达到“FSM 数量压到个位数”的预期，残留误判主要集中在两类：

- `SEND/RECEIVE/CLOSE/ABORT` 这类 call 小节
- `check the RST/SYN/FIN/... bit` 这类局部检查段

因此，本批对 TCP 的结论应表述为：

> Step 0-2.5 对 TCP 已确认有效，但尚未收敛完成；需要下一批继续处理 residual false positives，之后再重新跑完整 merge/codegen/verify 对比 `merge_state_machine_count` 与 `raw_branch_ratio`。

## 7. 总结

### 7.1 已确认完成的目标

- standalone FSM 边界已在 classifier 和 extractor 两侧同时收紧
- extractor 已修复对 alias-shaped FSM payload 的脆弱性
- BFD 的真实回归已恢复，并重新通过全链路验证
- TCP 的 classify / extract 指标已明显优于 baseline

### 7.2 仍未完成的目标

- TCP 的 fresh `merge_state_machine_count`
- TCP 的 fresh `raw_branch_ratio`
- TCP 的 fresh `generated_action_count / degraded_action_count`

这些指标需要在下一次完整 TCP run 中补齐。

## 8. 建议的下一步

建议直接进入下一小批收敛，而不是回滚本批：

1. 继续收紧 classifier，对 `SEND/RECEIVE/CLOSE/ABORT` 与 `check the ... bit` 这类标题做更强的 procedure-rule 约束。
2. 若仅靠 prompt/heuristic 仍不够，再考虑进入 Step 3 batching。
3. 完成后重新跑 `rfc793-TCP.pdf` 的 `classify,extract,merge,codegen,verify`，补齐最终对比指标。
