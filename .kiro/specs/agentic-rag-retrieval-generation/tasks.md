# 实施计划：Agentic RAG 检索增强生成系统

## 概述

按照分阶段方式实现基于 PageIndex 范式的 Agentic RAG 系统。Phase 0 实现工具层和数据准备，Phase 1 实现 Agent 循环和 MVP 问答，Phase 2 实现引用系统和答案质量提升，Phase 3 实现评测系统，Phase 4 预留扩展点。所有代码使用 Python 3.10+，数据模型使用 pydantic，属性测试使用 hypothesis。

## 任务

- [x] 1. 项目结构与数据模型
  - [x] 1.1 创建项目目录结构和 `__init__.py` 文件
    - 创建 `src/`、`src/tools/`、`src/agent/`、`src/agent/prompts/`、`tests/` 目录
    - 创建所有 `__init__.py` 文件
    - _需求: 全局_

  - [x] 1.2 实现数据模型 (`src/models.py`)
    - 使用 pydantic BaseModel 定义 Citation、ToolCallRecord、RAGResponse、TokenUsage、ToolCall、LLMResponse
    - 定义评测模型 TestCase、EvalResult
    - 定义扩展预留模型 ProtocolState、ProtocolTransition、ProtocolStateMachine、ProtocolField、ProtocolMessage、ProtocolSchema
    - models 模块不依赖 tools 或 agent 模块
    - _需求: 11.1, 11.2, 11.3, 11.4, 12.3_

  - [x] 1.3 编写数据模型属性测试
    - **Property 20: 数据模型字段完整性**
    - 验证 RAGResponse 包含 answer、answer_clean、citations、trace、pages_retrieved、total_turns 字段
    - 验证 Citation 包含 doc_name、page、context 字段
    - 验证 ToolCallRecord 包含 turn、tool、arguments、result_summary 字段
    - **验证: 需求 11.1, 11.2, 11.3**

  - [x] 1.4 编写 TestCase 序列化 round-trip 属性测试
    - **Property 19: 测试用例加载 round-trip**
    - 验证 TestCase 列表序列化为 JSON 后再加载回来，字段值完全一致
    - **验证: 需求 10.1**

- [-] 2. 文档注册表与结构工具 (Phase 0)
  - [x] 2.1 实现文档注册表 (`src/tools/registry.py`)
    - 定义 DocConfig TypedDict（chunks_dir, content_dir, total_pages）
    - 实现 DOC_REGISTRY 字典，包含 FC-LS.pdf 和 rfc5880-BFD.pdf 的配置
    - 实现 `get_doc_config(doc_name)` 函数，未注册文档返回包含可用文档列表的错误字典
    - _需求: 1.1, 1.2, 1.3_

  - [x] 2.2 编写注册表属性测试
    - **Property 1: 注册表查找返回完整配置**
    - 对任意已注册 doc_name，验证返回包含 chunks_dir（字符串）、content_dir（字符串）、total_pages（正整数）
    - **验证: 需求 1.1, 1.2**

  - [x] 2.3 编写未注册文档属性测试
    - **Property 2: 未注册文档名返回可用列表**
    - 对任意不在注册表中的字符串，验证返回包含 error 字段且错误信息包含所有已注册文档名
    - **验证: 需求 1.3**

  - [x] 2.4 实现文档结构工具 (`src/tools/document_structure.py`)
    - 实现 `get_document_structure(doc_name, part=1)` 函数
    - 从 `chunks_3/{doc_stem}/part_{part:04d}.json` 加载索引树
    - 返回 structure、next_steps、pagination 三个字段
    - part=1 时附加 doc_info（total_pages, total_parts）
    - part 越界返回 error 和 valid_range
    - 从 manifest.json 读取 total_parts
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 2.5 编写结构工具属性测试（Property 3-6）
    - **Property 3: 结构工具返回完整响应** — 验证有效调用返回 structure（列表）、next_steps（字符串）、pagination（含 current_part 和 total_parts）
    - **Property 4: 首次结构调用附加文档信息** — 验证 part=1 时返回 doc_info 字段
    - **Property 5: 结构工具越界返回错误与有效范围** — 验证越界 part 返回 error 和有效范围
    - **Property 6: 结构工具默认 part 等价于 part=1** — 验证无 part 参数与 part=1 结果一致
    - **验证: 需求 2.1, 2.2, 2.3, 2.4, 2.5**

