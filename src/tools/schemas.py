"""Tool Schema 定义 — OpenAI function calling 格式，支持转换为 Anthropic 格式。"""

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_document_structure",
            "description": (
                "获取文档的目录树索引（分块加载）。返回章节标题、摘要和页码范围。"
                "通过阅读摘要判断章节相关性，找到与问题相关的章节后，"
                "使用 get_page_content 获取具体页面内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_name": {
                        "type": "string",
                        "description": "文档名称，如 'FC-LS.pdf' 或 'rfc5880-BFD.pdf'",
                    },
                    "part": {
                        "type": "integer",
                        "description": "目录树分块编号，从 1 开始。首次调用使用默认值 1 查看文档概览。",
                        "default": 1,
                    },
                },
                "required": ["doc_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_content",
            "description": (
                "获取文档指定页码的实际内容（文本、表格、图片）。"
                "页码范围从目录树节点的 start_index 和 end_index 获得。"
                "单次请求不超过 10 页，超过请分批请求。"
                "支持三种页码格式：单页 '7'、范围 '7-11'、逗号分隔 '7,9,11'。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_name": {
                        "type": "string",
                        "description": "文档名称，如 'FC-LS.pdf' 或 'rfc5880-BFD.pdf'",
                    },
                    "pages": {
                        "type": "string",
                        "description": "页码字符串。单页: '7', 范围: '7-11', 逗号分隔: '7,9,11'",
                    },
                },
                "required": ["doc_name", "pages"],
            },
        },
    },
]


def get_tool_schemas() -> list[dict]:
    """返回 Tool Schema 列表（OpenAI function calling 格式）。"""
    return TOOL_SCHEMAS


def convert_to_anthropic_format(schemas: list[dict]) -> list[dict]:
    """将 OpenAI function calling 格式的 schema 转换为 Anthropic 格式。

    OpenAI:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Anthropic: {"name": ..., "description": ..., "input_schema": ...}
    """
    result = []
    for schema in schemas:
        func = schema["function"]
        result.append(
            {
                "name": func["name"],
                "description": func["description"],
                "input_schema": func["parameters"],
            }
        )
    return result
