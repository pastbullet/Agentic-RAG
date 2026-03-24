"""Debug script to verify the _build_end_index fix for BFD page index."""
import sys
import json
sys.path.insert(0, '.')
from utils import post_processing, list_to_tree

# Simulate the flat list after check_title_appearance_in_start_concurrent
items = [
    {"title": "Abstract", "structure": None, "physical_index": 1, "start_line": 5, "end_line": 10, "retrieval_disabled": True, "appear_start": "yes"},
    {"title": "Status of This Memo", "structure": None, "physical_index": 1, "start_line": 11, "end_line": 20, "retrieval_disabled": True, "appear_start": "yes"},
    {"title": "Copyright Notice", "structure": None, "physical_index": 1, "start_line": 21, "end_line": 33, "retrieval_disabled": True, "appear_start": "yes"},
    {"title": "Table of Contents", "structure": None, "physical_index": 2, "start_line": 1, "end_line": 9, "toc_span": True, "retrieval_disabled": True, "appear_start": "yes"},
    {"structure": "1", "title": "Introduction", "physical_index": 3, "appear_start": "yes"},
    {"structure": "1.1", "title": "Conventions Used in This Document", "physical_index": 4, "appear_start": "yes"},
    {"structure": "2", "title": "Design", "physical_index": 4, "appear_start": "no"},
    {"structure": "3", "title": "Protocol Overview", "physical_index": 5, "appear_start": "yes"},
    {"structure": "3.1", "title": "Addressing and Session Establishment", "physical_index": 5, "appear_start": "no"},
    {"structure": "3.2", "title": "Operating Modes", "physical_index": 5, "appear_start": "no"},
    {"structure": "4", "title": "BFD Control Packet Format", "physical_index": 7, "appear_start": "yes"},
    {"structure": "4.1", "title": "Generic BFD Control Packet Format", "physical_index": 7, "appear_start": "no"},
    {"structure": "4.2", "title": "Simple Password Authentication Section Format", "physical_index": 11, "appear_start": "yes"},
    {"structure": "4.3", "title": "Keyed MD5 Authentication Section Format", "physical_index": 11, "appear_start": "no"},
    {"structure": "4.4", "title": "Keyed SHA1 Authentication Section Format", "physical_index": 13, "appear_start": "yes"},
    {"structure": "5", "title": "BFD Echo Packet Format", "physical_index": 14, "appear_start": "yes"},
    {"structure": "6", "title": "Elements of Procedure", "physical_index": 14, "appear_start": "no"},
    {"structure": "6.1", "title": "Overview", "physical_index": 14, "appear_start": "no"},
    {"structure": "6.2", "title": "BFD State Machine", "physical_index": 16, "appear_start": "yes"},
    {"structure": "6.3", "title": "Demultiplexing and the Discriminator Fields", "physical_index": 17, "appear_start": "no"},
    {"structure": "6.4", "title": "The Echo Function and Asymmetry", "physical_index": 18, "appear_start": "yes"},
    {"structure": "6.5", "title": "The Poll Sequence", "physical_index": 19, "appear_start": "yes"},
    {"structure": "6.6", "title": "Demand Mode", "physical_index": 19, "appear_start": "no"},
    {"structure": "6.7", "title": "Authentication", "physical_index": 21, "appear_start": "yes"},
    {"structure": "6.7.1", "title": "Enabling and Disabling Authentication", "physical_index": 21, "appear_start": "no"},
    {"structure": "6.7.2", "title": "Simple Password Authentication", "physical_index": 22, "appear_start": "yes"},
    {"structure": "6.7.3", "title": "Keyed MD5 Authentication", "physical_index": 23, "appear_start": "yes"},
    {"structure": "6.7.4", "title": "Keyed SHA1 Authentication", "physical_index": 25, "appear_start": "yes"},
    {"structure": "6.8", "title": "Functional Specifics", "physical_index": 27, "appear_start": "yes"},
    {"structure": "6.8.1", "title": "State Variables", "physical_index": 27, "appear_start": "no"},
    {"structure": "6.8.2", "title": "Timer Negotiation", "physical_index": 30, "appear_start": "yes"},
    {"structure": "6.8.3", "title": "Timer Manipulation", "physical_index": 31, "appear_start": "yes"},
    {"structure": "6.8.4", "title": "Calculating the Detection Time", "physical_index": 32, "appear_start": "yes"},
    {"structure": "6.8.5", "title": "Detecting Failures with the Echo Function", "physical_index": 33, "appear_start": "yes"},
    {"structure": "6.8.6", "title": "Reception of BFD Control Packets", "physical_index": 33, "appear_start": "no"},
    {"structure": "6.8.7", "title": "Transmitting BFD Control Packets", "physical_index": 36, "appear_start": "yes"},
    {"structure": "6.8.8", "title": "Reception of BFD Echo Packets", "physical_index": 39, "appear_start": "yes"},
    {"structure": "6.8.9", "title": "Transmission of BFD Echo Packets", "physical_index": 39, "appear_start": "no"},
    {"structure": "6.8.10", "title": "Min Rx Interval Change", "physical_index": 40, "appear_start": "yes"},
    {"structure": "6.8.11", "title": "Min Tx Interval Change", "physical_index": 40, "appear_start": "no"},
    {"structure": "6.8.12", "title": "Detect Multiplier Change", "physical_index": 40, "appear_start": "no"},
    {"structure": "6.8.13", "title": "Enabling or Disabling The Echo Function", "physical_index": 40, "appear_start": "no"},
    {"structure": "6.8.14", "title": "Enabling or Disabling Demand Mode", "physical_index": 40, "appear_start": "no"},
    {"structure": "6.8.15", "title": "Forwarding Plane Reset", "physical_index": 41, "appear_start": "yes"},
    {"structure": "6.8.16", "title": "Administrative Control", "physical_index": 41, "appear_start": "no"},
    {"structure": "6.8.17", "title": "Concatenated Paths", "physical_index": 41, "appear_start": "no"},
    {"structure": "6.8.18", "title": "Holding Down Sessions", "physical_index": 42, "appear_start": "yes"},
    {"structure": "7", "title": "Operational Considerations", "physical_index": 43, "appear_start": "yes"},
    {"structure": "8", "title": "IANA Considerations", "physical_index": 44, "appear_start": "yes"},
    {"structure": "9", "title": "Security Considerations", "physical_index": 45, "appear_start": "yes"},
    {"structure": "10", "title": "References", "physical_index": 46, "appear_start": "yes"},
    {"structure": "10.1", "title": "Normative References", "physical_index": 46, "appear_start": "no"},
    {"structure": "10.2", "title": "Informative References", "physical_index": 47, "appear_start": "yes"},
    {"structure": None, "title": "Appendix A. Backward Compatibility", "physical_index": 48, "appear_start": "yes"},
    {"structure": None, "title": "Appendix B. Contributors", "physical_index": 48, "appear_start": "no"},
    {"structure": None, "title": "Appendix C. Acknowledgments", "physical_index": 49, "appear_start": "yes"},
]