- [x] 3. 页面内容工具 (Phase 0)
  - [x] 3.1 实现页码解析函数 (`src/tools/page_content.py` 中的 `parse_pages`)
    - 支持单页格式 "7"、范围格式 "7-11"、逗号分隔格式 "7,9,11"
    - 返回排序后的整数页码列表
    - 无效格式返回空列表或抛出 ValueError
    - _需求: 3.2_

  - [x] 3.2 编写页码解析属性测试
    - **Property 7: 页码解析三种格式正确性**
    - 对任意有效页码 n，验证三种格式分别返回正确的页码列表，且元素均为正整数
    - **验证: 需求 3.2**

  - [x] 3.3 实现页面内容工具 (`src/tools/page_content.py`)
    - 实现 `get_page_content(doc_name, pages)` 函数
    - 从 content_dir 下的 `content_{start}_{end}.json` 文件加载页面数据
    - 每页返回 page、text、tables、images 字段
    - 单次请求超过 10 页返回错误
    - 单页文本 > 4000 字符在段落边界截断，附加截断标注
    - 表格始终完整返回（markdown 格式），不截断
    - next_steps 包含 `<cite doc="..." page="N"/>` 引用格式提示
    - 页码越界返回错误和有效范围
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [x] 3.4 编写内容工具属性测试（Property 8-12）
    - **Property 8: 内容工具返回完整页面字段** — 验证每个页面包含 page、text、tables、images 四个字段
    - **Property 9: 内容工具拒绝超过 10 页的请求** — 验证超过 10 页返回 error 字典
    - **Property 10: 内容截断策略** — 验证文本截断至 ≤4000 字符附近并含截断标注，表格始终完整
    - **Property 11: 内容工具 next_steps 包含引用格式提示** — 验证 next_steps 包含 `<cite` 关键词
    - **Property 12: 内容工具越界页码返回错误** — 验证越界页码返回 error 和有效范围
    - **验证: 需求 3.1, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**

- [x] 4. Tool Schema 定义 (Phase 0)
  - [x] 4.1 实现 Tool Schema (`src/tools/schemas.py`)
    - 定义 TOOL_SCHEMAS 列表（OpenAI function calling 格式）
    - get_document_structure 的 description 包含"通过阅读摘要判断章节相关性"引导措辞
    - get_page_content 的 description 包含"单次请求不超过 10 页"约束和"页码范围从目录树节点的 start_index 和 end_index 获得"工作流关系
    - 实现 `get_tool_schemas()` 和 `convert_to_anthropic_format()` 函数
    - _需求: 4.1, 4.2, 4.3, 4.4, 5.4_

  - [x] 4.2 编写 Schema 格式转换属性测试
    - **Property 13: Schema 格式转换正确性**
    - 验证 convert_to_anthropic_format 转换后包含 name 和 input_schema 字段，且 properties 一致
    - **验证: 需求 5.3, 5.4**

- [x] 5. 检查点 — Phase 0 完成
  - 确保所有测试通过，如有问题请向用户确认。

- [ ] 6. LLM 适配层 (Phase 1)
  - [x] 6.1 实现 LLM Adapter (`src/agent/llm_adapter.py`)
    - 实现 LLMAdapter 类，支持 provider="openai" 和 provider="anthropic"
    - 实现 `chat_with_tools(messages, tools)` 异步方法，返回统一的 LLMResponse
    - OpenAI: 将 Tool Schema 直接传递，解析 message.tool_calls 为统一 ToolCall 列表
    - Anthropic: 调用 convert_to_anthropic_format 转换 schema，解析 content 中 type=="tool_use" 的 block
    - 实现 `make_tool_result_message(tool_call_id, result)` 方法
    - OpenAI 格式: `{"role": "tool", "tool_call_id": ..., "content": ...}`
    - Anthropic 格式: `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}`
    - Anthropic 适配层自动将 system message 拆分为独立 system 参数
    - 支持 LLM 单次返回多个并行 tool call
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 6.2 编写 Tool Result 消息格式属性测试
    - **Property 14: Tool Result 消息格式正确性**
    - 验证 OpenAI 适配器生成 `{"role": "tool", "tool_call_id": ...}` 格式
    - 验证 Anthropic 适配器生成 `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ...}]}` 格式
    - **验证: 需求 5.5, 5.6**

- [ ] 7. Agent 循环核心 (Phase 1)
  - [x] 7.1 实现 System Prompt 加载和 prompt 文件 (`src/agent/prompts/qa_system.txt`)
    - 创建 qa_system.txt，包含工作流引导、事实约束、交叉引用跟踪指引、信息不足处理指引
    - 实现 `load_system_prompt(prompt_file)` 函数从 src/agent/prompts/ 目录加载
    - 创建 extraction_system.txt 占位文件（Phase 4 预留）
    - _需求: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 7.2 实现 Agent Loop (`src/agent/loop.py`)
    - 实现 `agentic_rag(query, doc_name, model, max_turns)` 异步函数
    - 组装 system_prompt + user_query + tool_schemas 为初始消息列表
    - LLM 返回 tool_call 时执行工具并追加结果到消息列表
    - LLM 返回纯文本时作为最终答案返回
    - 记录每轮 ToolCallRecord 到 trace
    - 达到 max_turns 时终止并返回限制提示
    - 实现 `execute_tool(name, arguments)` 路由器，未知工具返回 error 字典不抛异常
    - _需求: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 12.1, 12.2_

  - [ ]* 7.3 编写未知工具名属性测试
    - **Property 15: 未知工具名返回错误字典**
    - 对任意不在已注册工具列表中的字符串，验证 execute_tool 返回包含 error 字段的字典，不抛异常
    - **验证: 需求 6.7**

  - [ ]* 7.4 编写 Agent Loop 集成测试（mock LLM）
    - 测试 mock LLM 返回 tool_call 时正确执行工具
    - 测试 mock LLM 返回 text 时正确终止循环
    - 测试达到 max_turns 时返回限制提示
    - _需求: 6.2, 6.3, 6.5_

