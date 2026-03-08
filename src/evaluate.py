"""评测脚本 — 自动化评测 Agentic RAG 系统表现。

从 JSON 测试集加载用例，逐个调用 agentic_rag，计算指标并输出汇总结果。

运行方式: python -m src.evaluate --test-set data/eval/test_questions.json --model gpt-4o
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from src.models import TestCase, EvalResult
from src.agent.loop import agentic_rag

logger = logging.getLogger(__name__)


def load_test_cases(path: str) -> list[TestCase]:
    """从 JSON 文件加载 TestCase 列表。

    Args:
        path: JSON 文件路径

    Returns:
        TestCase 对象列表
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [TestCase(**item) for item in data]


async def evaluate_single(test_case: TestCase, model: str | None) -> EvalResult:
    """对单个测试用例执行评测。

    Args:
        test_case: 测试用例
        model: LLM 模型名称（可选）

    Returns:
        EvalResult 评测结果
    """
    # 调用 agentic_rag 获取答案
    response = await agentic_rag(
        query=test_case.query,
        doc_name=test_case.doc_name,
        model=model,
    )

    answer_lower = response.answer.lower()

    # 计算 key_points 覆盖率：case-insensitive substring match
    key_points_covered = sum(
        1 for kp in test_case.key_points if kp.lower() in answer_lower
    )
    key_points_total = len(test_case.key_points)

    # 计算引用有效率：引用页码在 pages_retrieved 中的比例
    citation_count = len(response.citations)
    retrieved_set = set(response.pages_retrieved)
    if citation_count > 0:
        valid_citations = sum(
            1 for c in response.citations if c.page in retrieved_set
        )
        citation_valid_rate = valid_citations / citation_count
    else:
        citation_valid_rate = 1.0  # 无引用时视为 100%

    # 计算检索页码命中率：expected_pages 中出现在 pages_retrieved 中的比例
    if test_case.expected_pages:
        pages_hit = sum(
            1 for p in test_case.expected_pages if p in retrieved_set
        )
        pages_hit_rate = pages_hit / len(test_case.expected_pages)
    else:
        pages_hit_rate = 1.0  # 无预期页码时视为 100%

    return EvalResult(
        id=test_case.id,
        query=test_case.query,
        key_points_covered=key_points_covered,
        key_points_total=key_points_total,
        citation_count=citation_count,
        citation_valid_rate=citation_valid_rate,
        total_turns=response.total_turns,
        pages_hit_rate=pages_hit_rate,
        answer=response.answer,
    )


async def evaluate_all(test_set_path: str, model: str | None = None) -> list[EvalResult]:
    """从 JSON 测试集加载用例，逐个评测并输出汇总指标。

    Args:
        test_set_path: 测试集 JSON 文件路径
        model: LLM 模型名称（可选）

    Returns:
        EvalResult 列表
    """
    test_cases = load_test_cases(test_set_path)
    results: list[EvalResult] = []

    print(f"\n{'='*60}")
    print(f"评测开始 — 共 {len(test_cases)} 个用例")
    print(f"{'='*60}\n")

    for i, tc in enumerate(test_cases, 1):
        print(f"[{i}/{len(test_cases)}] {tc.id}: {tc.query[:50]}...")
        result = await evaluate_single(tc, model)
        results.append(result)

        # 打印每个用例的详细结果
        kp_rate = (
            result.key_points_covered / result.key_points_total * 100
            if result.key_points_total > 0
            else 100.0
        )
        print(f"  key_points: {result.key_points_covered}/{result.key_points_total} ({kp_rate:.1f}%)")
        print(f"  citation_valid_rate: {result.citation_valid_rate:.1%}")
        print(f"  total_turns: {result.total_turns}")
        print(f"  pages_hit_rate: {result.pages_hit_rate:.1%}")
        print(f"  citations: {result.citation_count}")
        print()

    # 计算汇总指标
    n = len(results)
    if n == 0:
        print("无评测结果。")
        return results

    avg_kp_rate = sum(
        r.key_points_covered / r.key_points_total if r.key_points_total > 0 else 1.0
        for r in results
    ) / n * 100

    avg_citation_valid = sum(r.citation_valid_rate for r in results) / n * 100
    avg_turns = sum(r.total_turns for r in results) / n
    avg_pages_hit = sum(r.pages_hit_rate for r in results) / n * 100

    # 输出汇总
    print(f"{'='*60}")
    print("汇总指标")
    print(f"{'='*60}")
    print(f"  key_points 覆盖率:  {avg_kp_rate:.1f}%  (目标 > 80%)")
    print(f"  引用有效率:         {avg_citation_valid:.1f}%  (目标 > 90%)")
    print(f"  平均轮次:           {avg_turns:.1f}    (目标 4-8)")
    print(f"  页码命中率:         {avg_pages_hit:.1f}%  (目标 > 70%)")
    print(f"  用例总数:           {n}")
    print(f"{'='*60}\n")

    return results


def main():
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="Agentic RAG 评测脚本")
    parser.add_argument(
        "--test-set",
        required=True,
        help="测试集 JSON 文件路径",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM 模型名称（可选）",
    )
    args = parser.parse_args()

    asyncio.run(evaluate_all(args.test_set, args.model))


if __name__ == "__main__":
    main()
