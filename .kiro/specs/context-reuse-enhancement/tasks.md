# 实施计划：上下文复用增强（Context Reuse Enhancement）

## 概述

按照自底向上的方式实现上下文复用层。Phase 0 实现三个核心组件（PageSummaryGenerator → ContextReuseBuilder → PageDedupWrapper），Phase 1 集成到 Agent Loop 和 ContextManager，Phase 2 扩展 Web API 并添加 Prompt_Level_Precheck。每个组件先实现后测试，测试采用单元测试 + Hypothesis 属性测试双轨策略。所有新增代码位于 `src/context/reuse/` 目录，对已有代码的修改遵循 Sidecar 模式（异常不影响主流程）。

## 任务

- [ ] 1. 模块骨架与 PageSummaryGenerator（Phase 0）
  - [ ] 1.1 创建 `src/context/reuse/` 目录结构
    - 创建 `src/context/reuse/__init__.py`，导出 `ContextReuseBuilder`、`PageSummaryGenerator`、`PageDedupWrapper`
    - 创建 `src/context/reuse/builder.py`、`src/context/reuse/page_summary.py`、`src/context/reuse/dedup.py` 空骨架
    - 创建 `tests/context/reuse/` 目录和 `__init__.py`
    - _需求: 全局_

  - [ ] 1.2 实现 PageSummaryGenerator (`src/context/reuse/page_summary.py`)
    - 实现 `generate(page_num, doc_name, text, turn_id) -> dict` 静态方法
    - 前段截断法：按 `\n\n` 分段，依次累加段落直到总字符数达到原文 50% 字符限制
    - 返回符合 Page_Summary JSON Schema 的字典（page_num、doc_name、summary_text、original_length、summary_length、generated_at、source_turn_id）
    - 空文本输入返回 summary_text 为空字符串的字典
    - 实现 `save(session_dir, doc_name, summary) -> None` 静态方法
    - 写入路径：`<session_dir>/documents/<doc_name>/page_summaries/page_<N>.json`
    - 文件已存在时跳过不覆盖（首次生成不可覆盖原则）
    - 实现 `load(session_dir, doc_name, page_num) -> dict | None` 静态方法
    - 实现 `load_all(session_dir, doc_name) -> list[dict]` 静态方法
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ] 1.3 编写 PageSummaryGenerator 属性测试 (`tests/context/reuse/test_page_summary.py`)
    - **Property 8: Page_Summary 生成约束** — 对任意非空文本，验证 summary_text 字符数 ≤ 原文字符数 50%，且字典包含所有必需字段
    - **Property 9: Page_Summary 首次生成不可覆盖** — 生成并 save 后再次 save 不同内容，验证文件内容不变
    - 单元测试：空文本、单段落文本、中文文本、超长文本
    - _验证: 需求 4.1, 4.2, 4.3, 4.4, 4.6_

