# 实施计划：Agentic RAG 闭环补全

## 概述

按照独立模块方式实现 5 个需求。每个需求自成一个 Phase，可独立交付和测试。Phase 1-2 补全核心闭环（证据回写 + 节点推进），Phase 3 补全评测指标，Phase 4 做可选增强（query-aware 过滤），Phase 5 做代码清理（TopicStore 弱化）。所有改动遵循 Sidecar 容错模式，测试采用单元测试 + Hypothesis 属性测试双轨策略。

## 任务

- [ ] 1. 证据回写闭环（Phase 1 — 需求 1）
  - [ ] 1.1 在 Agent Loop 中新增证据回写逻辑 (`src/agent/loop.py`)
    - 在 final answer 分支中，`ctx.finalize_turn()` 之前新增 try/except 块
    - 从 `citations` 列表中过滤有 `context` 的引用，构造 `evidence_items` 列表
    - 调用 `ctx.add_evidences(ctx_turn_id, doc_name, evidence_items)` 写入 EvidenceStore
    - 异常时 `logger.exception("Evidence writeback failed")`，不影响主流程
    - 同样在 max_turns 分支中添加相同的回写逻辑
    - _需求: 1.1, 1.2, 1.3, 1.5_

  - [ ] 1.2 编写证据回写测试 (`tests/context/test_evidence_writeback.py`)
    - 单元测试：mock Agent Loop，验证 final answer 后 EvidenceStore 中存在对应的 evidence 文件
    - 单元测试：mock add_evidences 抛异常，验证 RAGResponse 仍正常返回
    - 单元测试：citations 为空时不调用 add_evidences
    - **Property 1: 证据回写后 EvidenceStore 可读取** — 随机生成 N 个 Citation（含 doc_name、page、context），模拟回写后验证 EvidenceStore 中存在对应条目，content 匹配
    - **Property 2: 证据回写异常不影响 RAGResponse 返回** — 随机生成异常类型，mock add_evidences 抛出该异常，验证 agentic_rag 仍返回有效 RAGResponse
    - 注释格式：`# Feature: agentic-rag-completeness, Property {N}: {text}`
    - _验证: 需求 1.1, 1.2, 1.3, 1.4, 1.5_

- [ ] 2. 检查点 — Phase 1 完成
  - 运行 `pytest tests/context/test_evidence_writeback.py -v` 确保新测试通过
  - 运行 `pytest tests/ -v` 确保已有测试不受影响

- [ ] 3. 节点状态自动推进（Phase 2 — 需求 2）
  - [ ] 3.1 在 DocumentStore 中新增 `find_nodes_covering_pages` 方法 (`src/context/stores/document_store.py`)
    - 方法签名：`find_nodes_covering_pages(self, doc_id: str, pages: list[int]) -> list[str]`
    - 遍历 `<session_dir>/documents/<doc_id>/nodes/` 目录下所有 `*.json` 文件
    - 对每个节点检查是否存在 page ∈ pages 使得 `start_index <= page <= end_index`
    - 返回匹配的 node_id 列表（去重）
    - nodes 目录不存在时返回空列表
    - _需求: 2.1, 2.5, 2.6_

  - [ ] 3.2 在 Updater._handle_page_content 中新增节点推进调用 (`src/context/updater.py`)
    - 在 `update_retrieval_trace` 之前，当 `pages` 非空且 `doc_id` 非空时：
    - 调用 `self._document_store.find_nodes_covering_pages(doc_id, pages)` 获取匹配节点
    - 对每个匹配的 node_id 调用 `self._document_store.update_node_read_status(doc_id, node_id)`
    - 异常时 `logger.warning`，不影响主流程
    - _需求: 2.2, 2.3, 2.4, 2.5_

  - [ ] 3.3 编写节点-页面关联测试 (`tests/context/test_node_page_resolver.py`)
    - 单元测试：创建 3 个节点（不同页码范围），读取中间节点范围内的页面，验证只有该节点状态推进
    - 单元测试：读取跨越两个节点范围的页面，验证两个节点都推进
    - 单元测试：读取不属于任何节点的页面，验证无节点状态变化
    - 单元测试：nodes 目录不存在时返回空列表
    - **Property 3: 页码范围反查节点覆盖正确性** — 随机生成 N 个节点（随机 start_index/end_index）和 M 个页码，验证 find_nodes_covering_pages 返回的 node_id 集合与手动计算的集合一致
    - **Property 4: 节点状态推进三态转换正确性** — 随机生成初始状态（discovered/reading/read_complete）和读取次数，验证状态转换符合 discovered→reading→read_complete 规则，read_complete 不再变化
    - _验证: 需求 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [ ] 4. 检查点 — Phase 2 完成
  - 运行 `pytest tests/context/test_node_page_resolver.py -v` 确保新测试通过
  - 运行 `pytest tests/ -v` 确保已有测试不受影响

