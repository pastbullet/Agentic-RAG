# RFC793 TCP 全链路实验记录（2026-04-04）

## 1. 实验目标

验证当前主线是否已经能够将 `rfc793-TCP.pdf` 从原始文档稳定推进到：

`classify -> extract -> merge -> IR -> codegen -> verify`

并评估当前产物是否可以视为 TCP 第一阶段 baseline。

## 2. 实验配置

- 文档：`rfc793-TCP.pdf`
- 模型：`gpt-5.4`
- 运行命令：

```bash
python run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --model gpt-5.4 \
  --stages classify,extract,merge,codegen,verify \
  --show-message-irs
```

## 3. 本次运行结果

### 3.1 Stage 结果

- `classify`：成功，`nodes=51`
- `extract`：成功，`success_count=34`，`failure_count=0`
- `merge`：成功
- `codegen`：成功，无 warning
- `verify`：成功，`syntax_ok=True`

### 3.2 合并后产物规模

- `messages=1`
- `message_irs=1`
- `ready_message_ir_count=1`
- `state_machines=19`
- `fsm_irs=19`
- `state_contexts=1`
- `procedures=5`
- `timers=1`
- `errors=2`

### 3.3 MessageIR 结果

- 当前唯一 ready 的 MessageIR 为 `tcp_header`
- `layout_kind=composite`
- 固定头最小长度为 `160 bits`
- option 区域被建模为 `options_tail`
- 诊断为 `option_list_fallback_enabled`

## 4. 当前阶段判断

### 4.1 可以确认已跑通的部分

本次结果可以确认：

1. TCP 文档已经可以稳定完成 `classify -> extract -> merge -> codegen -> verify` 全链路。
2. 当前系统已经能够产出：
   - `ProtocolSchema`
   - `MessageIR`
   - `FSMIR`
   - `StateContextIR`
   - `AlignmentReport`
   - 可编译检查通过的生成代码
3. 因此，**TCP 第一阶段 pipeline 已经跑通**。

### 4.2 这个结论的边界

上述“跑通”指的是：

- 文档到 IR 主线跑通
- IR 到代码骨架跑通
- 代码能通过当前 verify

但这**不等于**：

- 已获得语义接近完整的 TCP 实现
- 已完成工业级 TCP 行为复现
- 已经可以直接与真实内核 TCP 栈做强对标

当前更准确的定位应为：

> 已完成 RFC793 的第一阶段结构化抽取与代码骨架生成，形成可审计、可验证的 TCP baseline。

## 5. 目前做得比较好的部分

### 5.1 TCP Header 主干较扎实

当前 `MessageIR` 与生成代码已经较完整地覆盖了 TCP 固定头字段：

- Source Port / Destination Port
- Sequence Number / Acknowledgment Number
- Data Offset / Reserved / URG / ACK / PSH / RST / SYN / FIN
- Window / Checksum / Urgent Pointer

并已完成：

- bitfield 打包
- `data_offset >= 5` 约束
- pack / unpack / validate 生成

### 5.2 Options 已进入结构化建模

当前 option 区域不是简单的原始字节，而是：

- `options_tail`
- `option_list`
- `EOL / NOP / MSS / WINDOW_SCALE`
- `opaque_remainder` fallback

这说明系统已经具备“结构化 option tail”能力。

### 5.3 FSM 已形成分解式结构

当前 TCP 被分解为多个子 FSM，例如：

- Connection State Machine
- OPEN / SEND / RECEIVE / CLOSE / ABORT
- segment arrival checks
- ACK field processing
- FIN-bit processing
- Timeout Handling

这对后续论文叙事中的“分解式 spec coding”是有利的。

### 5.4 IR 与生成代码闭环已经成立

当前已经形成：

`FSMIR + StateContextIR + AlignmentReport + generated C + verify report`

这使系统具备了较强的可审计性。

## 6. 当前主要缺口

### 6.1 FSM 重名导致 codegen 覆盖

当前 `TCP Timeout Handling` 在 schema 中出现了两次，但 codegen 文件名相同，导致只落盘一份。

现象：

- schema 中 `state_machines=19`
- verify 中只统计到 `state_machines=18`
- 总头文件重复 include 同一个 timeout header

这说明当前 pipeline 虽通过，但有一个 FSM 被覆盖。

### 6.2 StateContextIR 还不是可靠的 TCP TCB 模型

当前 context 中存在较多噪声字段：

- `ack`
- `bit`
- `control`
- `timeout`
- timer `the`

而 RFC793 §3.2 中更关键的 TCB / SEG 变量尚未明确补齐，例如：

- `SND.WND`
- `SND.WL1`
- `SND.WL2`
- `ISS`
- `RCV.WND`
- `RCV.UP`
- `SEG.SEQ`
- `SEG.LEN`
- `SEG.WND`
- `SEG.UP`

