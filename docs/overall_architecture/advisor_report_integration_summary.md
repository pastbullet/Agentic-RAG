# Agentic RAG 与毕设主线整合说明

## 1. 分支关系结论

当前仓库中：

- `main` 分支提交：`f5ab95c`
- `thesis` 分支提交：`447b012`

从 Git 历史看，`thesis` 是在 `main` 的 Agentic RAG 基础上继续演进出来的，而不是一条独立重写的路线。

关键提交链如下：

```text
f5ab95c  Agentic RAG单次QA状态，准备开始毕设
  └─ 332adab  fix: split end_index into display boundary and build boundary
      └─ a9a6b37  first pipeline
          └─ a40f98f  message IR
              └─ a118884 Update MessageIR implementation and tests
                  └─ 93e4030 msg IR暂时定型
                      └─ 447b012 thesis 最新提交
```

因此，如果导师问“Agentic RAG 和毕设有没有结合”，准确回答是：

**已经结合。当前 `thesis` 分支就是在 `main` 的 Agentic RAG 底座上，继续扩展协议抽取、MessageIR、代码生成与验证能力后的整合版本。**

## 2. 整合后的系统定位

项目已经从早期的“协议 PDF 问答系统”演进为：

**一个面向网络协议文档的 Agentic RAG + 结构化抽取 + 中间表示构建 + 代码生成验证平台。**

整体链路可以概括为：

```text
协议 PDF
-> 文档结构化索引
-> Agentic RAG 导航与按页检索
-> 节点语义分类
-> 多类型协议知识抽取
-> Merge / Normalization
-> MessageIR / OptionListIR / StateContextIR(部分)
-> C 代码生成
-> 编译与 roundtrip 验证
```

其中两部分的关系是：

- `main` 提供了可运行的 Agentic RAG 问答底座
- `thesis` 在此基础上把系统目标从“问答”推进到“协议理解与实现生成”

## 3. main 分支提供了什么

`main` 的核心能力是一个面向协议 PDF 的 Agentic RAG 问答系统，主要包括：

- PDF 文档处理与注册
- 文档树结构浏览
- 页面内容按需读取
- 基于工具调用的多轮推理问答
- Web 端交互界面
- 引文页码返回与会话保存

从架构上看，`main` 解决的是：

**如何让模型能在长协议文档中“自己找目录、自己翻页、自己组织证据后回答问题”。**

## 4. thesis 分支新增了什么

`thesis` 分支在 `main` 的基础上，新增了面向毕设主线的协议抽取与代码生成能力，主要增加：

- `src/extract/classifier.py`
  - 对文档叶节点做语义分类
- `src/extract/extractors/`
  - 分类型抽取 message / state machine / procedure / timer / error
- `src/extract/merge.py`
  - 对多来源抽取结果做合并与归一化
- `src/extract/message_ir.py`
  - 构建面向实现的统一消息中间表示
- `src/extract/option_tlv.py`
  - 处理最小 Option/TLV 结构
- `src/extract/codegen.py`
  - 根据 IR 生成 C 代码
- `src/extract/verify.py`
  - 进行语法检查与 roundtrip 验证
- `run_extract_pipeline.py`
  - 独立运行整条抽取流水线

同时，`src/models.py` 中也增加了毕设需要的关键结构：

- `MessageIR`
- `FieldIR`
- `SectionIR`
- `CompositeTailIR`
- `OptionListIR`
- `StateContextIR`
- `ProcedureRule`
- `TimerConfig`
- `ErrorRule`
- `NodeSemanticLabel`

## 5. “结合”体现在哪里

这次整合不是简单把两个目录放在一起，而是形成了明确的上下游关系：

### 5.1 Agentic RAG 仍然是底座

系统仍然保留了原先 `main` 的核心能力：

- 基于目录树的结构感知
- 基于页码的原文检索
- 基于工具调用的证据驱动问答

这部分能力既能独立用于 QA，也能作为协议抽取阶段的文档导航基础。

### 5.2 毕设把系统目标抬高了一层

在 `thesis` 中，系统不再满足于“回答文档里写了什么”，而是进一步追求：

- 识别协议中有哪些结构化对象
- 把这些对象变成可计算的 IR
- 把 IR 继续落成可验证的实现骨架

也就是说，Agentic RAG 的价值被保留下来，但角色从“最终产品”变成了“协议理解系统的入口与底层能力”。

### 5.3 Web / ingest / registry 仍服务于整条链

