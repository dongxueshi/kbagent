"""Repository snapshots and atomically refreshed code/Wiki documents (MySQL 5.7+)."""

from uuid import uuid4


class RepositoryStore:
    def repositories(self, space_id):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM repositories WHERE space_id=? ORDER BY created_at,id",
                    (space_id,),
                )
            ]

    def repository(self, space_id, repository_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM repositories WHERE id=? AND space_id=?", (repository_id, space_id)
            ).fetchone()
            return dict(row) if row else None

    def create_repository(self, space_id, name, kind, source, ref):
        from .store import now

        item = dict(
            id=uuid4().hex,
            space_id=space_id,
            name=name,
            kind=kind,
            source=source,
            ref=ref,
            sync_status="new",
            wiki_status="structure",
            error="",
            wiki_error="",
            snapshot_id=None,
            commit_sha=None,
            file_count=0,
            skipped_count=0,
            created_at=now(),
            updated_at=now(),
        )
        with self.connection() as db:
            names = ",".join("`" + k + "`" for k in item)
            values = ",".join(":" + k for k in item)
            db.execute(f"INSERT INTO repositories ({names}) VALUES ({values})", item)
        return item

    def enqueue_repository(self, space_id, repository_id, job):
        from .store import now

        column = "sync_status" if job == "sync" else "wiki_status"
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM repositories WHERE id=? AND space_id=? FOR UPDATE",
                (repository_id, space_id),
            ).fetchone()
            if not row:
                raise KeyError("仓库不存在")
            if row["sync_status"] in {"queued", "running"} or row["wiki_status"] in {
                "queued",
                "running",
            }:
                raise ValueError("仓库任务正在执行，请稍后再试")
            if job == "wiki" and not row["snapshot_id"]:
                raise ValueError("请先同步仓库代码")
            db.execute(
                f"UPDATE repositories SET {column}='queued',updated_at=? WHERE id=?",
                (now(), repository_id),
            )

    def recover_repository_jobs(self):
        with self.connection() as db:
            for column, error in [("sync_status", "error"), ("wiki_status", "wiki_error")]:
                db.execute(
                    f"UPDATE repositories SET {column}='error',{error}=? WHERE {column}='running'",
                    ("任务因服务重启中断，请重新执行",),
                )

    def claim_repository_job(self):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM repositories WHERE sync_status='queued' "
                "OR wiki_status='queued' ORDER BY updated_at,id LIMIT 1 FOR UPDATE"
            ).fetchone()
            if not row:
                return None
            job = "sync" if row["sync_status"] == "queued" else "wiki"
            column = "sync_status" if job == "sync" else "wiki_status"
            db.execute(f"UPDATE repositories SET {column}='running' WHERE id=?", (row["id"],))
            return dict(row, job=job)

    def repository_failure(self, repository_id, job, message):
        from .store import now

        column, error = ("sync_status", "error") if job == "sync" else ("wiki_status", "wiki_error")
        with self.connection() as db:
            db.execute(
                f"UPDATE repositories SET {column}='error',{error}=?,updated_at=? WHERE id=?",
                (message, now(), repository_id),
            )

    def repository_snapshot(self, space_id, repository_id, snapshot_id=None):
        with self.connection() as db:
            row = db.execute(
                """SELECT s.* FROM repository_snapshots s JOIN repositories r
                ON r.id=s.repository_id WHERE r.space_id=? AND r.id=? AND
                s.id=COALESCE(?,r.snapshot_id)""",
                (space_id, repository_id, snapshot_id),
            ).fetchone()
            return dict(row) if row else None

    def repository_files(self, space_id, repository_id):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    """SELECT f.document_id,f.path,f.kind,f.line_count,
                f.snapshot_id,d.title FROM repository_documents f JOIN documents d
                ON d.id=f.document_id WHERE d.space_id=? AND f.repository_id=?
                ORDER BY f.kind,f.path""",
                    (space_id, repository_id),
                )
            ]

    @staticmethod
    def _repository_source(db, document_id):
        row = db.execute(
            """SELECT f.repository_id,f.kind,f.path,f.snapshot_id,f.line_count,
            r.name repository_name,r.kind repository_kind,r.source repository_source,
            s.commit_sha FROM repository_documents f JOIN repositories r ON r.id=f.repository_id
            LEFT JOIN repository_snapshots s ON s.id=f.snapshot_id WHERE f.document_id=?""",
            (document_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _repository_sources(db, space_id):
        rows = db.execute(
            """SELECT f.document_id,f.repository_id,f.kind,f.path,f.snapshot_id,
            f.line_count,r.name repository_name,r.kind repository_kind,r.source repository_source,
            s.commit_sha FROM repository_documents f JOIN repositories r ON r.id=f.repository_id
            LEFT JOIN repository_snapshots s ON s.id=f.snapshot_id WHERE r.space_id=?""",
            (space_id,),
        ).fetchall()
        result = {}
        for row in rows:
            item = dict(row)
            result[item.pop("document_id")] = item
        return result

    def repository_document_source(self, space_id, document_id):
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM documents WHERE id=? AND space_id=?", (document_id, space_id)
            ).fetchone():
                return None
            return self._repository_source(db, document_id)

    def _upsert_repository_document(self, db, repo, kind, path, content, snapshot_id):
        from .store import now

        old = db.execute(
            """SELECT d.* FROM repository_documents f JOIN documents d
            ON d.id=f.document_id WHERE f.repository_id=? AND f.kind=? AND f.path=?""",
            (repo["id"], kind, path),
        ).fetchone()
        title = (repo["name"] + " · " + path)[-200:]
        item = dict(
            id=old["id"] if old else uuid4().hex,
            space_id=repo["space_id"],
            title=title,
            content=content,
            source="代码仓库 · " + repo["name"],
            created_at=old["created_at"] if old else now(),
            updated_at=now(),
        )
        if old:
            db.execute(
                """UPDATE documents SET title=:title,content=:content,updated_at=:updated_at
                WHERE id=:id""",
                item,
            )
            self._remove_chunks(db, item["id"])
        else:
            db.execute(
                "INSERT INTO documents VALUES "
                "(:id,:space_id,:title,:content,:source,:created_at,:updated_at)",
                item,
            )
        db.execute(
            """INSERT INTO repository_documents
            (document_id,repository_id,kind,path,snapshot_id,line_count)
            VALUES (?,?,?,?,?,?) ON DUPLICATE KEY UPDATE snapshot_id=VALUES(snapshot_id),
            line_count=VALUES(line_count)""",
            (item["id"], repo["id"], kind, path, snapshot_id, len(content.splitlines())),
        )
        if kind == "code":
            db.execute("DELETE FROM document_links WHERE source_id=?", (item["id"],))
        else:
            self._index_links(db, item)
        self._index_chunks(db, item)
        return item["id"]

    def save_repository_snapshot(self, repo, commit_sha, files, skipped, snapshot, wiki_builder):
        from .store import now

        with self.connection() as db:
            current = db.execute(
                "SELECT * FROM repositories WHERE id=? FOR UPDATE", (repo["id"],)
            ).fetchone()
            if not current:
                raise KeyError("仓库不存在")
            db.execute(
                """INSERT INTO repository_snapshots
                (id,repository_id,commit_sha,object_key,file_count,content_bytes,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    snapshot["id"],
                    repo["id"],
                    commit_sha,
                    snapshot["object_key"],
                    len(files),
                    sum(len(f["content"].encode()) for f in files),
                    now(),
                ),
            )
            ids = {
                f["path"]: self._upsert_repository_document(
                    db, repo, "code", f["path"], f["content"], snapshot["id"]
                )
                for f in files
            }
            pages = wiki_builder(repo, commit_sha, files, ids, skipped)
            retained = set(ids.values())
            for path, content in pages.items():
                retained.add(
                    self._upsert_repository_document(
                        db, repo, "wiki", path, content, snapshot["id"]
                    )
                )
            for old in db.execute(
                "SELECT document_id FROM repository_documents WHERE repository_id=?", (repo["id"],)
            ).fetchall():
                if old["document_id"] not in retained:
                    # Old AI wiki would refer to the previous code version; regenerate explicitly.
                    db.execute("DELETE FROM documents WHERE id=?", (old["document_id"],))
            self._resolve_pending(db, repo["space_id"])
            db.execute(
                """UPDATE repositories SET snapshot_id=?,commit_sha=?,file_count=?,
                skipped_count=?,sync_status='ready',wiki_status='structure',error='',wiki_error='',
                updated_at=? WHERE id=?""",
                (snapshot["id"], commit_sha, len(files), skipped, now(), repo["id"]),
            )
            db.execute("DELETE FROM object_deletions WHERE object_key=?", (snapshot["object_key"],))

    def save_repository_ai_wiki(self, repo, content):
        from .store import now

        with self.connection() as db:
            current = db.execute(
                "SELECT snapshot_id FROM repositories WHERE id=? FOR UPDATE", (repo["id"],)
            ).fetchone()
            if not current or current["snapshot_id"] != repo["snapshot_id"]:
                raise ValueError("代码版本已变化，请重新生成 Wiki")
            self._upsert_repository_document(
                db, repo, "ai_wiki", "AI 架构与关键流程", content, repo["snapshot_id"]
            )
            self._resolve_pending(db, repo["space_id"])
            db.execute(
                "UPDATE repositories SET wiki_status='ready',wiki_error='',updated_at=? WHERE id=?",
                (now(), repo["id"]),
            )

    @staticmethod
    def _queue_repository_snapshots(db, space_id, repository_id=None):
        db.execute(
            """INSERT INTO object_deletions (object_key,not_before)
            SELECT s.object_key,UTC_TIMESTAMP(6) FROM repository_snapshots s
            JOIN repositories r ON r.id=s.repository_id WHERE r.space_id=? AND (? IS NULL OR r.id=?)
            ON DUPLICATE KEY UPDATE not_before=UTC_TIMESTAMP(6)""",
            (space_id, repository_id, repository_id),
        )

    def delete_repository(self, space_id, repository_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM repositories WHERE id=? AND space_id=? FOR UPDATE",
                (repository_id, space_id),
            ).fetchone()
            if not row:
                raise KeyError("仓库不存在")
            if row["sync_status"] in {"running", "queued"} or row["wiki_status"] in {
                "running",
                "queued",
            }:
                raise ValueError("请等待仓库任务完成后再删除")
            self._queue_repository_snapshots(db, space_id, repository_id)
            db.execute(
                """DELETE d FROM documents d JOIN repository_documents f ON f.document_id=d.id
                WHERE d.space_id=? AND f.repository_id=?""",
                (space_id, repository_id),
            )
            db.execute(
                "DELETE FROM repositories WHERE id=? AND space_id=?", (repository_id, space_id)
            )
            self._resolve_pending(db, space_id)
