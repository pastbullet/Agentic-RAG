# PageIndex 技术分析报告 — 关键技术解析与本系统改进方案

## 一、关于"无向量"的真相

PageIndex 宣传的"Vectorless RAG"是一个**营销概念**，不是严格的技术事实。

### 1.1 它确实用了向量

PageIndex 文档（docs.pageindex.ai/tutorials/tree-search/hybrid）明确描述了三种树搜索策略：

| 策略 | 是否用向量 | 用途 |
|------|-----------|------|
| LLM Tree Search | ❌ 不用 | 纯 LLM 推理选节点 |
| Value-based Tree Search | ✅ 用了 | embedding 模型计算节点相关性分数 |
| Hybrid Tree Search | ✅ 用了 | 生产版，并行执行 LLM + Value 搜索 |

Value-based Tree Search 的具体做法：
1. 每个树节点的内容被切成小 chunk
2. 用 embedding 模型对 query 做向量搜索，找 top-K chunk
3. 每个 chunk 的相似度分数回溯到父节点
4. 节点分数公式：`NodeScore = Σ ChunkScore(n) / √(N+1)`

这就是标准的向量检索，只不过检索的目标不是"返回 chunk 给 LLM"，而是"给树节点打分"。

### 1.2 "无向量"的真正含义

PageIndex 说的"无向量"指的是：
- **不用向量数据库做最终检索**（不返回 chunk 给 LLM）
- **不依赖 chunk 拼接作为 context**
- 向量只用于**辅助树搜索的节点排序**，最终检索仍然是按页/按节点读原文

类比：就像说"我不用 GPS 导航"，但实际上用了 GPS 来预估哪条路更快，最终还是自己开车。

## 二、PageIndex 三种树搜索策略详解

### 2.1 LLM Tree Search（基础版）

**原理**：把完整的树索引 + query 一起喂给 LLM，让 LLM 输出推理过程和相关节点列表。

```
Prompt:
  You are given a query and the tree structure of a document.
  Query: {query}
  Document tree structure: {PageIndex_Tree}
  Reply: { "thinking": "...", "node_list": [node_id1, node_id2, ...] }
```

**优点**：LLM 能做深度推理，理解"EBITDA 调整"应该去 MD&A 章节找
**缺点**：慢（每次搜索都要一次 LLM 调用），且完全依赖摘要质量

**与本系统对比**：你当前的 `qa_system.txt` prompt 里的 workflow 本质上就是这个策略，
但你是让 LLM 在 tool call 循环中隐式完成的，没有把"选节点"和"读内容"显式分离。

### 2.2 Value-based Tree Search（加速版）

**原理**：预计算每个节点对 query 的"价值分数"，用分数排序代替 LLM 推理。

```
步骤：
1. 离线：每个节点内容 → 切成小 chunk → 存入向量索引
2. 在线：query embedding → 搜索 top-K chunk → chunk 分数回溯到父节点
3. 节点排序：NodeScore = Σ ChunkScore(n) / √(N+1)
4. 取 top 节点作为候选
```

**公式设计巧妙之处**：
- `√(N+1)` 而非 `N` 作为分母：让有多个相关 chunk 的节点得分更高，但增长递减
- 防止大节点（chunk 多）因数量优势霸榜
- 偏好"少量高相关 chunk"的节点，而非"大量弱相关 chunk"的节点

**关键区别**：传统向量 RAG 返回 chunk 本身给 LLM；这里只用 chunk 分数给节点排序，
最终读的是节点对应的完整页面原文。

### 2.3 Hybrid Tree Search（生产版 — PageIndex Chat 实际使用）

**原理**：并行执行 LLM 搜索和 Value 搜索，用队列系统合并结果。

```
架构：

┌─────────────────────┐     ┌─────────────────────┐
│  LLM Tree Search    │     │ Value-based Search   │
│  (深度推理，慢)      │     │ (向量打分，快)        │
└────────┬────────────┘     └────────┬────────────┘
         │                           │
         ▼                           ▼
    ┌────────────────────────────────────┐
    │        去重队列 (Unique Queue)      │
    │  Value 搜索先返回 → 快速填充队列     │
    │  LLM 搜索后返回 → 补充深度推理结果   │
    └────────────────┬───────────────────┘
                     │
                     ▼
    ┌────────────────────────────────────┐
    │        节点消费者 (Consumer)         │
    │  逐个读取节点对应的页面内容           │
    │  提取/摘要相关信息                   │
    └────────────────┬───────────────────┘
                     │
                     ▼
    ┌────────────────────────────────────┐
    │        LLM Agent 充分性判断          │
    │  信息够了？→ 生成答案，终止           │
    │  不够？→ 继续消费队列 / 触发新搜索    │
    └────────────────────────────────────┘
```

**生产版的关键设计**：
1. **异步并行**：Value 搜索毫秒级返回，LLM 搜索秒级返回，队列先处理快的结果
2. **去重**：同一节点不会被重复读取
3. **渐进式**：不等所有搜索完成就开始读内容，减少总延迟
4. **早停**：LLM Agent 判断信息足够就立即终止，不浪费后续搜索

