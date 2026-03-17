# Agentic RAG 项目面试问答清单

> 适用场景：你把本仓库作为主项目介绍。
> 使用方式：每题先讲「一句话结论」，再讲「实现细节」，最后讲「权衡/改进」。
> 分为三部分：项目深挖题 / LLM Agent 通用题 / 工程与测试题。

---

# 第一部分：项目深挖题

---

## P1. 这个项目解决什么问题？

**答：** 解决长协议文档问答中"跨章节信息分散、检索不稳定、答案不可追溯"的问题。

- 普通 RAG 容易一次性召回不足，或者引用不可靠。
- 这个系统通过工具循环逐步导航：`get_document_structure` + `get_page_content`。
- 输出强制带 `<cite doc="..." page="..."/>`，并校验引用页是否真的检索过。

---

## P2. 为什么是 Agentic RAG，而不是固定 Retriever + Reader？

**答：** 协议文档存在大量交叉引用，固定检索链路很容易漏上下文。

- Agent 模式可以"看目录 → 读页面 → 发现缺口 → 继续检索"，更接近人工阅读策略。
- 固定 pipeline 无法处理"读到 section 4.1 发现需要 section 6.8 的定义"这类动态跳转。
- 我保留了架构约束：没引入向量库，没做多 agent，保持轻量可解释。

---

## P3. 系统的端到端链路是什么？

**答：** `PDF → 索引构建 → 结构分片 → 内容库 → 注册 → QA`。

- 索引构建：`page_index.py`（提取每页文本、表格、图片，生成 page_index.json）
- 结构分片：`structure_chunker.py`（按 token 预算把目录树切成多个 part）
- 内容库：`build_content_db.py`（按页范围生成 content_*.json）
- 编排入口：`src/ingest/pipeline.py` 的 `process_document` / `ensure_document_ready`
- QA 主循环：`src/agent/loop.py` 的 `agentic_rag`

---

## P4. QA 主循环怎么工作的？

**答：** 是一个 max-turn 受控的工具调用循环。

1. 加载 system prompt + tool schemas，组装初始 messages。
2. 调用 LLM，若返回 tool call 就执行工具并把结果追加到 messages。
3. 若返回纯文本则结束，提取/校验引用并落盘会话日志。
4. 超过 `max_turns`（默认 15）返回保护性结果，防止无限循环。

关键设计：代码不做任何检索决策，所有导航路径完全由 LLM 通过 tool call 驱动。

---

## P5. `get_document_structure` 与 `get_page_content` 的边界是什么？

**答：**

- `get_document_structure(doc_name, part)`：返回目录树分片（节点标题、摘要、页码范围），不含正文。
- `get_page_content(doc_name, pages)`：按页取正文/表格，单次最多 10 页。
- 两个工具解耦：先用 structure 定位章节，再用 page_content 取原文，避免一次性把大文本塞进上下文。

---

## P6. 为什么要把目录树切成多个 part？

**答：** 控制单次 tool 返回的 token 量。

- FC-LS 有 210 页，目录树节点数量很大，一次性返回会超出 context window。
- `structure_chunker.py` 按 `max_limit`（token 预算）切分，每个 part 是一个独立 JSON 文件。
- LLM 通过 `part` 参数翻页浏览，类似分页 API。
- 实测 FC-LS 用 `max_limit=30000` 切出 23 个 part，每 part 约 1000 token。

---

## P7. 为什么限制 `get_page_content` 一次最多 10 页？

**答：** 控制上下文膨胀和响应时延。

- 协议文档每页约 500-2000 字，10 页约 5000-20000 字，已接近 context 上限。
- 超限会返回错误提示，促使 agent 分批读取，保持每轮信息密度可控。
- 代码里还对单页内容做了 8000 字符截断（`_PAGE_CONTENT_MAX_CHARS`），防止单页过长。

---

## P8. 为什么能保证答案可追溯？

**答：** 两层保障：流程约束 + 结果校验。

