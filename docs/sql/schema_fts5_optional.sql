-- Optional FTS5 extension. Execute only if the SQLite build supports FTS5.
-- Keep this index rebuildable from memory_items; it is not the source of truth.

CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
    memory_id UNINDEXED,
    user_id UNINDEXED,
    content,
    summary,
    tokenize = 'unicode61'
);

-- Application code should update the FTS table in the same logical write flow
-- as memory_items. Triggers below cover insert/update/delete for the indexed text.

CREATE TRIGGER IF NOT EXISTS trg_memory_fts_insert
AFTER INSERT ON memory_items
BEGIN
    INSERT INTO memory_items_fts(memory_id, user_id, content, summary)
    VALUES (new.id, new.user_id, new.content, COALESCE(new.summary, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_fts_update
AFTER UPDATE OF content, summary, user_id ON memory_items
BEGIN
    DELETE FROM memory_items_fts WHERE memory_id = old.id;
    INSERT INTO memory_items_fts(memory_id, user_id, content, summary)
    VALUES (new.id, new.user_id, new.content, COALESCE(new.summary, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_fts_delete
AFTER DELETE ON memory_items
BEGIN
    DELETE FROM memory_items_fts WHERE memory_id = old.id;
END;
