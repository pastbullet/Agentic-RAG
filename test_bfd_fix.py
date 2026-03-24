"""End-to-end test: run page_index on BFD PDF and verify Section 6 children."""
import json
from page_index import page_index

result = page_index("data/raw/rfc5880-BFD.pdf")

# Save output
with open("data/out/rfc5880-BFD_fixed_page_index.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

# Verify Section 6
def find_node(nodes, structure_val):
    for node in nodes:
        s = node.get("structure")
        if s and s.strip() == structure_val:
            return node
        children = node.get("nodes", [])
        r = find_node(children, structure_val)
        if r:
            return r
    return None

structure = result.get("structure", [])
sec6 = find_node(structure, "6")
if sec6:
    children = sec6.get("nodes", [])
    child_structs = [c.get("structure", "").strip() for c in children]
    print(f"Section 6 children ({len(children)}): {child_structs}")
    assert "6.2" in child_structs, "6.2 BFD State Machine is missing!"
    assert "6.8" in child_structs, "6.8 Functional Specifics is missing!"
    print("PASS: Section 6 has all expected children including 6.2 and 6.8")
else:
    print("FAIL: Section 6 not found in output")

# Also check _build_end_index is stripped from output
def check_no_build_field(nodes):
    for node in nodes:
        assert "_build_end_index" not in node, f"_build_end_index leaked in {node.get('title')}"
        if node.get("nodes"):
            check_no_build_field(node["nodes"])

check_no_build_field(structure)
print("PASS: _build_end_index properly stripped from output")
