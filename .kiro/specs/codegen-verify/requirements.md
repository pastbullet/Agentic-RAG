# 需求文档：代码生成与验证（CODEGEN & VERIFY）

## 简介

本特性为协议提取流水线（Protocol Extraction Pipeline）实现最后两个阶段：CODEGEN（代码生成）和 VERIFY（代码验证）。CODEGEN 阶段从已合并的 ProtocolSchema 出发，使用模板引擎（Jinja2）生成确定性的 C 语言代码骨架，包括状态机实现（enum + switch-case）、报文结构体骨架（struct + 字段注释 + pack/unpack 函数签名桩）。VERIFY 阶段对生成的 C 代码执行语法检查（gcc -fsyntax-only）、结构完整性检查，并输出验证报告。目标语言为 C（面向交换机/路由器软件场景），生成器本身使用 Python 实现。本阶段（Phase 1）定位为"C 代码骨架生成 + 轻量验证"，仅覆盖状态机和报文格式的骨架代码生成；完整的 pack/unpack 实现、bitfield 精确序列化、round-trip 正确性验证留待后续阶段（Phase 2），待 ProtocolSchema 补充字段偏移、打包顺序等精确约束后再实现。

## 术语表

- **Codegen**：代码生成模块（`src/extract/codegen.py`），从 ProtocolSchema 生成 C 语言源代码的 Python 模块
- **Verifier**：代码验证模块（`src/extract/verify.py`），对生成的 C 代码执行自动化验证
- **ProtocolSchema**：协议的完整结构化表示，包含 state_machines、messages、procedures、timers、errors、constants，是代码生成的输入
- **ProtocolStateMachine**：状态机模型，包含 states（name、description、is_initial、is_final）和 transitions（from_state、to_state、event、condition、actions）
- **ProtocolMessage**：报文结构模型，包含 fields 列表（name、type、size_bits、description）
- **VerifyReport**：验证报告数据结构，包含 syntax_ok、syntax_errors、test_results、coverage_summary 等字段
- **Bitfield**：C 语言位域，用于表示不足一个字节的字段（如 BFD 的 Vers:3、Diag:5）
- **Pack/Unpack**：将结构体序列化为网络字节序的字节流（pack）和从字节流反序列化为结构体（unpack）的函数对
- **Network_Byte_Order**：网络字节序（大端序），通过 htonl/ntohl/htons/ntohs 函数转换
- **Generated_Dir**：生成代码的输出目录，路径为 `data/out/{doc_stem}/generated/`
- **Template_Engine**：Jinja2 模板引擎，用于从 ProtocolSchema 数据渲染 C 代码文件

## 需求

### 需求 1：状态机 C 代码生成

**用户故事：** 作为协议工程师，我希望系统能从 ProtocolStateMachine 自动生成 C 语言的状态机实现代码，以减少手动编写状态机逻辑的工作量。

#### 验收标准

1. WHEN 接收到一个包含 ProtocolStateMachine 的 ProtocolSchema 时，THE Codegen SHALL 为每个 ProtocolStateMachine 生成一个 `.h` 头文件和一个 `.c` 源文件
2. THE Codegen SHALL 在头文件中生成一个 `enum` 类型，枚举该状态机的所有状态（state name 转换为大写下划线命名）
3. THE Codegen SHALL 在头文件中生成一个 `enum` 类型，枚举该状态机的所有事件（从 transitions 的 event 字段去重提取）
4. THE Codegen SHALL 在源文件中生成一个状态转移函数，使用 `switch-case` 结构根据当前状态和事件返回下一个状态
5. THE Codegen SHALL 在生成的代码中以注释形式标注 transition 的 condition 和 actions 信息，供开发者参考
6. THE Codegen SHALL 使用 Jinja2 模板引擎渲染 C 代码，确保相同输入产生相同输出（确定性生成）

### 需求 2：报文结构体 C 代码骨架生成

**用户故事：** 作为协议工程师，我希望系统能从 ProtocolMessage 自动生成 C 语言的报文结构体定义及 pack/unpack 函数桩，以便作为后续手动实现编解码逻辑的起点。

#### 验收标准

