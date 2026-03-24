from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass
class Node:
    """目录树节点（最小字段模型）"""

    node_id: str
    title: str
    summary: str = ""
    start_index: int | None = None
    end_index: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    retrieval_disabled: bool | None = None
    children: List["Node"] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Node":
        title = str(
            raw.get("title")
            or raw.get("display_title")
            or raw.get("raw_title")
            or raw.get("full_title")
            or ""
        )
        raw_children: List[Dict[str, Any]] = []
        if isinstance(raw.get("children"), list):
            raw_children.extend([c for c in raw["children"] if isinstance(c, dict)])
        if isinstance(raw.get("nodes"), list):
            raw_children.extend([c for c in raw["nodes"] if isinstance(c, dict)])
        children = [cls.from_dict(child) for child in raw_children]
        return cls(
            node_id=str(raw.get("node_id", "")),
            title=title,
            summary=str(raw.get("summary") or ""),
            start_index=raw.get("start_index") if isinstance(raw.get("start_index"), int) else None,
            end_index=raw.get("end_index") if isinstance(raw.get("end_index"), int) else None,
            start_line=raw.get("start_line") if isinstance(raw.get("start_line"), int) else None,
            end_line=raw.get("end_line") if isinstance(raw.get("end_line"), int) else None,
            retrieval_disabled=(
                raw.get("retrieval_disabled")
                if isinstance(raw.get("retrieval_disabled"), bool)
                else None
            ),
            children=children,
        )


class StructureChunker:
    """
    使用 DFS + 动态祖先补偿切分目录树。
    """

    def __init__(self, max_limit: int) -> None:
        if max_limit <= 0:
            raise ValueError("max_limit must be > 0")
        self.max_limit = max_limit
        self._parts: List[List[Dict[str, Any]]] = []
        self._current_roots: List[Dict[str, Any]] = []
        self._current_size: int = 0
        # 用“路径元组”做 key，避免 node_id 重复导致覆盖
        self._path_to_node: Dict[Tuple[str, ...], Dict[str, Any]] = {}

    def chunk(self, root: Node) -> List[List[Dict[str, Any]]]:
        self._reset_all()
        for child in root.children:
            self._dfs(node=child, ancestor_path=[])
        self._flush_current_part()
        return self._parts

    def chunk_to_tool_responses(self, root: Node, doc_name: str) -> List[Dict[str, Any]]:
        structures = self.chunk(root)
        total_parts = len(structures)
        responses: List[Dict[str, Any]] = []

        for idx, structure in enumerate(structures, start=1):
            # 动态动作引导：按当前分页位置给出前后跳转建议
            options: List[str] = []
            if idx < total_parts:
                options.append(f"Request next part with part: {idx + 1}")
                options.append(f"Jump to last part with part: {total_parts}")
            if idx > 1:
                options.append(f"Request previous part with part: {idx - 1}")
            # 始终保留目录 -> 内容抽取的出口动作
            options.append("Proceed to get_page_content() for specific sections")

            responses.append(
                {
                    "success": True,
                    "doc_name": doc_name,
                    "structure": structure,
                    "next_steps": {
                        "options": options,
                        "summary": f"Showing part {idx} of {total_parts}.",
                    },
                    "pagination": {
                        "part": str(idx),
                        "has_more": idx < total_parts,
                        "total_parts": str(total_parts),
                    },
                    "total_parts": str(total_parts),
                }
            )
        return responses

    def _reset_all(self) -> None:
        self._parts = []
        self._current_roots = []
        self._current_size = 0
        self._path_to_node = {}

    def _reset_current_part(self) -> None:
        self._current_roots = []
        self._current_size = 0
        self._path_to_node = {}

    def _flush_current_part(self) -> None:
        if self._current_roots:
            self._parts.append(self._current_roots)
            self._reset_current_part()

    def _dfs(self, node: Node, ancestor_path: List[Node]) -> None:
        self._append_node(node=node, ancestor_path=ancestor_path)
        for child in node.children:
            self._dfs(node=child, ancestor_path=ancestor_path + [node])

    def _append_node(self, node: Node, ancestor_path: List[Node]) -> None:
        node_payload = self._full_payload(node)
        node_size = self._estimate_size(node_payload)

        # 超限时触发切片，并在新分片注入“祖先骨架”
        if self._current_roots and (self._current_size + node_size > self.max_limit):
            self._flush_current_part()
            self._bootstrap_ancestors(ancestor_path)

        parent = self._ensure_ancestor_path(ancestor_path)
        if parent is None:
            self._current_roots.append(node_payload)
            node_path_key = (node.node_id,)
        else:
            parent["children"].append(node_payload)
            node_path_key = tuple([anc.node_id for anc in ancestor_path] + [node.node_id])

        self._path_to_node[node_path_key] = node_payload
        self._current_size += node_size

    def _bootstrap_ancestors(self, ancestor_path: List[Node]) -> None:
        if not ancestor_path:
            return
        self._ensure_ancestor_path(ancestor_path)

    def _ensure_ancestor_path(self, ancestor_path: List[Node]) -> Dict[str, Any] | None:
        if not ancestor_path:
            return None

        parent: Dict[str, Any] | None = None
        path_key: Tuple[str, ...] = tuple()

        for anc in ancestor_path:
            path_key = path_key + (anc.node_id,)
            existing = self._path_to_node.get(path_key)
            if existing is not None:
                parent = existing
                continue

            # 新分片里缺失的祖先节点，用“骨架节点”补偿
            skeleton = self._ancestor_payload(anc)
            if parent is None:
                self._current_roots.append(skeleton)
            else:
                parent["children"].append(skeleton)

            self._path_to_node[path_key] = skeleton
            self._current_size += self._estimate_size(skeleton)
            parent = skeleton

        return parent

    @staticmethod
    def _ancestor_payload(node: Node) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "node_id": node.node_id,
            "title": node.title,
            # 标记为骨架节点：用于路径补偿，正文不在这一层
            "is_skeleton": True,
            "children": [],
        }
        if node.start_index is not None:
            payload["start_index"] = node.start_index
        if node.end_index is not None:
            payload["end_index"] = node.end_index
        if node.start_line is not None:
            payload["start_line"] = node.start_line
        if node.end_line is not None:
            payload["end_line"] = node.end_line
        if node.retrieval_disabled is not None:
            payload["retrieval_disabled"] = node.retrieval_disabled
        return payload

    @staticmethod
    def _full_payload(node: Node) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "node_id": node.node_id,
            "title": node.title,
            "summary": node.summary,
            "is_skeleton": False,
            "children": [],
        }
        if node.start_index is not None:
            payload["start_index"] = node.start_index
        if node.end_index is not None:
            payload["end_index"] = node.end_index
        if node.start_line is not None:
            payload["start_line"] = node.start_line
        if node.end_line is not None:
            payload["end_line"] = node.end_line
        if node.retrieval_disabled is not None:
            payload["retrieval_disabled"] = node.retrieval_disabled
        return payload

    @staticmethod
    def _estimate_size(node_payload: Dict[str, Any]) -> int:
        # 只统计当前节点元信息，children 在遍历中逐步挂载
        no_children = {k: v for k, v in node_payload.items() if k != "children"}
        return len(json.dumps(no_children, ensure_ascii=False))


