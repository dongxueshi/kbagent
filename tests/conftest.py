import pytest
from fastapi.testclient import TestClient

from kbagent.main import create_app
from scripts.test_storage import isolated_storage


@pytest.fixture
def client():
    with isolated_storage() as settings:
        app = create_app(settings)
        with TestClient(app) as client:
            yield client


@pytest.fixture
def space(client):
    return client.post("/api/spaces", json={"name": "测试知识库"}).json()


@pytest.fixture
def base(space):
    return f"/api/spaces/{space['id']}"