- [ ] 5. Agent 效率评测指标（Phase 3 — 需求 3）
  - [ ] 5.1 扩展数据模型 (`src/models.py`)
    - RAGResponse 新增 `all_pages_requested: list[int] = []` 字段
    - EvalResult 新增 `duplicate_read_rate: float = 0.0` 字段
    - EvalResult 新增 `avg_pages_per_turn: float = 0.0` 字段
    - _需求: 3.1, 3.2, 3.6_

  - [ ] 5.2 在 Agent Loop 中累积 all_pages_requested (`src/agent/loop.py`)
    - 在 while 循环前初始化 `all_pages_requested: list[int] = []`
    - 在 get_page_content 结果处理中，`pages_retrieved.extend(extracted)` 之后新增 `all_pages_requested.extend(extracted)`
    - 构造 RAGResponse 时传入 `all_pages_requested=all_pages_requested`
    - 两个 RAGResponse 构造点（正常结束 + max_turns）都需要传入
    - _需求: 3.6_

  - [ ] 5.3 在评测脚本中计算新指标 (`src/evaluate.py`)
    - 在 `evaluate_single` 中计算 `duplicate_read_rate` 和 `avg_pages_per_turn`
    - `duplicate_read_rate = 1.0 - len(set(all_requested)) / len(all_requested)` （空时为 0.0）
    - `avg_pages_per_turn = len(set(all_requested)) / total_turns` （0 轮时为 0.0）
    - 在 `evaluate_all` 汇总中输出平均 duplicate_read_rate 和平均 avg_pages_per_turn
    - 在每个用例的详细输出中打印这两个指标
    - _需求: 3.3, 3.4, 3.5_

  - [ ] 5.4 编写评测指标测试 (`tests/test_eval_metrics.py`)
    - 单元测试：all_pages_requested=[1,2,3,1,2] → duplicate_read_rate=0.4
    - 单元测试：all_pages_requested=[] → duplicate_read_rate=0.0
    - 单元测试：unique_pages=5, total_turns=2 → avg_pages_per_turn=2.5
    - 单元测试：total_turns=0 → avg_pages_per_turn=0.0
    - **Property 5: duplicate_read_rate 计算正确性** — 随机生成页码列表，验证 `0.0 <= rate <= 1.0`，且全部唯一时 rate=0.0，全部相同时 rate 接近 `1 - 1/len`
    - **Property 6: avg_pages_per_turn 计算正确性** — 随机生成页码列表和轮次数，验证 `avg >= 0`，且 `avg * turns >= unique_pages - 1`（浮点容差）
    - _验证: 需求 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 6. 检查点 — Phase 3 完成
  - 运行 `pytest tests/test_eval_metrics.py -v` 确保新测试通过
  - 运行 `pytest tests/ -v` 确保已有测试不受影响

