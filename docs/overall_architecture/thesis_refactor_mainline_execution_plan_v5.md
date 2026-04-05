# thesis 分支主线执行方案 V5（对标 APG 的工程完善版）

> V5 是在 V4 基础上，因发现 APG (AAAI 2026, CCF-A) 这一直接竞争/对标工作后的战略性重构。
>
> **核心转变：从"首次提出 RFC2Code"转变为"在 APG 证明可行性的基础上，面向工业级交换机协议软件场景的工程完善"。**
>
> 这一转变精准命中专硕评审逻辑：不要求算法创新，要求系统完整性和工程深度。

---

## 1. V5 的战略背景

### 1.1 APG 带来的里程碑变化

APG（LLMs Unleashed: Generating Protocol Code from RFC Specifications，AAAI 2026）是第一个正式发表的 LLM 端到端 RFC→Code 系统。

**APG 对本毕设的影响：**

| 影响 | 说明 |
|------|------|
| 赛道合法性确立 | CCF-A 接收了 RFC2Code task，不需要再论证"问题值得研究" |
| 现成 CCF-A baseline | 有正式发表的工作可对标，实验含金量提升 |
| 方法论空白暴露 | APG 用 prompt 一把梭，没有 IR、没有分层验证、没有确定性——Kiro 的全部优势 |
| 专硕定位精准化 | 从"首次提出"（偏学硕）转为"工程完善"（精准命中专硕） |

### 1.2 APG 的核心弱点（V5 的机会窗口）

从 APG 源码分析发现的关键弱点：

1. **"全自动"有水分**：prompt 中硬编码大量领域知识（SOCK_RAW、字节序规则、FIN+ACK 优先规则等）
2. **没有 IR 中间层**：Implement Guidebook 只是松散 JSON，每次 LLM 生成结果不确定
3. **验证只有语法检查**：`g++ -fsyntax-only`，不运行代码，不做 roundtrip
4. **平台耦合**：代码绑死 Linux raw socket，不可移植
5. **人工预写框架**：无状态协议的 sender.h/receiver.h/checksum.h 约 600 行人工代码

### 1.3 V5 的核心定位

```text
APG 证明了: "LLM 能直接生成协议代码"（可行性）

V5 要证明的是: "通过 IR 中间层 + 分层验证 + 两阶段平台解耦，
一个无先验知识的系统可以仅从协议标准文档出发，
达到工业级协议软件开发所要求的
可审计性、确定性、可修正性和可移植性"（工程完善）
```

### 1.4 核心差异化亮点：Zero-Prior-Knowledge

Kiro 是一个**无先验知识（zero-prior-knowledge）**的协议代码生成系统：

- 系统本身**不包含任何协议特定知识**，所有协议语义均从标准文档中自动提取
- 不在 prompt 中硬编码领域规则，不预写协议框架代码
- 换协议 = 换 PDF，pipeline 代码不改

**与 APG 的本质区别：**

| 维度 | APG | Kiro |
|------|-----|------|
| 协议知识来源 | 人工注入 prompt + 预写框架代码 | 全部从 PDF 自动提取 |
| 换协议代价 | 重写 prompt 中的领域知识 | 换 PDF，pipeline 不改 |
| prompt 中的硬编码 | SOCK_RAW、字节序规则、FIN+ACK 优先等 | 无协议特定内容 |
| 预写代码 | sender.h/receiver.h/checksum.h ~600 行 | 无 |
| 对 LLM 预训练知识的依赖 | 高（依赖 LLM "认识"该协议） | 低（IR 从文档提取，codegen 确定性） |

**最强证据：FC 协议**

FC（Fibre Channel）在 LLM 训练数据中几乎没有代码样本。如果 Kiro 能在 FC 上跑通全链路，就直接证明：
- 系统不依赖 LLM 的预训练协议知识
- 协议语义完全来自文档提取 + IR 结构化
- 这是 APG 无法复现的——APG 在 FC 上必然失败，因为 LLM 不"认识"FC

---

## 2. V5 vs V4 变更总览

### 2.1 保留的部分

- 三层 IR 目标：MessageIR / BehaviorIR-lite / StateContextIR
- 协议角色：BFD 基线 + FC 主案例 + TCP 结构挑战
- consumer-driven 原则
- 先打通再抽象的执行思路

