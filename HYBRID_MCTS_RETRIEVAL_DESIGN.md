# Hybrid Tree Search + MCTS-Style 节点探索策略 — 设计方案

> 本文并非复现 PageIndex 未公开的内部 MCTS 细节，而是在其"LLM tree search + value function-based MCTS"公开框架基础上，提出一套面向文档树检索的 MCTS-style 调度机制。

---

## 0. 一句话定义

在 PageIndex 官方公开的 hybrid tree search 框架基础上，结合 value prior、树邻域扩展、后验证据回传与 early-stop 机制构建的 MCTS-style 节点探索策略。

核心思想：**快路先供给 node，慢路负责补漏，consumer 边消费边判断是否足够，从而尽早停机。**

---

## 1. 设计目标

PageIndex 的根本思路不是"更好的 top-k chunk"，而是：
- 先把文档组织成自然结构树
- 围绕树做检索，node 是主检索单位
- 不过度依赖 chunking，利用文档结构和 LLM reasoning
- 保留页码与章节可追溯性

Hybrid 要解决纯 LLM tree search 的两个问题：
1. 纯 LLM tree reasoning 慢
2. 只看 summary 可能漏细节

因此引入 value-based tree search 给 node 提供快速先验，再与 LLM-based tree search 合流。

---

## 2. 核心对象：Node，不是 Chunk

这是与常见向量 RAG 最本质的区别。

### 主对象：Document Tree Node

```
Node:
    node_id: str
    title: str
    summary: str
    start_index: int        # 起始页码
    end_index: int          # 结束页码
    start_line: int         # 页内起始行号
    end_line: int           # 页内结束行号
    parent_id: str | None
    child_ids: list[str]
    is_leaf: bool           # 是否为叶节点（有原文内容）
```

### 次对象：Chunk Evidence

只在 value-based 分支里作为"证据粒度"存在，用于给 node 估值，不是系统的最终返回对象。

```
ChunkEvidence:
    chunk_id: str
    node_id: str            # 所属节点
    text: str               # 原文切片
    embedding: list[float]
```

---

## 3. 官方已公开的部分（不可乱写的底座）

以下内容来自 PageIndex 官方公开文档：

1. **LLM tree search**：让 LLM 根据 query 和 document tree structure 返回可能相关的 node。
2. **Value-based tree search**：对每个 node 预测一个 value；一个简单实现是把每个 node 再切成 smaller chunks，用 query 搜 top-K relevant chunks，再把 chunk 的相似度聚合回 parent node，得到 node score。
3. **Hybrid tree search**：value-based 和 LLM-based 并行，把返回的 node 放进唯一节点队列，由 consumer 消费节点内容，LLM agent 持续判断是否已收集到足够信息，可以 early stop。
4. **Retrieval API / Dashboard**：官方明确说使用了 "LLM tree search + value function-based MCTS"，但更细节未公开。

---

## 4. 本方案的增强部分（MCTS-style 工程化改造）

以下是在官方公开框架之上新增的设计，属于面向文档树检索场景的自定义工程实现。

### 4.1 与标准 MCTS 的映射

| MCTS 概念 | 文档树检索对应 |
|---|---|
| State | 已访问节点集合 + 已积累 evidence + 候选节点池 |
| Action | 选择下一个要消费的叶节点 |
| Reward | 消费节点后提取到的 evidence 价值 |
| Simulation | 用 summary/chunk embedding 做 lightweight value estimation |
| Backpropagation | 将 evidence reward 沿祖先路径回传为 subtree_value |
| UCB/Selection | UCB-style 节点选择函数（prior + exploration bonus） |

### 4.2 与标准 MCTS 的关键区别

- 叶节点在一次查询中通常只被消费 0 或 1 次，不会反复访问
- Backprop 传递的不是"未来回报"，而是"结构相关性信号"
- Decay 应比博弈树更平缓，因为文档树的父子关系是语义包含关系

---

## 5. 数据流

### 5.1 索引阶段

```
Document
  → PDF parse + page_index 构建自然结构树
  → 每个节点记录 title, summary, page_range, start_line, end_line
  → 叶节点原文按 start_line/end_line 切片
  → 切片按 token 窗口切成 smaller chunks
  → embed(chunk) 存储为 ChunkEvidence
```

跨页切片逻辑：
```python
if start_page == end_page:
    text = page_text[start_line:end_line]
else:
    text = page_text[start_page][start_line:]       # 首页：从 start_line 到页尾
    for p in range(start_page + 1, end_page):
        text += page_text[p]                        # 中间页：整页
    text += page_text[end_page][:end_line]          # 末页：从页首到 end_line
```

### 5.2 查询阶段总流程

```
Query
  ├─ Branch A: Value-based node scoring (快路)
  │     query embedding → search top-K chunks → 聚合回 node score
  ├─ Branch B: LLM tree reasoning (慢路，按需触发)
  │     query + tree structure → LLM 返回候选 node_id 列表
  └─ 候选 node 进入 MCTS-style 探索循环
        → Selection (UCB-style)
        → Expansion (结构邻域 + 语义候选)
        → Consume leaf node → extract evidence
        → Backpropagation (subtree_value 回传)
        → judge_enough? → early stop
        → Answer synthesis
```

