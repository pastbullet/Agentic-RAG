# 需求文档：上下文复用增强（Context Reuse Enhancement）

## 简介

本功能为现有 Agentic RAG 系统新增上下文复用层。当前系统的 Context Management System 已经完整追踪了多轮对话中的文档访问状态（已读页面、已探索节点、已提取证据等），但这些信息未被反馈给 LLM。每次新提问时，LLM 从零开始探索文档结构、重复读取已读页面，造成不必要的 token 消耗和延迟。

本功能通过读取已有的上下文状态，构建结构化摘要并注入 LLM 消息流，实现已读内容复用、页面去重和 Prompt 级充分性预检，从而显著减少重复检索，提升多轮对话效率。

## 术语表

- **Context_Reuse_Builder**：上下文复用构建器，负责从 Context Management System 读取状态并生成结构化上下文摘要
- **Context_Summary**：上下文摘要，由三类数据源组装而成的结构化对象（见 Schema 定义），最终序列化为文本注入 LLM 消息流
- **Node_Summary**：节点摘要，来源于文档结构树节点的 `summary` 字段（已存在于 `node_xxxx.json`），属于章节级摘要，描述该章节的主题和覆盖范围。由 `get_document_structure` 工具返回并通过 Context Management System 持久化
- **Page_Summary**：页面摘要，在 `get_page_content` 返回页面内容后由规则方法（文本截断 + 关键句提取）自动生成，属于页面级内容摘要。存储于 `page_summaries/page_<N>.json`，用于页面去重时替代原始全文
- **Evidence**：证据条目，从文档页面中提取的与用户问题直接相关的文本片段（已存在于 `ev_xxxxxx.json`）。与 Page_Summary 的区别：Evidence 是问题驱动的精确片段，Page_Summary 是页面内容的通用压缩
- **Agent_Loop**：Agent 循环，`src/agent/loop.py::agentic_rag` 函数，协调 LLM 调用和工具执行
- **Context_Manager**：上下文管理器，`src/context/manager.py::ContextManager`，管理会话状态的高层接口
- **Document_Store**：文档状态存储，追踪 visited_parts 和 read_pages
- **Evidence_Store**：证据存储，管理从文档中提取的证据片段
- **Node_State**：节点状态，文档结构树中每个节点的访问和阅读状态
- **Page_Deduplication**：页面去重，当 LLM 请求已读页面时返回 Page_Summary 而非重新读取原文
- **Prompt_Level_Precheck**：Prompt 级充分性预检，通过在 system prompt 中追加指引段落，指示 LLM 在调用工具前先评估已有上下文是否足以回答问题。这是纯 prompt 工程技术，不涉及独立的代码模块或 API
- **Sidecar_Pattern**：旁路模式，上下文复用层的任何异常不影响主流程答案生成
- **Summary_Char_Budget**：摘要字符预算，Context_Summary 序列化为文本后允许的最大字符数（单位：字符，非 token）。默认值 4000 字符

## 数据 Schema 定义

### Context_Summary 结构化 Schema

```json
{
  "doc_name": "string — 文档标识符",
  "explored_structure": {
    "visited_parts": "[int] — 已访问的 part 编号列表",
    "nodes": [
      {
        "node_id": "string — 节点 ID",
        "title": "string — 节点标题",
        "start_index": "int — 起始页码",
        "end_index": "int — 结束页码",
        "summary": "string | null — Node_Summary（章节级摘要）",
        "status": "string — discovered | reading | read_complete"
      }
    ]
  },
  "read_pages": {
    "page_numbers": "[int] — 已读页码列表",
    "page_summaries": [
      {
        "page_num": "int — 页码",
        "summary_text": "string — Page_Summary（页面级摘要）"
      }
    ]
  },
  "evidences": [
    {
      "evidence_id": "string — 证据 ID",
      "source_page": "int — 来源页码",
      "content": "string — 证据文本片段",
      "extracted_in_turn": "string — 提取该证据的轮次 ID"
    }
  ],
  "total_chars": "int — 序列化后的总字符数"
}
```

### Page_Summary JSON Schema

```json
{
  "page_num": "int — 页码",
  "doc_name": "string — 文档标识符",
  "summary_text": "string — 页面内容摘要（规则方法生成）",
  "original_length": "int — 原始页面文本字符数",
  "summary_length": "int — 摘要文本字符数",
  "generated_at": "string — ISO 8601 时间戳",
  "source_turn_id": "string — 首次读取该页面的轮次 ID"
}
```

