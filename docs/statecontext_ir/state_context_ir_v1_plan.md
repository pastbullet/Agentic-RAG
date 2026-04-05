# StateContextIR v1 方案

## 1. 目标

`StateContextIR` 的目标不是实现完整 runtime，而是先解决当前 `FSM skeleton` 的核心缺口：

- 现在的 `FSM` 基本只有 `state + event -> next_state`
- 缺少统一的“操作对象”
- `guard / action / timer / resource` 现在大多只能停留在注释里

因此，`StateContextIR v1` 的定位是：

> **协议运行时状态的统一抽象层，用于承载 `FSM` 执行过程中跨消息、跨事件持续存在的状态、计时器和资源。**

它不是：

- `MessageIR` 的扩展
- `TCP TCB` 特例
- 完整 runtime 框架

它是：

- **`FSMIR` 的运行时状态模型**

---

## 2. 总体架构位置

建议后续主链路为：

```text
MessageIR       -> 定义报文结构
StateContextIR  -> 定义运行时状态
FSMIR           -> 定义状态转移、guard、action
Codegen         -> 生成 message codec + FSM skeleton + ctx skeleton
Verify          -> 验证 compile / transition / roundtrip
```

三者关系：

- `MessageIR`：字段在报文里是什么
- `StateContextIR`：字段在运行时上下文里是什么
- `FSMIR`：什么时候读 `msg.field`、什么时候读/写 `ctx.field`

---

## 3. 设计原则

### 原则 1：不绑定 TCP 术语

不要把 IR 直接写成：

- `snd_nxt`
- `rcv_nxt`
- `tcb`

而应使用更抽象的语义角色，例如：

- `send_next_seq`
- `recv_next_seq`
- `send_window`
- `recv_window`
- `state`

### 原则 2：先做最小可执行语义

v1 不做：

- 完整队列实现
- 完整 timer runtime
- 完整 socket / buffer / runtime integration

v1 只做：

- 字段
- timer 占位
- resource 占位
- `FSMIR` 可引用这些对象

### 原则 3：FSMIR 只引用它，不拥有它

`FSMIR` 不直接定义上下文字段，而是引用：

- `ctx.field`
- `ctx.timer`
- `ctx.resource`

### 原则 4：允许 `DEGRADED_READY`

就像 `MessageIR` 一样，`StateContextIR` 也不应该非黑即白。

有些上下文可以先达到“足够支撑最小 FSM codegen”，不必一步到位完整 runtime。

---

## 4. 核心对象设计

### 4.1 `StateContextIR`

```python
class StateContextIR:
    context_id: str
    name: str
    canonical_name: str

    scope: str
    # connection / session / association / transaction / global

    state_field: str | None

    fields: list[ContextFieldIR]
    timers: list[ContextTimerIR]
    resources: list[ContextResourceIR]

    invariants: list[ContextRuleIR]
    diagnostics: list[IRDiagnostic]

    readiness: str
    # READY / DEGRADED_READY / BLOCKED
```

字段说明：

- `scope`：上下文的生存范围
- `state_field`：哪个字段表示当前状态
- `fields`：持久状态字段
- `timers`：计时器抽象
- `resources`：队列、缓冲、映射等资源
- `invariants`：上下文不变量
- `readiness`：是否足够支撑当前 `FSM codegen`

### 4.2 `ContextFieldIR`

```python
class ContextFieldIR:
    field_id: str
    name: str
    canonical_name: str

    type_kind: str
    # enum / u32 / u16 / bool / bytes / counter / timestamp / opaque

    width_bits: int | None

    semantic_role: str | None
    # state / send_next_seq / recv_next_seq / send_window / recv_window / retry_counter / ...

    initial_value_kind: str | None
    # const / unknown / derived / runtime_supplied

    initial_value_expr: str | None

    read_only: bool = False
    optional: bool = False

    read_by: list[str]
    written_by: list[str]

    diagnostics: list[str]
```

为什么 `semantic_role` 很关键：

这是避免 TCP 特例化的核心。

例如 TCP 可以映射为：

- `send_next_seq`
- `send_unacked`
- `recv_next_seq`
- `send_window`
- `recv_window`

