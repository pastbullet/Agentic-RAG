# Kiro Agentic RAG

一个面向技术文档（尤其协议 PDF）的 **Agentic RAG** 工程：
- 支持文档处理（索引 + 结构分块 + 内容库）
- 支持工具调用式问答（LLM 自主导航）
- 支持本地 Web 调试界面（含流式进度、Tool 调用记录、结果展示）

---

## 1. 核心能力

- **端到端文档处理流水线**：`PDF -> page index -> structure chunks -> content DB -> registry`
- **Agentic 问答循环**：LLM 通过 `get_document_structure` / `get_page_content` 自主检索
- **精确引用体系**：答案内支持 `<cite doc="..." page="N"/>`，并做页码有效性校验
- **多模型支持**：统一适配 OpenAI / Anthropic（Tool Calling）
- **可观测性**：
  - CLI `--verbose` 输出 tool trace
  - Web 流式事件输出 turn、tool 调用、最终答案
  - 会话日志写入 `data/sessions/*.json`
- **上下文 Sidecar**：会话/轮次/文档/证据/主题状态写入 `data/sessions/`

---

## 2. 项目结构

```text
.
├── page_index.py                # 旧索引主脚本（ingest 会调用）
├── structure_chunker.py         # 结构分块脚本（ingest 会调用）
├── build_content_db.py          # 内容库构建脚本（ingest 会调用）
├── run_pageindex.py             # 旧版运行入口（兼容保留）
├── utils.py                     # 旧脚本公共工具函数
├── src/
│   ├── main.py                  # CLI 统一入口（处理/问答）
│   ├── evaluate.py              # 评测入口
│   ├── models.py                # 全局数据模型
│   ├── agent/
│   │   ├── loop.py              # Agent 循环核心
│   │   ├── llm_adapter.py       # OpenAI / Anthropic 统一适配
│   │   └── citation.py          # 引用提取/校验/清洗
│   ├── ingest/
│   │   └── pipeline.py          # 文档处理编排
│   ├── tools/
│   │   ├── document_structure.py
│   │   ├── page_content.py
│   │   ├── registry.py
│   │   └── schemas.py
│   ├── context/                 # Context sidecar stores + manager
│   └── web/
│       ├── app.py               # FastAPI
│       └── static/index.html    # 前端页面
├── data/
│   ├── raw/                     # 原始 PDF
│   ├── out/                     # 索引与分块产物、运行时注册表
│   └── sessions/                # 上下文会话状态
├── data/out/content/            # 内容库（分页 JSON，按文档分目录）
├── data/sessions/               # QA 会话日志（消息/trace/答案）
└── tests/                       # 单元 + 集成测试
```

说明：上面几份根目录脚本属于“历史核心脚本”，当前 `src/ingest/pipeline.py` 通过最小侵入方式复用它们，而不是重写一套新实现。

---

## 3. 环境要求

- Python **3.10+**（建议 3.10/3.11）
- 本地可用的 OpenAI 或 Anthropic API Key

安装依赖：

```bash
pip install -r requirements.txt
```

---

## 4. 环境变量

项目通过 `.env` 读取配置（`python-dotenv`）。常用变量：

```bash
# 选择提供商：openai / anthropic
PROTOCOL_TWIN_LLM_PROVIDER=openai

# OpenAI
OPENAI_API_KEY=...
OPENAI_BASE_URL=...              # 可选
OPENAI_MODEL_NAME=gpt-4o         # 可选

# Anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=...           # 可选
ANTHROPIC_MODEL_NAME=claude-sonnet-4-20250514

# 通用
PROTOCOL_TWIN_MODEL=gpt-4o       # 推荐：统一给 extraction / QA 指定默认模型
PROTOCOL_TWIN_LLM_TIMEOUT_SEC=120
```

> 若不显式指定 provider，会根据模型名前缀（`claude*`）做推断。

---

## 5. CLI 使用

### 5.1 仅处理文档

```bash
python -m src.main --process data/raw/rfc5880-BFD.pdf
```

可选参数：
- `--force` 强制重建
- `--model` 指定模型

### 5.2 基于已注册文档问答

```bash
python -m src.main \
  --doc rfc5880-BFD.pdf \
  --query "BFD 控制报文的帧格式是什么？" \
  --verbose
```

### 5.3 传入 PDF 路径并自动确保可问答

