from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import fitz  # PyMuPDF
import pdfplumber


def _clean_cell(cell: Any) -> str:
    """清洗表格单元格：None -> 空字符串，并统一换行表现。"""
    if cell is None:
        return ""
    text = str(cell)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "<br>")
    # Markdown 中竖线需要转义，避免破坏列结构
    return text.replace("|", r"\|").strip()


def table_to_markdown(table: List[List[Any]]) -> str:
    """
    将 pdfplumber 提取到的二维表格转为 Markdown。
    - 首行作为表头；
    - None 单元格会被转为空字符串；
    - 行列不足时自动补齐，保证输出稳定。
    """
    if not table:
        return ""

    normalized_rows: List[List[str]] = []
    max_cols = max((len(row) for row in table if row is not None), default=0)
    if max_cols == 0:
        return ""

    for raw_row in table:
        row = raw_row or []
        cleaned = [_clean_cell(cell) for cell in row]
        if len(cleaned) < max_cols:
            cleaned.extend([""] * (max_cols - len(cleaned)))
        normalized_rows.append(cleaned)

    header = normalized_rows[0]
    body = normalized_rows[1:]
    divider = ["---"] * max_cols

    md_lines = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(divider)} |",
    ]
    for row in body:
        md_lines.append(f"| {' | '.join(row)} |")
    return "\n".join(md_lines)


def extract_images_by_page(pdf_path: Path, images_dir: Path, rel_base_dir: Path) -> Dict[int, List[str]]:
    """
    提取 PDF 图片并按页号索引。
    返回：{page_num: [relative_image_path, ...]}
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    page_to_images: Dict[int, List[str]] = {}

    with fitz.open(pdf_path) as doc:
        for page_idx in range(doc.page_count):
            page_num = page_idx + 1
            page = doc.load_page(page_idx)
            image_entries = page.get_images(full=True)

            image_paths: List[str] = []
            for img_i, image_entry in enumerate(image_entries, start=1):
                try:
                    xref = image_entry[0]
                    image_info = doc.extract_image(xref)
                    image_bytes = image_info.get("image")
                    image_ext = image_info.get("ext", "png")
                    if not image_bytes:
                        continue

                    image_name = f"page_{page_num}_img_{img_i}.{image_ext}"
                    image_path = images_dir / image_name
                    with image_path.open("wb") as f:
                        f.write(image_bytes)

                    rel_path = os.path.relpath(image_path, start=rel_base_dir).replace("\\", "/")
                    image_paths.append(rel_path)
                except Exception:
                    # 单张图片解析失败不影响整页处理
                    continue

            page_to_images[page_num] = image_paths
    return page_to_images


def parse_pages_with_pdfplumber(
    pdf_path: Path,
    page_to_images: Dict[int, List[str]],
) -> List[Dict[str, Any]]:
    """逐页提取 text + tables + images。"""
    parsed_pages: List[Dict[str, Any]] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            page_num = page_idx + 1

            # 文本提取失败时容错为空字符串
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            # 表格提取失败时容错为空列表
            markdown_tables: List[str] = []
            try:
                raw_tables = page.extract_tables() or []
                for table in raw_tables:
                    if not table:
                        continue
                    md = table_to_markdown(table)
                    if md:
                        markdown_tables.append(md)
            except Exception:
                markdown_tables = []

            parsed_pages.append(
                {
                    "page_num": page_num,
                    "text": text,
                    "tables": markdown_tables,
                    "images": page_to_images.get(page_num, []),
                }
            )

    return parsed_pages


def write_chunk_jsons(
    pages: List[Dict[str, Any]],
    doc_name: str,
    json_dir: Path,
    chunk_size: int = 20,
) -> List[Path]:
    """按固定窗口（默认20页）切分并写出多个 JSON。"""
    json_dir.mkdir(parents=True, exist_ok=True)
    chunk_files: List[Path] = []

    total_pages = len(pages)
    for start_page in range(1, total_pages + 1, chunk_size):
        end_page = min(start_page + chunk_size - 1, total_pages)
        chunk_pages = pages[start_page - 1 : end_page]
        payload = {
            "doc_name": doc_name,
            "chunk_id": f"{start_page}-{end_page}",
            "start_page": start_page,
            "end_page": end_page,
            "pages": chunk_pages,
        }
        out_path = json_dir / f"content_{start_page}_{end_page}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        chunk_files.append(out_path)

    return chunk_files


def build_content_db(
    pdf_path: str,
    output_dir: str = "data/out/content/document",
    chunk_size: int = 20,
) -> List[Path]:
    """
    主流程：
    1) 提取图片到 <output_dir>/images
    2) 提取每页文本和表格
    3) 按20页切分写入 <output_dir>/json
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    source_pdf = Path(pdf_path).expanduser().resolve()
    if not source_pdf.exists():
        raise FileNotFoundError(f"PDF not found: {source_pdf}")

    output_root = Path(output_dir).expanduser().resolve()
    json_dir = output_root / "json"
    images_dir = output_root / "images"

    # 图片相对路径基准目录：与 output_dir 同级
    rel_base_dir = output_root.parent

    page_to_images = extract_images_by_page(
        pdf_path=source_pdf,
        images_dir=images_dir,
        rel_base_dir=rel_base_dir,
    )
    parsed_pages = parse_pages_with_pdfplumber(source_pdf, page_to_images)
    chunk_files = write_chunk_jsons(parsed_pages, source_pdf.name, json_dir, chunk_size=chunk_size)
    return chunk_files


def _pick_default_pdf() -> str:
    candidates = [
        Path("data/raw/FC-LS.pdf"),
        Path("data/FC-LS.pdf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "data/raw/FC-LS.pdf"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build content DB from PDF: text + markdown tables + extracted images."
    )
    parser.add_argument(
        "--pdf-path",
        default=_pick_default_pdf(),
        help="Path to source PDF (default: data/raw/FC-LS.pdf if exists).",
    )
    parser.add_argument(
        "--output-dir",
        default="data/out/content/FC-LS",
        help="Output root dir containing json/ and images/.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=20,
        help="Chunk size by pages (default: 20).",
    )
    args = parser.parse_args()

    files = build_content_db(
        pdf_path=args.pdf_path,
        output_dir=args.output_dir,
        chunk_size=args.chunk_size,
    )
    print(f"Done. Generated {len(files)} chunk files.")
    if files:
        print(f"First chunk: {files[0]}")
        print(f"Last chunk: {files[-1]}")