---

## 6. 节点状态模型

```python
@dataclass
class NodeState:
    prior_score: float      # 静态先验，来自 value-based 检索（chunk 聚合）
    visits: int = 0         # 被选择/扩展次数
    subtree_value: float = 0.0  # 由已读叶节点 evidence 回传得到的子树价值
```

三类量严格分开维护：
- `prior_score`：索引阶段计算，查询时不修改
- `visits`：每次被选中时 +1
- `subtree_value`：仅由 backprop 更新，不与 prior 混合

---

## 7. 两层分工：路由层 vs 消费层

### 核心原则

中间节点（`is_skeleton: true`，无原文）只做路由，不进消费候选池。
叶节点（有 `start_line`/`end_line`，有原文）才是消费对象。

```
中间节点：路由层
    用 internal_select_score 决定"往哪棵子树继续挖"
    subtree_value 在这一层发挥作用

叶节点：消费层
    用 leaf_select_score 决定"下一个读谁"
    prior_score 在这一层发挥作用

backprop：连接两层
    叶节点的 evidence reward 回传到中间节点的 subtree_value
```

---

## 8. Selection — 节点选择

### 叶节点选择

```
leaf_select_score(node) = α * prior_score(node) + c * exploration_bonus(node)
```

- `prior_score`：来自 value-based 检索（chunk embedding 聚合）
- `exploration_bonus`：鼓励访问次数低的节点

```python
exploration_bonus(node) = sqrt(log(1 + total_selections) / (1 + visits(node)))
```

### 中间节点选择（路由决策）

```
internal_select_score(node) = α * prior_score(node)
                            + β * subtree_value(node)
                            + c * exploration_bonus(node)
```

- `subtree_value`：该节点子树中已读叶节点 evidence 的回传价值
- 用于决定"往哪棵子树继续扩展"

### 参数建议

| 参数 | 建议初始值 | 含义 |
|------|-----------|------|
| α | 1.0 | prior 权重 |
| β | 0.5 | subtree_value 权重（仅中间节点） |
| c | 1.4 | 探索系数 |

---

## 9. Expansion — 候选扩展

读完一个叶节点后，扩展候选池：

```
expand(node) = structural_neighbors(node)
             ∪ semantic_candidates(node, query)
             ∪ llm_suggested_nodes_if_needed(node, query)
```

### 结构邻域扩展

```python
structural_neighbors(node) = {node.parent, *node.siblings, *node.children}
```

### 语义候选补充

避免困死在局部子树：
- 同 query 下仍高 prior 但未访问的节点
- 与当前 node title/summary 语义相近的节点

```python
semantic_candidates(query, tree, visited) = 
    top_m(n for n in tree.leaves if n.id not in visited, 
          key=prior_score, m=3)
```

### LLM 候选补充（按需触发）

当当前局部子树证据不足时，触发 LLM tree search 补充候选：

```python
if not judge_enough(query, evidence) and expansion_stalled:
    llm_nodes = llm_tree_search(query, tree, evidence)
    candidates |= llm_nodes
```

这把 PageIndex 官方 hybrid 的两路检索融进了 MCTS-style exploration：
value 分支给 prior，LLM 分支给补充候选。

---

## 10. Simulation — 轻量值估计

不是严格意义上的 MCTS rollout，而是对新候选节点的快速估分：

```python
def simulate_value(node, query):
    return λ1 * summary_sim(node, query) + λ2 * value_prior(node)
```

- `summary_sim`：query embedding 与 summary embedding 的余弦相似度
- `value_prior`：如果已有 chunk-aggregated node score，直接复用

---

## 11. Backpropagation — 结构相关性回传

### 语义定义

不是"奖励向上衰减传播"，而是：
**命中叶节点后，对其上层结构节点赋予子树相关性信号。**

- 当前节点：完全命中
- 父节点：同样强，因为它就是当前子树入口
- 祖父节点：仍然很强
- 再往上才逐步衰减

### 回传公式

```python
def backpropagate(node, tree, stats, reward):
    depth = 0
    cur = node
    while cur is not None:
        stats[cur.id].visits += 1
        stats[cur.id].subtree_value += reward * decay(depth)
        cur = tree.parent(cur)
        depth += 1
```

### 层级敏感衰减

```python
def decay(depth):
    if depth <= 1:        # 当前节点 + 父节点：不衰减
        return 1.0
    return 0.8 ** (depth - 1)  # 祖父及以上：逐步衰减
```

比经典博弈树衰减更平缓，因为文档树的父子关系是语义包含关系。

---

## 12. Reward — 节点消费价值

一个 node 被消费后，可能有三种不同价值：

| 类型 | 含义 | 示例 |
|------|------|------|
| answer_gain | 直接含答案 | 找到了定义句、具体数值 |
| routing_gain | 帮助确定下一步方向 | 确认了相关章节位置 |
| wasted_cost | 明确不相关，浪费了读取 | 完全无关的内容 |