```bash
python -m src.main \
  --pdf data/raw/FC-LS.pdf \
  --query "FLOGI 的关键字段有哪些？"
```

### 5.4 运行协议提取 / MessageIR 流水线

仓库根目录提供了一个独立 runner：[run_extract_pipeline.py](./run_extract_pipeline.py)。

它会自动读取 `.env`，因此通常**不需要显式传 `--model`**。模型解析顺序为：
- `PROTOCOL_TWIN_MODEL`
- `OPENAI_MODEL_NAME` / `ANTHROPIC_MODEL_NAME`
- `config.yaml`
- 代码默认值

先把新 PDF 处理到可提取状态，并运行到 `MERGE`：

```bash
python run_extract_pipeline.py \
  --pdf data/raw/rfc793-TCP.pdf \
  --stages process,classify,extract,merge \
  --show-message-irs
```

如果 `message_ir` 里已经出现 `READY` 的对象，再继续跑 codegen/verify：

```bash
python run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --stages codegen,verify \
  --show-message-irs
```

如果你要整条链一次跑完：

```bash
python run_extract_pipeline.py \
  --pdf data/raw/rfc793-TCP.pdf \
  --stages all \
  --show-message-irs
```

建议的测试顺序：
- 先跑 `process,classify,extract,merge`
- 观察 `data/out/<doc_stem>/message_ir.json`
- 只有在出现 `READY` MessageIR 后，再跑 `codegen,verify`

runner 会输出：
- 每个 stage 的成功/失败状态
- merge 后的 `message_ir_count` / `ready_message_ir_count`
- `MessageIR` 的 `READY/BLOCKED` 摘要与 diagnostics

---

## 6. Web 调试界面

启动服务：

```bash
uvicorn src.web.app:app --host 127.0.0.1 --port 8000 --reload
```

打开浏览器：
- <http://127.0.0.1:8000>

主要 API：
- `GET /api/docs`：已注册文档列表
- `POST /api/process/path`：按路径处理 PDF
- `POST /api/process/upload`：上传并处理 PDF
- `POST /api/qa`：同步问答
- `POST /api/qa/stream`：流式问答（SSE）
- `GET /api/sessions`：历史会话
- `GET /api/sessions/{session_id}`：会话详情

当前前端支持：
- 实时显示 turn 与 tool 调用
- 展示 tool 参数与返回内容（自动裁剪大结果）
- 展示模型可见中间说明（若模型在 tool call 响应中附带文本）
- 回答中页码引用可点击跳转 PDF 对应页

---

## 7. Tool 设计

### `get_document_structure(doc_name, part=1)`
- 输入：文档名、结构分块编号
- 输出：结构树、分页信息、导航提示
- 用途：先定位相关章节，再决定读取哪些页

### `get_page_content(doc_name, pages)`
- 支持页码格式：`"7"` / `"7-11"` / `"7,9,11"`
- 单次最多 10 页
- 输出字段：`page`, `text`, `tables`, `images`

---

## 8. 文档注册机制

注册来源为两层合并：
- 内置静态注册表：`src/tools/registry.py` 中 `DOC_REGISTRY`
- 运行时注册表：`data/out/doc_registry.runtime.json`

运行时注册优先生效。

---

## 9. 评测与测试

运行全量测试：

```bash
pytest -q
```

运行评测：

```bash
python -m src.evaluate --test-set data/eval/test_questions.json
```

也可使用：

```bash
python -m src.evaluate --test-set data/eval/goldset_pr9.json --model gpt-4o
```

---

## 10. 常见问题

### Q1: 提示 Unknown document
请先处理文档，或确认文档名与注册表一致（例如 `xxx.pdf`）。

### Q2: 回答中没有引用
请检查 prompt、模型输出与工具调用是否正常；日志可在 `data/sessions/` 中排查。

### Q3: 工具结果太大导致前端卡顿
已在后端流式事件中对 tool 结果做裁剪；完整结果仍保留在会话日志/trace 中。

---

## 11. 说明

- 本仓库目标是“**在现有 pageIndex 体系上做最小侵入工程化整合**”，而不是重做检索体系。
- 当前架构已经具备后续扩展空间（新工具、新 prompt 模式、结构化提取）。

---

## 12. README（按当前实现状态重写，保留旧版）

> 本节用于补充一版**更贴近当前真实实现状态**的 README。  
> 上面的内容保留，作为项目早期阶段与基础能力说明；本节更强调当前毕设主线、已经打通的链路，以及尚未完成的边界。

