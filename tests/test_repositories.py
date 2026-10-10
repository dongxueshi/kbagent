import asyncio
import base64
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kbagent.agent import build_agent
from kbagent.config import Settings
from kbagent.repositories import GitReader, RepositoryFailure, allowed_file
from kbagent.repository_wiki import generate_wiki
from tests.test_agent import ScriptedModel, call, sse


def git(path, *args):
    return (
        subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL)
        .decode()
        .strip()
    )


@pytest.fixture
def checkout(tmp_path):
    path = tmp_path / "service"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Repository test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "src").mkdir()
    (path / "src/orders.py").write_text(
        '# 订单升级策略\ndef forceUpgrade(order):\n    return order["version"] < 2\n'
    )
    (path / "README.md").write_text("# 服务\n订单系统\n")
    (path / ".env").write_text("SECRET=do-not-index-this")
    (path / "private.py").write_text('key="-----BEGIN RSA PRIVATE KEY-----"')
    (path / "binary.py").write_bytes(b"hello\0world")
    (path / "link.py").symlink_to("/etc/passwd")
    (path / "node_modules").mkdir()
    (path / "node_modules/lib.js").write_text("generated")
    (path / "src/template.py").write_text('text = "[[unresolved-code-string]]"\n')
    git(path, "add", ".")
    git(path, "commit", "-m", "First version")
    git(path, "branch", "develop")
    (path / "untracked.py").write_text("UNTRACKED_NOT_IMPORTED=1")
    return path


def add_local(client, base, checkout, ref="main"):
    client.app.state.settings.repository_local_roots = [checkout.parent]
    response = client.post(
        base + "/repositories",
        json=dict(name="订单服务", kind="local", source=str(checkout), ref=ref),
    )
    assert response.status_code == 201, response.text
    return wait_repo(client, base, response.json()["id"])


