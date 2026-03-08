"""CLI 入口 — 通过命令行提问并获取答案。

运行方式:
    python -m src.main --doc rfc5880-BFD.pdf --query "BFD 控制报文有哪些字段？"
    python -m src.main --doc FC-LS.pdf --query "..." --model gpt-4o --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json

from src.agent.loop import agentic_rag


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        description="Agentic RAG — 基于 PageIndex 范式的文档问答系统",
    )
    parser.add_argument(
        "--doc",
        required=True,
        help="文档名称，如 rfc5880-BFD.pdf",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="用户问题",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM 模型名称（可选）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="详细模式，打印完整 tool call trace",
    )
    return parser


async def main() -> None:
    """CLI 主函数。"""
    parser = build_parser()
    args = parser.parse_args()

    response = await agentic_rag(
        query=args.query,
        doc_name=args.doc,
        model=args.model,
    )

    # --verbose: 打印完整 tool call trace (Req 9.2)
    if args.verbose and response.trace:
        print("=" * 60)
        print("Tool Call Trace")
        print("=" * 60)
        for record in response.trace:
            print(f"  Turn {record.turn} | {record.tool}")
            print(f"    Arguments: {json.dumps(record.arguments, ensure_ascii=False)}")
            print(f"    Result:    {record.result_summary}")
        print("=" * 60)
        print()

    # 输出最终答案 (Req 9.3)
    print(response.answer)


if __name__ == "__main__":
    asyncio.run(main())