- Prompt 要求"只基于已检索页面回答，不编造"。
- 代码从答案提取 `<cite doc="..." page="N"/>` 标签，校验 page 是否在 `pages_retrieved` 中。
- 所有 tool 调用 trace 和最终答案都落盘到 `logs/sessions/*.json`，可事后复盘。

---

## P9. Structure-Only Sidecar 是什么？为什么这样设计？

**答：** 跨轮次持久化"已探索节点状态 + 已读页码"，注入下一轮 LLM 上下文作为导航地图。

- 早期版本把 page summaries 和 evidence 也注入，导致 LLM 优先消费旧内容而不重新取原文，答案质量下降。
- 改为 structure-only：只注入节点标题/页码范围/状态（discovered/reading/read_complete）和已读页码压缩范围。
- PRECHECK_GUIDANCE 明确告诉 LLM："这是导航地图，不是答案，必须调用 get_page_content 取原文"。
- 实测追问场景轮次从 5 轮降到 3 轮以内。

---

## P10. `_truncate_to_budget` 的优先级策略是什么？

**答：** 优先丢弃 discovered 节点，保留 read/reading 节点。

- discovered 节点只有标题和页码，信息量低，是"目录索引"。
- read/reading 节点有 summary，是"已读记录"，对追问更有价值。
- 超出 budget 时从后往前删 discovered，再删 read/reading，最后若仍超则返回空。
- 这保证了在 budget 有限时，最有导航价值的信息被保留。

---

## P11. 你如何做多模型支持？OpenAI 和 Anthropic 的差异在哪里？

**答：** 做了统一 `LLMAdapter` 抹平差异，对上层暴露统一接口。

| 差异点 | OpenAI | Anthropic |
|--------|--------|-----------|
| Tool 定义格式 | `{"type": "function", "function": {...}}` | `{"name": ..., "input_schema": {...}}` |
| Tool call 响应 | `message.tool_calls[].function` | `content[].type == "tool_use"` |
| Tool result 格式 | `{"role": "tool", "tool_call_id": ...}` | `{"role": "user", "content": [{"type": "tool_result", ...}]}` |

适配层统一输出 `LLMResponse(has_tool_calls, tool_calls, text)`，loop.py 不感知 provider 差异。

---

## P12. 你如何处理 LLM 可能陷入死循环的问题？

**答：** 三层防护。

1. `max_turns=15` 硬上限，超过直接返回保护性结果。
2. 工具结果截断：`_truncate_tool_result` 防止单次结果过大导致 context 爆炸。
3. Prompt 层面：明确"信息充足时直接回答，不要继续检索"，减少无效循环。

目前没有做重复 tool call 检测（同一 part 被调用多次），这是一个可以改进的点。

---

## P13. 你的文档注册机制是怎么设计的？

**答：** 静态注册表 + 运行时注册表，运行时优先。

- 静态：`src/tools/registry.py` 中 `DOC_REGISTRY`，内置已知文档。
- 动态：`data/out/doc_registry.runtime.json`，处理新文档后写入。
- 路径解析：显式路径 → `data/raw/` 查找 → 递归查找（多命中报错，避免歧义）。
- 这样既支持开箱即用，又支持动态扩展，不需要重启服务。

---

## P14. 为什么 Web 层用 SSE 而不是 WebSocket？

**答：** 调试场景下 SSE 更简单且足够。

- SSE 是单向推送，后端只需 `StreamingResponse(text/event-stream)`，前端用 `fetch` 读流。
- 支持 POST body（携带 query 参数），不需要额外握手协议。
- 不引入任务队列、连接管理、心跳机制，复杂度更低。
- 缺点是不支持双向通信，但问答场景不需要。

---

## P15. 你的测试策略是什么？为什么用 Hypothesis？

**答：** 分层测试 + 属性测试，共 206 个测试。

- 单元测试：工具函数、citation 提取、registry 解析、models 校验。
- 集成测试：ingest 编排、Web API 流式事件顺序。
- Hypothesis 属性测试：用于验证"任意合法输入下不变量成立"，比如：
  - `_compress_page_ranges` 对任意页码列表输出可解析的范围字符串。
  - `_truncate_to_budget` 输出长度永远不超过 budget。
  - citation 提取对任意格式的 `<cite/>` 标签都能正确解析。