def wait_repo(client, base, repo_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        repo = client.get(base + "/repositories/" + repo_id).json()
        if repo["sync_status"] not in {"new", "queued", "running"}:
            return repo
        time.sleep(0.05)
    raise AssertionError("Repository sync did not finish")


def test_local_sync_wiki_source_search_and_readonly(client, base, checkout):
    repo = add_local(client, base, checkout)
    assert repo["sync_status"] == "ready", repo
    assert repo["commit_sha"] == git(checkout, "rev-parse", "main")
    paths = {f["path"]: f for f in repo["files"] if f["kind"] == "code"}
    assert set(paths) == {"src/orders.py", "src/template.py", "README.md"}
    assert repo["skipped_count"] == 5
    code = paths["src/orders.py"]
    content = client.get(
        base + "/repositories/" + repo["id"] + "/source",
        params={"path": code["path"], "start_line": 2, "end_line": 3},
    ).json()
    assert content["start_line"] == 2 and content["line_count"] == 3
    assert "forceUpgrade" in content["content"]
    assert client.get(base + "/search", params={"q": "force upgrade"}).json()
    assert (
        client.put(
            base + "/documents/" + code["document_id"],
            json={"title": "edited", "content": "changed"},
        ).status_code
        == 409
    )
    assert client.delete(base + "/documents/" + code["document_id"]).status_code == 409
    assert (
        client.get(
            base + "/documents/" + paths["src/template.py"]["document_id"] + "/relations"
        ).json()["outgoing"]
        == []
    )
    overview = next(f for f in repo["files"] if f["path"] == "仓库概览")
    assert (
        len(
            client.get(base + "/documents/" + overview["document_id"] + "/relations").json()[
                "outgoing"
            ]
        )
        == 3
    )
    cid = client.post(base + "/conversations").json()["id"]
    answer = sse(
        client.post(base + "/conversations/" + cid + "/chat", json={"question": "forceUpgrade"})
    )[-1]
    source = next(s for s in answer["sources"] if s.get("repository", {}).get("kind") == "code")
    assert source["repository"]["snapshot_id"] == repo["snapshot_id"]
    assert source["repository"]["start_line"] >= 1
    assert client.post(base + "/repositories/" + repo["id"] + "/wiki").status_code == 422


def test_resync_versions_atomic_failure_and_cleanup(client, base, checkout, monkeypatch):
    repo = add_local(client, base, checkout)
    old = next(f for f in repo["files"] if f["path"] == "src/orders.py")
    (checkout / "src/orders.py").write_text("def newPolicy():\n    return 42\n")
    (checkout / "README.md").unlink()
    git(checkout, "add", "-u")
    git(checkout, "commit", "-m", "Changed code")
    assert client.post(base + "/repositories/" + repo["id"] + "/sync").status_code == 202
    updated = wait_repo(client, base, repo["id"])
    assert updated["sync_status"] == "ready"
    current = next(f for f in updated["files"] if f["path"] == "src/orders.py")
    assert current["document_id"] == old["document_id"]
    assert current["snapshot_id"] != old["snapshot_id"]
    history = client.get(
        base + "/repositories/" + repo["id"] + "/source",
        params={"path": old["path"], "snapshot_id": old["snapshot_id"]},
    ).json()
    assert "forceUpgrade" in history["content"]
    assert client.get(base + "/search", params={"q": "forceUpgrade"}).json() == []
    store = client.app.state.store
    with monkeypatch.context() as m:

        def fail(*args):
            raise RuntimeError("do-not-expose-token")

        m.setattr(store, "_index_chunks", fail)
        client.post(base + "/repositories/" + repo["id"] + "/sync")
        failed = wait_repo(client, base, repo["id"])
        assert failed["sync_status"] == "error" and failed["snapshot_id"] == updated["snapshot_id"]
        assert "do-not-expose-token" not in failed["error"]
    objects = client.app.state.objects
    keys = [
        obj.object_name
        for obj in objects.client.list_objects(objects.settings.minio_bucket, recursive=True)
    ]
    assert len(keys) == 2  # Failed transaction's unattached archive was collected.
    assert client.delete(base + "/repositories/" + repo["id"]).status_code == 204
    assert client.get(base + "/documents").json() == []
    assert list(objects.client.list_objects(objects.settings.minio_bucket, recursive=True)) == []


def test_repository_scope_validation_and_branch(client, base, checkout):
    client.app.state.settings.repository_local_roots = [checkout.parent]
    for path, ref in [
        ("/etc", "main"),
        (str(checkout / "src"), "main"),
        (str(checkout), "--upload-pack=evil"),
    ]:
        assert (
            client.post(
                base + "/repositories", json=dict(name="x", kind="local", source=path, ref=ref)
            ).status_code
            == 422
        )
    repo = add_local(client, base, checkout, "develop")
    assert repo["sync_status"] == "ready"
    private = client.post("/api/spaces", json={"name": "其他库"}).json()
    other = "/api/spaces/" + private["id"]
    assert client.get(other + "/repositories/" + repo["id"]).status_code == 404
    assert (
        client.get(
            other + "/repositories/" + repo["id"] + "/source",
            params={"path": "src/orders.py", "snapshot_id": repo["snapshot_id"]},
        ).status_code
        == 404
    )
    assert client.app.state.store.repository_files(private["id"], repo["id"]) == []
    assert client.app.state.store.search(private["id"], "forceUpgrade", 10, repo["id"], True) == []
    settings = Settings(_env_file=None, gitlab_url="https://gitlab.local/root")
    reader = GitReader(settings)
    for url in [
        "https://other.local/root/g/p.git",
        "https://gitlab.local/root/../g/p.git",
        "https://token@gitlab.local/root/g/p.git",
        "https://gitlab.local/root/g/p.git?token=secret",
    ]:
        with pytest.raises(RepositoryFailure):
            reader.validate("gitlab", url, "main")
    assert reader.validate("gitlab", "https://gitlab.local/root/g/sub/p.git", "main")
    assert not allowed_file("cert.key") and not allowed_file(".env.production")


def test_real_sdk_code_tools_and_ai_wiki(client, base, checkout):
    repo = add_local(client, base, checkout)
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    model = ScriptedModel(
        responses=[
            call("search_code", {"query": "forceUpgrade", "repository_id": repo["id"]}, "search"),
            call("read_code", {"document_id": doc, "start_line": 2, "end_line": 3}, "read"),
            AIMessage(content=f"低版本订单需要升级 [资料:{doc}]"),
        ]
    )
    evidence = {}
    graph = build_agent(
        client.app.state.store, repo["space_id"], client.app.state.settings, evidence, model
    )
    result = graph.invoke({"messages": [{"role": "user", "content": "代码升级规则"}]})
    tools = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert "forceUpgrade" in tools[1].content
    assert evidence[doc]["repository"]["commit_sha"] == repo["commit_sha"]
    # Run the real SDK through the Wiki orchestrator using a deterministic provider fixture.
    asyncio.run(generate_wiki(client.app.state.store, client.app.state.settings, repo, model=model))
    wiki = next(
        f
        for f in client.app.state.store.repository_files(repo["space_id"], repo["id"])
        if f["kind"] == "ai_wiki"
    )
    assert "[[id:" + doc in client.get(base + "/documents/" + wiki["document_id"]).json()["content"]
    invalid = ScriptedModel(responses=[AIMessage(content="无依据的架构说明")])
    with pytest.raises(RepositoryFailure):
        asyncio.run(
            generate_wiki(client.app.state.store, client.app.state.settings, repo, model=invalid)
        )


@pytest.fixture
def gitlab_http(checkout, tmp_path):
    root = tmp_path / "remote"
    (root / "team").mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--bare", str(checkout), str(root / "team/service.git")],
        check=True,
        capture_output=True,
    )
    git(root / "team/service.git", "config", "http.receivepack", "false")
    backend = (
        Path(subprocess.check_output(["git", "--exec-path"]).decode().strip()) / "git-http-backend"
    )
    auth = "Basic " + base64.b64encode(b"oauth2:readonly-fixture-token").decode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.handle_git()

        def do_POST(self):
            self.handle_git()

        def log_message(self, *args):
            pass

        def handle_git(self):
            if self.headers.get("Authorization") != auth:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="GitLab fixture"')
                self.end_headers()
                return
            path, _, query = self.path.partition("?")
            env = dict(
                os.environ,
                GIT_PROJECT_ROOT=str(root),
                GIT_HTTP_EXPORT_ALL="1",
                PATH_INFO=path,
                QUERY_STRING=query,
                REQUEST_METHOD=self.command,
                CONTENT_TYPE=self.headers.get("Content-Type", ""),
                REMOTE_USER="oauth2",
            )
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            response = subprocess.run(
                [str(backend)], input=data, capture_output=True, env=env, check=True
            ).stdout
            headers, body = response.split(b"\r\n\r\n", 1)
            self.send_response(200)
            for header in headers.decode().split("\r\n"):
                key, value = header.split(":", 1)
                self.send_header(key, value.strip())
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_gitlab_authenticated_clone(client, base, gitlab_http):
    from pydantic import SecretStr

    client.app.state.settings.gitlab_url = gitlab_http
    client.app.state.settings.gitlab_token = SecretStr("readonly-fixture-token")
    response = client.post(
        base + "/repositories",
        json=dict(
            name="GitLab 测试", kind="gitlab", source=gitlab_http + "/team/service.git", ref="main"
        ),
    )
    assert response.status_code == 201
    repo = wait_repo(client, base, response.json()["id"])
    assert repo["sync_status"] == "ready", repo
    assert repo["file_count"] == 3
    assert "readonly-fixture-token" not in str(repo)


