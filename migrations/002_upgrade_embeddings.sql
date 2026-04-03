-- Embedding Model Upgrade Migration: all-MiniLM-L6-v2 (384d) → text-embedding-3-large (1536d)
-- Run this in your Supabase SQL Editor BEFORE re-ingesting documents

-- 1. Delete all existing embeddings (they are incompatible with the new model)
DELETE FROM embeddings;

-- 2. Drop the existing IVFFlat index if it exists (it references the old dimension)
DROP INDEX IF EXISTS idx_embeddings_vector;
DROP INDEX IF EXISTS embeddings_embedding_idx;

-- 3. Update the embedding column dimension
ALTER TABLE embeddings
  ALTER COLUMN embedding TYPE vector(1536);

-- 4. Recreate the index with new dimensions
CREATE INDEX IF NOT EXISTS idx_embeddings_vector
  ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 5. Replace match_embeddings function with new dimension
CREATE OR REPLACE FUNCTION match_embeddings(
    query_embedding vector(1536),
    match_subject_id UUID,
    match_class_id UUID,
    match_school_id UUID,
    match_count INT DEFAULT 5,
    match_threshold FLOAT DEFAULT 0.3
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

-- 6. Mark all documents as not indexed (they need re-ingestion)
UPDATE documents SET is_indexed = FALSE, chunk_count = 0;