1. WHEN 接收到一个包含 ProtocolMessage 的 ProtocolSchema 时，THE Codegen SHALL 为每个 ProtocolMessage 生成一个 `.h` 头文件和一个 `.c` 源文件
2. THE Codegen SHALL 在头文件中生成一个 `struct` 定义，将每个 ProtocolField 按以下规则映射为 C 类型字段（类型来自 `<stdint.h>`）：size_bits == 8 → `uint8_t`；size_bits == 16 → `uint16_t`；size_bits == 32 → `uint32_t`；size_bits == 64 → `uint64_t`；size_bits < 8 → `uint8_t` 占位 + 注释标注实际位宽；8 < size_bits < 16 → `uint16_t` 占位 + 注释标注实际位宽；16 < size_bits < 32 → `uint32_t` 占位 + 注释标注实际位宽；32 < size_bits < 64 → `uint64_t` 占位 + 注释标注实际位宽；size_bits > 64 → `uint8_t {field_name}[N]` 字节数组（N = ceil(size_bits/8)）+ 注释标注实际位宽；size_bits 为 None → `uint32_t` 默认占位 + `/* TODO: size unknown */` 注释
3. WHEN ProtocolField 的 size_bits 不足 8 位或不对齐标准宽度时，THE Codegen SHALL 在结构体中以注释形式标注该字段的位宽信息（如 `/* Vers: 3 bits */`），使用向上取整的标准宽度整型作为占位类型；不强制生成 C 位域语法，以避免编译器可移植性问题
4. THE Codegen SHALL 在源文件中为每个 ProtocolMessage 生成 `pack` 和 `unpack` 函数签名桩（函数体仅包含 `/* TODO: implement in Phase 2 */` 注释和 `return -1;` 占位返回）
5. THE Codegen SHALL 在生成的结构体定义中以注释形式标注每个字段的 description 和 size_bits 信息
6. THE Codegen SHALL 在 pack/unpack 函数桩的注释中列出该报文的所有字段名及其位宽，供后续实现者参考

### 需求 3：生成代码的文件组织

**用户故事：** 作为协议工程师，我希望生成的 C 代码文件有清晰的目录结构和统一的头文件入口，以便集成到现有工程中。

#### 验收标准

1. THE Codegen SHALL 将所有生成的 C 文件输出到 `data/out/{doc_stem}/generated/` 目录
2. THE Codegen SHALL 生成一个主头文件 `{protocol_name}.h`，通过 `#include` 包含所有生成的子头文件
3. THE Codegen SHALL 在每个生成的文件头部添加注释，标注该文件由代码生成器自动生成（generator_name）和源文档名称（source_document）；不写入生成时间戳，以保证确定性生成（需求 4.3）
4. THE Codegen SHALL 为每个头文件添加 `#ifndef/#define/#endif` 的 include guard
5. THE Codegen SHALL 返回所有生成文件的路径列表，供后续 VERIFY 阶段使用

### 需求 4：模板引擎与确定性生成

**用户故事：** 作为系统开发者，我希望代码生成基于模板引擎实现且输出确定性，以便对生成结果进行回归测试。

#### 验收标准

1. THE Codegen SHALL 使用 Jinja2 作为模板引擎，将 C 代码模板与 ProtocolSchema 数据分离
2. THE Codegen SHALL 将模板文件存放在 `src/extract/templates/` 目录下
3. FOR ALL 相同的 ProtocolSchema 输入，THE Codegen SHALL 产生逐字节相同的输出文件（确定性生成）
4. THE Codegen SHALL 在模板渲染前对可排序集合按稳定键排序，以消除输入顺序对输出的影响：state_machine 和 message 按 name 排序；state 按 name 排序；transition 按 (from_state, to_state, event) 三元组排序

### 需求 5：语法验证

**用户故事：** 作为协议工程师，我希望系统能自动检查生成的 C 代码是否存在语法错误，以确保生成代码可被编译器正确解析。

#### 验收标准

1. WHEN 代码生成完成后，THE Verifier SHALL 对 Generated_Dir 中的每个 `.c` 文件执行 `gcc -fsyntax-only -Wall` 命令进行语法检查
2. WHEN 所有 `.c` 文件均通过语法检查时，THE Verifier SHALL 在 VerifyReport 中将 syntax_ok 设置为 true
3. IF 任一 `.c` 文件存在语法错误，THEN THE Verifier SHALL 在 VerifyReport 的 syntax_errors 列表中记录该文件路径、行号和错误信息
4. IF 系统中未安装 gcc 编译器，THEN THE Verifier SHALL 在 VerifyReport 中将 syntax_checked 设置为 false、syntax_ok 设置为 false，并在 coverage_summary 中注明"语法检查因 gcc 不可用而跳过"；syntax_checked 字段用于区分"未检查"与"检查失败"两种语义

### 需求 6：结构完整性验证

**用户故事：** 作为协议工程师，我希望系统能验证生成的 C 代码中包含了 ProtocolSchema 中定义的所有结构体和函数，以确保代码生成的完整性。

#### 验收标准

