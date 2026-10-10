import asyncio
import re
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from kbagent import agent
from kbagent.config import Settings
from kbagent.deepwiki import UnitScope, generate_deep_wiki, split_source
from kbagent.repositories import RepositoryFailure
from tests.test_agent import ScriptedModel, call
from tests.test_repositories import add_local, checkout, git, wait_repo

__all__ = ["checkout"]


class WikiModel(ScriptedModel):
    calls: int = 0
    prompts: list[str] = []
    fail_at: int = 0
    unknown_citation: bool = False
    loop: bool = False
    extra: str = ""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.fail_at and self.calls >= self.fail_at:
            raise RepositoryFailure("测试模型不可用")
        prompt = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        self.prompts.append(prompt)
        ids = list(dict.fromkeys(re.findall(r"\[资料:([a-f0-9]{32})\]", prompt)))
        assert ids, "The orchestrator must supply actual source or checked summaries"
        if self.loop and "只整理已给定源码或摘要" not in str(messages[0].content):
            result = call("read_code", {"document_id": ids[0]}, str(self.calls))
        else:
            result = AIMessage(
                content="职责、接口与关键条件来自提供的资料。"
                + self.extra
                + "".join(f"[资料:{i}]" for i in (["f" * 32] if self.unknown_citation else ids))
            )
        return ChatResult(generations=[ChatGeneration(message=result)])


def model(**kwargs):
    return WikiModel(responses=[AIMessage(content="unused")], **kwargs)


def pages(client, repo):
    store = client.app.state.store
    return {
        f["path"]: store.document(repo["space_id"], f["document_id"])
        for f in store.repository_files(repo["space_id"], repo["id"])
        if f["kind"] == "ai_wiki"
    }


def test_full_wiki_covers_more_than_old_file_limit_and_long_file(client, base, checkout):
    for i in range(14):
        (checkout / f"src/file{i}.py").write_text(f"VALUE_{i}={i}\n")
    long_content = "# first\n" + ("# middle segment\n" * 500) + "# FINAL_SOURCE_MARKER\n"
    (checkout / "src/long.py").write_text(long_content)
    (checkout / "src/main.py").write_text("from .orders import forceUpgrade\n")
    git(checkout, "add", "src")
    git(checkout, "commit", "-m", "Large repository")
    repo = add_local(client, base, checkout)
    settings, store = client.app.state.settings, client.app.state.store
    settings.wiki_source_chars = 4000
    settings.wiki_batch_files = 2
    settings.wiki_max_files = 1  # Old overview limit cannot cap the new full pipeline.
    fixture = model()
    asyncio.run(generate_deep_wiki(store, settings, repo, fixture))
    result = pages(client, repo)
    assert len([p for p in result if p.startswith("AI 文件 · ")]) == repo["file_count"] > 12
    assert any("FINAL_SOURCE_MARKER" in p for p in fixture.prompts)
    assert all(len(p.split("\n\n", 1)[-1]) < 4700 for p in fixture.prompts)
    assert len(re.findall(r"## 分段 \d+", result["AI 文件 · src/long.py"]["content"])) >= 3
    assert "已处理 19 / 19" in result["AI 架构与关键流程"]["content"]
    assert "AI 文件 · src/orders.py" in result["AI 静态依赖关系"]["content"]
    assert "[[wiki:" not in "".join(p["content"] for p in result.values())
    progress = client.get(base + "/repositories/" + repo["id"]).json()["wiki_progress"]
    assert progress["phase"] == "ready" and progress["completed_files"] == progress["total_files"]
    assert progress["completed_chunks"] == progress["total_chunks"]
    for page in result.values():
        relations = store.relations(repo["space_id"], page["id"])
        assert not any(link["status"] == "broken" for link in relations["outgoing"])
    again = model()
    asyncio.run(generate_deep_wiki(store, settings, repo, again))
    assert again.calls == 0
    assert {p: d["id"] for p, d in pages(client, repo).items()} == {
        p: d["id"] for p, d in result.items()
    }


