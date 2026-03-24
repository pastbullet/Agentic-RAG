# 实现计划：代码生成与验证（CODEGEN & VERIFY）

## 概述

基于设计文档，将 CODEGEN 和 VERIFY 两个阶段的实现拆分为增量式编码任务。每个任务在前一个任务的基础上递进构建，最终通过 pipeline.py 将所有组件串联。实现语言为 Python（Jinja2 模板 + pytest/hypothesis 测试），目标生成语言为 C。

## 任务

- [ ] 1. 实现核心工具函数与数据类型
  - [ ] 1.1 在 `src/extract/codegen.py` 中实现 `FieldTypeInfo` dataclass 及 `render_declaration()` 方法
    - 定义 `c_type`、`array_len`、`comment` 字段
    - `render_declaration(field_name)` 根据 `array_len` 是否为 None 拼接 C 声明字符串
    - _需求: 2.2, 2.3_

  - [ ] 1.2 在 `src/extract/codegen.py` 中实现 `_sanitize_c_identifier(name)`
    - 空格/连字符/点号替换为下划线，移除非 `[a-zA-Z0-9_]` 字符
    - 首字符为数字时前缀加下划线，连续下划线合并，空字符串返回 `_unnamed`
    - _需求: 10.1_

  - [ ] 1.3 在 `src/extract/codegen.py` 中实现 `_to_upper_snake(name)` 和 `_to_lower_snake(name)`
    - `_to_upper_snake`: 调用 `_sanitize_c_identifier` 后转大写
    - `_to_lower_snake`: 调用 `_sanitize_c_identifier` 后转小写
    - _需求: 10.2, 10.3_

  - [ ] 1.4 在 `src/extract/codegen.py` 中实现 `_protocol_prefix(protocol_name)`
    - 以 `-` 分割，过滤 `r'^rfc\d*$'`（不区分大小写）的段
    - 保留所有非 RFC 段，各段 `_sanitize_c_identifier` 后转小写，用 `_` 连接
    - 空结果回退为 `'proto'`
    - _需求: 10.4_

  - [ ] 1.5 在 `src/extract/codegen.py` 中实现 `_map_field_type(field)` 返回 `FieldTypeInfo`
    - 严格按设计文档的映射规则表处理 size_bits 的所有分支
    - size_bits > 64 时 `array_len = ceil(size_bits / 8)`
    - size_bits 为 None 时返回 `uint32_t` + `/* TODO: size unknown */`
    - _需求: 2.2, 2.3, 2.5_

  - [ ] 1.6 在 `tests/extract/test_codegen.py` 中编写属性测试：Property 4（标识符合法性）
    - **Property 4: 标识符合法性**
    - 使用 Hypothesis `st.text()` 生成任意字符串
    - 验证 `_sanitize_c_identifier` 输出匹配 `^[a-zA-Z_][a-zA-Z0-9_]*$`
    - 验证 `_to_upper_snake` 输出仅含大写字母、数字、下划线
    - 验证 `_to_lower_snake` 输出仅含小写字母、数字、下划线
    - **验证: 需求 10.1, 10.2, 10.3, 10.4**

  - [ ] 1.7 在 `tests/extract/test_codegen.py` 中编写属性测试：Property 5（字段类型映射正确性）
    - **Property 5: 字段类型映射正确性**
    - 使用 Hypothesis 生成随机 `ProtocolField`（size_bits 为 None 或 1~128 的整数）
    - 验证 `_map_field_type` 返回的 `FieldTypeInfo` 符合设计文档映射规则表
    - 验证 `render_declaration(field_name)` 生成的字符串以 `;` 结尾且包含合法 C 类型
    - **验证: 需求 2.2, 2.3**