### 三类摘要的职责边界

| 维度 | Node_Summary | Page_Summary | Evidence |
|------|-------------|--------------|----------|
| 粒度 | 章节/节 | 单页 | 片段（1~3 句） |
| 来源 | `get_document_structure` 返回 | `get_page_content` 返回后规则生成 | Agent 循环中 LLM 提取 |
| 存储位置 | `nodes/node_xxxx.json` 的 `summary` 字段 | `page_summaries/page_<N>.json` | `evidences/ev_xxxxxx.json` |
| 生成方式 | 文档预处理阶段（已有） | 文本截断 + 关键句提取（新增） | LLM 在回答中引用（已有） |
| 用途 | Context_Summary 的「已探索结构」区段 | 页面去重时替代原始全文；Context_Summary 的「已读页面」区段 | Context_Summary 的「已提取证据」区段 |
| 是否问题相关 | 否（通用结构描述） | 否（通用内容压缩） | 是（与特定问题相关） |

## 需求

### 需求 1：上下文摘要构建

**用户故事：** 作为系统开发者，我希望系统能从已有的上下文状态中构建结构化摘要，以便 LLM 在新一轮对话中了解之前已探索和阅读的内容。

#### 验收标准

1. WHEN 新一轮对话开始且存在活跃会话，THE Context_Reuse_Builder SHALL 从 Document_Store 读取 document_state.json 中的 visited_parts 和 read_pages 列表
2. WHEN 新一轮对话开始且存在活跃会话，THE Context_Reuse_Builder SHALL 从 Node_State 文件中读取所有状态为 "read_complete" 或 "reading" 的节点，提取其 node_id、title、start_index、end_index、summary（Node_Summary）和 status
3. WHEN 新一轮对话开始且存在活跃会话，THE Context_Reuse_Builder SHALL 从 Evidence_Store 读取所有已提取的证据条目，提取其 evidence_id、content、source_page 和 extracted_in_turn
4. WHEN 新一轮对话开始且存在活跃会话，THE Context_Reuse_Builder SHALL 从 `page_summaries/` 目录读取所有已生成的 Page_Summary 文件
5. THE Context_Reuse_Builder SHALL 将上述四类数据组装为一个符合 Context_Summary Schema 的结构化字典
6. THE Context_Summary 序列化为文本时 SHALL 包含三个 Markdown 区段：「## 已探索的文档结构」（含 Node_Summary）、「## 已读页面摘要」（含 Page_Summary）和「## 已提取的证据」（含 Evidence）
7. IF Document_Store、Evidence_Store 或 page_summaries 目录读取失败，THEN THE Context_Reuse_Builder SHALL 对失败的数据源返回空列表，其余数据源正常组装，并记录警告日志

### 需求 2：上下文摘要注入

**用户故事：** 作为系统开发者，我希望构建好的上下文摘要能被注入到 LLM 的消息流中，以便 LLM 在回答新问题时能利用之前的检索成果。

#### 验收标准

1. WHEN Context_Summary 非空，THE Agent_Loop SHALL 在 system prompt 之后、用户历史消息之前插入一条 role="system" 的上下文摘要消息
2. THE 上下文摘要消息 SHALL 以明确的标记 `[Context from previous turns]` 开头，使 LLM 能区分上下文摘要与其他系统指令
3. WHILE 上下文摘要消息已注入，THE Agent_Loop SHALL 在 system prompt 末尾追加 Prompt_Level_Precheck 指引段落（见需求 5）
4. WHEN Context_Summary 为空（首轮对话或构建失败），THE Agent_Loop SHALL 保持原有消息组装逻辑不变，不追加 Prompt_Level_Precheck 指引
5. THE Agent_Loop SHALL 在注入上下文摘要前验证其文本长度不超过配置的 Summary_Char_Budget（字符数）
6. IF Context_Summary 文本长度超过 Summary_Char_Budget，THEN THE Context_Reuse_Builder SHALL 按以下优先级截断：先保留 Evidence（按 extracted_in_turn 降序，最新优先），再保留 Page_Summary，最后保留 Node_Summary 和文档结构概览

### 需求 3：页面去重

**用户故事：** 作为系统开发者，我希望当 LLM 请求读取已读过的页面时，系统能返回 Page_Summary 而非重新读取原文，以减少重复 token 消耗。

#### 验收标准

