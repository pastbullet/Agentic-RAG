# 设计文档：Agentic RAG 闭环补全

## 概述（Overview）

本设计为现有 Agentic RAG 系统补全三个核心闭环缺口，同时做一项可选增强和一项代码清理。所有改动遵循最小侵入原则，在现有代码上补全缺失的调用链路，不改变架构方向。

核心设计原则：
- **最小改动**：每个需求只补几十行代码，不重构现有模块
- **Sidecar 延续**：新增的证据回写和节点推进遵循现有 Sidecar 容错模式
- **向后兼容**：所有改动不破坏现有 179 个测试
- **可增量交付**：5 个需求相互独立，可按任意顺序实现

### 设计决策

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 证据回写触发点 | finalize_turn 内部 / loop.py 显式调用 | loop.py 显式调用 | 保持 ContextManager 职责单一，loop.py 已有 Sidecar try/except 模式 |
| 节点-页面关联方式 | 结果携带 node_id / 页码范围反查 | 页码范围反查 | get_page_content 不返回 node_id，反查是唯一可行方案 |
| 节点查找位置 | Updater 内部 / DocumentStore 新方法 | DocumentStore 新方法 | 节点文件遍历属于 Store 职责，Updater 只做路由 |
| query-aware 打分算法 | BM25 / TF-IDF / token overlap | token overlap | 与 pageindex 风格一致，无外部依赖，实现简单 |
| TopicStore 处理 | 删除文件 / 标记 deprecated | 标记 deprecated | 保持 git 历史可追溯，减少破坏性变更 |
| 重复页码统计来源 | trace 解析 / RAGResponse 新字段 | RAGResponse 新字段 | 直接在 loop.py 中累积，比从 trace 反解析更准确 |

### Non-goals

- **Evidence 智能提取**：不新增 LLM 驱动的证据提取逻辑，仅从 citations 中提取
- **节点完成度百分比**：不计算节点内已读页面占比，仅推进 discovered/reading/read_complete 三态
- **评测结果持久化**：不将评测结果写入数据库，仅终端输出
- **TopicStore 文件删除**：不删除 topic_store.py 文件，仅标记 deprecated

## 架构（Architecture）

### 改动影响范围

```
src/agent/loop.py                    — 需求 1: 证据回写调用点（~15 行）
                                     — 需求 3: all_pages_requested 累积（~5 行）
src/context/updater.py               — 需求 2: 节点状态推进调用（~10 行）
src/context/stores/document_store.py — 需求 2: find_nodes_covering_pages 新方法（~25 行）
src/context/manager.py               — 需求 5: 移除 TopicStore 引用（~10 行）
src/context/reuse/builder.py         — 需求 4: query-aware 过滤（~30 行）
src/models.py                        — 需求 3: EvalResult + RAGResponse 新字段（~5 行）
src/evaluate.py                      — 需求 3: 新指标计算（~15 行）
```

## 详细设计

### 模块 1：证据回写闭环（需求 1）

#### 改动位置：`src/agent/loop.py`

在 Agent Loop 的 final answer 分支中，`ctx.finalize_turn()` 之前，新增证据回写逻辑：

```python
# ── Evidence writeback (Sidecar) ──
if ctx is not None and citations:
    try:
        evidence_items = [
            {
                "source_page": c.page,
                "content": c.context,
            }
            for c in citations
            if c.context  # 跳过空 context 的引用
        ]
        if evidence_items:
            ctx.add_evidences(ctx_turn_id, doc_name, evidence_items)
    except Exception:
        logger.exception("Evidence writeback failed")
```

#### 数据流

```
LLM final answer
  → extract_citations(answer) → citations: list[Citation]
  → 过滤有 context 的 citations
  → ctx.add_evidences(turn_id, doc_name, evidence_items)
  → EvidenceStore.add_evidence() 写入 ev_xxxxxx.json
  → 下一轮 ContextReuseBuilder.build_summary_dict() 读取 evidences/
  → 注入 Context_Summary 的「已提取的证据」区段
```