### 12.1 当前项目定位

这个项目已经不只是一个面向协议 PDF 的 QA Agentic RAG。

当前更准确的定位是：

**一个面向网络通信协议文档的自动化解析与代码生成实验平台。**

当前主线可以概括为：

```text
协议 PDF
-> 文档结构化
-> 节点语义分类
-> 多类型协议对象提取
-> 合并与归一化
-> MessageIR / Archetype / OptionListIR
-> C 代码生成
-> 编译与 roundtrip 验证
```

其中：

- QA Agentic RAG 是底座与展示入口
- 协议提取、IR、codegen、verify 才是当前毕设主线

### 12.2 当前已经打通的主链

当前仓库已经真实实现并验证了这条链：

```text
INDEX -> CLASSIFY -> EXTRACT -> MERGE -> CODEGEN -> VERIFY
```

主要入口：

- 文档处理与问答入口：
  - [src/main.py](/Users/zwy/毕设/Kiro/src/main.py)
- 协议提取主流程：
  - [src/extract/pipeline.py](/Users/zwy/毕设/Kiro/src/extract/pipeline.py)
- 独立 runner：
  - [run_extract_pipeline.py](/Users/zwy/毕设/Kiro/run_extract_pipeline.py)

### 12.3 当前已经实现的关键能力

#### 1. 文档处理与 QA

已完成：

- `PDF -> page index -> structure chunks -> content DB -> registry`
- Agentic RAG 问答
- Web 调试界面
- 引用页码校验与跳转

#### 2. 协议对象提取

已完成对叶节点的语义分类与按类型抽取：

- `state_machine`
- `message_format`
- `procedure_rule`
- `timer_rule`
- `error_handling`

相关代码：

- [src/extract/classifier.py](/Users/zwy/毕设/Kiro/src/extract/classifier.py)
- [src/extract/extractors/](/Users/zwy/毕设/Kiro/src/extract/extractors)

#### 3. MessageIR 主线

当前 `MessageIR` 已经不再只是字段列表，而是面向实现的统一帧结构表示。

当前已经支持：

- 固定字段与按字节对齐结构
- packed bitfield / mixed layout
- 条件尾部 composite case
- archetype-guided lowering
- 最小 `Option/TLV IR v1`

相关代码：

- [src/extract/message_ir.py](/Users/zwy/毕设/Kiro/src/extract/message_ir.py)
- [src/extract/message_archetype.py](/Users/zwy/毕设/Kiro/src/extract/message_archetype.py)
- [src/extract/message_archetype_lowering.py](/Users/zwy/毕设/Kiro/src/extract/message_archetype_lowering.py)
- [src/extract/option_tlv.py](/Users/zwy/毕设/Kiro/src/extract/option_tlv.py)

#### 4. codegen / verify

当前已能对 `MessageIR` 生成：

- `struct`
- `validate`
- `pack`
- `unpack`
- `test_roundtrip.c`

并执行：

- `gcc -fsyntax-only`
- 符号检查
- 消息级 roundtrip 测试

相关代码：

- [src/extract/codegen.py](/Users/zwy/毕设/Kiro/src/extract/codegen.py)
- [src/extract/verify.py](/Users/zwy/毕设/Kiro/src/extract/verify.py)

### 12.4 当前两个代表性协议样例

#### BFD

BFD 是当前 `MessageIR` 路径最完整的验证对象。

已经打通过：

- auth section
- packed header
- optional tail
- family dispatch

参考产物：

- [data/out/rfc5880-BFD/protocol_schema.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/protocol_schema.json)
- [data/out/rfc5880-BFD/verify_report.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/verify_report.json)

#### TCP

TCP 是当前 archetype-guided message 路径和 option list 路径的主要验证对象。

当前 TCP Header 已实现：

- `ProtocolMessage -> ArchetypeContribution -> MessageIR`
- packed fixed header
- `data_offset` 驱动的 tail
- 最小 TCP options 结构化支持

当前状态下，TCP Header 已能进入 codegen/verify，但仍然不是完整 TCP message 实现。

参考产物：

- [data/out/rfc793-TCP/protocol_schema.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/protocol_schema.json)
- [data/out/rfc793-TCP/message_ir.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/message_ir.json)
- [data/out/rfc793-TCP/generated/tcp_msg_tcp_header.c](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/generated/tcp_msg_tcp_header.c)