- 属性测试能发现边界 case，比如空列表、单元素、全相同页码等。

---

## P16. 你遇到过哪些具体的 bug，怎么定位和修复的？

**答：** 举三个典型：

1. **节点 start_index/end_index 全为 0**：旧版 structure_chunker 生成的 chunks 文件没有这两个字段，导致 builder 里 `find_nodes_covering_pages` 永远匹配不到节点，节点状态永远是 discovered，context reuse 完全失效。定位方式：看 session 日志发现节点状态异常，检查 chunks 文件发现字段缺失。修复：重新生成 chunks，并在 builder 里过滤掉 `start_index=0 AND end_index=0` 的骨架节点。

2. **Hypothesis 测试用了 pytest fixture**：`tmp_path` 和 `monkeypatch` 是 pytest fixture，不能在 `@given` 装饰的函数里使用。改用 `tempfile.mkdtemp()` + `unittest.mock.patch`。

3. **content-first 上下文策略**：早期把 page summaries 和 evidence 都注入上下文，LLM 优先消费旧内容，答案质量下降。通过分析 session 日志发现 LLM 没有调用 get_page_content 就直接回答，改为 structure-only 策略后解决。

---

## P17. 你如何评测系统质量？

**答：** 构建了 key_points 覆盖率 + 引用有效率 + 平均轮次的评测框架。

- 测试集：`data/eval/test_questions.json`，每题有 `expected_pages` 和 `key_points`。
- 指标：key_points 覆盖率（目标 >80%）、引用有效率（目标 >90%）、平均 turn 数（目标 4-8）。
- 评测脚本：`src/evaluate.py`，批量跑测试集并输出汇总报告。
- 失败分析：看 trace 日志，判断是"选错章节"还是"取了内容但没用"还是"引用了未读页"。

---

## P18. 如果要加向量检索，你会怎么做？

**答：** 作为 structure 导航的补充，而不是替代。

- 当前 structure 扫描是全量的（首轮需要翻完所有 part），向量检索可以快速定位相关 part，跳过无关 part。
- 实现方式：对每个节点的 title + summary 做 embedding，query 时先向量召回 top-k 节点，再只扫这些节点所在的 part。
- 不替换 get_page_content，仍然取原文，只是减少 structure 扫描轮次。
- 当前阶段不引入，因为首轮全扫是可接受的，且向量检索引入了新的依赖和调试成本。

---

## P19. 你认为当前版本的局限与下一步计划？

**答：**

- 局限：首轮仍需全量扫描 structure（23 个 part）；无并发文件锁；history 截断策略较粗糙。
- 下一步：引入轻量向量索引加速首轮定位；完善 context sidecar 的 topic 追踪；增加重复 tool call 检测。
- 原则：继续保持最小侵入，优先可解释性和稳定性。

---

# 第二部分：LLM Agent 通用题

---

## A1. 什么是 RAG？和普通 LLM 问答有什么区别？

**答：** RAG（Retrieval-Augmented Generation）是在生成答案前先检索相关文档片段，把检索结果作为上下文注入 LLM。

- 普通 LLM 只依赖训练时的参数知识，无法访问私有/最新文档，且容易幻觉。
- RAG 把"知识"外置到文档库，LLM 只负责理解和生成，知识可以随时更新。
- 核心挑战：检索质量（召回率/精确率）和上下文长度管理。

---

## A2. Agentic RAG 和传统 RAG 的区别是什么？

**答：** 传统 RAG 是固定的"检索一次 → 生成"，Agentic RAG 是"多轮检索 → 推理 → 再检索"的循环。

