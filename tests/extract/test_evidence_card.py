"""Tests for evidence-card generation."""

from __future__ import annotations

import json

import pytest

from src.extract.evidence_card import generate_evidence_cards
from src.models import LLMResponse


class EvidenceLLM:
    async def chat_with_tools(self, messages, tools):
        return LLMResponse(
            text=json.dumps(
                {
                    "common_evidence": ["shared field: Auth Type"],
                    "differing_evidence": ["password vs digest"],
                    "naming_relation": "partial overlap",
                    "wording_vs_substance": "substantive difference",
                    "llm_confidence": 0.61,
                    "unresolved_conflicts": ["needs reviewer"],
                    "decision": "merge",
                }
            )
        )


def _write_content(tmp_path, doc_stem: str) -> str:
    content_dir = tmp_path / "data" / "out" / "content" / doc_stem / "json"
    content_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "pages": [
            {"page_num": 1, "text": "left page text", "tables": [], "images": []},
            {"page_num": 2, "text": "right page text", "tables": [], "images": []},
        ]
    }
    (content_dir / "content_1_20.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(content_dir)


@pytest.mark.asyncio
async def test_generate_evidence_cards_contains_required_fields_and_no_decision_field(tmp_path):
    content_dir = _write_content(tmp_path, "rfc5880-BFD")
    near_miss_report = {
        "state_machine_near_misses": [
            {
                "pair": [0, 1],
                "names": ["A", "B"],
                "left": {"name": "A", "source_pages": [1]},
                "right": {"name": "B", "source_pages": [2]},
                "scores": {"name": 0.5, "states": 0.4, "transitions": 0.3},
                "diff": {},
            }
        ],
        "message_near_misses": [],
    }

    cards = await generate_evidence_cards(EvidenceLLM(), near_miss_report, content_dir=content_dir)

    assert len(cards) == 1
    payload = cards[0].model_dump()
    assert payload["pair_id"] == [0, 1]
    assert payload["object_type"] == "state_machine"
    assert "common_evidence" in payload
    assert "differing_evidence" in payload
    assert "naming_relation" in payload
    assert "wording_vs_substance" in payload
    assert "llm_confidence" in payload
    assert "unresolved_conflicts" in payload
    assert "decision" not in payload
    assert "merge" not in payload


@pytest.mark.asyncio
async def test_generate_evidence_cards_fallback_without_llm(tmp_path):
    content_dir = _write_content(tmp_path, "rfc5880-BFD")
    near_miss_report = {
        "state_machine_near_misses": [],
        "message_near_misses": [
            {
                "pair": [0, 1],
                "names": ["Message A", "Message B"],
                "left": {"name": "Message A", "source_pages": [1]},
                "right": {"name": "Message B", "source_pages": [2]},
                "name_similarity": 0.4,
                "field_jaccard": 0.5,
                "diff": {"fields_only_left": ["a"], "fields_only_right": ["b"]},
            }
        ],
    }

    cards = await generate_evidence_cards(None, near_miss_report, content_dir=content_dir)
    assert len(cards) == 1
    assert cards[0].object_type == "message"
    assert cards[0].llm_confidence == 0.0