```python
def compute_reward(evidence):
    return w1 * answer_gain + w2 * routing_gain - w3 * wasted_cost
```

建议初始权重：`w1=1.0, w2=0.3, w3=0.1`

---

## 13. Node Value 聚合 — chunk → node

value-based 分支的核心不是 chunk top-k，而是 chunk → node 聚合：

```python
node_score = sum(top_chunk_scores_for_node) / sqrt(num_matched_chunks + 1)
```

- 奖励命中多个相关 chunks 的 node
- 但不让"大 node 因为 chunk 多"天然占优

---

## 14. Judge Enough — 停止条件

### 三级停止策略

| Level | 策略 | 适用场景 |
|-------|------|---------|
| L1 | 硬阈值：读到 N 个 node 就停 | 快速兜底 |
| L2 | 规则：命中定义句 + 至少 2 个相关 node | 文档问答主力 |
| L3 | LLM 判断：当前 evidence 是否足够 | 复杂推理题 |

建议先上 L2 规则停止，避免 LLM judge 增加调用次数：

```python
def judge_enough(query, evidence):
    if len(evidence) >= 3:
        return True
    if len(evidence) >= 2 and has_direct_answer(evidence):
        return True
    return False
```

---

## 15. 完整伪代码

```python
def mcts_hybrid_retrieve(query, tree):
    evidence = []
    visited = set()

    # 初始化：value-based 快路产出种子候选
    stats = {
        n.id: NodeState(prior_score=init_prior_score(n, query))
        for n in tree.all_nodes()
    }
    candidates = set(top_k_leaves_by_prior(tree, query, k=8))

    total_selections = 0

    while candidates:
        # ── Selection ──
        # 只在叶节点里选下一个消费对象
        unvisited_leaves = [n for n in candidates if n.is_leaf and n.id not in visited]
        if not unvisited_leaves:
            break

        node = argmax(leaf_select_score(n, stats, total_selections) 
                      for n in unvisited_leaves)
        total_selections += 1

        # ── Consume ──
        ev = consume_node(node, query)
        evidence.append(ev)
        visited.add(node.id)

        # ── Reward ──
        reward = compute_reward(ev)

        # ── Backpropagation ──
        backpropagate(node, tree, stats, reward)

        # ── Expansion ──
        # 结构邻域
        new_nodes = structural_neighbors(node, tree)

        # 语义候选补充
        new_nodes |= semantic_candidates(query, tree, visited)

        # LLM 候选补充（按需）
        if not judge_enough(query, evidence) and expansion_stalled(candidates, visited):
            new_nodes |= llm_tree_search(query, tree, evidence)

        # 对新候选做 lightweight value estimation
        for n in new_nodes:
            if n.id not in visited:
                stats[n.id].prior_score = max(
                    stats[n.id].prior_score,
                    simulate_value(n, query)
                )
                candidates.add(n)

        # ── 路由决策：用中间节点 score 决定是否深入某子树 ──
        for internal in get_internal_ancestors(node, tree):
            if internal_select_score(internal, stats, total_selections) > threshold:
                candidates |= set(internal.leaf_descendants()) - visited

        # ── Early Stop ──
        if judge_enough(query, evidence):
            break

    return synthesize_answer(query, evidence)
```

---

## 16. 与普通向量 RAG 的本质区别

```
普通向量 RAG:
    query → chunk embeddings → top-k chunks → rerank → answer

本方案:
    query → node-centric retrieval
        ├─ value-based node scoring (chunk → node 聚合)
        ├─ MCTS-style selection / expansion / backprop
        ├─ LLM tree reasoning (按需补充候选)
        └─ unique node queue → leaf consumer → judge enough → answer
```

核心区别不是"有没有向量"，而是：
- 主检索对象是 node，向量只是 value/evidence
- 结构推理是正式分支，不只是 rerank
- 系统有 consumer + early stop
- 探索顺序由树结构动态驱动，不是静态 top-k

---

## 17. 实现优先级

| 阶段 | 内容 | 预期收益 |
|------|------|---------|
| P1 | value-based node scoring（chunk embedding 聚合） | 替换逐 part LLM 搜索，速度大幅提升 |
| P2 | judge_enough + early stop | 减少不必要的 node 消费 |
| P3 | MCTS-style selection / expansion / backprop | 探索质量提升，毕设研究价值 |
| P4 | LLM tree reasoning 按需触发 | 处理跨节点组合推理 |

---

## 18. 方法论定性

- `prior_score`：来自 hybrid 的 value-based 分支，负责全局先验
- `subtree_value`：来自已消费节点 evidence 的结构回传，负责局部子树强化
- `exploration_bonus`：防止一直读相邻高分节点，鼓励适度探索
- `judge_enough`：对应官方 hybrid 中 LLM agent 的 early-stop 机制
- `backpropagation`：在官方公开流程之上新增的 MCTS-style 强化机制
