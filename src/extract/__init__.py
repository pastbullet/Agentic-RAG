"""Protocol extraction package.

Keep package import side effects minimal so submodules like
`src.extract.option_tlv_models` can be imported by `src.models` without
pulling in the full extraction pipeline during interpreter startup.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "DEFAULT_LABEL_PRIORITY",
    "PROMPT_VERSION",
    "PipelineStage",
    "StageResult",
    "apply_overrides",
    "classify_all_nodes",
    "classify_node",
    "get_node_pages",
    "get_node_text",
    "load_or_classify",
    "load_or_classify_async",
    "run_pipeline",
    "summarize_labels",
]


def __getattr__(name: str):
    if name in {
        "DEFAULT_LABEL_PRIORITY",
        "PROMPT_VERSION",
        "apply_overrides",
        "classify_all_nodes",
        "classify_node",
        "load_or_classify",
        "load_or_classify_async",
        "summarize_labels",
    }:
        module = import_module(".classifier", __name__)
        return getattr(module, name)
    if name in {"get_node_pages", "get_node_text"}:
        module = import_module(".content_loader", __name__)
        return getattr(module, name)
    if name in {"PipelineStage", "StageResult", "run_pipeline"}:
        module = import_module(".pipeline", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