def test_failure_resume_and_source_change_only_reanalyzes_changed_file(client, base, checkout):
    repo = add_local(client, base, checkout)
    store, settings = client.app.state.store, client.app.state.settings
    broken = model(fail_at=2)
    with pytest.raises(RepositoryFailure, match="测试模型不可用"):
        asyncio.run(generate_deep_wiki(store, settings, repo, broken))
    assert not pages(client, repo)
    assert store.repository(repo["space_id"], repo["id"])["wiki_progress"]["completed_files"] == 1
    resumed = model()
    asyncio.run(generate_deep_wiki(store, settings, repo, resumed))
    assert not any("阶段：source\n单元：README.md" in p for p in resumed.prompts)
    assert store.repository(repo["space_id"], repo["id"])["wiki_progress"]["cache_hits"] >= 1
    (checkout / "src/orders.py").write_text(
        "def forceUpgrade(order):\n    return order['version'] < 3\n"
    )
    (checkout / "src/template.py").unlink()
    git(checkout, "add", "src")
    git(checkout, "commit", "-m", "Change one file and remove another")
    assert client.post(base + "/repositories/" + repo["id"] + "/sync").status_code == 202
    current = wait_repo(client, base, repo["id"])
    assert current["wiki_progress"] is None  # Previous-version progress must not appear current.
    incremental = model(extra="新版本分析")
    asyncio.run(generate_deep_wiki(store, settings, current, incremental))
    source_prompts = [p for p in incremental.prompts if "阶段：source" in p]
    assert len(source_prompts) == 1 and "src/orders.py" in source_prompts[0]
    assert "AI 文件 · src/template.py" not in pages(client, current)
    assert current["commit_sha"] in pages(client, current)["AI 架构与关键流程"]["content"]

    # Even identical model summaries cannot conceal a changed dependency/source hash.
    settings.model = "same-summary-model"
    asyncio.run(generate_deep_wiki(store, settings, current, model()))
    (checkout / "src/orders.py").write_text(
        "def forceUpgrade(order):\n    return order['version'] < 4\n"
    )
    git(checkout, "add", "src/orders.py")
    git(checkout, "commit", "-m", "Change source with identical model summary")
    client.post(base + "/repositories/" + repo["id"] + "/sync")
    changed = wait_repo(client, base, repo["id"])
    same_summary = model()
    asyncio.run(generate_deep_wiki(store, settings, changed, same_summary))
    assert same_summary.calls == 3  # Source, affected module and repository; root module is reused.


def test_invalid_or_oversized_result_preserves_published_pages(client, base, checkout):
    repo = add_local(client, base, checkout)
    store, settings = client.app.state.store, client.app.state.settings
    asyncio.run(generate_deep_wiki(store, settings, repo, model()))
    original = pages(client, repo)
    settings.model = "changed-model-invalidates-cache"
    for fixture in [model(unknown_citation=True), model(extra="x" * 2100)]:
        with pytest.raises(RepositoryFailure, match="校验失败"):
            asyncio.run(generate_deep_wiki(store, settings, repo, fixture))
        assert fixture.calls == 2  # One bounded graph call plus one tool-free repair.
        assert pages(client, repo) == original


def test_step_budget_fallback_per_unit_finishes_all_files(client, base, checkout):
    repo = add_local(client, base, checkout)
    settings = client.app.state.settings
    settings.wiki_max_steps = 8
    fixture = model(loop=True)
    asyncio.run(generate_deep_wiki(client.app.state.store, settings, repo, fixture))
    assert len([p for p in pages(client, repo) if p.startswith("AI 文件 · ")]) == 3
    assert any("上一轮失败：研究达到步骤或时间预算" in p for p in fixture.prompts)


def test_snapshot_checkpoints_reject_stale_writes_and_atomic_publish_rolls_back(
    client, base, checkout, monkeypatch
):
    repo = add_local(client, base, checkout)
    store, settings = client.app.state.store, client.app.state.settings
    asyncio.run(generate_deep_wiki(store, settings, repo, model()))
    original = pages(client, repo)
    stale = dict(repo, snapshot_id="f" * 32)
    with pytest.raises(ValueError, match="代码版本已变化"):
        store.save_wiki_checkpoint(stale, "a" * 64, "source", "invalid", [])
    with pytest.raises(ValueError, match="代码版本已变化"):
        store.save_repository_wiki_pages(stale, {"bad": "bad"}, set())
    write = store._upsert_repository_document
    count = 0

    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("injected publication failure")
        return write(*args, **kwargs)

    monkeypatch.setattr(store, "_upsert_repository_document", fail_second)
    with pytest.raises(RuntimeError):
        store.save_repository_wiki_pages(repo, {"page1": "one", "page2": "two"}, set())
    assert pages(client, repo) == original
    assert store.repository(repo["space_id"], repo["id"])["wiki_status"] == "ready"