- 传统 RAG：query → 向量检索 top-k → 拼接上下文 → 生成答案。一次性，不能根据中间结果调整检索策略。
- Agentic RAG：LLM 自主决定"要不要继续检索"、"检索哪里"、"信息够不够"。
- 优点：能处理多跳问题、交叉引用、信息不足时主动补充。
- 缺点：轮次多、延迟高、行为不确定性更大。

---

## A3. 什么是 Tool Calling / Function Calling？

**答：** 让 LLM 在生成过程中输出结构化的"工具调用请求"，由代码执行后把结果回填给 LLM。

- LLM 不直接执行代码，只输出 `{"name": "get_page_content", "arguments": {"doc_name": "...", "pages": "7-9"}}`。
- 代码层解析这个 JSON，调用真实函数，把结果作为 tool result message 追加到 messages。
- 这样 LLM 可以"使用工具"而不需要直接访问外部系统，保持安全边界。

---

## A4. OpenAI 和 Anthropic 的 Tool Calling 有什么差异？

**答：** 主要在消息格式上。

- OpenAI：tool result 是独立的 `{"role": "tool", "tool_call_id": "..."}` 消息。
- Anthropic：tool result 包在 user 消息里，`{"role": "user", "content": [{"type": "tool_result", ...}]}`。
- Tool 定义格式也不同：OpenAI 用 `{"type": "function", "function": {...}}`，Anthropic 用 `{"name": ..., "input_schema": {...}}`。
- 适配层的价值就是抹平这些差异，让业务代码不感知 provider。

---

## A5. 什么是 Context Window？如何管理它？

**答：** Context Window 是 LLM 单次能处理的最大 token 数（如 GPT-4o 是 128k）。

- 超出 context window 会报错或截断，导致信息丢失。
- 管理策略：
  1. 工具结果截断（本项目：page content 截 8000 字符，structure 截 12000 字符）。
  2. 历史消息截断（本项目：只保留最近 8 条，每条最多 4000 字符）。
  3. 分批检索（单次最多 10 页，超出让 LLM 分批请求）。
  4. 上下文压缩（把旧的 tool result 替换为摘要）。

---

## A6. 什么是 System Prompt？它的作用是什么？

**答：** System Prompt 是在对话开始前注入的指令，定义 LLM 的角色、行为规范和工作流程。

- 在本项目中，system prompt 定义了：工具使用顺序（先 structure 再 page_content）、引用格式要求、不编造原则、交叉引用跟踪策略。
- System prompt 的措辞直接影响 LLM 行为，是"代码侧智能"的重要组成部分。
- 本项目把 system prompt 外置到文件（`src/agent/prompts/qa_system.txt`），切换 prompt 即可改变行为模式，不需要改代码。

---

## A7. 如何防止 LLM 幻觉（Hallucination）？

**答：** 多层约束。

1. **流程约束**：强制"先取页再回答"，LLM 没有读过的页面不能引用。
2. **引用校验**：代码层校验 `<cite/>` 中的页码是否在 `pages_retrieved` 中，不一致则记录警告。
3. **Prompt 约束**：明确"只基于你实际读取到的内容回答，不要编造"。
4. **可观测性**：所有 tool call trace 落盘，可事后审计答案来源。

完全消除幻觉是不可能的，但可以通过这些手段大幅降低并使其可检测。

---

## A8. 什么是 Prompt Engineering？你在项目里怎么用的？

**答：** Prompt Engineering 是通过设计 prompt 文本来引导 LLM 产生期望行为的技术。

本项目中的具体应用：
- **工作流程描述**：在 system prompt 里明确"先看目录再取内容"的步骤，建立行为模式。
- **Tool schema description**：`"通过阅读摘要来判断章节与问题的相关性"` 引导 LLM 利用 summary 而不是盲目翻页。
- **next_steps 字段**：tool 返回结果里附带 next_steps 提示，在 LLM 读完内容后立即提醒下一步动作。
- **消歧提示**：检测到 query 里有"表 149"这类表格 ID 时，自动追加消歧说明，防止 LLM 把表格 ID 当页码。

---

## A9. 什么是 ReAct 模式？你的项目用了吗？

