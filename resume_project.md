# 简历项目说明

---

**项目名称：** 面向技术协议文档的 Agentic RAG 问答系统

**技术栈：** Python · FastAPI · OpenAI / Anthropic API · Tool Calling · SSE · Hypothesis

---

## 项目描述

设计并实现了一套面向 PDF 技术文档（如 FC-LS、BFD 等协议规范）的 Agentic RAG 问答系统。系统核心创新在于以 LLM Tool Calling 驱动文档导航，而非依赖向量检索：LLM 通过调用 `get_document_structure` 浏览分层目录索引，再通过 `get_page_content` 按需提取原文，自主判断信息充足性后生成带页码引用的答案。

**端到端处理流水线：** 构建了从 PDF 到可问答状态的完整离线流水线，包括页面索引生成、结构分块（按 token 预算切分目录树为多个 part）、页面内容数据库构建，以及运行时文档注册表管理。

**多轮上下文复用：** 实现了 Structure-Only Sidecar 机制，跨轮次持久化已探索节点状态（`discovered` / `reading` / `read_complete`）和已读页码范围，以压缩格式注入下一轮 LLM 上下文，作为导航地图而非内容缓存。实测在同一文档的追问场景中，LLM 可直接定位目标页面，轮次从首轮约 5 轮降至 3 轮以内。

**引用与可信度：** 答案内嵌 `<cite doc="..." page="N"/>` 格式引用，系统自动校验引用页码是否在实际检索范围内，确保答案可追溯。

**Web 界面：** 基于 FastAPI + SSE 实现流式问答界面，实时展示每轮 tool 调用参数与结果，支持多轮对话历史与会话日志持久化。

**测试体系：** 编写 206 个测试，涵盖单元测试与 Hypothesis 属性测试（`@given` + `@settings(max_examples=100)`），覆盖 builder 截断逻辑、页码范围压缩、引用提取等核心模块。