1. WHEN LLM 调用 get_page_content 且请求的页码全部存在于 Document_Store 的 read_pages 中且对应的 Page_Summary 文件存在，THE Page_Deduplication 模块 SHALL 返回这些页面的 Page_Summary 而非原始全文
2. WHEN LLM 调用 get_page_content 且请求的页码部分存在于 read_pages 中且有对应 Page_Summary，THE Page_Deduplication 模块 SHALL 对有 Page_Summary 的已读页面返回摘要，对其余页面调用原始 get_page_content 获取全文
3. THE 去重返回结果中每个页面条目 SHALL 包含 `is_cached` 布尔字段，标识该页面内容是否来自 Page_Summary 缓存
4. THE 去重返回结果中缓存页面的 `text` 字段 SHALL 为 Page_Summary 的 summary_text，并在文本开头添加 `[已读页面摘要]` 标记
5. IF Page_Summary 文件不存在（页面已读但摘要未生成），THEN THE Page_Deduplication 模块 SHALL 回退到调用原始 get_page_content 获取全文
6. THE Page_Deduplication 模块 SHALL 保持原始 get_page_content 的返回结构不变（content 列表、next_steps、total_pages），仅替换已缓存页面的 text 内容

### 需求 4：页面摘要生成与存储

**用户故事：** 作为系统开发者，我希望系统在每次读取页面后自动生成并存储 Page_Summary，以便后续页面去重时使用。

#### 验收标准

1. WHEN get_page_content 成功返回页面内容且页面文本非空，THE Context_Manager SHALL 为每个返回的页面生成 Page_Summary
2. THE Page_Summary SHALL 存储在 Document_Store 的会话目录下，路径为 `documents/<doc_name>/page_summaries/page_<page_num>.json`，文件内容符合 Page_Summary JSON Schema
3. THE Page_Summary 的 summary_text 字符数 SHALL 不超过原始页面文本字符数的 50%
4. THE Page_Summary 生成 SHALL 使用文本截断和关键句提取的规则方法（取前 N 个段落直到达到 50% 字符限制），不依赖额外的 LLM 调用
5. IF 页面摘要生成或写入失败，THEN THE Context_Manager SHALL 记录警告日志并跳过该页面的摘要存储，不影响主流程
6. WHEN 同一页面在不同轮次被多次读取，THE Context_Manager SHALL 保留首次生成的 Page_Summary，不覆盖已有摘要

### 需求 5：Prompt 级充分性预检

**用户故事：** 作为系统开发者，我希望通过 prompt 工程指引 LLM 在开始新一轮检索前评估已有上下文是否足以回答当前问题，以避免不必要的工具调用。

#### 验收标准

1. WHILE 上下文摘要已注入，THE Agent_Loop SHALL 在 system prompt 末尾追加一段 Prompt_Level_Precheck 指引文本
2. THE Prompt_Level_Precheck 指引 SHALL 明确告知 LLM：如果上方注入的 Context_Summary 中的 Evidence 和 Page_Summary 已包含回答所需的全部信息，可直接生成答案而无需调用任何工具
3. THE Prompt_Level_Precheck 指引 SHALL 明确告知 LLM：如果已有上下文仅部分覆盖问题，应仅检索缺失的部分（未读的章节或页面），而非重新检索已读内容
4. THE Prompt_Level_Precheck 指引 SHALL 明确告知 LLM：对于已读页面（列在 Context_Summary 的 read_pages 中），不要重复调用 get_page_content，除非需要获取更详细的原始内容
5. WHEN LLM 基于已有上下文直接生成答案（未调用任何工具），THE Agent_Loop SHALL 正常处理该答案并返回 RAGResponse
6. THE Prompt_Level_Precheck 为纯 prompt 工程技术，不涉及独立的代码模块、API 或程序化判断逻辑

### 需求 6：可配置性

**用户故事：** 作为系统开发者，我希望上下文复用功能可以通过配置开关和参数进行控制，以便在不同场景下灵活调整。

#### 验收标准

