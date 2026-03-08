"""CLI 入口 — 文档处理与 Agentic RAG 问答统一入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from src.agent.loop import agentic_rag
from src.ingest.pipeline import ProcessResult, ensure_document_ready, process_document


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        description="Agentic RAG — 文档处理 + 问答统一入口",
    )
    parser.add_argument(
        "--process",
        default=None,
        help="仅处理文档：传入 PDF 路径",
    )
    parser.add_argument(
        "--doc",
        default=None,
        help="文档名称（如 FC-LS.pdf），用于已处理文档问答或自动定位原始 PDF",
    )
    parser.add_argument(
        "--pdf",
        default=None,
        help="PDF 路径。用于问答时可自动先处理再回答",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="用户问题（问答模式必填）",
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
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="强制重建文档处理产物",
    )
    return parser


def _print_process_summary(result: ProcessResult) -> None:
    print("=" * 60)
    print("Document Process Summary")
    print("=" * 60)
    print(f"doc_name:       {result.doc_name}")
    print(f"pdf_path:       {result.pdf_path}")
    print(f"page_index:     {result.page_index_json}")
    print(f"chunks_dir:     {result.chunks_dir}")
    print(f"content_dir:    {result.content_dir}")
    print(f"total_pages:    {result.total_pages}")
    print(f"index_built:    {result.index_built}")
    print(f"structure_built:{result.structure_built}")
    print(f"content_built:  {result.content_built}")
    print(f"registered:     {result.registered}")
    print("=" * 60)


def _print_trace(response) -> None:
    if not response.trace:
        return
    print("=" * 60)
    print("Tool Call Trace")
    print("=" * 60)
    for record in response.trace:
        print(f"  Turn {record.turn} | {record.tool}")
        print(f"    Arguments: {json.dumps(record.arguments, ensure_ascii=False)}")
        print(f"    Result:    {record.result_summary}")
    print("=" * 60)
    print()


async def main() -> None:
    """CLI 主函数。"""
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    # Mode 1: process-only
    if args.process:
        if args.doc or args.pdf:
            parser.error("--process 不能与 --doc/--pdf 同时使用")
        if args.query:
            parser.error("--process 模式不支持 --query；请改用 --pdf + --query")

        logging.info("[cli] Start processing document: %s", args.process)
        result = await asyncio.to_thread(
            process_document,
            pdf_path=args.process,
            force=args.force,
            model=args.model,
        )
        logging.info("[cli] Document processing finished: %s", result.doc_name)
        _print_process_summary(result)
        return

    # QA modes require query
    if not args.query:
        parser.error("问答模式必须提供 --query")

    if args.doc and args.pdf:
        parser.error("问答模式下 --doc 与 --pdf 二选一")
    if not args.doc and not args.pdf:
        parser.error("问答模式需提供 --doc 或 --pdf")

    # Ensure document is ready
    logging.info("[cli] Ensuring document readiness")
    ready = await asyncio.to_thread(
        ensure_document_ready,
        doc=args.doc,
        pdf=args.pdf,
        force=args.force,
        model=args.model,
    )
    _print_process_summary(ready)

    # Run QA
    logging.info("[cli] QA start: doc=%s", ready.doc_name)
    response = await agentic_rag(
        query=args.query,
        doc_name=ready.doc_name,
        model=args.model,
    )
    logging.info("[cli] QA finished: turns=%s", response.total_turns)

    if args.verbose:
        _print_trace(response)

    print(response.answer)


if __name__ == "__main__":
    asyncio.run(main())
