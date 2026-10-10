"""Explicit, non-destructive migration of a legacy SQLite snapshot into empty MySQL."""

import sqlite3
from contextlib import closing
from pathlib import Path

from .store import Store

COPY_TABLES = {
    "spaces": ["id", "name", "description", "created_at"],
    "documents": ["id", "space_id", "title", "content", "source", "created_at", "updated_at"],
    "conversations": ["id", "space_id", "title", "created_at"],
    "messages": ["id", "conversation_id", "role", "content", "sources", "mode", "created_at"],
    "document_aliases": ["document_id", "name"],
    "document_links": [
        "id",
        "space_id",
        "source_id",
        "target_id",
        "bound_id",
        "target_name",
        "anchor",
        "label",
        "raw",
        "start",
        "end",
        "excerpt",
    ],
}


def migrate_sqlite(source: Path, settings):
    source = source.resolve(strict=True)
    store = Store(settings)
    store.initialize()
    # mode=ro forbids writes to the legacy DB; callers should stop the old app first.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
        old.row_factory = sqlite3.Row
        old.execute("BEGIN")
        tables = {
            row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"spaces", "documents", "conversations", "messages"} <= tables:
            raise RuntimeError("Source is not a supported KB Agent SQLite database")
        counts = {}
        with store.connection() as db:
            # Serialize migration attempts and block concurrent space inserts until commit.
            db.execute("SELECT id FROM spaces FOR UPDATE").fetchall()
            for table in [*COPY_TABLES, "chunks", "original_files", "object_deletions"]:
                if db.execute(f"SELECT count(*) FROM `{table}`").fetchone()[0]:
                    raise RuntimeError(
                        "Migration requires an empty destination; existing data was not changed"
                    )
            for table, columns in COPY_TABLES.items():
                if table not in tables:
                    continue
                names = ",".join(f"`{name}`" for name in columns)
                rows = old.execute(f"SELECT {names} FROM `{table}`").fetchall()
                placeholders = ",".join("?" for _ in columns)
                db.executemany(
                    f"INSERT INTO `{table}` ({names}) VALUES ({placeholders})",
                    [tuple(row) for row in rows],
                )
                counts[table] = len(rows)
            for row in db.execute("SELECT * FROM documents").fetchall():
                store._index_chunks(db, row)
                if "document_links" not in tables:
                    store._index_links(db, row)
            if "document_links" not in tables:
                for row in db.execute("SELECT id FROM spaces").fetchall():
                    store._resolve_pending(db, row[0])
        return counts
