# 需求文档

## 简介

本系统实现基于 PageIndex 范式的 Agentic RAG（检索增强生成）系统。核心思想是 LLM 通过 Tool-Use（函数调用）自主导航文档索引树，按需提取页面内容，推理判断信息充足性，最终生成带精确页码引用的答案。系统面向协议技术文档（如 FC-LS、BFD RFC），支持 OpenAI 和 Anthropic 两种 LLM 提供商。

系统智能分布在三个层面：索引质量（离线生成的 summary 和树结构）、Tool 响应设计（代码侧的字段选择、next_steps 措辞、内容截断策略）、LLM 推理（在线的导航路径选择和答案生成）。

## 术语表

- **Agent_Loop**: 核心循环引擎，负责将用户查询和工具定义发送给 LLM，转发 tool call 执行结果，直到 LLM 返回最终文本答案
- **Tool_Schema**: 工具的函数调用定义，包含名称、描述和参数规范，供 LLM 理解工具用途并生成调用
- **Document_Registry**: 文档注册表，维护 doc_name 到数据文件路径的映射关系
- **Structure_Tool**: `get_document_structure` 工具，返回文档目录树的分块索引
- **Content_Tool**: `get_page_content` 工具，返回指定页码的实际文档内容
- **LLM_Adapter**: LLM 适配层，统一 OpenAI 和 Anthropic 的 tool calling 接口差异
- **System_Prompt**: 系统提示词，引导 LLM 的导航行为、引用规范和答案生成策略
- **Citation**: 答案中的页码引用标签，格式为 `<cite doc="..." page="N"/>`
- **Tool_Call_Trace**: Agent 循环中所有 tool call 的完整记录，包含调用参数和结果摘要
- **RAG_Response**: 系统最终输出，包含答案文本、引用列表、tool call 记录和检索页码
- **Evaluation_Script**: 自动评测脚本，基于测试集量化系统在关键指标上的表现
- **next_steps**: 工具返回结果中的导航提示字段，引导 LLM 执行下一步操作

## 需求

### 需求 1：文档注册表

**用户故事：** 作为开发者，我希望通过文档名称查找对应的数据文件路径，以便工具函数能正确定位索引和内容数据。

#### 验收标准

1. THE Document_Registry SHALL 维护 doc_name 到 chunks_dir、content_dir 和 total_pages 的映射关系
2. WHEN 工具函数收到一个 doc_name 参数时，THE Document_Registry SHALL 返回该文档对应的数据路径配置
3. IF 提供的 doc_name 在 Document_Registry 中不存在，THEN THE Document_Registry SHALL 返回包含所有可用文档名称列表的错误信息

### 需求 2：文档结构工具

**用户故事：** 作为 LLM Agent，我希望获取文档的目录树索引，以便通过阅读章节标题和摘要判断哪些章节与用户问题相关。

#### 验收标准

1. WHEN 收到有效的 doc_name 和 part 参数时，THE Structure_Tool SHALL 从对应的 `chunks_3/{doc}/part_{part:04d}.json` 文件加载并返回该分块的索引树
2. THE Structure_Tool SHALL 在返回结果中包含 structure（节点树）、next_steps（导航提示）和 pagination（分页信息，含当前 part 编号和总 part 数）三个字段
3. WHEN part 参数为 1 时，THE Structure_Tool SHALL 在响应中附加文档整体信息，包括总页数和总 part 数
4. IF part 参数超出有效范围，THEN THE Structure_Tool SHALL 返回明确的错误提示和有效的 part 范围
5. WHEN part 参数未提供时，THE Structure_Tool SHALL 使用默认值 1

### 需求 3：页面内容工具

**用户故事：** 作为 LLM Agent，我希望获取文档指定页码的实际内容（文本、表格、图片），以便基于真实文档内容回答用户问题。

#### 验收标准

1. WHEN 收到有效的 doc_name 和 pages 参数时，THE Content_Tool SHALL 从对应的 `content_*.json` 文件加载并返回指定页码的内容
2. THE Content_Tool SHALL 支持三种页码格式：单页（如 "7"）、连续范围（如 "7-11"）和逗号分隔（如 "7,9,11"）
3. THE Content_Tool SHALL 对每个页面返回 page（页码）、text（文本内容）、tables（表格内容）和 images（图片信息）字段
4. IF 单次请求的页数超过 10 页，THEN THE Content_Tool SHALL 返回错误提示，要求 LLM 分批请求
5. WHILE 单页文本内容超过 4000 字符时，THE Content_Tool SHALL 在段落边界处截断文本，并附加截断标注
6. THE Content_Tool SHALL 始终完整返回表格内容（markdown 格式），不对表格进行截断
7. THE Content_Tool SHALL 在返回结果的 next_steps 字段中包含引用格式提示（`<cite doc="..." page="N"/>`）
8. IF 请求的页码超出文档总页数范围，THEN THE Content_Tool SHALL 返回明确的错误提示和有效页码范围