## 三、PageIndex Chat 的上下文管理机制

### 3.1 单次问答内的 Context 管理

PageIndex Chat 的核心优势不在树索引本身（你已经实现了），而在于 **agent loop 的状态管理**。

根据 systenics.ai 博客的实现参考和 PageIndex 官方文档推断，其 agent loop 采用了
**状态图（State Graph）** 架构，而非你当前的"单一 LLM 循环"：

```
你的系统（当前）：
  messages = [system, user, assistant+tool_calls, tool_result, assistant+tool_calls, tool_result, ...]
  → 所有历史堆积在 messages 里，LLM 每轮处理全部

PageIndex 的做法（推断）：
  state = {
    question: "...",
    toc: "...",                    # 树索引（常驻）
    collected_info: [...],         # 已收集的信息摘要（累积）
    selected_nodes: [...],         # 当前轮选中的节点
    iterations: 0,                 # 当前迭代次数
    answer: "",                    # 最终答案
    is_sufficient: false           # 充分性标记
  }
```

关键区别：
- **collected_info 是摘要池**，不是原始 tool result。每轮只保留最近 6 条相关信息
- **toc 常驻**，不需要每轮重新加载
- **LLM 每轮只看 state 的子集**，不看完整历史

### 3.2 跨问题的 Session 级复用

PageIndex Chat 宣传"no context limits"和"unlimited conversations"，核心机制：

1. **Structure 缓存**：同一文档的树索引只生成一次，后续问题直接复用
2. **已读页面索引**：session 级别维护"哪些页已读过、摘要是什么"
3. **Chat History 传递**：API 支持 `messages` 列表，多轮对话的上下文通过 messages 传递

从 Chat API 文档可以看到：
```python
messages = [
    {"role": "user", "content": "What is the main topic?"},
    {"role": "assistant", "content": "The main topic is climate change..."},
    {"role": "user", "content": "What solutions does it propose?"}
]
# 第二个问题可以利用第一个问题的上下文
```

### 3.3 充分性判断（Sufficiency Check）

这是 PageIndex 和你的系统最大的行为差异。

**你的系统**：LLM 在一次推理中同时完成"选节点 + 读内容 + 判断够不够 + 决定下一步"。
认知负担大，容易出现"读了一点就急着回答"的行为。

**PageIndex**：把流程拆成独立步骤，每步只做一件事：
```
navigate_toc()        → 只负责选节点
retrieve_sections()   → 只负责读内容
evaluate_and_answer() → 只负责判断够不够
```

evaluate_and_answer 的关键逻辑：
```python
# 只传最近收集的信息，不传完整历史
recent_context = "\n\n---\n\n".join(state["collected_info"][-6:])

prompt = f"""
Question: {question}
Relevant excerpts: {recent_context}
If enough information exists, answer. Otherwise respond NEED_MORE_INFO
"""
```

如果返回 NEED_MORE_INFO，流程回到 navigate_toc 继续搜索。

## 四、本系统现状与差距分析

### 4.1 已实现的能力（与 PageIndex 开源版对齐）

| 能力 | PageIndex 开源版 | 本系统 | 状态 |
|------|-----------------|--------|------|
| 层级树索引生成 | ✅ run_pageindex.py | ✅ ingest/pipeline.py | ✅ 已对齐 |
| LLM Tree Search | ✅ prompt 驱动 | ✅ qa_system.txt prompt | ✅ 已对齐 |
| Tool Call 循环 | ✅ 基础 loop | ✅ agent/loop.py | ✅ 已对齐 |
| 页面内容检索 | ✅ get_page_content | ✅ tools/page_content.py | ✅ 已对齐 |
| 引用追踪 | ✅ page-level cite | ✅ citation.py | ✅ 已对齐 |
| Context 持久化 | ❌ 开源版无 | ✅ context/ 模块 | ✅ 超越开源版 |

### 4.2 与 PageIndex Chat（商业版）的差距

| 能力 | PageIndex Chat | 本系统 | 优先级 |
|------|---------------|--------|--------|
| Hybrid Tree Search | ✅ LLM + Value 并行 | ❌ 仅 LLM Search | P2 |
| 显式充分性判断 | ✅ evaluate_and_answer 独立步骤 | ❌ 隐含在 LLM 推理中 | P0 |
| 已读页面摘要注入 | ✅ collected_info 池 | ❌ 原始 tool result 堆积 | P0 |
| Structure 跨问题缓存 | ✅ 同文档只读一次 | ❌ 每次问题重新读 | P1 |
| 已读页面去重 | ✅ 队列去重 | ❌ 可能重复读同一页 | P1 |
| 多文档对比 | ✅ doc_id 列表 | ❌ 单文档 | P3 |
| MCTS 搜索 | ✅ 提到但未开源 | ❌ | P3 |

## 五、改进方案（按优先级排序）

### P0：已读内容摘要注入（解决"复用性差"的核心问题）

**问题**：当前 messages 里堆积了所有 tool result 原文，LLM 在长 context 中注意力衰减，
早期读过的内容被"淹没"。

