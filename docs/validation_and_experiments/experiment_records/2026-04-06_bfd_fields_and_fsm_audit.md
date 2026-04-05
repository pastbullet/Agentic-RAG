---
title: BFD 当前产物审计（字段与状态机）
date: 2026-04-06
tags:
  - validation
  - experiments
  - audit
  - bfd
  - fields
  - fsm
  - kiro
status: completed
---

# BFD 当前产物审计（字段与状态机）

## 1. 审计目的

本文档用于对 `rfc5880-BFD` 当前真实 API 产物做一次针对性审计，重点回答两个问题：

- 当前 BFD 的报文/字段提取是否符合 RFC5880 原文；
- 当前 BFD 的核心状态机提取是否符合 RFC5880 原文。

本文档不讨论本轮 Step 0 到 Step 2c 的方案演化过程，只审计当前落盘产物本身。

相关 artifact：

- [message_ir.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/message_ir.json)
- [extract_results.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/extract_results.json)
- [protocol_schema.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/protocol_schema.json)
- [node_labels.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.json)

原文内容来源：

- [content_1_20.json](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json)
- [content_21_40.json](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_21_40.json)
- [content_41_49.json](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_41_49.json)

## 2. 当前全链路状态

当前 BFD 实际全链路结果为：

- `classified_state_machine_count = 1`
- `extract_state_machine_count = 1`
- `merge_state_machine_count = 1`
- `verify = True`

因此，BFD 当前不是“链路坏掉但局部看起来对”，而是已经恢复到可以稳定跑通的状态。

## 3. 字段侧审计

### 3.1 审计口径

本文将 RFC5880 中明确给出线格式的 section 视为字段提取的 ground truth：

- `4.1 Generic BFD Control Packet Format`
- `4.2 Simple Password Authentication Section Format`
- `4.3 Keyed MD5 and Meticulous Keyed MD5 Authentication Section Format`
- `4.4 Keyed SHA1 and Meticulous Keyed SHA1 Authentication Section Format`

这四类 section 都给出了明确的 bit/byte 级格式，因此适合直接用于字段覆盖审计。

### 3.2 原文 ground truth

相关原文位置：

