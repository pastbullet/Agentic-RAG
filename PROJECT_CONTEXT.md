# 项目背景、当前进展与后续路线

> 本文档用于在当前仓库中快速理解项目定位、真实实现状态，以及接下来应优先推进的毕设主线。
> 更新时间：2026-03-24

---

## 一、项目定位

这个项目最初是一个面向通信协议 PDF 的 **QA Agentic RAG** 工程，用来让 LLM 自主浏览文档结构、按页检索证据、生成带引用的回答。

当前它已经不再只是 QA 系统，而是发展为本次毕业设计的基础平台：

**基于大语言模型的网络协议文档自动化解析与代码生成系统**

毕设主线不是“做一个能问答的协议机器人”，而是：

```text
协议 PDF
  -> 文档结构化理解
  -> 全量协议语义提取
  -> 对象合并与归一化
  -> 代码生成
  -> 验证
```

其中：

- QA Agentic RAG 是基础设施与展示入口；
- 协议提取、合并、代码生成、验证才是毕设主线；
- 当前新增的 `MessageIR` 设计，正是为了把“抽取结果”推进到“可实现代码语义”。

---

## 二、与论文路线的对应关系

结合 [`docs/thesis_writing/thesis_outline.md`](docs/thesis_writing/thesis_outline.md)，当前项目的论文路线可以概括为四层：

### 1. 文档处理与问答底座

- PDF 解析、page index 构建、结构分块、内容库构建
- Agentic RAG 问答、页码引用、Web 调试界面

### 2. 全量协议语义提取

- 对文档树叶节点做语义分类
- 针对不同语义类型分别提取：
  - 状态机
  - 报文/帧结构
  - procedure rule
  - timer rule
  - error handling

### 3. 对象合并与工程化归一化

- 名称归一化
- 状态机多维相似度聚类
- 报文模糊匹配与互斥关键词阻断
- near-miss 诊断与人在回路裁决
- 后续引入 `MessageIR`，把报文从“文档抽取对象”转成“实现层对象”

### 4. 代码生成与验证

- 从协议 schema 生成 C 代码骨架
- GCC 语法检查
- 符号完整性检查
- roundtrip test stub 生成
- 后续从 skeleton 走向更真实的 `pack/unpack/validate`

---

## 三、当前系统已经实现到什么程度

### 3.1 已打通的主链路

当前仓库已经具备一个完整的五阶段协议提取原型：

```text
INDEX -> CLASSIFY -> EXTRACT -> MERGE -> CODEGEN -> VERIFY
```

这条链路不再停留在设计文档层面，核心代码已经存在于仓库中。

### 3.2 已完成模块

| 模块 | 路径 | 当前状态 | 作用 |
|------|------|----------|------|
| 文档处理 pipeline | `src/ingest/pipeline.py` | 已完成 | PDF -> page index -> structure chunks -> content DB -> registry |
| QA Agent 循环 | `src/agent/loop.py` | 已完成 | 基于 tool calling 的文档自主导航问答 |
| LLM 适配层 | `src/agent/llm_adapter.py` | 已完成 | 统一适配 OpenAI / Anthropic |
| 引用系统 | `src/agent/citation.py` | 已完成 | `<cite .../>` 提取、清洗、校验 |
| 文档结构工具 | `src/tools/document_structure.py` | 已完成 | 返回结构树分块 |
| 页面内容工具 | `src/tools/page_content.py` | 已完成 | 按页返回文本/表格/图片 |
| 上下文 sidecar | `src/context/` | 已完成 | 会话、轮次、证据、主题状态管理 |
| Web 调试界面 | `src/web/app.py` + `src/web/static/index.html` | 已完成 | QA、流式过程、PDF 跳页、历史会话 |
| 节点语义分类 | `src/extract/classifier.py` | 已完成 | 叶节点分类为 state_machine / message_format / timer_rule 等 |
| 多类型提取器 | `src/extract/extractors/` | 已完成 | 状态机、报文、过程、定时器、错误规则提取 |
| 提取主流程 | `src/extract/pipeline.py` | 已完成 | 协议提取五阶段编排 |
| 状态机合并 | `src/extract/sm_similarity.py` + `src/extract/merge.py` | 已完成 | 多维相似度合并与 near-miss 诊断 |
| 报文合并 | `src/extract/merge.py` | 已完成 | exact merge + fuzzy merge + 互斥关键词阻断 |
| HITL 证据卡 | `src/extract/evidence_card.py` | 已完成后端 | near-miss 证据整理、review decisions 持久化 |
| C 代码生成 | `src/extract/codegen.py` | 已完成第一版 | 生成状态机/报文的 `.h/.c` 骨架 |
| 代码验证 | `src/extract/verify.py` | 已完成第一版 | GCC 语法检查、符号完整性检查、roundtrip stub |
| 自动化测试 | `tests/` + `tests/extract/` | 已完成较大覆盖 | 覆盖 ingest、QA、extract、merge、codegen、verify |