def test_blank_files_are_filtered_without_fabricated_evidence(client, base, checkout):
    (checkout / "src/empty.py").write_text("")
    (checkout / "src/blank.py").write_text(" \n\n")
    git(checkout, "add", "src")
    git(checkout, "commit", "-m", "Empty files")
    repo = add_local(client, base, checkout)
    fixture = model()
    asyncio.run(
        generate_deep_wiki(client.app.state.store, client.app.state.settings, repo, fixture)
    )
    assert "AI 文件 · src/empty.py" not in pages(client, repo)
    assert "AI 文件 · src/blank.py" not in pages(client, repo)
    assert repo["file_count"] == 3 and repo["skipped_count"] == 7
    assert "已处理 3 / 3" in pages(client, repo)["AI 架构与关键流程"]["content"]


def test_unit_scope_and_segmentation_do_not_expand_source_access():
    content = "start\n" + "x" * 7000 + "\nend"
    parts = list(split_source(content, 2800))
    assert "".join(p["content"] for p in parts) == content
    assert all(len(p["content"]) <= 2800 for p in parts)
    scope = UnitScope(dict(space_id="one"), {"allowed": dict(content="fragment")})
    assert scope.document("one", "allowed")["content"] == "fragment"
    assert scope.document("other", "allowed") is None and scope.document("one", "outside") is None
    assert scope.search("one", "secret") == []


def test_provider_timeout_is_configurable(monkeypatch):
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(agent, "ChatOpenAI", capture)
    agent.create_model(Settings(_env_file=None, model_timeout=180))
    assert captured["timeout"] == 180


def test_default_api_job_generates_full_wiki_and_progress(client, base, checkout, monkeypatch):
    from kbagent import deepwiki

    repo = add_local(client, base, checkout)
    settings = client.app.state.settings
    settings.api_key, settings.model = "test-fixture", "test-fixture"
    monkeypatch.setattr(deepwiki, "create_model", lambda _: model())
    assert client.post(base + "/repositories/" + repo["id"] + "/wiki").status_code == 202
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = client.get(base + "/repositories/" + repo["id"]).json()
        if current["wiki_status"] in {"ready", "error"}:
            break
        time.sleep(0.05)
    assert current["wiki_status"] == "ready", current
    assert current["wiki_progress"]["completed_files"] == 3
    assert "AI 模块 · src" in pages(client, repo)
    original_ids = {name: doc["id"] for name, doc in pages(client, repo).items()}
    forced = model()
    monkeypatch.setattr(deepwiki, "create_model", lambda _: forced)
    assert client.post(base + "/repositories/" + repo["id"] + "/wiki?force=true").status_code == 202
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = client.get(base + "/repositories/" + repo["id"]).json()
        if current["wiki_status"] in {"ready", "error"}:
            break
        time.sleep(0.05)
    assert current["wiki_status"] == "ready", current
    assert forced.calls >= 6 and current["wiki_progress"]["cache_hits"] == 0
    assert {name: doc["id"] for name, doc in pages(client, repo).items()} == original_ids


def test_source_cache_survives_store_reinitialization(client, base, checkout):
    from kbagent.store import Store

    repo = add_local(client, base, checkout)
    store, settings = client.app.state.store, client.app.state.settings
    with pytest.raises(RepositoryFailure):
        asyncio.run(generate_deep_wiki(store, settings, repo, model(fail_at=2)))
    restarted = Store(settings)
    restarted.initialize()
    fixture = model()
    asyncio.run(generate_deep_wiki(restarted, settings, repo, fixture))
    assert not any("阶段：source\n单元：README.md" in p for p in fixture.prompts)
    assert restarted.repository(repo["space_id"], repo["id"])["wiki_status"] == "ready"


def test_task_timeout_keeps_completed_checkpoints_for_retry(client, base, checkout, monkeypatch):
    from kbagent.deepwiki import WikiPipeline

    repo = add_local(client, base, checkout)
    store, settings = client.app.state.store, client.app.state.settings
    run_unit = WikiPipeline.unit
    units = 0

    async def block_second(self, *args, **kwargs):
        nonlocal units
        units += 1
        if units == 2:
            await asyncio.Event().wait()
        return await run_unit(self, *args, **kwargs)

    monkeypatch.setattr(WikiPipeline, "unit", block_second)
    settings.wiki_task_timeout = 1
    with pytest.raises(RepositoryFailure, match="整仓任务超时"):
        asyncio.run(generate_deep_wiki(store, settings, repo, model()))
    assert store.repository(repo["space_id"], repo["id"])["wiki_progress"]["completed_files"] == 1
    monkeypatch.setattr(WikiPipeline, "unit", run_unit)
    settings.wiki_task_timeout = 60
    fixture = model()
    asyncio.run(generate_deep_wiki(store, settings, repo, fixture))
    assert not any("阶段：source\n单元：README.md" in p for p in fixture.prompts)
