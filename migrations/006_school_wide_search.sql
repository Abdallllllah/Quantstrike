-- ============================================================
-- 006_school_wide_search.sql — subject-agnostic retrieval.
-- Similarity search filtered ONLY by school, so the chat can answer from the
-- knowledge base without the student (or the query) naming a subject.
-- Idempotent; safe to re-run.
-- ============================================================
CREATE OR REPLACE FUNCTION reg_match_embeddings_school(
    query_embedding vector(1536),
    match_school_id UUID,
    match_count INT DEFAULT 6,
    match_threshold FLOAT DEFAULT 0.15
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
    FROM reg_embeddings e
    WHERE e.school_id = match_school_id
      AND 1 - (e.embedding <=> query_embedding) > match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

NOTIFY pgrst, 'reload schema';
