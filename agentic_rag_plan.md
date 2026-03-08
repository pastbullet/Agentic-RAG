# Agentic RAG 实施方案

## 一、架构定位

本系统复现 PageIndex 的核心范式：**LLM 通过 Tool-Use 自主导航文档索引树，按需提取页面内容，推理判断信息充足性，生成带引用的答案。**

### 1.1 "智能在哪里"的正确理解

常见误区是把 Tool-Use Agent 理解为"代码什么都不做，全靠 LLM"。实际上，系统智能分布在三个层面：

| 层面 | 负责什么 | 影响什么 |
|------|---------|---------|
| **索引质量**（离线） | summary 的准确性、树的粒度、节点的 metadata | LLM 能否找到正确的章节 |
| **Tool 响应设计**（代码） | 返回什么字段、next_steps 怎么写、内容如何截断 | LLM 每一步能获得多少有效信息 |
| **LLM 推理**（在线） | 导航路径选择、信息充足性判断、答案生成 | 最终答案质量 |

三者缺一不可。索引质量差，LLM 推理能力再强也找不到正确章节；Tool 响应设计差，LLM 拿到的信息不足以做出正确判断；LLM 推理弱，即使给了完美的索引和内容也会生成错误答案。

**因此：本方案在每个 Phase 中都同时关注这三个层面，而不是只关注代码或只关注 prompt。**

### 1.2 系统架构

```
                         ┌──────────────┐
                         │   用户查询    │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │  Agent Loop  │ ← 代码只做 tool call 转发
                         │  (循环引擎)   │
                         └──────┬───────┘
                                │
                    ┌───────────┼───────────┐
                    │                       │
             ┌──────▼───────┐        ┌──────▼───────┐
             │ get_document │        │ get_page     │
             │ _structure() │        │ _content()   │
             └──────┬───────┘        └──────┬───────┘
                    │                       │
           ┌────────▼────────┐     ┌────────▼────────┐
           │  chunks_3/      │     │  output/json/   │
           │  分块索引树      │     │  页面内容数据库  │
           └─────────────────┘     └─────────────────┘

LLM 自主决定：
  看哪个 part → 选哪个章节 → 取哪些页 → 信息够不够 → 生成答案
```

### 1.3 已有产物盘点

| 产物 | FC-LS | BFD | 状态 |
|------|-------|-----|------|
| 源 PDF | `data/raw/FC-LS.pdf` (210页) | `data/raw/rfc5880-BFD.pdf` (49页) | ✅ |
| PageIndex 索引 | `data/out/FC-LS_5_page_index.json` | `data/out/BFD_page_index.json` | ✅ |
| 分块索引树 | `data/out/chunks_3/FC-LS/` (9 parts) | 无 | ⚠️ BFD 待生成 |
| 页面内容库 | `output/json/content_*.json` (11 files) | 无 | ⚠️ BFD 待生成 |

---

## 二、Phase 0：Tool 实现 + 数据准备

### 2.1 目标

将已有的索引数据和内容数据包装成两个 Tool 函数，使 LLM 可通过 function calling 调用。

### 2.2 数据准备

#### 2.2.1 为 BFD 生成分块索引树

```bash
python structure_chunker.py \
  --input data/out/BFD_page_index.json \
  --max-limit 70000 \
  --output-root data/out/chunks_3
```

预期产出：`data/out/chunks_3/BFD/` 目录，BFD 文档较小（49页），预计 1-2 个 part。

#### 2.2.2 为 BFD 生成页面内容库

```bash
python build_content_db.py \
  --pdf-path data/raw/rfc5880-BFD.pdf \
  --output-dir output_bfd \
  --chunk-size 20
```

预期产出：`output_bfd/json/content_1_20.json`, `content_21_40.json`, `content_41_49.json`。

#### 2.2.3 文档注册表

建立 `doc_name` 到数据路径的映射，避免路径硬编码：

```python
# src/tools/registry.py

DOC_REGISTRY = {
    "FC-LS.pdf": {
        "chunks_dir": "data/out/chunks_3/FC-LS",
        "content_dir": "output/json",
        "total_pages": 210,
    },
    "rfc5880-BFD.pdf": {
        "chunks_dir": "data/out/chunks_3/BFD",
        "content_dir": "output_bfd/json",
        "total_pages": 49,
    },
}
```

### 2.3 Tool 实现

