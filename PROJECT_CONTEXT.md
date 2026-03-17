# 项目背景与后续计划

> 本文档用于在新工作目录中快速了解当前工程的全貌和毕设后续要做的事。

---

## 一、当前工程是什么

一个面向通信协议 PDF 文档的 Agentic RAG 系统，核心能力是让 LLM 自主导航文档结构树、检索页面内容、生成带引用的回答。

### 已完成的模块

| 模块 | 路径 | 功能 |
|------|------|------|
| 文档处理 pipeline | `src/ingest/pipeline.py` | PDF → page_index → 结构分块 → 内容库 → 注册 |
| Agent 问答循环 | `src/agent/loop.py` | LLM 通过 tool calling 自主检索并回答 |
| LLM 适配层 | `src/agent/llm_adapter.py` | 统一适配 OpenAI / Anthropic |
| 引用系统 | `src/agent/citation.py` | `<cite doc="..." page="N"/>` 提取、校验、清洗 |
| 工具：文档结构 | `src/tools/document_structure.py` | 返回文档树的分块结构（章节标题、摘要、页码范围） |
| 工具：页面内容 | `src/tools/page_content.py` | 返回指定页的文本、表格、图片 |
| 文档注册表 | `src/tools/registry.py` | 静态 + 运行时注册，管理已处理文档 |
| Context 管理 | `src/context/manager.py` | 会话/轮次/文档/证据/主题状态管理 |
| Context 复用 | `src/context/reuse/builder.py` | 跨轮次导航上下文复用（已读节点、已读页面） |
| Web UI | `src/web/app.py` + `src/web/static/index.html` | FastAPI + SSE 流式进度、tool 调用展示、PDF 页码跳转 |
| 评测 | `src/evaluate.py` | 基于 test_questions.json 的自动评测 |
| 数据模型 | `src/models.py` | 全局共享数据结构，包括已预留的协议提取模型 |

### 关键外部依赖（根目录脚本）

| 脚本 | 功能 |
|------|------|
| `page_index.py` | 调用 LLM 构建文档结构树（page_index JSON） |
| `structure_chunker.py` | 将结构树分块，生成 part_XXXX.json |
| `build_content_db.py` | 将 PDF 按页提取文本/表格/图片，生成 content JSON |

这三个脚本由 `src/ingest/pipeline.py` 以最小侵入方式调用，不是重写的。

### 数据目录

```
data/
  raw/              # 原始 PDF（FC-LS.pdf, rfc5880-BFD.pdf, rfc792-ICMP.pdf 等）
  out/              # page_index JSON、结构分块（chunks_3/）、运行时注册表
  sessions/         # Context sidecar 会话状态
  eval/             # 评测用例

output/docs/        # 内容库（分页 JSON，按文档名分目录）
logs/sessions/      # QA 会话日志
```

### page_index.json 的节点结构

这是整个系统的核心数据结构。每个节点包含：

```json
{
  "node_id": "0127",
  "title": "4.2.23.1 Introduction",
  "summary": "...",
  "start_index": 82,        // 起始页码
  "end_index": 82,           // 结束页码
  "start_line": 10,          // 页内起始行号（叶节点有）
  "end_line": 15,            // 页内结束行号（叶节点有）
  "is_skeleton": false,      // true = 中间节点（无原文），false = 叶节点
  "children": [...],
  "retrieval_disabled": false
}
```

小文档（如 ICMP）的节点还有 `text` 字段（完整原文）。大文档（如 FC-LS）的节点没有 `text`，需要通过 start_index/end_index + start_line/end_line 从 content DB 中切取。

### 已预留的协议提取数据模型（`src/models.py`）

```python
class ProtocolState:        # 状态机中的单个状态
    name, description, is_initial, is_final

class ProtocolTransition:   # 状态转移
    from_state, to_state, event, condition, actions

class ProtocolStateMachine:  # 完整状态机
    name, states, transitions, source_pages

class ProtocolField:        # 帧/报文中的单个字段
    name, type, size_bits, description

class ProtocolMessage:      # 完整帧/报文结构
    name, fields, source_pages

class ProtocolSchema:       # 协议的结构化表示（代码生成的输入）
    protocol_name, state_machines, messages, constants, source_document
```

### 已有的 prompt 模板

- `src/agent/prompts/qa_system.txt` — QA 问答用
- `src/agent/prompts/extraction_system.txt` — 提取用（已预留）
- `src/agent/prompts/pageindex_system.txt` — pageindex 风格

### 配置

```yaml
# config.yaml
model: "gpt-4o-2024-11-20"
toc_check_page_num: 25
max_page_num_each_node: 20
max_token_num_each_node: 10000
if_add_node_id: "yes"
if_add_node_summary: "yes"
if_add_doc_description: "yes"
if_add_node_text: "no"
```

环境变量通过 `.env` 配置，支持 OpenAI / Anthropic 双提供商。

---

## 二、毕设课题