- [ ] 2. 创建 Jinja2 模板文件
  - [ ] 2.1 创建 `src/extract/templates/state_machine.h.j2` 状态机头文件模板
    - 文件头注释（generator_name + source_document，不含时间戳）
    - `#ifndef/#define/#endif` include guard
    - `#include <stdint.h>`
    - 状态枚举 `enum {prefix}_{sm_name}_state { ... }`（排序后的状态列表）
    - 事件枚举 `enum {prefix}_{sm_name}_event { ... }`（去重排序后的事件列表）
    - 转移函数声明 `enum {prefix}_{sm_name}_state {prefix}_{sm_name}_transition(...)`
    - _需求: 1.1, 1.2, 1.3, 3.3, 3.4_

  - [ ] 2.2 创建 `src/extract/templates/state_machine.c.j2` 状态机源文件模板
    - 文件头注释
    - `#include` 对应头文件
    - 转移函数实现：外层 switch(current_state)，内层 switch(event)
    - 每个 transition 的 condition 和 actions 以注释形式标注
    - _需求: 1.4, 1.5, 1.6_

  - [ ] 2.3 创建 `src/extract/templates/message.h.j2` 报文头文件模板
    - 文件头注释 + include guard + `#include <stdint.h>`
    - `struct {prefix}_{msg_name} { ... }` 结构体定义
    - 每个字段使用 `field.type_info.render_declaration(field.name)` 渲染
    - 字段注释标注 description 和 size_bits
    - pack/unpack 函数声明
    - _需求: 2.1, 2.2, 2.3, 2.5, 3.4_

  - [ ] 2.4 创建 `src/extract/templates/message.c.j2` 报文源文件模板
    - 文件头注释 + `#include` 对应头文件
    - pack 函数桩：函数体含 `/* TODO: implement in Phase 2 */` + `return -1;`
    - unpack 函数桩：同上
    - 函数桩注释中列出所有字段名及位宽
    - _需求: 2.4, 2.6_

  - [ ] 2.5 创建 `src/extract/templates/main_header.h.j2` 主头文件模板
    - 文件头注释 + include guard
    - `#include` 仅成功生成且排序后的子头文件（跳过的组件对应的头文件不包含）
    - _需求: 3.2, 3.3, 3.4_

  - [ ] 2.6 创建 `src/extract/templates/test_roundtrip.c.j2` 测试桩模板
    - `#include` 仅成功生成的报文头文件
    - 每个报文对应 `test_{msg_name}_roundtrip()` 函数桩
    - 函数体含字段列表注释 + `/* TODO: implement after Phase 2 pack/unpack */` + `return 0;`
    - `main()` 函数调用所有测试函数并汇总结果
    - _需求: 7.1, 7.2, 7.3, 7.4_

- [ ] 3. 实现 codegen.py 主逻辑
  - [ ] 3.1 在 `src/extract/codegen.py` 中实现 `CodegenResult` dataclass
    - 字段：`files: list[str]`、`skipped_components: list[dict]`、`warnings: list[str]`、`expected_symbols: list[dict]`、`generated_msg_headers: list[str]`（成功生成的报文头文件路径列表）、`generated_msgs: list[ProtocolMessage]`（成功生成的 ProtocolMessage 列表）
    - _需求: 3.5_

  - [ ] 3.2 在 `src/extract/codegen.py` 中实现 `_sort_schema(schema)`
    - state_machines 按 name 排序，messages 按 name 排序
    - 每个 state_machine 内部：states 按 name 排序，transitions 按 (from_state, to_state, event) 排序
    - fields 保持原始顺序
    - 返回新 ProtocolSchema 对象
    - _需求: 4.3, 4.4_

  - [ ] 3.3 在 `src/extract/codegen.py` 中实现 `_load_templates()` 和 `_build_expected_symbols()`
    - `_load_templates()`: FileSystemLoader 指向 `src/extract/templates/`，注册自定义 filter
    - `_build_expected_symbols()`: 仅为成功生成的组件构建预期符号列表
    - _需求: 4.1, 4.2, 6.1, 6.2_

  - [ ] 3.4 在 `src/extract/codegen.py` 中实现 `generate_code(schema, output_dir)` 主函数
    - 调用 `_sort_schema` → `_load_templates` → 遍历渲染状态机和报文 → 渲染主头文件
    - 主头文件 `{protocol_name}.h` 仅 `#include` 成功生成的子头文件（跳过的组件对应的头文件不包含）
    - 单个组件渲染失败时记录到 `skipped_components`，继续处理其他组件
    - 调用 `_build_expected_symbols` 仅为成功生成的组件构建符号列表
    - 返回 `CodegenResult`
    - _需求: 1.1, 1.6, 2.1, 3.1, 3.2, 3.5, 10.4_

  - [ ] 3.5 在 `tests/extract/test_codegen.py` 中编写属性测试：Property 1（确定性生成）
    - **Property 1: 确定性生成**
    - 使用 Hypothesis 生成随机 ProtocolSchema
    - 对同一 schema 调用 `generate_code` 两次（不同 tmp_path），比较所有文件逐字节相同
    - 打乱 state_machines/messages 顺序后再次生成，验证输出仍一致
    - **验证: 需求 1.6, 4.3, 4.4**

  - [ ] 3.6 在 `tests/extract/test_codegen.py` 中编写属性测试：Property 3（结构完整性）
    - **Property 3: 结构完整性**
    - 使用 Hypothesis 生成随机 ProtocolSchema
    - 验证每个未跳过的状态机有状态枚举、事件枚举、转移函数
    - 验证每个未跳过的报文有结构体、pack/unpack 函数签名桩
    - 验证主头文件 `#include` 所有成功生成的子头文件
    - 验证 `CodegenResult.files` 中每个路径存在于磁盘
    - 验证 `expected_symbols` 中每个符号可在生成代码中被子串匹配找到
    - **验证: 需求 1.1, 1.2, 1.3, 1.4, 2.1, 2.4, 3.2, 3.5, 6.1, 6.2**

