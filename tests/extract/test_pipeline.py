"""Tests for protocol extraction pipeline orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.fsm_ir import RefineStats
from src.models import LLMResponse, NodeSemanticLabel, ProtocolMessage, ProtocolSchema, ProtocolStateMachine
from src.extract import pipeline as pipeline_module
from src.extract.pipeline import (
    FsmSegment,
    PipelineStage,
    _build_fsm_segments,
    _build_outline_contexts,
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
        if "You refine protocol FSM branches into a restricted typed IR subset." in system:
            payload = json.loads(user)
            raw_actions = payload.get("raw_actions", [])
            return LLMResponse(text=json.dumps({"guard": None, "actions": [None for _ in raw_actions]}))
        if "You are reclassifying protocol document nodes that may have been over-labeled as standalone state machines." in system:
            return LLMResponse(text=json.dumps({"updates": []}))
        if "You classify communication-protocol document nodes." in system:
            if "Overview" in user:
                payload = {"label": "general_description", "confidence": 0.9, "rationale": "overview"}
            elif "Packet Format" in user:
                payload = {"label": "message_format", "confidence": 0.9, "rationale": "format"}
            else:
                payload = {"label": "state_machine", "confidence": 0.9, "rationale": "state"}
            return LLMResponse(text=json.dumps(payload))
        if "protocol state machine" in system:
            return LLMResponse(
                text=json.dumps(
                    {
                        "name": "BFD Session",
                        "states": [
                            {"name": "Down", "is_initial": True},
                            {"name": "Up", "is_final": True},
                        ],
                        "transitions": [
                            {
                                "from_state": "Down",
                                "to_state": "Up",
                                "event": "Receive valid control packet",
                            }
                        ],
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


def _write_extract_results(tmp_path: Path, doc_stem: str, records: list[dict]) -> Path:
    artifact_dir = tmp_path / "data" / "out" / doc_stem
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "extract_results.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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


def test_build_outline_contexts_recovers_parent_heading_and_siblings():
    payload = {
        "structure": [
            {
                "node_id": "root",
                "title": "3 Event Processing",
                "children": [
                    {
                        "node_id": "parent",
                        "title": "3.9 Segment Arrives",
                        "children": [
                            {"node_id": "syn-check", "title": "Third Check for SYN"},
                            {"node_id": "ack-check", "title": "Fourth Check for ACK"},
                            {"node_id": "rst-check", "title": "Fifth Check for RST"},
                        ],
                    }
                ],
            }
        ]
    }

    contexts = _build_outline_contexts(payload)

    assert contexts["syn-check"].section_path == [
        "3 Event Processing",
        "3.9 Segment Arrives",
        "Third Check for SYN",
    ]
    assert contexts["syn-check"].parent_heading == "3.9 Segment Arrives"
    assert contexts["syn-check"].parent_node_id == "parent"
    assert contexts["syn-check"].sibling_titles == [
        "Fourth Check for ACK",
        "Fifth Check for RST",
    ]


def test_build_fsm_segments_collects_all_siblings_when_anchor_is_in_middle():
    payload = {
        "structure": [
            {
                "node_id": "root",
                "title": "3 Event Processing",
                "children": [
                    {
                        "node_id": "parent",
                        "title": "3.9 Event Processing",
                        "children": [
                            {"node_id": "open-call", "title": "3.9.1 OPEN Call"},
                            {"node_id": "send-call", "title": "3.9.2 SEND Call"},
                            {"node_id": "close-call", "title": "3.9.3 CLOSE Call"},
                            {"node_id": "status-call", "title": "3.9.4 STATUS Call"},
                        ],
                    }
                ],
            }
        ]
    }

    nodes = _collect_leaf_nodes(payload)
    contexts = _build_outline_contexts(payload)
    labels = {
        "open-call": NodeSemanticLabel(node_id="open-call", label="procedure_rule", rationale="open"),
        "send-call": NodeSemanticLabel(node_id="send-call", label="state_machine", rationale="send"),
        "close-call": NodeSemanticLabel(node_id="close-call", label="procedure_rule", rationale="close"),
        "status-call": NodeSemanticLabel(node_id="status-call", label="state_machine", rationale="status"),
    }

    segments, skip_reasons = _build_fsm_segments(nodes, labels, contexts)

    assert len(segments) == 1
    assert segments[0] == FsmSegment(
        anchor_node_id="send-call",
        parent_node_id="parent",
        parent_heading="3.9 Event Processing",
        node_ids=["open-call", "send-call", "close-call", "status-call"],
        target_node_ids=["send-call", "status-call"],
    )
    assert skip_reasons["no_parent"] == 0
    assert skip_reasons["single_node"] == 0


@pytest.mark.asyncio
async def test_reclassify_fsm_segments_updates_only_targets_and_persists_labels(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_stem = "rfc793-TCP"
    payload = {
        "structure": [
            {
                "node_id": "root",
                "title": "3 Event Processing",
                "children": [
                    {
                        "node_id": "parent",
                        "title": "3.9 Event Processing",
                        "children": [
                            {
                                "node_id": "open-call",
                                "title": "3.9.1 OPEN Call",
                                "start_index": 1,
                                "end_index": 1,
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "node_id": "send-call",
                                "title": "3.9.2 SEND Call",
                                "start_index": 2,
                                "end_index": 2,
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "node_id": "close-call",
                                "title": "3.9.3 CLOSE Call",
                                "start_index": 3,
                                "end_index": 3,
                                "start_line": 1,
                                "end_line": 1,
                            },
                        ],
                    }
                ],
            }
        ]
    }
    nodes = _collect_leaf_nodes(payload)
    contexts = _build_outline_contexts(payload)
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "The user issues an OPEN call.",
            2: "The user issues a SEND call when the application requests transmission.",
            3: "The user issues a CLOSE call.",
        },
    )
    labels = {
        "open-call": NodeSemanticLabel(node_id="open-call", label="procedure_rule", confidence=0.9, rationale="open"),
        "send-call": NodeSemanticLabel(node_id="send-call", label="state_machine", confidence=0.9, rationale="send"),
        "close-call": NodeSemanticLabel(node_id="close-call", label="procedure_rule", confidence=0.9, rationale="close"),
    }

    class SegmentLLM(DummyLLM):
        async def chat_with_tools(self, messages, tools):
            system = messages[0]["content"]
            if "You are reclassifying protocol document nodes that may have been over-labeled as standalone state machines." in system:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "updates": [
                                {
                                    "node_id": "open-call",
                                    "label": "general_description",
                                    "confidence": 0.8,
                                    "rationale": "ignored non-target update",
                                },
                                {
                                    "node_id": "send-call",
                                    "label": "procedure_rule",
                                    "confidence": 0.97,
                                    "rationale": "Sibling call handlers show this is a local procedure, not a standalone FSM.",
                                },
                            ]
                        }
                    )
                )
            return await super().chat_with_tools(messages, tools)

    refined_labels, stats = await pipeline_module._reclassify_fsm_segments(
        doc_stem=doc_stem,
        nodes=nodes,
        labels=labels,
        outline_contexts=contexts,
        content_dir=content_dir,
        llm=SegmentLLM(),
        label_priority=["state_machine", "procedure_rule", "general_description"],
    )

    assert refined_labels["open-call"].label == "procedure_rule"
    assert refined_labels["send-call"].label == "procedure_rule"
    assert stats["fsm_segment_count"] == 1
    assert stats["fsm_segment_reclassified_count"] == 1
    assert stats["fsm_segment_updated_node_count"] == 1
    cached_labels = json.loads((tmp_path / "data" / "out" / doc_stem / "node_labels.json").read_text(encoding="utf-8"))
    assert cached_labels["send-call"]["label"] == "procedure_rule"


@pytest.mark.asyncio
async def test_reclassify_fsm_segments_treats_same_label_updates_as_noop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_stem = "rfc793-TCP"
    payload = {
        "structure": [
            {
                "node_id": "root",
                "title": "3 Event Processing",
                "children": [
                    {
                        "node_id": "parent",
                        "title": "3.9 Event Processing",
                        "children": [
                            {
                                "node_id": "open-call",
                                "title": "3.9.1 OPEN Call",
                                "start_index": 1,
                                "end_index": 1,
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "node_id": "send-call",
                                "title": "3.9.2 SEND Call",
                                "start_index": 2,
                                "end_index": 2,
                                "start_line": 1,
                                "end_line": 1,
                            },
                        ],
                    }
                ],
            }
        ]
    }
    nodes = _collect_leaf_nodes(payload)
    contexts = _build_outline_contexts(payload)
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {1: "The user issues an OPEN call.", 2: "The user issues a SEND call."},
    )
    labels = {
        "open-call": NodeSemanticLabel(node_id="open-call", label="procedure_rule", confidence=0.9, rationale="open"),
        "send-call": NodeSemanticLabel(node_id="send-call", label="state_machine", confidence=0.9, rationale="send"),
    }

    class NoopSegmentLLM(DummyLLM):
        async def chat_with_tools(self, messages, tools):
            system = messages[0]["content"]
            if "You are reclassifying protocol document nodes that may have been over-labeled as standalone state machines." in system:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "updates": [
                                {
                                    "node_id": "send-call",
                                    "label": "state_machine",
                                    "confidence": 0.91,
                                    "rationale": "same label should be treated as no-op",
                                }
                            ]
                        }
                    )
                )
            return await super().chat_with_tools(messages, tools)

    _, stats = await pipeline_module._reclassify_fsm_segments(
        doc_stem=doc_stem,
        nodes=nodes,
        labels=labels,
        outline_contexts=contexts,
        content_dir=content_dir,
        llm=NoopSegmentLLM(),
        label_priority=["state_machine", "procedure_rule", "general_description"],
    )

    assert stats["fsm_segment_reclassified_count"] == 1
    assert stats["fsm_segment_updated_node_count"] == 0
    assert not (tmp_path / "data" / "out" / doc_stem / "node_labels.json").exists()


@pytest.mark.asyncio
async def test_reclassify_fsm_segments_skips_single_node_parent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_stem = "rfc5880-BFD"
    payload = {
        "structure": [
            {
                "node_id": "root",
                "title": "6 BFD",
                "children": [
                    {
                        "node_id": "parent",
                        "title": "6.2 BFD State Machine",
                        "children": [
                            {
                                "node_id": "bfd-state-machine",
                                "title": "6.2.1 State Diagram",
                                "start_index": 1,
                                "end_index": 1,
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    nodes = _collect_leaf_nodes(payload)
    contexts = _build_outline_contexts(payload)
    content_dir = _write_content_db(tmp_path, doc_stem, {1: "Down, Init, Up, AdminDown transitions."})
    labels = {
        "bfd-state-machine": NodeSemanticLabel(
            node_id="bfd-state-machine",
            label="state_machine",
            confidence=0.99,
            rationale="true fsm",
        )
    }

    refined_labels, stats = await pipeline_module._reclassify_fsm_segments(
        doc_stem=doc_stem,
        nodes=nodes,
        labels=labels,
        outline_contexts=contexts,
        content_dir=content_dir,
        llm=DummyLLM(),
        label_priority=["state_machine", "procedure_rule", "general_description"],
    )

    assert refined_labels == labels
    assert stats["fsm_segment_count"] == 0
    assert stats["fsm_segment_reclassified_count"] == 0
    assert stats["fsm_segment_updated_node_count"] == 0
    assert stats["fsm_segment_skip_reasons"]["single_node"] == 1


@pytest.mark.asyncio
async def test_reclassify_fsm_segments_records_over_limit_and_invalid_response(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_stem = "rfc793-TCP"
    payload = {
        "structure": [
            {
                "node_id": "root",
                "title": "3 Event Processing",
                "children": [
                    {
                        "node_id": "parent-a",
                        "title": "3.9 Event Processing",
                        "children": [
                            {
                                "node_id": "open-call",
                                "title": "3.9.1 OPEN Call",
                                "start_index": 1,
                                "end_index": 1,
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "node_id": "send-call",
                                "title": "3.9.2 SEND Call",
                                "start_index": 2,
                                "end_index": 2,
                                "start_line": 1,
                                "end_line": 1,
                            },
                        ],
                    },
                    {
                        "node_id": "parent-b",
                        "title": "3.10 Segment Arrives",
                        "children": [
                            {
                                "node_id": "check-a",
                                "title": "First Check",
                                "start_index": 3,
                                "end_index": 3,
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "node_id": "check-b",
                                "title": "Second Check",
                                "start_index": 4,
                                "end_index": 4,
                                "start_line": 1,
                                "end_line": 1,
                            },
                        ],
                    },
                ],
            }
        ]
    }
    nodes = _collect_leaf_nodes(payload)
    contexts = _build_outline_contexts(payload)
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "A" * 9000,
            2: "B" * 9000,
            3: "Check A text",
            4: "Check B text",
        },
    )
    labels = {
        "open-call": NodeSemanticLabel(node_id="open-call", label="procedure_rule", confidence=0.9, rationale="open"),
        "send-call": NodeSemanticLabel(node_id="send-call", label="state_machine", confidence=0.9, rationale="send"),
        "check-a": NodeSemanticLabel(node_id="check-a", label="procedure_rule", confidence=0.9, rationale="check"),
        "check-b": NodeSemanticLabel(node_id="check-b", label="state_machine", confidence=0.9, rationale="check"),
    }

    class InvalidSegmentLLM(DummyLLM):
        async def chat_with_tools(self, messages, tools):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "You are reclassifying protocol document nodes that may have been over-labeled as standalone state machines." in system:
                if "Target node IDs: check-b" in user:
                    return LLMResponse(text=json.dumps({"updates": [{"node_id": "check-b"}]}))
            return await super().chat_with_tools(messages, tools)

    refined_labels, stats = await pipeline_module._reclassify_fsm_segments(
        doc_stem=doc_stem,
        nodes=nodes,
        labels=labels,
        outline_contexts=contexts,
        content_dir=content_dir,
        llm=InvalidSegmentLLM(),
        label_priority=["state_machine", "procedure_rule", "general_description"],
    )

    assert refined_labels == labels
    assert stats["fsm_segment_count"] == 2
    assert stats["fsm_segment_reclassified_count"] == 1
    assert stats["fsm_segment_updated_node_count"] == 0
    assert stats["fsm_segment_skip_reasons"]["over_limit"] == 1
    assert stats["fsm_segment_skip_reasons"]["invalid_response"] == 1


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
    assert results[0].data["state_machine_sanity_downgrade_count"] == 0
    assert results[0].data["state_machine_sanity_downgrade_by_reason"] == {}
    assert not (tmp_path / "data" / "out" / doc_stem / "protocol_schema.json").exists()


@pytest.mark.asyncio
async def test_run_pipeline_extract_only_uses_refined_cached_labels_after_classify(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doc_name = "rfc793-TCP.pdf"
    doc_stem = "rfc793-TCP"
    _write_page_index(
        tmp_path,
        doc_stem,
        {
            "doc_name": doc_name,
            "structure": [
                {
                    "node_id": "root",
                    "title": "3 Event Processing",
                    "children": [
                        {
                            "node_id": "parent",
                            "title": "3.9 Event Processing",
                            "children": [
                                {
                                    "node_id": "open-call",
                                    "title": "3.9.1 OPEN Call",
                                    "start_index": 1,
                                    "end_index": 1,
                                    "start_line": 1,
                                    "end_line": 1,
                                },
                                {
                                    "node_id": "send-call",
                                    "title": "3.9.2 SEND Call",
                                    "start_index": 2,
                                    "end_index": 2,
                                    "start_line": 1,
                                    "end_line": 1,
                                },
                            ],
                        }
                    ],
                }
            ],
        },
    )
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "The user issues an OPEN call.",
            2: "Transmit queued data over the connection according to the current send path.",
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

    class SegmentRefineLLM(DummyLLM):
        async def chat_with_tools(self, messages, tools):
            system = messages[0]["content"]
            if "You classify communication-protocol document nodes." in system:
                user = messages[-1]["content"]
                if "OPEN Call" in user:
                    return LLMResponse(text=json.dumps({"label": "procedure_rule", "confidence": 0.9, "rationale": "open"}))
                if "SEND Call" in user:
                    return LLMResponse(text=json.dumps({"label": "state_machine", "confidence": 0.9, "rationale": "send"}))
            if "You are reclassifying protocol document nodes that may have been over-labeled as standalone state machines." in system:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "updates": [
                                {
                                    "node_id": "send-call",
                                    "label": "procedure_rule",
                                    "confidence": 0.96,
                                    "rationale": "This call handler belongs to the same procedural series as OPEN Call.",
                                }
                            ]
                        }
                    )
                )
            return await super().chat_with_tools(messages, tools)

    classify_results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.CLASSIFY],
        llm=SegmentRefineLLM(),
    )

    assert classify_results[0].data["fsm_segment_updated_node_count"] == 1

    extract_results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.EXTRACT],
        llm=DummyLLM(),
    )

    extract_data = extract_results[0].data
    assert extract_results[0].success is True
    assert extract_data["state_machine_count"] == 0
    assert extract_data["procedure_count"] == 2


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
    state_context_ir_path = Path(merge_result.data["state_context_ir_path"])
    alignment_report_path = Path(merge_result.data["alignment_report_path"])
    verify_report_path = Path(verify_result.data["verify_report_path"])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    merge_report = json.loads(merge_report_path.read_text(encoding="utf-8"))
    near_miss_report = json.loads(near_miss_report_path.read_text(encoding="utf-8"))
    alignment_report = json.loads(alignment_report_path.read_text(encoding="utf-8"))
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
    assert state_context_ir_path.exists()
    assert alignment_report_path.exists()
    assert verify_report_path.exists()
    assert schema["protocol_name"] == doc_stem
    assert len(schema["state_machines"]) == 1
    assert len(schema["state_contexts"]) == 1
    assert len(schema["messages"]) == 1
    assert schema["state_machines"][0]["source_pages"] == [1]
    assert schema["messages"][0]["source_pages"] == [2]
    assert extract_result.data["skipped_by_label"]["general_description"] == 1
    assert merge_report["post_merge_counts"]["message"] == 1
    assert merge_report["post_merge_counts"]["timer"] == 0
    assert merge_report["near_miss_summary"] == {"sm_count": 0, "msg_count": 0}
    assert merge_result.data["state_context_ir_count"] == 1
    assert alignment_report["protocol_name"] == doc_stem
    assert alignment_report["summary"]["fsm_count"] == 1
    assert merge_result.data["alignment_error_count"] == alignment_report["summary"]["error_count"] == 0
    assert merge_result.data["alignment_warning_count"] == alignment_report["summary"]["warning_count"]
    assert merge_result.data["aligned_fsm_count"] == alignment_report["summary"]["aligned_fsm_count"] == 1
    assert merge_result.data["alignment_typed_ref_count"] == alignment_report["summary"]["typed_ref_count"] == 0
    assert merge_result.data["alignment_resolved_ref_count"] == alignment_report["summary"]["resolved_ref_count"] == 0
    assert merge_result.data["alignment_coverage_ratio"] == alignment_report["summary"]["coverage_ratio"] == 0.0
    assert near_miss_report["summary"] == {"sm_count": 0, "msg_count": 0}
    assert codegen_result.data["file_count"] >= 3
    assert codegen_result.data["typed_action_count"] == 0
    assert codegen_result.data["generated_action_count"] == 0
    assert codegen_result.data["degraded_action_count"] == 0
    assert codegen_result.data["action_codegen_ratio"] == 0.0
    assert all(isinstance(item, dict) for item in codegen_result.data["generated_msgs"])
    assert "syntax_checked" in verify_report


@pytest.mark.asyncio
async def test_run_pipeline_merge_emits_phase_c_metrics(monkeypatch, tmp_path):
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
                }
            ],
        },
    )
    content_dir = _write_content_db(tmp_path, doc_stem, {1: "State transition text"})
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 1,
        },
    )
    _write_extract_results(
        tmp_path,
        doc_stem,
        [
            {
                "node_id": "sm-1",
                "title": "State Machine",
                "label": "state_machine",
                "confidence": 0.9,
                "source_pages": [1],
                "payload": {
                    "name": "BFD Session",
                    "states": [{"name": "Down", "is_initial": True}, {"name": "Up"}],
                    "transitions": [
                        {
                            "from_state": "Down",
                            "to_state": "Up",
                            "event": "Receive Control Packet",
                            "condition": "complex raw guard",
                            "actions": ["do something raw"],
                        }
                    ],
                    "source_pages": [1],
                },
            }
        ],
    )

    async def fake_refine(fsm_irs, schema, llm):
        assert len(fsm_irs) == 1
        return fsm_irs, RefineStats(
            triggered_count=1,
            accepted_guard_count=2,
            accepted_action_count=3,
            raw_branch_ratio_before=0.75,
            raw_branch_ratio_after=0.25,
        )

    monkeypatch.setattr("src.extract.pipeline.refine_fsm_irs", fake_refine)

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.MERGE],
        llm=DummyLLM(),
    )

    merge_data = results[0].data
    assert results[0].success is True
    assert merge_data["llm_refine_triggered_count"] == 1
    assert merge_data["llm_refine_accepted_guard_count"] == 2
    assert merge_data["llm_refine_accepted_action_count"] == 3
    assert merge_data["raw_branch_ratio_before"] == 0.75
    assert merge_data["raw_branch_ratio_after"] == 0.25


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


@pytest.mark.asyncio
async def test_run_pipeline_extract_augments_only_state_machine_text(monkeypatch, tmp_path):
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
                    "node_id": "section-1",
                    "title": "3 Event Processing",
                    "children": [
                        {
                            "node_id": "sm-node",
                            "title": "Third Check for SYN",
                            "text": "State transition text",
                            "start_index": 1,
                            "end_index": 1,
                            "start_line": 1,
                            "end_line": 1,
                        },
                        {
                            "node_id": "msg-node",
                            "title": "Packet Format",
                            "text": "Field table text",
                            "start_index": 2,
                            "end_index": 2,
                            "start_line": 1,
                            "end_line": 1,
                        },
                    ],
                }
            ],
        },
    )
    content_dir = _write_content_db(tmp_path, doc_stem, {1: "State transition text", 2: "Field table text"})
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 2,
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline._load_cached_labels",
        lambda _doc_stem: {
            "sm-node": NodeSemanticLabel(node_id="sm-node", label="state_machine", confidence=1.0, rationale="fsm"),
            "msg-node": NodeSemanticLabel(node_id="msg-node", label="message_format", confidence=1.0, rationale="msg"),
        },
    )

    captured: dict[str, str] = {}

    class CaptureStateMachineExtractor:
        async def extract(self, node_id, text, title, source_pages=None):
            captured["state_machine"] = text
            return ProtocolStateMachine(name=title, source_pages=list(source_pages or []))

    class CaptureMessageExtractor:
        async def extract(self, node_id, text, title, source_pages=None):
            captured["message_format"] = text
            return ProtocolMessage(name=title, fields=[{"name": "Version"}], source_pages=list(source_pages or []))

    def fake_route(label, llm):
        if label == "state_machine":
            return CaptureStateMachineExtractor()
        if label == "message_format":
            return CaptureMessageExtractor()
        return None

    monkeypatch.setattr("src.extract.pipeline._route_to_extractor", fake_route)

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.EXTRACT],
        llm=DummyLLM(),
    )

    assert results[0].success is True
    assert captured["state_machine"].startswith("Document outline context:\n")
    assert "Section path: 3 Event Processing > Third Check for SYN" in captured["state_machine"]
    assert "Node text:\nState transition text" in captured["state_machine"]
    assert captured["message_format"] == "Field table text"


@pytest.mark.asyncio
async def test_run_pipeline_extract_emits_outline_metrics(monkeypatch, tmp_path):
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
                    "node_id": "section-1",
                    "title": "3 Event Processing",
                    "children": [
                        {
                            "node_id": "sm-node",
                            "title": "Third Check for SYN",
                            "text": "This is a numbered local check.",
                            "start_index": 1,
                            "end_index": 1,
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                }
            ],
        },
    )
    content_dir = _write_content_db(tmp_path, doc_stem, {1: "This is a numbered local check."})
    monkeypatch.setattr(
        "src.extract.pipeline.get_doc_config",
        lambda name: {
            "content_dir": content_dir,
            "chunks_dir": f"data/out/chunk/{doc_stem}",
            "total_pages": 1,
        },
    )
    monkeypatch.setattr(
        "src.extract.pipeline._load_cached_labels",
        lambda _doc_stem: {
            "sm-node": NodeSemanticLabel(node_id="sm-node", label="state_machine", confidence=1.0, rationale="fsm"),
        },
    )

    class EmptyStateMachineExtractor:
        async def extract(self, node_id, text, title, source_pages=None):
            return ProtocolStateMachine(name=title, source_pages=list(source_pages or []))

    monkeypatch.setattr(
        "src.extract.pipeline._route_to_extractor",
        lambda label, llm: EmptyStateMachineExtractor() if label == "state_machine" else None,
    )

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.EXTRACT],
        llm=DummyLLM(),
    )

    extract_data = results[0].data
    assert results[0].success is True
    assert extract_data["empty_fsm_return_count"] == 1
    assert extract_data["state_machine_context_augmented_count"] == 1


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
        if "protocol state machine" in system:
            if "Title: Session State Machine Summary" in user:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "name": "BFD Session State Machine (RFC 5880 §6.1 Summary)",
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
                    "title": "Session State Machine Summary",
                    "text": "session summary",
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
            1: "session summary",
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
async def test_run_pipeline_merge_codegen_verify_from_cached_extract_results(monkeypatch, tmp_path):
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
            ],
        },
    )
    content_dir = _write_content_db(
        tmp_path,
        doc_stem,
        {
            1: "State transition text",
            2: "Field table text",
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
    _write_extract_results(
        tmp_path,
        doc_stem,
        [
            {
                "node_id": "sm-1",
                "title": "State Machine",
                "label": "state_machine",
                "confidence": 0.9,
                "source_pages": [1],
                "payload": {
                    "name": "BFD Session",
                    "states": [{"name": "Down", "is_initial": True}],
                    "transitions": [],
                    "source_pages": [1],
                },
            },
            {
                "node_id": "msg-1",
                "title": "Packet Format",
                "label": "message_format",
                "confidence": 0.9,
                "source_pages": [2],
                "payload": {
                    "name": "BFD Control Packet",
                    "fields": [{"name": "Version", "size_bits": 3}],
                    "source_pages": [2],
                },
            },
        ],
    )

    results = await run_pipeline(
        doc_name=doc_name,
        stages=[PipelineStage.MERGE, PipelineStage.CODEGEN, PipelineStage.VERIFY],
    )

    assert [result.stage for result in results] == [
        PipelineStage.MERGE,
        PipelineStage.CODEGEN,
        PipelineStage.VERIFY,
    ]
    assert results[0].success is True
    assert results[1].success is True
    assert results[2].success is True
    assert Path(results[0].data["schema_path"]).exists()
    assert Path(results[0].data["state_context_ir_path"]).exists()
    assert Path(results[0].data["alignment_report_path"]).exists()
    assert Path(results[1].data["generated_dir"]).exists()
    assert Path(results[2].data["verify_report_path"]).exists()
    merged_schema = json.loads(Path(results[0].data["schema_path"]).read_text(encoding="utf-8"))
    assert len(merged_schema["state_machines"]) == 1
    assert len(merged_schema["fsm_irs"]) == 1
    assert len(merged_schema["state_contexts"]) == 1


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
