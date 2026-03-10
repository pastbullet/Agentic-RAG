# 需求文档

## 简介

为现有 Agentic RAG 系统新增一个上下文管理子系统（Context Management System）。该子系统以侧车（Sidecar）模式运行，在不改变现有 Agent 循环核心逻辑的前提下，对每次问答会话的多轮交互状态进行结构化持久化。持久化内容包括会话元数据、轮次记录、文档访问状态、证据条目和主题快照，存储于 `data/sessions/<session_id>/` 目录下的 JSON 文件中。目标是实现"多轮可复用、可追踪、可调试"的上下文状态层。

## 术语表

- **Context_Manager**：上下文管理器，对外暴露的唯一高层接口，负责协调各 Store 完成状态的创建、更新和查询
- **Session_Store**：会话存储，负责 `session.json` 的创建与读写
- **Turn_Store**：轮次存储，负责 `turns/turn_xxxx.json` 的创建与读写
- **Document_Store**：文档状态存储，负责 `documents/<doc>/document_state.json` 的创建与读写
- **Evidence_Store**：证据存储，负责 `evidences/ev_xxxxxx.json` 的创建与读写
- **Topic_Store**：主题存储，负责 `topics/topic_xxxx.json` 的创建与读写
- **Node_State**：节点状态，记录文档结构树中单个节点的访问与阅读情况，存储于 `nodes/node_xxxx.json`
- **JSON_IO**：JSON 读写工具模块，提供原子写（tmp + fsync + replace）能力
- **Atomic_Write**：原子写操作，通过写入临时文件、fsync 刷盘、再 rename 替换目标文件的方式，确保写入过程中断不会损坏已有 JSON 文件
- **Updater**：状态更新器，将 Agent 循环中的工具调用事件映射为对应的状态更新操作
- **Agentic_RAG**：现有的 Agent 循环主函数，位于 `src/agent/loop.py`
- **Session_Log**：现有的会话日志，存储于 `logs/sessions/<timestamp>.json`
- **Turn**：Agent 循环中的一个完整轮次，包含一次 LLM 调用及其可能的工具调用
- **Evidence**：从文档页面中提取的与用户问题相关的证据片段

## 需求

### 需求 1：原子 JSON 读写

**用户故事：** 作为开发者，我希望所有上下文状态文件的写入都是原子的，以便在写入过程中断时不会损坏已有数据。

#### 验收标准

1. WHEN JSON_IO 执行写入操作时, THE JSON_IO SHALL 先将内容写入同目录下的临时文件，执行 fsync 刷盘，再通过 os.replace 原子替换目标文件
2. IF 写入过程中发生异常, THEN THE JSON_IO SHALL 清理临时文件并向调用方抛出异常
3. WHEN JSON_IO 执行 load 操作且目标文件不存在时, THE JSON_IO SHALL 返回 None 而非抛出异常
4. WHEN JSON_IO 执行 append 操作时, THE JSON_IO SHALL 读取现有列表、追加新条目后以原子写方式保存完整文件
5. THE JSON_IO SHALL 使用 UTF-8 编码和 ensure_ascii=False 写入所有 JSON 文件

### 需求 2：会话状态管理

**用户故事：** 作为开发者，我希望每次问答会话都有一个独立的结构化状态目录，以便追踪和调试整个会话过程。

#### 验收标准

1. WHEN Context_Manager 创建新会话时, THE Session_Store SHALL 在 `data/sessions/` 下创建以 session_id 命名的目录，并写入 `session.json` 文件
2. THE Session_Store SHALL 使用 `sess_YYYYMMDD_HHMMSS` 格式生成 session_id
3. THE session.json SHALL 包含 session_id、created_at、doc_name、status 和 turns 列表字段
4. WHEN 新轮次被创建时, THE Session_Store SHALL 将 turn_id 追加到 session.json 的 turns 列表中
5. WHEN 会话结束时, THE Session_Store SHALL 将 session.json 的 status 字段更新为 completed

