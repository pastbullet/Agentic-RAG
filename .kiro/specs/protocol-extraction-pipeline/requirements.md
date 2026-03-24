# 需求文档：协议提取 Pipeline（Protocol Extraction Pipeline）

## 简介

本特性为现有 Agentic RAG 系统新增一条完整的协议提取流水线。该流水线读取已解析为文档结构树（page_index.json）的通信协议文档（RFC、FC 标准），对叶节点进行语义分类，路由到专用提取器生成结构化模型（ProtocolStateMachine、ProtocolMessage 等），最终合并为 ProtocolSchema 并驱动代码生成与验证。新模块位于 `src/extract/`，与现有 `src/agent/`、`src/ingest/` 平行。

## 术语表

- **Extraction_Pipeline**：协议提取流水线的主编排模块，负责串联分类、提取、合并、代码生成、验证五个阶段
- **Classifier**：节点语义分类器，将文档树叶节点标注为六类协议实现语义标签之一
- **Extractor**：专用结构化提取器的统称，按标签类型分为 StateMachineExtractor、MessageExtractor、ProcedureExtractor、TimerExtractor、ErrorExtractor
- **NodeSemanticLabel**：单个叶节点的分类结果，包含 label、confidence、rationale、secondary_hints
- **NodeLabelMeta**：分类运行的元信息记录，包含 source_document、model_name、prompt_version、label_priority、created_at
- **ProtocolSchema**：协议的完整结构化表示，包含 state_machines、messages、constants，是代码生成的输入
- **ProtocolStateMachine**：从文档中提取的状态机模型，包含 states 和 transitions
- **ProtocolMessage**：从文档中提取的报文/帧结构模型，包含 fields 列表
- **Content_DB**：已有的按页内容数据库（`output/docs/*/json/content_*.json`），存储每页的文本、表格、图片
- **Page_Index**：已有的文档结构树（`data/out/*_page_index.json`），包含层级节点结构
- **Override_File**：人工修正覆盖文件（`data/out/{doc_stem}_node_labels.override.json`），用于手动纠正分类结果
- **Codegen**：代码生成模块，从 ProtocolSchema 生成可执行的协议实现代码
- **Verifier**：验证模块，检查生成代码与协议规范的一致性
- **Label_Priority**：标签优先级列表，用于多标签冲突时的消解，可按文档配置

## 需求

### 需求 1：节点语义分类

**用户故事：** 作为协议工程师，我希望系统能自动将文档结构树的叶节点按协议实现语义进行分类，以便后续针对性地提取结构化信息。

#### 验收标准

1. WHEN Extraction_Pipeline 接收到一个文档的 Page_Index 时，THE Classifier SHALL 遍历所有叶节点（is_skeleton 为 false 的节点）并为每个叶节点生成一个 NodeSemanticLabel
2. THE Classifier SHALL 将每个叶节点分类为以下六类标签之一：state_machine、message_format、procedure_rule、timer_rule、error_handling、general_description
3. WHEN 一个叶节点同时符合多个标签时，THE Classifier SHALL 按 Label_Priority 列表选择优先级最高的标签作为主标签，并将其余相关标签记入 secondary_hints 字段
4. WHEN 一段文本能够套入"在状态 X 下，收到事件 Y，执行动作 Z，转移到状态 W"模板时，THE Classifier SHALL 将该节点标记为 state_machine 而非 procedure_rule
5. THE Classifier SHALL 为每个分类结果生成 confidence（0.0 到 1.0）和 rationale（一句话分类理由）
6. THE Classifier SHALL 通过 LLM 调用（使用现有 llm_adapter）完成分类，分类 prompt 包含类别定义、优先级规则和排他规则

### 需求 2：分类结果持久化与缓存

**用户故事：** 作为协议工程师，我希望分类结果能被持久化保存并支持缓存复用，以避免重复调用 LLM 产生不必要的开销。

#### 验收标准

1. WHEN 分类完成后，THE Classifier SHALL 将所有 NodeSemanticLabel 持久化到 `data/out/{doc_stem}_node_labels.json` 文件
2. WHEN 分类完成后，THE Classifier SHALL 将 NodeLabelMeta（包含 source_document、model_name、prompt_version、label_priority、created_at）持久化到 `data/out/{doc_stem}_node_labels.meta.json` 文件
3. WHEN 启动分类且缓存文件已存在时，THE Classifier SHALL 比较当前 model_name、prompt_version、label_priority 与缓存的 NodeLabelMeta；若三者均一致，THE Classifier SHALL 直接加载缓存结果而跳过 LLM 调用
4. WHEN 缓存的 NodeLabelMeta 中任一字段（model_name、prompt_version、label_priority）与当前配置不一致时，THE Classifier SHALL 丢弃缓存并重新执行全量分类

### 需求 3：人工修正覆盖

**用户故事：** 作为协议工程师，我希望能手动修正个别节点的分类结果，以纠正 LLM 分类错误。

#### 验收标准