def test_busy_jobs_recovery_and_failed_branch_keep_snapshot(client, base, checkout, monkeypatch):
    repo = add_local(client, base, checkout)
    store = client.app.state.store
    service = client.app.state.repositories
    started, release = threading.Event(), threading.Event()
    original = service.git.read

    def blocked(item):
        started.set()
        assert release.wait(5)
        return original(item)

    with monkeypatch.context() as patch:
        patch.setattr(service.git, "read", blocked)
        try:
            assert client.post(base + "/repositories/" + repo["id"] + "/sync").status_code == 202
            assert started.wait(5)
            assert client.post(base + "/repositories/" + repo["id"] + "/sync").status_code == 409
            assert client.delete(base + "/repositories/" + repo["id"]).status_code == 409
            assert client.delete(base).status_code == 409
        finally:
            release.set()
        assert wait_repo(client, base, repo["id"])["sync_status"] == "ready"
    with store.connection() as db:
        db.execute("UPDATE repositories SET ref='missing-branch' WHERE id=?", (repo["id"],))
    client.post(base + "/repositories/" + repo["id"] + "/sync")
    failed = wait_repo(client, base, repo["id"])
    assert failed["sync_status"] == "error" and failed["snapshot_id"]
    assert (
        client.get(
            base + "/repositories/" + repo["id"] + "/source", params={"path": "src/orders.py"}
        ).status_code
        == 200
    )
    with store.connection() as db:
        db.execute("UPDATE repositories SET sync_status='running' WHERE id=?", (repo["id"],))
    store.recover_repository_jobs()
    assert "重启中断" in store.repository(repo["space_id"], repo["id"])["error"]


