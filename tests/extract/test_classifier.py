"""Tests for the protocol extraction classifier."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.classifier import (
    DEFAULT_LABEL_PRIORITY,
    _is_cache_valid,
    apply_overrides,
    coerce_node_label,
    load_or_classify_async,
    resolve_priority,
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
        prompt_version="v1.0",
        label_priority=DEFAULT_LABEL_PRIORITY,
        created_at="now",
    ).model_dump_json(indent=2)
    (out_dir / "node_labels.meta.json").write_text(
        NodeLabelMeta(
            source_document="doc",
            model_name="mock-model",
            prompt_version="v1.0",
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
