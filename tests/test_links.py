import pytest

from kbagent.store import Store
from kbagent.wikilinks import parse_links


def create(client, base, title, content="资料内容", **extra):
    response = client.post(base + "/documents", json={"title": title, "content": content, **extra})
    assert response.status_code == 201
    return response.json()


def relations(client, base, document):
    return client.get(base + "/documents/" + document["id"] + "/relations").json()


def test_parser_offsets_anchor_alias_and_exclusions():
    text = r"""😀 [[目标#安装|显示文字]] 与 [[#本页标题]]
`[[行内]]` 和 ``含 ` 的 [[长行内]]``
```markdown
[[示例]]
```
~~~
[[另一个代码块]]
~~~
    [[缩进代码]]
<!-- [[注释]] -->
\[[转义]] ![[嵌入]] [[真实]]
"""
    links = parse_links(text)
    assert [link["target_name"] for link in links] == ["目标", "", "真实"]
    assert links[0]["anchor"] == "安装" and links[0]["label"] == "显示文字"
    for link in links:
        assert text[link["start"] : link["end"]] == link["raw"]


def test_parser_unclosed_fence_comment_and_escaped_backslashes():
    assert parse_links("```\n[[code]]") == []
    assert parse_links("<!-- [[comment]]") == []
    assert parse_links(r"\\[[target]]")[0]["target_name"] == "target"
    assert parse_links("[[ ]] [[]]") == []


@pytest.mark.parametrize(
    "text",
    [
        "```\n<!--\n```\n[[真实]]",
        "`<!--` [[真实]]",
        "<!--\n```\n[[注释]]\n-->\n[[真实]]",
        "````\n```\n[[代码]]\n````\n[[真实]]",
    ],
)
def test_code_and_comments_do_not_mask_real_links_outside_them(text):
    assert [link["target_name"] for link in parse_links(text)] == ["真实"]


def test_pending_link_auto_resolves_with_backlink_and_import(client, base):
    source = create(client, base, "入口", "😀 阅读 [[安装手册#启动|下一步]] 和 [[#本页]]\n# 本页")
    links = relations(client, base, source)["outgoing"]
    assert [link["status"] for link in links] == ["unresolved", "resolved"]
    target = create(client, base, "安装手册", "# 启动\n执行 uv sync")
    link = relations(client, base, source)["outgoing"][0]
    assert link["status"] == "resolved" and link["target_id"] == target["id"]
    backlinks = relations(client, base, target)["backlinks"]
    assert backlinks[0]["source_id"] == source["id"]
    assert "下一步" in backlinks[0]["excerpt"]
    imported = client.post(
        base + "/import", files={"file": ("参考.md", "另见 [[安装手册]]".encode(), "text/markdown")}
    ).json()
    assert relations(client, base, imported)["outgoing"][0]["target_id"] == target["id"]


def test_rename_and_source_edit_preserve_stable_binding(client, base):
    target = create(client, base, "指南")
    source = create(client, base, "入口", "参考 [[指南]]")
    link = relations(client, base, source)["outgoing"][0]
    response = client.put(
        base + "/documents/" + target["id"], json={"title": "新指南", "content": "更新后的资料"}
    )
    assert response.status_code == 200 and "指南" in response.json()["aliases"]
    client.put(
        base + "/documents/" + source["id"],
        json={"title": "入口", "content": "前面新增文字\n参考 [[指南]]"},
    )
    updated = relations(client, base, source)["outgoing"][0]
    assert updated["id"] == link["id"] and updated["target_id"] == target["id"]
    assert updated["target_title"] == "新指南"
    another = create(client, base, "另一个入口", "[[指南]]")
    assert relations(client, base, another)["outgoing"][0]["target_id"] == target["id"]
    client.put(
        base + "/documents/" + source["id"], json={"title": "入口", "content": "已经移除引用"}
    )
    assert relations(client, base, source)["outgoing"] == []
    assert len(relations(client, base, target)["backlinks"]) == 1


def test_deleted_target_not_silently_rebound(client, base):
    target = create(client, base, "手册")
    source = create(client, base, "入口", "参考 [[手册]]")
    client.delete(base + "/documents/" + target["id"])
    replacement = create(client, base, "手册", "不同内容的新文档")
    client.put(
        base + "/documents/" + source["id"],
        json={"title": "入口", "content": "增加文字，参考 [[手册]]"},
    )
    link = relations(client, base, source)["outgoing"][0]
    assert link["status"] == "broken" and link["target_id"] is None
    assert relations(client, base, replacement)["backlinks"] == []
    response = client.put(
        base + f"/documents/{source['id']}/links/{link['id']}",
        json={"target_id": replacement["id"]},
    )
    assert response.status_code == 200
    assert relations(client, base, source)["outgoing"][0]["target_id"] == replacement["id"]


