"""MySQL 5.7+ InnoDB schema. DDL is explicit; application writes use transactions."""

import re


def require_supported_version(version):
    match = re.match(r"^(\d+)\.(\d+)(?:\.|$)", version)
    if "mariadb" in version.lower() or not match or tuple(map(int, match.groups())) < (5, 7):
        raise RuntimeError(f"MySQL 5.7 or newer is required (server version: {version})")


TABLES = [
    """CREATE TABLE IF NOT EXISTS spaces (
        id VARCHAR(32) PRIMARY KEY, name VARCHAR(100) NOT NULL,
        description VARCHAR(500) NOT NULL, created_at VARCHAR(40) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS documents (
        id VARCHAR(32) PRIMARY KEY, space_id VARCHAR(32) NOT NULL,
        title VARCHAR(200) NOT NULL, content MEDIUMTEXT NOT NULL,
        source VARCHAR(512) NOT NULL, created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL, INDEX documents_space (space_id),
        FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS chunks (
        id VARCHAR(32) PRIMARY KEY, document_id VARCHAR(32) NOT NULL,
        space_id VARCHAR(32) NOT NULL, position INT NOT NULL, content TEXT NOT NULL,
        token_count INT NOT NULL, INDEX chunks_document (document_id),
        INDEX chunks_space (space_id),
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
        FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS chunk_terms (
        chunk_id VARCHAR(32) NOT NULL, space_id VARCHAR(32) NOT NULL,
        term VARCHAR(200) NOT NULL, title_freq INT NOT NULL, body_freq INT NOT NULL,
        PRIMARY KEY (chunk_id,term), INDEX terms_lookup (space_id,term,chunk_id),
        FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
        FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id VARCHAR(32) PRIMARY KEY, space_id VARCHAR(32) NOT NULL,
        title VARCHAR(60) NOT NULL, created_at VARCHAR(40) NOT NULL,
        INDEX conversations_space (space_id),
        FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id BIGINT PRIMARY KEY AUTO_INCREMENT, conversation_id VARCHAR(32) NOT NULL,
        role VARCHAR(16) NOT NULL, content MEDIUMTEXT NOT NULL, sources MEDIUMTEXT NOT NULL,
        mode VARCHAR(16) NOT NULL, created_at VARCHAR(40) NOT NULL,
        INDEX messages_conversation (conversation_id),
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS document_aliases (
        document_id VARCHAR(32) NOT NULL, name VARCHAR(200) NOT NULL,
        PRIMARY KEY (document_id,name), INDEX aliases_name (name),
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS document_links (
        id BIGINT PRIMARY KEY AUTO_INCREMENT, space_id VARCHAR(32) NOT NULL,
        source_id VARCHAR(32) NOT NULL, target_id VARCHAR(32), bound_id VARCHAR(32),
        target_name VARCHAR(200) NOT NULL, anchor VARCHAR(200) NOT NULL,
        label VARCHAR(200) NOT NULL,
        raw TEXT NOT NULL, `start` INT NOT NULL, `end` INT NOT NULL, excerpt TEXT NOT NULL,
        INDEX links_space (space_id), INDEX links_source (source_id),
        INDEX links_target (target_id),
        FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE,
        FOREIGN KEY (source_id) REFERENCES documents(id) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES documents(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE IF NOT EXISTS schema_meta (
        `key` VARCHAR(100) PRIMARY KEY, value VARCHAR(200) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS original_files (
        document_id VARCHAR(32) PRIMARY KEY, object_key VARCHAR(512) NOT NULL UNIQUE,
        filename VARCHAR(255) NOT NULL, content_type VARCHAR(128) NOT NULL,
        size BIGINT NOT NULL, sha256 CHAR(64) NOT NULL,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS object_deletions (
        object_key VARCHAR(512) PRIMARY KEY, not_before DATETIME(6) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS repositories (
        id VARCHAR(32) PRIMARY KEY, space_id VARCHAR(32) NOT NULL, name VARCHAR(100) NOT NULL,
        kind VARCHAR(16) NOT NULL, source VARCHAR(1024) NOT NULL, ref VARCHAR(200) NOT NULL,
        sync_status VARCHAR(16) NOT NULL, wiki_status VARCHAR(16) NOT NULL,
        error VARCHAR(500) NOT NULL, wiki_error VARCHAR(500) NOT NULL,
        snapshot_id VARCHAR(32), commit_sha CHAR(40), file_count INT NOT NULL DEFAULT 0,
        skipped_count INT NOT NULL DEFAULT 0, created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL, INDEX repositories_space (space_id),
        FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS repository_snapshots (
        id VARCHAR(32) PRIMARY KEY, repository_id VARCHAR(32) NOT NULL,
        commit_sha CHAR(40) NOT NULL,
        object_key VARCHAR(512) NOT NULL UNIQUE, file_count INT NOT NULL,
        content_bytes BIGINT NOT NULL, created_at VARCHAR(40) NOT NULL,
        INDEX snapshots_repository (repository_id),
        FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS repository_documents (
        document_id VARCHAR(32) PRIMARY KEY, repository_id VARCHAR(32) NOT NULL,
        kind VARCHAR(16) NOT NULL, path VARCHAR(512) NOT NULL, snapshot_id VARCHAR(32),
        line_count INT NOT NULL, UNIQUE KEY repository_path (repository_id,kind,path),
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
        FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE,
        FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE IF NOT EXISTS repository_wiki_progress (
        repository_id VARCHAR(32) PRIMARY KEY, snapshot_id VARCHAR(32) NOT NULL,
        phase VARCHAR(16) NOT NULL, current_path VARCHAR(512) NOT NULL,
        total_files INT NOT NULL, completed_files INT NOT NULL,
        total_chunks INT NOT NULL, completed_chunks INT NOT NULL,
        total_modules INT NOT NULL, completed_modules INT NOT NULL,
        cache_hits INT NOT NULL, updated_at VARCHAR(40) NOT NULL,
        FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS repository_wiki_cache (
        repository_id VARCHAR(32) NOT NULL, cache_key CHAR(64) NOT NULL,
        stage VARCHAR(16) NOT NULL, content MEDIUMTEXT NOT NULL,
        source_ids MEDIUMTEXT NOT NULL, updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (repository_id,cache_key),
        FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE
    )""",
]


def initialize_schema(db):
    version = db.execute("SELECT VERSION() version").fetchone()["version"]
    require_supported_version(version)
    for ddl in TABLES:
        db.execute(
            ddl + " ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
        )