#### 2.3.1 `get_document_structure`

```python
# src/tools/document_structure.py

def get_document_structure(doc_name: str, part: int = 1) -> dict:
    """
    返回文档目录树的第 part 个分块。

    数据来源：chunks_3/{doc_stem}/part_{part:04d}.json
    返回内容：structure(节点树) + next_steps(导航提示) + pagination(分页信息)
    """
```

**设计决策（这些是"代码侧智能"）：**

- **next_steps 的措辞影响 LLM 行为**：不是随便写的提示，而是引导 LLM 做出正确动作的关键信号。例如 `"Proceed to get_page_content() for specific sections"` 暗示 LLM 应该在找到目标节点后切换到内容提取。
- **manifest 信息前置**：在第一次调用时，在响应中附加文档整体信息（总页数、总 part 数），帮助 LLM 建立全局认知。
- **part 越界处理**：返回明确的错误提示和有效范围，而不是空结果，避免 LLM 陷入错误循环。

#### 2.3.2 `get_page_content`

```python
# src/tools/page_content.py

def get_page_content(doc_name: str, pages: str) -> dict:
    """
    返回指定页码范围的实际内容。

    数据来源：output/json/content_{start}_{end}.json
    支持格式："7-11", "7", "7,9,11"
    返回内容：content([{page, text, tables, images}]) + next_steps + total_pages
    """
```

**设计决策：**

- **单次最大页数限制**：设置上限（如 10 页），超过时返回提示让 LLM 分批请求。防止一次返回太多内容挤占 context window。
- **内容截断策略**：单页文本超过阈值（如 4000 字符）时截断并标注 `[内容已截断，请缩小页码范围]`。截断位置选在段落边界而非字符中间。
- **表格保留**：表格对协议文档至关重要（字段定义、状态转换表等），始终完整返回 markdown 表格，不截断。
- **next_steps 设计**：返回的 next_steps 要包含引用格式提示（`<cite doc="..." page="N"/>`），在 LLM 读完内容后立即提醒引用规范。

#### 2.3.3 Tool Schema 定义

```python
# src/tools/schemas.py

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_document_structure",
            "description": (
                "获取文档的目录树索引。每个节点包含标题(title)、内容摘要(summary)、"
                "页码范围(start_index/end_index)。通过阅读摘要来判断章节与问题的相关性。"
                "大文档的目录树会分成多个 part，用 part 参数翻页浏览。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_name": {
                        "type": "string",
                        "description": "文档名称，如 'FC-LS.pdf'"
                    },
                    "part": {
                        "type": "integer",
                        "description": "目录树分页编号，从 1 开始。首次调用使用 1。",
                        "default": 1
                    }
                },
                "required": ["doc_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_content",
            "description": (
                "获取文档指定页码的实际内容，包括文本和表格。"
                "页码范围从目录树节点的 start_index 和 end_index 获得。"
                "单次请求不超过 10 页。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_name": {
                        "type": "string",
                        "description": "文档名称"
                    },
                    "pages": {
                        "type": "string",
                        "description": "页码或页码范围，如 '7-11' 或 '75'"
                    }
                },
                "required": ["doc_name", "pages"]
            }
        }
    }
]
```

**Schema description 的措辞也是"代码侧智能"**：
- `"通过阅读摘要来判断章节与问题的相关性"` — 引导 LLM 利用 summary 而不是盲目翻页
- `"单次请求不超过 10 页"` — 在 schema 层面约束 LLM 行为
- `"页码范围从目录树节点的 start_index 和 end_index 获得"` — 建立两个 Tool 之间的工作流关系

### 2.4 验收标准

- [ ] `get_document_structure("FC-LS.pdf", 1)` 返回与 `chunks_3/FC-LS/part_0001.json` 一致的 JSON
- [ ] `get_document_structure("FC-LS.pdf", 10)` 返回 part 越界的错误提示
- [ ] `get_page_content("FC-LS.pdf", "75-76")` 返回第 75-76 页的文本和表格
- [ ] `get_page_content("FC-LS.pdf", "1-20")` 触发单次页数限制提示
- [ ] BFD 的分块索引和内容库已生成

---

## 三、Phase 1：Agent Loop + MVP 问答

### 3.1 目标

实现 Agent 循环，端到端跑通 "用户提问 → LLM 自主导航 → 生成答案"。