这意味着当前 `StateContextIR` 更像“从 typed ref 中抽到的一批上下文字段”，而不是高质量 TCP runtime context。

### 6.3 当前 ready 的 MessageIR 更接近“TCP Header”而不是“完整 TCP Segment”

原始 schema 中仍有 `Data` 字段，但当前 ready MessageIR 和生成 struct 实际只覆盖到：

- 固定 TCP header
- options tail

尚未把 payload/data 建成一个显式的 variable tail。

### 6.4 option 建模是部分完整，不是严格完整

当前 option list 已建模：

- `EOL`
- `NOP`
- `MSS`
- `WINDOW_SCALE`

问题在于：

1. 这不是一个完整的 TCP option 集合
2. `WINDOW_SCALE` 并非 RFC793 原始 option
3. 仍然依赖 `opaque_remainder` fallback

因此当前可表述为“部分结构化、保守 fallback”，不宜表述为“完整 option 语义建模”。

### 6.5 checksum 尚未进入真实协议语义

当前生成代码只是读写 checksum 字段本身，还未体现 RFC793 所需的：

- pseudo-header
- 源 / 目的地址参与
- 协议号与 TCP Length 参与

因此当前代码还不具备真实的 TCP checksum 行为。

### 6.6 ctx-aware code 仍以骨架为主

当前生成代码里，大量 guard 与 action 仍是：

- `if (0)` 降级 guard
- 注释形式 action
- placeholder `emit_message`

真正落地的内容很少，主要是：

- 少量 timer slot 写入
- 个别 `ctx->bit` 风格的最小 guard

因此当前 codegen v1 仍应被视为“可编译的协议代码骨架”，而非可执行的 TCP 逻辑实现。

### 6.7 `coverage_ratio=1.0` 不能被误读

当前 `AlignmentReport` 显示：

- `error_count=0`
- `coverage_ratio=1.0`

但这只表示：

> 已经抽取出来的 typed refs 都能在 context 中找到对齐项

它**不表示**：

- TCP 关键语义已经全部结构化
- TCP 核心行为已经基本实现

## 7. 论文与汇报中建议采用的表述

建议当前阶段使用如下口径：

> 当前系统已在 RFC793/TCP 上完成第一阶段主线验证，实现了从协议文档到 MessageIR、FSMIR、StateContextIR、AlignmentReport 以及可验证代码骨架的稳定转换。该结果证明了主线 pipeline 的可行性，但当前产物仍主要停留在结构化抽取与代码骨架阶段，尚未形成语义接近完整的 TCP 实现。

对于 option 部分，建议使用：

> 当前 MessageIR 已能够结构化表示 TCP option 区域的边界、长度约束、终止符与部分高价值 option 项，并通过 opaque fallback 保持对未覆盖项的兼容性。

## 8. 下一步优先任务

### P0

1. 修复 `TCP Timeout Handling` 重名覆盖问题，保证 19 个 FSM 都独立落盘。
2. 重构 TCP `StateContextIR`，按 RFC793 §3.2 明确补齐关键 TCB / SEG 变量。

### P1

3. 优先把 ACK / SEQ / window 这条链做成真实 ctx-aware 代码：
   - `SND.UNA`
   - `SND.NXT`
   - `SEG.ACK`
   - 窗口更新
4. 明确 TCP baseline 口径：
   - 严格 RFC793
   - 或 RFC793 header + 扩展 option

### P2

5. 补 payload/data tail。
6. 补 pseudo-header checksum 语义。
7. 在实验指标中区分：
   - typed ref coverage
   - 真实代码落地比例

## 9. 当前结论

本次 TCP 实验结果可以作为：

- **第一阶段 pipeline 跑通证明**
- **TCP baseline 初版**
- **后续 state_context 与 ctx-aware codegen 强化的起点**

但当前还不适合被定义为：

- “完整 TCP 实现”
- “可直接与真实工业 TCP 栈强对标的最终产物”

## 10. 同日追加实验：Phase C v1 接入后的再次运行

### 10.1 追加实验背景

在完成 Phase C v1（LLM-assisted typed lowering refine path）后，对同一文档再次执行：

```bash
python3 run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --model gpt-5.4 \
  --stages classify,extract,merge,codegen,verify \
  --show-message-irs
```

本次运行的目的不是重新证明“主线能跑通”，而是观察：

1. Phase C v1 在 TCP 上是否能显著提升 typed guard / action 落地率；
2. 额外的 LLM refine 开销是否值得；
3. MessageIR / FSMIR / StateContextIR 的整体形态是否有明显变化。

### 10.2 Stage 结果