**答：** ReAct（Reasoning + Acting）是让 LLM 在行动前先输出推理过程（Thought），再输出行动（Action），再观察结果（Observation）的循环模式。

- 本项目没有显式实现 ReAct，但 LLM 在 tool call 响应中可以附带文本（`assistant_note`），这相当于隐式的 Thought。
- 代码里会把这个 note 通过 `emit` 发送给前端展示，用户可以看到 LLM 的推理过程。
- 完整 ReAct 需要在 prompt 里明确要求输出 Thought，本项目没有强制这一点。

---

## A10. 什么是 Chain of Thought（CoT）？和 Agent 有什么关系？

**答：** CoT 是让 LLM 在给出最终答案前先输出推理步骤，提升复杂问题的准确率。

- 在 Agent 场景里，CoT 体现在 LLM 决定"调用哪个工具、用什么参数"的推理过程。
- 本项目的 system prompt 隐式引导了 CoT：先看目录 → 判断相关性 → 取页面 → 判断信息充足性 → 生成答案。
- 显式 CoT 可以通过在 prompt 里加 "Think step by step" 或要求输出 `<thinking>` 标签来实现。

---

## A11. 如何评估一个 RAG 系统的质量？

**答：** 从检索质量和生成质量两个维度评估。

检索质量：
- 召回率（Recall）：相关页面是否被检索到。
- 精确率（Precision）：检索到的页面是否都相关。
- 本项目用 `pages_hit_rate`（检索页码与 expected_pages 的重叠率）衡量。

生成质量：
- key_points 覆盖率：答案是否包含了问题的核心要点。
- 引用有效率：引用的页码是否真的被检索过。
- 人工评估：答案是否准确、完整、无幻觉。

效率指标：
- 平均 turn 数、平均 token 消耗、响应时延。

---

## A12. 什么是 Few-shot Prompting？你用了吗？

**答：** Few-shot Prompting 是在 prompt 里提供几个示例（input-output 对），让 LLM 学习期望的输出格式或推理模式。

- 本项目在引用格式上用了 few-shot：system prompt 里给出了 `<cite doc="rfc5880-BFD.pdf" page="7"/>` 的示例。
- 没有做完整的 few-shot 问答示例，因为协议文档问答的多样性太高，示例很难覆盖。
- 如果要提升引用格式的稳定性，可以加更多引用示例。

---

## A13. 什么是 Structured Output / JSON Mode？

**答：** 让 LLM 输出符合特定 JSON schema 的结构化数据，而不是自由文本。

- OpenAI 支持 `response_format={"type": "json_object"}` 或 `json_schema`。
- 本项目没有用 JSON mode，因为答案是自然语言 + 内嵌 `<cite/>` 标签的混合格式。
- 如果要做协议状态机提取（Phase 4），就需要 JSON mode 输出 `ProtocolStateMachine` 结构。

---

## A14. 如何处理 LLM 的不确定性和随机性？

**答：** 通过 temperature 控制和结果校验。

- `temperature=0` 或接近 0 可以让输出更确定，适合需要精确引用的场景。
- 本项目没有显式设置 temperature，使用模型默认值。
- 通过引用校验（`validate_citations`）检测不一致，通过 trace 日志审计行为。
- 对于关键场景，可以多次采样取多数投票（self-consistency），但本项目没有实现。

---

## A15. 什么是 Multi-Agent 系统？你的项目为什么没用？

**答：** Multi-Agent 是多个 LLM agent 协作完成任务，每个 agent 有不同的角色和工具。

- 典型模式：Orchestrator agent 分解任务，Sub-agent 各自执行，再汇总结果。
- 本项目没用，因为：
  1. 协议文档问答是单一任务，不需要分解。
  2. Multi-agent 引入了协调复杂度和调试难度。
  3. 保持单 agent 更容易保证可观测性和可复现性。
- 如果要做"同时查多个文档"或"并行提取多个状态机"，才值得考虑 multi-agent。

---

## A16. 什么是 Memory 在 Agent 中的作用？你是怎么实现的？