### 2.2 关键变更

| 维度 | V4 | V5 |
|------|-----|-----|
| 战略定位 | BFD 基线 + FC 主案例 | **对标 APG 的工程完善方案** |
| 代码生成 | 单阶段（IR → C 库代码） | **两阶段**（Stage 1 平台无关 + Stage 2 平台适配） |
| 互操作验证 | 无 | **pcap 字节级验证 + Linux 用户态实验** |
| 实验设计 | 模糊（V4 未正式定义） | **6 个实验，直接对标 APG** |
| baseline 对比 | "强烈建议做" | **必做，APG 是天然 baseline** |
| 论文叙事 | 未明确 | **"在 APG 基础上工程完善"** |
| patch lane | 后置 | 做最小版（展示可修正性，对标 APG 的不可修正） |
| 简单协议泛化 | 未提及 | 视时间加入 ICMP/ARP |
| Directed 模式 | 未提及 | 视时间加入，作为"辅助"亮点 |

---

## 3. 系统架构（V5 版）

### 3.1 两阶段代码生成架构

V5 的核心架构创新是**两阶段解耦**，直接解决 APG 的平台耦合问题：

```text
                      Stage 1（平台无关，确定性）
                      ┌───────────────────────────────────────────┐
    协议 PDF ──→      │ RAG 提取 → 多层 IR → 确定性 codegen        │
                      │                                           │
                      │ 产物:                                      │
                      │   msg_*.h/c   （struct + pack/unpack）     │
                      │   fsm_*.h/c   （dispatcher skeleton）      │
                      │   test_roundtrip.c （自验证）               │
                      └──────────────────┬────────────────────────┘
                                         │
                      Stage 2（平台特定，Agent 驱动）
                      ┌──────────────────┴────────────────────────┐
                      │  Prompt Generation Agent                   │
                      │    输入: Stage 1 接口签名 + platform profile│
                      │    输出: 平台适配 prompt                    │
                      │       ↓                                    │
                      │  Code Generation LLM                       │
                      │    输出: protocol_app.c + Makefile          │
                      └──────────────────┬────────────────────────┘
                                         │
                   ┌─────────────────────┼─────────────────────┐
                   ▼                     ▼                     ▼
         ┌──────────────┐     ┌────────────┐      ┌───────────────┐
         │ Linux raw    │     │ DPDK       │      │ 嵌入式 RTOS   │
         │ socket 适配  │     │ 适配       │      │ 适配          │
         └──────────────┘     └────────────┘      └───────────────┘
          （本文实现）           （架构可扩展）       （架构可扩展）
```

**对比 APG：**

```text
APG（一阶段，耦合）:
  RFC → LLM（人工硬编码 prompt，含 SOCK_RAW + Linux 规则）→ 平台绑定代码
  换平台 = 重写 prompt + 重跑整条 LLM 链

Kiro V5（两阶段，Agent 驱动解耦）:
  Stage 1: RFC → IR → 平台无关库代码（确定性，不含系统调用）
  Stage 2: Stage 1 产物 + platform profile → Agent 生成 prompt → LLM 生成薄集成层
  换平台 = 改 profile，Agent 自动适配
```

