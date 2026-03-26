from __future__ import annotations

import pytest

from run_extract_pipeline import _parse_stage_tokens
from src.extract.pipeline import PipelineStage


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