- `4.1` 控制包格式：[content_1_20.json#L45](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json#L45)
- `4.2` Simple Password：[content_1_20.json#L67](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json#L67)
- `4.3` Keyed MD5：[content_1_20.json#L73](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json#L73)
- `4.4` Keyed SHA1：[content_1_20.json#L79](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json#L79)

### 3.3 当前提取结果

当前 ready MessageIR 中，对应的四类结构均已存在：

- `bfd_control_packet`：[message_ir.json#L907](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/message_ir.json#L907)
- `bfd_auth_simple_password`：[message_ir.json#L657](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/message_ir.json#L657)
- `bfd_auth_keyed_md5`：[message_ir.json#L3](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/message_ir.json#L3)
- `bfd_auth_keyed_sha1`：[message_ir.json#L334](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/message_ir.json#L334)

其中：

- `bfd_control_packet` 抽出了 mandatory header 的 16 个核心字段；
- 还将 authentication tail 建模为一个 `message_family`，按 `auth_type` 分派到三类认证 section，见 [message_ir.json#L995](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/message_ir.json#L995)；
- 三种认证 section 都具有独立字段集合、长度约束与基础校验规则。

### 3.4 覆盖判断

按本文审计口径，BFD 当前字段覆盖情况如下：

| 审计对象 | RFC 是否给出明确线格式 | 当前是否形成 ready MessageIR | 结论 |
|----------|------------------------|-------------------------------|------|
| `4.1 Generic BFD Control Packet Format` | 是 | 是 | 覆盖 |
| `4.2 Simple Password Authentication Section Format` | 是 | 是 | 覆盖 |
| `4.3 Keyed MD5 / Meticulous Keyed MD5` | 是 | 是 | 覆盖 |
| `4.4 Keyed SHA1 / Meticulous Keyed SHA1` | 是 | 是 | 覆盖 |

因此，按“RFC 明确给出线格式的 section 数量”统计：

- 字段/帧结构覆盖率 = `4 / 4 = 100%`

### 3.5 一个需要说明的边界

`5 BFD Echo Packet Format` 当前没有形成 concrete ready MessageIR，只保留了 section summary，见 [extract_results.json#L325](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/extract_results.json#L325)。

但这不应直接视为字段漏提，因为 RFC 原文明确说明：

- Echo packet 的 payload 是 local matter；
- 只要求能够 demultiplex 回正确 session；
- 其具体内容不在本规范范围内。

对应原文见 [content_1_20.json#L85](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json#L85)。

因此，对 `5 BFD Echo Packet Format` 更合理的判断是：

- `N/A`，而不是“字段提取失败”。

### 3.6 字段侧结论

当前 BFD 的字段/线格式提取可以评价为：

- 显式线格式覆盖率：`100%`
- 字段级 fidelity：高
- 结构化质量：高

与 TCP 不同，BFD 当前字段侧没有明显的“模板污染超出原文”的问题，整体更干净。

## 4. 状态机侧审计

### 4.1 当前提取结果

当前只保留了一个 FSM：

- `0018 / 6.2 BFD State Machine`

见 [extract_results.json#L361](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/extract_results.json#L361)。

对应 label 也只有这一个 `state_machine`：

- [node_labels.json#L180](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.json#L180)

### 4.2 原文 ground truth

核心原文位置：

- `6.2 BFD State Machine` 文本说明：[content_1_20.json#L98](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json#L98)
- `6.2` 的图与补充说明：[content_1_20.json#L104](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_1_20.json#L104)
- 更细的接收状态转移规则在 `6.8.6`：[content_21_40.json#L81](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_21_40.json#L81) 和 [content_21_40.json#L87](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_21_40.json#L87)
- `AdminDown` 的进入/退出与管理控制在 `6.8.16`：[content_41_49.json#L9](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_41_49.json#L9)

### 4.3 状态集合审计

当前抽出的状态为：

- `Down`
- `Init`
- `Up`
- `AdminDown`

见 [extract_results.json#L369](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/extract_results.json#L369)。

RFC5880 `6.2` 的核心状态集合也是这四个状态，因此按状态集合统计：

- 状态覆盖率 = `4 / 4 = 100%`

这一点说明当前 BFD 的状态机提取至少没有出现“主状态漏召回”的问题。

### 4.4 转移审计口径

为了避免把旁路行为、诊断码副作用和后续 functional specifics 混成一团，本文只审计 BFD session 核心状态机的粗粒度主转移。

按 `6.2 + 6.8.16`，可将核心转移概括为以下 10 项：

1. `Down -> Init` on remote `Down`
2. `Down -> Up` on remote `Init`
3. `Init -> Up` on remote `Init|Up`
4. `Init -> Down` on detection timeout
5. `Init -> Down` on remote `Down|AdminDown`
6. `Up -> Down` on remote `Down`
7. `Up -> Down` on detection timeout
8. `Up -> Down` on remote `AdminDown`
9. `Any -> AdminDown` on local administrative disable
10. `AdminDown -> Down` on local enable

这一定义是本文为了审计方便所做的工程化抽象，不是 RFC 自带的编号。

### 4.5 当前转移结果

当前提取出的转移为：

- `Down -> Init` on `remote_state=Down`
- `Down -> Up` on `remote_state=Init`
- `Down -> Up` on `remote_state=AdminDown`
- `Init -> Up` on `remote_state=Init|Up`
- `Init -> Down` on `detection_time_expired`
- `Up -> Down` on `remote_state=Down`
- `Up -> Down` on `detection_time_expired`
- `Any -> AdminDown` on `local_administrative_down`
- `AdminDown -> Down` on `local_exit_admin_down`

见 [extract_results.json#L397](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/extract_results.json#L397)。

### 4.6 准确性判断

与上面的 10 条核心转移对照后，可得到：

**正确保留的转移**

- `1, 2, 3, 4, 6, 7, 9, 10`

**缺失的转移**

- `5. Init -> Down` on remote `Down|AdminDown`
- `8. Up -> Down` on remote `AdminDown`

**明显不正确的额外转移**

- `Down -> Up` on `remote_state=AdminDown`

这条边与原文不符。根据 `6.8.6` 的接收规则，当接收到 `AdminDown` 时，语义是转到 `Down`，而不是从 `Down` 直接升到 `Up`，见 [content_21_40.json#L93](/Users/zwy/毕设/Kiro/data/out/content/rfc5880-BFD/json/content_21_40.json#L93)。

### 4.7 覆盖判断

按本文的粗粒度审计口径：

- 核心状态覆盖率 = `4 / 4 = 100%`
- 核心转移覆盖率约为 `8 / 10 = 80%`

但需要同时强调：

- 当前转移精度不是满分；
- `AdminDown` 相关边存在缺边和误边；
- `LocalDiag = 3` 等副作用没有进入 FSM payload，而是仍保留在 procedure / functional specifics 语义中。

### 4.8 哪些内容没有进 FSM，其实是合理的

BFD 中以下内容当前没有被抽成核心 FSM：

- `6.4 The Echo Function and Asymmetry`
- `6.5 The Poll Sequence`
- `6.6 Demand Mode`
- `6.8.14 Enabling or Disabling Demand Mode`

对应当前分类分别为 `timer_rule` 或 `procedure_rule`，例如：

- `6.4`：[node_labels.json#L205](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.json#L205)
- `6.5`：[node_labels.json#L218](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.json#L218)
- `6.6`：[node_labels.json#L231](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.json#L231)
- `6.8.14`：[node_labels.json#L466](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/node_labels.json#L466)

这并不意味着“漏了状态机”，因为这些 section 在 RFC5880 中本来就主要描述：

- 模式切换规则
- 计时行为
- Poll/Final procedure
- Demand mode operational behavior

它们与 `6.2` 的核心 session FSM 有关，但不等价于“应单独抽成核心 standalone FSM”。

## 5. 结论

综合字段侧与状态机侧，当前 BFD 产物可以评价为：

### 5.1 字段侧

- 显式线格式覆盖率：`100%`
- 基本符合 RFC5880 原文
- 当前字段提取质量明显高于 TCP

### 5.2 状态机侧

- 核心状态覆盖率：`100%`
- 核心转移覆盖率：约 `80%`
- 主状态机已经成功保住
- 但 `AdminDown` 相关转移仍有局部语义误差

### 5.3 总体判断

如果用一句话概括：

> 当前 BFD 的报文/字段提取已经基本对齐 RFC5880；核心 session FSM 也已成功提取，但在 `AdminDown` 相关转移的精细语义上仍存在局部偏差，因此它是“整体可用、局部待精修”的状态，而不是“结构已坏”或“主状态机未抽到”的状态。

对于后续工作安排，这意味着：

- BFD 不再是当前主矛盾；
- 当前更值得优先处理的仍然是 TCP 上的召回问题；
- BFD 若继续优化，应聚焦于 FSM 转移细化，而不是重新做大规模 classify 收紧。