### 3.2 完整系统架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Kiro 系统 (V5)                            │
├──────────────┬──────────────────────────────────────────────────┤
│  输入层       │  协议标准文档（PDF）                                │
├──────────────┼──────────────────────────────────────────────────┤
│  提取层       │  index → chunk → classify → extract → merge      │
│  (RAG+LLM)   │  全自动 pipeline                                 │
├──────────────┼──────────────────────────────────────────────────┤
│  IR 层        │  MessageIR（结构层）                              │
│  (三层中间表示) │  BehaviorIR-lite（行为层）                        │
│              │  StateContextIR（状态层，最小版）                    │
├──────────────┼──────────────────────────────────────────────────┤
│  Stage 1     │  结构 codegen（struct + pack/unpack/validate）    │
│  生成层       │  行为 codegen（dispatcher skeleton）              │
│  (平台无关)   │  roundtrip test 生成                             │
├──────────────┼──────────────────────────────────────────────────┤
│  Stage 2     │  Prompt Generation Agent:                         │
│  适配层       │    platform profile + Stage 1 接口 → 动态 prompt  │
│  (Agent 驱动) │  Code Generation LLM → 薄集成层代码               │
│              │  支持: Linux raw socket / DPDK / RTOS 等           │
├──────────────┼──────────────────────────────────────────────────┤
│  验证层       │  IR 层: readiness 检查 + diagnostics              │
│  (分层验证)   │  Stage 1: roundtrip verify                       │
│              │  Stage 1: pcap 字节级互操作验证                    │
│              │  Stage 2: Linux 用户态互操作验证                   │
├──────────────┼──────────────────────────────────────────────────┤
│  修正层       │  patch lane（IR 层修正入口）                       │
└──────────────┴──────────────────────────────────────────────────┘
```

---

## 4. 协议角色（V5 沿用 V4，增加对标说明）

### 4.1 FC-LS：第一主案例

- 与题目「交换机协议软件」完全吻合
- LLM 训练数据中几乎没有 FC 代码 → **放大 IR 层价值**
- APG 未覆盖 → 差异化
- 承载所有层：MessageIR + BehaviorIR-lite + StateContextIR

### 4.2 BFD：pipeline 基线

- 当前最稳定的协议，回归保护
- APG 未覆盖，SAGE 覆盖了但方法不同
- 承载：MessageIR + BehaviorIR-lite + pcap 验证 + Stage 2 互操作实验

### 4.3 TCP Header：结构挑战样例

- APG 做了 TCP → 可直接对标结构层数据
- 只做 Header 的 pack/unpack + pcap 验证，不做完整 TCP 实现
- 展示 packed bitfield + options tail 的结构处理能力

### 4.4 简单协议（可选，视时间）

- ICMP：与 APG 直接对比数据最有力
- ARP：APG 没做，避免被其成熟数据压制
- 用于泛化证明："系统不是只对一两个协议有效"

---

## 5. 当前可行性分析（2026-03-31 snapshot）

### 5.1 已有资产清单

#### BFD (rfc5880)

| 资产 | 状态 | 详情 |
|------|------|------|
| 原始 PDF | ✅ | `data/raw/rfc5880-BFD.pdf` |
| protocol_schema.json | ✅ | 4 个消息、37 个字段、8 个状态机 |
| message_ir.json | ❌ **缺失** | schema 有但未 lower 到 MessageIR |
| generated/ | ✅ | 26 个文件（8 msg .c/.h + 16 SM .c/.h + test） |
| verify_report.json | ⚠️ | 结构检查 36/36 通过，roundtrip 通过，**但 FSM 有 10 个 duplicate case 编译错误** |

> **BFD 结论**：消息结构层基本完整可用（roundtrip 通过），但需要补 message_ir.json 落盘 + 修 FSM duplicate case。

#### TCP (rfc793)

| 资产 | 状态 | 详情 |
|------|------|------|
| 原始 PDF | ✅ | `data/raw/rfc793-TCP.pdf` |
| protocol_schema.json | ✅ | 1 个消息（TCP Header, 18 字段）、6 个状态机 |
| message_ir.json | ✅ | 1 个消息，状态 `degraded_ready`（有 options_tail） |
| generated/ | ✅ | 17 个文件 + 已编译 test_roundtrip.bin |
| verify_report.json | ⚠️ | 结构检查 22/22 通过，**roundtrip 编译+运行均通过**，FSM 有 22 个 duplicate case 错误 |

> **TCP 结论**：消息结构层完全可用（roundtrip 编译运行通过，有二进制产物），FSM 同样需修。**TCP 是当前最稳定的结构层样例。**

#### FC-LS

| 资产 | 状态 | 详情 |
|------|------|------|
| 原始 PDF | ✅ | `FC-LS.pdf` (2.1MB) + 多个子集版本 + `★FC-FS-4.pdf` (3.2MB) |
| 预处理 (chunks/content) | ✅ | 已完成 index + chunk + content |
| protocol_schema.json | ❌ **缺失** | 提取 pipeline 未跑完 |
| message_ir.json | ❌ **缺失** | — |
| generated/ | ❌ **缺失** | — |
| e2e evidence bundle | ✅ | 有检索证据排序数据 |

> **FC 结论**：PDF 已入库、内容已分块，但 classify → extract → merge → codegen → verify 全链路**从未跑通**。这是 V5 最关键的待完成项。

### 5.2 基础设施就绪度

| 基础设施 | 状态 | 工作量估计 |
|---------|------|----------|
| **提取 pipeline** (classify→extract→merge) | ✅ 可用 | 0（直接跑） |
| **codegen 模板** (pack/unpack/validate/roundtrip) | ✅ 可用 | 0 |
| **verify 框架** (syntax + symbol + roundtrip) | ✅ 可用 | 0 |
| **MessageIR normalization** | ✅ 可用 | 0 |
| **archetype lowering** | ✅ 可用 | FC 可能需新 archetype |
| **FSM codegen** | ⚠️ 有 bug | 需修 duplicate case（约 50-100 行） |
| **测试套件** | ✅ 343 个测试 | 0 |
| **pcap 读取/解析** | ❌ 不存在 | 新建，约 200-400 行 |
| **pcap 驱动的验证框架** | ❌ 不存在 | 新建，约 300-500 行 |
| **Ground Truth JSON** | ❌ 不存在 | 人工标注，每协议约 2-4 小时 |
| **Stage 2 platform profile** | ❌ 不存在 | 新建，约 200-400 行 |
| **Stage 2 codegen (Linux raw socket)** | ❌ 不存在 | 新建，约 300-600 行（模板 + 集成层） |
| **StateContextIR** | ⚠️ 模型层存在 | 需接入主链 + FC 实例化 |
| **BehaviorIR-lite** | ⚠️ FSM 提取有但 codegen 有 bug | 修 bug + 最小 typed action |
| **trace verify harness** | ❌ 不存在 | 新建，约 300-500 行 |
| **性能计时/token 统计** | ❌ 不存在 | pipeline 加埋点，约 100-200 行 |

### 5.3 风险评估

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| FC-LS 提取质量差（PDF 太长/结构复杂） | Phase 1 阻塞 | 中 | 用子集 PDF（FC-LS_36-50.pdf）先验证，逐步扩展 |
| FC-LS 无法获取 pcap | Phase 2 FC 部分缺失 | 高 | FC 只做 roundtrip，pcap 验证集中在 BFD+TCP |
| FSM duplicate case 修复引入回归 | BFD/TCP 产物退步 | 低 | 先冻结当前 artifacts，修后 diff 对比 |
| Stage 2 互操作在 macOS 上无法做 | Phase 3 阻塞 | 中 | 用 Docker Linux 环境或远程 Linux 服务器 |
| LLM API 费用超支 | 全 pipeline 受影响 | 低 | 利用已有 artifacts 缓存，只对 FC 做新提取 |

### 5.4 各 Phase 可行性判定

| Phase | 可行性 | 依赖 | 预计工作量 |
|-------|--------|------|-----------|
| Phase 0: 基线冻结 + GT 标注 | ✅ 高 | 无 | 1-2 天 |
| Phase 1: FC 结构主线打通 | ⚠️ 中高 | LLM API、FC PDF 质量 | 3-5 天 |
| Phase 2: pcap 字节级验证 | ✅ 高 | 需获取 BFD/TCP pcap 样例 | 2-3 天 |
| Phase 3: Stage 2 Linux 互操作 | ⚠️ 中 | 需 Linux 环境（Docker 可解决） | 2-3 天 |
| Phase 4: BehaviorIR + FSM 修复 | ✅ 高 | 只修现有 bug + 最小扩展 | 2-3 天 |
| Phase 5: StateContextIR + trace | ⚠️ 中 | 依赖 Phase 4 | 3-4 天 |
| Phase 6: 实验执行 | ✅ 高 | 依赖 Phase 0-3 | 3-5 天 |
| Phase 7: 回归 + 泛化 | ✅ 高 | 无 | 1-2 天 |
| **P0 总计** | | | **~12-18 天** |
| **P0+P1 总计** | | | **~20-28 天** |

### 5.5 关键路径

```text
Phase 0 (1-2d)
  │
  ├─→ Phase 1: FC 打通 (3-5d)──→ Phase 6 实验一/三/四
  │                                 │
  ├─→ Phase 2: pcap 验证 (2-3d)──→ Phase 6 实验一(pcap 部分)
  │                                 │
  └─→ Phase 3: Stage 2 (2-3d)───→ Phase 6 实验五
                                    │
                              Phase 4: FSM (2-3d)──→ Phase 5: trace (3-4d)──→ Phase 6 实验二
