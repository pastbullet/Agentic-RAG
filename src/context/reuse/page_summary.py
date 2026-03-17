"""Rule-based page summaries for context reuse."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.context.json_io import JSON_IO


class PageSummaryGenerator:
    """Generate and persist page summaries without extra LLM calls."""

    @staticmethod
    def generate(page_num: int, doc_name: str, text: str, turn_id: str) -> dict:
        source_text = text or ""
        limit = len(source_text) // 2
        summary_text = ""

        if source_text and limit > 0:
            paragraphs = [part.strip() for part in source_text.split("\n\n") if part.strip()]
            if not paragraphs:
                summary_text = source_text[:limit]
            else:
                collected: list[str] = []
                current_len = 0
                for paragraph in paragraphs:
                    addition = paragraph if not collected else f"\n\n{paragraph}"
                    if current_len + len(addition) <= limit:
                        collected.append(paragraph)
                        current_len += len(addition)
                        continue
                    if not collected:
                        collected.append(paragraph[:limit])
                    break
                summary_text = "\n\n".join(collected)

        return {
            "page_num": int(page_num),
            "doc_name": doc_name,
            "summary_text": summary_text,
            "original_length": len(source_text),
            "summary_length": len(summary_text),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_turn_id": turn_id,
        }

    @staticmethod
    def _page_summary_path(session_dir: Path, doc_name: str, page_num: int) -> Path:
        return Path(session_dir) / "documents" / doc_name / "page_summaries" / f"page_{page_num}.json"

    @staticmethod
    def save(session_dir: Path, doc_name: str, summary: dict) -> None:
        page_num = int(summary["page_num"])
        path = PageSummaryGenerator._page_summary_path(session_dir, doc_name, page_num)
        if path.exists():
            return
        JSON_IO.save(path, summary)

    @staticmethod
    def load(session_dir: Path, doc_name: str, page_num: int) -> dict | None:
        path = PageSummaryGenerator._page_summary_path(session_dir, doc_name, page_num)
        return JSON_IO.load(path)

    @staticmethod
    def load_all(session_dir: Path, doc_name: str) -> list[dict]:
        page_dir = Path(session_dir) / "documents" / doc_name / "page_summaries"
        if not page_dir.exists():
            return []

        summaries: list[dict] = []
        for path in sorted(page_dir.glob("page_*.json")):
            payload = JSON_IO.load(path)
            if isinstance(payload, dict):
                summaries.append(payload)
        summaries.sort(key=lambda item: int(item.get("page_num", 0)))
        return summaries
