# 实现计划：Context Management System

## 概述

将上下文管理子系统以 Sidecar 模式集成到现有 Agentic RAG 系统中。实现按自底向上顺序推进：先完成基础设施层（JSON_IO、ID 生成），再逐一实现各 Store，然后组装 Updater 和 ContextManager 高层接口，最后接入 Agent 循环。每个阶段都包含对应的属性测试和单元测试，确保增量可验证。

## Tasks

- [x] 1. 基础设施层：JSON_IO 原子读写与 ID 生成
  - [x] 1.1 创建 `src/context/` 包结构
    - 创建 `src/context/__init__.py`、`src/context/stores/__init__.py` 空包文件
    - 创建 `src/context/json_io.py` 和 `src/context/id_gen.py` 空模块
    - 创建 `tests/context/__init__.py` 空包文件
    - _Requirements: 13.1_

  - [x] 1.2 实现 JSON_IO 原子读写模块
    - 在 `src/context/json_io.py` 中实现 `JSON_IO` 类，包含 `save`、`load`、`append_to_list` 三个静态方法
    - `save`: 写入同目录临时文件 → `os.fsync` → `os.replace` 原子替换，异常时清理临时文件
    - `load`: 文件不存在返回 `None`，存在则解析 JSON 返回
    - `append_to_list`: 读取现有列表 → 追加新条目 → 调用 `save` 原子写回
    - 所有写入使用 `UTF-8` 编码和 `ensure_ascii=False`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.3 编写 JSON_IO 属性测试
    - **Property 1: JSON_IO 读写往返**
    - **Validates: Requirements 1.1, 1.5**
    - 在 `tests/context/test_json_io.py` 中使用 Hypothesis 生成含中文字符的 JSON 可序列化对象，验证 save → load 往返一致性

  - [ ]* 1.4 编写 JSON_IO 列表追加属性测试
    - **Property 2: JSON_IO 列表追加语义**
    - **Validates: Requirements 1.4**
    - 验证 append_to_list 后列表长度 +1 且末尾元素正确

  - [ ]* 1.5 编写 JSON_IO 单元测试
    - 测试文件不存在时 load 返回 None
    - 测试写入异常时临时文件被清理
    - 测试空列表 append
    - _Requirements: 1.2, 1.3_

  - [x] 1.6 实现 ID 生成工具模块
    - 在 `src/context/id_gen.py` 中实现以下函数：
    - `generate_session_id()` → `sess_YYYYMMDD_HHMMSS` 格式
    - `generate_turn_id(seq: int)` → `turn_` + 4 位零填充（如 `turn_0001`）
    - `generate_evidence_id(seq: int)` → `ev_` + 6 位零填充（如 `ev_000001`）
    - `generate_topic_id(seq: int)` → `topic_` + 4 位零填充（如 `topic_0001`）
    - _Requirements: 12.1, 12.2, 12.3_

  - [ ]* 1.7 编写 ID 生成属性测试
    - **Property 19: ID 格式一致性**
    - **Validates: Requirements 12.2, 12.3, 12.4**
    - 在 `tests/context/test_id_gen.py` 中验证各 ID 格式匹配对应正则


- [x] 2. 检查点 — 基础设施层验证
  - 确保所有测试通过，如有疑问请向用户确认。

- [x] 3. Store 层：SessionStore 与 TurnStore
  - [x] 3.1 实现 SessionStore
    - 在 `src/context/stores/session_store.py` 中实现 `SessionStore` 类
    - `create_session(session_id, doc_name)`: 创建 `data/sessions/<session_id>/` 目录及子目录（turns/、documents/、evidences/、topics/），写入 `session.json`（含 session_id、created_at、doc_name、status="active"、turns=[]）
    - `add_turn(turn_id)`: 将 turn_id 追加到 session.json 的 turns 列表
    - `finalize()`: 将 status 更新为 "completed"
    - 目录不存在时自动创建（`os.makedirs`）
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 13.1, 13.2_

  - [ ]* 3.2 编写 SessionStore 属性测试
    - **Property 3: 会话创建完整性**
    - **Validates: Requirements 2.1, 2.2, 2.3, 12.1**
    - 验证 create_session 后 session.json 包含所有必需字段且 session_id 匹配格式

  - [ ]* 3.3 编写轮次注册属性测试
    - **Property 4: 轮次注册到会话**
    - **Validates: Requirements 2.4**
    - 验证 N 次 add_turn 后 turns 列表包含 N 个 turn_id 且顺序一致

  - [x] 3.4 实现 TurnStore
    - 在 `src/context/stores/turn_store.py` 中实现 `TurnStore` 类
    - `create_turn(turn_id, user_query, doc_name)`: 在 `turns/` 下创建 `turn_xxxx.json`（含 turn_id、user_query、doc_name、started_at、tool_calls=[]、status="active"、retrieval_trace 初始结构）
    - `add_tool_call(turn_id, tool_name, arguments, result_summary)`: 追加工具调用记录到 tool_calls 列表
    - `update_retrieval_trace(turn_id, parts_seen, candidate_nodes, pages_read)`: 更新 retrieval_trace 字段
    - `finalize(turn_id, answer_payload)`: 记录 answer_payload，设置 status="completed" 和 finished_at 时间戳
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 3.5 编写 TurnStore 属性测试 — 轮次创建
    - **Property 6: 轮次创建完整性**
    - **Validates: Requirements 3.1, 3.2, 12.2**
    - 验证 create_turn 后 turn_xxxx.json 包含所有必需字段且 turn_id 匹配格式

  - [ ]* 3.6 编写 TurnStore 属性测试 — 工具调用追加
    - **Property 7: 工具调用记录追加**
    - **Validates: Requirements 3.3**
    - 验证 K 次 add_tool_call 后 tool_calls 列表包含 K 条记录且顺序一致

  - [ ]* 3.7 编写会话与轮次终结属性测试
    - **Property 5: 会话与轮次终结状态**
    - **Validates: Requirements 2.5, 3.4**
    - 验证终结后 status="completed"、finished_at 非空、answer_payload 非空