### 3.2 LLM 适配层

需要统一 OpenAI 和 Anthropic 的 tool calling 接口差异：

```python
# src/agent/llm_adapter.py

class LLMAdapter:
    """统一的 LLM 调用接口，屏蔽 OpenAI/Anthropic 的 tool calling 差异。"""

    def __init__(self, provider: str, model: str):
        ...

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMResponse:
        """
        返回统一的 LLMResponse：
        - has_tool_calls: bool
        - tool_calls: list[ToolCall]  (name, arguments, id)
        - text: str | None (最终回答)
        - usage: TokenUsage (prompt_tokens, completion_tokens)
        """
```

**OpenAI 与 Anthropic 的关键差异：**

| | OpenAI | Anthropic |
|---|---|---|
| Tool 定义格式 | `{"type": "function", "function": {...}}` | `{"name": ..., "input_schema": {...}}` |
| Tool call 响应 | `message.tool_calls[].function` | `content[].type == "tool_use"` |
| Tool result 格式 | `{"role": "tool", "tool_call_id": ...}` | `{"role": "user", "content": [{"type": "tool_result", ...}]}` |
| 并行 tool call | 支持（一次返回多个 tool_call） | 支持 |

适配层需要处理这些差异，对上层暴露统一接口。已有的 `utils.py` 中的 `_build_openai_client` 和 `_build_anthropic_client` 可以复用。

### 3.3 Agent Loop 实现

```python
# src/agent/loop.py

async def agentic_rag(
    query: str,
    doc_name: str,
    model: str = None,
    max_turns: int = 15,
) -> RAGResponse:
    """
    核心 Agent 循环。

    代码只做三件事：
    1. 把 query + system prompt + tools 发给 LLM
    2. 如果 LLM 返回 tool_call，执行 tool 并把结果喂回去
    3. 如果 LLM 返回 text，作为最终答案返回

    代码不做任何检索决策。
    """
    adapter = LLMAdapter(provider=..., model=model)
    system_prompt = load_system_prompt()
    tool_schemas = get_tool_schemas()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"文档：{doc_name}\n\n问题：{query}"}
    ]

    trace = []  # 记录完整的 tool call trace

    for turn in range(max_turns):
        response = await adapter.chat_with_tools(messages, tool_schemas)

        if response.has_tool_calls:
            messages.append(response.raw_message)
            for tc in response.tool_calls:
                result = execute_tool(tc.name, tc.arguments)
                trace.append(ToolCallRecord(
                    turn=turn,
                    tool=tc.name,
                    arguments=tc.arguments,
                    result_summary=summarize_result(result),
                ))
                messages.append(make_tool_result(tc.id, result))
        else:
            # LLM 给出了最终答案
            return RAGResponse(
                answer=response.text,
                trace=trace,
                total_turns=turn + 1,
                total_tokens=sum_tokens(trace),
            )

    # 安全阀：超过 max_turns
    return RAGResponse(
        answer="[达到最大轮次限制，未能生成完整答案]",
        trace=trace,
        total_turns=max_turns,
        ...
    )
```

**关于 `max_turns` 的说明：**
- 这不是"检索迭代次数"，而是 Agent 的安全阀
- 一次 `get_document_structure` + 一次 `get_page_content` 就要 2 个 turn
- FC-LS 大文档可能需要翻多个 part（3-4 次 structure 调用）+ 多次 content 调用
- 设置 15 比较安全，正常查询通常 4-8 turn 内完成

### 3.4 System Prompt（初版）

```markdown
你是一个协议文档问答助手。你的任务是基于文档内容准确回答用户的技术问题。

## 可用工具

1. **get_document_structure(doc_name, part)**
   获取文档的目录树索引。目录树中每个节点包含章节标题、内容摘要和页码范围。
   通过阅读节点的摘要(summary)来判断该章节是否与问题相关。
   大文档的目录树会分成多个 part，通过 part 参数翻页。

2. **get_page_content(doc_name, pages)**
   获取指定页码范围的实际文档内容。页码从目录树节点的 start_index/end_index 获得。

## 工作流程

1. 调用 get_document_structure 查看文档的目录树
2. 阅读各章节的标题和摘要，推理哪些章节最可能包含回答所需的信息
3. 对于相关章节，调用 get_page_content 获取其实际内容
4. 阅读内容后，判断是否已有足够信息回答问题
   - 如果信息充足：直接生成答案
   - 如果信息不足：继续查看其他章节
5. 如果文档中看到交叉引用（如"see section X"），主动跳转查找被引用的章节

## 注意事项

- 只基于你实际读取到的文档内容回答，不要编造信息
- 如果文档中没有足够信息回答问题，明确说明
- 不要一次请求过多页面，聚焦于最相关的章节
```

