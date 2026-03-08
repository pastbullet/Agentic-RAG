"""Citation 模块单元测试。"""

from src.agent.citation import clean_answer, extract_citations, validate_citations
from src.models import Citation


class TestExtractCitations:
    """extract_citations 测试。"""

    def test_single_cite(self):
        answer = 'BFD uses UDP <cite doc="rfc5880-BFD.pdf" page="7"/>'
        result = extract_citations(answer)
        assert len(result) == 1
        assert result[0].doc_name == "rfc5880-BFD.pdf"
        assert result[0].page == 7

    def test_multiple_cites(self):
        answer = (
            'Field A is defined <cite doc="FC-LS.pdf" page="10"/> '
            'and Field B is on <cite doc="FC-LS.pdf" page="20"/>.'
        )
        result = extract_citations(answer)
        assert len(result) == 2
        assert result[0].page == 10
        assert result[1].page == 20

    def test_no_cites(self):
        answer = "This answer has no citations at all."
        result = extract_citations(answer)
        assert result == []

    def test_context_extraction(self):
        answer = 'Some surrounding text here <cite doc="doc.pdf" page="5"/>'
        result = extract_citations(answer)
        assert len(result) == 1
        assert "surrounding text" in result[0].context

    def test_different_docs(self):
        answer = (
            '<cite doc="a.pdf" page="1"/> '
            '<cite doc="b.pdf" page="2"/>'
        )
        result = extract_citations(answer)
        assert result[0].doc_name == "a.pdf"
        assert result[1].doc_name == "b.pdf"


class TestValidateCitations:
    """validate_citations 测试。"""

    def test_all_valid(self):
        citations = [
            Citation(doc_name="doc.pdf", page=7),
            Citation(doc_name="doc.pdf", page=10),
        ]
        warnings = validate_citations(citations, [7, 10, 15])
        assert warnings == []

    def test_some_invalid(self):
        citations = [
            Citation(doc_name="doc.pdf", page=7),
            Citation(doc_name="doc.pdf", page=99),
        ]
        warnings = validate_citations(citations, [7, 10])
        assert len(warnings) == 1
        assert "99" in warnings[0]

    def test_all_invalid(self):
        citations = [
            Citation(doc_name="doc.pdf", page=50),
            Citation(doc_name="doc.pdf", page=60),
        ]
        warnings = validate_citations(citations, [1, 2, 3])
        assert len(warnings) == 2

    def test_empty_citations(self):
        warnings = validate_citations([], [1, 2, 3])
        assert warnings == []

    def test_empty_retrieved(self):
        citations = [Citation(doc_name="doc.pdf", page=5)]
        warnings = validate_citations(citations, [])
        assert len(warnings) == 1


class TestCleanAnswer:
    """clean_answer 测试。"""

    def test_removes_cite_tags(self):
        answer = 'Hello <cite doc="d.pdf" page="1"/> world'
        assert clean_answer(answer) == "Hello  world"

    def test_multiple_tags(self):
        answer = '<cite doc="a.pdf" page="1"/>text<cite doc="b.pdf" page="2"/>'
        assert clean_answer(answer) == "text"

    def test_no_tags(self):
        answer = "Plain text without citations."
        assert clean_answer(answer) == answer

    def test_preserves_non_tag_content(self):
        answer = 'Before <cite doc="x.pdf" page="3"/> after'
        cleaned = clean_answer(answer)
        assert "Before" in cleaned
        assert "after" in cleaned
        assert "<cite" not in cleaned