### 需求 3：轮次状态管理

**用户故事：** 作为开发者，我希望每个 Agent 轮次的详细信息都被记录，以便回溯每一步的决策过程。

#### 验收标准

1. WHEN Context_Manager 创建新轮次时, THE Turn_Store SHALL 在 `turns/` 子目录下创建 `turn_xxxx.json` 文件，其中 xxxx 为从 0001 开始的四位递增编号
2. THE turn_xxxx.json SHALL 包含 turn_id、user_query、doc_name、started_at、tool_calls 列表和 status 字段
3. WHEN 工具调用被记录时, THE Turn_Store SHALL 将工具名称、参数和结果摘要追加到该轮次的 tool_calls 列表中
4. WHEN 轮次被终结时, THE Turn_Store SHALL 记录 answer_payload 并将 status 更新为 completed，同时写入 finished_at 时间戳

### 需求 4：文档访问状态管理

**用户故事：** 作为开发者，我希望记录每个文档的结构浏览和页面阅读情况，以便了解 Agent 对文档的探索覆盖程度。

#### 验收标准

1. WHEN get_document_structure 工具被调用时, THE Document_Store SHALL 在 `documents/<doc_name>/` 下创建或更新 `document_state.json`，记录已访问的 part 编号
2. WHEN get_page_content 工具被调用时, THE Document_Store SHALL 将已读取的页码追加到 document_state.json 的 read_pages 列表中（去重）
3. THE document_state.json SHALL 包含 doc_name、visited_parts 列表、read_pages 列表和 total_reads 计数字段

### 需求 5：节点状态管理

**用户故事：** 作为开发者，我希望追踪文档结构树中每个节点的访问情况，以便分析 Agent 的导航路径。

#### 验收标准

1. WHEN 文档结构树中的节点首次被访问时, THE Document_Store SHALL 在 `nodes/` 子目录下创建 `node_xxxx.json` 文件，其中 xxxx 复用现有结构树的 node_id
2. WHEN 已存在的节点被再次访问时, THE Document_Store SHALL 递增该节点的 read_count 字段
3. THE node_xxxx.json SHALL 包含 node_id、title、first_seen_turn、read_count 和 status 字段
4. THE Node_State 的 status 字段 SHALL 支持 discovered、reading、read_complete 三种状态值
5. WHEN node_state 合并时且来源节点 is_skeleton=true, THE Document_Store SHALL 仅更新结构定位字段（title/page_range/parent 关系/seen_in_parts），不覆盖已有的 summary 和 fact_digest
6. WHEN node_state 合并时且来源节点 is_skeleton=false, THE Document_Store SHALL 可补全或覆盖 summary 与结构字段，优先级为 full node > skeleton
7. THE node_xxxx.json SHALL 额外包含 is_skeleton_latest、seen_in_parts、first_seen_turn_id、last_seen_turn_id 字段
8. WHEN 结构树节点缺失 node_id 时, THE Document_Store SHALL 生成稳定临时键 `tmp_sha1(doc_id|title|start|end|path)` 并在 node_state 中标记 `is_provisional_id=true`

### 需求 6：证据管理

**用户故事：** 作为开发者，我希望从文档中提取的证据片段被独立存储和追踪，以便在多轮追问中复用已有证据。

#### 验收标准

1. WHEN Context_Manager 添加证据时, THE Evidence_Store SHALL 在 `evidences/` 子目录下创建 `ev_xxxxxx.json` 文件，其中 xxxxxx 为从 000001 开始的六位递增编号
2. THE ev_xxxxxx.json SHALL 包含 evidence_id、source_doc、source_page、content、extracted_in_turn 和 used_in_turns 列表字段
3. WHEN 已有证据在后续轮次中被引用时, THE Evidence_Store SHALL 将该轮次的 turn_id 追加到对应证据的 used_in_turns 列表中
4. THE Evidence_Store SHALL 支持按 source_doc 和 source_page 查询已有证据条目

### 需求 7：主题快照管理