### 12.5 当前三层消息表示

当前消息相关表示分三层：

#### 1. `ProtocolMessage`

抽取阶段的原始消息对象，表示“文档里提到了哪些字段”。

#### 2. `ArchetypeContribution`

上游中间层，用于表示：

- 这个消息更像什么结构模式
- 是否有尾部
- 是否由长度字段控制
- 有哪些结构线索

#### 3. `MessageIR`

下游统一实现层，供 `codegen / verify` 真正消费。

### 12.6 当前 `MessageIR` 的状态语义

当前 `MessageIR` 已采用三态：

- `READY`
- `DEGRADED_READY`
- `BLOCKED`

含义是：

- `READY`：结构完整，可按完整路径生成
- `DEGRADED_READY`：外层结构已稳定，但局部仍保守降级
- `BLOCKED`：结构关键要素尚不明确，不能安全生成

### 12.7 当前 `StateContextIR` 的状态

当前仓库里已经加入了 `StateContextIR` 的模型层与最小 normalization：

- [src/extract/state_context.py](/Users/zwy/毕设/Kiro/src/extract/state_context.py)
- [src/models.py](/Users/zwy/毕设/Kiro/src/models.py)

它的定位是：

- 表达运行时状态
- 表达 `ctx.field / ctx.timer / ctx.resource`
- 为后续 `FSMIR` 升级做准备

但需要特别说明：

**当前 `state_contexts` 还只进入了 `ProtocolSchema` 和测试层，尚未真正接入 extraction / pipeline / codegen / verify 主链。**

也就是说，`StateContextIR` 当前是：

- 已建立模型层
- 已有样例与 readiness
- 但还不是系统真实产物

### 12.8 当前 FSM 的真实状态

当前 FSM 抽取已经存在，也能生成第一版 skeleton：

- [src/extract/templates/state_machine.c.j2](/Users/zwy/毕设/Kiro/src/extract/templates/state_machine.c.j2)
- [src/extract/templates/state_machine.h.j2](/Users/zwy/毕设/Kiro/src/extract/templates/state_machine.h.j2)

但当前 FSM codegen 还不稳定，主要问题是：

- 同一 `state + event` 下多条件分支被直接展开成多个 `case`
- 生成代码容易出现 `duplicate case value`

因此当前 FSM 更适合被理解为：

- 已有结构化语义骨架
- 还不是可直接运行的最终实现骨架

### 12.9 推荐的当前开发顺序

如果你继续在这个仓库里推进，当前最合理的顺序是：

1. 继续把 `MessageIR` 稳定在“结构层骨架”
2. 正式接入 `StateContextIR`
3. 升级 `FSMIR`，让它能引用 `ctx.field`
4. 再由 `FSM / procedure` 反推 message 还缺哪些字段和 option
5. 最后引入 `Agentic RAG + LLM` 做骨架槽位补全

也就是说，当前不建议再把所有复杂性继续压进 `MessageIR`。

### 12.10 当前建议使用方式

#### 查看 TCP 当前 MessageIR

```bash
python run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --stages merge \
  --show-message-irs
```

#### 直接对已有 TCP 产物做 codegen / verify

```bash
python run_extract_pipeline.py \
  --doc rfc793-TCP.pdf \
  --stages codegen,verify \
  --show-message-irs
```

#### 查看当前关键产物

- [data/out/rfc793-TCP/message_archetypes.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/message_archetypes.json)
- [data/out/rfc793-TCP/message_ir.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/message_ir.json)
- [data/out/rfc793-TCP/verify_report.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/verify_report.json)

### 12.11 当前边界与未完成项

当前还没有完成的关键部分包括：

- `StateContextIR` 尚未进入真实主链
- `FSM skeleton` 还未升级成可编译稳定版本
- `FSMIR` 还未正式引用 `ctx.field`
- TCP options 仍只支持最小集合
- TCP payload / checksum / 更复杂 option 仍未结构化
- 还没有进入“规则骨架 + Agentic RAG 补全行为代码”的完整新阶段

### 12.12 一句话结论

当前仓库已经从早期的 QA Agentic RAG 工程，发展成：

**一个已经打通消息结构抽取、`MessageIR`、codegen 与 verify，并正在向 `StateContextIR + FSMIR + 行为层补全` 演进的协议代码生成平台。**