- [ ] 4. 检查点 — 确保 codegen 模块测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [ ] 5. 实现 verify.py 验证逻辑
  - [ ] 5.1 更新 `src/extract/verify.py` 中的 `VerifyReport` dataclass
    - 新增 `syntax_checked` 字段，新增 `structural_checks` 字段
    - 移除 `test_vectors_used` 字段
    - 实现 `to_dict()` 和 `from_dict()` 方法
    - _需求: 5.4, 8.2, 8.4_

  - [ ] 5.2 在 `src/extract/verify.py` 中实现 `_is_gcc_available()` 和 `_check_syntax()`
    - `_is_gcc_available()`: 通过 `shutil.which('gcc')` 检测
    - `_check_syntax()`: 执行 `subprocess.run(['gcc', '-fsyntax-only', '-Wall', '-I', include_dir, file_path])`，解析 stderr 提取错误
    - _需求: 5.1, 5.2, 5.3, 5.4_

  - [ ] 5.3 在 `src/extract/verify.py` 中实现 `_check_structural_completeness()`
    - 读取 generated_dir 下所有 .h/.c 文件全文，拼接为单个文本
    - 遍历 expected_symbols，对每个符号做简单子串匹配（`symbol in text`）
    - 返回检查结果列表 `[{'check': str, 'passed': bool, 'detail': str}]`
    - _需求: 6.1, 6.2, 6.3, 6.4_

  - [ ] 5.3.5 在 `src/extract/verify.py` 中实现 `_infer_expected_symbols_from_generated_files()`
    - 当 VERIFY 单独执行（无 CODEGEN 结果）且 `expected_symbols=None` 时，扫描 `generated_dir` 下的 .h/.c 文件推断预期符号
    - 解析文件名模式（如 `{prefix}_sm_{name}.h`、`{prefix}_msg_{name}.h`）提取组件信息
    - 返回推断的 `expected_symbols` 列表，格式与 `_build_expected_symbols()` 输出一致
    - 在 `verify_generated_code()` 中当 `expected_symbols is None` 时调用此函数
    - _需求: 9.5_

  - [ ] 5.4 在 `src/extract/verify.py` 中实现 `_generate_roundtrip_stub()`
    - 函数签名：`_generate_roundtrip_stub(generated_msg_headers: list[str], generated_msgs: list[ProtocolMessage], output_dir: str, protocol_prefix: str) -> str`
    - 接收 `generated_msg_headers`（成功生成的报文头文件路径列表）和 `generated_msgs`（成功生成的 ProtocolMessage 列表）作为输入
    - 使用 Jinja2 模板 `test_roundtrip.c.j2` 渲染
    - 仅面向成功生成的 message 列表，不 `#include` 被跳过的头文件
    - _需求: 7.1, 7.2, 7.3_

  - [ ] 5.5 在 `src/extract/verify.py` 中实现 `verify_generated_code()` 主函数
    - 完整签名：`verify_generated_code(generated_dir: str, schema: ProtocolSchema, doc_name: str, expected_symbols: list[dict] | None = None, generated_msg_headers: list[str] | None = None, generated_msgs: list[ProtocolMessage] | None = None) -> VerifyReport`
    - 接收 `generated_msg_headers` 和 `generated_msgs` 参数（来自 CODEGEN 阶段的 CodegenResult），传递给 `_generate_roundtrip_stub()`
    - 调用 `_is_gcc_available` → `_check_syntax`（每个 .c 文件）→ `_check_structural_completeness` → `_generate_roundtrip_stub` → 对 test_roundtrip.c 也执行语法检查
    - 汇总 coverage_summary：组件计数应反映"成功生成的组件数"，而非 schema 原始组件数
    - 返回 VerifyReport
    - _需求: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 7.4, 8.2, 8.3_

  - [ ] 5.6 在 `tests/extract/test_verify.py` 中编写属性测试：Property 6（VerifyReport Round-Trip）
    - **Property 6: VerifyReport 序列化 Round-Trip**
    - 使用 Hypothesis 生成随机 VerifyReport 对象
    - 验证 `VerifyReport.from_dict(report.to_dict())` 与原始对象等价
    - **验证: 需求 8.4**

  - [ ]* 5.7 在 `tests/extract/test_verify.py` 中编写属性测试：Property 2（语法有效性）
    - **Property 2: 语法有效性**
    - 使用 Hypothesis 生成包含至少一个状态机或报文的 ProtocolSchema
    - 先调用 `generate_code` 获取 C 文件和 CodegenResult，再调用 `verify_generated_code()` 执行完整验证流程
    - 验证 VerifyReport 中 `syntax_ok=True`（test_roundtrip.c 由 verify 内部生成并检查）
    - 标记 `@pytest.mark.skipif(not _is_gcc_available(), reason='gcc not available')`
    - **验证: 需求 5.1, 5.2, 7.4**

