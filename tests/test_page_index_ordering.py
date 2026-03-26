from page_index import extract_sub_toc_by_headings


def test_extract_sub_toc_by_headings_sorts_numeric_section_ids_naturally():
    page_list = [
        (
            "\n".join(
                [
                    "2.8 Data Communication",
                    "2.10 Robustness Principle",
                    "2.9 Precedence and Security",
                ]
            ),
            100,
        )
    ]

    result = extract_sub_toc_by_headings(page_list, start_index=18, parent_structure="2")

    assert [item["structure"] for item in result] == ["2.8", "2.9", "2.10"]
    assert [item["title"] for item in result] == [
        "Data Communication",
        "Precedence and Security",
        "Robustness Principle",
    ]
