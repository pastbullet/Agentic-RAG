# 面试视角代码阅读 Checklist

这份清单的目标不是“把仓库所有代码都读完”，而是帮你在有限时间内，读到足够支持面试表达的程度。

核心原则：

- 面试准备优先级是：`能讲清 > 全都看过`
- 先建立主链路认知，再补关键设计，再补性能与局限
- 读代码时始终围绕“我怎么向面试官解释”来记笔记

---

## 一、你在面试里必须能讲清的 6 件事

在开始读代码前，先记住目标。你最终至少要能稳定回答这 6 个问题：

1. 这个项目解决什么问题？
2. 系统的端到端链路是什么？
3. Agent 是怎么工作的？
4. 为什么用 tool-based 而不是固定检索链路？
5. 你做了哪些工程化整合？
6. 当前系统的局限和下一步优化方向是什么？

如果你读完代码后，这 6 个问题还讲不顺，就说明阅读顺序需要收缩，不要继续加细节。

---

## 二、2 小时版：够你讲清项目主线

适合场景：

- 快速准备一轮面试
- 明天就要讲项目
- 目标是先把“能说清”建立起来

### Step 1: 先读说明，不急着看实现

阅读：

- [`INTERVIEW_QA_AGENTIC_RAG.md`](/Users/zwy/毕设/Kiro/INTERVIEW_QA_AGENTIC_RAG.md)
- [`README.md`](/Users/zwy/毕设/Kiro/README.md)

读完后，你要能脱口而出：

- 这是一个什么系统
- 面向什么文档
- 和普通 RAG 有什么区别

### Step 2: 只看主链路 4 个文件

阅读顺序：

- [`src/main.py`](/Users/zwy/毕设/Kiro/src/main.py)
- [`src/ingest/pipeline.py`](/Users/zwy/毕设/Kiro/src/ingest/pipeline.py)
- [`src/agent/loop.py`](/Users/zwy/毕设/Kiro/src/agent/loop.py)
- [`src/web/app.py`](/Users/zwy/毕设/Kiro/src/web/app.py)

读的时候只抓这些问题：

- 请求从哪里进来？
- 文档如何被处理成可问答资产？
- Agent 怎么调工具？
- Web 怎么把处理过程暴露出来？

### Step 3: 只补两个工具

阅读：

- [`src/tools/document_structure.py`](/Users/zwy/毕设/Kiro/src/tools/document_structure.py)
- [`src/tools/page_content.py`](/Users/zwy/毕设/Kiro/src/tools/page_content.py)

你要能说：

- 为什么工具拆成两个
- 为什么先看结构再按页取内容
- 为什么它适合协议文档

### Step 4: 看引用和模型输出结构

阅读：

- [`src/agent/citation.py`](/Users/zwy/毕设/Kiro/src/agent/citation.py)
- [`src/models.py`](/Users/zwy/毕设/Kiro/src/models.py)

你要能说：

- 回答如何做页码引用
- 为什么这个系统是“可追溯”的

### 2 小时版完成标准

如果下面这些你都能讲出来，就算达标：

- [ ] 我能用 30 秒介绍这个项目
- [ ] 我能画出 `PDF -> 处理 -> 注册 -> QA -> 引用输出` 的链路
- [ ] 我能解释 Agent 为什么只靠两个工具就能导航
- [ ] 我能说明答案为什么可追溯
- [ ] 我能讲清 Web 调试台的价值

---

## 三、半天版：够你应对大多数深问

适合场景：

- 面试官会继续问设计取舍
- 你要讲“为什么这样设计”
- 你希望能答性能、扩展性、可维护性问题

在 2 小时版基础上，继续看下面这些文件。

### Step 5: 看 tool schema 与注册机制

阅读：

- [`src/tools/schemas.py`](/Users/zwy/毕设/Kiro/src/tools/schemas.py)
- [`src/tools/registry.py`](/Users/zwy/毕设/Kiro/src/tools/registry.py)

