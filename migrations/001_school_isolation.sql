-- School Isolation Migration
-- Run this in your Supabase SQL Editor BEFORE deploying the new code

-- 1. Schools table
CREATE TABLE IF NOT EXISTS schools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Add school_id columns to existing tables
ALTER TABLE users      ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id);
ALTER TABLE documents  ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id);
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id);
ALTER TABLE messages   ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id);

-- 3. Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_embeddings_school ON embeddings(school_id);
CREATE INDEX IF NOT EXISTS idx_documents_school  ON documents(school_id);
CREATE INDEX IF NOT EXISTS idx_users_school      ON users(school_id);
CREATE INDEX IF NOT EXISTS idx_messages_school   ON messages(school_id);

-- 4. Replace match_embeddings RPC to include school filter
CREATE OR REPLACE FUNCTION match_embeddings(
    query_embedding vector(384),
    match_subject_id UUID,
    match_class_id UUID,
    match_school_id UUID,
    match_count INT DEFAULT 5,
    match_threshold FLOAT DEFAULT 0.2
)
RETURNS TABLE (
    id UUID,
    content TEXT,
    page_number INT,
    chunk_index INT,
    metadata JSONB,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        e.id,
        e.content,
        e.page_number,
        e.chunk_index,
        e.metadata,
        1 - (e.embedding <=> query_embedding) AS similarity
    FROM embeddings e
    WHERE e.subject_id = match_subject_id
      AND e.class_id   = match_class_id
      AND e.school_id  = match_school_id
      AND 1 - (e.embedding <=> query_embedding) > match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