```

> Phase 1/2/3 之间**无依赖，可并行**。Phase 4→5 是串行的。关键路径是 Phase 0 → Phase 1 → Phase 6。

---

## 6. V5 主线 Phase

### Phase 0：基线冻结 + Ground Truth 标注

**目标**：固定起点，为实验做准备。

**要做的事：**

1. 固定 BFD/TCP 当前 artifacts（protocol_schema.json, message_ir.json, verify_report.json, generated/）
2. 明确 FC-LS 输入材料（PDF, 已有产物）
3. 人工标注 Ground Truth：
   - BFD Control Packet：字段名、偏移、长度、类型（从 RFC 5880 Section 4.1）
   - TCP Header：字段名、偏移、长度、类型（从 RFC 9293 Section 3.1）
   - FC-LS FLOGI/PLOGI：字段名、偏移、长度、类型（从 FC-LS 标准）
4. 获取 pcap 样例：
   - BFD：Wireshark wiki sample captures
   - TCP：tcpdump 捕获一次连接
   - ICMP：tcpdump + ping（如果做简单协议）

**验收标准：**
- BFD/TCP 当前产物可重放
- 至少 BFD + TCP 有 ground truth JSON
- 至少 BFD + TCP 有 pcap 样例文件

---

### Phase 1：FC 主案例打通结构主线

**目标**：让 FC-LS 从 PDF 跑通到 verify，产出与 BFD/TCP 同等质量的结构层产物。

**要做的事：**

1. FC-LS PDF 处理：index → chunk → classify → extract → merge
2. FC-LS MessageIR lowering（FLOGI / PLOGI / ACC）
3. FC-LS codegen → verify（roundtrip 通过）
4. FC-LS ground truth 标注 + 字段对照评估

**验收标准：**
- 至少一个 FC message family 的 MessageIR 为 READY
- 至少一条 FC codegen 路径编译通过
- 至少一条 FC roundtrip 通过
- 字段覆盖率 / 正确率有初步数据

---

### Phase 2：pcap 字节级互操作验证

**目标**：用 pcap 驱动验证补上互操作短板，验证粒度超过 APG。

**要做的事：**

1. 实现 pcap 驱动的测试框架：
   - 读取 pcap 中的原始字节
   - 调用生成的 `unpack()` 解码
   - 与 TShark 解析结果逐字段对比
   - 调用 `pack()` 重新编码，与原始字节 `memcmp`
2. 在 BFD 上验证 pcap 互操作
3. 在 TCP Header 上验证 pcap 互操作
4. 汇总 pcap_interop_report.json

**验收标准：**
- BFD pcap unpack + 字段对照通过
- TCP Header pcap unpack + 字段对照通过
- pack roundtrip 字节级一致

---

### Phase 3：两阶段 codegen — Stage 2 实现

**目标**：实现 Linux raw socket 的 Stage 2 适配层，与 APG 在其主场直接对比。

**核心设计：Prompt Generation Agent**

Stage 2 的代码生成采用 **Prompt Generation Agent** 架构，而非固定模板或人工编写 prompt：

```text
Stage 1 产物（msg_*.h/c 函数签名）
    + platform_profile.yaml（目标环境描述）
    ↓
