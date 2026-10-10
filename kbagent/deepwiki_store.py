"""Durable, snapshot-checked Wiki checkpoints and atomic multi-page publication."""

import json
import re
from uuid import uuid4


class DeepWikiStore:
    @staticmethod
    def _check_wiki_snapshot(db, repo):
        current = db.execute(
            "SELECT snapshot_id FROM repositories WHERE id=? AND space_id=? FOR UPDATE",
            (repo["id"], repo["space_id"]),
        ).fetchone()
        if not current or current["snapshot_id"] != repo["snapshot_id"]:
            raise ValueError("代码版本已变化，请重新生成 Wiki")

    @staticmethod
    def _wiki_progress(db, repo):
        progress = db.execute(
            "SELECT * FROM repository_wiki_progress WHERE repository_id=? AND snapshot_id=?",
            (repo["id"], repo["snapshot_id"]),
        ).fetchone()
        return dict(progress) if progress else None

    def save_wiki_progress(self, repo, progress):
        from .store import now

        item = dict(
            progress, repository_id=repo["id"], snapshot_id=repo["snapshot_id"], updated_at=now()
        )
        with self.connection() as db:
            self._check_wiki_snapshot(db, repo)
            columns = ",".join("`" + k + "`" for k in item)
            values = ",".join(":" + k for k in item)
            updates = ",".join(f"`{k}`=VALUES(`{k}`)" for k in item if k != "repository_id")
            db.execute(
                f"INSERT INTO repository_wiki_progress ({columns}) VALUES ({values}) "
                f"ON DUPLICATE KEY UPDATE {updates}",
                item,
            )

    def wiki_checkpoint(self, repo, cache_key):
        with self.connection() as db:
            row = db.execute(
                """SELECT c.content,c.source_ids FROM repository_wiki_cache c
                JOIN repositories r ON r.id=c.repository_id
                WHERE r.id=? AND r.space_id=? AND c.cache_key=?""",
                (repo["id"], repo["space_id"], cache_key),
            ).fetchone()
            return (
                dict(content=row["content"], source_ids=json.loads(row["source_ids"]))
                if row
                else None
            )

    def save_wiki_checkpoint(self, repo, cache_key, stage, content, source_ids):
        from .store import now

        with self.connection() as db:
            self._check_wiki_snapshot(db, repo)
            db.execute(
                """INSERT INTO repository_wiki_cache
                (repository_id,cache_key,stage,content,source_ids,updated_at)
                VALUES (?,?,?,?,?,?) ON DUPLICATE KEY UPDATE content=VALUES(content),
                source_ids=VALUES(source_ids),updated_at=VALUES(updated_at)""",
                (repo["id"], cache_key, stage, content, json.dumps(source_ids), now()),
            )

    def save_repository_wiki_pages(self, repo, pages, cache_keys):
        """Publish all pages and their navigation links in one transaction."""
        from .store import now

        with self.connection() as db:
            self._check_wiki_snapshot(db, repo)
            old = {
                r["path"]: r["document_id"]
                for r in db.execute(
                    "SELECT path,document_id FROM repository_documents "
                    "WHERE repository_id=? AND kind='ai_wiki'",
                    (repo["id"],),
                )
            }
            ids = {path: old.get(path) or uuid4().hex for path in pages}

            def link(match):
                path = match[1]
                if path not in ids:
                    raise ValueError("Wiki 目录包含不存在的页面")
                label = re.sub(r"[\[\]|\n\r]", " ", path)[-180:]
                return f"[[id:{ids[path]}|{label}]]"

            for path, content in pages.items():
                content = re.sub(r"\[\[wiki:([^\]\n]+)\]\]", link, content)
                self._upsert_repository_document(
                    db, repo, "ai_wiki", path, content, repo["snapshot_id"], ids[path]
                )
            for path, doc_id in old.items():
                if path not in pages:
                    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self._resolve_pending(db, repo["space_id"])
            db.execute(
                "UPDATE repositories SET wiki_status='ready',wiki_error='',updated_at=? WHERE id=?",
                (now(), repo["id"]),
            )
            db.execute(
                "UPDATE repository_wiki_progress SET phase='ready',current_path='',updated_at=? "
                "WHERE repository_id=? AND snapshot_id=?",
                (now(), repo["id"], repo["snapshot_id"]),
            )
            # Bound cache growth while retaining every checkpoint needed by this version.
            for row in db.execute(
                "SELECT cache_key FROM repository_wiki_cache WHERE repository_id=?", (repo["id"],)
            ).fetchall():
                if row["cache_key"] not in cache_keys:
                    db.execute(
                        "DELETE FROM repository_wiki_cache WHERE repository_id=? AND cache_key=?",
                        (repo["id"], row["cache_key"]),
                    )