**Prompt 设计要点：**
- 第一步明确是 `get_document_structure`，建立"先看目录再取内容"的行为模式
- 强调"阅读摘要来判断相关性"，利用索引中预生成的 summary
- 交叉引用提示——协议文档大量使用 "see section X" 互相引用
- "不要一次请求过多页面"——控制 context 消耗

### 3.5 Tool 执行器

```python
# src/agent/loop.py

def execute_tool(name: str, arguments: dict) -> dict:
    """路由 tool call 到对应的函数实现。"""
    if name == "get_document_structure":
        return get_document_structure(**arguments)
    elif name == "get_page_content":
        return get_page_content(**arguments)
    else:
        return {"error": f"Unknown tool: {name}"}
```

### 3.6 CLI 入口

```python
# src/main.py

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", required=True, help="文档名称，如 FC-LS.pdf")
    parser.add_argument("--query", required=True, help="用户问题")
    parser.add_argument("--model", default=None, help="LLM 模型名称")
    parser.add_argument("--verbose", action="store_true", help="打印完整 tool call trace")
    args = parser.parse_args()

    response = await agentic_rag(args.query, args.doc, args.model)

    if args.verbose:
        print_trace(response.trace)
    print(response.answer)
```

使用方式：

```bash
python -m src.main --doc rfc5880-BFD.pdf --query "BFD 控制报文有哪些字段？" --verbose
```

### 3.7 验收标准

- [ ] BFD 简单问题端到端可回答（如"BFD 的控制报文格式"）
- [ ] FC-LS 问题能正确导航多 part 目录树（如"FLOGI 的 payload 格式"）
- [ ] `--verbose` 模式能打印完整的 tool call 序列，可观察 LLM 的导航路径
- [ ] OpenAI 和 Anthropic 两个 provider 都能跑通

---

## 四、Phase 2：引用 + 答案质量

### 4.1 目标

答案中关键信息带精确页码引用，整体答案质量提升到可演示水平。

### 4.2 引用实现

#### 4.2.1 在 System Prompt 中加入引用要求

在 Phase 1 的 system prompt 基础上追加：

```markdown
## 引用规则

对答案中的每个关键信息点，使用以下格式标注来源：
<cite doc="文档名" page="页码"/>

示例：BFD 控制报文的版本号字段定义协议版本为 1 <cite doc="rfc5880-BFD.pdf" page="7"/>

注意：
- 使用单页页码（page="7"），不要用范围（不要写 page="7-11"）
- 只引用你通过 get_page_content 实际读取过的页面
- 一条信息来自多页时，分别标注每页的引用
```

#### 4.2.2 引用后处理

```python
# src/models.py

class Citation(BaseModel):
    doc_name: str
    page: int
    context: str  # 引用所在的文本片段

class RAGResponse(BaseModel):
    answer: str                     # 包含 <cite/> 标签的原始答案
    answer_clean: str               # 去除 <cite/> 标签的纯文本答案
    citations: list[Citation]       # 提取的引用列表
    trace: list[ToolCallRecord]     # 完整的 tool call 记录
    pages_retrieved: list[int]      # 实际检索过的页码
    total_turns: int
```

```python
# src/agent/citation.py

import re

def extract_citations(answer: str) -> list[Citation]:
    """从答案文本中解析 <cite doc="..." page="..."/> 标签。"""
    pattern = r'<cite\s+doc="([^"]+)"\s+page="(\d+)"\s*/>'
    ...

def validate_citations(citations: list[Citation], pages_retrieved: list[int]) -> list[str]:
    """检查引用的页码是否在实际检索的页码范围内。返回警告列表。"""
    warnings = []
    for c in citations:
        if c.page not in pages_retrieved:
            warnings.append(f"引用了未检索的页面: page {c.page}")
    return warnings
```

### 4.3 答案质量提升

#### 4.3.1 交叉引用跟踪

协议文档大量使用交叉引用（"see section 6.8.3", "as defined in 4.1"）。在 system prompt 中强化这个行为：