Prompt Generation Agent（LLM）
    → 读取 Stage 1 接口签名 + 平台约束
    → 动态合成 Stage 2 代码生成 prompt
    ↓
Code Generation LLM
    → 生成 protocol_app.c + Makefile + glue code
```

**为什么用 Agent 而非固定模板/人工 prompt：**

1. **自由度高**：不同平台差异大（raw socket vs DPDK vs RTOS），固定模板难以覆盖，agent 按需组装
2. **知识注入自然**：agent 将平台 API 约束、头文件要求、内存模型等自然编入 prompt，无需硬编码
3. **对标 APG 叙事优势**：APG 的 prompt 是人工硬编码的（SOCK_RAW、字节序规则等），Kiro 是 agent 动态生成——自动化程度更高
4. **换平台成本低**：只需修改 platform_profile.yaml，agent 自动适配新环境的 prompt

**与 APG 的关键对比：**

| 维度 | APG | Kiro Stage 2 |
|------|-----|--------------|
| prompt 来源 | 人工硬编码在代码中 | Agent 根据 platform profile 动态生成 |
| 换平台代价 | 重写 prompt + 重跑全链路 | 改 profile，agent 重新生成 prompt |
| 领域知识位置 | 散落在各处 prompt 字符串中 | 集中在 platform profile + agent meta-prompt |
| 可审计性 | 低（prompt 藏在代码里） | 高（profile + 生成的 prompt 均可检查） |

**要做的事：**

1. 定义 platform profile 格式（YAML）：
   - 平台名、socket API 风格、编译器、头文件、约束条件
   - Linux raw socket 作为第一个 profile 实例
2. 实现 Prompt Generation Agent：
   - 输入：Stage 1 头文件列表 + 函数签名 + platform_profile.yaml
   - 输出：完整的 Stage 2 code generation prompt
   - 实现轻量：meta-prompt + Stage 1 接口扫描 + profile 注入
3. 用 Agent 生成的 prompt 驱动 LLM 生成 Stage 2 代码：
   - 输出：`protocol_app.c` + `Makefile`
   - 集成层只做：socket 创建 → 收字节 → unpack → 业务逻辑占位 → pack → 发送
4. 在 BFD 上实现 Linux 用户态发包 + tcpdump 验证
5. 统计：Stage 1 vs Stage 2 代码行数比例，展示平台代码隔离度
6. （可选 ablation）对比 agent-generated prompt vs human-written prompt 的代码质量

**执行策略：**
- 先用固定 prompt 把 BFD Linux 互操作跑通（验证 Stage 2 链路可行）
- 再加 Agent 层，将固定 prompt 重构为 agent 动态生成
- 论文中两者都展示：固定 prompt 是 baseline，agent prompt 是改进——自带 ablation

**验收标准：**
- BFD Stage 2 编译通过
- tcpdump 能抓到正确的 BFD 报文
- 平台相关代码行数 < 总代码行数 20%
- Prompt Generation Agent 能根据 profile 生成可用的 Stage 2 prompt

---

### Phase 4：BehaviorIR-lite + FSM skeleton 稳定化

**目标**：在 FC 和 BFD 上推出可编译的行为层骨架。

**要做的事：**

1. 修复 FSM codegen 的 duplicate case values 问题
2. 实现最小 BehaviorIR-lite：
   - v1 typed actions：`set_state`, `emit_message`, `start_timer`, `cancel_timer`
   - 其余保留 `raw_text` / `evidence`
3. FC Login 状态机提取 + dispatcher skeleton
4. BFD Session 状态机复用
5. 编译验证通过

**验收标准：**
- FC Login FSM dispatcher 编译通过
- BFD Session FSM dispatcher 编译通过（回归不退步）
- 至少支持 `set_state` + `emit_message` typed action

---

### Phase 5：StateContextIR 最小落地 + trace verify

**目标**：在 FC 上落地最小 StateContextIR，实现最小 trace verify 闭环。

**要做的事：**

1. 在 FC 上物化最小 StateContextIR：
   - fields: `state`, 关键会话字段
   - timers: 至少一个（如 `E_D_TOV`）
2. 实现最小 trace harness：
   ```
   decode msg → derive event → run transition → assert ctx delta → assert emit
   ```
3. FC Login 至少一条 trace 跑通
4. BFD 至少一条小 trace 跑通（推荐）

**验收标准：**
- `state_context_ir.json` 作为正式 artifact 落盘
- 至少一条 FC trace 跑通
- 三层 IR 全部有实际落地实例

---

### Phase 6：实验执行 + 对标 APG

**目标**：执行全部实验，产出论文核心数据。

**实验矩阵：**

| 实验 | 内容 | 协议 | 对标 APG |
|------|------|------|---------|
| 实验一（核心） | 结构层 codegen 评估 | FC+BFD+TCP | 对标 Table 3/4 |
| 实验二 | 行为层骨架评估 | FC+BFD | 无直接对标 |
| 实验三 | vs 直接 LLM 生成对比 | FC+BFD | 对标 APG 方法论 |
| 实验四 | IR 层价值专项 | FC | APG 无法做 |
| 实验五 | Linux 用户态互操作对比 | BFD | 直接对标 APG |
| 实验六 | 性能与开销分析 | 全 pipeline | 对标 Table 5 |

**各实验产物：**

- 实验一：`eval_structural_<protocol>.json`（字段覆盖率、正确率、编译、roundtrip、pcap）
- 实验二：`eval_behavioral_<protocol>.json`（状态/转移覆盖率、编译、trace）
- 实验三：对比表格（直接 LLM vs Kiro，多维度）
- 实验四：确定性实验（10 次 codegen diff=0）、可修正性实验、诊断实验、溯源展示
- 实验五：Stage 2 互操作结果 + 平台代码隔离度对比
- 实验六：`timing_report.json`、`llm_usage_report.json`

**验收标准：**
- 实验一核心表格数据完整
- 实验三对比表格完整
- 实验五互操作通过

---

### Phase 7：回归 + 泛化 + 论文

**目标**：补全工作量展示，确保稳定性。

**要做的事：**

1. BFD 全链路回归（确保 FC 改动没有回退 BFD）
2. TCP 结构层补全（pcap 互操作数据补齐）
3. （视时间）简单协议泛化：ARP 或 ICMP
4. （视时间）Directed 模式展示
5. （视时间）patch lane 最小版

**验收标准：**
- BFD 不因 FC/TCP 改造回退
- 论文数据全部产出

---

## 7. 验证策略（V5 分层版）

V5 的验证策略对标 APG 并在每一层超越：

```text
Layer 0: IR 层验证（APG 无）
  - MessageIR readiness 检查（BLOCKED/DEGRADED_READY/READY）
  - 字段偏移/宽度一致性校验
  - diagnostics 提前拦截错误

