from __future__ import annotations

import pytest

from run_extract_pipeline import _parse_stage_tokens, _print_stage_results
from src.extract.pipeline import PipelineStage, StageResult


def test_parse_stage_tokens_expands_all_and_process():
    run_process, stages = _parse_stage_tokens("all")

    assert run_process is True
    assert stages == [
        PipelineStage.CLASSIFY,
        PipelineStage.EXTRACT,
        PipelineStage.MERGE,
        PipelineStage.CODEGEN,
        PipelineStage.VERIFY,
    ]


def test_parse_stage_tokens_deduplicates_and_preserves_order():
    run_process, stages = _parse_stage_tokens("process,merge,codegen,merge,verify")

    assert run_process is True
    assert stages == [
        PipelineStage.MERGE,
        PipelineStage.CODEGEN,
        PipelineStage.VERIFY,
    ]


def test_parse_stage_tokens_rejects_invalid_stage():
    with pytest.raises(ValueError):
        _parse_stage_tokens("process,merge,shipit")


def test_print_stage_results_includes_classifier_sanity_metrics(capsys):
    _print_stage_results(
        [
            StageResult(
                stage=PipelineStage.CLASSIFY,
                success=True,
                duration_sec=1.23,
                node_count=42,
                data={
                    "state_machine_sanity_downgrade_count": 3,
                    "state_machine_sanity_downgrade_by_reason": {
                        "numbered_check": 2,
                        "call_procedure": 1,
                    },
                    "fsm_segment_count": 2,
                    "fsm_segment_reclassified_count": 1,
                    "fsm_segment_updated_node_count": 1,
                    "fsm_segment_skipped_count": 1,
                    "fsm_segment_skip_reasons": {"over_limit": 1},
                },
            )
        ]
    )

    out = capsys.readouterr().out
    assert "state_machine_sanity_downgrade_count: 3" in out
    assert "state_machine_sanity_downgrade_by_reason: {'numbered_check': 2, 'call_procedure': 1}" in out
    assert "fsm_segment_count: 2" in out
    assert "fsm_segment_reclassified_count: 1" in out
    assert "fsm_segment_updated_node_count: 1" in out
    assert "fsm_segment_skipped_count: 1" in out
    assert "fsm_segment_skip_reasons: {'over_limit': 1}" in out
