"""Tests for protocol extraction pipeline orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.models import LLMResponse, NodeSemanticLabel, ProtocolMessage, ProtocolSchema, ProtocolStateMachine
from src.extract import pipeline as pipeline_module
from src.extract.pipeline import (
    PipelineStage,
    _collect_leaf_nodes,
    _merge_to_schema,
    _route_to_extractor,
    run_pipeline,
)


class DummyLLM:
    provider = "openai"
    model = "mock-model"

    async def chat_with_tools(self, messages, tools):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "You classify communication-protocol document nodes." in system:
            if "Overview" in user:
                payload = {"label": "general_description", "confidence": 0.9, "rationale": "overview"}
            elif "Packet Format" in user:
                payload = {"label": "message_format", "confidence": 0.9, "rationale": "format"}
            else:
                payload = {"label": "state_machine", "confidence": 0.9, "rationale": "state"}
            return LLMResponse(text=json.dumps(payload))
        if "Extract a protocol state machine" in system:
            return LLMResponse(
                text=json.dumps(
                    {
                        "name": "BFD Session",
                        "states": [{"name": "Down", "is_initial": True}],
                        "transitions": [],
                    }
                )
            )
        if "Extract a protocol message or frame definition" in system:
            return LLMResponse(
                text=json.dumps(
                    {
                        "name": "BFD Control Packet",
                        "fields": [{"name": "Version", "size_bits": 3}],
                    }
                )
            )
        if "Extract a procedure rule" in system:
            return LLMResponse(text=json.dumps({"name": "Procedure", "steps": []}))
        if "Extract a timer configuration" in system:
            return LLMResponse(text=json.dumps({"timer_name": "Timer"}))
        if "Extract an error-handling rule" in system:
            return LLMResponse(text=json.dumps({"error_condition": "Error", "handling_action": ""}))
        raise AssertionError(f"Unexpected prompt: {system}")


def _write_page_index(tmp_path: Path, doc_stem: str, payload: dict) -> Path:
    out_dir = tmp_path / "data" / "out" / "chunk" / doc_stem
    out_dir.mkdir(parents=True, exist_ok=True)
    page_index_path = out_dir / "page_index.json"
    page_index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return page_index_path


def _write_content_db(tmp_path: Path, doc_stem: str, text_by_page: dict[int, str]) -> str:
    content_dir = tmp_path / "data" / "out" / "content" / doc_stem / "json"
    content_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "pages": [
            {"page_num": page, "text": text, "tables": [], "images": []}
            for page, text in sorted(text_by_page.items())
        ]
    }
    (content_dir / "content_1_20.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(content_dir)


# Feature: protocol-extraction-pipeline, Property 8: 提取器路由正确性
@given(
    label=st.sampled_from(
        [
            "state_machine",
            "message_format",
            "procedure_rule",
            "timer_rule",
            "error_handling",
            "general_description",
        ]
    )
)
@settings(max_examples=100)
def test_route_to_extractor_matches_label(label: str):
    routed = _route_to_extractor(label, DummyLLM())
    mapping = {
        "state_machine": "StateMachineExtractor",
        "message_format": "MessageExtractor",
        "procedure_rule": "ProcedureExtractor",
        "timer_rule": "TimerExtractor",
        "error_handling": "ErrorExtractor",
        "general_description": None,
    }
    expected = mapping[label]
    assert (type(routed).__name__ if routed is not None else None) == expected


def test_collect_leaf_nodes_supports_children_and_nodes():
    payload = {
        "structure": [
            {
                "node_id": "root-1",
                "is_skeleton": True,
                "children": [
                    {"node_id": "leaf-1", "is_skeleton": False, "title": "Leaf 1"},
                ],
            },
            {
                "node_id": "root-2",
                "nodes": [
                    {"node_id": "leaf-2", "title": "Leaf 2"},
                ],
            },
        ]
    }
    leaves = _collect_leaf_nodes(payload)
    assert [node["node_id"] for node in leaves] == ["leaf-1", "leaf-2"]


# Feature: protocol-extraction-pipeline, Property 9: Schema 合并完整性
@given(
    state_count=st.integers(min_value=0, max_value=3),
    message_count=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=100)
def test_merge_to_schema_preserves_components(state_count: int, message_count: int):
    state_machines = [ProtocolStateMachine(name=f"sm-{idx}") for idx in range(state_count)]
    messages = [ProtocolMessage(name=f"msg-{idx}") for idx in range(message_count)]
    schema = _merge_to_schema(
        doc_stem="rfc5880-BFD",
        source_document="rfc5880-BFD.pdf",
        state_machines=state_machines,
        messages=messages,
        procedures=[],
        timers=[],
        errors=[],
    )
    assert schema.protocol_name == "rfc5880-BFD"
    assert schema.source_document == "rfc5880-BFD.pdf"
    assert schema.state_machines == state_machines
    assert schema.messages == messages


@pytest.mark.asyncio
async def test_run_pipeline_respects_stage_subset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(
        tmp_path,
        doc_stem,
        {
            "doc_name": doc_name,
            "structure": [
                {
                    "node_id": "n1",
                    "title": "Overview",
                    "text": "Background text",
                    "start_index": 1,
                    "end_index": 1,
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
        },
    )
    content_dir = _write_content_db(tmp_path, doc_stem, {1: "Background text"})
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 1,
        },
    )

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.CLASSIFY],
        llm=DummyLLM(),
    )

    assert [result.stage for result in results] == [PipelineStage.CLASSIFY]
    assert results[0].success is True
    assert not (tmp_path / "data" / "out" / doc_stem / "protocol_schema.json").exists()


# Feature: protocol-extraction-pipeline, Property 13: Pipeline 阶段控制
@pytest.mark.asyncio
async def test_run_pipeline_classify_extract_merge_end_to_end(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(
        tmp_path,
        doc_stem,
        {
            "doc_name": doc_name,
            "structure": [
                {
                    "node_id": "sm-1",
                    "title": "State Machine",
                    "text": "State transition text",
                    "start_index": 1,
                    "end_index": 1,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "msg-1",
                    "title": "Packet Format",
                    "text": "Field table text",
                    "start_index": 2,
                    "end_index": 2,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "gen-1",
                    "title": "Overview",
                    "text": "General background",
                    "start_index": 3,
                    "end_index": 3,
                    "start_line": 1,
                    "end_line": 1,
                },
            ],
        },
    )
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "State transition text",
            2: "Field table text",
            3: "General background",
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 3,
        },
    )

    results = await run_pipeline(doc_name=doc_name, llm=DummyLLM())
    extract_result = results[1]
    merge_result = results[2]
    codegen_result = results[3]
    verify_result = results[4]
    extract_results_path = Path(extract_result.data["extract_results_path"])
    schema_path = Path(merge_result.data["schema_path"])
    merge_report_path = Path(merge_result.data["merge_report_path"])
    near_miss_report_path = Path(merge_result.data["near_miss_report_path"])
    verify_report_path = Path(verify_result.data["verify_report_path"])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    merge_report = json.loads(merge_report_path.read_text(encoding="utf-8"))
    near_miss_report = json.loads(near_miss_report_path.read_text(encoding="utf-8"))
    verify_report = json.loads(verify_report_path.read_text(encoding="utf-8"))

    assert [result.stage for result in results] == [
        PipelineStage.CLASSIFY,
        PipelineStage.EXTRACT,
        PipelineStage.MERGE,
        PipelineStage.CODEGEN,
        PipelineStage.VERIFY,
    ]
    assert merge_result.success is True
    assert codegen_result.success is True
    assert verify_result.success is True
    assert extract_results_path.exists()
    assert merge_report_path.exists()
    assert near_miss_report_path.exists()
    assert verify_report_path.exists()
    assert schema["protocol_name"] == doc_stem
    assert len(schema["state_machines"]) == 1
    assert len(schema["messages"]) == 1
    assert schema["state_machines"][0]["source_pages"] == [1]
    assert schema["messages"][0]["source_pages"] == [2]
    assert extract_result.data["skipped_by_label"]["general_description"] == 1
    assert merge_report["post_merge_counts"]["message"] == 1
    assert merge_report["post_merge_counts"]["timer"] == 0
    assert merge_report["near_miss_summary"] == {"sm_count": 0, "msg_count": 0}
    assert near_miss_report["summary"] == {"sm_count": 0, "msg_count": 0}
    assert codegen_result.data["file_count"] >= 3
    assert all(isinstance(item, dict) for item in codegen_result.data["generated_msgs"])
    assert "syntax_checked" in verify_report


# Feature: protocol-extraction-pipeline, Property 14: 故障隔离
@pytest.mark.asyncio
async def test_run_pipeline_isolates_failed_nodes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(
        tmp_path,
        doc_stem,
        {
            "doc_name": doc_name,
            "structure": [
                {
                    "node_id": "ok-node",
                    "title": "State Machine",
                    "text": "ok text",
                    "start_index": 1,
                    "end_index": 1,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "bad-node",
                    "title": "Packet Format",
                    "text": "bad text",
                    "start_index": 2,
                    "end_index": 2,
                    "start_line": 1,
                    "end_line": 1,
                },
            ],
        },
    )
    content_dir = _write_content_db(tmp_path, doc_stem, {1: "ok text", 2: "bad text"})
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 2,
        },
    )

    async def fake_load_or_classify_async(**kwargs):
        return {
            "ok-node": NodeSemanticLabel(
                node_id="ok-node",
                label="state_machine",
                confidence=1.0,
                rationale="ok",
            ),
            "bad-node": NodeSemanticLabel(
                node_id="bad-node",
                label="message_format",
                confidence=1.0,
                rationale="bad",
            ),
        }

    monkeypatch.setattr(
        "src.extract.pipeline.load_or_classify_async",
        fake_load_or_classify_async,
    )
    original_get_node_text = pipeline_module.get_node_text

    def fake_get_node_text(node, content_dir_arg):
        if node["node_id"] == "bad-node":
            return None
        return original_get_node_text(node, content_dir_arg)

    monkeypatch.setattr("src.extract.pipeline.get_node_text", fake_get_node_text)

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.CLASSIFY, PipelineStage.EXTRACT],
        llm=DummyLLM(),
    )

    extract_data = results[-1].data
    assert results[-1].success is True
    assert extract_data["success_count"] == 1
    assert extract_data["failure_count"] == 1
    assert extract_data["failed_node_ids"] == ["bad-node"]


class MergeAwareLLM(DummyLLM):
    async def chat_with_tools(self, messages, tools):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "You classify communication-protocol document nodes." in system:
            if "Timer" in user:
                return LLMResponse(
                    text=json.dumps(
                        {"label": "timer_rule", "confidence": 0.9, "rationale": "timer"}
                    )
                )
            if "Empty Packet Format" in user:
                return LLMResponse(
                    text=json.dumps(
                        {"label": "message_format", "confidence": 0.9, "rationale": "message"}
                    )
                )
            return await super().chat_with_tools(messages, tools)
        if "Extract a timer configuration" in system:
            if "Timer A" in user:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "timer_name": "Detection Time",
                            "timeout_value": "3 x interval",
                            "trigger_action": "declare down",
                            "description": "short timer description",
                        }
                    )
                )
            if "Timer B" in user:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "timer_name": "6.8.4 Detection Time",
                            "timeout_value": "Detect Mult * negotiated receive interval",
                            "trigger_action": "declare the session down immediately",
                            "description": "longer detection time description",
                        }
                    )
                )
        if "Extract a protocol message or frame definition" in system and "Empty Packet Format" in user:
            return LLMResponse(text=json.dumps({"name": "Empty Packet Format", "fields": []}))
        return await super().chat_with_tools(messages, tools)


class MergePhase2LLM(DummyLLM):
    async def chat_with_tools(self, messages, tools):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "You classify communication-protocol document nodes." in system:
            if "Session" in user:
                return LLMResponse(
                    text=json.dumps(
                        {"label": "state_machine", "confidence": 0.9, "rationale": "state"}
                    )
                )
            if "Control Packet" in user:
                return LLMResponse(
                    text=json.dumps(
                        {"label": "message_format", "confidence": 0.9, "rationale": "message"}
                    )
                )
        if "Extract a protocol state machine" in system:
            if "Overview" in user:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "name": "BFD Session State Machine (RFC 5880 §6.1 Overview)",
                            "states": [
                                {"name": "Down", "is_initial": True},
                                {"name": "Init"},
                                {"name": "Up", "is_final": True},
                            ],
                            "transitions": [
                                {
                                    "from_state": "Down",
                                    "to_state": "Init",
                                    "event": "Receive Control Packet",
                                },
                                {
                                    "from_state": "Init",
                                    "to_state": "Up",
                                    "event": "Receive Control Packet",
                                },
                            ],
                        }
                    )
                )
            return LLMResponse(
                text=json.dumps(
                    {
                        "name": "BFD Session State Machine",
                        "states": [
                            {"name": "Down", "is_initial": True},
                            {"name": "Init"},
                            {"name": "Up", "is_final": True},
                        ],
                        "transitions": [
                            {
                                "from_state": "Down",
                                "to_state": "Init",
                                "event": "Receive BFD Control packet",
                            },
                            {
                                "from_state": "Init",
                                "to_state": "Up",
                                "event": "Receive BFD Control packet",
                            },
                        ],
                    }
                )
            )
        if "Extract a protocol message or frame definition" in system:
            if "Generic" in user:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "name": "Generic BFD Control Packet Format",
                            "fields": [
                                {"name": "Version", "size_bits": 3},
                                {"name": "Length", "size_bits": 8},
                            ],
                        }
                    )
                )
            return LLMResponse(
                text=json.dumps(
                    {
                        "name": "BFD Control Packet",
                        "fields": [
                            {"name": "Version", "size_bits": 3},
                            {"name": "Length", "size_bits": 8},
                            {"name": "Detect Mult", "size_bits": 8},
                        ],
                    }
                )
            )
        return await super().chat_with_tools(messages, tools)


@pytest.mark.asyncio
async def test_run_pipeline_merge_filters_empty_messages_and_merges_timers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(
        tmp_path,
        doc_stem,
        {
            "doc_name": doc_name,
            "structure": [
                {
                    "node_id": "timer-1",
                    "title": "Timer A",
                    "text": "Detection time formula A",
                    "start_index": 4,
                    "end_index": 4,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "timer-2",
                    "title": "Timer B",
                    "text": "Detection time formula B",
                    "start_index": 5,
                    "end_index": 5,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "msg-empty",
                    "title": "Empty Packet Format",
                    "text": "format with no parsed fields",
                    "start_index": 6,
                    "end_index": 6,
                    "start_line": 1,
                    "end_line": 1,
                },
            ],
        },
    )
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            4: "Detection time formula A",
            5: "Detection time formula B",
            6: "format with no parsed fields",
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 6,
        },
    )

    results = await run_pipeline(doc_name=doc_name, llm=MergeAwareLLM())

    extract_path = Path(results[1].data["extract_results_path"])
    merge_report_path = Path(results[2].data["merge_report_path"])
    near_miss_report_path = Path(results[2].data["near_miss_report_path"])
    schema_path = Path(results[2].data["schema_path"])
    extract_payload = json.loads(extract_path.read_text(encoding="utf-8"))
    merge_report = json.loads(merge_report_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert extract_path.exists()
    assert merge_report_path.exists()
    assert near_miss_report_path.exists()
    assert len(extract_payload) == 3
    assert merge_report["dropped_empty_counts"]["message"] == 1
    assert merge_report["pre_merge_counts"]["timer"] == 2
    assert merge_report["post_merge_counts"]["timer"] == 1
    assert merge_report["merged_groups"]["timer"][0]["source_pages_union"] == [4, 5]
    assert results[2].data["timer_count"] == 1
    assert results[2].data["message_count"] == 0
    assert schema["messages"] == []
    assert len(schema["timers"]) == 1
    assert schema["timers"][0]["source_pages"] == [4, 5]


@pytest.mark.asyncio
async def test_run_pipeline_merge_phase2_merges_state_machines_and_fuzzy_messages(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(
        tmp_path,
        doc_stem,
        {
            "doc_name": doc_name,
            "structure": [
                {
                    "node_id": "sm-1",
                    "title": "Session State Machine Overview",
                    "text": "session overview",
                    "start_index": 1,
                    "end_index": 1,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "sm-2",
                    "title": "Session State Machine",
                    "text": "session detailed state machine",
                    "start_index": 2,
                    "end_index": 2,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "msg-1",
                    "title": "Generic Control Packet",
                    "text": "generic control packet format",
                    "start_index": 3,
                    "end_index": 3,
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "node_id": "msg-2",
                    "title": "Control Packet",
                    "text": "control packet",
                    "start_index": 4,
                    "end_index": 4,
                    "start_line": 1,
                    "end_line": 1,
                },
            ],
        },
    )
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "session overview",
            2: "session detailed state machine",
            3: "generic control packet format",
            4: "control packet",
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 4,
        },
    )

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.CLASSIFY, PipelineStage.EXTRACT, PipelineStage.MERGE],
        llm=MergePhase2LLM(),
    )

    merge_report = json.loads(Path(results[2].data["merge_report_path"]).read_text(encoding="utf-8"))
    near_miss_report = json.loads(Path(results[2].data["near_miss_report_path"]).read_text(encoding="utf-8"))
    schema = json.loads(Path(results[2].data["schema_path"]).read_text(encoding="utf-8"))

    assert results[2].success is True
    assert results[2].data["state_machine_count"] == 1
    assert results[2].data["message_count"] == 1
    assert merge_report["post_merge_counts"]["state_machine"] == 1
    assert merge_report["post_merge_counts"]["message"] == 1
    assert merge_report["merged_groups"]["state_machine"][0]["canonical_name"] == "BFD Session State Machine"
    assert schema["messages"][0]["name"] == "BFD Control Packet"
    assert near_miss_report["summary"] == {"sm_count": 0, "msg_count": 0}


@pytest.mark.asyncio
async def test_run_pipeline_supports_standalone_codegen_and_verify(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    artifact_dir = tmp_path / "data" / "out" / doc_stem
    artifact_dir.mkdir(parents=True, exist_ok=True)
    schema = ProtocolSchema(
        protocol_name=doc_stem,
        source_document=doc_name,
        state_machines=[ProtocolStateMachine(name="BFD Session")],
        messages=[ProtocolMessage(name="BFD Control Packet", fields=[{"name": "Version", "size_bits": 3}])],
    )
    (artifact_dir / "protocol_schema.json").write_text(
        schema.model_dump_json(indent=2),
        encoding="utf-8",
    )

    def _unexpected_config(_name):
        raise AssertionError("get_doc_config should not be called for standalone CODEGEN/VERIFY")

    monkeypatch.setattr("src.extract.pipeline.get_doc_config", _unexpected_config)

    codegen_results = await run_pipeline(doc_name=doc_name, stages=[PipelineStage.CODEGEN])
    verify_results = await run_pipeline(doc_name=doc_name, stages=[PipelineStage.VERIFY])

    assert [result.stage for result in codegen_results] == [PipelineStage.CODEGEN]
    assert codegen_results[0].success is True
    assert Path(codegen_results[0].data["generated_dir"]).exists()
    assert [result.stage for result in verify_results] == [PipelineStage.VERIFY]
    assert verify_results[0].success is True
    assert Path(verify_results[0].data["verify_report_path"]).exists()


@pytest.mark.asyncio
async def test_run_pipeline_stops_after_codegen_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    artifact_dir = tmp_path / "data" / "out" / doc_stem
    artifact_dir.mkdir(parents=True, exist_ok=True)
    schema = ProtocolSchema(protocol_name=doc_stem, source_document=doc_name)
    (artifact_dir / "protocol_schema.json").write_text(
        schema.model_dump_json(indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "src.extract.pipeline.generate_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.CODEGEN, PipelineStage.VERIFY],
    )

    assert [result.stage for result in results] == [PipelineStage.CODEGEN]
    assert results[0].success is False
    assert results[0].error == "boom"


class HitlNearMissLLM(DummyLLM):
    evidence_call_count = 0

    async def chat_with_tools(self, messages, tools):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "protocol evidence organizer" in system.lower():
            self.evidence_call_count += 1
            return LLMResponse(
                text=json.dumps(
                    {
                        "common_evidence": ["both are authentication-related sections"],
                        "differing_evidence": ["simple password vs keyed md5"],
                        "naming_relation": "partial overlap",
                        "wording_vs_substance": "substantive difference exists",
                        "llm_confidence": 0.55,
                        "unresolved_conflicts": ["decision requires reviewer"],
                    }
                )
            )
        if "You classify communication-protocol document nodes." in system:
            if "Simple Password" in user or "Keyed MD5" in user:
                return LLMResponse(
                    text=json.dumps({"label": "message_format", "confidence": 0.9, "rationale": "message"})
                )
        if "Extract a protocol message or frame definition" in system:
            if "Simple Password" in user:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "name": "BFD Simple Password Authentication Section",
                            "fields": [
                                {"name": "Auth Type", "size_bits": 8},
                                {"name": "Auth Len", "size_bits": 8},
                                {"name": "Password", "size_bits": None},
                            ],
                        }
                    )
                )
            if "Keyed MD5" in user:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "name": "BFD Keyed MD5 Authentication Section",
                            "fields": [
                                {"name": "Authentication Type", "size_bits": 8},
                                {"name": "Auth Len", "size_bits": 8},
                                {"name": "Digest", "size_bits": 128},
                            ],
                        }
                    )
                )
        return await super().chat_with_tools(messages, tools)


def _hitl_page_index_payload(doc_name: str) -> dict:
    return {
        "doc_name": doc_name,
        "structure": [
            {
                "node_id": "msg-1",
                "title": "Simple Password Authentication Section",
                "text": "Simple Password Authentication format",
                "start_index": 1,
                "end_index": 1,
                "start_line": 1,
                "end_line": 1,
            },
            {
                "node_id": "msg-2",
                "title": "Keyed MD5 Authentication Section",
                "text": "Keyed MD5 Authentication format",
                "start_index": 2,
                "end_index": 2,
                "start_line": 1,
                "end_line": 1,
            },
        ],
    }


@pytest.mark.asyncio
async def test_run_pipeline_hitl_false_runs_without_review_cards(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(tmp_path, doc_stem, _hitl_page_index_payload(doc_name))
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "Simple Password Authentication format",
            2: "Keyed MD5 Authentication format",
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 2,
        },
    )
    llm = HitlNearMissLLM()
    results = await run_pipeline(doc_name=doc_name, llm=llm, enable_hitl=False)

    assert results[-1].stage == PipelineStage.VERIFY
    assert results[2].data["pending_review"] is False
    assert Path(results[2].data["near_miss_report_path"]).exists()
    assert not (tmp_path / "data" / "out" / doc_stem / "review_cards.json").exists()
    assert llm.evidence_call_count == 0


@pytest.mark.asyncio
async def test_run_pipeline_hitl_true_pauses_on_near_miss(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(tmp_path, doc_stem, _hitl_page_index_payload(doc_name))
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "Simple Password Authentication format",
            2: "Keyed MD5 Authentication format",
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 2,
        },
    )
    llm = HitlNearMissLLM()
    results = await run_pipeline(doc_name=doc_name, llm=llm, enable_hitl=True)

    assert [result.stage for result in results] == [
        PipelineStage.CLASSIFY,
        PipelineStage.EXTRACT,
        PipelineStage.MERGE,
    ]
    assert results[-1].data["pending_review"] is True
    assert Path(results[-1].data["review_cards_path"]).exists()
    assert not (tmp_path / "data" / "out" / doc_stem / "verify_report.json").exists()
    assert llm.evidence_call_count >= 1


@pytest.mark.asyncio
async def test_run_pipeline_hitl_resume_after_decisions(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc5880-BFD.pdf"
    doc_stem = "rfc5880-BFD"
    _write_page_index(tmp_path, doc_stem, _hitl_page_index_payload(doc_name))
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "Simple Password Authentication format",
            2: "Keyed MD5 Authentication format",
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 2,
        },
    )
    decisions_path = tmp_path / "data" / "out" / doc_stem / "review_decisions.json"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text(
        json.dumps(
            [
                {
                    "object_type": "message",
                    "pair": [0, 1],
                    "decision": "keep_separate",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    llm = HitlNearMissLLM()
    results = await run_pipeline(doc_name=doc_name, llm=llm, enable_hitl=True)

    assert results[-1].stage == PipelineStage.VERIFY
    assert all(result.data.get("pending_review") is not True for result in results if isinstance(result.data, dict))
