# Docs Index

当前 `docs` 目录按主题拆分如下：

## 1. 结构层 / Frame IR

目录：`docs/frame_ir/`

主要存放报文结构、`MessageIR`、archetype-guided lowering 等文档。这里的 `frame_ir` 对应当前项目里的**结构层实现语义**，虽然文件名里多数仍沿用 `MessageIR` 命名。

## 2. 状态机 / FSM IR

目录：`docs/fsm_ir/`

主要存放 `FSM` 消费层优化、状态机 codegen、行为桥接相关方案。

## 3. 状态上下文 / StateContextIR

目录：`docs/statecontext_ir/`

主要存放连接上下文、timer/resource/context slot 等状态上下文设计文档。

## 4. 总体架构设计

目录：`docs/overall_architecture/`

主要存放主线执行方案、架构评估、分支整合、阶段路线等整体设计文档。

## 5. 验证与实验

目录：`docs/validation_and_experiments/`

主要存放测试计划、协议选型、验证路线、实验设计相关文档。

## 6. 论文写作

目录：`docs/thesis_writing/`

主要存放论文提纲、写作框架等文档。

## 7. RAG / PageIndex 分析

目录：`docs/rag_analysis/`

主要存放 Agentic RAG、PageIndex、检索与上下文组织方案分析文档。

## 8. 当前建议优先阅读顺序

如果你后续要快速回到主线，建议按这个顺序看：

1. `docs/overall_architecture/thesis_refactor_mainline_execution_plan_v5.md`
2. `docs/frame_ir/current_message_ir_summary_zh.md`
3. `docs/fsm_ir/fsm_ir_optimization_plan.md`
4. `docs/statecontext_ir/state_context_ir_v1_plan.md`
5. `docs/validation_and_experiments/test_plan.md`

