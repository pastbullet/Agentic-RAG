# Agentic RAG 项目面试问答清单（20 题）

> 适用场景：你把本仓库作为主项目介绍。  
> 使用方式：每题先讲「一句话结论」，再讲「实现细节」，最后讲「权衡/改进」。

---

## 0. 30 秒项目介绍（开场）

我是做了一个 **端到端 Agentic RAG 系统**，面向协议 PDF 问答。  
它不是固定检索链路，而是让 LLM 在循环里自主调用工具：先看文档结构分片，再按页取内容，最后基于真实证据回答并输出页码引用。  
工程上我把旧的索引脚本整合成可运行流水线（处理/注册/问答一体化），并加了 Web 调试台和流式进度，能实时看到每轮工具调用和最终答案。

---

## 1) 这个项目解决什么问题？

**答：** 解决长协议文档问答中“跨章节信息分散、检索不稳定、答案不可追溯”的问题。  

- 普通 RAG 容易一次性召回不足，或者引用不可靠。  
- 这个系统通过工具循环逐步导航：`get_document_structure` + `get_page_content`。  
- 输出强制带 `<cite doc="..." page="..."/>`，并校验引用页是否真的检索过。

---

## 2) 为什么是 Agentic RAG，而不是固定 Retriever + Reader？

**答：** 协议文档存在大量交叉引用，固定检索链路很容易漏上下文。  

- Agent 模式可以“看目录 -> 读页面 -> 发现缺口 -> 继续检索”。  
- 更接近人工阅读策略，尤其适合标准文档问答。  
- 我保留了架构约束：没引入向量库，没做多 agent，保持轻量。

---

## 3) 系统的端到端链路是什么？

**答：** `PDF -> 索引构建 -> 结构分片 -> 内容库 -> 注册 -> QA`。  

- 索引构建：`page_index.py`  
- 结构分片：`structure_chunker.py`  
- 内容库：`build_content_db.py`  
- 编排入口：`src/ingest/pipeline.py` 的 `process_document` / `ensure_document_ready`  
- QA 主循环：`src/agent/loop.py` 的 `agentic_rag`

---

## 4) 你做的最关键工程改造是什么？

**答：** 做了“最小侵入整合层”，把离散脚本整成统一可运行系统。  

- 新增 ingest 编排层，自动判断文档是否已处理。  
- 新增运行时注册表，支持动态文档。  
- CLI 支持三种模式：仅处理、已处理问答、新文档自动处理后问答。  
- Web 支持同步与流式 API，方便调试。

---

## 5) QA 主循环怎么工作的？

**答：** 是一个 max-turn 受控的工具调用循环。  

1. 加载 system prompt + tool schemas。  
2. 调用 LLM，若返回 tool call 就执行工具并回填结果。  
3. 若返回文本则结束，提取/校验引用并落会话日志。  
4. 超过 `max_turns` 返回保护性结果。

---

## 6) 为什么能保证答案可追溯？

**答：** 两层保障：流程约束 + 结果校验。  

- Prompt 要求“只基于已检索页面回答”。  
- 代码里从答案提取 `<cite/>` 并校验是否在 `pages_retrieved` 中。  
- 所有 tool 调用 trace 和最终答案都落盘到 `logs/sessions/*.json`。

---

## 7) `get_document_structure` 与 `get_page_content` 的边界是什么？

**答：**

- `get_document_structure(doc_name, part)`：只看结构分片和页范围，不返回正文。  
- `get_page_content(doc_name, pages)`：按页取正文/表格/图片，单次最多 10 页。  
- 这两个工具解耦，避免一次性把大文本塞进上下文。

---

## 8) 为什么限制 `get_page_content` 一次最多 10 页？

**答：** 控制上下文膨胀和响应时延。  

- 页面太多会导致 token 爆炸，影响稳定性。  
- 10 页是性能与信息密度的折中。  
- 超限会返回错误，促使 agent 分批读取。

---

## 9) 你做了哪些性能优化？

**答：**

- 索引侧复用了 `page_index` 里已有并行 `asyncio.gather` 能力。  
- QA 侧对工具结果做截断（尤其 page content），避免上下文过大。  
- ingest 有“已处理检查”，跳过重复构建。  
- Web 流式接口用 SSE 反馈进度，降低“卡死感”。

---

## 10) 你们是怎么做文档注册与路径解析的？

**答：** 注册表是静态+运行时合并，运行时优先。  

- 静态内置在 `DOC_REGISTRY`。  
- 动态写入 `data/out/doc_registry.runtime.json`。  
- 路径解析顺序：显式路径 -> `data/raw` -> 递归查找（多命中报错，避免误用）。

---

## 11) 为什么 Web 层用了 SSE，而不是 WebSocket？

