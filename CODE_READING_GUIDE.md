# Kiro 工程阅读指南

这份指南不是简单的“目录说明”，而是给你一个更省力的阅读顺序：先建立全局心智模型，再沿着一条真实链路把核心代码走通，最后再补历史脚本和上下文系统。

如果你第一次读这个仓库，建议按下面顺序来，不要一上来就扎进 [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py)。

## 1. 先知道这个工程在做什么

第一步先看整体说明，不求记细节，只回答两个问题：

1. 这个项目解决什么问题？
2. 运行时主链路有哪些阶段？

推荐先读：

- [`README.md`](/Users/zwy/毕设/Kiro/README.md)
- [`INTERVIEW_QA_AGENTIC_RAG.md`](/Users/zwy/毕设/Kiro/INTERVIEW_QA_AGENTIC_RAG.md)

读完你应该能说出一句话版本：

“这是一个面向协议 PDF 的 Agentic RAG 系统，支持文档处理、工具调用式问答、Web 调试和会话可观测。”

## 2. 第一轮阅读：只看主链路

这一轮的目标不是理解所有细节，而是先把“用户一次提问”在代码里怎么流动走通。

### Step A: 从入口开始

先读：

- [`src/main.py`](/Users/zwy/毕设/Kiro/src/main.py)

你要重点看：

- CLI 分成哪几种模式
- 什么时候调用文档处理
- 什么时候调用问答循环

这一步读完后，你应该知道：

- `--process` 只做文档处理
- `--doc/--pdf + --query` 会先确保文档可用，再进入问答

### Step B: 看处理链路如何把 PDF 变成可问答资产

接着读：

- [`src/ingest/pipeline.py`](/Users/zwy/毕设/Kiro/src/ingest/pipeline.py)

这一文件是整个工程的“编排层”，建议重点理解这几个函数：

- `process_document`
- `ensure_document_ready`
- `resolve_pdf_for_doc`

你要回答的问题：

- 文档是否已处理，怎么判断？
- 新 PDF 处理时，会生成哪些产物？
- 为什么这个文件会去调用根目录的旧脚本？

这一层的核心心智模型是：

`PDF -> page_index -> structure chunks -> content DB -> register`

### Step C: 看问答主循环

接着读：

- [`src/agent/loop.py`](/Users/zwy/毕设/Kiro/src/agent/loop.py)

这是最重要的一份代码。建议带着下面几个问题读：

- 初始 `messages` 怎么组装？
- system prompt 从哪里来？
- LLM 返回 tool call 后发生了什么？
- 结果如何记录到 `trace`、`logs/sessions` 和 `data/sessions`？

你需要重点关注：

- `agentic_rag`
- `execute_tool`
- `_truncate_tool_result`
- `_truncate_result_for_stream`
- `_save_session`

这一轮读到这里，主链路其实已经通了。

## 3. 第二轮阅读：理解 Agent 靠什么工作

### Step D: 看 tool 定义与 tool 实现

按这个顺序读：

- [`src/tools/schemas.py`](/Users/zwy/毕设/Kiro/src/tools/schemas.py)
- [`src/tools/registry.py`](/Users/zwy/毕设/Kiro/src/tools/registry.py)
- [`src/tools/document_structure.py`](/Users/zwy/毕设/Kiro/src/tools/document_structure.py)
- [`src/tools/page_content.py`](/Users/zwy/毕设/Kiro/src/tools/page_content.py)

推荐顺序这样安排的原因：

- `schemas.py` 告诉你模型“看见”的工具长什么样
- `registry.py` 告诉你 `doc_name` 如何映射到产物路径
- `document_structure.py` 解释“结构检索”怎么实现
- `page_content.py` 解释“按页取证据”怎么实现

你要重点理解：

- 为什么 `get_document_structure` 和 `get_page_content` 被拆成两个工具
- `get_page_content` 为什么限制单次最多 10 页
- 为什么这个系统强调 `doc_name + page` 的可追溯性

### Step E: 看 prompt 和引用系统

接着读：

