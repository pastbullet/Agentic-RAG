from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest import pipeline
from src.tools.registry import get_doc_config, is_document_processed


def _write_dummy_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%mock\n")


def _install_fake_builders(monkeypatch, total_pages: int = 3):
    def fake_page_index(pdf_path: str, **kwargs):
        return {
            "doc_name": Path(pdf_path).name,
            "structure": [{"node_id": "1", "title": "Intro", "summary": "s"}],
        }

    def fake_load_root(page_index_json: str):
        payload = json.loads(Path(page_index_json).read_text(encoding="utf-8"))
        return "ROOT", str(payload.get("doc_name", "unknown.pdf"))

    def fake_chunk(root_node, doc_name: str, max_limit: int):
        return [
            {
                "success": True,
                "doc_name": doc_name,
                "structure": [{"node_id": "1", "title": "Intro", "children": []}],
                "pagination": {"part": "1", "has_more": False, "total_parts": "1"},
                "total_parts": "1",
            }
        ]

    def fake_save_parts(parts, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "part_0001.json").write_text(
            json.dumps(parts[0], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "total_parts": 1,
                    "files": ["part_0001.json"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def fake_content_builder(pdf_path: str, output_dir: str = "output", chunk_size: int = 20):
        json_dir = Path(output_dir) / "json"
        json_dir.mkdir(parents=True, exist_ok=True)
        out = json_dir / f"content_1_{total_pages}.json"
        out.write_text(
            json.dumps(
                {
                    "doc_name": Path(pdf_path).name,
                    "chunk_id": f"1-{total_pages}",
                    "start_page": 1,
                    "end_page": total_pages,
                    "pages": [
                        {
                            "page_num": i,
                            "text": f"p{i}",
                            "tables": [],
                            "images": [],
                        }
                        for i in range(1, total_pages + 1)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return [out]

    monkeypatch.setattr(pipeline, "_load_page_index_builder", lambda: fake_page_index)
    monkeypatch.setattr(
        pipeline,
        "_load_structure_helpers",
        lambda: (fake_load_root, fake_chunk, fake_save_parts),
    )
    monkeypatch.setattr(pipeline, "_load_content_builder", lambda: fake_content_builder)


def test_process_document_first_time_builds_and_registers(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _install_fake_builders(monkeypatch, total_pages=5)

    pdf = tmp_path / "incoming" / "new_doc.pdf"
    _write_dummy_pdf(pdf)

    result = pipeline.process_document(str(pdf))

    assert result.doc_name == "new_doc.pdf"
    assert result.index_built is True
    assert result.structure_built is True
    assert result.content_built is True
    assert result.registered is True
    assert result.total_pages == 5

    cfg = get_doc_config("new_doc.pdf")
    assert "error" not in cfg
    assert cfg["chunks_dir"] == "data/out/chunks_3/new_doc"
    assert cfg["content_dir"] == "output/docs/new_doc/json"
    assert cfg["total_pages"] == 5

    assert is_document_processed("new_doc.pdf") is True
    assert (tmp_path / "data" / "out" / "doc_registry.runtime.json").exists()


def test_process_document_skips_rebuild_when_already_processed(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _install_fake_builders(monkeypatch, total_pages=4)

    pdf = tmp_path / "incoming" / "skip_doc.pdf"
    _write_dummy_pdf(pdf)

    first = pipeline.process_document(str(pdf))
    assert first.index_built is True

    def _fail_loader(*args, **kwargs):
        raise AssertionError("builder should not be called when artifacts already exist")

    monkeypatch.setattr(pipeline, "_load_page_index_builder", lambda: _fail_loader)
    monkeypatch.setattr(pipeline, "_load_structure_helpers", _fail_loader)
    monkeypatch.setattr(pipeline, "_load_content_builder", lambda: _fail_loader)

    second = pipeline.process_document(str(pdf), force=False)

    assert second.index_built is False
    assert second.structure_built is False
    assert second.content_built is False


def test_process_document_force_rebuild(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    counters = {"index": 0, "structure": 0, "content": 0}

    def fake_page_index(pdf_path: str, **kwargs):
        counters["index"] += 1
        return {
            "doc_name": Path(pdf_path).name,
            "structure": [{"node_id": "1", "title": "Intro", "summary": "s"}],
        }

    def fake_load_root(page_index_json: str):
        return "ROOT", "force_doc.pdf"

    def fake_chunk(root_node, doc_name: str, max_limit: int):
        counters["structure"] += 1
        return [{"success": True, "doc_name": doc_name, "structure": []}]

    def fake_save_parts(parts, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "part_0001.json").write_text("{}", encoding="utf-8")
        (output_dir / "manifest.json").write_text(
            json.dumps({"total_parts": 1, "files": ["part_0001.json"]}),
            encoding="utf-8",
        )

    def fake_content_builder(pdf_path: str, output_dir: str = "output", chunk_size: int = 20):
        counters["content"] += 1
        json_dir = Path(output_dir) / "json"
        json_dir.mkdir(parents=True, exist_ok=True)
        out = json_dir / "content_1_2.json"
        out.write_text(
            json.dumps(
                {
                    "doc_name": Path(pdf_path).name,
                    "start_page": 1,
                    "end_page": 2,
                    "pages": [{"page_num": 1}, {"page_num": 2}],
                }
            ),
            encoding="utf-8",
        )
        return [out]

    monkeypatch.setattr(pipeline, "_load_page_index_builder", lambda: fake_page_index)
    monkeypatch.setattr(
        pipeline,
        "_load_structure_helpers",
        lambda: (fake_load_root, fake_chunk, fake_save_parts),
    )
    monkeypatch.setattr(pipeline, "_load_content_builder", lambda: fake_content_builder)

    pdf = tmp_path / "incoming" / "force_doc.pdf"
    _write_dummy_pdf(pdf)

    pipeline.process_document(str(pdf), force=False)
    before = counters.copy()

    forced = pipeline.process_document(str(pdf), force=True)
    assert forced.index_built is True
    assert forced.structure_built is True
    assert forced.content_built is True

    assert counters["index"] > before["index"]
    assert counters["structure"] > before["structure"]
    assert counters["content"] > before["content"]


def test_resolve_pdf_for_doc_prefers_data_raw(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "data" / "raw" / "sample.pdf"
    _write_dummy_pdf(target)

    resolved = pipeline.resolve_pdf_for_doc("sample.pdf")
    assert resolved == target.resolve()


def test_resolve_pdf_for_doc_recursive_search(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "nested" / "x" / "deep.pdf"
    _write_dummy_pdf(target)

    resolved = pipeline.resolve_pdf_for_doc("deep.pdf")
    assert resolved == target.resolve()


def test_resolve_pdf_for_doc_ambiguous_match_raises(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_dummy_pdf(tmp_path / "a" / "dup.pdf")
    _write_dummy_pdf(tmp_path / "b" / "dup.pdf")

    with pytest.raises(ValueError, match="Multiple PDF matches"):
        pipeline.resolve_pdf_for_doc("dup.pdf")


def test_process_document_emits_progress_events(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _install_fake_builders(monkeypatch, total_pages=2)

    pdf = tmp_path / "incoming" / "event_doc.pdf"
    _write_dummy_pdf(pdf)

    events: list[dict] = []
    result = pipeline.process_document(str(pdf), progress_callback=events.append)

    assert result.doc_name == "event_doc.pdf"
    stages = [(e.get("type"), e.get("stage")) for e in events]
    assert ("stage_start", "index") in stages
    assert ("stage_done", "index") in stages
    assert ("stage_start", "chunk") in stages
    assert ("stage_done", "chunk") in stages
    assert ("stage_start", "content") in stages
    assert ("stage_done", "content") in stages
    assert ("stage_start", "register") in stages
    assert ("stage_done", "register") in stages
    assert ("stage_done", "ingest") in stages


def test_ensure_document_ready_skip_emits_event(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _install_fake_builders(monkeypatch, total_pages=2)

    pdf = tmp_path / "incoming" / "ready_doc.pdf"
    _write_dummy_pdf(pdf)
    pipeline.process_document(str(pdf))

    events: list[dict] = []
    result = pipeline.ensure_document_ready(doc="ready_doc.pdf", progress_callback=events.append)

    assert result.doc_name == "ready_doc.pdf"
    assert any(
        e.get("type") == "stage_done"
        and e.get("stage") == "ensure_document_ready"
        and e.get("skipped") is True
        for e in events
    )