**利用 LLM 读通信协议文档，提取状态机和帧结构，生成代码，并验证。**

专业硕士，重点看工作量和 motivation。

### Motivation

- 通信协议文档（RFC、FC 标准）是工程师必须手动阅读并实现的，耗时且容易出错
- LLM 可以辅助自动化这个过程，但直接扔整个文档给 LLM 效果差（太长、结构复杂、表格多）
- 需要一套 pipeline：先结构化理解文档 → 提取关键模型 → 生成代码 → 验证

### 整体架构

```
全量提取 pipeline（毕设核心）
    协议文档 → 文档结构树 → 遍历节点 → LLM 提取状态机 + 帧结构 → 代码生成 → 验证

展示系统（毕设呈现）
    当前 web UI + code preview + page 溯源

QA 补充（锦上添花）
    现有 agentic RAG 作为补漏 + 工程师辅助工具
```

### 全量提取 vs QA 的区别

全量提取不需要"找到相关内容"（内容已经在文档树里了），只需要"从内容里提取模型"。所以不需要 hybrid 检索，直接遍历树、逐节点让 LLM 提取结构化信息。

QA 系统作为辅助：
- 全量提取后，部分未提取到的内容可以通过 QA 补充
- 让工程师对当前协议和生成的代码更熟悉
- 最终展示时，QA 界面可以点击 page 引用跳转到对应代码实现

---

## 三、后续要做的事

### 阶段 1：全量提取 pipeline

在当前项目中新建 `src/extract/` 模块：

```
src/extract/
    pipeline.py         # 提取主流程：遍历文档树，逐节点提取
    state_machine.py    # 状态机提取逻辑
    frame_parser.py     # 帧结构提取逻辑
```

核心流程：
1. 加载已有的 page_index.json（文档结构树）
2. 遍历所有叶节点
3. 对每个节点，从 content DB 取原文
4. 用 LLM 提取该节点中的状态机和帧结构
5. 输出 ProtocolSchema JSON

建议先用 BFD（RFC 5880）跑通，因为它小、状态机明确（BFD 有 6 个状态、清晰的转移条件）。FC-LS 太大，不适合第一个。

### 阶段 2：代码生成

```
src/extract/
    codegen.py          # 从 ProtocolSchema 生成代码
```

输入：ProtocolSchema（状态机 + 帧结构）
输出：可执行代码（parser、encoder/decoder、状态机实现）

目标语言待定（C / Python / 语言无关中间表示）。

### 阶段 3：验证

```
src/extract/
    verify.py           # 验证生成代码的正确性
```

可选验证方式：
- 用 RFC 里的测试向量验证帧解析
- 用已有开源实现（如 Linux 内核 TCP 状态机）作为 ground truth 对比
- 用 model checking 工具验证状态机性质（可达性、死锁）
- 生成单元测试并运行

### 阶段 4：展示系统扩展

在现有 web UI 基础上增加：
- Code preview 界面：展示生成的代码
- Page 溯源：点击代码片段跳转到 PDF 对应页
- 状态机可视化：展示提取出的状态图
- QA 辅助入口：对提取结果进行补充查询

---

## 四、可复用的现有资产

| 资产 | 用途 |
|------|------|
| `src/ingest/pipeline.py` | 文档处理，直接复用 |
| `page_index.json` 产物 | 文档结构树，提取的遍历基础 |
| content DB（`output/docs/`） | 原文内容，提取的输入 |
| `src/models.py` 中的 Protocol* 系列 | 提取输出的数据结构，已定义好 |
| `src/agent/llm_adapter.py` | LLM 调用，提取时复用 |
| `src/web/app.py` | Web 展示，后续扩展 |
| `src/tools/page_content.py` | 页面内容读取，提取时复用 |
| `data/raw/*.pdf` | 原始协议文档 |

---

## 五、未来可选优化（非毕设必须）

### Hybrid MCTS 检索优化

已有完整设计方案：`HYBRID_MCTS_RETRIEVAL_DESIGN.md`

核心思想：用 chunk embedding 给 node 打 value 分，结合 MCTS-style 的 selection/expansion/backprop 做动态树探索，替换当前逐 part LLM 搜索。

这个优化适用于 QA 场景的加速，不影响全量提取 pipeline。如果毕设主线完成后有时间，可以回来做。

---

## 六、关键文件速查

| 需求 | 看哪个文件 |
|------|-----------|
| 理解文档处理流程 | `src/ingest/pipeline.py` |
| 理解 agent 问答循环 | `src/agent/loop.py` |
| 理解数据模型 | `src/models.py` |
| 理解文档树结构 | `data/out/*_page_index.json` |
| 理解页面内容格式 | `output/docs/*/json/content_*.json` |
| 理解 web API | `src/web/app.py` |
| 理解 context 管理 | `src/context/manager.py` |
| 理解 hybrid 检索方案 | `HYBRID_MCTS_RETRIEVAL_DESIGN.md` |
| 理解工具定义 | `src/tools/schemas.py` |