- [`src/agent/prompts/qa_system.txt`](/Users/zwy/毕设/Kiro/src/agent/prompts/qa_system.txt)
- [`src/agent/citation.py`](/Users/zwy/毕设/Kiro/src/agent/citation.py)
- [`src/models.py`](/Users/zwy/毕设/Kiro/src/models.py)

这三份放在一起读最合适，因为它们共同定义了“输出应该长什么样”：

- prompt 规定模型如何导航、如何回答、如何引用
- `citation.py` 负责从答案里提取和校验 `<cite .../>`
- `models.py` 定义了 `RAGResponse`、`ToolCallRecord`、`Citation` 等核心数据结构

读完这一组，你应该能回答：

- 为什么这个系统不是普通聊天机器人，而是“带证据的回答系统”
- 一条回答最终有哪些结构化产出

### Step F: 看模型适配层

然后读：

- [`src/agent/llm_adapter.py`](/Users/zwy/毕设/Kiro/src/agent/llm_adapter.py)

这里建议只抓大逻辑，不要陷入 provider 细节：

- OpenAI / Anthropic 差异是怎么被抹平的
- tool call 和 tool result message 是怎么统一表示的

如果你先读这个文件再读 `loop.py`，会有点抽象；但在已经理解主循环以后再回来读，会很顺。

## 4. 第三轮阅读：看 Web 层如何把主链路暴露出来

先读后端，再读前端。

### Step G: 后端 API

读：

- [`src/web/app.py`](/Users/zwy/毕设/Kiro/src/web/app.py)

重点看三类接口：

- 文档处理：`/api/process/*`
- 问答：`/api/qa`、`/api/qa/stream`
- 会话与 PDF：`/api/sessions*`、`/api/pdf/{doc_name}`

你要重点理解：

- 为什么这里会有同步接口和流式接口两套
- SSE 事件类型有哪些
- 前端为什么能看到 turn、tool 调用、tool 结果

### Step H: 前端页面

读：

- [`src/web/static/index.html`](/Users/zwy/毕设/Kiro/src/web/static/index.html)

这个文件很大，但建议按块阅读：

1. HTML 结构：页面分为左侧文档/历史，中间聊天，右侧 PDF
2. 状态对象：`state`
3. PDF 预览相关函数
4. SSE / 聊天消息渲染
5. 参数设置面板与本地持久化

重点不是背 DOM，而是看：

- 流式事件如何映射成“过程记录”
- tool 参数和结果如何显示到 UI
- 处理参数如何传回后端

## 5. 第四轮阅读：最后再读历史脚本

这一轮才建议看根目录的旧脚本，因为它们细节多、体量大，放在前面很容易迷路。

推荐顺序：

- [`structure_chunker.py`](/Users/zwy/毕设/Kiro/structure_chunker.py)
- [`build_content_db.py`](/Users/zwy/毕设/Kiro/build_content_db.py)
- [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py)
- [`utils.py`](/Users/zwy/毕设/Kiro/utils.py)
- [`run_pageindex.py`](/Users/zwy/毕设/Kiro/run_pageindex.py)

原因是：

- `structure_chunker.py` 相对独立，容易先建立“结构分块”概念
- `build_content_db.py` 清晰地说明内容库如何按页产出
- `page_index.py` 最重、最复杂，放在前面成本最高
- `utils.py` 是 `page_index.py` 的支持库，最好边查边看，不建议通读
- `run_pageindex.py` 只是旧入口，价值最低

### 读 `page_index.py` 时的正确姿势

不要试图从头到尾顺着读。建议只抓下面几块：

- `page_index` / `page_index_main`
- `tree_parser`
- `meta_processor`
- `process_large_node_recursively`

你要理解的是：

- 它如何从 PDF 中抽 TOC / 章节树
- 为什么会有大量规则 + LLM 混合逻辑
- 为什么它会成为整个处理流程里最慢的部分

## 6. 第五轮阅读：如果你要继续做多轮能力，再看 Context 系统

建议顺序：