1. WHEN `data/out/{doc_stem}_node_labels.override.json` 文件存在时，THE Extraction_Pipeline SHALL 在加载分类结果后将 Override_File 中的条目合并覆盖到对应 node_id 的分类结果上
2. THE Override_File SHALL 采用 JSON 格式，以 node_id 为键，值包含 label 和 rationale 字段
3. WHEN Override_File 中的某个 node_id 在分类结果中不存在时，THE Extraction_Pipeline SHALL 忽略该条目并记录一条警告日志

### 需求 4：标签优先级配置

**用户故事：** 作为协议工程师，我希望能按文档类型配置标签优先级，以适应不同协议的特点（如 BFD 侧重状态机，FC-LS 侧重帧格式）。

#### 验收标准

1. THE Extraction_Pipeline SHALL 提供默认标签优先级列表：state_machine > message_format > timer_rule > error_handling > procedure_rule > general_description
2. WHERE 用户为特定文档配置了自定义优先级列表，THE Extraction_Pipeline SHALL 使用该自定义列表替代默认列表
3. THE Extraction_Pipeline SHALL 将当前使用的 Label_Priority 记录到 NodeLabelMeta 中，用于缓存有效性判断

### 需求 5：节点内容获取

**用户故事：** 作为系统开发者，我希望提取流水线能从现有 Content_DB 中获取叶节点的原文内容，以作为分类和提取的输入。

#### 验收标准

1. WHEN 处理一个叶节点时，THE Extraction_Pipeline SHALL 根据该节点的 start_index、end_index、start_line、end_line 从 Content_DB 中切取对应的文本内容
2. WHEN 叶节点自身包含 text 字段（小文档场景）时，THE Extraction_Pipeline SHALL 直接使用该 text 字段内容而跳过 Content_DB 查询
3. IF Content_DB 中对应页面的内容文件不存在，THEN THE Extraction_Pipeline SHALL 记录错误日志并跳过该节点，继续处理后续节点

### 需求 6：状态机提取

**用户故事：** 作为协议工程师，我希望系统能从标记为 state_machine 的节点中提取出完整的状态机模型，以便后续生成状态机实现代码。

#### 验收标准

1. WHEN 一个叶节点被分类为 state_machine 时，THE StateMachineExtractor SHALL 从该节点的文本内容中提取 ProtocolState 列表（包含 name、description、is_initial、is_final）
2. WHEN 一个叶节点被分类为 state_machine 时，THE StateMachineExtractor SHALL 从该节点的文本内容中提取 ProtocolTransition 列表（包含 from_state、to_state、event、condition、actions）
3. THE StateMachineExtractor SHALL 将提取结果组装为 ProtocolStateMachine 对象，包含 name、states、transitions、source_pages
4. IF 节点文本中未能识别出有效的状态或转移信息，THEN THE StateMachineExtractor SHALL 返回空的 ProtocolStateMachine 并记录警告日志

### 需求 7：报文格式提取

**用户故事：** 作为协议工程师，我希望系统能从标记为 message_format 的节点中提取出完整的报文/帧结构模型，以便后续生成解析器代码。

#### 验收标准

1. WHEN 一个叶节点被分类为 message_format 时，THE MessageExtractor SHALL 从该节点的文本内容中提取 ProtocolField 列表（包含 name、type、size_bits、description）
2. THE MessageExtractor SHALL 将提取结果组装为 ProtocolMessage 对象，包含 name、fields、source_pages
3. WHEN 节点文本中包含表格形式的字段定义时，THE MessageExtractor SHALL 优先从表格中提取字段信息
4. IF 节点文本中未能识别出有效的字段定义，THEN THE MessageExtractor SHALL 返回空的 ProtocolMessage 并记录警告日志

### 需求 8：辅助提取器（procedure_rule、timer_rule、error_handling）

**用户故事：** 作为协议工程师，我希望系统能从 procedure_rule、timer_rule、error_handling 类型的节点中提取结构化规则，以补充协议的完整语义。

#### 验收标准

1. WHEN 一个叶节点被分类为 procedure_rule 时，THE ProcedureExtractor SHALL 从该节点文本中提取处理步骤列表，每个步骤包含条件和动作描述
2. WHEN 一个叶节点被分类为 timer_rule 时，THE TimerExtractor SHALL 从该节点文本中提取定时器配置，包含定时器名称、超时值、触发动作
3. WHEN 一个叶节点被分类为 error_handling 时，THE ErrorExtractor SHALL 从该节点文本中提取错误处理规则，包含错误条件和处理动作
4. WHEN 一个叶节点被分类为 general_description 时，THE Extraction_Pipeline SHALL 跳过该节点的结构化提取

### 需求 9：ProtocolSchema 合并

**用户故事：** 作为协议工程师，我希望系统能将所有节点的提取结果合并为一个完整的 ProtocolSchema，以作为代码生成的统一输入。

#### 验收标准