Layer 1: 编译验证（与 APG 同级）
  - gcc -fsyntax-only
  - 符号完整性检查

Layer 2: Roundtrip 验证（APG 无）
  - pack(sample) → unpack → validate → 逐字段断言
  - 编译 + 运行 test_roundtrip

Layer 3: pcap 字节级验证（超越 APG）
  - 真实报文 unpack → 与 TShark 逐字段对比
  - pack 重编码 → 与原始字节 memcmp

Layer 4: Stage 2 互操作验证（对标 APG）
  - Linux raw socket 发包 + tcpdump 抓包
  - 与 APG 在同等条件下对比
```

| 验证层 | APG 有 | Kiro V5 有 | 谁更强 |
|--------|--------|-----------|--------|
| IR 层 readiness | ❌ | ✅ | Kiro |
| 编译检查 | ✅ (g++ -fsyntax-only) | ✅ (gcc + 符号检查) | 持平 |
| Roundtrip | ❌ | ✅ | Kiro |
| pcap 字节级 | ❌ | ✅ | Kiro |
| 互操作（发包） | ✅ (raw socket + tcpdump) | ✅ (Stage 2 raw socket) | 持平 |
| 行为正确性 | 人工检查 | trace verify | Kiro |

---

## 8. 产物矩阵（V5 版）

```text
                提取层   MessageIR  BehaviorIR  StateCtxIR  Stage1代码  Stage2适配  Roundtrip  pcap验证  互操作  Trace
