import asyncio
from types import SimpleNamespace

from page_index import process_large_node_recursively


def _opt():
    return SimpleNamespace(
        max_page_num_each_node=20,
        max_token_num_each_node=10000,
        model="gpt-4o-2024-11-20",
    )


def test_existing_children_are_not_overwritten_by_truncated_parent_span():
    page_list = [
        ("6 Elements of Procedure\n6.1 Overview\nintro", 50),
        ("body text", 50),
        ("6.2 BFD State Machine\nbody", 50),
        ("body text", 50),
    ]
    node = {
        "title": "6 Elements of Procedure",
        "structure": "6",
        "start_index": 1,
        "end_index": 1,
        "nodes": [
            {
                "title": "6.1 Overview",
                "structure": "6.1",
                "start_index": 1,
                "end_index": 2,
            },
            {
                "title": "6.2 BFD State Machine",
                "structure": "6.2",
                "start_index": 3,
                "end_index": 4,
            },
        ],
    }

    result = asyncio.run(process_large_node_recursively(node, page_list, opt=_opt(), logger=None))

    assert [child["structure"] for child in result["nodes"]] == ["6.1", "6.2"]
    assert result["end_index"] == 1


def test_toc_like_nodes_are_not_refined_from_toc_page_text():
    page_list = [
        ("Table of Contents\n1 Introduction\n6 Elements of Procedure\n6.1 Overview", 50),
    ]
    node = {
        "title": "Table of Contents",
        "start_index": 1,
        "end_index": 1,
        "retrieval_disabled": True,
        "toc_span": True,
    }

    result = asyncio.run(process_large_node_recursively(node, page_list, opt=_opt(), logger=None))

    assert "nodes" not in result