1. WHEN 所有叶节点的提取完成后，THE Extraction_Pipeline SHALL 将所有 ProtocolStateMachine 对象合并到 ProtocolSchema 的 state_machines 列表中
2. WHEN 所有叶节点的提取完成后，THE Extraction_Pipeline SHALL 将所有 ProtocolMessage 对象合并到 ProtocolSchema 的 messages 列表中
3. THE Extraction_Pipeline SHALL 将合并后的 ProtocolSchema 持久化到 `data/out/{doc_stem}_protocol_schema.json` 文件
4. THE ProtocolSchema SHALL 包含 protocol_name（从文档名推导）和 source_document 字段


### 需求 10：ProtocolSchema 序列化与反序列化（Round-Trip）

**用户故事：** 作为系统开发者，我希望 ProtocolSchema 的 JSON 序列化和反序列化是无损的，以确保持久化后的数据能被完整还原。

#### 验收标准

1. THE Extraction_Pipeline SHALL 使用 JSON 格式序列化 ProtocolSchema 对象
2. FOR ALL 有效的 ProtocolSchema 对象，序列化为 JSON 后再反序列化 SHALL 产生与原始对象等价的 ProtocolSchema（round-trip 性质）
3. THE Extraction_Pipeline SHALL 使用 Pydantic 的 model_dump_json / model_validate_json 方法进行序列化与反序列化

### 需求 11：代码生成

**用户故事：** 作为协议工程师，我希望系统能从 ProtocolSchema 自动生成可执行的协议实现代码，以减少手动编码工作量。

#### 验收标准

1. WHEN 接收到一个有效的 ProtocolSchema 时，THE Codegen SHALL 为每个 ProtocolStateMachine 生成状态机实现代码，包含状态枚举、转移函数和事件处理逻辑
2. WHEN 接收到一个有效的 ProtocolSchema 时，THE Codegen SHALL 为每个 ProtocolMessage 生成报文解析器（parser）和编码器（encoder）代码
3. THE Codegen SHALL 生成的代码为 Python 语言
4. THE Codegen SHALL 将生成的代码文件输出到 `data/out/{doc_stem}_generated/` 目录
5. THE Codegen SHALL 为每个生成的报文解析器同时生成对应的格式化输出函数（pretty printer），使 ProtocolMessage 对象能被格式化为可读的文本表示
6. FOR ALL 有效的 ProtocolMessage 对象，解析原始字节后再格式化输出再解析 SHALL 产生与原始解析结果等价的对象（round-trip 性质）

### 需求 12：代码验证

**用户故事：** 作为协议工程师，我希望系统能验证生成代码与协议规范的一致性，以确保生成代码的正确性。

#### 验收标准

1. WHEN 代码生成完成后，THE Verifier SHALL 对生成的代码执行语法检查，确保代码可被 Python 解释器正确解析
2. WHEN 代码生成完成后，THE Verifier SHALL 自动生成单元测试用例，覆盖状态机的状态转移路径和报文的字段解析
3. WHEN 协议文档中包含测试向量或示例数据时，THE Verifier SHALL 使用这些数据作为测试输入验证生成代码的输出正确性
4. THE Verifier SHALL 将验证结果（通过/失败、失败原因、覆盖率）输出到 `data/out/{doc_stem}_verify_report.json` 文件
5. IF 生成的代码存在语法错误，THEN THE Verifier SHALL 在验证报告中标记错误位置和错误信息

### 需求 13：Pipeline 主流程编排

**用户故事：** 作为协议工程师，我希望能通过一个统一入口运行完整的提取流水线（分类 → 提取 → 合并 → 代码生成 → 验证），以简化操作流程。

#### 验收标准

1. THE Extraction_Pipeline SHALL 提供一个统一入口函数，接收文档名称作为参数，按顺序执行分类、提取、合并、代码生成、验证五个阶段
2. WHEN 任一阶段执行失败时，THE Extraction_Pipeline SHALL 记录错误日志并停止后续阶段的执行，同时返回已完成阶段的部分结果
3. THE Extraction_Pipeline SHALL 支持指定仅执行部分阶段（如仅分类、仅分类+提取），以便调试和增量开发
4. THE Extraction_Pipeline SHALL 在每个阶段完成后输出该阶段的执行耗时和处理节点数量的日志

### 需求 14：错误处理与容错

**用户故事：** 作为系统开发者，我希望提取流水线在遇到单个节点处理失败时能继续处理其余节点，以保证整体流水线的鲁棒性。

#### 验收标准

1. IF 单个叶节点的分类调用 LLM 失败（网络错误、超时、返回格式异常），THEN THE Classifier SHALL 记录该节点的错误信息并跳过该节点，继续处理后续节点
2. IF 单个叶节点的结构化提取失败，THEN THE Extractor SHALL 记录该节点的错误信息并跳过该节点，继续处理后续节点
3. WHEN 流水线执行完成后，THE Extraction_Pipeline SHALL 在日志中汇总报告成功处理的节点数量和失败的节点数量及其 node_id 列表