### 3.3 关键外部依赖脚本

根目录仍保留三份历史核心脚本，由新 pipeline 复用，而不是重写：

| 脚本 | 功能 |
|------|------|
| `page_index.py` | 调用 LLM 构建文档结构树 |
| `structure_chunker.py` | 将结构树切成 `part_XXXX.json` |
| `build_content_db.py` | 逐页提取文本/表格/图片，构建内容库 |

因此当前工程是“新编排层 + 旧核心脚本复用”的结构。

---

## 四、当前真实架构

### 4.1 基础层：文档处理与 QA

这一层已经比较稳定，主要负责：

- 将 PDF 转为结构化可检索资产；
- 通过 `get_document_structure` + `get_page_content` 支撑 Agentic QA；
- 在 Web 页面中展示 tool trace、页码引用和 PDF 跳转。

这部分对应论文中的：

- 第三章 3.2 文档处理层设计
- 第五章 5.2 系统界面展示中的 QA 部分

### 4.2 毕设主线：全量协议提取

当前 `src/extract/pipeline.py` 已实现以下阶段：

1. 加载 page index / content DB
2. 收集全部叶节点
3. 对叶节点做语义分类
4. 路由到不同 extractor
5. 合并为 `ProtocolSchema`
6. 生成代码
7. 运行验证

已经实现的提取对象包括：

- `ProtocolStateMachine`
- `ProtocolMessage`
- `ProcedureRule`
- `TimerConfig`
- `ErrorRule`

也就是说，项目已经从“只有 QA”前进到“可从协议文档中抽取多类型协议对象”的阶段。

### 4.3 合并优化层

这是当前项目最有特色、也最贴近论文贡献的部分之一。

已经实现：

- conservative / aggressive 双模式名称归一化
- 状态机名称、状态集合、转移集合三维相似度
- Union-Find 聚类
- 报文 fuzzy merge
- 互斥关键词阻断（如 `md5` vs `sha1`）
- near-miss 报告
- review decision 持久化
- LLM 证据卡生成（只整理证据，不直接代替人工判决）

这一部分直接对应论文中的：

- 第三章 3.4 合并优化层设计
- 第四章 4.3 多维相似度合并算法
- 第四章 4.4 人在回路优化机制

### 4.4 代码生成与验证层

当前已经实现第一版 C 代码骨架生成：

- 状态机 `.h/.c`
- 报文 `.h/.c`
- 汇总头文件

并有第一版验证链路：

- `gcc -fsyntax-only`
- 结构符号检查
- `test_roundtrip.c` 测试桩生成

但这一层还没有达到“真实协议栈代码”的程度，当前更准确的说法是：

**已经具备 C skeleton generation + syntax/structure verification，尚未完成真实实现级的 pack/unpack/validate。**

---

## 五、当前数据模型与产物

### 5.1 当前主数据模型

当前核心模型位于 `src/models.py`，包括：

```python
ProtocolStateMachine
ProtocolMessage
ProcedureRule
TimerConfig
ErrorRule
ProtocolSchema
```

这些模型已经被真实用于提取、合并、代码生成与验证，不再只是预留。

### 5.2 当前数据目录

```text
data/
  raw/                # 原始 PDF
  out/                # 提取产物、schema、merge 报告、near-miss 报告、生成代码等
  sessions/           # QA 会话与上下文 sidecar
  uploads/            # Web 上传的 PDF
```

对单个协议文档，当前典型产物包括：