你要能回答：

- LLM 是如何知道可以调哪些工具的？
- 文档名如何映射到 chunks/content 目录？
- 为什么要有运行时注册表？

### Step 6: 看 LLM 适配层

阅读：

- [`src/agent/llm_adapter.py`](/Users/zwy/毕设/Kiro/src/agent/llm_adapter.py)

这一块不用讲太细，但你要能说：

- 为什么要统一 OpenAI / Anthropic 接口
- tool call / tool result 的 provider 差异怎么处理

### Step 7: 看 prompt

阅读：

- [`src/agent/prompts/qa_system.txt`](/Users/zwy/毕设/Kiro/src/agent/prompts/qa_system.txt)

你要能回答：

- Prompt 如何约束“先看结构，再取页面”
- Prompt 如何约束“只基于已取证据回答”
- Prompt 如何约束引用格式

### Step 8: 看旧脚本的“角色”，不是细啃全部实现

阅读：

- [`structure_chunker.py`](/Users/zwy/毕设/Kiro/structure_chunker.py)
- [`build_content_db.py`](/Users/zwy/毕设/Kiro/build_content_db.py)
- [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py)
1
注意：这一步的目标不是把 [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py) 全部读透，而是理解三件事：

- 旧脚本分别负责什么
- 新工程如何“最小侵入复用”
- 哪一部分最耗时、最复杂

### 半天版完成标准

- [ ] 我能解释为什么做成 tool-based agent，而不是固定 Retriever + Reader
- [ ] 我能解释 registry / schema / adapter 各自的职责
- [ ] 我能说明旧脚本和新 `src/` 编排层的关系
- [ ] 我能解释为什么新文档处理慢
- [ ] 我能说出当前系统 2 到 3 个明显局限

---

## 四、1 天版：够你把“我的贡献”和“未来方向”讲得更硬

适合场景：

- 你想把这个项目作为主项目深讲
- 面试官可能追问实现细节
- 你需要更强的 ownership 感

### Step 9: 看 Context Sidecar

阅读：

- [`src/context/manager.py`](/Users/zwy/毕设/Kiro/src/context/manager.py)
- [`src/context/updater.py`](/Users/zwy/毕设/Kiro/src/context/updater.py)
- [`src/context/stores/session_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/session_store.py)
- [`src/context/stores/turn_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/turn_store.py)
- [`src/context/stores/document_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/document_store.py)
- [`src/context/stores/evidence_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/evidence_store.py)
- [`src/context/stores/topic_store.py`](/Users/zwy/毕设/Kiro/src/context/stores/topic_store.py)

这部分适合回答：

- 多轮会话如何落地
- 为什么现在要做 Sidecar，而不是直接重写 Agent
- 后续如何支持主题跟踪、证据复用

### Step 10: 用测试反向验证理解

阅读顺序：

- [`tests/test_main_cli.py`](/Users/zwy/毕设/Kiro/tests/test_main_cli.py)
- [`tests/test_ingest_pipeline.py`](/Users/zwy/毕设/Kiro/tests/test_ingest_pipeline.py)
- [`tests/test_agent_loop.py`](/Users/zwy/毕设/Kiro/tests/test_agent_loop.py)
- [`tests/test_web_api.py`](/Users/zwy/毕设/Kiro/tests/test_web_api.py)

读测试时不要只看断言，要问自己：

- 作者默认认为什么行为是“必须成立”的？
- 哪些能力是显式保护的？
- 哪些是这个系统最看重的回归面？

### 1 天版完成标准

- [ ] 我能说出自己最有含金量的工程改造点
- [ ] 我能解释为什么这是“最小侵入整合”而不是重做
- [ ] 我能说明 current bottleneck 和优化方向
- [ ] 我能回答“如果继续做，你下一步做什么”
- [ ] 我能讲清测试策略和覆盖面

---

## 五、面试时做笔记，建议固定记这 5 类