#### 容错

- `add_evidences` 异常时 logger.exception + 继续执行
- 不 emit `context_reuse_error` 事件（证据回写不属于 context_reuse 层）
- citations 为空时跳过整个回写块

### 模块 2：节点状态自动推进（需求 2）

#### 改动位置 1：`src/context/stores/document_store.py`

新增 `find_nodes_covering_pages` 方法：

```python
def find_nodes_covering_pages(self, doc_id: str, pages: list[int]) -> list[str]:
    """查找覆盖给定页码的所有节点 ID。

    遍历 nodes/ 目录下所有节点文件，对每个节点检查
    是否存在 page ∈ pages 使得 start_index <= page <= end_index。

    Returns
    -------
    list[str]
        匹配的 node_id 列表（去重）。
    """
    nodes_dir = self._nodes_dir(doc_id)
    if not nodes_dir.exists():
        return []

    page_set = set(pages)
    matched: list[str] = []
    for path in sorted(nodes_dir.glob("*.json")):
        node = JSON_IO.load(path)
        if not isinstance(node, dict):
            continue
        start = node.get("start_index", 0)
        end = node.get("end_index", 0)
        if any(start <= p <= end for p in page_set):
            node_id = node.get("node_id", path.stem)
            matched.append(node_id)
    return matched
```

#### 改动位置 2：`src/context/updater.py`

在 `_handle_page_content` 方法末尾，`update_retrieval_trace` 之前，新增节点推进逻辑：

```python
# --- Update node read status based on page coverage ---
if pages and doc_id:
    try:
        covering_nodes = self._document_store.find_nodes_covering_pages(doc_id, pages)
        for node_id in covering_nodes:
            self._document_store.update_node_read_status(doc_id, node_id)
    except Exception:
        logger.warning("Node status update failed for doc '%s'", doc_id)
```

#### 状态推进示例

```
初始状态: node_001 (start=7, end=15, status=discovered)

Turn 1: get_page_content("doc.pdf", "7-9")
  → pages=[7,8,9], 匹配 node_001
  → update_node_read_status → status: discovered → reading

Turn 2: get_page_content("doc.pdf", "10-12")
  → pages=[10,11,12], 匹配 node_001
  → update_node_read_status → status: reading → read_complete
```

### 模块 3：Agent 效率评测指标（需求 3）

#### 改动位置 1：`src/models.py`

```python
class RAGResponse(BaseModel):
    # ... 现有字段 ...
    all_pages_requested: list[int] = []  # 所有请求的页码（含重复）

class EvalResult(BaseModel):
    # ... 现有字段 ...
    duplicate_read_rate: float = 0.0
    avg_pages_per_turn: float = 0.0
```

#### 改动位置 2：`src/agent/loop.py`

在 tool call 处理中，`pages_retrieved.extend(...)` 的同时，新增一个 `all_pages_requested` 列表累积所有请求页码（含重复）：

```python
all_pages_requested: list[int] = []

# 在 get_page_content 结果处理中：
if tc.name == "get_page_content":
    extracted = _extract_pages_from_result(result)
    pages_retrieved.extend(extracted)
    all_pages_requested.extend(extracted)  # 新增：含重复
```

最终构造 RAGResponse 时传入 `all_pages_requested=all_pages_requested`。

#### 改动位置 3：`src/evaluate.py`

```python
# 在 evaluate_single 中：
all_requested = response.all_pages_requested
if all_requested:
    duplicate_read_rate = 1.0 - len(set(all_requested)) / len(all_requested)
else:
    duplicate_read_rate = 0.0

unique_pages_count = len(set(all_requested)) if all_requested else 0
avg_pages_per_turn = unique_pages_count / response.total_turns if response.total_turns > 0 else 0.0

# 在 evaluate_all 汇总中：
avg_dup_rate = sum(r.duplicate_read_rate for r in results) / n * 100
avg_ppt = sum(r.avg_pages_per_turn for r in results) / n
```