FC (主案例)     ✅       ✅         ✅           ✅          ✅          ❌          ✅         ❌*       ❌      ✅
BFD (基线)      ✅       ✅         ✅           ❌          ✅          ✅          ✅         ✅        ✅      ✅(推荐)
TCP (结构)      ✅       ✅         ❌           ❌          ✅          ❌          ✅         ✅        ❌      ❌
简单协议(可选)   ✅       ✅         ❌           ❌          ✅          ❌          ✅         ✅        ❌      ❌
```

> *FC 的 pcap 取决于是否有 FCoE/FC 模拟器或实验室设备。如果没有，FC 只做 roundtrip 验证。

**工作量统计：**
- 提取层：3-4 协议 × 5 阶段 = 15-20 个处理步骤
- IR 层：3 个 MessageIR + 2 个 BehaviorIR + 1 个 StateContextIR = 6 个 IR 实例
- Stage 1 codegen：3-4 个结构 + 2 个行为 = 5-6 个生成目标
- Stage 2 codegen：1 个平台适配实现
- 验证：3-4 个 roundtrip + 2 个 pcap + 1 个 Stage 2 互操作 + 1-2 个 trace = 7-9 个验证目标
- 实验：6 个实验 × 多协议 = 30+ 数据点
- **总计 60+ 可展示的工程产物**

---

## 9. 论文章节与 Phase 的对应

| 论文章节 | 对应 Phase | 核心展示 |
|---------|-----------|---------|
| 第一章 绪论 | - | APG 引出 motivation + 本文定位 |
| 第二章 相关工作 | - | APG / SAGE / RFCNLP / DocPrompting / EverParse |
| 第三章 系统设计 | 全部 | 两阶段架构图 + 三层 IR + 分层验证 |
| 第四章 协议信息提取 | Phase 1 | RAG pipeline + 提取结果 |
| 第五章 IR 设计与代码生成 | Phase 1-5 | MessageIR / BehaviorIR / StateContextIR + codegen |
| 第六章 平台适配与验证 | Phase 2-3 | Stage 2 + pcap + 互操作 |
| 第七章 实验评估 | Phase 6 | 6 个实验的数据表格 |
| 第八章 总结与展望 | - | 贡献总结 + 未来工作 |

---

## 10. 论文叙事线（V5 版）

```text
"协议软件开发很痛苦"（第一章 motivation）
  → "APG (AAAI 2026) 首次证明了 LLM 辅助的可行性"（第二章 related work）
  → "但 APG 存在不可审计、不确定、平台耦合、依赖先验知识等工程缺陷"（第二章 gap analysis）
  → "本文提出基于多层 IR 的两阶段代码生成系统，无需任何协议先验知识"（第三章 system design）
  → "系统通过 IR 中间层保证质量，所有协议语义从文档自动提取"（第四-五章 提取+IR+codegen）
  → "两阶段架构 + Prompt Agent 实现平台解耦"（第六章 Stage 2 + 验证）
  → "在 FC 等 LLM 未见过的协议上验证了 zero-prior-knowledge 能力"（第七章 实验）
  → "Demo 演示"（答辩）
