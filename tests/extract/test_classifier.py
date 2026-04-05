"""Tests for the protocol extraction classifier."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.classifier import (
    PROMPT_VERSION,
    _CLASSIFIER_SYSTEM_PROMPT,
    _apply_state_machine_sanity_filter,
    DEFAULT_LABEL_PRIORITY,
    _is_cache_valid,
    apply_overrides,
    coerce_node_label,
    load_or_classify_async,
    resolve_priority,
    summarize_labels,
)
from src.models import NodeLabelMeta, NodeSemanticLabel

LABELS = [
    "state_machine",
    "message_format",
    "procedure_rule",
    "timer_rule",
    "error_handling",
    "general_description",
]


# Feature: protocol-extraction-pipeline, Property 2: NodeSemanticLabel 有效性不变量
@given(
    node_id=st.text(min_size=1, max_size=20),
    maybe_label=st.one_of(st.sampled_from(LABELS), st.text(min_size=1, max_size=20)),
    confidence=st.one_of(
        st.integers(min_value=-10, max_value=10),
        st.floats(allow_nan=False, allow_infinity=False, width=16),
    ),
    rationale=st.text(max_size=100),
    candidates=st.lists(st.sampled_from(LABELS), max_size=6),
)
@settings(max_examples=100)
def test_coerce_node_label_returns_valid_model(
    node_id: str,
    maybe_label,
    confidence,
    rationale: str,
    candidates: list[str],
):
    label = coerce_node_label(
        node_id=node_id,
        payload={
            "label": maybe_label,
            "confidence": confidence,
            "rationale": rationale,
            "candidate_labels": candidates,
        },
        label_priority=DEFAULT_LABEL_PRIORITY,
    )
    assert label.node_id == node_id
    assert label.label in LABELS
    assert 0.0 <= label.confidence <= 1.0
    assert isinstance(label.rationale, str)
    assert label.rationale.strip()


# Feature: protocol-extraction-pipeline, Property 3: 优先级冲突消解
@given(candidates=st.lists(st.sampled_from(LABELS), min_size=1, max_size=6, unique=True))
@settings(max_examples=100)
def test_resolve_priority_selects_highest_priority(candidates: list[str]):
    resolved = resolve_priority(candidates, DEFAULT_LABEL_PRIORITY)
    expected = min(
        candidates,
        key=lambda label: DEFAULT_LABEL_PRIORITY.index(label),
    )
    assert resolved == expected


# Feature: protocol-extraction-pipeline, Property 5: 缓存有效性判断
@given(
    model_name=st.text(min_size=1, max_size=20),
    prompt_version=st.text(min_size=1, max_size=10),
    created_at=st.text(min_size=1, max_size=20),
    priority=st.just(DEFAULT_LABEL_PRIORITY),
    tweak_model=st.booleans(),
    tweak_prompt=st.booleans(),
    tweak_priority=st.booleans(),
)
@settings(max_examples=100)
def test_cache_validity_depends_only_on_three_fields(
    model_name: str,
    prompt_version: str,
    created_at: str,
    priority: list[str],
    tweak_model: bool,
    tweak_prompt: bool,
    tweak_priority: bool,
):
    cached = NodeLabelMeta(
        source_document="doc",
        model_name=model_name,
        prompt_version=prompt_version,
        label_priority=priority,
        created_at=created_at,
    )
    current = NodeLabelMeta(
        source_document="other-doc",
        model_name=model_name + "-x" if tweak_model else model_name,
        prompt_version=prompt_version + "-x" if tweak_prompt else prompt_version,
        label_priority=list(reversed(priority)) if tweak_priority else priority,
        created_at=created_at + "-later",
    )
    assert _is_cache_valid(cached, current) == (not tweak_model and not tweak_prompt and not tweak_priority)


# Feature: protocol-extraction-pipeline, Property 6: Override 合并正确性
@given(
    override_label=st.sampled_from(LABELS),
    original_label=st.sampled_from(LABELS),
)
@settings(max_examples=100)
def test_apply_overrides_replaces_existing_labels(override_label: str, original_label: str):
    labels = {
        "n1": NodeSemanticLabel(
            node_id="n1",
            label=original_label,
            confidence=0.5,
            rationale="before",
            secondary_hints=[],
        )
    }
    with tempfile.TemporaryDirectory() as tmp_dir:
        override_path = Path(tmp_dir) / "override.json"
        override_path.write_text(
            json.dumps({"n1": {"label": override_label, "rationale": "after"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        merged = apply_overrides(labels, str(override_path))
        assert merged["n1"].label == override_label
        assert merged["n1"].rationale == "after"
        assert merged["n1"].confidence == labels["n1"].confidence


def test_apply_overrides_ignores_unknown_node(tmp_path):
    labels = {
        "n1": NodeSemanticLabel(
            node_id="n1",
            label="general_description",
            confidence=0.5,
            rationale="keep",
            secondary_hints=[],
        )
    }
    override_path = tmp_path / "override.json"
    override_path.write_text(
        json.dumps({"missing": {"label": "state_machine", "rationale": "nope"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    merged = apply_overrides(labels, str(override_path))
    assert merged == labels


def test_load_or_classify_ignores_empty_valid_cache(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "data" / "out" / "doc"
    out_dir.mkdir(parents=True)
    (out_dir / "node_labels.json").write_text("{}", encoding="utf-8")
    NodeLabelMeta(
        source_document="doc",
        model_name="mock-model",
        prompt_version=PROMPT_VERSION,
        label_priority=DEFAULT_LABEL_PRIORITY,
        created_at="now",
    ).model_dump_json(indent=2)
    (out_dir / "node_labels.meta.json").write_text(
        NodeLabelMeta(
            source_document="doc",
            model_name="mock-model",
            prompt_version=PROMPT_VERSION,
            label_priority=DEFAULT_LABEL_PRIORITY,
            created_at="now",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    async def fake_classify_all_nodes(**kwargs):
        return {
            "n1": NodeSemanticLabel(
                node_id="n1",
                label="state_machine",
                confidence=0.9,
                rationale="fresh",
            )
        }

    class DummyLLM:
        model = "mock-model"

    monkeypatch.setattr("src.extract.classifier.classify_all_nodes", fake_classify_all_nodes)

    labels = asyncio.run(
        load_or_classify_async(
            doc_stem="doc",
            nodes=[{"node_id": "n1"}],
            content_dir="unused",
            llm=DummyLLM(),
        )
    )

    assert labels["n1"].label == "state_machine"


def test_classifier_prompt_regression_mentions_standalone_and_removes_old_rule():
    assert "COMPLETE, standalone state machine" in _CLASSIFIER_SYSTEM_PROMPT
    assert "single \"in state X, on event Y, do Z\" clause should" in _CLASSIFIER_SYSTEM_PROMPT
    assert "Section summaries may be noisy" in _CLASSIFIER_SYSTEM_PROMPT
    assert "overview/introduction/design sections" in _CLASSIFIER_SYSTEM_PROMPT
    assert 'prefer state_machine over procedure_rule' not in _CLASSIFIER_SYSTEM_PROMPT


def test_classifier_prompt_version_invalidates_old_v1_3_cache():
    assert PROMPT_VERSION != "v1.0"
    assert PROMPT_VERSION != "v1.3-standalone-fsm-sanity-generic-procedure"

    cached = NodeLabelMeta(
        source_document="doc",
        model_name="mock-model",
        prompt_version="v1.3-standalone-fsm-sanity-generic-procedure",
        label_priority=DEFAULT_LABEL_PRIORITY,
        created_at="now",
    )
    current = NodeLabelMeta(
        source_document="doc",
        model_name="mock-model",
        prompt_version=PROMPT_VERSION,
        label_priority=DEFAULT_LABEL_PRIORITY,
        created_at="later",
    )

    assert _is_cache_valid(cached, current) is False


def test_state_machine_sanity_filter_downgrades_meta_sections():
    label = NodeSemanticLabel(
        node_id="0049",
        label="state_machine",
        confidence=0.99,
        rationale="wrong",
    )

    downgraded = _apply_state_machine_sanity_filter(
        label=label,
        title="9 Security Considerations",
        summary="Threat model and authentication tradeoffs.",
        text_snippet="Security discussion for BFD sessions.",
    )

    assert downgraded.label == "general_description"
    assert "Downgraded from state_machine (meta_section):" in downgraded.rationale
    assert "sanity_downgrade:meta_section" in downgraded.secondary_hints


def test_state_machine_sanity_filter_downgrades_local_procedure_sections():
    label = NodeSemanticLabel(
        node_id="0034",
        label="state_machine",
        confidence=0.98,
        rationale="wrong",
    )

    downgraded = _apply_state_machine_sanity_filter(
        label=label,
        title="6.8.6 Reception of BFD Control Packets",
        summary="Ordered packet-validation rules and local state updates.",
        text_snippet="When a BFD Control packet is received, the implementation follows ordered checks.",
    )

    assert downgraded.label == "procedure_rule"
    assert "Downgraded from state_machine (numbered_check):" in downgraded.rationale
    assert "sanity_downgrade:numbered_check" in downgraded.secondary_hints


def test_state_machine_sanity_filter_downgrades_send_call_with_invocation_language():
    label = NodeSemanticLabel(
        node_id="0031",
        label="state_machine",
        confidence=0.98,
        rationale="wrong",
    )

    downgraded = _apply_state_machine_sanity_filter(
        label=label,
        title="3.9.2 SEND Call",
        summary="The user issues this call to send data on a connection.",
        text_snippet="The user issues a SEND call when the application requests to transmit data.",
    )

    assert downgraded.label == "procedure_rule"
    assert "Downgraded from state_machine (call_procedure):" in downgraded.rationale
    assert "sanity_downgrade:call_procedure" in downgraded.secondary_hints


def test_state_machine_sanity_filter_downgrades_numbered_check_sections():
    label = NodeSemanticLabel(
        node_id="0049",
        label="state_machine",
        confidence=0.98,
        rationale="wrong",
    )

    downgraded = _apply_state_machine_sanity_filter(
        label=label,
        title="1.4.2 second check the RST bit,",
        summary="This local check handles RST processing.",
        text_snippet="Second, check the RST bit and process the segment accordingly.",
    )

    assert downgraded.label == "procedure_rule"
    assert "Downgraded from state_machine (numbered_check):" in downgraded.rationale
    assert "sanity_downgrade:numbered_check" in downgraded.secondary_hints


def test_state_machine_sanity_filter_preserves_bfd_state_machine():
    label = NodeSemanticLabel(
        node_id="0018",
        label="state_machine",
        confidence=0.99,
        rationale="correct",
    )

    preserved = _apply_state_machine_sanity_filter(
        label=label,
        title="6.2 BFD State Machine",
        summary="Defines Down, Init, Up, and AdminDown transitions.",
        text_snippet="The BFD state machine defines transitions among Down, Init, Up, and AdminDown.",
    )

    assert preserved.label == "state_machine"
    assert preserved.secondary_hints == []


def test_state_machine_sanity_filter_preserves_true_standalone_state_diagram():
    label = NodeSemanticLabel(
        node_id="sm-diagram",
        label="state_machine",
        confidence=0.95,
        rationale="correct",
    )

    preserved = _apply_state_machine_sanity_filter(
        label=label,
        title="TCP Connection State Diagram",
        summary="Shows transitions among the stable TCP connection states.",
        text_snippet="The diagram shows transitions among LISTEN, SYN-SENT, ESTABLISHED, FIN-WAIT-1, and TIME-WAIT.",
    )

    assert preserved.label == "state_machine"
    assert preserved.secondary_hints == []


def test_summarize_labels_reports_sanity_downgrade_metrics():
    labels = {
        "n1": NodeSemanticLabel(
            node_id="n1",
            label="procedure_rule",
            confidence=0.9,
            rationale="Downgraded from state_machine (numbered_check): example",
            secondary_hints=["sanity_downgrade:numbered_check"],
        ),
        "n2": NodeSemanticLabel(
            node_id="n2",
            label="procedure_rule",
            confidence=0.9,
            rationale="Downgraded from state_machine (call_procedure): example",
            secondary_hints=["timer_rule", "sanity_downgrade:call_procedure"],
        ),
        "n3": NodeSemanticLabel(
            node_id="n3",
            label="general_description",
            confidence=0.9,
            rationale="Downgraded from state_machine (meta_section): example",
            secondary_hints=["sanity_downgrade:meta_section"],
        ),
    }

    summary = summarize_labels(labels)

    assert summary["state_machine_sanity_downgrade_count"] == 3
    assert summary["state_machine_sanity_downgrade_by_reason"] == {
        "numbered_check": 1,
        "call_procedure": 1,
        "meta_section": 1,
    }
