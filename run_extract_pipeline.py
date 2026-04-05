"""CLI runner for protocol extraction / MessageIR pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from src.extract.pipeline import PipelineStage, run_pipeline
from src.ingest.pipeline import ProcessResult, process_document
from src.tools.pathing import artifact_dir_for_doc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run extraction pipeline for a protocol PDF and summarize MessageIR/codegen results.",
    )
    parser.add_argument(
        "--pdf",
        default=None,
        help="PDF path. If provided, the script can ingest it before extraction.",
    )
    parser.add_argument(
        "--doc",
        default=None,
        help="Document name for an already-processed document, for example rfc5880-BFD.pdf.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name. Falls back to .env / config.yaml / pipeline defaults.",
    )
    parser.add_argument(
        "--stages",
        default="process,classify,extract,merge",
        help=(
            "Comma-separated stages. Supported tokens: process,classify,extract,merge,codegen,verify,all. "
            "Default runs through merge."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force reprocessing PDF artifacts before running pipeline.",
    )
    parser.add_argument(
        "--enable-hitl",
        action="store_true",
        default=False,
        help="Enable HITL evidence-card generation during merge if near-miss cases exist.",
    )
    parser.add_argument(
        "--show-message-irs",
        action="store_true",
        default=False,
        help="Print per-MessageIR READY/BLOCKED details after merge.",
    )
    return parser


def _parse_stage_tokens(raw: str) -> tuple[bool, list[PipelineStage]]:
    tokens = [token.strip().lower() for token in raw.split(",") if token.strip()]
    if not tokens:
        tokens = ["process", "classify", "extract", "merge"]

    if "all" in tokens:
        tokens = ["process", "classify", "extract", "merge", "codegen", "verify"]

    run_process = "process" in tokens
    stages: list[PipelineStage] = []
    seen: set[PipelineStage] = set()
    mapping = {
        "classify": PipelineStage.CLASSIFY,
        "extract": PipelineStage.EXTRACT,
        "merge": PipelineStage.MERGE,
        "codegen": PipelineStage.CODEGEN,
        "verify": PipelineStage.VERIFY,
    }
    invalid = [token for token in tokens if token not in {"process", *mapping.keys()}]
    if invalid:
        raise ValueError(f"Unsupported stage tokens: {', '.join(invalid)}")
    for token in tokens:
        stage = mapping.get(token)
        if stage is None or stage in seen:
            continue
        stages.append(stage)
        seen.add(stage)
    return run_process, stages


def _print_process_summary(result: ProcessResult) -> None:
    print("=" * 72)
    print("Ingest Summary")
    print("=" * 72)
    print(f"doc_name:        {result.doc_name}")
    print(f"pdf_path:        {result.pdf_path}")
    print(f"page_index_json: {result.page_index_json}")
    print(f"chunks_dir:      {result.chunks_dir}")
    print(f"content_dir:     {result.content_dir}")
    print(f"total_pages:     {result.total_pages}")
    print(
        "rebuilt:         "
        f"index={result.index_built} structure={result.structure_built} content={result.content_built} registered={result.registered}"
    )


def _print_stage_results(results: list) -> None:
    print("=" * 72)
    print("Pipeline Stages")
    print("=" * 72)
    for item in results:
        print(
            f"{item.stage.value:<8} success={str(item.success):<5} "
            f"duration={item.duration_sec:.2f}s nodes={item.node_count}"
        )
        if item.error:
            print(f"  error: {item.error}")
        if isinstance(item.data, dict):
            interesting_keys = [
                "success_count",
                "failure_count",
                "empty_fsm_return_count",
                "message_count",
                "message_ir_count",
                "ready_message_ir_count",
                "state_machine_count",
                "state_machine_sanity_downgrade_count",
                "state_machine_sanity_downgrade_by_reason",
                "fsm_segment_count",
                "fsm_segment_reclassified_count",
                "fsm_segment_updated_node_count",
                "fsm_segment_skipped_count",
                "fsm_segment_skip_reasons",
                "state_machine_context_augmented_count",
                "llm_refine_triggered_count",
                "raw_branch_ratio_before",
                "raw_branch_ratio_after",
                "typed_action_count",
                "generated_action_count",
                "degraded_action_count",
                "action_codegen_ratio",
                "warnings",
                "syntax_ok",
                "test_results",
                "verify_report_path",
                "schema_path",
                "message_ir_path",
            ]
            for key in interesting_keys:
                if key in item.data:
                    print(f"  {key}: {item.data[key]}")


def _message_ir_payloads(doc_name: str) -> list[dict]:
    doc_stem = Path(doc_name).stem
    path = artifact_dir_for_doc(doc_stem) / "message_ir.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def _print_message_ir_summary(doc_name: str, verbose: bool = False) -> None:
    payload = _message_ir_payloads(doc_name)
    if not payload:
        print("No message_ir.json artifact found.")
        return

    ready = [item for item in payload if item.get("normalization_status") == "ready"]
    blocked = [item for item in payload if item.get("normalization_status") == "blocked"]
    draft = [item for item in payload if item.get("normalization_status") == "draft"]

    print("=" * 72)
    print("MessageIR Summary")
    print("=" * 72)
    print(f"total={len(payload)} ready={len(ready)} blocked={len(blocked)} draft={len(draft)}")
    for item in payload:
        diagnostics = item.get("diagnostics", [])
        codes = [diag.get("code", "") for diag in diagnostics]
        print(
            f"- {item.get('canonical_name')}: status={item.get('normalization_status')} "
            f"layout={item.get('layout_kind')} diagnostics={codes}"
        )
        if verbose:
            print(f"  display_name: {item.get('display_name')}")
            print(f"  field_order:  {item.get('normalized_field_order')}")
            for diag in diagnostics:
                print(f"  diag: {diag.get('level')} {diag.get('code')} - {diag.get('message')}")


async def _run(args: argparse.Namespace) -> int:
    run_process, stages = _parse_stage_tokens(args.stages)
    if not args.pdf and not args.doc:
        raise ValueError("Provide either --pdf or --doc.")
    if run_process and not args.pdf:
        raise ValueError("The 'process' stage requires --pdf.")
    if not run_process and not stages:
        raise ValueError("No pipeline stages selected.")

    doc_name = args.doc
    if run_process:
        process_result = await asyncio.to_thread(
            process_document,
            pdf_path=args.pdf,
            force=args.force,
            model=args.model,
        )
        _print_process_summary(process_result)
        doc_name = process_result.doc_name
    elif args.pdf and not doc_name:
        doc_name = Path(args.pdf).name

    assert doc_name is not None
    if stages:
        results = await run_pipeline(
            doc_name=doc_name,
            stages=stages,
            model=args.model,
            enable_hitl=args.enable_hitl,
        )
        _print_stage_results(results)

    if PipelineStage.MERGE in stages or args.show_message_irs:
        _print_message_ir_summary(doc_name, verbose=args.show_message_irs)
    return 0


def main() -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
