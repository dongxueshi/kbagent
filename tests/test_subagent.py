from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from kbagent.agent import build_agent
from kbagent.config import Settings


class DelegatingModel(BaseChatModel):
    """Drive a real task-tool delegation and retrieval without a provider API."""

    tools_available: list[str] = []

    @property
    def _llm_type(self):
        return "delegation-test"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"tools_available": [tool.name for tool in tools]})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        last = messages[-1]
        if "task" in self.tools_available:
            if isinstance(last, ToolMessage) and last.name == "task":
                message = AIMessage(content="子智能体已完成调查：" + last.content)
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "subagent_type": "knowledge-researcher",
                                "description": "检索安装依赖资料",
                            },
                            "id": "delegate",
                            "type": "tool_call",
                        }
                    ],
                )
        elif isinstance(last, ToolMessage):
            message = AIMessage(content="找到了安装依赖资料。")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": "安装依赖"},
                        "id": "sub-search",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_real_subagent_delegation(client, space):
    store = client.app.state.store
    doc = store.save_document(space["id"], "安装", "安装依赖使用 uv sync")
    sources = {}
    graph = build_agent(
        store, space["id"], Settings(_env_file=None), sources, model=DelegatingModel()
    )
    result = graph.invoke(
        {"messages": [{"role": "user", "content": "调查如何安装依赖"}]}, {"recursion_limit": 30}
    )
    assert any(isinstance(m, ToolMessage) and m.name == "task" for m in result["messages"])
    assert "完成调查" in result["messages"][-1].content
    assert list(sources) == [doc["id"]]
