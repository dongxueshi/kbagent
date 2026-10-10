"""Transactional MySQL storage and Chinese-friendly BM25 retrieval."""

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jieba

from .config import Settings
from .database import transaction
from .relations import LinkStore
from .repository_store import RepositoryStore
from .schema import initialize_schema

jieba.setLogLevel(30)


def now() -> str:
    return datetime.now(UTC).isoformat()


STOP_WORDS = set("的 了 是 我 你 他 她 它 和 与 或 在 有 吗 呢 啊 请 如何 什么 怎么 哪些".split())


def tokenize(text: str) -> list[str]:
    result = []
    for word in jieba.cut_for_search(text):
        if re.search(r"[\w\u4e00-\u9fff]", word) and word.strip() not in STOP_WORDS:
            result.append(word.lower())
            parts = re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", word)
            if len(parts) > 1:
                result.extend(part.lower() for part in parts if part)
    return result


def chunks(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    return [
        text[i : i + size]
        for i in range(0, len(text), size - overlap)
        if text[i : i + size].strip()
    ]


class Store(LinkStore, RepositoryStore):
    def __init__(self, settings: Settings):
        self.settings = settings

    def connection(self):
        return transaction(self.settings)

    def initialize(self):
        with self.connection() as db:
            initialize_schema(db)
            self._initialize_links(db)

    def spaces(self) -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute("""
                SELECT s.*, count(d.id) document_count FROM spaces s
                LEFT JOIN documents d ON d.space_id=s.id
                GROUP BY s.id ORDER BY s.created_at, s.id
            """)
            ]

    def space(self, space_id: str) -> dict | None:
        with self.connection() as db:
            r = db.execute("SELECT * FROM spaces WHERE id=?", (space_id,)).fetchone()
            return dict(r) if r else None

    def create_space(self, name: str, description: str) -> dict:
        item = dict(id=uuid4().hex, name=name, description=description, created_at=now())
        with self.connection() as db:
            db.execute("INSERT INTO spaces VALUES (:id,:name,:description,:created_at)", item)
        return item

    def delete_space(self, space_id: str):
        with self.connection() as db:
            self._queue_space_files(db, space_id)
            self._queue_repository_snapshots(db, space_id)
            db.execute("DELETE FROM spaces WHERE id=?", (space_id,))

    def documents(self, space_id: str) -> list[dict]:
        with self.connection() as db:
            aliases = {}
            for row in db.execute(
                """SELECT a.document_id,a.name FROM document_aliases a
                JOIN documents d ON d.id=a.document_id WHERE d.space_id=? ORDER BY a.name""",
                (space_id,),
            ):
                aliases.setdefault(row["document_id"], []).append(row["name"])
            repository_sources = self._repository_sources(db, space_id)
            return [
                dict(
                    r,
                    aliases=aliases.get(r["id"], []),
                    repository=repository_sources.get(r["id"]),
                )
                for r in db.execute(
                    """
                SELECT id, space_id, title, source, created_at, updated_at,
                    CHAR_LENGTH(content) character_count,
                    (SELECT count(*) FROM chunks c WHERE c.document_id=d.id) chunk_count
                FROM documents d WHERE space_id=? ORDER BY updated_at DESC, id
            """,
                    (space_id,),
                )
            ]

    def document(self, space_id: str, document_id: str) -> dict | None:
        with self.connection() as db:
            r = db.execute(
                "SELECT * FROM documents WHERE space_id=? AND id=?", (space_id, document_id)
            ).fetchone()
            if not r:
                return None
            original = db.execute(
                "SELECT filename,content_type,size,sha256 FROM original_files WHERE document_id=?",
                (document_id,),
            ).fetchone()
            return dict(
                r,
                original_file=dict(original) if original else None,
                repository=self._repository_source(db, document_id),
                aliases=[
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM document_aliases WHERE document_id=? ORDER BY name",
                        (document_id,),
                    )
                ],
            )

    def save_document(
        self,
        space_id: str,
        title: str,
        content: str,
        source: str = "手动创建",
        document_id: str | None = None,
        aliases: list[str] | None = None,
        original_file: dict | None = None,
    ) -> dict:
        if document_id and self.repository_document_source(space_id, document_id):
            raise ValueError("仓库文档由同步管理，请修改代码并重新同步")
        stamp = now()
        item = dict(
            id=document_id or uuid4().hex,
            space_id=space_id,
            title=title,
            content=content,
            source=source,
            created_at=stamp,
            updated_at=stamp,
        )
        with self.connection() as db:
            old_title = None
            if document_id:
                existing = db.execute(
                    "SELECT * FROM documents WHERE id=? AND space_id=?", (document_id, space_id)
                ).fetchone()
                if not existing:
                    raise KeyError("文档不存在")
                item["created_at"] = existing["created_at"]
                item["source"] = existing["source"]
                old_title = existing["title"]
                db.execute(
                    """UPDATE documents SET title=:title, content=:content,
                           updated_at=:updated_at WHERE id=:id AND space_id=:space_id""",
                    item,
                )
                self._remove_chunks(db, document_id)
            else:
                db.execute(
                    """INSERT INTO documents VALUES
                           (:id,:space_id,:title,:content,:source,:created_at,:updated_at)""",
                    item,
                )
            if aliases is None:
                aliases = [
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM document_aliases WHERE document_id=?", (item["id"],)
                    )
                ]
            if old_title and old_title != title and old_title not in aliases:
                aliases = [*aliases, old_title]
            self._set_aliases(db, item["id"], aliases)
            self._index_links(db, item)
            self._resolve_pending(db, space_id)
            self._index_chunks(db, item)
            if original_file:
                db.execute(
                    """INSERT INTO original_files
                    (document_id,object_key,filename,content_type,size,sha256)
                    VALUES (:document_id,:object_key,:filename,:content_type,:size,:sha256)""",
                    dict(original_file, document_id=item["id"]),
                )
                db.execute(
                    "DELETE FROM object_deletions WHERE object_key=?",
                    (original_file["object_key"],),
                )
        return self.document(space_id, item["id"])

    @staticmethod
    def _index_chunks(db, document):
        title_terms = Counter(term for term in tokenize(document["title"]) if len(term) <= 200)
        for pos, body in enumerate(chunks(document["content"])):
            cid = uuid4().hex
            body_terms = Counter(term for term in tokenize(body) if len(term) <= 200)
            size = 3 * sum(title_terms.values()) + sum(body_terms.values())
            db.execute(
                """INSERT INTO chunks
                (id,document_id,space_id,position,content,token_count) VALUES (?,?,?,?,?,?)""",
                (cid, document["id"], document["space_id"], pos, body, max(size, 1)),
            )
            db.executemany(
                "INSERT INTO chunk_terms VALUES (?,?,?,?,?)",
                [
                    (cid, document["space_id"], term, title_terms[term], body_terms[term])
                    for term in title_terms.keys() | body_terms.keys()
                ],
            )

    @staticmethod
    def _remove_chunks(db, document_id):
        db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))

    @staticmethod
    def _queue_space_files(db, space_id):
        db.execute(
            """INSERT INTO object_deletions (object_key,not_before)
            SELECT f.object_key,UTC_TIMESTAMP(6) FROM original_files f
            JOIN documents d ON d.id=f.document_id WHERE d.space_id=?
            ON DUPLICATE KEY UPDATE not_before=UTC_TIMESTAMP(6)""",
            (space_id,),
        )

    def delete_document(self, space_id: str, document_id: str):
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM documents WHERE id=? AND space_id=?", (document_id, space_id)
            ).fetchone():
                raise KeyError("文档不存在")
            db.execute(
                """INSERT INTO object_deletions (object_key,not_before)
                SELECT object_key,UTC_TIMESTAMP(6) FROM original_files WHERE document_id=?
                ON DUPLICATE KEY UPDATE not_before=UTC_TIMESTAMP(6)""",
                (document_id,),
            )
            db.execute("DELETE FROM documents WHERE id=? AND space_id=?", (document_id, space_id))
            self._resolve_pending(db, space_id)

    def search(
        self,
        space_id: str,
        query: str,
        limit: int = 6,
        repository_id: str | None = None,
        code_only: bool = False,
    ) -> list[dict]:
        terms = [term for term in dict.fromkeys(tokenize(query)) if len(term) <= 200][:32]
        if not terms:
            return []
        slots = ",".join("?" for _ in terms)
        with self.connection() as db:
            stats = db.execute(
                """SELECT count(*) n, COALESCE(AVG(token_count),1) average
                FROM chunks WHERE space_id=?""",
                (space_id,),
            ).fetchone()
            if not stats["n"]:
                return []
            # BM25 (k1=1.2, b=0.75), title frequency weighted by 3.
            # Exact token postings avoid MySQL FULLTEXT minimum-word-length/stopword pitfalls.
            rows = db.execute(
                f"""SELECT c.id chunk_id,c.document_id,c.position,c.content,
                d.title,d.source,
                -SUM(LOG(1+(?-freq.df+0.5)/(freq.df+0.5)) *
                    ((3*t.title_freq+t.body_freq)*2.2) /
                    ((3*t.title_freq+t.body_freq)+1.2*(0.25+0.75*c.token_count/?))) `rank`
                FROM chunk_terms t JOIN chunks c ON c.id=t.chunk_id
                JOIN documents d ON d.id=c.document_id
                JOIN (SELECT term,count(*) df FROM chunk_terms
                    WHERE space_id=? AND term IN ({slots}) GROUP BY term) freq ON freq.term=t.term
                WHERE t.space_id=? AND d.space_id=? AND t.term IN ({slots})
                AND (? IS NULL OR EXISTS (SELECT 1 FROM repository_documents rd
                    WHERE rd.document_id=d.id AND rd.repository_id=?))
                AND (?=0 OR EXISTS (SELECT 1 FROM repository_documents rd
                    WHERE rd.document_id=d.id AND rd.kind='code'))
                GROUP BY c.id,d.id ORDER BY `rank`,c.id LIMIT ?""",
                (
                    stats["n"],
                    float(stats["average"]),
                    space_id,
                    *terms,
                    space_id,
                    space_id,
                    *terms,
                    repository_id,
                    repository_id,
                    int(code_only),
                    limit,
                ),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["repository"] = self._repository_source(db, row["document_id"])
                if item["repository"] and item["repository"]["kind"] == "code":
                    content = db.execute(
                        "SELECT content FROM documents WHERE id=?", (row["document_id"],)
                    ).fetchone()[0]
                    offsets = [
                        i for i in range(0, len(content), 780) if content[i : i + 900].strip()
                    ]
                    offset = offsets[row["position"]]
                    item["repository"]["start_line"] = content[:offset].count("\n") + 1
                    item["repository"]["end_line"] = min(
                        item["repository"]["line_count"],
                        content[: offset + len(row["content"])].count("\n") + 1,
                    )
                result.append(item)
            return result

    def original_file(self, space_id, document_id):
        with self.connection() as db:
            row = db.execute(
                """SELECT f.* FROM original_files f JOIN documents d
                ON d.id=f.document_id WHERE d.space_id=? AND d.id=?""",
                (space_id, document_id),
            ).fetchone()
            return dict(row) if row else None

    def reserve_object(self, key):
        # Reserve cleanup before uploading. A failed/ambiguous SQL commit cannot delete a live blob.
        with self.connection() as db:
            db.execute(
                "INSERT INTO object_deletions VALUES (?,?)",
                (key, datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)),
            )

    def abandon_object(self, key):
        with self.connection() as db:
            db.execute(
                """UPDATE object_deletions SET not_before=UTC_TIMESTAMP(6)
                WHERE object_key=? AND NOT EXISTS
                (SELECT 1 FROM original_files WHERE object_key=?)
                AND NOT EXISTS (SELECT 1 FROM repository_snapshots WHERE object_key=?)""",
                (key, key, key),
            )

    def pending_deletions(self, limit=100):
        with self.connection() as db:
            return [
                row["object_key"]
                for row in db.execute(
                    """SELECT q.object_key
                FROM object_deletions q WHERE q.not_before<=UTC_TIMESTAMP(6)
                AND NOT EXISTS (SELECT 1 FROM original_files f WHERE f.object_key=q.object_key)
                AND NOT EXISTS (SELECT 1 FROM repository_snapshots s
                    WHERE s.object_key=q.object_key)
                ORDER BY q.not_before LIMIT ?""",
                    (limit,),
                )
            ]

    def finish_deletion(self, key):
        with self.connection() as db:
            db.execute("DELETE FROM object_deletions WHERE object_key=?", (key,))

    def create_conversation(self, space_id: str, title: str = "新对话") -> dict:
        item = dict(id=uuid4().hex, space_id=space_id, title=title, created_at=now())
        with self.connection() as db:
            db.execute("INSERT INTO conversations VALUES (:id,:space_id,:title,:created_at)", item)
        return item

    def conversations(self, space_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM conversations WHERE space_id=? ORDER BY created_at DESC",
                    (space_id,),
                )
            ]

    def conversation(self, space_id: str, conversation_id: str) -> dict | None:
        with self.connection() as db:
            r = db.execute(
                "SELECT * FROM conversations WHERE id=? AND space_id=?", (conversation_id, space_id)
            ).fetchone()
            if not r:
                return None
            result = dict(r)
            result["messages"] = [
                dict(m)
                for m in db.execute(
                    "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
                )
            ]
            for m in result["messages"]:
                m["sources"] = json.loads(m["sources"])
            return result

    def save_turn(
        self, conversation_id: str, question: str, answer: str, sources: list[dict], mode: str
    ):
        # Only persist completed turns: provider failures never create orphan user messages.
        with self.connection() as db:
            db.executemany(
                """INSERT INTO messages
                (conversation_id,role,content,sources,mode,created_at) VALUES (?,?,?,?,?,?)""",
                [
                    (conversation_id, "user", question, "[]", mode, now()),
                    (conversation_id, "assistant", answer, json.dumps(sources), mode, now()),
                ],
            )
            db.execute(
                "UPDATE conversations SET title=? WHERE id=? AND title='新对话'",
                (question[:60], conversation_id),
            )

    def seed_demo(self) -> dict:
        """Explicit demo creation only; never add fictitious content on startup."""
        space = self.create_space("产品与团队手册", "示例知识库 · 可编辑、可删除")
        docs = [
            (
                "欢迎使用 KB Agent",
                """# 欢迎使用 KB Agent

KB Agent 是一个基于 DeepAgents SDK 的本地知识库智能体系统。

## 开始使用
1. 创建知识库，将不同主题的资料分别管理。
2. 新建 Markdown 文档，或导入 Markdown、TXT、PDF、DOCX 文件。
3. 在「智能问答」中提问，查看可追溯的文档来源。

## 关联阅读
查看 [[文档导入指南]]，或阅读 [[本地模型配置]]。
使用双向链接连接资料，右侧可查看反向引用和局部知识图谱。

## 检索与智能体
未配置模型时，系统只返回知识库检索片段，不生成 AI 答案。
配置支持工具调用的模型后，DeepAgents 可以规划任务、检索资料和阅读文档。
所有知识库检索工具仅能访问当前选中的知识库。
""",
            ),
            (
                "文档导入指南",
                """# 文档导入指南

支持的文件格式：Markdown (.md)、纯文本 (.txt)、PDF (.pdf)、Word (.docx)。
单个文件大小限制为 10 MB。每篇文档最多 200,000 个字符。
Markdown 与 TXT 使用 UTF-8 编码。
扫描版 PDF 暂不支持 OCR，请先转为含文本的 PDF。

文档会自动按 900 字符分片，相邻片段重叠 120 字符。
系统使用中文分词和 MySQL 词项索引进行 BM25 检索，不需要嵌入模型。
编辑或删除文档后，检索索引在同一事务中更新。
""",
            ),
            (
                "本地模型配置",
                """# 本地模型配置

在项目根目录的 .env 文件设置 KBAGENT_API_KEY、KBAGENT_MODEL 和 KBAGENT_BASE_URL。
模型需支持 OpenAI 兼容的 Chat Completions API 和工具调用。
修改配置后重启服务即可进入 DeepAgents 智能体模式。
密钥只在服务端读取，不会返回给浏览器。
文档、会话和关联保存到 MySQL，上传原件保存到 MinIO。重启服务不会丢失已保存资料。
返回 [[欢迎使用 KB Agent]] 查看完整开始流程。
""",
            ),
        ]
        for title, content in docs:
            self.save_document(space["id"], title, content, "示例资料")
        return space
