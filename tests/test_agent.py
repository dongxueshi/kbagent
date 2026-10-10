import json

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from kbagent import agent
from kbagent.config import Settings


class ScriptedModel(FakeMessagesListChatModel):
    """Deterministic SDK integration fixture; no external provider involved."""

    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args, call_id):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}]
    )


def sse(response):
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def test_real_sdk_search_read_and_host_isolation(client, space):
    store = client.app.state.store
    doc = store.save_document(space["id"], "部署手册", "使用 uv sync 安装依赖")
    other = store.create_space("私密库", "")
    private = store.save_document(other["id"], "私密文档", "不得访问的资料")
    model = ScriptedModel(
        responses=[
            call("search_knowledge", {"query": "安装依赖"}, "search"),
            call("read_document", {"document_id": doc["id"]}, "read"),
            call("read_document", {"document_id": private["id"]}, "deny"),
            call("read_file", {"file_path": "/etc/passwd"}, "file"),
            AIMessage(content=f"用 uv sync 安装 [资料:{doc['id']}]"),
        ]
    )
    sources = {}
    graph = agent.build_agent(store, space["id"], Settings(_env_file=None), sources, model)
    result = graph.invoke({"messages": [{"role": "user", "content": "如何安装依赖"}]})
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 4
    assert "uv sync" in tool_messages[1].content
    assert "不属于" in tool_messages[2].content
    assert "root:" not in tool_messages[3].content
    assert list(sources) == [doc["id"]]


def test_agent_sse_citations_and_unverified_citation(client, space, base, monkeypatch):
    store = client.app.state.store
    doc = store.save_document(space["id"], "检索", "中文分词与 BM25 检索")
    unknown = "f" * 32
    model = ScriptedModel(
        responses=[
            call("search_knowledge", {"query": "中文分词"}, "search"),
            AIMessage(content=f"中文分词 [资料:{doc['id']}] 错误引用 [资料:{unknown}]"),
        ]
    )
    original = agent.build_agent
    monkeypatch.setattr(
        agent,
        "build_agent",
        lambda store, sid, settings, sources: original(store, sid, settings, sources, model),
    )
    client.app.state.settings.api_key = "test-not-a-secret"
    client.app.state.settings.model = "test-model"
    cid = client.post(base + "/conversations").json()["id"]
    response = client.post(base + f"/conversations/{cid}/chat", json={"question": "中文分词"})
    result = sse(response)
    assert any("search_knowledge" in e.get("message", "") for e in result)
    answer = result[-1]
    assert answer["type"] == "answer" and answer["mode"] == "agent"
    assert "[1]" in answer["content"] and "[未核实引用]" in answer["content"]
    assert answer["sources"][0]["document_id"] == doc["id"]
    assert len(client.get(base + f"/conversations/{cid}").json()["messages"]) == 2


def test_provider_failure_not_saved_or_downgraded(client, base, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive-provider-error-and-key")

    monkeypatch.setattr(agent, "build_agent", fail)
    client.app.state.settings.api_key = "test-not-a-secret"
    client.app.state.settings.model = "test-model"
    cid = client.post(base + "/conversations").json()["id"]
    response = client.post(base + f"/conversations/{cid}/chat", json={"question": "模型配置"})
    assert sse(response)[-1]["type"] == "error"
    assert "sensitive-provider" not in response.text
    assert client.get(base + f"/conversations/{cid}").json()["messages"] == []
    assert client.get("/api/settings").json()["mode"] == "agent"


def test_agent_graph_tools_require_originals_for_evidence(client, space):
    store = client.app.state.store
    target = store.save_document(space["id"], "目标", "原文内容")
    source = store.save_document(space["id"], "来源", "参考 [[目标]]")
    other = store.create_space("其他库", "")
    private = store.save_document(other["id"], "私密", "不能读取")
    model = ScriptedModel(
        responses=[
            call("get_backlinks", {"document_id": target["id"]}, "backlinks"),
            call("get_related_documents", {"document_id": target["id"]}, "neighbors"),
            call("get_backlinks", {"document_id": private["id"]}, "denied"),
            AIMessage(content="完成关联查询"),
        ]
    )
    evidence = {}
    graph = agent.build_agent(store, space["id"], Settings(_env_file=None), evidence, model)
    result = graph.invoke({"messages": [{"role": "user", "content": "查看关联资料"}]})
    outputs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert json.loads(outputs[0].content)["backlinks"][0]["source_id"] == source["id"]
    assert {n["id"] for n in json.loads(outputs[1].content)["nodes"]} == {
        source["id"],
        target["id"],
    }
    assert "不属于" in outputs[2].content
    assert evidence == {}  # A graph relation is not proof of a document's claims.
