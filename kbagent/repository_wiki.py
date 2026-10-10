"""Generate a grounded code Wiki with the same DeepAgents architecture as QA."""

import asyncio
import re

from langchain_core.messages import HumanMessage

from .agent import PROMPT, build_agent, text_content
from .repositories import RepositoryFailure

WIKI_PROMPT = (
    PROMPT
    + """
当前任务是生成代码 Wiki，不是简短问答。先用 search_code 检索实现，再用 read_code
或 read_document 核对入口和关键流程。文件列表只用于定位，不是实现证据。
每项实现结论使用 [资料:文档ID] 引用实际读取的源码；文档ID是工具返回的32位ID，
不是仓库ID、提交号、文件路径或数字序号。不得引用生成的 Wiki 页面作为源码证据。
未能核实的实现细节写为待确认。不要输出没有来源的架构结论。
"""
)


class RepositoryScope:
    """Restrict every delegated retrieval capability to this repository's source files."""

    def __init__(self, store, repo):
        self.store, self.repo = store, repo
        self.ids = {
            f["document_id"]
            for f in store.repository_files(repo["space_id"], repo["id"])
            if f["kind"] == "code"
        }

    def search(self, space_id, query, limit=6, repository_id=None, code_only=False):
        if repository_id and repository_id != self.repo["id"]:
            return []
        # The shared QA prompt can choose search_knowledge. Wiki generation still needs code.
        return self.store.search(space_id, query, limit, self.repo["id"], True)

    def document(self, space_id, document_id):
        return self.store.document(space_id, document_id) if document_id in self.ids else None

    def repository_files(self, space_id, repository_id):
        return (
            [f for f in self.store.repository_files(space_id, repository_id) if f["kind"] == "code"]
            if repository_id == self.repo["id"]
            else []
        )

    def relations(self, *args, **kwargs):
        return None

    def graph(self, *args, **kwargs):
        return None


CITATION = re.compile(r"\[资料\s*[:：]\s*([^\]\n]+)\]")
ID_LINK = re.compile(r"\[\[id:([a-fA-F0-9]{32})(?:\|[^\]\n]*)?\]\]")
LINE_SUFFIX = re.compile(r"(?:[:#]L?\d+(?:[-–](?:L)?\d+)?)$", re.IGNORECASE)


def normalize_citations(content, evidence):
    """Accept exact IDs/paths only when actual source evidence was read this run."""
    sources = {
        cid: item
        for cid, item in evidence.items()
        if item.get("repository")
        and item["repository"]["kind"] == "code"
        and item.get("excerpt", "").strip()
    }
    paths = {}
    for cid, item in sources.items():
        paths.setdefault(item["repository"]["path"], []).append(cid)
    cited, invalid = [], []

    def resolve(raw):
        value = raw.strip().strip("`").strip()
        value = LINE_SUFFIX.sub("", value).strip().strip("`")
        cid = value.lower() if re.fullmatch(r"[a-fA-F0-9]{32}", value) else None
        if cid not in sources:
            matches = paths.get(value, [])
            cid = matches[0] if len(matches) == 1 else None
        if cid:
            cited.append(cid)
            return f"[资料:{cid}]"
        invalid.append(raw)
        return f"[资料:{raw}]"

    content = ID_LINK.sub(lambda m: resolve(m[1]), content)
    content = CITATION.sub(lambda m: resolve(m[1]), content)
    if not sources:
        reason = "没有读取到有效源码；请调用 search_code 或 read_code 读取代码后再生成"
    elif invalid:
        reason = "引用包含未读取的源码或无法匹配的文档 ID"
    elif not cited:
        reason = "回答缺少源码引用，请使用 [资料:工具返回的32位文档ID]"
    else:
        reason = None
    return content, reason, sources


async def generate_wiki(store, settings, repo, model=None):
    if model is None and not settings.agent_enabled:
        raise RepositoryFailure("请先在 .env 配置模型服务；结构 Wiki 无需模型")
    scope = RepositoryScope(store, repo)
    evidence = {}
    graph = build_agent(
        scope, repo["space_id"], settings, evidence, model=model, system_prompt=WIKI_PROMPT
    )
    files = scope.repository_files(repo["space_id"], repo["id"])
    listing = "\n".join(f"{f['document_id']} {f['path']}" for f in files)
    prompt = (
        "为以下代码仓库生成中文架构与关键流程 Wiki。先检索再阅读源码。"
        "描述入口、模块职责、调用流程和配置方法。每项实现结论用 [资料:文档ID] 引用实际读取的源码。"
        "无法从代码证明的内容标为待确认。不要把结构索引当作实现证据。\n"
        f"仓库ID：{repo['id']}\n仓库：{repo['name']}\n提交：{repo['commit_sha']}\n"
        f"源码文档ID与路径（仅用于定位，不是已读取的证据）：\n{listing}"
    )
    messages = [HumanMessage(prompt)]
    async with asyncio.timeout(settings.agent_timeout):
        # One repair attempt shares the original timeout and actual tool evidence.
        for attempt in range(2):
            result = await graph.ainvoke({"messages": messages}, {"recursion_limit": 40})
            content, reason, sources = normalize_citations(
                text_content(result["messages"][-1]), evidence
            )
            if reason is None:
                break
            if attempt:
                raise RepositoryFailure(f"AI Wiki 引用校验失败：{reason}。未保存，请重试")
            verified = "\n".join(
                f"[资料:{cid}] {item['repository']['path']}" for cid, item in sources.items()
            )
            messages = [
                *result["messages"],
                HumanMessage(
                    f"上一版 Wiki 未通过校验：{reason}。请修正并返回完整 Wiki。"
                    "不得为未读取的源码补造引用；缺少证据时继续调用源码工具。"
                    f"\n当前已核实的引用：\n{verified or '无，请先读取源码'}"
                ),
            ]
    content = CITATION.sub(
        lambda m: f"[[id:{m[1]}|{sources[m[1]]['repository']['path'][-180:]}]]", content
    )
    content = (
        f"# AI 架构与关键流程\n\n提交：`{repo['commit_sha']}`\n\nAI 生成，请通过源码引用核对。\n\n"
        + content
    )
    await asyncio.to_thread(store.save_repository_ai_wiki, repo, content)
