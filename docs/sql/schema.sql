-- Historical conceptual schema, not the desktop V3 initialization schema.
-- Runtime V3 DDL lives in core/app/memory/schema.py and infrastructure/workspace.py.
PRAGMA foreign_keys = ON;

-- Initialize once for SQLite. WAL is a database setting and may be enabled by
-- the application at startup: PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived', 'deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'text',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    memory_type TEXT NOT NULL
        CHECK (memory_type IN ('episodic', 'semantic', 'preference')),
    category TEXT NOT NULL,
    memory_key TEXT,
    content TEXT NOT NULL,
    summary TEXT,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('explicit', 'observed', 'inferred')),
    scope TEXT NOT NULL DEFAULT 'global'
        CHECK (scope IN ('global', 'project', 'thread')),
    scope_ref TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0.0 AND importance <= 1.0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'archived', 'deleted', 'pending')),
    supersedes_id TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE SET NULL,
    FOREIGN KEY (supersedes_id) REFERENCES memory_items(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_user_idempotency
    ON memory_items(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_evidence (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    message_id TEXT,
    evidence_text TEXT,
    evidence_kind TEXT NOT NULL DEFAULT 'message',
    created_at TEXT NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES memory_items(id) ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    profile_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    completeness REAL NOT NULL DEFAULT 0.0
        CHECK (completeness >= 0.0 AND completeness <= 1.0),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_profile_versions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    profile_json TEXT NOT NULL,
    change_summary TEXT,
    source_memory_ids_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, version),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS retrieval_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    query_text TEXT NOT NULL,
    retrieved_memory_ids_json TEXT NOT NULL,
    retrieval_strategy TEXT NOT NULL,
    profile_version INTEGER,
    context_token_estimate INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS interaction_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    message_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE SET NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS memory_feedback (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    memory_id TEXT,
    profile_version INTEGER,
    feedback_type TEXT NOT NULL
        CHECK (feedback_type IN ('confirm', 'correct', 'reject', 'delete', 'disable')),
    feedback_content TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (memory_id) REFERENCES memory_items(id) ON DELETE SET NULL
);

-- Optional mapping to an external vector index (e.g. FAISS).
-- The vector itself is stored outside SQLite and can be rebuilt from memory_items.
CREATE TABLE IF NOT EXISTS memory_embedding_refs (
    memory_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    index_name TEXT NOT NULL,
    external_vector_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_threads_user_updated
    ON threads(user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_thread_created
    ON messages(thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_user_created
    ON messages(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_user_status_type
    ON memory_items(user_id, status, memory_type);

CREATE INDEX IF NOT EXISTS idx_memory_user_category_scope
    ON memory_items(user_id, category, scope);

CREATE INDEX IF NOT EXISTS idx_memory_user_updated
    ON memory_items(user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_evidence_memory
    ON memory_evidence(memory_id);

CREATE INDEX IF NOT EXISTS idx_profile_versions_user_version
    ON user_profile_versions(user_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_retrieval_logs_user_created
    ON retrieval_logs(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_interaction_events_user_created
    ON interaction_events(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_user_created
    ON memory_feedback(user_id, created_at DESC);