- [x] 4. 检查点 — Session/Turn Store 验证
  - 确保所有测试通过，如有疑问请向用户确认。

- [x] 5. Store 层：DocumentStore（含节点状态管理）
  - [x] 5.1 实现 DocumentStore 核心功能
    - 在 `src/context/stores/document_store.py` 中实现 `DocumentStore` 类
    - `update_visited_parts(doc_id, part)`: 追加 part 到 `documents/<doc_name>/document_state.json` 的 visited_parts（去重）
    - `update_read_pages(doc_id, pages)`: 追加页码到 read_pages（去重），递增 total_reads
    - `get_document_state(doc_id)`: 读取 document_state.json
    - 目录不存在时自动创建
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 5.2 实现 DocumentStore 节点状态管理
    - `flatten_structure(structure, parent_path)`: 递归展平嵌套结构树，返回扁平节点列表
    - `generate_provisional_id(doc_id, title, start, end, path)`: 生成 `tmp_` + sha1 前 12 位的稳定临时键
    - `upsert_node(doc_id, node_data, turn_id)`: 创建或更新 `nodes/node_xxxx.json`
      - 新节点：设置 status="discovered"、read_count=0、first_seen_turn_id、is_provisional_id 等
      - 已有节点 + is_skeleton=true：仅更新定位字段（title/start_index/end_index/parent_path/seen_in_parts/last_seen_turn_id），保留 summary 和 fact_digest
      - 已有节点 + is_skeleton=false：可覆盖 summary 和结构字段，设置 is_skeleton_latest=false
    - `update_node_read_status(doc_id, node_id)`: 递增 read_count，更新 status（discovered→reading→read_complete）
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 14.2, 14.3, 14.4, 14.5_

  - [ ]* 5.3 编写文档访问状态属性测试
    - **Property 8: 文档访问状态聚合**
    - **Validates: Requirements 4.1, 4.2, 4.3**
    - 验证一系列 parts 和 pages 操作后 visited_parts 和 read_pages 为去重集合

  - [ ]* 5.4 编写节点创建属性测试
    - **Property 9: 节点创建完整性**
    - **Validates: Requirements 5.1, 5.3, 5.7**
    - 验证首次 upsert 后 node_xxxx.json 包含所有必需字段

  - [ ]* 5.5 编写 Skeleton 合并保护属性测试
    - **Property 10: Skeleton 合并保护**
    - **Validates: Requirements 5.5, 14.3**
    - 验证 is_skeleton=true 合并不覆盖已有 summary 和 fact_digest

  - [ ]* 5.6 编写 Full Node 合并覆盖属性测试
    - **Property 11: Full Node 合并覆盖**
    - **Validates: Requirements 5.6, 14.4**
    - 验证 is_skeleton=false 合并覆盖 summary，设置 is_skeleton_latest=false

  - [ ]* 5.7 编写临时 ID 稳定性属性测试
    - **Property 12: 临时 ID 稳定性**
    - **Validates: Requirements 5.8, 14.5**
    - 验证相同输入始终生成相同 tmp_ 前缀 ID，且 is_provisional_id=true

  - [ ]* 5.8 编写结构树递归展平属性测试
    - **Property 13: 结构树递归展平**
    - **Validates: Requirements 8.7, 14.2**
    - 使用 Hypothesis 递归策略生成嵌套结构树，验证展平后节点总数正确

  - [ ]* 5.9 编写 DocumentStore 单元测试
    - 测试节点状态三态流转（discovered → reading → read_complete）
    - 测试 skeleton 合并边界情况
    - 测试 provisional ID 边界情况
    - _Requirements: 5.4_


