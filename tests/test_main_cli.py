from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from src import main as main_cli
from src.ingest.pipeline import ProcessResult


def _ready_result(doc_name: str = "doc.pdf") -> ProcessResult:
    return ProcessResult(
        doc_name=doc_name,
        doc_stem="doc",
        pdf_path="/tmp/doc.pdf",
        page_index_json="data/out/doc_page_index.json",
        chunks_dir="data/out/chunks_3/doc",
        content_dir="output/docs/doc/json",
        total_pages=3,
        index_built=False,
        structure_built=False,
        content_built=False,
        registered=False,
    )


def test_cli_process_only(monkeypatch, capsys):
    called = {}

    def fake_process_document(pdf_path: str, force: bool = False, model: str | None = None):
        called["pdf_path"] = pdf_path
        called["force"] = force
        called["model"] = model
        return _ready_result("process.pdf")

    monkeypatch.setattr(main_cli, "process_document", fake_process_document)
    monkeypatch.setattr(sys, "argv", ["prog", "--process", "/tmp/process.pdf"])

    asyncio.run(main_cli.main())

    out = capsys.readouterr().out
    assert called["pdf_path"] == "/tmp/process.pdf"
    assert "Document Process Summary" in out
    assert "process.pdf" in out


def test_cli_doc_query(monkeypatch, capsys):
    called = {}

    def fake_ensure_document_ready(doc=None, pdf=None, force=False, model=None):
        called["doc"] = doc
        called["pdf"] = pdf
        return _ready_result("FC-LS.pdf")

    async def fake_agentic_rag(query: str, doc_name: str, model: str | None = None):
        called["query"] = query
        called["doc_name"] = doc_name
        return SimpleNamespace(answer="final answer", trace=[], total_turns=2)

    monkeypatch.setattr(main_cli, "ensure_document_ready", fake_ensure_document_ready)
    monkeypatch.setattr(main_cli, "agentic_rag", fake_agentic_rag)
    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--doc", "FC-LS.pdf", "--query", "FLOGI 是什么？"],
    )

    asyncio.run(main_cli.main())

    out = capsys.readouterr().out
    assert called["doc"] == "FC-LS.pdf"
    assert called["pdf"] is None
    assert called["doc_name"] == "FC-LS.pdf"
    assert "final answer" in out


def test_cli_pdf_query_verbose(monkeypatch, capsys):
    called = {}

    def fake_ensure_document_ready(doc=None, pdf=None, force=False, model=None):
        called["doc"] = doc
        called["pdf"] = pdf
        return _ready_result("new.pdf")

    trace = [
        SimpleNamespace(
            turn=1,
            tool="get_document_structure",
            arguments={"doc_name": "new.pdf", "part": 1},
            result_summary="ok",
        )
    ]

    async def fake_agentic_rag(query: str, doc_name: str, model: str | None = None):
        return SimpleNamespace(answer="done", trace=trace, total_turns=1)

    monkeypatch.setattr(main_cli, "ensure_document_ready", fake_ensure_document_ready)
    monkeypatch.setattr(main_cli, "agentic_rag", fake_agentic_rag)
    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--pdf", "/tmp/new.pdf", "--query", "Q", "--verbose"],
    )

    asyncio.run(main_cli.main())

    out = capsys.readouterr().out
    assert called["pdf"] == "/tmp/new.pdf"
    assert "Tool Call Trace" in out
    assert "done" in out