- [ ] 7. query-aware 过滤（Phase 4 — 需求 4，可选）
  - [ ] 7.1 在 ContextReuseBuilder 中新增打分方法 (`src/context/reuse/builder.py`)
    - 新增 `_tokenize(text: str) -> set[str]` 静态方法：提取英文小写单词（≥2字符）+ 中文单字
    - 新增 `_score_relevance(query_tokens: set[str], text: str) -> int` 静态方法：计算 token overlap
    - _需求: 4.3, 4.6_

  - [ ] 7.2 修改 build_summary 和 build_summary_dict 签名 (`src/context/reuse/builder.py`)
    - 新增可选参数 `query: str | None = None`
    - 当 query 非空时，在 `_truncate_to_budget` 之前对 evidences 按 `_score_relevance(q_tokens, content)` 降序排序
    - 当 query 非空时，对 node_summaries 按 `_score_relevance(q_tokens, title + summary)` 降序排序
    - 当 query 为空时保持现有排序不变
    - _需求: 4.1, 4.2, 4.4, 4.5_

  - [ ] 7.3 修改 Agent Loop 调用点 (`src/agent/loop.py`)
    - 将 `builder.build_summary(doc_name)` 改为 `builder.build_summary(doc_name, query=query)`
    - _需求: 4.1_

  - [ ] 7.4 编写 query-aware 过滤测试 (`tests/context/reuse/test_builder_query.py`)
    - 单元测试：query 包含特定关键词，验证匹配的 evidence 排在前面
    - 单元测试：query 为空时排序与原有行为一致
    - **Property 7: query-aware 排序不丢失数据** — 随机生成 query + N 个 evidence/node，验证排序后数量不变，所有原始条目都存在
    - **Property 8: query 为空时保持原有排序** — 随机生成 evidence 列表，验证 query=None 时排序结果与不传 query 时完全一致
    - _验证: 需求 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [ ] 8. 检查点 — Phase 4 完成
  - 运行 `pytest tests/context/reuse/test_builder_query.py -v` 确保新测试通过
  - 运行 `pytest tests/ -v` 确保已有测试不受影响

- [ ] 9. TopicStore 清理（Phase 5 — 需求 5）
  - [ ] 9.1 标记 TopicStore 为 deprecated (`src/context/stores/topic_store.py`)
    - 在文件顶部 docstring 中添加 `DEPRECATED` 标记和说明
    - _需求: 5.3_

  - [ ] 9.2 从 ContextManager 中移除 TopicStore 引用 (`src/context/manager.py`)
    - 移除 `self.topic_store` 属性初始化
    - 移除 `self._topic_seq` 计数器
    - 移除 `_init_components` 中的 `TopicStore(...)` 创建
    - 移除 `load_session` 中的 `_topic_seq` 恢复逻辑
    - 保留 `from src.context.stores.topic_store import TopicStore` 导入（避免破坏外部引用）
    - _需求: 5.1, 5.2_

  - [ ] 9.3 从 Updater 中移除 TopicStore 依赖 (`src/context/updater.py`)
    - 移除构造函数中的 `topic_store: TopicStore` 参数
    - 移除 `self._topic_store` 属性
    - _需求: 5.2_

  - [ ] 9.4 修复因 TopicStore 移除导致的调用点 (`src/context/manager.py`)
    - 修改 `_init_components` 中 Updater 构造调用，移除 `topic_store=` 参数
    - _需求: 5.2_

  - [ ] 9.5 编写 TopicStore 移除后的兼容性测试 (`tests/context/test_manager_no_topic.py`)
    - 单元测试：创建 ContextManager，验证 create_session / create_turn / finalize_session 正常工作
    - 单元测试：验证 session 目录下仍有 `topics/` 子目录（SessionStore 创建）
    - **Property 9: TopicStore 移除后 ContextManager 正常工作** — 随机生成 N 轮 turn，验证 ContextManager 全流程（create_session → create_turn × N → finalize_session）无异常
    - _验证: 需求 5.1, 5.2, 5.3, 5.4, 5.5_

- [ ] 10. 最终检查点
  - 运行 `pytest tests/ -v` 确保全部测试通过（预期 ~190+ 测试）
  - 验证 EvidenceStore 闭环：手动运行一次 agentic_rag，检查 session 目录下 evidences/ 中有 ev_*.json 文件
  - 验证节点推进：检查 session 目录下 nodes/ 中节点状态从 discovered 推进到 reading

## 备注

- 所有 Hypothesis 属性测试使用 `@settings(max_examples=100)`，注释格式：`# Feature: agentic-rag-completeness, Property {N}: {text}`
- Hypothesis 测试中不使用 pytest 的 `tmp_path` 和 `monkeypatch` fixture，改用 `tempfile.mkdtemp()` + `uuid4()` 和 `unittest.mock.patch`
- Phase 4（query-aware 过滤）为可选增强，可跳过不影响核心闭环
- 证据回写和节点推进都遵循 Sidecar 容错模式：异常时 log + 继续，不影响主流程
- `all_pages_requested` 字段记录含重复的页码列表，`pages_retrieved` 保持现有行为（去重后的唯一页码）
- TopicStore 文件保留不删除，仅标记 deprecated 并断开调用链路
