"""Main orchestration for the protocol extraction pipeline."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from src.agent.llm_adapter import LLMAdapter
from src.extract.classifier import (
    DEFAULT_LABEL_PRIORITY,
    load_labels,
    load_or_classify_async,
    summarize_labels,
)
from src.extract.codegen import CodegenResult, generate_code
from src.extract.content_loader import get_node_pages, get_node_text
from src.extract.evidence_card import (
    generate_evidence_cards,
    load_review_decisions,
)
from src.extract.message_ir import lower_protocol_messages_to_message_ir
from src.extract.message_archetype import build_message_archetype_contributions
from src.extract.extractors import (
    BaseExtractor,
    ErrorExtractor,
    MessageExtractor,
    ProcedureExtractor,
    StateMachineExtractor,
    TimerExtractor,
)
from src.extract.merge import (
    ExtractionRecord,
    build_merge_report,
    is_empty_error,
    is_empty_message,
    is_empty_procedure,
    is_empty_state_machine,
    is_empty_timer,
    merge_messages,
    merge_messages_v2,
    merge_state_machines,
    merge_timers,
)
from src.extract.verify import verify_generated_code
from src.models import (
    ErrorRule,
    MessageIR,
    NodeSemanticLabel,
    ProcedureRule,
    ProtocolMessage,
    ProtocolSchema,
    ProtocolStateMachine,
    TimerConfig,
)
from src.tools.pathing import artifact_dir_for_doc, page_index_path_for_doc
from src.tools.registry import get_doc_config

logger = logging.getLogger("extract")


class PipelineStage(str, Enum):
    CLASSIFY = "classify"
    EXTRACT = "extract"
    MERGE = "merge"
    CODEGEN = "codegen"
    VERIFY = "verify"


@dataclass
class StageResult:
    stage: PipelineStage
    success: bool
    duration_sec: float
    node_count: int = 0
    error: str | None = None
    data: Any = None


def _detect_provider(model: str) -> str:
    env_provider = os.getenv("PROTOCOL_TWIN_LLM_PROVIDER", "").strip().lower()
    if env_provider in {"openai", "anthropic"}:
        return env_provider
    if model.lower().startswith("claude"):
        return "anthropic"
    return "openai"


def _resolve_model(model: str | None = None) -> str:
    if model:
        return model

    explicit_env_model = (
        os.getenv("PROTOCOL_TWIN_MODEL")
        or os.getenv("OPENAI_MODEL_NAME")
        or os.getenv("ANTHROPIC_MODEL_NAME")
    )
    if isinstance(explicit_env_model, str) and explicit_env_model.strip():
        return explicit_env_model.strip()

    config_path = Path("config.yaml")
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            configured = payload.get("model")
            if isinstance(configured, str) and configured.strip():
                return configured.strip()
        except yaml.YAMLError:
            pass

    provider = _detect_provider("")
    if provider == "anthropic":
        return "claude-sonnet-4-20250514"
    return "gpt-4o"


def _default_stage_sequence() -> list[PipelineStage]:
    return [
        PipelineStage.CLASSIFY,
        PipelineStage.EXTRACT,
        PipelineStage.MERGE,
        PipelineStage.CODEGEN,
        PipelineStage.VERIFY,
    ]


def _resolve_enable_hitl(enable_hitl: bool | None) -> bool:
    if enable_hitl is not None:
        return bool(enable_hitl)
    config_path = Path("config.yaml")
    if not config_path.exists():
        return False
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    value = payload.get("enable_hitl", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _needs_extraction_context(stages: list[PipelineStage]) -> bool:
    return any(stage in stages for stage in (PipelineStage.CLASSIFY, PipelineStage.EXTRACT, PipelineStage.MERGE))


def _resolve_page_index_path(doc_stem: str, chunks_dir: str | None = None) -> Path:
    preferred = page_index_path_for_doc(doc_stem, chunks_dir)
    if preferred.exists():
        return preferred
    legacy = Path("data/out") / f"{doc_stem}_page_index.json"
    return legacy


def _load_page_index(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid page_index payload at {path}")
    return payload


def _load_schema_from_artifact(doc_stem: str) -> ProtocolSchema:
    schema_path = artifact_dir_for_doc(doc_stem) / "protocol_schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Protocol schema not found: {schema_path}")
    return ProtocolSchema.model_validate_json(schema_path.read_text(encoding="utf-8"))


def _child_nodes(node: dict) -> list[dict]:
    children: list[dict] = []
    for key in ("children", "nodes", "structure"):
        value = node.get(key)
        if isinstance(value, list):
            children.extend(item for item in value if isinstance(item, dict))
    return children


def _collect_leaf_nodes(page_index: dict) -> list[dict]:
    roots = page_index.get("structure")
    if not isinstance(roots, list):
        roots = page_index.get("nodes")
    if not isinstance(roots, list):
        roots = []

    leaf_nodes: list[dict] = []
    stack = [node for node in roots if isinstance(node, dict)]
    while stack:
        node = stack.pop(0)
        children = _child_nodes(node)
        if children:
            stack = children + stack
            continue
        if node.get("is_skeleton") is True:
            continue
        leaf_nodes.append(node)
    return leaf_nodes


def _route_to_extractor(label: str, llm: LLMAdapter) -> BaseExtractor | None:
    if label == "state_machine":
        return StateMachineExtractor(llm)
    if label == "message_format":
        return MessageExtractor(llm)
    if label == "procedure_rule":
        return ProcedureExtractor(llm)
    if label == "timer_rule":
        return TimerExtractor(llm)
    if label == "error_handling":
        return ErrorExtractor(llm)
    return None


def _merge_to_schema(
    doc_stem: str,
    source_document: str,
    state_machines: list[ProtocolStateMachine],
    messages: list[ProtocolMessage],
    procedures: list[ProcedureRule],
    timers: list[TimerConfig],
    errors: list[ErrorRule],
) -> ProtocolSchema:
    return ProtocolSchema(
        protocol_name=doc_stem,
        state_machines=state_machines,
        messages=messages,
        procedures=procedures,
        timers=timers,
        errors=errors,
        source_document=source_document,
    )


def _load_cached_labels(doc_stem: str) -> dict[str, NodeSemanticLabel] | None:
    labels_path = artifact_dir_for_doc(doc_stem) / "node_labels.json"
    if labels_path.exists():
        return load_labels(str(labels_path))
    legacy_labels_path = Path("data/out") / f"{doc_stem}_node_labels.json"
    if legacy_labels_path.exists():
        return load_labels(str(legacy_labels_path))
    return None


def _make_stage_result(
    stage: PipelineStage,
    started_at: float,
    success: bool,
    node_count: int = 0,
    error: str | None = None,
    data: Any = None,
) -> StageResult:
    return StageResult(
        stage=stage,
        success=success,
        duration_sec=time.perf_counter() - started_at,
        node_count=node_count,
        error=error,
        data=data,
    )


def _artifact_path(doc_stem: str, suffix: str) -> Path:
    return artifact_dir_for_doc(doc_stem) / f"{suffix}.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_codegen_result(result: CodegenResult, generated_dir: Path) -> dict[str, Any]:
    return {
        "generated_dir": str(generated_dir),
        "files": list(result.files),
        "file_count": len(result.files),
        "skipped_components": list(result.skipped_components),
        "warnings": list(result.warnings),
        "expected_symbols": list(result.expected_symbols),
        "generated_msg_headers": list(result.generated_msg_headers),
        "generated_msgs": [message.model_dump() for message in result.generated_msgs],
        "generated_message_irs": [message_ir.model_dump() for message_ir in result.generated_message_irs],
    }


def _deserialize_generated_msgs(payload: list[dict] | None) -> list[ProtocolMessage] | None:
    if payload is None:
        return None
    return [ProtocolMessage(**item) for item in payload]


def _deserialize_generated_message_irs(payload: list[dict] | None) -> list[MessageIR] | None:
    if payload is None:
        return None
    return [MessageIR(**item) for item in payload]


async def run_pipeline(
    doc_name: str,
    stages: list[PipelineStage] | None = None,
    label_priority: list[str] | None = None,
    llm: LLMAdapter | None = None,
    model: str | None = None,
    page_index_path: str | None = None,
    enable_hitl: bool | None = None,
) -> list[StageResult]:
    active_stages = stages or _default_stage_sequence()
    hitl_enabled = _resolve_enable_hitl(enable_hitl)
    priority = label_priority or list(DEFAULT_LABEL_PRIORITY)
    results: list[StageResult] = []
    doc_stem = Path(doc_name).stem

    nodes: list[dict] = []
    labels: dict[str, NodeSemanticLabel] | None = None
    state_machines: list[ProtocolStateMachine] = []
    messages: list[ProtocolMessage] = []
    procedures: list[ProcedureRule] = []
    timers: list[TimerConfig] = []
    errors: list[ErrorRule] = []
    extraction_records: list[ExtractionRecord] = []
    schema: ProtocolSchema | None = None
    codegen_result: CodegenResult | None = None
    config: dict[str, Any] = {}

    if _needs_extraction_context(active_stages):
        config = get_doc_config(doc_name)
        if "error" in config:
            first_stage = active_stages[0] if active_stages else PipelineStage.CLASSIFY
            results.append(
                StageResult(
                    stage=first_stage,
                    success=False,
                    duration_sec=0.0,
                    error=str(config["error"]),
                )
            )
            return results

        resolved_page_index_path = (
            Path(page_index_path)
            if page_index_path
            else _resolve_page_index_path(doc_stem, str(config.get("chunks_dir", "")))
        )
        if not resolved_page_index_path.exists():
            first_stage = active_stages[0] if active_stages else PipelineStage.CLASSIFY
            results.append(
                StageResult(
                    stage=first_stage,
                    success=False,
                    duration_sec=0.0,
                    error=f"Page index not found: {resolved_page_index_path}",
                )
            )
            return results

        try:
            page_index = _load_page_index(resolved_page_index_path)
        except Exception as exc:
            first_stage = active_stages[0] if active_stages else PipelineStage.CLASSIFY
            results.append(
                StageResult(
                    stage=first_stage,
                    success=False,
                    duration_sec=0.0,
                    error=f"Failed to load page index: {exc}",
                )
            )
            return results
        nodes = _collect_leaf_nodes(page_index)

    needs_llm = any(stage in active_stages for stage in (PipelineStage.CLASSIFY, PipelineStage.EXTRACT))
    if needs_llm and llm is None:
        resolved_model = _resolve_model(model)
        llm = LLMAdapter(provider=_detect_provider(resolved_model), model=resolved_model)

    for stage in active_stages:
        started_at = time.perf_counter()

        if stage == PipelineStage.CLASSIFY:
            try:
                assert llm is not None
                labels = await load_or_classify_async(
                    doc_stem=doc_stem,
                    nodes=nodes,
                    content_dir=str(config["content_dir"]),
                    llm=llm,
                    label_priority=priority,
                )
                if nodes and not labels:
                    results.append(
                        _make_stage_result(
                            stage=stage,
                            started_at=started_at,
                            success=False,
                            node_count=len(nodes),
                            error="Classification produced no labels.",
                            data=summarize_labels(labels),
                        )
                    )
                    break
                stats = summarize_labels(labels)
                logger.info(
                    "Stage %s completed: %d nodes in %.2fs",
                    stage.value,
                    len(nodes),
                    time.perf_counter() - started_at,
                )
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=True,
                        node_count=len(nodes),
                        data=stats,
                    )
                )
            except Exception as exc:
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=False,
                        node_count=len(nodes),
                        error=str(exc),
                    )
                )
                break
            continue

        if stage == PipelineStage.EXTRACT:
            if labels is None:
                labels = _load_cached_labels(doc_stem)
                if labels is None:
                    results.append(
                        _make_stage_result(
                            stage=stage,
                            started_at=started_at,
                            success=False,
                            node_count=len(nodes),
                            error="Classification results unavailable; run CLASSIFY first.",
                        )
                    )
                    break

            assert llm is not None
            extractor_cache: dict[str, BaseExtractor] = {}
            success_count = 0
            failed_node_ids: list[str] = []
            skipped_node_ids: list[str] = []
            skipped_by_label: dict[str, int] = {}

            for node in nodes:
                node_id = str(node.get("node_id", ""))
                label = labels.get(node_id)
                if label is None:
                    failed_node_ids.append(node_id or "<missing-node-id>")
                    continue

                if label.label == "general_description":
                    skipped_node_ids.append(node_id)
                    skipped_by_label[label.label] = skipped_by_label.get(label.label, 0) + 1
                    continue

                text = get_node_text(node, str(config["content_dir"]))
                if not text:
                    failed_node_ids.append(node_id)
                    continue

                extractor = extractor_cache.get(label.label)
                if extractor is None:
                    extractor = _route_to_extractor(label.label, llm)
                    if extractor is None:
                        skipped_node_ids.append(node_id)
                        skipped_by_label[label.label] = skipped_by_label.get(label.label, 0) + 1
                        continue
                    extractor_cache[label.label] = extractor

                try:
                    result = await extractor.extract(
                        node_id=node_id,
                        text=text,
                        title=str(node.get("title", "")),
                        source_pages=get_node_pages(node),
                    )
                    if label.label == "state_machine":
                        state_machines.append(result)
                    elif label.label == "message_format":
                        messages.append(result)
                    elif label.label == "procedure_rule":
                        procedures.append(result)
                    elif label.label == "timer_rule":
                        timers.append(result)
                    elif label.label == "error_handling":
                        errors.append(result)
                    extraction_records.append(
                        ExtractionRecord(
                            node_id=node_id,
                            title=str(node.get("title", "")),
                            label=label.label,
                            confidence=label.confidence,
                            source_pages=get_node_pages(node),
                            payload=result.model_dump(),
                        )
                    )
                    success_count += 1
                except Exception as exc:
                    logger.warning("Node %s extraction failed: %s", node_id, exc)
                    failed_node_ids.append(node_id)

            extract_results_path = _artifact_path(doc_stem, "extract_results")
            _write_json(extract_results_path, [asdict(record) for record in extraction_records])
            stage_data = {
                "success_count": success_count,
                "failure_count": len(failed_node_ids),
                "failed_node_ids": failed_node_ids,
                "skipped_count": len(skipped_node_ids),
                "skipped_node_ids": skipped_node_ids,
                "skipped_by_label": skipped_by_label,
                "state_machine_count": len(state_machines),
                "message_count": len(messages),
                "procedure_count": len(procedures),
                "timer_count": len(timers),
                "error_count": len(errors),
                "extract_results_path": str(extract_results_path),
            }
            if success_count == 0 and failed_node_ids and not skipped_node_ids:
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=False,
                        node_count=len(nodes),
                        error="Extraction produced no structured results.",
                        data=stage_data,
                    )
                )
                break
            logger.info(
                "Stage %s completed: %d nodes in %.2fs",
                stage.value,
                len(nodes),
                time.perf_counter() - started_at,
            )
            results.append(
                _make_stage_result(
                    stage=stage,
                    started_at=started_at,
                    success=True,
                    node_count=len(nodes),
                    data=stage_data,
                )
            )
            continue

        if stage == PipelineStage.MERGE:
            if not any([state_machines, messages, procedures, timers, errors]):
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=False,
                        node_count=len(nodes),
                        error="Extraction results unavailable; run EXTRACT first.",
                    )
                )
                break

            pre_merge_counts = {
                "state_machine": len(state_machines),
                "message": len(messages),
                "procedure": len(procedures),
                "timer": len(timers),
                "error": len(errors),
            }
            filtered_state_machines = [item for item in state_machines if not is_empty_state_machine(item)]
            filtered_messages = [item for item in messages if not is_empty_message(item)]
            filtered_procedures = [item for item in procedures if not is_empty_procedure(item)]
            filtered_timers = [item for item in timers if not is_empty_timer(item)]
            filtered_errors = [item for item in errors if not is_empty_error(item)]

            dropped_empty_counts = {
                "state_machine": len(state_machines) - len(filtered_state_machines),
                "message": len(messages) - len(filtered_messages),
                "procedure": len(procedures) - len(filtered_procedures),
                "timer": len(timers) - len(filtered_timers),
                "error": len(errors) - len(filtered_errors),
            }
            post_filter_counts = {
                "state_machine": len(filtered_state_machines),
                "message": len(filtered_messages),
                "procedure": len(filtered_procedures),
                "timer": len(filtered_timers),
                "error": len(filtered_errors),
            }

            merge_warnings: list[str] = []
            review_decisions_path = _artifact_path(doc_stem, "review_decisions")
            review_decisions = load_review_decisions(review_decisions_path)
            try:
                merged_state_machines, state_machine_groups, sm_warnings, sm_near_miss = merge_state_machines(
                    filtered_state_machines,
                    review_decisions=review_decisions,
                )
                merge_warnings.extend(sm_warnings)
            except Exception as exc:
                logger.warning("State-machine merge failed, falling back to filtered list: %s", exc)
                merged_state_machines = filtered_state_machines
                state_machine_groups = []
                sm_near_miss = []
                merge_warnings.append(f"state_machine merge fallback: {exc}")

            merged_timers, timer_groups = merge_timers(filtered_timers)
            try:
                merged_messages, message_groups, msg_near_miss = merge_messages_v2(
                    filtered_messages,
                    enable_fuzzy_match=True,
                    review_decisions=review_decisions,
                )
            except Exception as exc:
                logger.warning("Enhanced message merge failed, falling back to exact merge: %s", exc)
                merge_warnings.append(f"message merge fallback: {exc}")
                try:
                    merged_messages, message_groups = merge_messages(filtered_messages)
                    msg_near_miss = []
                except Exception as inner_exc:
                    logger.warning("Exact message merge failed, preserving filtered messages: %s", inner_exc)
                    merge_warnings.append(f"message exact merge fallback: {inner_exc}")
                    merged_messages = filtered_messages
                    message_groups = []
                    msg_near_miss = []
            post_merge_counts = {
                "state_machine": len(merged_state_machines),
                "message": len(merged_messages),
                "procedure": len(filtered_procedures),
                "timer": len(merged_timers),
                "error": len(filtered_errors),
            }
            near_miss_report = {
                "doc_name": doc_stem,
                "state_machine_near_misses": sm_near_miss,
                "message_near_misses": msg_near_miss,
                "summary": {
                    "sm_count": len(sm_near_miss),
                    "msg_count": len(msg_near_miss),
                },
            }
            near_miss_report_path = _artifact_path(doc_stem, "near_miss_report")
            _write_json(near_miss_report_path, near_miss_report)

            schema = _merge_to_schema(
                doc_stem=doc_stem,
                source_document=doc_name,
                state_machines=merged_state_machines,
                messages=merged_messages,
                procedures=filtered_procedures,
                timers=merged_timers,
                errors=filtered_errors,
            )
            message_archetypes = build_message_archetype_contributions(
                protocol_name=doc_stem,
                messages=merged_messages,
                extraction_records=extraction_records,
            )
            message_irs = lower_protocol_messages_to_message_ir(
                protocol_name=doc_stem,
                messages=merged_messages,
                extraction_records=extraction_records,
            )
            schema.message_irs = message_irs
            schema_path = _artifact_path(doc_stem, "protocol_schema")
            message_archetype_path = _artifact_path(doc_stem, "message_archetypes")
            message_ir_path = _artifact_path(doc_stem, "message_ir")
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            schema_path.write_text(schema.model_dump_json(indent=2), encoding="utf-8")
            _write_json(message_archetype_path, [item.model_dump() for item in message_archetypes])
            _write_json(message_ir_path, [item.model_dump() for item in message_irs])
            merge_report = build_merge_report(
                pre=pre_merge_counts,
                dropped=dropped_empty_counts,
                post_filter=post_filter_counts,
                post=post_merge_counts,
                timer_groups=timer_groups,
                message_groups=message_groups,
                state_machine_groups=state_machine_groups,
                near_miss_summary=near_miss_report["summary"],
            )
            merge_report_path = _artifact_path(doc_stem, "merge_report")
            _write_json(merge_report_path, merge_report)
            stage_data = {
                "schema_path": str(schema_path),
                "message_archetype_path": str(message_archetype_path),
                "message_ir_path": str(message_ir_path),
                "merge_report_path": str(merge_report_path),
                "near_miss_report_path": str(near_miss_report_path),
                "state_machine_count": len(schema.state_machines),
                "message_count": len(schema.messages),
                "message_archetype_count": len(message_archetypes),
                "message_ir_count": len(message_irs),
                "ready_message_ir_count": sum(item.normalization_status == "ready" for item in message_irs),
                "degraded_ready_message_ir_count": sum(item.normalization_status == "degraded_ready" for item in message_irs),
                "procedure_count": len(schema.procedures),
                "timer_count": len(schema.timers),
                "error_count": len(schema.errors),
                "warnings": merge_warnings,
                "pending_review": False,
            }

            has_near_miss = near_miss_report["summary"]["sm_count"] + near_miss_report["summary"]["msg_count"] > 0
            if hitl_enabled and has_near_miss:
                if llm is None:
                    resolved_model = _resolve_model(model)
                    llm = LLMAdapter(provider=_detect_provider(resolved_model), model=resolved_model)
                content_dir = str(config.get("content_dir", ""))
                cards = await generate_evidence_cards(llm, near_miss_report, content_dir=content_dir)
                review_cards_path = _artifact_path(doc_stem, "review_cards")
                _write_json(review_cards_path, [card.model_dump() for card in cards])
                stage_data["pending_review"] = True
                stage_data["review_cards_path"] = str(review_cards_path)
                stage_data["review_decisions_path"] = str(review_decisions_path)
            results.append(
                _make_stage_result(
                    stage=stage,
                    started_at=started_at,
                    success=True,
                    node_count=len(nodes),
                    data=stage_data,
                )
            )
            if stage_data["pending_review"]:
                break
            continue

        if stage == PipelineStage.CODEGEN:
            try:
                if schema is None:
                    schema = _load_schema_from_artifact(doc_stem)
                generated_dir = artifact_dir_for_doc(doc_stem) / "generated"
                codegen_result = generate_code(schema, str(generated_dir))
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=True,
                        node_count=len(nodes),
                        data=_serialize_codegen_result(codegen_result, generated_dir),
                    )
                )
            except Exception as exc:
                logger.error("Stage CODEGEN failed: %s", exc)
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=False,
                        node_count=len(nodes),
                        error=str(exc),
                    )
                )
                break
            continue

        if stage == PipelineStage.VERIFY:
            try:
                if schema is None:
                    schema = _load_schema_from_artifact(doc_stem)
                generated_dir = artifact_dir_for_doc(doc_stem) / "generated"
                expected_symbols: list[dict] | None = None
                generated_msg_headers: list[str] | None = None
                generated_msgs: list[ProtocolMessage] | None = None
                generated_message_irs: list[MessageIR] | None = None

                if codegen_result is not None:
                    expected_symbols = codegen_result.expected_symbols
                    generated_msg_headers = codegen_result.generated_msg_headers
                    generated_msgs = codegen_result.generated_msgs
                    generated_message_irs = codegen_result.generated_message_irs
                else:
                    prior_codegen = next(
                        (
                            result.data
                            for result in reversed(results)
                            if result.stage == PipelineStage.CODEGEN and isinstance(result.data, dict)
                        ),
                        None,
                    )
                    if prior_codegen is not None:
                        expected_symbols = prior_codegen.get("expected_symbols")
                        generated_msg_headers = prior_codegen.get("generated_msg_headers")
                        generated_msgs = _deserialize_generated_msgs(prior_codegen.get("generated_msgs"))
                        generated_message_irs = _deserialize_generated_message_irs(prior_codegen.get("generated_message_irs"))

                report = verify_generated_code(
                    str(generated_dir),
                    schema,
                    doc_name,
                    expected_symbols=expected_symbols,
                    generated_msg_headers=generated_msg_headers,
                    generated_msgs=generated_msgs,
                    generated_message_irs=generated_message_irs,
                )
                verify_report_path = _artifact_path(doc_stem, "verify_report")
                _write_json(verify_report_path, report.to_dict())
                data = report.to_dict()
                data["verify_report_path"] = str(verify_report_path)
                data["generated_dir"] = str(generated_dir)
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=True,
                        node_count=len(nodes),
                        data=data,
                    )
                )
            except Exception as exc:
                logger.error("Stage VERIFY failed: %s", exc)
                results.append(
                    _make_stage_result(
                        stage=stage,
                        started_at=started_at,
                        success=False,
                        node_count=len(nodes),
                        error=str(exc),
                    )
                )
                break
            continue

    if results:
        last_data = results[-1].data if isinstance(results[-1].data, dict) else {}
        fail_count = last_data.get("failure_count", 0)
        failed_ids = last_data.get("failed_node_ids", [])
        logger.info(
            "Pipeline summary: %d stages, %d failed nodes. Failed nodes: %s",
            len(results),
            fail_count,
            failed_ids,
        )
    return results