tree = post_processing(items, 49)

def find_node(nodes, structure_val):
    for node in nodes:
        if node.get("structure") == structure_val:
            return node
        children = node.get("nodes", [])
        result = find_node(children, structure_val)
        if result:
            return result
    return None

print("=== _build_end_index 验证 ===")
for s in ["5", "6", "6.1", "6.7", "6.8", "7", "10"]:
    n = find_node(tree, s)
    if n:
        bei = n.get("_build_end_index", "N/A")
        print(f"  {s} {n['title'][:40]:40s}  end_index={n['end_index']:3d}  _build_end_index={bei}")

print()
section6 = find_node(tree, "6")
if section6:
    print(f"Section 6: end_index={section6['end_index']}, _build_end_index={section6.get('_build_end_index', 'N/A')}")
    children = section6.get("nodes", [])
    print(f"Section 6 children count: {len(children)}")
    for child in children:
        bei = child.get("_build_end_index", "N/A")
        print(f"  {child.get('structure')} {child.get('title')[:40]:40s}  end={child.get('end_index'):3d}  build_end={bei}")
        for sc in child.get("nodes", []):
            bei2 = sc.get("_build_end_index", "N/A")
            print(f"    {sc.get('structure')} {sc.get('title')[:40]:40s}  end={sc.get('end_index'):3d}  build_end={bei2}")
else:
    print("Section 6 NOT FOUND!")