- [ ] 6. 检查点 — 确保 verify 模块测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [ ] 7. Pipeline 集成
  - [ ] 7.1 在 `src/extract/pipeline.py` 中实现 CODEGEN 阶段处理逻辑
    - 从内存 schema 或 `protocol_schema.json` 文件加载 ProtocolSchema
    - 构造 `generated_dir = artifact_dir / "generated"`
    - 调用 `generate_code(schema, str(generated_dir))`
    - 将 CodegenResult 各字段记录到 StageResult.data
    - _需求: 9.1, 9.3_

  - [ ] 7.2 在 `src/extract/pipeline.py` 中实现 VERIFY 阶段处理逻辑
    - 从 CODEGEN 阶段的 CodegenResult 中提取 `expected_symbols`、`generated_msg_headers` 和 `generated_msgs`，传递给 `verify_generated_code()`
    - 调用 `verify_generated_code(generated_dir, schema, doc_name, expected_symbols, generated_msg_headers, generated_msgs)`
    - 将 VerifyReport 序列化写入 `verify_report.json`
    - 将 VerifyReport 记录到 StageResult.data
    - _需求: 9.2, 8.1_

  - [ ] 7.3 更新 `_default_stage_sequence()` 为五阶段序列
    - 返回 `[CLASSIFY, EXTRACT, MERGE, CODEGEN, VERIFY]`
    - _需求: 9.4_

  - [ ] 7.4 实现 CODEGEN 失败时阻止 VERIFY 执行的逻辑
    - CODEGEN 阶段 StageResult.success == False 时 break，不执行 VERIFY
    - _需求: 9.3_

  - [ ] 7.5 在 `tests/extract/test_verify.py` 中编写属性测试：Property 7（Pipeline 阶段控制）
    - **Property 7: Pipeline 阶段控制**
    - Mock codegen 使其抛出异常，验证 VERIFY 阶段不执行
    - Mock codegen 使其成功，验证 VERIFY 阶段正常执行
    - 验证单独执行 CODEGEN/VERIFY 阶段（前置产出文件已存在）能独立完成
    - **验证: 需求 9.3, 9.5**

- [ ] 8. 单元测试补充
  - [ ] 8.1 在 `tests/extract/test_codegen.py` 中编写 codegen 单元测试
    - 空 Schema 测试：无状态机、无报文时仅生成主头文件
    - BFD 具体示例测试：在代码中构造合成 BFD schema 片段（synthetic schema fragment），不直接读取 `data/out/rfc5880-BFD/protocol_schema.json`，验证端到端生成
    - 错误路径测试：模板缺失时的异常处理
    - _需求: 1.1, 2.1, 3.2_

  - [ ] 8.2 在 `tests/extract/test_verify.py` 中编写 verify 单元测试
    - gcc 不可用测试：mock `shutil.which('gcc')` 返回 None，验证 `syntax_checked=False`
    - 结构完整性检查测试：构造已知符号列表验证子串匹配逻辑
    - coverage_summary 格式测试：验证摘要包含状态机数量、报文数量等信息
    - _需求: 5.4, 6.3, 8.3_

- [ ] 9. 最终检查点 — 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的子任务为可选（仅 5.7 依赖外部工具 gcc），可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号以确保可追溯性
- 检查点任务用于增量验证，确保每个阶段的实现正确
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界条件
- Hypothesis 属性测试配置 `@settings(max_examples=100)`