```markdown
## 交叉引用

协议文档中经常出现 "see section X"、"as defined in Y" 等交叉引用。
当你在阅读内容时遇到这类引用，且被引用的内容对回答问题有帮助时：
1. 回到目录树找到对应章节
2. 调用 get_page_content 获取被引用的内容
3. 将两部分信息综合后回答
```

#### 4.3.2 信息不足的处理

```markdown
## 信息不足时

如果查阅了多个相关章节后仍然无法完整回答问题：
- 明确说明"基于文档中的信息，可以确认以下内容：..."
- 列出已找到的相关信息
- 指出缺失的部分
不要为了凑完整答案而编造内容。
```

### 4.4 验收标准

- [ ] 答案中关键信息均有 `<cite/>` 标注
- [ ] 引用页码与实际内容一致（通过 `validate_citations` 检查）
- [ ] 遇到交叉引用时 LLM 能主动跟踪查找
- [ ] 信息不足时不编造，而是明确标注

---

## 五、Phase 3：评测 + 优化

### 5.1 目标

构建测试集，量化系统表现，通过失败分析针对性优化。

### 5.2 测试集设计

为 BFD 和 FC-LS 各编写 10-15 个测试问题，覆盖以下类型：

```json
// data/eval/test_questions.json
[
    {
        "id": "bfd-01",
        "doc_name": "rfc5880-BFD.pdf",
        "query": "BFD 控制报文的强制部分包含哪些字段？",
        "type": "format",
        "expected_sections": ["4.1"],
        "expected_pages": [7, 8, 9, 10],
        "key_points": [
            "Version", "Diagnostic", "State",
            "Poll/Final/Control Plane Independent/Auth/Demand/Multipoint",
            "Detect Mult", "Length",
            "My Discriminator", "Your Discriminator",
            "Desired Min TX Interval",
            "Required Min RX Interval",
            "Required Min Echo RX Interval"
        ]
    },
    {
        "id": "bfd-02",
        "doc_name": "rfc5880-BFD.pdf",
        "query": "BFD 状态机有哪些状态？状态之间如何转换？",
        "type": "state_machine",
        "expected_sections": ["6.2"],
        "expected_pages": [16, 17],
        "key_points": ["Init", "Up", "Down", "AdminDown", "three-way handshake"]
    },
    {
        "id": "bfd-03",
        "doc_name": "rfc5880-BFD.pdf",
        "query": "BFD 的 Demand 模式是如何工作的？",
        "type": "procedure",
        "expected_sections": ["6.6"],
        "expected_pages": [19, 20, 21],
        "key_points": ["D bit", "Poll Sequence", "Detection Time", "periodic transmission halted"]
    },
    {
        "id": "bfd-04",
        "doc_name": "rfc5880-BFD.pdf",
        "query": "BFD 的 Detection Time 是如何计算的？",
        "type": "procedure",
        "expected_sections": ["6.8.4"],
        "expected_pages": [32, 33],
        "key_points": ["Detect Mult", "transmit interval", "Asynchronous mode", "Demand mode"]
    },
    {
        "id": "fcls-01",
        "doc_name": "FC-LS.pdf",
        "query": "FLOGI 的功能是什么？它的 payload 格式是怎样的？",
        "type": "format",
        "expected_sections": ["4.2.x"],
        "key_points": ["Fabric Login", "N_Port", "F_Port", "Service Parameters"]
    },
    {
        "id": "fcls-02",
        "doc_name": "FC-LS.pdf",
        "query": "PRLO 的 Payload 格式是什么？包含哪些字段？",
        "type": "format",
        "expected_sections": ["4.2.21"],
        "expected_pages": [75, 76],
        "key_points": ["Process Logout", "TYPE code", "Process_Associator", "Response codes"]
    }
]
```

**问题类型分布：**
- format（帧/报文格式）：4-5 题 — 协议文档核心
- state_machine（状态机）：2-3 题 — 毕设后续提取目标
- procedure（流程/机制）：3-4 题 — 需要理解上下文
- definition（定义）：2-3 题 — 简单事实查找
- cross_reference（跨章节）：1-2 题 — 测试导航能力

### 5.3 自动评测脚本