def chunk_document_structure(root_node: Node, doc_name: str, max_limit: int) -> List[Dict[str, Any]]:
    chunker = StructureChunker(max_limit=max_limit)
    return chunker.chunk_to_tool_responses(root=root_node, doc_name=doc_name)


def load_root_from_page_index_json(path: str) -> tuple[Node, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    doc_name = str(data.get("doc_name") or Path(path).name)
    structure = data.get("structure")
    if not isinstance(structure, list):
        raise ValueError("Invalid page_index json: `structure` must be a list")

    root = Node(
        node_id="__ROOT__",
        title="ROOT",
        summary="",
        children=[Node.from_dict(item) for item in structure if isinstance(item, dict)],
    )
    return root, doc_name


def build_mock_tree() -> Node:
    return Node(
        node_id="ROOT",
        title="Protocol Spec",
        children=[
            Node(
                node_id="1",
                title="1 Overview",
                summary="Overview of the protocol and compatibility constraints.",
                children=[
                    Node(
                        node_id="1.1",
                        title="1.1 Background",
                        summary="Historical context. " * 8,
                    ),
                    Node(
                        node_id="1.2",
                        title="1.2 Scope",
                        summary="Scope and exclusions. " * 8,
                    ),
                ],
            ),
            Node(
                node_id="2",
                title="2 Packet Format",
                summary="Packet header and payload format.",
                children=[
                    Node(
                        node_id="2.1",
                        title="2.1 Header",
                        summary="Header bit fields and encoding rules. " * 8,
                        children=[
                            Node(
                                node_id="2.1.1",
                                title="2.1.1 Flags",
                                summary="Flag semantics and defaults. " * 8,
                            )
                        ],
                    ),
                    Node(
                        node_id="2.2",
                        title="2.2 Payload",
                        summary="Payload formats for different message categories. " * 8,
                    ),
                ],
            ),
            Node(
                node_id="3",
                title="3 State Machine",
                summary="State transitions and timers. " * 8,
            ),
        ],
    )


def _safe_doc_stem(doc_name: str) -> str:
    stem = Path(doc_name).stem.strip()
    if not stem:
        stem = "document"
    safe = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in stem)
    return safe or "document"


def save_parts_to_folder(parts: List[Dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for idx, part in enumerate(parts, start=1):
        part_path = output_dir / f"part_{idx:04d}.json"
        with part_path.open("w", encoding="utf-8") as f:
            json.dump(part, f, ensure_ascii=False, indent=2)

    manifest = {
        "total_parts": len(parts),
        "files": [f"part_{idx:04d}.json" for idx in range(1, len(parts) + 1)],
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Chunk document structure JSON into part_XXXX.json files."
    )
    parser.add_argument(
        "--input",
        default="data/out/chunk/FC-LS/page_index.json",
        help="Input page_index json path.",
    )
    parser.add_argument(
        "--max-limit",
        type=int,
        default=8000,
        help="Max char budget per part.",
    )
    parser.add_argument(
        "--output-root",
        default="data/out/chunk",
        help="Root output folder.",
    )
    parser.add_argument(
        "--with-mock",
        action="store_true",
        help="Also generate mock output for debugging.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    real_root, real_doc_name = load_root_from_page_index_json(str(input_path))
    real_parts = chunk_document_structure(
        root_node=real_root,
        doc_name=real_doc_name,
        max_limit=args.max_limit,
    )
    real_out_dir = Path(args.output_root) / _safe_doc_stem(real_doc_name)
    save_parts_to_folder(real_parts, real_out_dir)
    print(f"[REAL] total_parts = {len(real_parts)}")
    print(f"[REAL] output_dir = {real_out_dir.resolve()}")

    if args.with_mock:
        mock_root = build_mock_tree()
        mock_parts = chunk_document_structure(
            root_node=mock_root,
            doc_name="mock_protocol.pdf",
            max_limit=360,
        )
        mock_out_dir = Path(args.output_root) / "mock_protocol"
        save_parts_to_folder(mock_parts, mock_out_dir)
        print(f"[MOCK] total_parts = {len(mock_parts)}")
        print(f"[MOCK] output_dir = {mock_out_dir.resolve()}")