- [ ] 2. ContextReuseBuilder（Phase 0）
  - [ ] 2.1 实现 ContextReuseBuilder (`src/context/reuse/builder.py`)
    - `__init__(self, session_dir: Path, summary_char_budget: int = 4000)`
    - `build_summary_dict(self, doc_name: str) -> dict`：从会话目录读取四类数据源（document_state.json、nodes/*.json、evidences/*.json、page_summaries/*.json），组装为符合 Context_Summary Schema 的字典
    - 仅包含 status 为 `read_complete` 或 `reading` 的节点
    - Evidence 按 `extracted_in_turn` 降序排序（最新优先）
    - `build_summary(self, doc_name: str) -> str`：将字典序列化为 Markdown 文本（三个 `##` 区段），`total_chars` 仅写入 logger.info 和字典，不出现在 Markdown 文本中
    - 所有数据源为空时返回空字符串
    - `_truncate_to_budget()`：超出 summary_char_budget 时按优先级截断（Evidence > Page_Summary > Node_Summary）
    - 各 `_read_*` 方法：单个数据源读取失败返回空列表/None，记录 warning，不抛异常
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.5, 2.6, 8.4, 9.1, 9.2, 9.4_

  - [ ] 2.2 编写 ContextReuseBuilder 属性测试 (`tests/context/reuse/test_builder.py`)
    - **Property 1: 上下文摘要完整组装** — 随机生成 node/evidence/page_summary 文件，验证 build_summary_dict 返回完整且正确的字典
    - **Property 2: Markdown 序列化格式** — 验证 build_summary 返回包含三个区段标题的文本，total_chars 不出现在文本中但存在于字典中
    - **Property 3: 数据源失败容错** — 随机使一个数据源目录不可读，验证其余正常组装
    - **Property 6: 摘要字符预算约束** — 随机生成超出预算的数据，验证文本字符数 ≤ budget，截断优先级正确
    - **Property 13: 跨轮次累积状态** — 随机生成多轮次数据，验证 build_summary_dict 反映全部累积状态
    - **Property 14: JSON 往返** — build_summary_dict 结果经 JSON 序列化再反序列化后与原始字典相等
    - _验证: 需求 1.1-1.7, 2.5, 2.6, 8.1-8.4, 9.1-9.4_

- [ ] 3. PageDedupWrapper（Phase 0）
  - [ ] 3.1 实现 PageDedupWrapper (`src/context/reuse/dedup.py`)
    - `__init__(self, session_dir: Path, doc_name: str, enable: bool = True)`
    - `get_page_content(self, doc_name: str, pages: str) -> dict`
    - 解析 pages 字符串为 `list[int]`（复用 `src/tools/page_content.parse_pages`）
    - 读取 document_state.json 获取 read_pages
    - 对已读且有 Page_Summary 的页面：构造 is_cached=True 的条目，text 以 `[已读页面摘要]` 开头
    - 对其余页面：调用原始 `get_page_content` 获取全文，标记 is_cached=False
    - 合并结果保持原始返回结构（content 列表、next_steps、total_pages）
    - enable=False 时直接调用原始 get_page_content，所有页面 is_cached=False
    - 任何异常向上抛出（由 Agent Loop 层捕获）
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 6.5_

  - [ ] 3.2 编写 PageDedupWrapper 属性测试 (`tests/context/reuse/test_dedup.py`)
    - **Property 7: 页面去重正确性** — 随机生成 read_pages + page_summaries + 请求页码，验证缓存/非缓存页面的 is_cached 和 text 标记正确
    - **Property 11: enable_page_dedup=False 直通** — enable=False 时所有页面 is_cached=False
    - 单元测试：全部已读、全部未读、混合场景、Page_Summary 缺失回退
    - _验证: 需求 3.1-3.6, 6.5_

- [ ] 4. 检查点 — Phase 0 完成
  - 运行 `pytest tests/context/reuse/ -v` 确保所有测试通过

- [ ] 5. 配置解析与 Prompt_Level_Precheck（Phase 1）
  - [ ] 5.1 实现配置解析辅助函数 (`src/context/reuse/builder.py` 或独立 `src/context/reuse/config.py`)
    - `resolve_config(func_param, env_var_name, default)` — 按优先级解析：函数参数 > 环境变量 > 默认值
    - 支持 bool 和 int 类型
    - 环境变量名：`CONTEXT_REUSE_ENABLED`、`CONTEXT_REUSE_CHAR_BUDGET`、`CONTEXT_REUSE_PAGE_DEDUP`
    - _需求: 6.1, 6.3, 6.4, 6.6, 6.7_

  - [ ] 5.2 编写配置解析属性测试 (`tests/context/reuse/test_config.py`)
    - **Property 10: 配置优先级** — 随机生成函数参数 + 环境变量组合，验证优先级正确
    - _验证: 需求 6.6, 6.7_

  - [ ] 5.3 创建 Prompt_Level_Precheck 指引文本
    - 在 `src/context/reuse/builder.py` 中定义 `PRECHECK_GUIDANCE` 常量字符串
    - 内容为设计文档中定义的 "## Context Reuse Guidance" 段落
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.6_

- [ ] 6. ContextManager 扩展（Phase 1）
  - [ ] 6.1 实现 `ContextManager.load_session()` 方法 (`src/context/manager.py`)
    - 读取 `<base_dir>/<session_id>/session.json`，恢复 `_session_id`、`_session_dir`
    - 重新初始化所有 Store（SessionStore、TurnStore、DocumentStore、EvidenceStore、TopicStore）和 Updater
    - 从 session.json 的 turns 列表恢复 `_turn_seq` 计数器
    - 会话目录不存在时抛出 FileNotFoundError
    - 新增 `session_dir` 只读属性
    - _需求: 10.2, 10.5_

  - [ ] 6.2 编写 ContextManager.load_session 测试 (`tests/context/reuse/test_session_reuse.py`)
    - **Property 15: 会话复用加载** — 创建含 N 轮的会话，load_session 后验证 turn_seq 恢复正确，create_turn 生成递增 turn_id
    - 单元测试：有效 session_id 加载、无效 session_id 抛异常
    - _验证: 需求 10.2, 10.5_

- [ ] 7. Agent Loop 集成（Phase 1）
  - [ ] 7.1 修改 `agentic_rag` 函数签名 (`src/agent/loop.py`)
    - 新增参数：`enable_context_reuse: bool = True`、`context_session_id: str | None = None`、`summary_char_budget: int = 4000`、`enable_page_dedup: bool = True`
    - 使用 `resolve_config` 解析最终配置值
    - _需求: 6.1, 6.2, 6.3, 6.4_

  - [ ] 7.2 实现会话复用接入点 (`src/agent/loop.py`)
    - 当 `context_session_id` 非空且会话目录存在时，调用 `ctx.load_session(context_session_id)` 加载已有会话
    - 否则保持原有 `ctx.create_session(doc_name)` 行为
    - load_session 异常时回退到 create_session，记录 warning
    - _需求: 10.2, 10.5_

  - [ ] 7.3 实现上下文摘要构建与注入接入点 (`src/agent/loop.py`)
    - 在消息组装阶段（messages 列表构建后），当 enable_context_reuse=True 时：
    - 创建 ContextReuseBuilder，调用 build_summary(doc_name)
    - 非空时：追加 PRECHECK_GUIDANCE 到 system_prompt 末尾，在 messages[0] 之后插入 `{"role": "system", "content": "[Context from previous turns]\n{context_text}"}`
    - 异常时：logger.exception + emit context_reuse_error 事件，继续执行
    - _需求: 2.1, 2.2, 2.3, 2.4, 5.1, 7.1_

  - [ ] 7.4 实现页面去重接入点 (`src/agent/loop.py`)
    - 在 tool call 执行阶段，当 enable_page_dedup=True 且 tool 为 get_page_content 时：
    - 创建 PageDedupWrapper，调用 get_page_content 替代原始 execute_tool
    - 异常时：回退到原始 execute_tool + emit context_reuse_error 事件
    - _需求: 3.1, 3.2, 7.2_

  - [ ] 7.5 实现页面摘要自动生成接入点 (`src/agent/loop.py`)
    - 在 get_page_content 工具调用结果返回后（record_tool_call 之后）：
    - 遍历 result["content"]，对 is_cached=False 的页面调用 PageSummaryGenerator.generate + save
    - 异常时：logger.exception，不影响主流程
    - _需求: 4.1, 4.5, 7.3_

  - [ ] 7.6 编写消息组装属性测试 (`tests/context/reuse/test_message_assembly.py`)
    - **Property 4: 消息注入结构正确性** — 有上下文时验证 messages 列表结构（system prompt 含 Precheck、第二条为上下文摘要消息）
    - **Property 5: 空上下文保持原始行为** — 无上下文或 enable=False 时验证消息列表与原始一致
    - _验证: 需求 2.1-2.4, 5.1, 6.2_

  - [ ] 7.7 编写 Sidecar 容错测试 (`tests/context/reuse/test_sidecar.py`)
    - **Property 12: Sidecar 容错** — Mock Builder/Dedup/Generator 抛出异常，验证 Agent Loop 捕获并继续执行，emit context_reuse_error 事件
    - _验证: 需求 7.1, 7.2, 7.3, 7.4_

- [ ] 8. 检查点 — Phase 1 完成
  - 运行 `pytest tests/context/reuse/ -v` 确保所有测试通过
  - 运行 `pytest tests/ -v` 确保已有测试不受影响

- [ ] 9. Web API 扩展（Phase 2）
  - [ ] 9.1 扩展 QARequest 模型 (`src/web/app.py`)
    - 新增字段：`enable_context_reuse: bool = True`、`context_session_id: str | None = None`
    - _需求: 10.1_

  - [ ] 9.2 修改 `/api/qa` 和 `/api/qa/stream` 端点 (`src/web/app.py`)
    - 将 `enable_context_reuse` 和 `context_session_id` 传递给 `agentic_rag` 调用
    - 响应中新增 `context_session_id` 字段（从 agentic_rag 返回值或 ctx_session_id 获取）
    - _需求: 10.1, 10.2, 10.3, 10.4_

- [ ] 10. 最终检查点
  - 运行 `pytest tests/ -v` 确保全部测试通过
  - 手动验证：启动 Web API，发送两轮问答请求（第二轮传入 context_session_id），确认上下文复用生效

## 备注

- 所有新增代码中使用 `doc_name` 作为参数名，调用已有 DocumentStore 时传入 `doc_name` 作为 `doc_id` 参数值
- Page_Summary 生成使用前段截断法（按段落累加至 50% 字符限制），不使用 LLM
- `total_chars` 仅出现在 `build_summary_dict` 返回的字典和 `logger.info` 日志中，不出现在 `build_summary` 返回的 Markdown 文本中
- Summary_Char_Budget 单位为字符数，默认 4000
- Prompt_Level_Precheck 为纯 prompt 工程，不涉及独立代码模块
- 属性测试使用 Hypothesis，每个 Property 至少 100 次迭代，注释格式：`# Feature: context-reuse-enhancement, Property {N}: {text}`
