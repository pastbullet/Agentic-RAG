# 系统测试方案

## 概述

本系统的核心流程为：**协议 PDF → LLM 提取（状态机 + 帧结构）→ C 代码生成 → 验证**。

测试分三个维度，按重要性排序：

1. 状态机提取质量
2. 帧结构提取质量
3. 代码生成正确性

---

## 一、状态机提取测试

### 数据集

使用 [RFC2PSM](https://huggingface.co/datasets/zilinlin/RFC2PSM)（NeurIPS 2025，PSMBench 配套数据集）。

- 1580 页清洗后的 RFC 原文
- 108 个人工验证的状态，297 个人工验证的转移
- 覆盖 14 个协议：BGP、TCP、DCCP、DHCP、FTP、IMAP、MQTT、NNTP、POP3、PPP、L2TP、RTSP、SIP、SMTP

### 评测指标

| 指标 | 计算方式 |
|------|----------|
| 状态召回率（State Recall） | `\|提取状态 ∩ GT 状态\| / \|GT 状态\|` |
| 状态精确率（State Precision） | `\|提取状态 ∩ GT 状态\| / \|提取状态\|` |
| 转移召回率（Transition Recall） | `\|提取转移 ∩ GT 转移\| / \|GT 转移\|` |
| 转移精确率（Transition Precision） | `\|提取转移 ∩ GT 转移\| / \|提取转移\|` |
| F1 | `2 × P × R / (P + R)` |

### 匹配规则

- 状态名匹配：经 `normalize_state_name()` 归一化后比较，不区分大小写
- 转移匹配：归一化为 `(from_state, to_state, event_keyword)` 三元组后比较，event 使用 `normalize_transition_key()` 处理，允许同义词映射（如 "timer expires" == "when the timer has expired"）

### 加载方式

```python
from datasets import load_dataset
ds = load_dataset("zilinlin/RFC2PSM")
rfc_chunks = ds["rfc_chunks"]   # RFC 原文输入
psm_labels = ds["psm_labels"]   # ground truth 状态机
```

---

## 二、帧结构提取测试

### 数据集

自标注，选取 4 个典型协议，覆盖不同复杂度：

| 协议 | RFC | 特点 |
|------|-----|------|
| ARP | RFC 826 | 极简，8 个固定字段 |
| BFD Control Packet | RFC 5880 | 固定 24 字节，含位域 |
| TCP | RFC 793 | 经典，含可变长选项字段 |
| UDP | RFC 768 | 最简单，4 个字段，用于基线验证 |

### Ground Truth 格式

```json
{
  "protocol": "BFD",
  "rfc": "RFC 5880",
  "message": "Control Packet",
  "fields": [
    {"name": "Version", "size_bits": 3},
    {"name": "Diagnostic", "size_bits": 5},
    {"name": "State", "size_bits": 2},
    {"name": "Poll", "size_bits": 1},
    {"name": "Final", "size_bits": 1},
    {"name": "Control Plane Independent", "size_bits": 1},
    {"name": "Authentication Present", "size_bits": 1},
    {"name": "Demand", "size_bits": 1},
    {"name": "Multipoint", "size_bits": 1},
    {"name": "Detect Mult", "size_bits": 8},
    {"name": "Length", "size_bits": 8},
    {"name": "My Discriminator", "size_bits": 32},
    {"name": "Your Discriminator", "size_bits": 32},
    {"name": "Desired Min TX Interval", "size_bits": 32},
    {"name": "Required Min RX Interval", "size_bits": 32},
    {"name": "Required Min Echo RX Interval", "size_bits": 32}
  ]
}
```

ground truth 文件存放于 `data/eval/frame_gt/` 目录下，每个协议一个 JSON 文件。

### 评测指标

| 指标 | 计算方式 |
|------|----------|
| 字段召回率 | `\|提取字段 ∩ GT 字段\| / \|GT 字段\|` |
| 字段精确率 | `\|提取字段 ∩ GT 字段\| / \|提取字段\|` |
| 字段大小准确率 | 匹配字段中 size_bits 正确的比例 |
| 字段顺序准确率 | 提取字段顺序与 GT 顺序的一致性（Kendall τ） |

### 匹配规则

字段名匹配使用 `normalize_field_name()` 归一化，支持：
- 大小写不敏感
- 括号内缩写提取（"Diagnostic (Diag)" 匹配 "Diag"）
- 缩写映射表（"Vers" 匹配 "Version"，"Auth Type" 匹配 "Authentication Type"）

---

## 三、代码生成测试

### 3.1 编译正确性

使用 GCC 对生成的所有 `.c` 文件做语法检查：

```bash
gcc -fsyntax-only -Wall -I<generated_dir> <file>.c
```

**指标：编译通过率** = 编译成功的文件数 / 总生成文件数

已集成在 `src/extract/verify.py` 的 `verify_generated_code()` 中。

### 3.2 符号完整性

检查生成代码中是否包含所有预期符号：

| 类型 | 预期符号示例 |
|------|-------------|
| 状态枚举 | `bfd_session_state` |
| 事件枚举 | `bfd_session_event` |
| 转移函数 | `bfd_session_transition` |
| 报文结构体 | `bfd_control_packet` |
| 序列化函数 | `bfd_control_packet_pack` |
| 反序列化函数 | `bfd_control_packet_unpack` |

**指标：符号完整率** = 存在的预期符号数 / 总预期符号数

### 3.3 Roundtrip 测试（Wireshark 真实报文）

用 Wireshark 抓取真实协议报文，验证 pack/unpack 的正确性。

**步骤：**

1. 用 Wireshark 抓取目标协议的真实流量，导出原始字节（十六进制）
2. 将字节序列作为测试向量写入测试文件
3. 调用生成的 `unpack` 函数解析，验证各字段值与 Wireshark 显示一致
4. 调用 `pack` 函数重新序列化，验证输出字节与原始字节一致

**测试协议：** BFD（固定 24 字节，结构简单，易于验证）

```c
// 示例：BFD Control Packet roundtrip 测试
uint8_t raw[] = { /* 从 Wireshark 导出的真实字节 */ };

bfd_control_packet_t pkt;
int ret = bfd_control_packet_unpack(raw, sizeof(raw), &pkt);
assert(ret == 0);
assert(pkt.version == 1);
assert(pkt.length == 24);
// ... 验证其他字段

uint8_t out[sizeof(raw)];
bfd_control_packet_pack(&pkt, out, sizeof(out));
assert(memcmp(raw, out, sizeof(raw)) == 0);
```

**指标：Roundtrip 通过率** = 通过 roundtrip 验证的报文数 / 总测试报文数

---

## 四、测试执行计划

| 阶段 | 测试内容 | 数据来源 | 预期目标 |
|------|----------|----------|----------|
| T1 | 状态机提取 | RFC2PSM（14 个协议） | State F1 ≥ 0.75，Transition F1 ≥ 0.65 |
| T2 | 帧结构提取 | 自标注（4 个协议） | 字段召回率 ≥ 0.85，大小准确率 ≥ 0.80 |
| T3 | 代码编译 | T1/T2 生成产物 | 编译通过率 = 100% |
| T4 | 符号完整性 | T1/T2 生成产物 | 符号完整率 ≥ 0.90 |
| T5 | Roundtrip | Wireshark 抓包（BFD） | Roundtrip 通过率 = 100% |

---

## 五、运行方式

```bash
# 运行单元测试
pytest tests/extract/ -q

# 运行状态机提取评测
python -m src.evaluate --test-set data/eval/psm_bench.json

# 运行帧结构提取评测
python -m src.evaluate --test-set data/eval/frame_gt/

# 运行完整 pipeline（BFD 端到端）
python -m src.main --process data/raw/rfc5880-BFD.pdf
```
