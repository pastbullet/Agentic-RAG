"""Agent Loop 扩展性验证测试。

验证:
- execute_tool 路由器支持添加新工具无需修改循环核心 (Req 12.2)
- 切换 prompt_file 参数即可改变 LLM 行为模式 (Req 12.1)
- 未知工具名返回错误字典不抛异常 (Req 6.7)
"""

import asyncio

from src.agent import loop
from src.agent.loop import TOOL_REGISTRY, execute_tool, load_system_prompt
from src.models import LLMResponse, ToolCall, TokenUsage


class TestToolRegistryExtensibility:
    """验证 TOOL_REGISTRY 支持动态添加新工具 (Req 12.2)。"""

    def test_add_mock_tool_to_registry(self):
        """添加 mock 工具到 TOOL_REGISTRY 后可通过 execute_tool 调用。"""
        def mock_search_structure(query: str) -> dict:
            return {"results": [f"match for: {query}"], "count": 1}

        # 注册新工具
        TOOL_REGISTRY["search_structure"] = mock_search_structure
        try:
            result = execute_tool("search_structure", {"query": "BFD states"})
            assert "results" in result
            assert result["count"] == 1
            assert "BFD states" in result["results"][0]
        finally:
            # 清理，避免影响其他测试
            del TOOL_REGISTRY["search_structure"]

    def test_add_another_mock_tool(self):
        """添加 get_document_image 工具同样无需修改循环核心。"""
        def mock_get_document_image(doc_name: str, page: int) -> dict:
            return {"image_url": f"/images/{doc_name}/{page}.png", "page": page}

        TOOL_REGISTRY["get_document_image"] = mock_get_document_image
        try:
            result = execute_tool("get_document_image", {"doc_name": "FC-LS.pdf", "page": 5})
            assert result["page"] == 5
            assert "FC-LS.pdf" in result["image_url"]
        finally:
            del TOOL_REGISTRY["get_document_image"]

    def test_registry_is_dict(self):
        """TOOL_REGISTRY 是普通 dict，支持标准字典操作。"""
        assert isinstance(TOOL_REGISTRY, dict)
        assert "get_document_structure" in TOOL_REGISTRY
        assert "get_page_content" in TOOL_REGISTRY


class TestExecuteToolUnknown:
    """验证未知工具名返回错误字典 (Req 6.7)。"""

    def test_unknown_tool_returns_error_dict(self):
        result = execute_tool("nonexistent_tool", {})
        assert isinstance(result, dict)
        assert "error" in result
        assert "nonexistent_tool" in result["error"]

    def test_unknown_tool_no_exception(self):
        """未知工具不抛异常，返回错误字典。"""
        # 如果抛异常，测试会自动失败
        result = execute_tool("totally_fake_tool", {"arg": "value"})
        assert "error" in result


class TestPromptFileSwitching:
    """验证切换 prompt_file 即可改变 LLM 行为模式 (Req 12.1)。"""

    def test_load_qa_prompt(self):
        """加载默认 QA system prompt。"""
        prompt = load_system_prompt("qa_system.txt")
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # QA prompt 应包含问答相关内容
        assert "get_document_structure" in prompt

    def test_load_extraction_prompt(self):
        """加载提取型 system prompt。"""
        prompt = load_system_prompt("extraction_system.txt")
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # 提取 prompt 应包含提取相关内容
        assert "extract" in prompt.lower()

    def test_different_prompts_different_content(self):
        """两个 prompt 文件内容不同，代表不同行为模式。"""
        qa_prompt = load_system_prompt("qa_system.txt")
        extraction_prompt = load_system_prompt("extraction_system.txt")
        assert qa_prompt != extraction_prompt


def test_agentic_rag_emits_progress_events(monkeypatch):
    events: list[dict] = []

    class FakeAdapter:
        def __init__(self, provider: str, model: str):
            self.calls = 0

        async def chat_with_tools(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    has_tool_calls=True,
                    tool_calls=[
                        ToolCall(
                            name="get_page_content",
                            arguments={"doc_name": "doc.pdf", "pages": "3"},
                            id="tc1",
                        )
                    ],
                    usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                    raw_message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "type": "function",
                                "function": {
                                    "name": "get_page_content",
                                    "arguments": "{\"doc_name\": \"doc.pdf\", \"pages\": \"3\"}",
                                },
                            }
                        ],
                    },
                )
            return LLMResponse(
                has_tool_calls=False,
                tool_calls=[],
                text="final",
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                raw_message={"role": "assistant", "content": "final"},
            )

        def make_tool_result_message(self, tool_call_id: str, result: dict) -> dict:
            return {"role": "tool", "tool_call_id": tool_call_id, "content": "{}"}

    monkeypatch.setattr(loop, "LLMAdapter", FakeAdapter)
    monkeypatch.setattr(loop, "load_system_prompt", lambda *_: "system")
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(
        loop,
        "execute_tool",
        lambda name, arguments: {
            "content": [{"page": 3, "text": "ok", "tables": [], "images": []}]
        },
    )
    monkeypatch.setattr(loop, "_save_session", lambda *args, **kwargs: None)

    test_loop = asyncio.new_event_loop()
    try:
        response = test_loop.run_until_complete(
            loop.agentic_rag(
                query="q",
                doc_name="doc.pdf",
                max_turns=5,
                progress_callback=events.append,
            )
        )
    finally:
        test_loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())

    assert response.answer == "final"
    types = [e.get("type") for e in events]
    assert "turn_start" in types
    assert "tool_call" in types
    assert "final_answer" in types