- `classify`：成功，`nodes=51`，`duration=0.00s`
- `extract`：成功，`nodes=51`，`duration=466.85s`
  - `success_count=34`
  - `failure_count=0`
  - `message_count=1`
  - `state_machine_count=25`
- `merge`：成功，`duration=3296.71s`
- `codegen`：成功，`duration=0.04s`
- `verify`：成功，`duration=3.83s`
  - `syntax_ok=True`
  - `test_roundtrip_stub=True`
  - `test_roundtrip_runtime=True`

说明：

- `classify duration=0.00s` 很可能意味着本次运行命中了缓存。这是基于阶段耗时的推断，而不是 pipeline 显式打印的字段。
- 本次真正的时间瓶颈不在 `extract`，而在 `merge`。

### 10.3 合并后产物规模

本次 merge 后得到：

- `state_machines=23`
- `fsm_irs=23`
- `messages=1`
- `message_irs=2`
- `ready_message_ir_count=1`
- `state_contexts=1`
- `procedures=5`
- `timers=1`
- `errors=2`

与本记录前半部分的 baseline run 相比，本次结果有两个显著变化：

1. `state_machines / fsm_irs` 从 `19` 增加到了 `23`
2. `message_irs` 从 `1` 增加到了 `2`

这说明当前重复提取 / 近似重复合并问题仍然存在，TCP 结果并未真正收口，反而在某些维度上更“散”了。

### 10.4 MessageIR 结果

本次 `message_ir.json` 中共有 2 个 MessageIR：

1. `tcp_header`
   - `status=ready`
   - `display_name=tcp_header_rfc793`
   - `layout_kind=composite`
   - `field_count=15`
   - `tails=['option_list']`
   - diagnostics:
     - `tail_width_not_opaque`
     - `tail_width_not_opaque`
     - `option_list_fallback_enabled`

2. `tcp_header_rfc`
   - `status=blocked`
   - `display_name=tcp_header_rfc793`
   - `layout_kind=bitfield_packed`
   - `field_count=18`
   - diagnostics:
     - `unsupported_field_storage` for `options`
     - `unsupported_field_storage` for `padding`
     - `unsupported_field_storage` for `data`

这说明：

- 当前系统仍然能稳定得到一个可用的 ready TCP header IR；
- 但同时保留了一条更“原始 RFC 展开视角”的竞争路径，该路径由于 `options/padding/data` 宽度与 storage type 不能稳定落下而 blocked；
- 因此，本次 MessageIR 结果的主结论仍然是：**TCP header 主干可用，但完整 segment/payload tail 仍未进入 ready IR。**

### 10.5 StateContextIR 结果

本次 `state_context_ir.json` 中主 context 为：

- `fields=15`
- `timers=9`
- `resources=3`
- `readiness=ready`

字段中已经出现了一批较关键的 TCP 变量：

- `connection_state`
- `initial_recv_seq`
- `initial_send_seq`
- `recv_next_seq`
- `recv_urgent_ptr`
- `recv_window`
- `segment_ack`
- `segment_precedence`
- `send_next_seq`
- `send_unacked`
- `send_urgent_ptr`
- `send_window`
- `send_window_update_ack`
- `send_window_update_seq`

这说明 Phase A/B context lane 已经不再只是极少量噪声 token，而是开始形成 TCP runtime context 的骨架。

但 timer 侧仍然明显偏脏，仍包含：

- `2`
- `msl`
- `time`
- `user`
- `wait`

因此更准确的判断是：

> 当前 StateContextIR 已形成“可用但不干净”的 TCP context baseline，字段主干比早期版本明显更好，但 timer canonicalization 与噪声过滤仍需继续收口。

### 10.6 Alignment 结果

本次 `alignment_report.json` 摘要如下：

- `fsm_count=23`
- `error_count=0`
- `warning_count=9`
- `aligned_fsm_count=23`
- `typed_ref_count=40`
- `resolved_ref_count=40`
- `coverage_ratio=1.0`

这仍然只能解释为：

> 已被 typed 的 40 个 refs 都能在当前 context 中找到对齐项。

它不能解释为：

- TCP 大多数分支已经被 typed 化；
- TCP 关键行为已经充分进入代码生成层。

### 10.7 FSM / Phase C v1 结果诊断

本次最重要的新增观察来自 `fsm_ir.json`：

- `fsm_count=23`
- `branch_total=188`
- `branch_raw=173`
- `ready=1`
- `degraded_ready=187`
- `blocked=0`
- `llm_refined_guard=0`
- `llm_refined_actions=0`

进一步按 Phase C v1 的 trigger 条件检查，满足 refine 条件的 FSM 有 `21` 个。

这意味着：

1. 本次 `merge` 的超长耗时，主要不是 merge 本地逻辑本身，而是 Phase C v1 的 branch-level LLM refine；
2. refine 的触发范围非常广；
3. 但最终被 acceptance gate 接受的 typed guard / action 为 `0`。