BFD 可以映射为：

- `desired_min_tx_interval`
- `required_min_rx_interval`
- `detect_mult`

### 4.3 `ContextTimerIR`

```python
class ContextTimerIR:
    timer_id: str
    name: str
    canonical_name: str

    semantic_role: str | None
    # retransmission / user_timeout / keepalive / hold_timer / idle_timer

    duration_source_kind: str | None
    # const / ctx_field / msg_field / derived

    duration_expr: str | None

    triggers_event: str | None
    start_actions: list[str]
    cancel_actions: list[str]

    diagnostics: list[str]
```

v1 中它不是完整 timer runtime，而是告诉 `FSMIR`：

- 有这个 timer
- 它大概干什么
- 哪些 action 会 start / cancel
- timeout 触发什么 event

### 4.4 `ContextResourceIR`

```python
class ContextResourceIR:
    resource_id: str
    name: str
    canonical_name: str

    kind: str
    # queue / buffer / map / set / opaque_handle

    semantic_role: str | None
    # send_queue / retransmission_queue / receive_queue / reassembly_buffer

    element_kind: str | None
    # message_ref / bytes / segment_ref / opaque

    diagnostics: list[str]
```

为什么要有 `resource`：

很多行为注释本质上都在操作资源，例如：

- flush queue
- reassemble queued segments
- delete queued sends / receives

如果没有 `ContextResourceIR`，这些永远只能停在注释。

### 4.5 `ContextRuleIR`

```python
class ContextRuleIR:
    rule_id: str
    kind: str
    # invariant / derivation / precondition / postcondition

    expression: str
    depends_on_fields: list[str]

    diagnostics: list[str]
```

v1 用途：

先支持少量 invariant，例如：

- `send_next_seq >= send_unacked`
- `state` 必须是 enum 成员
- 某 timer 依赖某字段存在

---

## 5. readiness 设计

建议与 `MessageIR` 一样，采用三态：

### `READY`

- 核心状态字段明确
- 足够支撑 guard / action / codegen

### `DEGRADED_READY`

- 核心状态字段有了
- `timers / resources` 仍偏 opaque
- 仍可进入最小 `FSM codegen`

### `BLOCKED`

- 连状态字段或核心上下文字段都不清楚
- 无法支撑 `FSM codegen`

---

## 6. 和 `MessageIR` / `FSMIR` 的关系

### 6.1 `MessageIR` 和 `StateContextIR` 不合并

`MessageIR` 负责：

- wire format
- frame fields
- tail / layout / rules

`StateContextIR` 负责：

- 运行时状态
- queues / timers / resources
- 跨消息持续变量

### 6.2 `FSMIR` 引用两者

`FSMIR` 后续应支持：

#### guard

```text
msg.ack_number == ctx.send_next_seq
ctx.connection_state == SYN_SENT
msg.syn_flag == 1
```

#### action

```text
ctx.recv_next_seq = msg.sequence_number + 1
ctx.connection_state = ESTABLISHED
start_timer(retransmission_timer)
clear_resource(send_queue)
emit tcp_header { ... }
```

---

## 7. v1 最小可用集合

不要一上来做大。建议 v1 只支持这三类最小对象：

### 字段

- `state`
- `send_next_seq`
- `recv_next_seq`
- `send_window`
- `recv_window`

### timer

- `retransmission_timer`

### resource

- `send_queue`

这已经足够把很多 TCP / BFD 的 `FSM skeleton` 从：

- 注释动作

推进到：

- 可生成的 action skeleton

---

## 8. TCP 映射示例（作为实例，不是特例）

```text
StateContextIR(
  context_id="tcp_connection",
  scope="connection",
  state_field="connection_state",

  fields=[
    connection_state (role=state),
    send_next_seq (role=send_next_seq),
    send_unacked (role=send_unacked),
    recv_next_seq (role=recv_next_seq),
    send_window (role=send_window),
    recv_window (role=recv_window),
  ],

  timers=[
    retransmission_timer (role=retransmission),
    user_timeout (role=user_timeout),
  ],

  resources=[
    send_queue (role=send_queue),
    retransmission_queue (role=retransmission_queue),
    receive_queue (role=receive_queue),
  ]
)
```