- [ ] 8. CLI 入口 (Phase 1)
  - [x] 8.1 实现 CLI 入口 (`src/main.py`)
    - 接受 --doc（必填）、--query（必填）、--model（可选）、--verbose（可选）参数
    - --verbose 模式打印完整 tool call trace
    - 输出最终答案文本
    - 支持 `python -m src.main` 方式运行
    - _需求: 9.1, 9.2, 9.3, 9.4_

- [x] 9. 检查点 — Phase 1 完成
  - 确保所有测试通过，如有问题请向用户确认。

- [ ] 10. 引用系统 (Phase 2)
  - [x] 10.1 实现 Citation 模块 (`src/agent/citation.py`)
    - 实现 `extract_citations(answer)` — 使用正则 `r'<cite\s+doc="([^"]+)"\s+page="(\d+)"\s*/>'` 解析所有 cite 标签
    - 实现 `validate_citations(citations, pages_retrieved)` — 验证引用页码是否在检索列表中，返回警告列表
    - 实现 `clean_answer(answer)` — 去除 cite 标签返回纯文本
    - _需求: 8.4, 8.5, 8.6_

  - [ ]* 10.2 编写 Citation 属性测试（Property 16-18）
    - **Property 16: Citation 提取正确解析所有 cite 标签** — 验证返回的 Citation 数量与标签数量一致，doc_name 和 page 值正确
    - **Property 17: Citation 验证识别未检索页码** — 验证对未检索页码生成警告，对已检索页码不生成警告
    - **Property 18: 清理答案去除 cite 标签保留文本** — 验证结果不含 `<cite` 标签且非标签文本完整保留
    - **验证: 需求 8.4, 8.5, 8.6**

  - [x] 10.3 在 System Prompt 中添加引用规则
    - 在 qa_system.txt 中追加引用格式要求：`<cite doc="文档名" page="页码"/>`
    - 要求使用单页页码引用，不使用范围
    - 要求只引用通过 get_page_content 实际读取过的页面
    - _需求: 8.1, 8.2, 8.3_

  - [x] 10.4 在 Agent Loop 中集成引用处理
    - 在 agentic_rag 函数中，LLM 返回最终答案后调用 extract_citations 和 clean_answer
    - 将 citations、answer_clean、pages_retrieved 填充到 RAGResponse
    - 调用 validate_citations 生成警告（日志输出）
    - _需求: 8.4, 8.5, 8.6_

- [x] 11. 检查点 — Phase 2 完成
  - 确保所有测试通过，如有问题请向用户确认。

- [ ] 12. 评测系统 (Phase 3)
  - [x] 12.1 创建评测测试集 (`data/eval/test_questions.json`)
    - 为 BFD 和 FC-LS 编写测试用例，每个包含 id、doc_name、query、type、expected_pages、key_points
    - 覆盖 format、state_machine、procedure、definition、cross_reference 五种问题类型
    - _需求: 10.1, 10.5_

  - [x] 12.2 实现评测脚本 (`src/evaluate.py`)
    - 实现 `evaluate_all(test_set_path, model)` 异步函数
    - 从 JSON 文件加载 TestCase 列表
    - 对每个用例调用 agentic_rag 获取答案
    - 计算 key_points 覆盖率、引用有效率、总轮次数、检索页码命中率
    - 输出汇总指标和每个用例的详细结果
    - 指标目标：key_points 覆盖率 > 80%，引用有效率 > 90%，平均轮次 4-8，页码命中率 > 70%
    - _需求: 10.1, 10.2, 10.3, 10.4, 10.5_

- [ ] 13. 扩展预留 (Phase 4)
  - [x] 13.1 创建提取型 System Prompt 占位文件
    - 在 `src/agent/prompts/extraction_system.txt` 中编写协议知识提取的 system prompt
    - 引导 LLM 从文档中系统性提取状态机、消息格式等结构化信息
    - 输出格式为 JSON，与 ProtocolSchema 模型对应
    - _需求: 12.1_

  - [x] 13.2 验证 Agent Loop 扩展性
    - 确认 execute_tool 路由器支持添加新工具（如 search_structure、get_document_image）无需修改循环核心
    - 确认切换 prompt_file 参数即可改变 LLM 行为模式
    - _需求: 12.1, 12.2_

- [x] 14. 最终检查点
  - 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的任务为可选任务，可跳过以加速 MVP 开发
- 每个任务引用了具体的需求编号以确保可追溯性
- 检查点任务确保增量验证
- 属性测试验证设计文档中定义的 20 个正确性属性
- 单元测试验证具体示例和边界条件
- 所有代码使用 Python 3.10+，数据模型使用 pydantic，属性测试使用 hypothesis