**答：** 调试场景下 SSE 更简单且足够。  

- 后端只需 `StreamingResponse(text/event-stream)`。  
- 前端用 `fetch` 读取流即可，支持 POST body。  
- 不引入任务队列、连接管理，复杂度更低。

---

## 12) 你遇到过哪些线上/调试问题，怎么修的？

**答：** 典型有三类：

1. PDF 预览被浏览器下载劫持：改 `Content-Disposition: inline`，前端改 blob + PDF.js。  
2. 引用页跳转偏移：修正滚动定位算法，用真实几何位置计算。  
3. SSE 事件解析失败：修复封包换行格式，统一前后端解析逻辑。

---

## 13) 为什么不直接做向量检索？

**答：** 这是需求约束：保留现有索引与工具化架构，不引入新检索体系。  

- 项目目标是“整合现有脚本为可运行系统”，不是重做架构。  
- 对协议文档，结构树 + 页码检索本身具备很强可解释性。  
- 后续可扩展混合检索，但 V1 不越界。

---

## 14) 你如何做多模型支持？

**答：** 做了统一 `LLMAdapter` 抹平 OpenAI/Anthropic 差异。  

- 输入统一：`messages + tools`。  
- 输出统一：`LLMResponse(has_tool_calls, tool_calls, text, usage)`。  
- tool result message 也做了 provider 适配。

---

## 15) 你如何保证改造是“最小侵入”？

**答：** 不改旧索引脚本逻辑，只做外层编排调用。  

- `page_index.py` / `structure_chunker.py` / `build_content_db.py` 仍是事实源。  
- 新代码主要放在 `src/ingest`、`src/web`、`src/tools/registry`。  
- 主循环保持 tool-based，没改成固定 pipeline。

---

## 16) 测试策略是什么？

**答：** 分层测试 + 属性测试。  

- unit：工具、adapter、registry、citation、models。  
- integration：ingest 编排、CLI、Web API（含流式事件顺序）。  
- property-based：页码格式、schema 转换、注册表约束等。  
- 当前仓库测试可稳定通过（80+）。

---

## 17) 如果让你继续做上下文管理系统，怎么推进？

**答：** 走 Sidecar 路线，不破坏现有 loop。  

- 新增 `data/sessions/<session_id>/` 存 turn/node/evidence/topic 状态。  
- 在 `agentic_rag` 的 `turn_start/tool_call/final_answer` 挂钩更新。  
- 与 `logs/sessions` 双轨并存，保证现有 Web 不受影响。  
- 先做 JSON 原子写，再做 store，再接 manager。

---

## 18) 你这个项目最有含金量的点是什么？

**答：** 把“论文式概念”做成了“可运行工程系统”。  

- 不是 demo，而是完整 ingest + QA + Web + 测试闭环。  
- 工具调用全链路可观测，可复盘。  
- 能处理新文档并自动注册后立即问答。

---

## 19) 你会如何回答“你负责的部分”？

**答（可直接背）：**  
我主要负责系统整合和工程化落地：包括 ingest 编排层、注册表动态化、QA 主循环增强（进度事件与上下文控制）、Web 流式调试接口，以及关键回归测试。核心价值是把原本分散脚本整成稳定可运行的端到端产品形态。

---

## 20) 你认为当前版本的局限与下一步计划？

**答：**

- 局限：目前仍是单轮问答主流程，长期上下文复用不够；无并发文件锁；无混合检索。  
- 下一步：上线 context sidecar（turn/node/evidence/topic）、增强 sufficiency 判定、再评估引入轻量 rerank。  
- 原则：继续保持最小侵入，优先可解释性和稳定性。

---

## 附录 A：面试演示建议（5 分钟）

1. 先跑处理：
   - `python -m src.main --process data/raw/FC-LS.pdf`
2. 再跑问答：
   - `python -m src.main --doc FC-LS.pdf --query "FLOGI 的帧结构是什么？" --verbose`
3. 打开 Web：
   - `uvicorn src.web.app:app --host 127.0.0.1 --port 8000 --reload`
4. 演示点：
   - 实时进度（turn/tool/final）  
   - 引用点击跳页  
   - 会话可回放

---

## 附录 B：常见追问的一句话答案

- 为什么你们答案可信？  
  因为强制“先取页再回答”，并校验引用页码是否真的被检索。

- 为什么不用数据库？  
  当前阶段以调试可视化和快速迭代为主，JSON + 文件系统成本最低。

- 你们怎么控制 token？  
  通过 page 请求上限、工具结果截断、分轮检索来控制上下文大小。

- 并行在哪里？  
  索引阶段有并行任务；QA 阶段主要是多轮串行决策，保证正确性优先。