**用户故事：** 作为开发者，我希望记录每轮问答涉及的主题及其关联关系，以便分析问题覆盖情况和识别信息缺口。

#### 验收标准

1. WHEN 轮次被终结时, THE Topic_Store SHALL 创建或更新 `topics/topic_xxxx.json` 文件
2. THE topic_xxxx.json SHALL 包含 topic_id、related_turn_ids 列表、related_node_ids 列表、core_evidence_ids 列表和 open_gaps 列表字段
3. WHEN 新轮次涉及已有主题时, THE Topic_Store SHALL 将新的 turn_id 追加到该主题的 related_turn_ids 列表中

### 需求 8：Context_Manager 高层接口

**用户故事：** 作为开发者，我希望通过一个统一的高层接口操作所有上下文状态，以便降低与各 Store 的耦合度。

#### 验收标准

1. THE Context_Manager SHALL 提供 create_turn(user_query, doc_name) 方法，返回 turn_id
2. THE Context_Manager SHALL 提供 record_tool_call(turn_id, tool_name, arguments, result, doc_id) 方法，将工具调用分发到对应的 Store 进行状态更新
3. THE Context_Manager SHALL 提供 add_evidences(turn_id, doc_id, evidence_items) 方法，返回 evidence_ids 列表
4. THE Context_Manager SHALL 提供 finalize_turn(turn_id, answer_payload) 方法，完成轮次终结、证据使用记录和主题快照更新
5. WHEN record_tool_call 接收到 get_document_structure 工具调用时, THE Context_Manager SHALL 更新文档的 visited_parts 和相关节点状态
6. WHEN record_tool_call 接收到 get_page_content 工具调用时, THE Context_Manager SHALL 更新文档的 read_pages、节点读取状态和证据抽取输入
7. WHEN record_tool_call 处理 get_document_structure 时, THE Context_Manager SHALL 递归 flatten result.structure，解析每个节点的 node_id/title/start_index/end_index/summary/is_skeleton 并执行 upsert
8. WHEN record_tool_call 处理 get_document_structure 时, THE Context_Manager SHALL 回写 turn 的 retrieval_trace.structure_parts_seen 和 retrieval_trace.history_candidate_nodes
9. WHEN record_tool_call 处理 get_document_structure 且 result 包含 error 字段或 structure 非 list 时, THE Context_Manager SHALL 仅记录 event 日志，不创建任何 node_state

### 需求 9：Updater 事件映射

**用户故事：** 作为开发者，我希望工具调用事件能自动映射为状态更新，以避免在 Agent 循环中散落状态写入逻辑。

#### 验收标准

1. THE Updater SHALL 提供按工具名称分流的映射逻辑，将 get_document_structure 调用映射为文档和节点状态更新
2. THE Updater SHALL 将 get_page_content 调用映射为页面阅读记录、节点读取状态更新和证据抽取输入记录
3. THE Updater SHALL 将 final_answer 事件映射为轮次终结、证据使用记录和主题覆盖快照更新
4. IF Updater 接收到未知工具名称, THEN THE Updater SHALL 记录警告日志并跳过状态更新，不抛出异常

### 需求 10：Sidecar 接入 Agent 循环

**用户故事：** 作为开发者，我希望上下文管理系统以最小侵入方式接入现有 Agent 循环，以便不破坏现有 QA 管线的行为。

#### 验收标准

1. WHEN Agentic_RAG 函数启动时, THE Agentic_RAG SHALL 创建或加载一个 Context_Manager 会话实例
2. WHEN Agent 循环中发生工具调用时, THE Agentic_RAG SHALL 调用 Context_Manager.record_tool_call 记录该事件
3. WHEN Agent 循环产生最终答案时, THE Agentic_RAG SHALL 调用 Context_Manager.finalize_turn 完成轮次终结
4. THE Agentic_RAG 的现有返回值结构（RAGResponse）SHALL 保持不变
5. THE Agentic_RAG 的现有 progress_callback 事件 SHALL 保持不变
6. IF Context_Manager 操作发生异常, THEN THE Agentic_RAG SHALL 记录错误日志并继续执行，不影响主流程的答案生成