def test_snapshot_outbox_never_collects_committed_object_and_space_cleanup(client, base, checkout):
    repo = add_local(client, base, checkout)
    store, objects = client.app.state.store, client.app.state.objects
    snapshot = store.repository_snapshot(repo["space_id"], repo["id"])
    # Simulate an ambiguous successful commit with a stale cleanup reservation.
    with store.connection() as db:
        db.execute(
            "INSERT INTO object_deletions VALUES (?,UTC_TIMESTAMP(6))", (snapshot["object_key"],)
        )
    store.abandon_object(snapshot["object_key"])
    assert snapshot["object_key"] not in store.pending_deletions()
    objects.collect(store)
    assert (
        client.get(
            base + "/repositories/" + repo["id"] + "/source", params={"path": "src/orders.py"}
        ).status_code
        == 200
    )
    assert client.delete(base).status_code == 204
    assert list(objects.client.list_objects(objects.settings.minio_bucket, recursive=True)) == []


def test_local_no_hooks_no_worktree_and_limits(checkout, tmp_path):
    marker = tmp_path / "executed"
    hook = checkout / ".git/hooks/post-checkout"
    hook.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
    hook.chmod(0o755)
    git(checkout, "config", "uploadpack.packObjectsHook", "touch " + str(marker))
    before = git(checkout, "status", "--porcelain")
    settings = Settings(
        _env_file=None,
        repository_local_roots=[checkout.parent],
        repository_max_files=1,
        repository_max_file_bytes=100,
    )
    _, files, skipped = GitReader(settings).read(
        dict(kind="local", source=str(checkout), ref="main")
    )
    assert len(files) == 1 and skipped == 7
    assert not marker.exists()
    assert git(checkout, "status", "--porcelain") == before
    (tmp_path / "alias").symlink_to(checkout, target_is_directory=True)
    with pytest.raises(RepositoryFailure):
        GitReader(
            settings.model_copy(update={"repository_local_roots": [tmp_path / "other"]})
        ).validate("local", str(tmp_path / "alias"), "main")


def test_long_source_chunk_exact_line_provenance(client, base, checkout):
    # Blank windows are omitted by the generic chunker; provenance must retain their offsets.
    content = "initial = 1\n" + "\n" * 1800 + "finalUpgradePolicy = 42\n"
    (checkout / "src/long.py").write_text(content)
    git(checkout, "add", "src/long.py")
    git(checkout, "commit", "-m", "Long file")
    repo = add_local(client, base, checkout)
    hits = client.app.state.store.search(
        repo["space_id"], "finalUpgradePolicy", 10, repo["id"], True
    )
    hit = next(h for h in hits if "finalUpgradePolicy" in h["content"])
    assert hit["repository"]["end_line"] == 1802
    source = client.get(
        base + "/repositories/" + repo["id"] + "/source",
        params={"path": "src/long.py", "start_line": 1802, "end_line": 1802},
    ).json()
    assert source["content"] == "finalUpgradePolicy = 42"


