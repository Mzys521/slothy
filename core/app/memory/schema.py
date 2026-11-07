"""V3 additive schema on a freshly reset business store. / 新业务库上的 V3 结构。"""


async def install_schema(connection):
    """Install fact/concept constraints and indexes before serving requests.
    / 服务启动前建立约束和索引。"""
    async with connection.executescript("""
        CREATE TABLE IF NOT EXISTS memory_concepts (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, concept_key TEXT NOT NULL,
            kind TEXT NOT NULL, label TEXT NOT NULL, aliases_json TEXT NOT NULL DEFAULT '[]',
            scope TEXT NOT NULL, scope_ref TEXT NOT NULL DEFAULT '',
            UNIQUE(user_id,scope,scope_ref,kind,concept_key)
        );
        CREATE TABLE IF NOT EXISTS memory_reinforcements (
            memory_id TEXT NOT NULL REFERENCES workspace_memories(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
            PRIMARY KEY(memory_id,session_id)
        );
        CREATE TABLE IF NOT EXISTS memory_fact_concepts (
            memory_id TEXT NOT NULL REFERENCES workspace_memories(id) ON DELETE CASCADE,
            concept_id TEXT NOT NULL REFERENCES memory_concepts(id) ON DELETE CASCADE,
            PRIMARY KEY(memory_id,concept_id)
        );
        CREATE INDEX IF NOT EXISTS fact_concept_reverse ON memory_fact_concepts(concept_id);
        CREATE TABLE IF NOT EXISTS concept_reinforcements (
            concept_id TEXT NOT NULL REFERENCES memory_concepts(id) ON DELETE CASCADE,
            scope TEXT NOT NULL, scope_ref TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL,
            PRIMARY KEY(concept_id,scope,scope_ref,session_id)
        );
        CREATE TABLE IF NOT EXISTS memory_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            event TEXT NOT NULL, memory_id TEXT, reason TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
            memory_id UNINDEXED, user_id UNINDEXED, terms, tokenize='unicode61'
        );
    """):
        pass
    async with connection.execute("PRAGMA table_info(workspace_memories)") as rows:
        columns = {row[1] async for row in rows}
    for name, definition in (
        ("memory_key", "TEXT"),
        ("dimension", "TEXT NOT NULL DEFAULT 'fact'"),
        ("value_json", "TEXT NOT NULL DEFAULT 'null'"),
        ("predicate", "TEXT NOT NULL DEFAULT 'related'"),
        ("subject", "TEXT NOT NULL DEFAULT 'user'"),
        ("source_concept_id", "TEXT"),
        ("target_concept_id", "TEXT"),
        ("effective_frequency", "INTEGER NOT NULL DEFAULT 0 CHECK(effective_frequency>=0)"),
        ("protected", "INTEGER NOT NULL DEFAULT 0"),
        ("superseded_by", "TEXT"),
    ):
        if name not in columns:
            await connection.execute(
                f"ALTER TABLE workspace_memories ADD COLUMN {name} {definition}"
            )
    async with connection.execute("PRAGMA table_info(memory_claims)") as rows:
        claim_columns = {row[1] async for row in rows}
    if "semantic_json" not in claim_columns:
        await connection.execute(
            "ALTER TABLE memory_claims ADD COLUMN semantic_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "pending_reason" not in claim_columns:
        await connection.execute("ALTER TABLE memory_claims ADD COLUMN pending_reason TEXT")
    async with connection.execute("PRAGMA table_info(memory_evidence)") as rows:
        evidence_columns = {row[1] async for row in rows}
    if "qualified" not in evidence_columns:
        await connection.execute(
            "ALTER TABLE memory_evidence ADD COLUMN qualified INTEGER NOT NULL DEFAULT 0"
        )
    async with connection.execute("PRAGMA table_info(workspace_profiles)") as rows:
        profile_columns = {row[1] async for row in rows}
    if "profile_json" in profile_columns:
        await connection.execute("ALTER TABLE workspace_profiles DROP COLUMN profile_json")
    await connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS memory_current_slot
        ON workspace_memories(user_id,scope,coalesce(scope_ref,''),memory_key)
        WHERE status='active' AND memory_key IS NOT NULL""")
    for event in ("INSERT", "UPDATE", "DELETE"):
        ref = "OLD" if event == "DELETE" else "NEW"
        await connection.executescript(f"""
            CREATE TRIGGER IF NOT EXISTS concept_revision_{event.lower()}
            AFTER {event} ON memory_concepts BEGIN
                INSERT INTO memory_meta(user_id,memory_index_version) VALUES({ref}.user_id,1)
                ON CONFLICT(user_id) DO UPDATE SET memory_index_version=memory_index_version+1;
            END;
        """)