- `protocol_schema.json`
- `merge_report.json`
- `near_miss_report.json`
- `review_cards.json` / `review_decisions.json`（启用 HITL 时）
- `verify_report.json`
- `generated/` 目录下的 `.h/.c`

### 5.3 当前 BFD 示例产物

仓库中已经有 BFD 的真实产物，可作为当前完成度的参考样例：

- `data/out/rfc5880-BFD/protocol_schema.json`
- `data/out/rfc5880-BFD/verify_report.json`

当前该样例的状态是：

- 当前 `protocol_schema.json` 中包含：
  - `8` 个状态机
  - `4` 个 message
  - `8` 个 procedure
  - `1` 个 timer
  - `2` 个 error rule
- 已抽取出状态机、报文、procedure、timer、error 等对象；
- 已能生成 C 代码骨架；
- 已能运行 verify；
- `near_miss_report.json` 仍有较多候选待人工或后续策略消化；
- `verify_report.json` 中 `syntax_checked=True`、`syntax_ok=False`，说明 schema 质量和 message/codegen 语义还需要继续提升。

这符合当前项目的真实阶段：

**闭环已打通，但质量仍在迭代。**

---

## 六、与论文贡献的对应完成度

结合论文大纲中的“主要贡献”，当前可按如下方式理解项目进度：

### 贡献 1：端到端协议文档解析与代码生成流水线

**完成度：较高**

已基本具备从文档到 schema、再到代码生成和验证的端到端闭环原型。

当前缺口：

- extraction quality 仍不稳定；
- message 侧还缺一层更强的工程化 IR；
- verify 仍偏“语法/结构层”，不是完整语义验证。

### 贡献 2：基于多维相似度的协议对象智能合并算法

**完成度：较高**

状态机和 message 两条 merge 线都已有实现，并具备 near-miss 诊断与 review 决策机制。

当前缺口：

- 仍存在残余重复对象；
- 报文 merge 在“实现语义”层面还不够，更多还是对象去重而非工程归一化。

### 贡献 3：面向协议帧结构的 C 代码自动生成与验证方法

**完成度：中等**

已实现：

- schema 到 C skeleton 的映射；
- header/source 生成；
- 标识符规范化；
- 语法检查与符号完整性验证；
- roundtrip stub 生成。

当前缺口：

- 仍主要是 skeleton，不是完整 parser/encoder；
- 变长字段、条件字段、bitfield packing 等语义尚未系统落入代码；
- 这正是 `MessageIR` 需要解决的问题。

### 贡献 4：支持人在回路的渐进式提取优化机制

**完成度：中等偏高**

后端能力已具备：

- near-miss 候选生成
- evidence card 生成
- 人工裁决持久化
- 断点续跑

当前缺口：

- 主要还停留在 pipeline/文件产物层；
- 尚未形成完整的前端 review workflow。

---

## 七、当前最关键的问题

当前项目的主要问题已经不是“能不能跑通”，而是“抽取得到的 schema 能否稳定支持真实代码实现”。

### 7.1 message 语义过薄

当前 `ProtocolMessage` 只有：

- `name`
- `fields`
- `source_pages`

这足以支撑“抽到一个报文对象”和“生成结构体骨架”，但不足以支撑：

- 最终编码顺序
- bit/byte offset
- optional section
- variable-length
- presence rule
- validation rule
- enum domain

因此它更像“文档抽取对象”，还不是“实现层对象”。

### 7.2 merge 解决的是对象去重，不是实现归一化

当前 merge 很重要，但它的主要职责仍是：

- 减少重复状态机
- 减少重复 message
- 收敛名称差异

它还没有真正把 message lower 成代码生成可直接消费的实现语义对象。

### 7.3 codegen 已经到达现有 schema 的上限

当前 codegen 继续堆模板，不会从根本上解决问题。

真正的瓶颈在于：

- 上游 message 抽取结果不够完整
- 中间缺少一层实现导向的归一化表示

因此下一阶段的重点不应是继续美化模板，而应是补上 message IR。

---

## 八、为什么现在要引入 MessageIR

当前新增的设计文档：

- `docs/frame_ir/message_ir_design.md`
- `docs/frame_ir/message_ir_design_optimized.md`
- `docs/frame_ir/message_ir_design_optimized_v2.md`