也就是说，本次 TCP run 的真实情况不是：

> Phase C 很慢，但有效提升了 typed 化率。

而是：

> Phase C 很慢，但本次在 TCP 上几乎没有带来可接受的 typed lowering 收益。

这是本次追加实验最重要的结论。

### 10.8 Codegen / Verify 结果解读

本次 codegen 仍然成功，verify 也通过。

但 codegen warning 明确提示：

- `Field options cannot infer a supported storage type from width 0`
- `Field padding cannot infer a supported storage type from width 0`
- `Field data cannot infer a supported storage type from width 0`

因此 codegen 的成功应继续被解释为：

- ready MessageIR 与 FSM skeleton 足以支撑代码骨架落盘；
- verify 通过表示代码在当前 stub/runtime 校验下可编译 / 可通过基础测试；
- 但并不表示 TCP payload/data/option 完整语义已经被纳入。

### 10.9 对本次追加实验的总体判断

本次运行最适合得出的结论是：

1. **TCP baseline 仍然成立**
   - 文档到 IR 主线仍然跑通
   - IR 到代码骨架仍然跑通
   - verify 仍然通过

2. **MessageIR 依然是当前最成熟的部分**
   - ready `tcp_header` 持续稳定
   - structured option tail 路线仍然成立

3. **StateContextIR 比前期更像 TCP context 了**
   - 核心 TCB / SEG 变量骨架更完整
   - 但 timer/noise 仍需清洗

4. **Phase C v1 在 TCP 上当前 ROI 很低**
   - 触发广
   - 耗时高
   - accepted typed refine 为 `0`

5. **因此当前不宜把“默认开启 Phase C refine 的 TCP run”当作推荐实验配置**
   - 它会显著增加 merge 时间
   - 但当前对 TCP typed 化率的收益不足

### 10.10 对后续工作的直接启示

基于本次追加实验，建议后续策略进一步收口为：

#### P0

1. 给 pipeline 增加阶段级 / 节点级 / refine 级进度日志，避免长时间运行但不可观测。
2. 给 Phase C 增加更强的早停 / gating 机制，避免在 TCP 上对大批 raw branch 做长时间低收益 refine。
3. 在 TCP 上默认将 Phase C 视为“可选增强”，而不是 baseline 默认配置。

#### P1

4. 优先做 TCP 定向 parser/canonical 收口，而不是继续扩大 refine 调用量。
5. 优先清洗 `StateContextIR` 中的 timer 噪声项。
6. 继续保留 `context_patch` 作为 HITL 修正路径。

#### P2

7. 再评估是否要对 `extract` 做有界并行，或对连续同类节点做 grouped extraction。
8. 继续把主要精力转回 FC 主案例，而不是在 TCP 上继续深挖行为层。

## 11. 更新后的结论

截至 2026-04-04 的两次 TCP 实验可以一起支持如下判断：

- **TCP 第一阶段 baseline 已成立**
- **MessageIR / codegen / verify 主线稳定**
- **StateContextIR 已具备继续修补的基础**
- **但 FSM 行为层 typed lowering 仍然不足**
- **Phase C v1 在 TCP 上当前尚未体现出可接受的性价比**

因此，TCP 现在最准确的定位仍然是：

> header-level + decomposed control-logic skeleton baseline  
> 可审计、可验证，但语义仍不完整

## 12. 2026-04-05 追加记录：Standalone FSM 收紧前的 TCP baseline

为后续 `standalone FSM` 收紧实验建立对照基线，基于当前 `data/out/rfc793-TCP/` 产物记录如下指标：

- `classified_state_machine_count = 25`
- `extract_state_machine_count = 25`
- `merge_state_machine_count = 23`
- `singleton_fsm_ratio = 0.0435`
- `avg_transitions_per_fsm = 8.1739`
- `raw_branch_ratio = 0.9202`

补充说明：

- 这里的 `singleton_fsm_ratio` 按 merge 后 `ProtocolSchema.state_machines` 中 `transition_count <= 1` 的 FSM 占比计算。
- 这里的 `raw_branch_ratio` 按 `fsm_ir.json` 中“仍保留 raw guard 或 raw actions 的 branch 占全部 branch 的比例”计算。
- 当前产物里尚无稳定的 `codegen_result.json` artifact，因此 `generated_action_count / degraded_action_count` 需在后续 fresh run 时从 codegen stage 输出记录。

这组 baseline 用于和以下改动后的 TCP 结果对比：

1. `StateMachineExtractor` standalone prompt 收紧
2. classifier 中 `state_machine` 判定收紧
3. 仅对 `state_machine` 节点补充 outline context 的 Step 2.5