- [x] 6. 检查点 — DocumentStore 验证
  - 确保所有测试通过，如有疑问请向用户确认。

- [x] 7. Store 层：EvidenceStore 与 TopicStore
  - [x] 7.1 实现 EvidenceStore
    - 在 `src/context/stores/evidence_store.py` 中实现 `EvidenceStore` 类
    - `add_evidence(evidence_id, source_doc, source_page, content, turn_id)`: 在 `evidences/` 下创建 `ev_xxxxxx.json`（含 evidence_id、source_doc、source_page、content、extracted_in_turn、used_in_turns=[]）
    - `add_usage(evidence_id, turn_id)`: 将 turn_id 追加到 used_in_turns 列表
    - `query_by_source(source_doc, source_page)`: 按 source_doc 和 source_page 查询匹配的证据条目
    - 内部维护递增计数器用于生成 evidence_id
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 7.2 编写证据创建与查询属性测试
    - **Property 16: 证据创建与查询**
    - **Validates: Requirements 6.1, 6.2, 6.4, 12.3**
    - 验证添加后按 source_doc/source_page 查询返回且仅返回匹配证据，evidence_id 匹配格式

  - [ ]* 7.3 编写证据跨轮次引用属性测试
    - **Property 17: 证据跨轮次引用**
    - **Validates: Requirements 6.3**
    - 验证 M 次 add_usage 后 used_in_turns 包含所有引用轮次

  - [x] 7.4 实现 TopicStore
    - 在 `src/context/stores/topic_store.py` 中实现 `TopicStore` 类
    - `create_topic(topic_id, turn_id, node_ids, evidence_ids, open_gaps)`: 创建 `topics/topic_xxxx.json`
    - `add_turn_to_topic(topic_id, turn_id)`: 将 turn_id 追加到 related_turn_ids
    - `update_topic(topic_id, node_ids, evidence_ids, open_gaps)`: 更新主题关联数据
    - _Requirements: 7.1, 7.2, 7.3_

  - [ ]* 7.5 编写 EvidenceStore 单元测试
    - 测试证据编号递增
    - 测试空查询结果
    - _Requirements: 6.1, 6.4_

- [x] 8. 检查点 — Evidence/Topic Store 验证
  - 确保所有测试通过，如有疑问请向用户确认。

- [x] 9. Updater 事件映射器
  - [x] 9.1 实现 Updater 事件映射器
    - 在 `src/context/updater.py` 中实现 `Updater` 类
    - `handle_tool_call(turn_id, tool_name, arguments, result, doc_id)`: 按 tool_name 分流
    - `_handle_document_structure(turn_id, arguments, result, doc_id)`:
      - 校验 result 无 error 且 structure 为 list
      - 调用 DocumentStore.flatten_structure 递归展平
      - 对每个节点调用 DocumentStore.upsert_node（遵循 skeleton 合并规则）
      - 调用 DocumentStore.update_visited_parts 更新 part
      - 调用 TurnStore.update_retrieval_trace 回写 structure_parts_seen 和 history_candidate_nodes
    - `_handle_page_content(turn_id, arguments, result, doc_id)`:
      - 调用 DocumentStore.update_read_pages 更新页码
      - 调用 DocumentStore.update_node_read_status 更新节点读取状态
      - 调用 TurnStore.update_retrieval_trace 回写 pages_read
    - `handle_final_answer(turn_id, answer_payload)`: 终结轮次 + 更新证据使用 + 主题快照
    - 未知 tool_name → 记录 warning 日志并跳过，不抛出异常
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ]* 9.2 编写错误结果不创建节点属性测试
    - **Property 14: 错误结果不创建节点**
    - **Validates: Requirements 8.9, 14.6**
    - 验证含 error 字段或 structure 非 list 的 result 不创建/更新 node_state

  - [ ]* 9.3 编写 Retrieval Trace 回写属性测试
    - **Property 15: Retrieval Trace 回写**
    - **Validates: Requirements 8.8, 14.7**
    - 验证成功处理后 turn 的 structure_parts_seen 包含 part，history_candidate_nodes 包含节点列表

  - [ ]* 9.4 编写未知工具容错属性测试
    - **Property 18: 未知工具容错**
    - **Validates: Requirements 9.4**
    - 验证未知 tool_name 不抛出异常且不修改任何状态文件

  - [ ]* 9.5 编写节点唯一数据源属性测试
    - **Property 21: 节点唯一数据源**
    - **Validates: Requirements 14.1**
    - 验证 node_state 仅在 handle_tool_call 处理 get_document_structure 时被创建/更新

  - [ ]* 9.6 编写 Updater 单元测试
    - 测试未知工具跳过
    - 测试 error result 处理
    - 测试空 structure 处理
    - _Requirements: 9.4, 8.9_