读代码时建议自己建一个简单笔记文档，每个模块都按这 5 个维度记录：

1. 这个模块的职责是什么？
2. 它的输入和输出是什么？
3. 它依赖哪个上游模块？服务哪个下游模块？
4. 这里的设计取舍是什么？
5. 这里最可能被面试官追问什么？

这样你最后复习的时候，不会掉进“只记函数名，不记设计意义”的坑。

---

## 六、面试表达时，最值得先准备的 8 个回答

你读完代码以后，建议先把这 8 题写成自己的话，每题控制在 1 分钟内：

1. 请你介绍一下这个项目。
2. 为什么这是 Agentic RAG，而不是普通 RAG？
3. 文档处理链路是什么？
4. Agent Loop 是怎么工作的？
5. 为什么答案可以追溯？
6. Web 调试台解决了什么问题？
7. 你做的最关键工程改造是什么？
8. 当前版本的局限和下一步计划是什么？

如果这 8 题能说顺，说明你已经具备“面试可用”的理解深度了。

### 8 题参考答案

下面不是标准答案，而是基于当前仓库状态整理出的“可直接复述的一版”。你可以先背骨架，再按自己的真实贡献微调措辞。

#### 1. 请你介绍一下这个项目。

参考答案：

这是一个面向协议 PDF 的 Agentic RAG 系统，目标是解决长文档问答里检索不稳定、跨章节信息难串联、答案不可追溯的问题。系统先把 PDF 处理成结构分块和分页内容库，再让 LLM 通过工具调用自主导航文档，最后输出带页码引用的答案。除了 CLI，我还做了 Web 调试界面，可以看到流式进度、工具调用记录和会话日志，方便排查检索路径和答案来源。

#### 2. 为什么这是 Agentic RAG，而不是普通 RAG？

参考答案：

普通 RAG 更像“先检索一批内容，再让模型读”，路径通常比较固定；这个项目是让模型在循环里自己决定下一步查哪里。它先调用 `get_document_structure` 看目录和摘要，再调用 `get_page_content` 取具体页面，如果信息不够还可以继续回到结构层做补检索。这样更适合协议文档这种强结构、强交叉引用的材料，因为很多定义、流程和状态机不会集中出现在一个段落里。

#### 3. 文档处理链路是什么？

参考答案：

端到端链路是 `PDF -> page_index -> structure chunks -> content DB -> register -> QA`。其中 `page_index.py` 负责从原始 PDF 里抽章节树，`structure_chunker.py` 把结构树按大小切成多个 `part_xxx.json`，`build_content_db.py` 按页提取正文、表格和图片，最后由 `src/ingest/pipeline.py` 统一编排并注册到运行时 registry。这样问答阶段就不需要再次解析原始 PDF，而是直接走结构树和分页内容库。

#### 4. Agent Loop 是怎么工作的？

参考答案：

主循环在 [`src/agent/loop.py`](/Users/zwy/毕设/Kiro/src/agent/loop.py)。它会先加载 system prompt 和 tool schema，把用户问题和文档名封装成消息发给 LLM；如果 LLM 返回 tool call，就执行对应工具，把结果回填给模型继续推理；如果返回纯文本，就把它当作最终答案。这个循环里还会记录 `trace`、收集 `pages_retrieved`、校验引用，并把完整消息和结果落盘到 `logs/sessions`，保证后续可以复盘。

#### 5. 为什么答案可以追溯？

参考答案：

这个项目有两层追溯机制。第一层是 prompt 约束，要求模型只能基于实际调用 `get_page_content` 读到的页面回答，并且用 `<cite doc="..." page="N"/>` 这种格式做引用。第二层是代码校验，`citation.py` 会从最终答案里提取引用页码，再和实际检索过的 `pages_retrieved` 做比对，如果模型引用了没读过的页面，就会被识别出来。这让答案不是“看起来像对”，而是能回到具体页码核查。

