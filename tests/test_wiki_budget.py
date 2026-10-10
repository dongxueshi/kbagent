import asyncio

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from kbagent.repositories import RepositoryFailure
from kbagent.repository_wiki import (
    generate_overview,
    prioritized_files,
    synthesize_wiki,
)
from tests.test_agent import ScriptedModel, call
from tests.test_repositories import add_local, checkout

__all__ = ["checkout"]


class LoopingModel(ScriptedModel):
    document_id: str
    cited_id: str = ""
    research_calls: int = 0
    synthesis_calls: int = 0
    request_tools_on_finish: bool = False
    no_evidence: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if "只整理已给定源码" in str(messages[0].content):
            self.synthesis_calls += 1
            result = (
                call("read_code", {"document_id": self.document_id}, "finish-loop")
                if self.request_tools_on_finish
                else AIMessage(
                    content=f"版本判断在订单函数中。[资料:{self.cited_id or self.document_id}]"
                )
            )
        else:
            self.research_calls += 1
            result = call(
                "list_repository_files" if self.no_evidence else "read_code",
                {"repository_id": "unknown"}
                if self.no_evidence
                else {"document_id": self.document_id},
                str(self.research_calls),
            )
        return ChatResult(generations=[ChatGeneration(message=result)])


def test_real_sdk_step_budget_finishes_from_actual_source(client, base, checkout):
    repo = add_local(client, base, checkout)
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    settings = client.app.state.settings
    settings.wiki_max_steps = 8
    model = LoopingModel(document_id=doc, responses=[AIMessage(content="unused")])
    asyncio.run(generate_overview(client.app.state.store, settings, repo, model))
    assert model.research_calls >= 2 and model.synthesis_calls == 1
    wiki = next(
        f
        for f in client.app.state.store.repository_files(repo["space_id"], repo["id"])
        if f["kind"] == "ai_wiki"
    )
    content = client.get(base + "/documents/" + wiki["document_id"]).json()["content"]
    assert "研究阶段达到步骤预算" in content and f"[[id:{doc}|" in content


def test_file_budget_finishes_without_reading_every_file(client, base, checkout):
    repo = add_local(client, base, checkout)
    docs = [f["document_id"] for f in repo["files"] if f["kind"] == "code"]
    settings = client.app.state.settings
    settings.wiki_max_files = 1
    model = ScriptedModel(
        responses=[
            call("read_code", {"document_id": docs[0]}, "first"),
            call("read_code", {"document_id": docs[1]}, "over-budget"),
            AIMessage(content=f"基于已读文件整理。[资料:{docs[0]}]"),
        ]
    )
    asyncio.run(generate_overview(client.app.state.store, settings, repo, model))
    wiki = next(
        f
        for f in client.app.state.store.repository_files(repo["space_id"], repo["id"])
        if f["kind"] == "ai_wiki"
    )
    assert (
        "研究阶段达到文件预算"
        in client.get(base + "/documents/" + wiki["document_id"]).json()["content"]
    )


@pytest.mark.parametrize("mode", ["unknown_citation", "more_tools", "no_evidence"])
def test_exhausted_research_never_saves_ungrounded_or_looping_synthesis(
    client, base, checkout, mode
):
    repo = add_local(client, base, checkout)
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    settings = client.app.state.settings
    settings.wiki_max_steps = 8
    model = LoopingModel(
        document_id=doc,
        responses=[AIMessage(content="unused")],
        cited_id="f" * 32 if mode == "unknown_citation" else "",
        request_tools_on_finish=mode == "more_tools",
        no_evidence=mode == "no_evidence",
    )
    with pytest.raises(RepositoryFailure):
        asyncio.run(generate_overview(client.app.state.store, settings, repo, model))
    assert model.synthesis_calls == (0 if mode == "no_evidence" else 1)
    assert not any(
        f["kind"] == "ai_wiki"
        for f in client.app.state.store.repository_files(repo["space_id"], repo["id"])
    )


def test_source_bundle_is_bounded_and_only_supplied_ids_can_be_cited():
    from kbagent.config import Settings
    from kbagent.repository_wiki import normalize_citations

    cid = "a" * 32
    evidence = {
        cid: dict(
            excerpt="x" * 10000, repository=dict(kind="code", path="main.py", commit_sha="b" * 40)
        )
    }

    class CaptureModel:
        async def ainvoke(self, messages):
            assert len(messages[1].content) < 5000
            return AIMessage(content=f"源码说明。[资料:{cid}]")

    content, sources = asyncio.run(
        synthesize_wiki(
            CaptureModel(),
            evidence,
            dict(name="test", commit_sha="b" * 40),
            Settings(_env_file=None, wiki_source_chars=4000),
        )
    )
    assert list(sources) == [cid] and normalize_citations(content, sources)[1] is None


def test_priority_starts_with_manifests_and_entry_points():
    files = [
        dict(path=p) for p in ["src/utils/helpers.py", "src/main.py", "README.md", "pyproject.toml"]
    ]
    paths = [f["path"] for f in prioritized_files(files)]
    assert paths[:2] == ["README.md", "pyproject.toml"] and paths[2] == "src/main.py"


def test_research_timeout_reserves_time_for_verified_synthesis(client, base, checkout, monkeypatch):
    from kbagent import repository_wiki

    repo = add_local(client, base, checkout)
    store, settings = client.app.state.store, client.app.state.settings
    doc_id = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    doc = store.document(repo["space_id"], doc_id)

    class SlowResearch:
        async def ainvoke(self, *args, **kwargs):
            await asyncio.Event().wait()

    def build(*args, **kwargs):
        evidence = args[3]
        evidence[doc_id] = dict(excerpt=doc["content"], repository=doc["repository"])
        return SlowResearch()

    monkeypatch.setattr(repository_wiki, "build_agent", build)
    # A short test deadline exercises cancellation and the reserved synthesis phase.
    settings.agent_timeout = 0.2
    model = ScriptedModel(responses=[AIMessage(content=f"升级判断来自源码。[资料:{doc_id}]")])
    asyncio.run(generate_overview(store, settings, repo, model))
    wiki = next(
        f for f in store.repository_files(repo["space_id"], repo["id"]) if f["kind"] == "ai_wiki"
    )
    content = client.get(base + "/documents/" + wiki["document_id"]).json()["content"]
    assert "研究阶段达到时间预算" in content and f"[[id:{doc_id}|" in content