```

---

## 11. 执行优先级总览

```text
P0（必须做，论文无法缺少）:
  ■ Phase 0: 基线冻结 + GT 标注 + pcap 收集
  ■ Phase 1: FC 结构主线打通
  ■ Phase 2: pcap 字节级验证（BFD + TCP）
  ■ Phase 3: Stage 2 Linux 用户态实验（BFD）
  ■ Phase 6: 实验一（结构层）+ 实验三（vs LLM 对比）+ 实验四（IR 价值）

P1（强烈建议做，显著增强论文）:
  ■ Phase 4: BehaviorIR-lite + FSM 稳定化
  ■ Phase 5: StateContextIR 最小版 + trace verify（凑齐三层）
  ■ Phase 6: 实验二（行为层）+ 实验五（Stage 2 互操作对比）+ 实验六（性能）

P2（加分项，视时间）:
  ■ Phase 7: 简单协议泛化（ICMP 或 ARP）
  ■ Phase 7: Directed 模式
  ■ Phase 7: patch lane 最小版
```

---

## 12. V5 与 V4 的关系

V5 不是否定 V4，而是因 APG 的出现做了战略性重构。

**V4 保留的精髓**：
- 三层目标、职责边界、先打通再抽象
- BFD 基线 + FC 主案例 + TCP 结构挑战
- consumer-driven 原则

**V5 新增的维度**：
- **Zero-Prior-Knowledge 定位**：系统不含任何协议先验知识，全部从文档提取
- 两阶段代码生成架构（Stage 1/2 解耦）
- Stage 2 Prompt Generation Agent（动态生成平台适配 prompt）
- 对标 APG 的实验设计（6 个实验）
- pcap 字节级互操作验证
- 分层验证策略（5 层）
- 专硕精准定位的论文叙事

---

## 13. 一句话版

> **V5 = 无先验知识 + 三层 IR + 两阶段平台解耦（Agent 驱动）+ 分层验证 + 对标 APG 的 6 个实验。核心叙事是"一个无先验知识的系统，仅从协议标准文档出发，通过多层 IR、分层验证、两阶段 Agent 驱动的平台解耦，将 LLM 辅助的协议代码生成从 APG 的可行性验证推进为面向工业级场景的工程化流水线"。**