#### 6. Web 调试台解决了什么问题？

参考答案：

Web 调试台主要解决“Agent 看起来像黑盒”的问题。以前只看最终答案，很难知道模型中间到底查了哪些章节、读了哪些页、卡在了哪一步；现在 Web 层通过 SSE 把 `turn_start`、tool 调用、tool 参数、tool 结果和最终答案实时推到前端，还能联动 PDF 预览和历史会话日志。这样无论是开发阶段调 prompt，还是演示阶段讲检索过程，都更直观。

#### 7. 你做的最关键工程改造是什么？

参考答案：

我做的最关键改造是把原来分散的旧脚本整合成一个最小侵入、可运行的端到端系统。旧的 `page_index.py`、`structure_chunker.py`、`build_content_db.py` 仍然保留为事实来源，但我在 `src/ingest/pipeline.py` 上面加了一层统一编排，把“处理、注册、问答”串起来；同时补了运行时 registry、统一的 Agent Loop、Web 调试台和测试，让它从“几个能单独跑的脚本”变成“一个可演示、可复盘、可扩展的产品雏形”。

#### 8. 当前版本的局限和下一步计划是什么？

参考答案：

当前版本最大的局限有三个。第一，文档预处理还是比较重，尤其 `page_index.py` 会触发大量规则和 LLM 调用，所以新文档上传比较慢；第二，主流程虽然支持上下文 sidecar，但多轮会话复用还不够强；第三，检索仍然主要依赖结构树和按页内容，没有加入更强的 rerank 或混合检索。下一步我会优先做两件事：一是进一步拆分快速模式和高质量模式，降低文档处理延迟；二是继续把 context sidecar 用起来，让多轮会话能复用历史证据和主题状态。

### 8 题背诵完成标准

- [ ] 我能不看文档讲完这 8 题
- [ ] 我知道每个答案分别由哪些代码文件支撑
- [ ] 我能把“我的贡献”替换进第 7 题，而不是照本宣科

---

## 七、最容易踩的阅读误区

- 一上来就看 [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py)，结果陷进规则细节，反而讲不清主线
- 只看 Web UI，不看 [`src/agent/loop.py`](/Users/zwy/毕设/Kiro/src/agent/loop.py)，最后不知道 Agent 核心机制
- 只背目录结构，不理解模块之间的调用关系
- 想把每个函数都看懂，导致投入很大但面试产出很低

面试准备的正确目标是：

“我知道系统怎么流动、为什么这么设计、我的价值在哪、现在哪里还不够。”

---

## 八、最推荐的实际执行顺序

如果你现在就开始准备，我建议按这个顺序走：

1. 读 [`INTERVIEW_QA_AGENTIC_RAG.md`](/Users/zwy/毕设/Kiro/INTERVIEW_QA_AGENTIC_RAG.md)
2. 读 [`README.md`](/Users/zwy/毕设/Kiro/README.md)
3. 读 [`src/main.py`](/Users/zwy/毕设/Kiro/src/main.py)
4. 读 [`src/ingest/pipeline.py`](/Users/zwy/毕设/Kiro/src/ingest/pipeline.py)
5. 读 [`src/agent/loop.py`](/Users/zwy/毕设/Kiro/src/agent/loop.py)
6. 读 [`src/tools/document_structure.py`](/Users/zwy/毕设/Kiro/src/tools/document_structure.py)
7. 读 [`src/tools/page_content.py`](/Users/zwy/毕设/Kiro/src/tools/page_content.py)
8. 读 [`src/web/app.py`](/Users/zwy/毕设/Kiro/src/web/app.py)
9. 读 [`src/agent/llm_adapter.py`](/Users/zwy/毕设/Kiro/src/agent/llm_adapter.py)
10. 最后再看 [`page_index.py`](/Users/zwy/毕设/Kiro/page_index.py)

这条顺序最贴近“先能讲清项目，再补细节”的目标。