### 需求 4：工具 Schema 定义

**用户故事：** 作为开发者，我希望有标准化的工具 Schema 定义，以便 LLM 能通过函数调用接口正确理解和调用工具。

#### 验收标准

1. THE Tool_Schema SHALL 为 get_document_structure 和 get_page_content 分别定义符合 OpenAI function calling 格式的 Schema
2. THE Tool_Schema SHALL 在 get_document_structure 的 description 中包含"通过阅读摘要判断章节相关性"的引导措辞
3. THE Tool_Schema SHALL 在 get_page_content 的 description 中包含"单次请求不超过 10 页"的约束说明
4. THE Tool_Schema SHALL 在 get_page_content 的 description 中说明"页码范围从目录树节点的 start_index 和 end_index 获得"，建立两个工具之间的工作流关系

### 需求 5：LLM 适配层

**用户故事：** 作为开发者，我希望有统一的 LLM 调用接口，以便 Agent Loop 无需关心底层是 OpenAI 还是 Anthropic 的 API 差异。

#### 验收标准

1. THE LLM_Adapter SHALL 提供统一的 `chat_with_tools` 异步方法，接受 messages 列表和 tools 列表作为参数
2. THE LLM_Adapter SHALL 返回统一的响应结构，包含 has_tool_calls（布尔值）、tool_calls（调用列表，含 name、arguments、id）、text（最终文本答案）和 usage（token 用量）字段
3. WHEN provider 为 OpenAI 时，THE LLM_Adapter SHALL 将 Tool Schema 转换为 OpenAI 格式，并将 OpenAI 的 tool call 响应解析为统一结构
4. WHEN provider 为 Anthropic 时，THE LLM_Adapter SHALL 将 Tool Schema 转换为 Anthropic 的 input_schema 格式，并将 Anthropic 的 tool_use content block 解析为统一结构
5. WHEN provider 为 OpenAI 时，THE LLM_Adapter SHALL 使用 `{"role": "tool", "tool_call_id": ...}` 格式构造 tool result 消息
6. WHEN provider 为 Anthropic 时，THE LLM_Adapter SHALL 使用 `{"role": "user", "content": [{"type": "tool_result", ...}]}` 格式构造 tool result 消息
7. THE LLM_Adapter SHALL 支持 LLM 在单次响应中返回多个并行 tool call

### 需求 6：Agent 循环核心

**用户故事：** 作为用户，我希望提出一个关于协议文档的问题后，系统能自主导航文档并生成答案，无需我手动指定查找哪些章节。

#### 验收标准

1. WHEN 收到用户查询和文档名称时，THE Agent_Loop SHALL 将查询、System_Prompt 和 Tool_Schema 组装为初始消息列表，发送给 LLM
2. WHEN LLM 返回 tool_call 时，THE Agent_Loop SHALL 执行对应的工具函数，将结果作为 tool result 消息追加到消息列表，并再次调用 LLM
3. WHEN LLM 返回纯文本响应（无 tool_call）时，THE Agent_Loop SHALL 将该文本作为最终答案返回
4. THE Agent_Loop SHALL 记录每一轮 tool call 的完整 trace，包含轮次编号、工具名称、调用参数和结果摘要
5. IF Agent 循环达到 max_turns 上限（默认 15）时，THEN THE Agent_Loop SHALL 终止循环并返回包含"达到最大轮次限制"提示的响应
6. THE Agent_Loop SHALL 不执行任何检索决策逻辑，所有导航和检索决策完全由 LLM 通过 tool call 驱动
7. WHEN 收到未知的工具名称时，THE Agent_Loop SHALL 返回包含错误信息的 tool result，而非抛出异常

### 需求 7：System Prompt 设计

**用户故事：** 作为开发者，我希望通过精心设计的 System Prompt 引导 LLM 的导航行为，以便 LLM 能高效地找到相关章节并生成高质量答案。

#### 验收标准

1. THE System_Prompt SHALL 指导 LLM 首先调用 get_document_structure 查看目录树，再根据摘要判断相关性后调用 get_page_content 获取内容
2. THE System_Prompt SHALL 包含"只基于实际读取到的文档内容回答，不编造信息"的约束
3. THE System_Prompt SHALL 包含交叉引用跟踪指引，指导 LLM 在遇到"see section X"等引用时主动跳转查找被引用章节
4. THE System_Prompt SHALL 包含信息不足时的处理指引，要求 LLM 明确说明已找到的信息和缺失的部分
5. THE System_Prompt SHALL 从外部文件（如 `prompts/qa_system.txt`）加载，支持独立于代码修改

