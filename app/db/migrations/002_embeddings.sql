-- Multi-Subject AI Tutor - Embeddings with pgvector
-- Migration: 002_embeddings.sql
-- Run this in your Supabase SQL Editor AFTER 001_initial.sql

-- ============================================
-- ENABLE PGVECTOR EXTENSION
-- ============================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================
-- EMBEDDINGS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    subject_id UUID REFERENCES subjects(id) ON DELETE CASCADE,
    class_id UUID REFERENCES classes(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding VECTOR(384),  -- all-MiniLM-L6-v2 produces 384-dim vectors
    page_number INTEGER,
    chunk_index INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- INDEXES FOR VECTOR SEARCH
-- ============================================

-- Composite index for class-scoped queries (filter before vector search)
CREATE INDEX IF NOT EXISTS idx_embeddings_class_scope 
    ON embeddings(subject_id, class_id);

-- IVFFlat index for approximate nearest neighbor search
-- Note: This index is best created AFTER you have some data (1000+ rows)
-- For initial setup, we create a smaller index
CREATE INDEX IF NOT EXISTS idx_embeddings_vector 
    ON embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- ============================================
-- SIMILARITY SEARCH FUNCTION
-- ============================================
CREATE OR REPLACE FUNCTION match_embeddings(
    query_embedding VECTOR(384),
    match_subject_id UUID,
    match_class_id UUID,
    match_count INT DEFAULT 5,
    match_threshold FLOAT DEFAULT 0.7
)
RETURNS TABLE (
    id UUID,
    content TEXT,
    page_number INTEGER,
    chunk_index INTEGER,
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
      AND e.class_id = match_class_id
      AND 1 - (e.embedding <=> query_embedding) > match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ============================================
-- HELPER FUNCTION: Get document chunks
-- ============================================
CREATE OR REPLACE FUNCTION get_document_chunks(
    doc_id UUID
)
RETURNS TABLE (
    chunk_id UUID,
    content TEXT,
    page_number INTEGER,
    chunk_index INTEGER
)
LANGUAGE sql
AS $$
    SELECT id, content, page_number, chunk_index
    FROM embeddings
    WHERE document_id = doc_id
    ORDER BY chunk_index;
$$;

-- ============================================
-- HELPER FUNCTION: Delete document embeddings
-- ============================================
CREATE OR REPLACE FUNCTION delete_document_embeddings(
    doc_id UUID
)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM embeddings WHERE document_id = doc_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;