- [x] 10. 检查点 — Updater 验证
  - 确保所有测试通过，如有疑问请向用户确认。

- [x] 11. ContextManager 高层接口
  - [x] 11.1 实现 ContextManager 类
    - 在 `src/context/manager.py` 中实现 `ContextManager` 类
    - `__init__(base_dir="data/sessions")`: 初始化各 Store 和 Updater
    - `create_session(doc_name) -> str`: 生成 session_id，调用 SessionStore.create_session，返回 session_id
    - `create_turn(user_query, doc_name) -> str`: 生成 turn_id（基于内部递增计数器），调用 TurnStore.create_turn 和 SessionStore.add_turn，返回 turn_id
    - `record_tool_call(turn_id, tool_name, arguments, result, doc_id=None)`: 调用 TurnStore.add_tool_call 记录工具调用，然后委托 Updater.handle_tool_call 进行状态更新
    - `add_evidences(turn_id, doc_id, evidence_items) -> list[str]`: 批量添加证据，返回 evidence_ids
    - `finalize_turn(turn_id, answer_payload)`: 调用 TurnStore.finalize 终结轮次，委托 Updater.handle_final_answer 更新证据和主题
    - `finalize_session()`: 调用 SessionStore.finalize 将 status 更新为 completed
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9_

  - [x] 11.2 在 `src/context/__init__.py` 中导出 ContextManager
    - `from .manager import ContextManager`
    - _Requirements: 8.1_

  - [ ]* 11.3 编写 ContextManager 集成测试
    - 测试完整会话生命周期：create_session → create_turn → record_tool_call × N → finalize_turn → finalize_session
    - 验证所有 JSON 文件正确生成且内容完整
    - 测试多轮次场景
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 12. 检查点 — ContextManager 验证
  - 确保所有测试通过，如有疑问请向用户确认。

- [x] 13. Agent 循环 Sidecar 接入
  - [x] 13.1 在 `src/agent/loop.py` 中接入 ContextManager
    - 接入点 1（函数入口）：在 `agentic_rag` 函数开头，初始化消息列表之后，创建 `ContextManager` 实例并调用 `create_session(doc_name)` 和 `create_turn(query, doc_name)`，用 try/except 包裹
    - 接入点 2（tool_call 执行后）：在 `emit({"type": "tool_call", ...})` 之后，调用 `ctx.record_tool_call(turn_id, tc.name, tc.arguments, result, doc_id=doc_name)`，用 try/except 包裹并 `logger.exception`
    - 接入点 3（final_answer 前）：在 `emit({"type": "final_answer", ...})` 之后、`_save_session` 之前，调用 `ctx.finalize_turn(turn_id, answer_payload)` 和 `ctx.finalize_session()`，用 try/except 包裹并 `logger.exception`
    - 确保 max_turns 超限的代码路径也包含接入点 3
    - 所有 ContextManager 调用异常仅记录日志，不影响主流程
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 13.2 修改 `_save_session` 添加 context_session_id 字段
    - 在 `_save_session` 函数签名中新增可选参数 `context_session_id: str | None = None`
    - 将 context_session_id 写入 `logs/sessions/<timestamp>.json` 的会话日志中
    - 当 context_session_id 为 None 时不写入该字段，保持向后兼容
    - 更新 `agentic_rag` 中对 `_save_session` 的调用，传入 session_id
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [ ]* 13.3 编写 Agent 循环容错属性测试
    - **Property 20: Agent 循环容错**
    - **Validates: Requirements 10.4, 10.5, 10.6**
    - 在 `tests/context/test_agent_integration.py` 中 Mock ContextManager 使其抛出异常
    - 验证 agentic_rag 仍返回有效 RAGResponse，progress_callback 事件序列不变

  - [ ]* 13.4 编写 Agent 循环集成单元测试
    - 测试 context_session_id 正确写入会话日志
    - 测试 ContextManager 异常不影响主流程
    - 测试现有 RAGResponse 返回值结构不变
    - _Requirements: 10.4, 10.5, 10.6, 11.2, 11.3_

- [x] 14. 最终检查点 — 全量测试通过
  - 确保所有测试通过，如有疑问请向用户确认。
  - 运行 `pytest tests/context/ -v` 验证所有上下文管理系统测试
  - 运行 `pytest tests/test_agent_loop.py -v` 验证现有 Agent 循环测试未被破坏

## 备注

- 标记 `*` 的子任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 检查点确保增量验证，每完成一个层级即可确认正确性
- 属性测试验证通用正确性属性（21 个），单元测试验证具体边界和错误条件
- 实现语言为 Python，属性测试使用 Hypothesis 框架
