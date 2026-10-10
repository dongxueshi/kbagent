"""Transactional SQLite storage and Chinese-friendly FTS5 retrieval."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import jieba

from .relations import LinkStore

jieba.setLogLevel(30)


def now() -> str:
    return datetime.now(UTC).isoformat()


STOP_WORDS = set("的 了 是 我 你 他 她 它 和 与 或 在 有 吗 呢 啊 请 如何 什么 怎么 哪些".split())


def tokenize(text: str) -> list[str]:
    return [
        word.lower()
        for word in jieba.cut_for_search(text)
        if re.search(r"[\w\u4e00-\u9fff]", word) and word.strip() not in STOP_WORDS
    ]


def chunks(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    return [
        text[i : i + size]
        for i in range(0, len(text), size - overlap)
        if text[i : i + size].strip()
    ]


class Store(LinkStore):
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "kbagent.sqlite3"

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS spaces (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id)
                    ON DELETE CASCADE, title TEXT NOT NULL, content TEXT NOT NULL,
                    source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id)
                    ON DELETE CASCADE, space_id TEXT NOT NULL, position INTEGER NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    chunk_id UNINDEXED, space_id UNINDEXED, title, body
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id)
                    ON DELETE CASCADE, title TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL,
                    mode TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS documents_space ON documents(space_id);
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id);
            """)
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
            db.execute("DELETE FROM chunk_fts WHERE space_id=?", (space_id,))
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
            return [
                dict(r, aliases=aliases.get(r["id"], []))
                for r in db.execute(
                    """
                SELECT id, space_id, title, source, created_at, updated_at,
                    length(content) character_count,
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
            return dict(
                r,
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
    ) -> dict:
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
            title_tokens = " ".join(tokenize(title))
            for pos, body in enumerate(chunks(content)):
                cid = uuid4().hex
                db.execute(
                    "INSERT INTO chunks VALUES (?,?,?,?,?)", (cid, item["id"], space_id, pos, body)
                )
                db.execute(
                    "INSERT INTO chunk_fts VALUES (?,?,?,?)",
                    (cid, space_id, title_tokens, " ".join(tokenize(body))),
                )
        return dict(item, aliases=aliases)

    @staticmethod
    def _remove_chunks(db, document_id):
        db.execute(
            "DELETE FROM chunk_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)",
            (document_id,),
        )
        db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))

    def delete_document(self, space_id: str, document_id: str):
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM documents WHERE id=? AND space_id=?", (document_id, space_id)
            ).fetchone():
                raise KeyError("文档不存在")
            self._remove_chunks(db, document_id)
            db.execute("DELETE FROM documents WHERE id=? AND space_id=?", (document_id, space_id))
            self._resolve_pending(db, space_id)

    def search(self, space_id: str, query: str, limit: int = 6) -> list[dict]:
        terms = list(dict.fromkeys(tokenize(query)))[:32]
        if not terms:
            return []
        expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with self.connection() as db:
            rows = db.execute(
                """
                SELECT c.id chunk_id, c.document_id, c.position, c.content,
                    d.title, d.source, bm25(chunk_fts, 0, 0, 3, 1) rank
                FROM chunk_fts JOIN chunks c ON c.id=chunk_fts.chunk_id
                JOIN documents d ON d.id=c.document_id
                WHERE chunk_fts MATCH ? AND chunk_fts.space_id=? AND d.space_id=?
                ORDER BY rank LIMIT ?
            """,
                (expression, space_id, space_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

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
系统使用中文分词和 SQLite FTS5 BM25 检索，不需要嵌入模型。
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
本地数据默认保存到 data/kbagent.sqlite3，重启服务不会丢失文档和对话。
返回 [[欢迎使用 KB Agent]] 查看完整开始流程。
""",
            ),
        ]
        for title, content in docs:
            self.save_document(space["id"], title, content, "示例资料")
        return space