### 需求 8：引用系统

**用户故事：** 作为用户，我希望答案中的关键信息带有精确的页码引用，以便我能快速定位原文验证答案的准确性。

#### 验收标准

1. THE System_Prompt SHALL 要求 LLM 对答案中的每个关键信息点使用 `<cite doc="文档名" page="页码"/>` 格式标注来源
2. THE System_Prompt SHALL 要求使用单页页码引用（page="7"），不使用页码范围
3. THE System_Prompt SHALL 要求 LLM 只引用通过 get_page_content 实际读取过的页面
4. WHEN 答案文本包含 cite 标签时，THE Citation 模块 SHALL 使用正则表达式解析所有 `<cite doc="..." page="..."/>` 标签，提取文档名和页码
5. WHEN 提取到引用列表后，THE Citation 模块 SHALL 验证每个引用的页码是否在 Agent 实际检索过的页码列表中，并对未检索页码的引用生成警告
6. THE RAG_Response SHALL 同时包含带 cite 标签的原始答案和去除 cite 标签的纯文本答案

### 需求 9：CLI 入口

**用户故事：** 作为用户，我希望通过命令行界面提问并获取答案，以便快速测试和使用系统。

#### 验收标准

1. THE CLI SHALL 接受 `--doc`（文档名称，必填）、`--query`（用户问题，必填）、`--model`（LLM 模型名称，可选）和 `--verbose`（详细模式，可选）参数
2. WHEN `--verbose` 参数启用时，THE CLI SHALL 在输出答案前打印完整的 tool call trace
3. WHEN 执行完成时，THE CLI SHALL 输出最终答案文本
4. THE CLI SHALL 支持通过 `python -m src.main` 方式运行

### 需求 10：评测系统

**用户故事：** 作为开发者，我希望有自动化的评测系统来量化系统表现，以便通过数据驱动的方式发现问题并优化。

#### 验收标准

1. THE Evaluation_Script SHALL 从 JSON 格式的测试集文件加载测试用例，每个用例包含 id、doc_name、query、type、expected_pages 和 key_points 字段
2. WHEN 执行评测时，THE Evaluation_Script SHALL 对每个测试用例调用 Agent_Loop 获取答案，并计算以下指标：key_points 覆盖率、引用有效率、总轮次数和检索页码命中率
3. THE Evaluation_Script SHALL 将 key_points 覆盖率目标设为大于 80%，引用有效率目标设为大于 90%，平均轮次数目标设为 4 至 8 轮，检索页码命中率目标设为大于 70%
4. WHEN 评测完成时，THE Evaluation_Script SHALL 输出汇总指标和每个用例的详细结果
5. THE Evaluation_Script SHALL 支持覆盖 format、state_machine、procedure、definition 和 cross_reference 五种问题类型的测试用例

### 需求 11：数据模型

**用户故事：** 作为开发者，我希望有清晰定义的数据模型，以便系统各模块之间通过结构化数据交互。

#### 验收标准

1. THE RAG_Response 模型 SHALL 包含 answer（原始答案）、answer_clean（纯文本答案）、citations（引用列表）、trace（tool call 记录）、pages_retrieved（检索页码列表）和 total_turns（总轮次）字段
2. THE Citation 模型 SHALL 包含 doc_name（文档名）、page（页码）和 context（引用所在文本片段）字段
3. THE ToolCallRecord 模型 SHALL 包含 turn（轮次）、tool（工具名）、arguments（调用参数）和 result_summary（结果摘要）字段
4. THE models 模块 SHALL 不依赖 tools 或 agent 模块，作为所有模块的共享数据定义

### 需求 12：毕设扩展预留

**用户故事：** 作为开发者，我希望系统架构支持未来扩展为协议知识提取系统，以便复用现有的 Tool 和 Agent Loop 基础设施。

#### 验收标准

1. THE Agent_Loop SHALL 支持通过切换 System_Prompt 文件来改变 LLM 的行为模式（从问答模式切换为提取模式），无需修改循环代码
2. THE Agent_Loop SHALL 支持在 Tool_Schema 列表和工具路由中添加新工具（如 search_structure、get_document_image），无需修改循环核心逻辑
3. THE models 模块 SHALL 预定义 ProtocolStateMachine、ProtocolMessage 和 ProtocolSchema 等数据模型，每个模型包含 source_pages 字段以支持引用追溯