@pytest.mark.parametrize("style", ["spaced_id", "path", "path_lines", "wiki_id"])
def test_wiki_accepts_verified_source_citation_formats(client, base, checkout, style):
    repo = add_local(client, base, checkout)
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    citations = {
        "spaced_id": f"[资料： `{doc.upper()}` ]",
        "path": "[资料:src/orders.py]",
        "path_lines": "[资料:src/orders.py:L2-L3]",
        "wiki_id": f"[[id:{doc}|订单源码]]",
    }
    model = ScriptedModel(
        responses=[
            call("read_code", {"document_id": doc, "start_line": 2, "end_line": 3}, "read"),
            AIMessage(content="订单升级条件来自源码。" + citations[style]),
        ]
    )
    asyncio.run(generate_wiki(client.app.state.store, client.app.state.settings, repo, model=model))
    page = next(
        f
        for f in client.app.state.store.repository_files(repo["space_id"], repo["id"])
        if f["kind"] == "ai_wiki"
    )
    content = client.get(base + "/documents/" + page["document_id"]).json()["content"]
    assert f"[[id:{doc}|src/orders.py]]" in content


def test_wiki_repairs_missing_citations_without_rereading(client, base, checkout):
    repo = add_local(client, base, checkout)
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    model = ScriptedModel(
        responses=[
            call("read_code", {"document_id": doc}, "read"),
            AIMessage(content="订单版本低于2时升级。"),
            AIMessage(content=f"订单版本低于2时升级。[资料:{doc}]"),
        ]
    )
    asyncio.run(generate_wiki(client.app.state.store, client.app.state.settings, repo, model=model))
    assert client.app.state.store.repository(repo["space_id"], repo["id"])["wiki_status"] == "ready"
    assert model.i == 0  # Exactly three provider calls; no third generation attempt.


def test_wiki_repair_can_read_code_and_denies_structure_wiki(client, base, checkout):
    from kbagent.repository_wiki import RepositoryScope

    repo = add_local(client, base, checkout)
    store = client.app.state.store
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    wiki = next(f for f in repo["files"] if f["kind"] == "wiki")["document_id"]
    scope = RepositoryScope(store, repo)
    assert scope.document(repo["space_id"], wiki) is None
    assert all(h["repository"]["kind"] == "code" for h in scope.search(repo["space_id"], "订单"))
    model = ScriptedModel(
        responses=[
            AIMessage(content="先给一版没有读取源码的说明"),
            call("read_code", {"document_id": doc}, "read"),
            AIMessage(content=f"升级实现。[资料:{doc}]"),
        ]
    )
    asyncio.run(generate_wiki(store, client.app.state.settings, repo, model=model))
    assert store.repository(repo["space_id"], repo["id"])["wiki_status"] == "ready"


def test_wiki_unknown_reference_rejected_after_one_repair(client, base, checkout):
    repo = add_local(client, base, checkout)
    store = client.app.state.store
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    other = store.create_space("其他代码库", "")
    outside = store.save_document(other["id"], "外部资料", "其他实现")
    model = ScriptedModel(
        responses=[
            call("read_code", {"document_id": doc}, "read"),
            AIMessage(content=f"未读取的实现。[资料:{outside['id']}]"),
            AIMessage(content="还是未知引用。[资料:nonexistent.py]"),
        ]
    )
    with pytest.raises(RepositoryFailure, match="未读取的源码"):
        asyncio.run(generate_wiki(store, client.app.state.settings, repo, model=model))
    assert not any(
        f["kind"] == "ai_wiki" for f in store.repository_files(repo["space_id"], repo["id"])
    )


def test_empty_source_reads_are_not_wiki_evidence(client, base, checkout):
    repo = add_local(client, base, checkout)
    doc = next(f for f in repo["files"] if f["path"] == "src/orders.py")["document_id"]
    model = ScriptedModel(
        responses=[
            call("read_document", {"document_id": doc, "offset": 999999}, "empty"),
            AIMessage(content=f"无源码内容。[资料:{doc}]"),
            AIMessage(content=f"仍无源码内容。[资料:{doc}]"),
        ]
    )
    with pytest.raises(RepositoryFailure, match="没有读取到有效源码"):
        asyncio.run(
            generate_wiki(client.app.state.store, client.app.state.settings, repo, model=model)
        )