其核心目标是：

```text
ProtocolMessage -> MessageIR -> C Skeleton -> Real Implementation
```

`MessageIR` 的定位是：

- 承接 message 的工程语义归一化；
- 把分散在多个章节中的字段、规则、枚举、条件、长度约束聚合起来；
- 为真实 `pack/unpack/validate/test` 提供统一输入。

对当前项目而言，`MessageIR` 不是锦上添花，而是下一阶段的关键基础设施。

它解决的是：

- 当前 `ProtocolMessage` 无法支撑真实代码生成的问题；
- 当前 codegen 过度依赖模板隐式假设的问题；
- 当前 verify 只能验证结构、不能验证更多协议语义的问题。

---

## 九、接下来的优先路线

### 第一优先级：实现 MessageIR v1

建议按当前设计文档中的务实路线推进：

- 沿用当前 merge 后 message 粒度，不在 v1 阶段再拆细对象；
- 先只覆盖 BFD 认证段；
- 先打通真实 `pack/unpack/validate/test`；
- 再扩展到更复杂的 control packet。

推荐切入对象：

- `Simple Password Authentication Section`
- `Keyed MD5 and Meticulous Keyed MD5 Authentication Section`
- `Keyed SHA1 / Meticulous Keyed SHA1 Authentication Section`

### 第二优先级：将 codegen 切到 MessageIR

目标不是完全推翻现有 codegen，而是：

- 让 codegen 优先消费 `MessageIR`
- 让 `ProtocolSchema.messages` 继续保留为抽取层产物
- 将“文档对象”和“实现对象”解耦

### 第三优先级：增强 verify

当前 verify 已有基础，但还需要继续向协议语义验证推进，例如：

- 校验规则自动生成
- 更真实的 roundtrip case
- 与 RFC 约束直接对照

### 第四优先级：补齐展示与实验

后续还需要为论文和答辩准备：

- 代码生成展示界面
- page-to-code 溯源展示
- 提取/合并/生成质量实验
- 典型案例分析

---

## 十、哪些部分已经适合写进论文

当前已经可以较稳定写入论文的内容包括：

- 文档处理与 QA 底座架构
- 五阶段提取 pipeline 设计
- 节点语义分类与多 extractor 架构
- 状态机多维相似度合并
- 报文 fuzzy merge 与互斥关键词阻断
- near-miss 与 HITL 机制
- C skeleton codegen 与 verify 初版实现

当前应以“已实现但仍在优化中”的口径写入论文的内容包括：

- 报文结构提取质量
- 合并后 schema 的稳定性
- 代码生成结果质量

当前更适合写成“下一阶段关键工作”的内容包括：

- `MessageIR`
- 更真实的 parser/encoder/validator
- 更完整的可运行协议栈代码

---

## 十一、关键文件速查

| 需求 | 文件 |
|------|------|
| 理解文档处理 | `src/ingest/pipeline.py` |
| 理解 QA 主循环 | `src/agent/loop.py` |
| 理解全局模型 | `src/models.py` |
| 理解提取主流程 | `src/extract/pipeline.py` |
| 理解节点分类 | `src/extract/classifier.py` |
| 理解 message 提取 | `src/extract/extractors/message.py` |
| 理解 merge 逻辑 | `src/extract/merge.py` |
| 理解状态机相似度 | `src/extract/sm_similarity.py` |
| 理解 HITL 证据卡 | `src/extract/evidence_card.py` |
| 理解 codegen | `src/extract/codegen.py` |
| 理解 verify | `src/extract/verify.py` |
| 理解 Web API | `src/web/app.py` |
| 理解 thesis 路线 | `docs/thesis_writing/thesis_outline.md` |
| 理解 MessageIR 方案 | `docs/frame_ir/message_ir_design_optimized_v2.md` |

---

## 十二、一句话总结

当前项目已经从“协议文档问答系统”发展为“协议文档解析、对象提取、智能合并、代码生成与验证的端到端原型”。

下一阶段的核心任务不是继续扩 QA，也不是继续堆模板，而是补齐 **MessageIR 这层实现语义归一化表示**，把现有闭环从“能跑通”推进到“能更稳定地产出真实协议实现代码”。
