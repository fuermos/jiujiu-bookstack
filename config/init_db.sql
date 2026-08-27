-- jiujiu-bookstack 数据库初始化
-- PostgreSQL 14+ with pgvector extension
--
-- 适用场景:
--   1. Docker 一键启动 (docker-compose up -d) - 自动跑这个文件
--   2. 挂载到已有 PG: psql -U admin -d jiujiu_mind -f config/init_db.sql
--
-- 表清单 (9 张):
--   books / chunks / chunk_vectors / book_mindmaps
--   game_scripts / tts_audio
--   users / script_play_records (v0.4 用户隔离)
--   sensitive_discoveries (调试用)

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
    cover_url TEXT,                    -- v0.4 自动提取 epub 封面
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

-- ============== book_mindmaps (v0.3: 联合主键支持每剧本一图) ==============
CREATE TABLE IF NOT EXISTS book_mindmaps (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    script_id INTEGER NOT NULL DEFAULT 0,    -- 0=书级汇总; N=第N个剧本
    mermaid TEXT NOT NULL,
    structure JSONB,
    llm_model TEXT,
    png_path TEXT,                          -- v0.3 Playwright 渲染的 PNG 路径
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (book_id, script_id)
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

-- ============== tts_audio (TTS 缓存, v0.4 支持预生成) ==============
CREATE TABLE IF NOT EXISTS tts_audio (
    id SERIAL PRIMARY KEY,
    script_id INTEGER REFERENCES game_scripts(id) ON DELETE CASCADE,
    scene_id TEXT,
    audio_path TEXT NOT NULL,
    voice TEXT,
    duration_seconds NUMERIC,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS tts_audio_script_scene_idx ON tts_audio (script_id, scene_id);

-- ============== users (v0.4 用户隔离) ==============
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,   -- pbkdf2_sha256 + salt
    nickname TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE
);

-- ============== script_play_records (v0.4 剧本使用记录 / 进度恢复) ==============
CREATE TABLE IF NOT EXISTS script_play_records (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    script_id INTEGER NOT NULL,
    player_role TEXT,
    current_scene_idx INTEGER DEFAULT 0,
    game_history JSONB DEFAULT '[]'::jsonb,
    world_state JSONB DEFAULT '{}'::jsonb,
    status TEXT DEFAULT 'playing',  -- playing/completed/paused
    total_score REAL DEFAULT 0,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, script_id)
);
CREATE INDEX IF NOT EXISTS script_play_records_user_idx ON script_play_records (user_id);
CREATE INDEX IF NOT EXISTS script_play_records_status_idx ON script_play_records (status);

-- ============== sensitive_discoveries_log (调试用) ==============
CREATE TABLE IF NOT EXISTS sensitive_discoveries (
    id SERIAL PRIMARY KEY,
    trigger_word TEXT NOT NULL,
    replacement TEXT,
    source_book_id INTEGER,
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
-- ============== pipeline_jobs (v0.5 任务队列, 铲屎官 2026-08-25 钓定) ==============
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id SERIAL PRIMARY KEY,
    book_id INT,
    book_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',  -- queued/running/completed/failed/cancelled
    current_step TEXT DEFAULT 'queued',
    step_progress INT DEFAULT 0,
    step_total INT DEFAULT 0,
    log_path TEXT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS pipeline_jobs_status_idx ON pipeline_jobs(status, id);
