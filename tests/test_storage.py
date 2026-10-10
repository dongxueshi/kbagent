import hashlib
import sqlite3

import pytest
from minio.error import S3Error

from kbagent.migration import COPY_TABLES, migrate_sqlite


def upload(client, base, data=b"original text", name="原件.txt"):
    response = client.post(base + "/import", files={"file": (name, data)})
    assert response.status_code == 201
    return response.json()


def test_original_roundtrip_edit_isolation_and_delete(client, base):
    data = "原始 UTF-8 文件\n".encode()
    doc = upload(client, base, data)
    path = base + "/documents/" + doc["id"]
    meta = doc["original_file"]
    assert meta["size"] == len(data) and meta["sha256"] == hashlib.sha256(data).hexdigest()
    assert "object_key" not in meta
    result = client.get(path + "/original")
    assert result.content == data
    assert "filename*=UTF-8''" in result.headers["content-disposition"]
    other = client.post("/api/spaces", json={"name": "other"}).json()
    assert (
        client.get(f"/api/spaces/{other['id']}/documents/{doc['id']}/original").status_code == 404
    )
    edited = client.put(path, json={"title": "编辑", "content": "新的正文"}).json()
    assert edited["original_file"] == meta
    assert client.get(path + "/original").content == data
    objects = client.app.state.objects
    assert (
        len(list(objects.client.list_objects(objects.settings.minio_bucket, recursive=True))) == 1
    )
    assert client.delete(path).status_code == 204
    assert client.get(path + "/original").status_code == 404
    assert list(objects.client.list_objects(objects.settings.minio_bucket, recursive=True)) == []


def test_identical_names_keep_separate_originals_and_space_cleanup(client, base):
    a = upload(client, base, b"first")
    b = upload(client, base, b"second")
    assert client.get(base + f"/documents/{a['id']}/original").content == b"first"
    assert client.get(base + f"/documents/{b['id']}/original").content == b"second"
    assert client.delete(base).status_code == 204
    objects = client.app.state.objects
    assert list(objects.client.list_objects(objects.settings.minio_bucket, recursive=True)) == []


def test_sql_failure_after_object_upload_rolls_back_and_cleans(client, base, monkeypatch):
    store = client.app.state.store
    objects = client.app.state.objects

    def fail(*args):
        raise RuntimeError("intentional SQL index failure")

    monkeypatch.setattr(store, "_index_chunks", fail)
    with pytest.raises(RuntimeError):
        upload(client, base)
    assert client.get(base + "/documents").json() == []
    assert list(objects.client.list_objects(objects.settings.minio_bucket, recursive=True)) == []
    assert store.pending_deletions() == []


def test_minio_upload_failure_leaves_no_document(client, base, monkeypatch):
    objects = client.app.state.objects

    def fail(*args, **kwargs):
        raise S3Error("AccessDenied", "test only", "", "", "", None)

    monkeypatch.setattr(objects.client, "put_object", fail)
    response = client.post(base + "/import", files={"file": ("a.txt", b"text")})
    assert response.status_code == 503
    assert client.get(base + "/documents").json() == []
    assert client.app.state.store.pending_deletions() == []


def test_deletion_retry_and_live_object_safety(client, base, monkeypatch):
    doc = upload(client, base)
    objects = client.app.state.objects
    store = client.app.state.store
    key = store.original_file(doc["space_id"], doc["id"])["object_key"]
    # Even a stale deletion job must never select an attached object.
    store.reserve_object(key)
    store.abandon_object(key)
    with store.connection() as db:
        db.execute(
            "UPDATE object_deletions SET not_before=UTC_TIMESTAMP(6) WHERE object_key=?", (key,)
        )
    assert key not in store.pending_deletions()
    remove = objects.client.remove_object

    def fail(*args):
        raise RuntimeError("temporarily unavailable")

    monkeypatch.setattr(objects.client, "remove_object", fail)
    assert client.delete(base + f"/documents/{doc['id']}").status_code == 204
    assert key in store.pending_deletions()
    monkeypatch.setattr(objects.client, "remove_object", remove)
    objects.collect(store)
    assert store.pending_deletions() == []
    assert list(objects.client.list_objects(objects.settings.minio_bucket, recursive=True)) == []


def test_reservation_prevents_premature_cleanup(client):
    store = client.app.state.store
    store.reserve_object("originals/inflight")
    assert store.pending_deletions() == []
    store.abandon_object("originals/inflight")
    assert store.pending_deletions() == ["originals/inflight"]
    client.app.state.objects.collect(store)
    assert store.pending_deletions() == []


def test_migrate_legacy_sqlite_preserves_ids_bindings_and_chat(client, tmp_path):
    store = client.app.state.store
    space = store.create_space("旧库", "")
    target = store.save_document(space["id"], "旧标题", "目标内容")
    source = store.save_document(space["id"], "入口", "参考 [[旧标题]]")
    store.save_document(space["id"], "新标题", "目标内容", document_id=target["id"])
    conversation = store.create_conversation(space["id"])
    store.save_turn(
        conversation["id"], "问题", "回答", [{"document_id": target["id"]}], "retrieval"
    )
    with store.connection() as db:
        records = {
            table: db.execute(f"SELECT * FROM `{table}`").fetchall() for table in COPY_TABLES
        }
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as db:
        for table, columns in COPY_TABLES.items():
            names = ",".join(f"`{name}`" for name in columns)
            db.execute(f"CREATE TABLE `{table}` ({names})")
            placeholders = ",".join("?" for _ in columns)
            db.executemany(
                f"INSERT INTO `{table}` VALUES ({placeholders})",
                [tuple(row[name] for name in columns) for row in records[table]],
            )
    previous_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="empty destination"):
        migrate_sqlite(legacy, store.settings)
    store.delete_space(space["id"])
    counts = migrate_sqlite(legacy, store.settings)
    assert counts["documents"] == 2 and counts["messages"] == 2
    assert store.document(space["id"], target["id"])["aliases"] == ["旧标题"]
    assert store.relations(space["id"], source["id"])["outgoing"][0]["target_id"] == target["id"]
    assert store.search(space["id"], "目标内容")
    assert store.conversation(space["id"], conversation["id"])["messages"][1]["content"] == "回答"
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == previous_hash


def test_ambiguous_commit_error_cannot_remove_attached_original(client, base, monkeypatch):
    store = client.app.state.store
    save = store.save_document

    def save_then_fail(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError("commit acknowledgement unavailable")

    monkeypatch.setattr(store, "save_document", save_then_fail)
    with pytest.raises(RuntimeError):
        upload(client, base, b"committed original")
    docs = client.get(base + "/documents").json()
    assert len(docs) == 1
    assert (
        client.get(base + f"/documents/{docs[0]['id']}/original").content == b"committed original"
    )
    assert store.pending_deletions() == []


def test_missing_bucket_health_is_unavailable(client, monkeypatch):
    monkeypatch.setattr(client.app.state.objects.client, "bucket_exists", lambda *args: False)
    assert client.get("/api/health").status_code == 503