def test_ambiguous_names_and_manual_choice(client, base):
    first = create(client, base, "同名")
    second = create(client, base, "同名")
    source = create(client, base, "入口", "[[同名]]")
    link = relations(client, base, source)["outgoing"][0]
    assert link["status"] == "ambiguous" and len(link["candidates"]) == 2
    assert (
        client.put(
            base + f"/documents/{source['id']}/links/{link['id']}", json={"target_id": second["id"]}
        ).status_code
        == 200
    )
    assert relations(client, base, source)["outgoing"][0]["target_id"] == second["id"]
    assert relations(client, base, first)["backlinks"] == []
    explicit = create(client, base, "指定目标", f"[[id:{first['id']}|第一篇]]")
    assert relations(client, base, explicit)["outgoing"][0]["target_id"] == first["id"]


def test_alias_resolution_and_ambiguity_recovery(client, base):
    source = create(client, base, "引用", "[[别名]]")
    target = create(client, base, "原文", aliases=["别名", "别名"])
    assert target["aliases"] == ["别名"]
    assert relations(client, base, source)["outgoing"][0]["target_id"] == target["id"]
    first = create(client, base, "重复")
    second = create(client, base, "重复")
    another = create(client, base, "另一入口", "[[重复]]")
    assert relations(client, base, another)["outgoing"][0]["status"] == "ambiguous"
    client.delete(base + "/documents/" + first["id"])
    assert relations(client, base, another)["outgoing"][0]["target_id"] == second["id"]


def test_graph_two_hops_duplicates_cycles_and_isolation(client, base):
    a = create(client, base, "A")
    b = create(client, base, "B", "[[A]] [[A]]")
    c = create(client, base, "C", "[[B]] [[C]]")
    path = base + f"/documents/{a['id']}/graph"
    one = client.get(path).json()
    assert {node["id"] for node in one["nodes"]} == {a["id"], b["id"]}
    assert one["edges"][0]["occurrences"] == 2
    two = client.get(path, params={"depth": 2}).json()
    assert {node["id"] for node in two["nodes"]} == {a["id"], b["id"], c["id"]}
    assert client.get(path, params={"depth": 3}).status_code == 422
    assert client.app.state.store.graph(a["space_id"], a["id"], 2, limit=2)["truncated"]
    other = client.post("/api/spaces", json={"name": "隔离库"}).json()
    other_base = f"/api/spaces/{other['id']}"
    other_doc = create(client, other_base, "秘密", f"[[id:{a['id']}]] [[A]]")
    assert all(
        link["status"] == "unresolved"
        for link in relations(client, other_base, other_doc)["outgoing"]
    )
    assert client.get(other_base + f"/documents/{a['id']}/graph").status_code == 404
    assert client.get(other_base + f"/documents/{a['id']}/relations").status_code == 404
    link = relations(client, base, b)["outgoing"][0]
    assert (
        client.put(
            base + f"/documents/{b['id']}/links/{link['id']}", json={"target_id": other_doc["id"]}
        ).status_code
        == 404
    )
    assert (
        client.put(
            base + f"/documents/{a['id']}/links/{link['id']}", json={"target_id": a["id"]}
        ).status_code
        == 404
    )


def test_migration_backfills_existing_docs_once(client, base):
    a = create(client, base, "A", "旧资料 [[B]]")
    b = create(client, base, "B", "旧资料")
    with client.app.state.store.connection() as db:
        for table in ["document_links", "document_aliases", "schema_meta"]:
            db.execute(f"DROP TABLE {table}")
    restored = Store(client.app.state.settings)
    restored.initialize()
    first = restored.relations(a["space_id"], a["id"])["outgoing"]
    assert len(first) == 1 and first[0]["target_id"] == b["id"]
    restored.initialize()
    assert restored.relations(a["space_id"], a["id"])["outgoing"] == first
    assert restored.document(a["space_id"], a["id"])["content"] == "旧资料 [[B]]"


def test_link_index_failure_rolls_back_document_and_search(client, base, monkeypatch):
    doc = create(client, base, "原文", "原始资料 [[待创建]]")
    store = client.app.state.store

    def fail(*args):
        raise RuntimeError("test-index-failure")

    monkeypatch.setattr(store, "_index_links", fail)
    with pytest.raises(RuntimeError):
        store.save_document(doc["space_id"], "修改", "修改后的资料", document_id=doc["id"])
    assert store.document(doc["space_id"], doc["id"])["title"] == "原文"
    assert store.search(doc["space_id"], "原始资料")
    assert relations(client, base, doc)["outgoing"][0]["target_name"] == "待创建"


def test_space_cascade_removes_links_and_aliases(client, base):
    create(client, base, "A", "[[B]]", aliases=["旧A"])
    create(client, base, "B")
    assert client.delete(base).status_code == 204
    with client.app.state.store.connection() as db:
        for table in ["document_links", "document_aliases"]:
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("aliases", [[""], ["x" * 201], ["n"] * 51])
def test_alias_validation(client, base, aliases):
    assert (
        client.post(
            base + "/documents", json={"title": "标题", "content": "内容", "aliases": aliases}
        ).status_code
        == 422
    )