**答：** Memory 让 agent 在多轮对话中保持上下文，避免重复工作。

- Short-term memory：当前对话的 messages 列表，随对话增长。
- Long-term memory：跨会话持久化的知识，本项目用 `data/sessions/` 存储。
- 本项目的 Structure-Only Sidecar 是 long-term memory 的一种实现：把已探索节点状态和已读页码持久化，下一轮对话注入作为导航地图。
- 关键设计决策：只存 structure（节点状态），不存 content（页面内容），避免 LLM 消费旧内容而不取原文。

---

## A17. 如何处理 LLM API 的错误和重试？

**答：** 分层处理。

- 网络错误/超时：在 `LLMAdapter` 层做重试（指数退避），本项目依赖 SDK 内置重试。
- Tool 执行错误：`execute_tool` 捕获异常，返回 `{"error": "..."}` 而不是抛出，让 LLM 看到错误信息后决定下一步。
- 整体循环错误：`agentic_rag` 里 LLM 调用失败会 `raise`，由上层（Web API）捕获并返回 500。
- 关键原则：tool 执行失败不应该中断整个循环，LLM 应该能看到错误并自行恢复。

---

## A18. 什么是 Token 计费？如何优化成本？

**答：** LLM API 按 token 计费，包括 input token（prompt + context）和 output token（生成内容）。

- Input token 通常比 output token 便宜，但量更大（尤其是多轮 tool call 后 messages 很长）。
- 优化策略：
  1. 截断工具结果（本项目已实现）。
  2. 压缩历史消息（本项目保留最近 8 条）。
  3. 减少不必要的 tool call（context reuse 减少重复 structure 扫描）。
  4. 选择合适的模型（简单问题用便宜模型，复杂问题用强模型）。
- 本项目没有做成本监控，这是一个可以改进的点。

---

## A19. 什么是 Streaming 输出？为什么要用？

**答：** Streaming 是 LLM 边生成边输出 token，而不是等全部生成完再返回。

- 用户体验：用户能看到实时进度，不会感觉"卡死"。
- 本项目的 SSE 流式接口：每个 turn_start、tool_call、final_answer 事件都实时推送给前端。
- 注意：本项目的 streaming 是"事件级"的（每个 tool call 完成后推送），不是"token 级"的（逐 token 推送）。
- Token 级 streaming 需要 LLM SDK 支持 `stream=True`，本项目没有实现。

---

## A20. 如何做 LLM 应用的可观测性（Observability）？

**答：** 三个层面：日志、追踪、指标。

- 日志：本项目把完整 messages 历史、tool call trace、最终答案落盘到 `logs/sessions/*.json`。
- 追踪：`ToolCallRecord` 记录每次 tool call 的 turn、工具名、参数、结果摘要，可以重现 LLM 的导航路径。
- 指标：`total_turns`、`pages_retrieved`、`citation_valid_rate` 等，通过评测脚本汇总。
- 生产级可观测性还需要：token 用量追踪、延迟分布、错误率监控，本项目没有实现。

---

# 第三部分：工程与测试题

---

## E1. 为什么用 FastAPI 而不是 Flask？

**答：** FastAPI 原生支持 async/await，与 `agentic_rag` 的异步设计天然匹配。

- `agentic_rag` 是 `async def`，FastAPI 可以直接 `await` 调用，不需要额外的事件循环管理。
- FastAPI 自动生成 OpenAPI 文档，方便调试。
- Pydantic 集成：请求/响应模型自动校验，与项目里的 `RAGResponse` 等 Pydantic 模型一致。

---

## E2. 为什么用 Pydantic 做数据模型？

**答：** 类型安全 + 自动校验 + 序列化。

- `RAGResponse`、`Citation`、`ToolCallRecord` 等模型用 Pydantic 定义，字段类型在运行时自动校验。
- `model_dump()` 直接序列化为 dict，方便 JSON 落盘和 API 响应。
- 与 FastAPI 深度集成，请求体自动解析和校验。

---

## E3. 你的异步设计是怎样的？