```python
# src/evaluate.py

async def evaluate_all(test_set_path: str, model: str):
    test_cases = load_test_set(test_set_path)
    results = []

    for case in test_cases:
        response = await agentic_rag(case["query"], case["doc_name"], model)

        result = {
            "id": case["id"],
            "query": case["query"],
            # 检索质量
            "pages_hit_rate": pages_overlap(response.pages_retrieved, case.get("expected_pages", [])),
            # 答案质量
            "key_points_covered": count_covered(response.answer, case["key_points"]),
            "key_points_total": len(case["key_points"]),
            # 引用质量
            "citation_count": len(response.citations),
            "citation_valid_rate": citation_validity(response.citations, response.pages_retrieved),
            # 效率
            "total_turns": response.total_turns,
            "pages_retrieved_count": len(response.pages_retrieved),
            # 原始数据
            "answer": response.answer,
            "trace": response.trace,
        }
        results.append(result)

    # 汇总
    summary = aggregate_metrics(results)
    save_results(results, summary)
    print_summary(summary)
```

### 5.4 关注指标

| 指标 | 目标 | 说明 |
|------|------|------|
| key_points 覆盖率 | > 80% | 答案是否包含了问题的核心要点 |
| 引用有效率 | > 90% | 引用的页码是否真的被检索过 |
| 平均 turn 数 | 4-8 | LLM 调用效率 |
| 检索页码命中率 | > 70% | 是否找到了正确的章节 |

### 5.5 失败分析与优化

典型失败模式及对策：

| 失败模式 | 根因 | 对策 |
|----------|------|------|
| LLM 选错章节 | summary 不够准确，标题不明确 | 优化 summary 生成质量（离线改进索引） |
| LLM 反复翻页找不到 | 目录树分块太细，关键信息分散在多个 part | 调整 `max_limit`，重新分块 |
| LLM 取了太多页但不回答 | system prompt 没有明确的"何时停止"指引 | 优化 prompt 中的充足性判断引导 |
| 引用页码错误 | LLM 记混了页码，或凭推测引用 | 在 prompt 中强调"只引用实际读取的页面" |
| 遗漏交叉引用信息 | LLM 没有跟踪 "see section X" | 强化交叉引用跟踪的 prompt |
| 对表格内容理解差 | 表格 markdown 格式不够清晰 | 改进 `build_content_db.py` 的表格提取质量 |

**关键认识：优化不仅是调 prompt——索引质量（summary）、内容格式（表格 markdown）、分块粒度（max_limit）同样重要。**

---

## 六、Phase 4：面向毕设的扩展预留

### 6.1 扩展路径

本系统为毕设最终目标（协议代码生成）提供基础。扩展路径：

```
当前 Agentic RAG（Phase 0-3）
       │
       │ 同样的 Tool + Agent Loop
       │ 换 system prompt + 输出解析
       ▼
协议知识提取（毕设扩展）
       │
       ▼
结构化 Schema（ProtocolSchema）
       │
       ▼
代码生成
```

### 6.2 扩展点一：提取型 System Prompt

无需改代码，只需新的 system prompt：

```markdown
你是一个协议分析助手。你的任务是从文档中系统性地提取所有状态机定义。

## 工作流程

1. 调用 get_document_structure 浏览完整的目录树
2. 识别所有可能包含状态机定义的章节
   （关注标题中含 state machine, state, FSM, transition 等关键词）
3. 逐个调用 get_page_content 读取这些章节
4. 从内容中提取每个状态机的：
   - 名称和描述
   - 所有状态（名称、是否初始/终止状态）
   - 所有事件/触发条件
   - 所有转换规则（源状态 → 目标状态、触发事件、条件、动作）
5. 以 JSON 格式输出提取结果

## 输出格式
{
    "state_machines": [
        {
            "name": "BFD Session State Machine",
            "source_pages": [16, 17],
            "states": [...],
            "transitions": [...]
        }
    ]
}
```

### 6.3 扩展点二：新增 Tool

如果基础的两个 Tool 不够用，可以按需添加：

```python
# 搜索目录树（避免逐 part 翻找）
{"name": "search_structure", "parameters": {"doc_name": str, "keyword": str}}

# 获取文档图片（帧格式图、状态机图）
{"name": "get_document_image", "parameters": {"doc_name": str, "img_path": str}}
```

Agent Loop 代码完全不变——只需在 `tools` 列表和 `execute_tool()` 路由中添加新项。

### 6.4 数据模型预留

