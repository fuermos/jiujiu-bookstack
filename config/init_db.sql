-- jiujiu-bookstack 数据库初始化
-- PostgreSQL 14+ with pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;

-- ============== books ==============
CREATE TABLE IF NOT EXISTS books (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT,
    category TEXT,
    md5 TEXT UNIQUE,
    summary TEXT,
    summary_generated_at TIMESTAMP WITH TIME ZONE,
    total_scenes INTEGER DEFAULT 0,
    game_type TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS books_category_idx ON books (category);
CREATE INDEX IF NOT EXISTS books_md5_idx ON books (md5);

-- ============== chunks ==============
CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_index INTEGER NOT NULL DEFAULT 0,
    chunk_text TEXT NOT NULL,
    char_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS chunks_book_idx ON chunks (book_id);
CREATE INDEX IF NOT EXISTS chunks_book_chapter_idx ON chunks (book_id, chapter_index);
-- 防重复导入
CREATE UNIQUE INDEX IF NOT EXISTS chunks_book_md5_uniq
    ON chunks (book_id, MD5(chunk_text));

-- ============== chunk_vectors (pgvector) ==============
CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    embedding vector(1024) NOT NULL
);
-- vector 索引（IVFFlat 适合百万级数据）
CREATE INDEX IF NOT EXISTS chunk_vectors_embedding_idx
    ON chunk_vectors USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============== book_mindmaps ==============
CREATE TABLE IF NOT EXISTS book_mindmaps (
    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    mermaid TEXT NOT NULL,
    structure JSONB,
    llm_model TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS book_mindmaps_updated_idx ON book_mindmaps (updated_at DESC);

-- ============== game_scripts ==============
CREATE TABLE IF NOT EXISTS game_scripts (
    id SERIAL PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    game_type TEXT NOT NULL,
    chapter_index INTEGER DEFAULT 0,
    script_json JSONB NOT NULL,
    script_hash TEXT,
    total_scenes INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ready',
    provider TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS game_scripts_book_idx ON game_scripts (book_id);
CREATE INDEX IF NOT EXISTS game_scripts_status_idx ON game_scripts (status);
-- 防双重 v2_ 前缀 + 重复数据
CREATE UNIQUE INDEX IF NOT EXISTS game_scripts_uniq
    ON game_scripts (book_id, chapter_index, game_type);

-- ============== tts_audio (可选, TTS 缓存) ==============
CREATE TABLE IF NOT EXISTS tts_audio (
    id SERIAL PRIMARY KEY,
    script_id INTEGER REFERENCES game_scripts(id) ON DELETE CASCADE,
    scene_id TEXT,
    audio_path TEXT NOT NULL,
    voice TEXT,
    duration_seconds NUMERIC,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============== sensitive_discoveries_log (可选, 调试用) ==============
CREATE TABLE IF NOT EXISTS sensitive_discoveries (
    id SERIAL PRIMARY KEY,
    trigger_word TEXT NOT NULL,
    replacement TEXT,
    source_book_id INTEGER,
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
