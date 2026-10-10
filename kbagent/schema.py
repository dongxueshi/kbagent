"""MySQL 8 InnoDB schema. DDL is explicit; application writes use transactions."""

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
]


def initialize_schema(db):
    version = db.execute("SELECT VERSION() version").fetchone()["version"]
    if "MariaDB" in version or int(version.split(".")[0]) < 8:
        raise RuntimeError("MySQL 8.0 or newer is required")
    for ddl in TABLES:
        db.execute(ddl + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
