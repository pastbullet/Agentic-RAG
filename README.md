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