### 需求 11：与现有日志系统兼容

**用户故事：** 作为开发者，我希望现有的会话日志格式保持兼容，以便 Web 前端和 API 能正常读取历史会话。

#### 验收标准

1. THE Agentic_RAG SHALL 继续将完整会话日志写入 `logs/sessions/<timestamp>.json`
2. WHEN 上下文管理系统处于活跃状态时, THE Session_Log SHALL 新增 context_session_id 字段，记录对应的上下文会话 ID
3. THE Web API 的 `/api/sessions` 端点 SHALL 能正常读取包含 context_session_id 字段的会话日志，无需修改
4. THE 现有 CLI 和 Web API 的所有路径与返回结构 SHALL 保持向后兼容

### 需求 12：ID 生成规则

**用户故事：** 作为开发者，我希望所有上下文实体的 ID 遵循固定的命名规则，以便在文件系统中快速定位和排序。

#### 验收标准

1. THE Context_Manager SHALL 使用 `sess_YYYYMMDD_HHMMSS` 格式生成 session_id
2. THE Turn_Store SHALL 使用 `turn_` 前缀加四位零填充递增编号生成 turn_id（如 turn_0001）
3. THE Evidence_Store SHALL 使用 `ev_` 前缀加六位零填充递增编号生成 evidence_id（如 ev_000001）
4. THE Document_Store SHALL 复用现有文档结构树的 node_id 作为节点标识，不重新编号

### 需求 13：目录结构规范

**用户故事：** 作为开发者，我希望上下文状态文件按照清晰的目录层级组织，以便手工检查和调试。

#### 验收标准

1. THE Context_Manager SHALL 在 `data/sessions/<session_id>/` 下创建以下子目录结构：session.json（根目录）、turns/（轮次文件）、documents/<doc_name>/（文档状态）、nodes/（节点状态）、evidences/（证据文件）、topics/（主题文件）
2. WHEN 子目录不存在时, THE Context_Manager SHALL 自动创建所需的目录层级
3. THE Context_Manager SHALL 仅对新产生的会话写入上下文状态，不回填历史旧会话数据

### 需求 14：结构树数据源契约

**用户故事：** 作为开发者，我希望节点状态的数据来源有明确的契约定义，以便避免双源漂移和不一致。

#### 验收标准

1. THE node_state 的唯一增量来源 SHALL 为 `get_document_structure` 工具调用结果中 `record_tool_call(..., result)` 的 result，系统不主动预扫描 chunks 目录
2. WHEN record_tool_call 处理 get_document_structure 结果时, THE Context_Manager SHALL 递归 flatten result.structure 列表，对每个节点解析 node_id、title、start_index、end_index、summary、is_skeleton 字段
3. WHEN 合并 node_state 且来源节点 is_skeleton=true 时, THE Context_Manager SHALL 仅更新结构定位字段（title/page_range/parent 关系/seen_in_parts），不覆盖已有的 summary 和 fact_digest
4. WHEN 合并 node_state 且来源节点 is_skeleton=false 时, THE Context_Manager SHALL 可补全或覆盖 summary 与结构字段；字段冲突时优先级为 full node > skeleton
5. WHEN 结构树节点缺失 node_id 时, THE Context_Manager SHALL 生成稳定临时键 `tmp_sha1(doc_id|title|start_index|end_index|path)` 并在 node_state 中标记 `is_provisional_id=true`
6. WHEN result 包含 error 字段或 result.structure 非 list 类型时, THE Context_Manager SHALL 仅记录 event 日志，不创建或更新任何 node_state
7. WHEN record_tool_call 成功处理 get_document_structure 时, THE Context_Manager SHALL 回写当前 turn 的 retrieval_trace.structure_parts_seen（追加 part 编号）和 retrieval_trace.history_candidate_nodes（追加当前 part 中的 node_id 列表）
