import time

import httpx
import openai
import pymysql
import pytest
from langgraph.errors import GraphRecursionError
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from kbagent.repository_errors import repository_error
from tests.test_repositories import add_local, checkout

# Reuse the actual committed Git fixture for worker integration tests.
__all__ = ["checkout"]


SECRET = "sensitive-provider-body-and-credential"


def provider_error(cls, status, body=None):
    response = httpx.Response(status, request=httpx.Request("POST", "https://model.invalid"))
    return cls(SECRET, response=response, body=body or {"message": SECRET})


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (provider_error(openai.AuthenticationError, 401), "401"),
        (provider_error(openai.PermissionDeniedError, 403), "403"),
        (provider_error(openai.NotFoundError, 404), "404"),
        (provider_error(openai.RateLimitError, 429), "429"),
        (provider_error(openai.BadRequestError, 400), "工具调用支持"),
        (
            provider_error(
                openai.BadRequestError, 400, {"error": {"code": "context_length_exceeded"}}
            ),
            "上下文长度超限",
        ),
        (provider_error(openai.InternalServerError, 500), "模型服务返回错误"),
        (
            openai.APITimeoutError(request=httpx.Request("POST", "https://model.invalid")),
            "单次请求超时",
        ),
        (
            openai.APIConnectionError(
                message=SECRET, request=httpx.Request("POST", "https://model.invalid")
            ),
            "无法连接模型",
        ),
        (pymysql.OperationalError(2003, SECRET), "无法连接 MySQL"),
        (pymysql.OperationalError(1045, SECRET), "MySQL 账号认证失败"),
        (pymysql.OperationalError(1213, SECRET), "死锁"),
        (
            S3Error(
                code="AccessDenied",
                message=SECRET,
                resource="secret-path",
                request_id="request",
                host_id="host",
                response=None,
            ),
            "MinIO 拒绝访问",
        ),
        (
            S3Error(
                code="NoSuchKey",
                message=SECRET,
                resource="secret-path",
                request_id="request",
                host_id="host",
                response=None,
            ),
            "源码快照不存在",
        ),
        (HTTPError(SECRET), "无法连接 MinIO"),
        (GraphRecursionError(SECRET), "步骤上限"),
        (TimeoutError(SECRET), "任务上限 120 秒"),
        (RuntimeError(SECRET), "AI Wiki 生成失败"),
    ],
)
def test_failure_classification_does_not_expose_provider_details(exc, expected):
    result = repository_error(exc, "wiki", 120)
    assert expected in result
    assert SECRET not in result and "model.invalid" not in result and "secret-path" not in result


def test_unknown_sync_error_does_not_blame_model():
    assert "代码仓库同步失败" in repository_error(RuntimeError(SECRET), "sync", 120)


@pytest.mark.parametrize(
    "failure", [TimeoutError(SECRET), provider_error(openai.AuthenticationError, 401)]
)
def test_failed_wiki_preserves_search_and_reports_stage(
    client, base, checkout, monkeypatch, caplog, failure
):
    from kbagent import repository_wiki

    repo = add_local(client, base, checkout)

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(repository_wiki, "generate_wiki", fail)
    settings = client.app.state.settings
    settings.api_key, settings.model = "fixture-not-a-real-key", "fixture"
    response = client.post(base + "/repositories/" + repo["id"] + "/wiki")
    assert response.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = client.get(base + "/repositories/" + repo["id"]).json()
        if current["wiki_status"] == "error":
            break
        time.sleep(0.05)
    assert current["wiki_status"] == "error"
    assert current["sync_status"] == "ready" and current["snapshot_id"] == repo["snapshot_id"]
    expected = "任务上限 120 秒" if isinstance(failure, TimeoutError) else "401"
    assert expected in current["wiki_error"] and current["error"] == ""
    assert client.get(base + "/search", params={"q": "forceUpgrade"}).json()
    assert SECRET not in caplog.text
    assert "Repository job failed" in caplog.text and "job=wiki" in caplog.text

    # Requeueing clears only the prior error for this job, not stored code.
    async def succeed(*args, **kwargs):
        store = client.app.state.store
        with store.connection() as db:
            db.execute("UPDATE repositories SET wiki_status='ready' WHERE id=?", (repo["id"],))

    monkeypatch.setattr(repository_wiki, "generate_wiki", succeed)
    client.post(base + "/repositories/" + repo["id"] + "/wiki")
    assert client.get(base + "/repositories/" + repo["id"]).json()["wiki_error"] == ""
