"""Regression test: run page_index on FC-LS PDF and verify basic structure."""
import json
from page_index import page_index

result = page_index("data/raw/FC-LS.pdf",
                     if_add_node_summary="no",
                     if_add_doc_description="no",
                     if_add_node_text="no")

with open("data/out/FC-LS_regression_page_index.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

structure = result.get("structure", [])
print(f"Top-level nodes: {len(structure)}")
for node in structure[:10]:
    title = node.get("title", "")[:60]
    children = node.get("nodes", [])
    print(f"  {title:60s}  children={len(children)}")

# Check _build_end_index is stripped
def check_no_build_field(nodes):
    for node in nodes:
        assert "_build_end_index" not in node, f"_build_end_index leaked in {node.get('title')}"
        if node.get("nodes"):
            check_no_build_field(node["nodes"])

check_no_build_field(structure)
print("\nPASS: _build_end_index properly stripped from FC-LS output")
print("PASS: FC-LS regression test completed")