- [`src/context/manager.py`](/Users/zwy/毕设/Kiro/src/context/manager.py)
- [`src/context/updater.py`](/Users/zwy/毕设/Kiro/src/context/updater.py)
- [`src/context/stores/session_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/session_store.py)
- [`src/context/stores/turn_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/turn_store.py)
- [`src/context/stores/document_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/document_store.py)
- [`src/context/stores/evidence_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/evidence_store.py)
- [`src/context/stores/topic_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/topic_store.py)

这一部分不是主问答链路的第一理解重点，但如果你要做：

- 多轮会话
- 主题跟踪
- 证据复用
- Sidecar 状态演化

那它就很关键。

## 7. 最后再用测试反向验证理解

当你对主链路有感觉以后，再读测试会非常高效。推荐顺序：

- [`tests/test_main_cli.py`](/Users/zwy/毕设/Kiro/tests/test_main_cli.py)
- [`tests/test_ingest_pipeline.py`](/Users/zwy/毕设/Kiro/tests/test_ingest_pipeline.py)
- [`tests/test_agent_loop.py`](/Users/zwy/毕设/Kiro/tests/test_agent_loop.py)
- [`tests/test_web_api.py`](/Users/zwy/毕设/Kiro/tests/test_web_api.py)
- [`tests/test_document_structure.py`](/Users/zwy/毕设/Kiro/tests/test_document_structure.py)
- [`tests/test_page_content.py`](/Users/zwy/毕设/Kiro/tests/test_page_content.py)

测试适合拿来做两件事：

- 验证自己对行为的理解是否正确
- 快速找到“代码作者默认认为系统应该怎么工作”

## 8. 推荐的阅读节奏

如果你时间很紧，可以按下面节奏：

### 1 小时速读版

- [`README.md`](/Users/zwy/毕设/Kiro/README.md)
- [`src/main.py`](/Users/zwy/毕设/Kiro/src/main.py)
- [`src/ingest/pipeline.py`](/Users/zwy/毕设/Kiro/src/ingest/pipeline.py)
- [`src/agent/loop.py`](/Users/zwy/毕设/Kiro/src/agent/loop.py)
- [`src/tools/document_structure.py`](/Users/zwy/毕设/Kiro/src/tools/document_structure.py)
- [`src/tools/page_content.py`](/Users/zwy/毕设/Kiro/src/tools/page_content.py)
- [`src/web/app.py`](/Users/zwy/毕设/Kiro/src/web/app.py)

### 半天深入版

在速读版基础上继续看：

- [`src/tools/registry.py`](/Users/zwy/毕设/Kiro/src/tools/registry.py)
- [`src/tools/schemas.py`](/Users/zwy/毕设/Kiro/src/tools/schemas.py)
- [`src/agent/llm_adapter.py`](/Users/zwy/毕设/Kiro/src/agent/llm_adapter.py)
- [`src/agent/citation.py`](/Users/zwy/毕设/Kiro/src/agent/citation.py)
- [`src/models.py`](/Users/zwy/毕设/Kiro/src/models.py)
- [`src/web/static/index.html`](/Users/zwy/毕设/Kiro/src/web/static/index.html)
- [`structure_chunker.py`](/Users/zwy/毕设/Kiro/structure_chunker.py)
- [`build_content_db.py`](/Users/zwy/毕设/Kiro/build_content_db.py)

### 1 天完整版

再补：

- [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py)
- [`utils.py`](/Users/zwy/毕设/Kiro/utils.py)
- [`src/context/manager.py`](/Users/zwy/毕设/Kiro/src/context/manager.py)
- [`src/context/updater.py`](/Users/zwy/毕设/Kiro/src/context/updater.py)
- 核心测试文件

## 9. 阅读时最值得记的 5 个问题

建议你边读边自己写下答案：

1. 文档从原始 PDF 到可问答资产，中间经历了哪些文件产物？
2. Agent 为什么只靠两个工具也能完成导航？
3. 回答中的引用是如何从 prompt、代码到 UI 一路落地的？
4. 这个工程为什么要保留根目录旧脚本，而不是全部重写到 `src/`？
5. 当前系统的性能瓶颈在哪里，为什么新文档上传会慢？

## 10. 一句话建议

先读“编排层和主循环”，再读“工具和 Web”，最后才读“旧索引脚本”。  
如果一开始就钻进 [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py)，很容易见树不见林。
