-- ============================================================
-- 008_retrieval_recall.sql — stop the vector index from missing chunks.
-- The original IVFFlat index (lists = 100) is queried with the default
-- ivfflat.probes = 1, i.e. only ~1% of the index is scanned, so the chunk
-- that actually holds a definition is often never considered. HNSW gives
-- near-exact recall without tuning. Also adds a trigram index so the
-- exact-phrase (ILIKE) keyword search used for definitions stays fast.
-- Idempotent; safe to re-run.
-- ============================================================
DROP INDEX IF EXISTS idx_reg_embeddings_vector;
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_vector_hnsw
    ON reg_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_content_trgm
    ON reg_embeddings USING gin (content gin_trgm_ops);

NOTIFY pgrst, 'reload schema';