**答：** QA 主循环是 `async def`，LLM 调用是 `await`，工具执行是同步的。

- `agentic_rag` 是 `async def`，通过 `await adapter.chat_with_tools(...)` 异步等待 LLM 响应。
- 工具函数（`get_document_structure`、`get_page_content`）是同步的，因为它们只是读文件，不涉及 IO 等待。
- 如果工具需要访问外部 API，应该改为 `async def` 并用 `await asyncio.to_thread()` 包装。
- Web 层用 FastAPI 的 `async def` 路由，支持并发请求。

---

## E4. 如何保证文件写入的原子性？

**答：** 用"写临时文件 + rename"的原子操作模式。

- 直接写目标文件，如果中途崩溃会留下损坏的文件。
- 正确做法：先写 `file.tmp`，写完后 `os.rename(file.tmp, file)`，rename 在同一文件系统上是原子操作。
- 本项目的 `JSON_IO` 类实现了这个模式，保证 session 状态文件不会因为崩溃而损坏。

---

## E5. 你如何做配置管理？

**答：** 分层配置：环境变量 > 代码默认值。

- 敏感配置（API Key）放 `.env`，通过 `python-dotenv` 加载。
- 运行时配置（模型名、超时、context budget）通过环境变量覆盖，代码里有默认值。
- `resolve_config` 函数统一处理"参数 > 环境变量 > 默认值"的优先级。
- 不把配置硬编码在代码里，方便不同环境（开发/测试/生产）切换。

---

## E6. 你的 Hypothesis 属性测试是怎么写的？有什么注意事项？

**答：** 用 `@given` + `@settings(max_examples=100)` 定义属性，用 `st.*` 生成随机输入。

```python
@given(st.lists(st.integers(min_value=1, max_value=500), min_size=1))
@settings(max_examples=100)
def test_compress_page_ranges_roundtrip(pages):
    compressed = ContextReuseBuilder._compress_page_ranges(sorted(set(pages)))
    # 验证压缩后的字符串可以解析回原始页码集合
    assert parse_ranges(compressed) == sorted(set(pages))
```

注意事项：
- 不能用 pytest fixture（`tmp_path`、`monkeypatch`），要用 `tempfile.mkdtemp()` 和 `unittest.mock.patch`。
- `@settings(max_examples=100)` 控制生成数量，太少发现不了边界 case，太多测试太慢。
- Hypothesis 会自动缩小（shrink）失败 case 到最小复现，方便调试。

---

## E7. 如何做 API 的集成测试？

**答：** 用 FastAPI 的 `TestClient` + `httpx`。

- `TestClient` 可以在不启动真实服务器的情况下测试 API。
- 对于流式接口（SSE），用 `httpx.AsyncClient` + `stream=True` 读取事件流。
- Mock 掉 LLM 调用（`unittest.mock.patch`），避免测试依赖真实 API Key。
- 验证事件顺序：`turn_start` → `tool_call` → `final_answer`，确保流式输出格式正确。

---

## 附录：面试演示建议（5 分钟）

1. 先跑处理：`python -m src.main --process data/raw/FC-LS.pdf`
2. 再跑问答：`python -m src.main --doc FC-LS.pdf --query "FLOGI 的帧结构是什么？" --verbose`
3. 打开 Web：`uvicorn src.web.app:app --host 127.0.0.1 --port 8000 --reload`
4. 演示点：实时进度（turn/tool/final）、引用点击跳页、会话可回放

---

## 附录：常见追问一句话答案

- 为什么答案可信？强制"先取页再回答"，并校验引用页码是否真的被检索。
- 为什么不用数据库？当前阶段 JSON + 文件系统成本最低，调试最方便。
- 怎么控制 token？page 请求上限 + 工具结果截断 + 历史消息截断。
- 并行在哪里？索引阶段有并行；QA 阶段串行决策，保证正确性优先。
- 和 LangChain 有什么区别？本项目是手写 agent loop，没有用框架，更轻量、更可控、更容易调试。