1. WHEN 代码生成完成后，THE Verifier SHALL 检查 Generated_Dir 中是否为每个 ProtocolStateMachine 生成了对应的状态枚举和转移函数
2. WHEN 代码生成完成后，THE Verifier SHALL 检查 Generated_Dir 中是否为每个 ProtocolMessage 生成了对应的结构体定义、pack 函数和 unpack 函数
3. THE Verifier SHALL 在 VerifyReport 的 test_results 列表中记录每项结构完整性检查的结果（检查项名称、通过/失败、缺失项说明）
4. THE Verifier SHALL 通过文本搜索（grep 或正则匹配）方式检查生成代码中的符号定义，无需编译或链接

### 需求 7：Pack/Unpack Round-Trip 测试生成

**用户故事：** 作为协议工程师，我希望系统能自动生成 C 语言的 round-trip 测试代码框架，为后续 Phase 2 实现完整 pack/unpack 后的验证做好准备。

#### 验收标准

1. THE Verifier SHALL 生成一个 `test_roundtrip.c` 文件，包含 main 函数和每个 ProtocolMessage 对应的测试函数占位桩
2. THE Verifier SHALL 在每个测试函数桩中以注释形式列出该报文的所有字段名及其位宽，供后续实现者参考
3. THE Verifier SHALL 在测试函数桩的函数体中标注 `/* TODO: implement after Phase 2 pack/unpack */` 并返回占位结果
4. THE Verifier SHALL 确保生成的 `test_roundtrip.c` 能通过 `gcc -fsyntax-only` 语法检查（即使所有测试函数均为桩）

### 需求 8：验证报告输出

**用户故事：** 作为协议工程师，我希望验证结果以结构化的 JSON 报告形式输出，以便自动化流程读取和展示。

#### 验收标准

1. THE Verifier SHALL 将验证结果输出到 `data/out/{doc_stem}/verify_report.json` 文件
2. THE VerifyReport SHALL 包含以下字段：syntax_checked（布尔值，是否执行了语法检查）、syntax_ok（布尔值）、syntax_errors（错误列表）、structural_checks（结构完整性检查结果列表）、test_results（测试结果列表）、coverage_summary（文本摘要）
3. THE Verifier SHALL 在 coverage_summary 中汇总报告：已检查的状态机数量、已检查的报文数量、语法检查结果、结构完整性检查通过率
4. FOR ALL 有效的 VerifyReport 对象，序列化为 JSON 后再反序列化 SHALL 产生与原始对象等价的 VerifyReport（round-trip 性质）

### 需求 9：Pipeline 集成

**用户故事：** 作为协议工程师，我希望 CODEGEN 和 VERIFY 阶段能无缝集成到现有的 Extraction Pipeline 主流程中，以便通过统一入口运行完整的五阶段流水线。

#### 验收标准

1. WHEN Pipeline 执行到 CODEGEN 阶段时，THE Extraction_Pipeline SHALL 加载 MERGE 阶段产出的 ProtocolSchema（从 `data/out/{doc_stem}/protocol_schema.json`），调用 Codegen 生成 C 代码，并将生成文件路径列表记录到 StageResult.data 中
2. WHEN Pipeline 执行到 VERIFY 阶段时，THE Extraction_Pipeline SHALL 将 CODEGEN 阶段的输出目录和 ProtocolSchema 传递给 Verifier，执行验证并将 VerifyReport 记录到 StageResult.data 中
3. WHEN CODEGEN 阶段执行失败时，THE Extraction_Pipeline SHALL 记录错误日志并停止后续 VERIFY 阶段的执行
4. THE Extraction_Pipeline 的默认阶段序列 SHALL 更新为包含全部五个阶段：CLASSIFY → EXTRACT → MERGE → CODEGEN → VERIFY
5. THE Extraction_Pipeline SHALL 支持单独执行 CODEGEN 或 VERIFY 阶段（前提是前置阶段的产出文件已存在）

### 需求 10：C 标识符命名规范

**用户故事：** 作为协议工程师，我希望生成的 C 代码中的标识符命名遵循 C 语言惯例，以确保代码可读性和编译兼容性。

#### 验收标准

1. THE Codegen SHALL 将 ProtocolSchema 中的名称转换为合法的 C 标识符：空格替换为下划线、移除特殊字符、以字母或下划线开头
2. THE Codegen SHALL 将状态名和事件名转换为全大写下划线命名（如 `STATE_INIT`、`EVENT_TIMER_EXPIRED`）
3. THE Codegen SHALL 将结构体名和函数名转换为小写下划线命名（如 `bfd_control_packet`、`bfd_control_packet_pack`）
4. THE Codegen SHALL 为所有生成的公开符号添加协议名称前缀（如 `bfd_`），以避免命名冲突