1. THE Agent_Loop SHALL 支持 `enable_context_reuse` 布尔参数，默认值为 True
2. WHEN `enable_context_reuse` 为 False，THE Agent_Loop SHALL 跳过上下文摘要构建、注入和 Prompt_Level_Precheck 追加，保持原有行为
3. THE Context_Reuse_Builder SHALL 支持 `summary_char_budget` 整数参数，控制 Context_Summary 序列化文本的最大字符数，默认值为 4000
4. THE Context_Reuse_Builder SHALL 支持 `enable_page_dedup` 布尔参数，控制是否启用页面去重，默认值为 True
5. WHEN `enable_page_dedup` 为 False，THE Page_Deduplication 模块 SHALL 对所有页面请求直接调用原始 get_page_content，不进行去重
6. THE 配置参数 SHALL 可通过 `agentic_rag` 函数参数传入，也可通过环境变量 `CONTEXT_REUSE_ENABLED`、`CONTEXT_REUSE_CHAR_BUDGET`、`CONTEXT_REUSE_PAGE_DEDUP` 设置
7. THE 环境变量优先级 SHALL 低于函数参数：函数参数 > 环境变量 > 默认值

### 需求 7：Sidecar 容错

**用户故事：** 作为系统开发者，我希望上下文复用层遵循 Sidecar 模式，任何异常不影响主流程的答案生成。

#### 验收标准

1. IF Context_Reuse_Builder 在构建 Context_Summary 时抛出异常，THEN THE Agent_Loop SHALL 捕获异常、记录日志并继续执行，使用空的 Context_Summary（不注入、不追加 Prompt_Level_Precheck）
2. IF Page_Deduplication 模块在处理页面请求时抛出异常，THEN THE Agent_Loop SHALL 回退到调用原始 get_page_content，不影响工具执行结果
3. IF Page_Summary 存储写入失败，THEN THE Context_Manager SHALL 记录警告日志并继续执行，不影响当前轮次的工具调用结果
4. THE Agent_Loop 在上下文复用相关操作失败时 SHALL 通过 progress_callback 发送 type="context_reuse_error" 的事件，包含错误信息

### 需求 8：跨轮次会话连续性

**用户故事：** 作为系统开发者，我希望上下文复用能在多轮对话中持续累积，使后续轮次能利用所有之前轮次的检索成果。

#### 验收标准

1. WHEN 同一会话中发起第 N 轮对话（N > 1），THE Context_Reuse_Builder SHALL 读取前 N-1 轮所有累积的文档状态（visited_parts、read_pages）、Node_State 和 Evidence
2. THE Context_Summary SHALL 反映截至当前轮次开始时的完整累积状态，而非仅上一轮的状态
3. WHEN 新一轮对话的 get_page_content 返回新页面内容，THE Context_Manager SHALL 将新页面的 Page_Summary 追加到已有的 page_summaries 目录中
4. THE Context_Reuse_Builder SHALL 对 Evidence 按 extracted_in_turn 降序排序（最近轮次优先），以便在 Summary_Char_Budget 截断时优先保留最新证据

### 需求 9：Context_Summary 序列化与反序列化

**用户故事：** 作为系统开发者，我希望 Context_Summary 有明确的序列化格式，以便调试和测试。

#### 验收标准

1. THE Context_Reuse_Builder SHALL 提供 `build_summary` 方法，返回 Context_Summary 的纯文本字符串（Markdown 格式，三个 `## ` 区段）
2. THE Context_Reuse_Builder SHALL 提供 `build_summary_dict` 方法，返回符合 Context_Summary Schema 的结构化字典
3. FOR ALL 合法的 Context_Summary 字典，通过 `build_summary_dict` 生成后再通过 JSON 序列化和反序列化，SHALL 产生与原始字典相等的值（往返属性）
4. THE `build_summary` 方法返回的文本 SHALL 包含 `total_chars` 字段值，以便在日志中追踪摘要大小

### 需求 10：Web API 兼容

**用户故事：** 作为前端开发者，我希望上下文复用功能能通过现有的 Web API 无缝使用，无需修改前端调用方式。

#### 验收标准

1. THE QARequest 模型 SHALL 新增可选字段 `enable_context_reuse`（布尔类型，默认 True）和 `context_session_id`（字符串类型，可选）
2. WHEN QARequest 包含 `context_session_id`，THE Agent_Loop SHALL 使用该 session_id 加载已有会话状态，而非创建新会话
3. WHEN QARequest 不包含 `context_session_id`，THE Agent_Loop SHALL 保持现有行为，创建新会话
4. THE `/api/qa` 和 `/api/qa/stream` 端点的响应 SHALL 在结果中包含 `context_session_id` 字段，以便前端在后续请求中传回
5. IF 指定的 `context_session_id` 对应的会话目录不存在，THEN THE Agent_Loop SHALL 创建新会话并记录警告日志