在 `models.py` 中提前定义（不实现），建立与当前系统的追溯关系：

```python
class ProtocolState(BaseModel):
    name: str
    description: str = ""
    is_initial: bool = False
    is_final: bool = False

class ProtocolTransition(BaseModel):
    from_state: str
    to_state: str
    event: str
    condition: str = ""
    actions: list[str] = []

class ProtocolStateMachine(BaseModel):
    name: str
    states: list[ProtocolState] = []
    transitions: list[ProtocolTransition] = []
    source_pages: list[int] = []     # 追溯到 get_page_content 的页码

class ProtocolField(BaseModel):
    name: str
    type: str = ""
    size_bits: int | None = None
    description: str = ""

class ProtocolMessage(BaseModel):
    name: str
    fields: list[ProtocolField] = []
    source_pages: list[int] = []

class ProtocolSchema(BaseModel):
    """协议的结构化表示——代码生成的输入。"""
    protocol_name: str
    state_machines: list[ProtocolStateMachine] = []
    messages: list[ProtocolMessage] = []
    constants: dict = {}
    source_document: str = ""
```

每个提取元素都有 `source_pages`，直接对应 `get_page_content` 返回的页码——复用当前系统的引用追溯能力。

---

## 七、文件结构

```
src/
├── tools/
│   ├── __init__.py
│   ├── document_structure.py    # get_document_structure 实现
│   ├── page_content.py          # get_page_content 实现
│   ├── schemas.py               # Tool schema 定义（function calling）
│   └── registry.py              # doc_name → 数据路径映射
│
├── agent/
│   ├── __init__.py
│   ├── loop.py                  # Agent 循环核心
│   ├── llm_adapter.py           # OpenAI/Anthropic 统一适配
│   ├── citation.py              # 引用提取与验证
│   └── prompts/
│       ├── qa_system.txt        # 问答 system prompt
│       └── extraction_system.txt # 提取 system prompt（Phase 4 预留）
│
├── models.py                    # 所有数据结构（RAGResponse, Citation, ProtocolSchema 等）
├── evaluate.py                  # 评测脚本
└── main.py                      # CLI 入口
```

**依赖关系：**
- `tools/` 不 import `agent/`，只依赖 `registry.py` 和数据文件
- `agent/` import `tools/` 的函数和 schema
- `models.py` 被所有模块共享，不依赖其他模块
- `main.py` 组装 `agent/` 和 `tools/`

**与已有代码的关系：**
- `page_index.py`, `utils.py`, `structure_chunker.py`, `build_content_db.py` 保持不动
- `src/` 是全新模块，通过读取已有代码产出的数据文件（chunks_3/, content_*.json）与已有代码间接关联
- 不 import 已有代码中的函数（避免耦合）

---

## 八、风险与对策

### 8.1 索引质量风险

**风险：** Summary 质量不够好，导致 LLM 找不到正确章节。

**对策：**
- 在 Phase 3 的失败分析中重点检查"LLM 选错章节"的 case
- 对失败 case 对应的 summary 逐个审查，必要时重新生成
- 考虑在 summary 中加入关键术语列表（如节点包含 "FLOGI", "payload", "Service Parameters" 等关键词），帮助 LLM 匹配

### 8.2 Context Window 风险

**风险：** 大文档多轮 tool call 后 messages 列表过长，超出 context window。

**对策：**
- 监控每轮的 token 用量
- 当总 token 接近上限时，压缩历史 tool result（只保留 summary，丢弃原始 JSON）
- `get_page_content` 设置单次最大页数限制和单页字符截断

### 8.3 LLM 行为不可控风险

**风险：** LLM 可能陷入循环（反复翻同一个 part）、跳过关键步骤、或直接编造答案。

**对策：**
- `max_turns` 安全阀
- 在 trace 中监测重复 tool call，如果检测到死循环则提前终止
- System prompt 中明确禁止编造（"只基于你实际读取到的内容回答"）
- 引用验证（`validate_citations`）可以事后检测编造行为

### 8.4 表格理解风险

**风险：** 协议文档中大量使用表格定义字段和参数，markdown 表格格式可能影响 LLM 理解。

**对策：**
- 审查 `build_content_db.py` 产出的 markdown 表格质量
- 对于复杂嵌套表格，考虑额外的格式化处理
- 在评测中专门加入表格相关的测试问题
