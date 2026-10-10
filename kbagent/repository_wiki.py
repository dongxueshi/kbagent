"""Generate a grounded code Wiki with the same DeepAgents architecture as QA."""

import asyncio
import re

from langchain_core.messages import HumanMessage

from .agent import build_agent, text_content
from .repositories import RepositoryFailure


class RepositoryScope:
    """Restrict every delegated retrieval capability to this single repository."""

    def __init__(self, store, repo):
        self.store, self.repo = store, repo
        self.ids = {f["document_id"] for f in store.repository_files(repo["space_id"], repo["id"])}

    def search(self, space_id, query, limit=6, repository_id=None, code_only=False):
        if repository_id and repository_id != self.repo["id"]:
            return []
        return self.store.search(space_id, query, limit, self.repo["id"], code_only)

    def document(self, space_id, document_id):
        return self.store.document(space_id, document_id) if document_id in self.ids else None

    def repository_files(self, space_id, repository_id):
        return (
            self.store.repository_files(space_id, repository_id)
            if repository_id == self.repo["id"]
            else []
        )

    def relations(self, *args, **kwargs):
        return None

    def graph(self, *args, **kwargs):
        return None


async def generate_wiki(store, settings, repo, model=None):
    if model is None and not settings.agent_enabled:
        raise RepositoryFailure("请先在 .env 配置模型服务；结构 Wiki 无需模型")
    scope = RepositoryScope(store, repo)
    evidence = {}
    graph = build_agent(scope, repo["space_id"], settings, evidence, model=model)
    files = scope.repository_files(repo["space_id"], repo["id"])
    listing = "\n".join(f"{f['document_id']} {f['path']}" for f in files if f["kind"] == "code")
    prompt = (
        "为以下代码仓库生成中文架构与关键流程 Wiki。先检索再阅读源码。"
        "描述入口、模块职责、调用流程和配置方法。每项实现结论用 [资料:文档ID] 引用实际读取的源码。"
        "无法从代码证明的内容标为待确认。不要把结构索引当作实现证据。\n"
        f"仓库：{repo['name']}\n提交：{repo['commit_sha']}\n文件：\n{listing}"
    )
    async with asyncio.timeout(settings.agent_timeout):
        result = await graph.ainvoke({"messages": [HumanMessage(prompt)]}, {"recursion_limit": 40})
    content = text_content(result["messages"][-1])
    cited = re.findall(r"\[资料:([a-f0-9]{32})\]", content)
    if not cited or any(
        cid not in evidence
        or not evidence[cid].get("repository")
        or evidence[cid]["repository"]["kind"] != "code"
        for cid in cited
    ):
        raise RepositoryFailure("模型未提供有效源码引用，Wiki 未保存，请重试")
    content = re.sub(
        r"\[资料:([a-f0-9]{32})\]",
        lambda m: f"[[id:{m[1]}|{evidence[m[1]]['repository']['path']}]]",
        content,
    )
    content = (
        f"# AI 架构与关键流程\n\n提交：`{repo['commit_sha']}`\n\nAI 生成，请通过源码引用核对。\n\n"
        + content
    )
    await asyncio.to_thread(store.save_repository_ai_wiki, repo, content)
