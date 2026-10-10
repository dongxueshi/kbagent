import pytest
from fastapi.testclient import TestClient

from kbagent.config import Settings
from kbagent.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, api_key="", model="", _env_file=None))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def space(client):
    return client.post("/api/spaces", json={"name": "测试知识库"}).json()


@pytest.fixture
def base(space):
    return f"/api/spaces/{space['id']}"
