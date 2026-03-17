# 需求文档：Agentic RAG 闭环补全

## 简介

本功能为现有 Agentic RAG 系统补全三个核心缺口：证据主链路闭环、节点状态自动推进、Agent 化评测指标。当前系统已具备完整的 Agent Loop、工具层、引用系统、上下文复用层，但 EvidenceStore 未接入主链路（证据只有基础设施没有写入）、节点阅读状态无法自动推进（`update_node_read_status` 依赖不存在的 `node_id` 字段）、评测指标缺少 Agent 效率维度。

本功能不改变现有架构方向，仅在现有代码上补全缺失的调用链路和指标。

## 术语表

- **Evidence_Writeback**：证据回写，在 Agent Loop 生成最终答案后，从 citations 中提取证据写入 EvidenceStore
- **Node_Page_Resolver**：节点-页面关联解析器，根据已读页码反查覆盖该页码范围的节点，推进节点阅读状态
- **Agent_Efficiency_Metrics**：Agent 效率指标，衡量 Agent 检索行为效率的量化指标（重复读取率、每轮平均页数等）
- **Evidence_Store**：证据存储，`src/context/stores/evidence_store.py`，管理 `ev_xxxxxx.json` 文件
- **Document_Store**：文档状态存储，`src/context/stores/document_store.py`，管理节点状态和文档访问状态
- **Updater**：状态更新路由器，`src/context/updater.py`，将工具调用事件路由到对应的状态更新操作
- **ContextReuseBuilder**：上下文复用构建器，`src/context/reuse/builder.py`，构建注入 LLM 的上下文摘要

## 需求

### 需求 1：证据回写闭环

**用户故事：** 作为系统开发者，我希望 Agent 生成最终答案时引用的页面内容能自动写入 EvidenceStore，以便后续轮次的 ContextReuseBuilder 能读取并注入这些证据。

#### 验收标准

1. WHEN Agent Loop 生成最终答案且答案包含 `<cite>` 标签，THE Agent_Loop SHALL 从 citations 列表中提取每个引用的 doc_name、page、context，调用 `ContextManager.add_evidences()` 写入 EvidenceStore
2. THE 写入的 evidence 条目 SHALL 包含 source_doc（来自 citation.doc_name）、source_page（来自 citation.page）、content（来自 citation.context）和 extracted_in_turn（当前 turn_id）
3. IF 证据回写过程中发生异常，THEN THE Agent_Loop SHALL 记录警告日志并继续执行，不影响最终答案返回（Sidecar 模式）
4. WHEN 同一会话的后续轮次开始时，THE ContextReuseBuilder SHALL 能从 EvidenceStore 读取到之前轮次回写的证据，并将其包含在 Context_Summary 的「已提取的证据」区段中
5. THE 证据回写 SHALL 在 `ctx.finalize_turn()` 之前执行，确保 turn 记录中能反映已写入的证据

### 需求 2：节点状态自动推进

**用户故事：** 作为系统开发者，我希望当 Agent 读取了某个节点覆盖范围内的页面后，该节点的阅读状态能自动推进，以便 ContextReuseBuilder 能准确反映节点的完成度。

#### 验收标准

1. WHEN `get_page_content` 返回页面内容后，THE Updater SHALL 根据已读页码列表查找所有 `start_index <= page <= end_index` 的节点
2. FOR EACH 匹配的节点，THE Updater SHALL 调用 `DocumentStore.update_node_read_status()` 推进该节点的阅读状态
3. THE 节点状态推进规则 SHALL 保持现有逻辑不变：`discovered → reading`（首次读取）、`reading → read_complete`（第二次读取）
4. IF 同一次 `get_page_content` 调用读取了多个页面且它们属于不同节点，THEN THE Updater SHALL 对每个匹配的节点分别推进状态
5. IF 已读页码不属于任何已知节点的范围，THEN THE Updater SHALL 跳过节点状态推进，不报错
6. THE 节点查找 SHALL 遍历 `<session_dir>/documents/<doc_id>/nodes/` 目录下的所有节点文件，读取每个节点的 `start_index` 和 `end_index` 进行范围匹配

### 需求 3：Agent 效率评测指标

**用户故事：** 作为系统开发者，我希望评测系统能量化 Agent 的检索效率，以便发现重复读取、过度检索等问题并优化 prompt 和策略。

#### 验收标准

1. THE EvalResult 模型 SHALL 新增 `duplicate_read_rate`（float）字段，表示 pages_retrieved 中重复页码占总请求页数的比例
2. THE EvalResult 模型 SHALL 新增 `avg_pages_per_turn`（float）字段，表示平均每轮检索的页数（总检索页数 / 总轮次）
3. THE evaluate_single 函数 SHALL 计算 `duplicate_read_rate`：`1 - len(unique_pages) / len(all_requested_pages)`，当 all_requested_pages 为空时值为 0.0
4. THE evaluate_single 函数 SHALL 计算 `avg_pages_per_turn`：`len(unique_pages) / total_turns`，当 total_turns 为 0 时值为 0.0
5. THE evaluate_all 函数 SHALL 在汇总指标中输出平均 duplicate_read_rate 和平均 avg_pages_per_turn
6. THE RAGResponse 模型 SHALL 新增 `all_pages_requested`（list[int]）字段，记录所有请求的页码（含重复），以便评测脚本计算重复率

### 需求 4：ContextReuseBuilder query-aware 过滤（可选增强）

**用户故事：** 作为系统开发者，我希望 ContextReuseBuilder 在构建上下文摘要时能根据当前 query 对已有节点和证据做相关性排序，优先注入与当前问题最相关的内容。

#### 验收标准

1. THE ContextReuseBuilder.build_summary 方法 SHALL 支持可选的 `query` 参数
2. WHEN query 参数非空，THE ContextReuseBuilder SHALL 使用 token overlap 打分（与 pageindex 风格一致）对节点和证据按相关性降序排序
3. THE 打分逻辑 SHALL 为：对 query 和目标文本分别提取 token 集合（英文小写单词 + 中文单字），计算交集大小作为分数
4. THE 排序后的截断 SHALL 仍遵循现有的 summary_char_budget 和优先级规则（Evidence > Page_Summary > Node_Summary），但在同一类别内按相关性分数降序排列
5. WHEN query 参数为空或 None，THE ContextReuseBuilder SHALL 保持现有行为不变（按 extracted_in_turn 降序排列 Evidence）
6. THE query-aware 过滤 SHALL 不引入额外的外部依赖

### 需求 5：TopicStore 清理

**用户故事：** 作为系统开发者，我希望移除未使用的 TopicStore 相关代码，减少维护负担和代码复杂度。

#### 验收标准

1. THE ContextManager SHALL 移除 `topic_store` 属性和 `_topic_seq` 计数器
2. THE Updater SHALL 移除对 TopicStore 的依赖（构造函数参数和内部引用）
3. THE TopicStore 文件 SHALL 保留但标记为 deprecated（添加模块级注释），不删除文件以保持 git 历史可追溯
4. THE SessionStore.create_session SHALL 保留 `topics/` 目录创建（向后兼容），但不再写入 topic 文件
5. 所有现有测试 SHALL 继续通过，不因 TopicStore 弱化而失败