当前系统仍沿用 `main` 中已有的：

- ingest 流程
- 文档注册与内容库
- Web 前端交互
- 会话保存

只是 `thesis` 在这套基础设施上，继续把产物延伸到了：

- `protocol_schema.json`
- `message_ir.json`
- `generated/*.c|*.h`
- `verify_report.json`

## 6. 适合向导师强调的系统演进

建议把项目发展概括成三个阶段：

### 阶段一：文档可问答

先解决“模型能否在长协议 PDF 中稳定找信息”。

成果：

- 结构化浏览文档
- 按页读取
- Agentic RAG 问答

### 阶段二：文档可抽取

再解决“模型能否把协议规范转换成结构化知识”。

成果：

- 节点分类
- message / procedure / timer / error / state machine 抽取
- merge 与 schema 归一化

### 阶段三：知识可生成与验证

最后解决“抽取结果能否进入工程实现链路”。

成果：

- MessageIR
- OptionListIR
- codegen
- verify

这样汇报时，导师会比较容易理解这不是换题，而是同一条路线不断深化。

## 7. 当前可以展示的成果

如果导师要看“已经做出来了什么”，可以重点展示：

### 7.1 可运行的 Agentic RAG 问答

入口：

- [src/web/app.py](/Users/zwy/毕设/Kiro/src/web/app.py)
- [src/agent/loop.py](/Users/zwy/毕设/Kiro/src/agent/loop.py)

可展示点：

- 选择协议 PDF
- 提问
- 模型自己调 `get_document_structure` / `get_page_content`
- 返回答案与页码引用

### 7.2 BFD / TCP 的抽取结果

关键产物：

- [data/out/rfc5880-BFD/protocol_schema.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/protocol_schema.json)
- [data/out/rfc793-TCP/protocol_schema.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/protocol_schema.json)
- [data/out/rfc793-TCP/message_ir.json](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/message_ir.json)

可展示点：

- 已经不只是文档摘要，而是形成了结构化协议对象
- TCP Header 已经能走到 MessageIR
- BFD 已经能走到更完整的 codegen / verify 路径

### 7.3 代码生成与验证

关键产物：

- [data/out/rfc793-TCP/generated/tcp_msg_tcp_header.c](/Users/zwy/毕设/Kiro/data/out/rfc793-TCP/generated/tcp_msg_tcp_header.c)
- [data/out/rfc5880-BFD/verify_report.json](/Users/zwy/毕设/Kiro/data/out/rfc5880-BFD/verify_report.json)

可展示点：

- 从协议文档抽取结果出发，已经能生成部分 C 实现骨架
- 已经有自动验证，而不是只停留在“模型输出 JSON”

## 8. 当前还没有完成的部分

这一部分建议主动讲清楚，会显得判断更成熟。

当前仍然存在的边界包括：

- `StateContextIR` 已建模，但尚未完全进入主链
- FSM codegen 还不稳定，复杂条件分支会出现重复 case 问题
- TCP option 支持仍然是最小集合
- 行为层代码生成还没有完全闭环
- 还没有做到“规则骨架 + LLM 自动补全行为实现”的最终形态

因此，当前最准确的表述不是“已经全自动生成完整协议实现”，而是：

**已经完成从协议文档到结构化 IR，再到部分实现骨架与自动验证的主链打通。**

## 9. 对导师的一句话总结

可以直接用下面这句话：

**我的毕设不是脱离原先 Agentic RAG 另起炉灶，而是在 Agentic RAG 的文档导航与证据检索底座上，继续把系统推进到了协议结构抽取、MessageIR 建模、代码生成与验证这一层。**

如果要更技术一点，也可以说：

**`main` 解决“怎么从长协议 PDF 中可靠找信息”，`thesis` 解决“怎么把这些信息变成可生成、可验证的协议实现中间表示”，两者现在已经是同一条系统链路。**

## 10. 当前建议的汇报口径

建议你向导师汇报时按下面顺序讲：

1. 先讲 `main` 的 Agentic RAG 底座已经完成
2. 再讲 `thesis` 不是推翻重来，而是在这个底座上扩展
3. 强调当前主成果是 `INDEX -> CLASSIFY -> EXTRACT -> MERGE -> CODEGEN -> VERIFY`
4. 展示 BFD / TCP 两个样例产物
5. 最后说明当前边界与下一阶段计划

这样导师通常更容易接受你的工作是“连续演进、技术路线收敛”，而不是“前后两套系统割裂”。