重点：

- 这是 `StateContextIR` 的一个实例化
- 不是系统只支持 TCP

---

## 9. 代码生成目标（v1）

`StateContextIR v1` 不需要生成完整 runtime 框架，先生成：

### 9.1 context struct

例如：

```c
typedef struct {
    tcp_connection_state connection_state;
    uint32_t send_next_seq;
    uint32_t recv_next_seq;
    uint32_t send_window;
    uint32_t recv_window;
} tcp_connection_ctx;
```

### 9.2 timer / resource placeholder

例如：

```c
typedef struct {
    bool retransmission_timer_active;
} tcp_connection_timers;
```

或者更简单，先只生成注释 / slot。

### 9.3 action / helper skeleton 可引用 `ctx`

例如：

```c
int tcp_on_recv_syn_ack(tcp_connection_ctx* ctx, const tcp_tcp_header* msg, tcp_tcp_header* out);
```

---

## 10. 最合理的落地顺序

### 第一步

先建 `StateContextIR` 模型层。

### 第二步

让 `FSMIR` 支持引用 `ctx.field`。

### 第三步

让 `FSMIR action` 能生成：

- `ctx.xxx = ...`
- `emit message using ctx.xxx`

### 第四步

再逐步扩 `timers / resources` 的 runtime 含义。

---

# 给 Codex 的实现 Prompt

你现在在当前仓库的 `thesis` 分支上继续开发新的运行时状态主线：

`StateContextIR v1`

## 任务目标

实现最小可用的 `StateContextIR v1`，并把它接到当前 `FSM` 主线上，用于替代当前 `FSM skeleton` 中大量只能停留在注释里的上下文依赖。

本轮重点不是做完整 runtime，而是：

1. 新增 `StateContextIR` 相关模型
2. 让 `FSMIR` 能引用 `ctx.field / ctx.timer / ctx.resource`
3. 生成最小的 `context struct` 和 `FSM skeleton`
4. 为后续 `Agentic RAG` 的代码补全留出稳定 slot

## 严格范围

不要扩到：

- 完整 runtime
- socket / buffer integration
- 完整 timer runtime
- 新协议大范围支持
- checksum / payload 细节
- message 侧大改

## 必须遵守

1. 不要把 `StateContextIR` 做成 TCP 特例
2. 不要把它并入 `MessageIR`
3. 不要让 `FSMIR` 拥有上下文字段定义，`FSMIR` 只引用 `StateContextIR`
4. 保持最小闭环，不做大爆炸式重构
5. 直接改代码、补测试、跑验证，不要只写设计说明

## 建议新增对象

至少新增：

- `StateContextIR`
- `ContextFieldIR`
- `ContextTimerIR`
- `ContextResourceIR`
- `ContextRuleIR`

支持三态：

- `READY`
- `DEGRADED_READY`
- `BLOCKED`

## v1 最小字段集合

至少支持：

- `state`
- `send_next_seq`
- `recv_next_seq`
- `send_window`
- `recv_window`

最小 timer：

- `retransmission_timer`

最小 resource：

- `send_queue`

## 与 FSM 的关系

后续 guard / action 至少能够引用：

- `ctx.connection_state`
- `ctx.send_next_seq`
- `ctx.recv_next_seq`
- `msg.xxx`

并允许生成最小 action skeleton，例如：

- `ctx.xxx = ...`
- `start_timer(...)`
- `clear_resource(...)`

## codegen 目标

至少生成：

1. context struct
2. timer / resource placeholder
3. 可引用 `ctx` 的 FSM skeleton

## 测试要求

至少新增：

1. `StateContextIR` 模型测试
2. `FSMIR -> ctx 引用` 测试
3. 最小 codegen 测试
4. 旧 message 主线不被打坏的回归测试

## 交付要求

完成后输出：

1. 审计结论
2. 修改了哪些文件
3. 新增了哪些文件
4. 关键设计决策
5. 哪些测试通过
6. 当前有哪些明确未完成项

现在先阅读代码并直接开始实现。