**方案**：在 agent loop 中维护一个 `read_pages_summary` 字典，每次读完页面后生成摘要，
在每轮 LLM 调用前注入到 system message 或 user message 中。

```python
# 伪代码
read_pages_summary = {}  # {page_num: "摘要文本"}

# 每次 get_page_content 返回后
for page in result["content"]:
    read_pages_summary[page["page"]] = page["text"][:200]  # 简单截取前200字符

# 每轮 LLM 调用前，注入已读摘要
summary_text = "\n".join(
    f"- p.{p}: {s}" for p, s in sorted(read_pages_summary.items())
)
inject_msg = f"[已读页面摘要]\n{summary_text}\n\n避免重复读取以上页面。"
```

**预期效果**：LLM 知道自己读过什么，不会重复读，也能基于摘要做跨页推理。

**改动范围**：仅 `src/agent/loop.py`，约 30 行代码。

### P0：显式充分性判断

**问题**：LLM 同时承担"选节点 + 读内容 + 判断够不够"的认知负担，容易提前结束。

**方案 A（轻量）**：在 prompt 中增加显式的充分性检查指令：
```
Before generating your final answer, explicitly verify:
1. Have you found direct evidence for EACH part of the question?
2. If the question asks about a process/mechanism, have you found ALL steps?
3. If any part is missing, call get_page_content for additional sections.
Only generate a final answer when ALL parts are covered.
```

**方案 B（重量，类似 PageIndex）**：把 loop 改成状态图，分离 navigate / retrieve / evaluate。
这需要较大重构，建议先用方案 A 验证效果。

### P1：Structure 跨问题缓存

**问题**：每个新问题都重新调用 `get_document_structure`，浪费 1-2 轮。

**方案**：在 `agentic_rag()` 函数中接受可选的 `cached_structure` 参数。
前端在同一文档的连续问题中，把上一次的 structure 传入。

或者更简单：在 system prompt 中直接注入已缓存的 structure 摘要，
告诉 LLM "以下是文档结构概览，你已经看过了，不需要再调用 get_document_structure"。

**改动范围**：`src/agent/loop.py` + `src/web/app.py`（传递缓存），约 50 行。

### P1：已读页面去重

**问题**：LLM 可能在 Turn 3 读了 p.45-50，Turn 7 又请求 p.47。

**方案**：在 `execute_tool` 层面拦截重复请求：
```python
# 在 loop.py 中维护
read_pages_cache = {}  # {(doc_name, page): content}

# execute_tool 时检查
if name == "get_page_content":
    # 过滤掉已读页面，只请求新页面
    # 对已读页面直接返回缓存
```

**改动范围**：`src/agent/loop.py`，约 20 行。

### P2：Hybrid Tree Search（Value-based 加速）

**问题**：纯 LLM Tree Search 慢，且完全依赖摘要质量。

**方案**：在 ingest 阶段为每个节点预计算 embedding，查询时并行执行：
1. LLM 推理选节点（现有逻辑）
2. 向量搜索给节点打分（新增）
3. 合并两个结果的节点列表

**实现要点**：
- ingest 阶段：每个节点内容 → 切 chunk → 计算 embedding → 存储
- 查询阶段：query embedding → 搜索 top-K chunk → 回溯到节点 → 打分排序
- 节点分数公式：`NodeScore = Σ ChunkScore(n) / √(N+1)`

**改动范围**：需要新增 embedding 模块，改动 ingest pipeline 和 agent loop，约 200 行。
这是较大的改动，建议在 P0/P1 完成后再考虑。

### P3：多文档对比 & MCTS

这些是 PageIndex Chat 的高级功能，当前阶段不建议投入。
多文档对比需要改 tool schema 支持多 doc_name，MCTS 需要训练 value function。

## 六、实施建议

### 第一阶段（立即可做，1-2 天）
1. P0：已读内容摘要注入 — 改 loop.py，维护 read_pages_summary
2. P0：prompt 增加充分性检查指令 — 改 qa_system.txt
3. P1：已读页面去重 — 改 loop.py，加 cache

### 第二阶段（1 周）
4. P1：Structure 跨问题缓存 — 改 loop.py + app.py
5. 利用已有的 Context Management System 接入 loop（你已经建好了基础设施）

### 第三阶段（2-3 周）
6. P2：Hybrid Tree Search — 新增 embedding 模块
7. 状态图重构（可选）— 把 loop 改成 navigate/retrieve/evaluate 三步分离

## 七、结论

你的系统在架构层面已经和 PageIndex 开源版完全对齐，甚至在 Context 持久化方面超越了它。
当前"已读内容复用性差"的问题不是架构缺陷，而是 agent loop 的状态管理策略不够精细。

PageIndex Chat 的核心竞争力不在"无向量"（它实际上用了向量），而在于：
1. **Hybrid Search 的速度 + 精度平衡**
2. **显式的充分性判断循环**
3. **已读内容的摘要化管理**

这三点中，第 2 和第 3 点可以用最小改动量实现，预期能显著改善回答质量。

---
*报告生成时间：2026-03-13*
*数据来源：PageIndex 官方文档 (docs.pageindex.ai)、GitHub 开源代码、systenics.ai 技术博客*
