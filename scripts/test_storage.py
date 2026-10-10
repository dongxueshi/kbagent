"""Explicitly isolated service configuration shared by pytest and browser checks."""

import os
from contextlib import contextmanager
from uuid import uuid4

from dotenv import dotenv_values

from kbagent.config import Settings
from kbagent.object_storage import ObjectStorage
from kbagent.store import Store


@contextmanager
def isolated_storage():
    normal = Settings()
    name = os.environ.get("KBAGENT_TEST_MYSQL_DATABASE") or dotenv_values(".env").get(
        "KBAGENT_TEST_MYSQL_DATABASE", "kbagent_test"
    )
    name = Settings.valid_database(name)
    if name == normal.mysql_database or not name.endswith("_test"):
        raise RuntimeError("Tests require a separate database ending in _test")
    settings = normal.model_copy(
        update=dict(
            mysql_database=name,
            minio_bucket="kbagent-test-" + uuid4().hex,
            minio_create_bucket=True,
            api_key="",
            model="",
        )
    )
    store = Store(settings)
    store.initialize()
    objects = ObjectStorage(settings)
    objects.initialize()
    with store.connection() as db:
        db.execute("DELETE FROM spaces")
        db.execute("DELETE FROM object_deletions")
    try:
        yield settings
    finally:
        with store.connection() as db:
            db.execute("DELETE FROM spaces")
            db.execute("DELETE FROM object_deletions")
        for obj in objects.client.list_objects(settings.minio_bucket, recursive=True):
            objects.client.remove_object(settings.minio_bucket, obj.object_name)
        objects.client.remove_bucket(settings.minio_bucket)