#### 指标定义

| 指标 | 公式 | 含义 | 理想值 |
|------|------|------|--------|
| duplicate_read_rate | `1 - unique / total` | 重复读取页面的比例 | < 10% |
| avg_pages_per_turn | `unique_pages / turns` | 每轮平均检索页数 | 2-5 |

### 模块 4：query-aware 过滤（需求 4，可选）

#### 改动位置：`src/context/reuse/builder.py`

新增 `_tokenize` 静态方法和 `_score_relevance` 静态方法：

```python
import re

@staticmethod
def _tokenize(text: str) -> set[str]:
    """提取 token 集合：英文小写单词（≥2字符）+ 中文单字。"""
    lower = text.lower()
    en = set(re.findall(r"[a-z0-9]{2,}", lower))
    zh = set(re.findall(r"[\u4e00-\u9fff]", text))
    return en | zh

@staticmethod
def _score_relevance(query_tokens: set[str], text: str) -> int:
    """计算 query 与目标文本的 token overlap 分数。"""
    text_tokens = ContextReuseBuilder._tokenize(text)
    return len(query_tokens & text_tokens)
```

修改 `build_summary` 和 `build_summary_dict` 签名，新增可选 `query: str | None = None` 参数。

在 `_truncate_to_budget` 之前，如果 query 非空，对 evidences 和 node_summaries 按相关性分数降序排序：

```python
if query:
    q_tokens = self._tokenize(query)
    evidences.sort(
        key=lambda e: self._score_relevance(q_tokens, e.get("content", "")),
        reverse=True,
    )
    node_summaries.sort(
        key=lambda n: self._score_relevance(
            q_tokens,
            f"{n.get('title', '')} {n.get('summary') or ''}",
        ),
        reverse=True,
    )
```

#### 调用点改动：`src/agent/loop.py`

```python
context_text = builder.build_summary(doc_name, query=query)
```

### 模块 5：TopicStore 清理（需求 5）

#### 改动位置 1：`src/context/stores/topic_store.py`

在文件顶部添加 deprecated 注释：

```python
"""Topic Store — DEPRECATED.

This module is no longer actively used in the main pipeline.
Retained for backward compatibility and git history.
New features should not depend on TopicStore.
"""
```

#### 改动位置 2：`src/context/manager.py`

- 移除 `self.topic_store` 初始化
- 移除 `self._topic_seq` 计数器
- 移除 `_init_components` 中的 TopicStore 创建
- 移除 `load_session` 中的 `_topic_seq` 恢复

#### 改动位置 3：`src/context/updater.py`

- 移除构造函数中的 `topic_store` 参数
- 移除 `self._topic_store` 属性

#### 向后兼容

- `topics/` 目录仍由 SessionStore.create_session 创建（不改动 SessionStore）
- topic_store.py 文件保留，不删除
- 现有测试中如果有直接使用 TopicStore 的，需要调整

## 正确性属性（Hypothesis Property Tests）

| Property | 描述 | 测试文件 |
|----------|------|----------|
| P1 | 证据回写后 EvidenceStore 可读取 | `tests/context/test_evidence_writeback.py` |
| P2 | 证据回写异常不影响 RAGResponse 返回 | `tests/context/test_evidence_writeback.py` |
| P3 | 页码范围反查节点覆盖正确性 | `tests/context/test_node_page_resolver.py` |
| P4 | 节点状态推进三态转换正确性 | `tests/context/test_node_page_resolver.py` |
| P5 | duplicate_read_rate 计算正确性 | `tests/test_eval_metrics.py` |
| P6 | avg_pages_per_turn 计算正确性 | `tests/test_eval_metrics.py` |
| P7 | query-aware 排序不丢失数据 | `tests/context/reuse/test_builder_query.py` |
| P8 | query 为空时保持原有排序 | `tests/context/reuse/test_builder_query.py` |
| P9 | TopicStore 移除后 ContextManager 正常工作 | `tests/context/test_manager_no_topic.py` |
