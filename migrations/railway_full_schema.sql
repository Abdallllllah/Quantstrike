-- ============================================================
-- railway_full_schema.sql — COMPLETE schema for a fresh self-hosted
-- Supabase (e.g. on Railway). Run ONCE on the new, empty database.
--
-- Aggregates, in their final state:
--   000_full_setup (reg_*) + 004_edu_app + 005_chat + 006_school_wide_search
--   + 007_subjects_classes + 008_retrieval_recall
--   + business_setup / business_whatsapp / business_add_message_id (biz_*)
--   + sql/tier_migration (gateway: users, messages, documents, practice_sessions)
--   + storage buckets (reg_documents, GCEResources)
--
-- NON-DESTRUCTIVE: no DROPs of tables. Every statement is idempotent.
--
-- Data: after running this, load data with a DATA-ONLY dump:
--   pg_dump "OLD_URI" --data-only --schema=public --no-owner --no-privileges \
--           --disable-triggers -Fc -f data.dump
--   pg_restore -d "NEW_URI" --data-only --no-owner --no-privileges \
--           --disable-triggers data.dump
-- (Skip the seed INSERTs below if you prefer; ON CONFLICT makes them harmless,
--  but a data-only restore of reg_subjects/reg_classes will then hit duplicate
--  keys — see the note at the SEED section.)
--
-- NOTE: the gateway tables (users, messages, documents) have no CREATE in the
-- repo; they are reconstructed from the columns the code reads/writes. Compare
-- with the live DB before relying on them (see bottom of file).
-- ============================================================

-- ---------- EXTENSIONS ----------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ============================================================
-- EDU APP (reg_*)
-- ============================================================

-- ---------- SCHOOLS (multi-tenant isolation) ----------
CREATE TABLE IF NOT EXISTS reg_schools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------- SUBJECTS ----------
CREATE TABLE IF NOT EXISTS reg_subjects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL UNIQUE,
    slug VARCHAR(50) NOT NULL UNIQUE,
    prompt_template TEXT,
    retrieval_k INTEGER DEFAULT 5,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ---------- CLASSES ----------
CREATE TABLE IF NOT EXISTS reg_classes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    subject_id UUID REFERENCES reg_subjects(id) ON DELETE CASCADE,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_classes_subject ON reg_classes(subject_id);

-- ---------- USERS ----------
CREATE TABLE IF NOT EXISTS reg_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    display_name VARCHAR(100),
    school_id UUID REFERENCES reg_schools(id),
    role TEXT NOT NULL DEFAULT 'student',
    enrolled_by UUID REFERENCES reg_users(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT TRUE,
    current_subject_id UUID REFERENCES reg_subjects(id),
    current_class_id UUID REFERENCES reg_classes(id),
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT reg_users_role_check CHECK (role IN ('school_admin', 'teacher', 'student'))
);
CREATE INDEX IF NOT EXISTS idx_reg_users_phone       ON reg_users(phone_number);
CREATE INDEX IF NOT EXISTS idx_reg_users_school      ON reg_users(school_id);
CREATE INDEX IF NOT EXISTS idx_reg_users_role        ON reg_users(role);
CREATE INDEX IF NOT EXISTS idx_reg_users_enrolled_by ON reg_users(enrolled_by);
CREATE INDEX IF NOT EXISTS idx_reg_users_school_role ON reg_users(school_id, role);

-- ---------- MESSAGES (chat history) ----------
CREATE TABLE IF NOT EXISTS reg_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES reg_users(id) ON DELETE CASCADE,
    school_id UUID REFERENCES reg_schools(id),
    subject_id UUID REFERENCES reg_subjects(id),
    class_id UUID REFERENCES reg_classes(id),
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    intent VARCHAR(50),
    metadata JSONB DEFAULT '{}',
    conversation_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_messages_user_time    ON reg_messages(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reg_messages_context      ON reg_messages(user_id, subject_id, class_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reg_messages_school       ON reg_messages(school_id);
CREATE INDEX IF NOT EXISTS idx_reg_messages_conversation ON reg_messages(user_id, conversation_id, created_at);

-- ---------- DOCUMENTS ----------
CREATE TABLE IF NOT EXISTS reg_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    school_id UUID REFERENCES reg_schools(id),
    subject_id UUID REFERENCES reg_subjects(id) ON DELETE CASCADE,
    class_id UUID REFERENCES reg_classes(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    doc_type VARCHAR(50) DEFAULT 'notes',
    chunk_count INTEGER DEFAULT 0,
    is_indexed BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_documents_subject_class ON reg_documents(subject_id, class_id);
CREATE INDEX IF NOT EXISTS idx_reg_documents_school        ON reg_documents(school_id);

-- ---------- EMBEDDINGS (pgvector, 1536-dim, text-embedding-3-large) ----------
CREATE TABLE IF NOT EXISTS reg_embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID REFERENCES reg_documents(id) ON DELETE CASCADE,
    school_id UUID REFERENCES reg_schools(id),
    subject_id UUID REFERENCES reg_subjects(id) ON DELETE CASCADE,
    class_id UUID REFERENCES reg_classes(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding VECTOR(1536),
    page_number INTEGER,
    chunk_index INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_class_scope ON reg_embeddings(subject_id, class_id);
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_school      ON reg_embeddings(school_id);
-- Used by retrieval to fetch neighbouring chunks of a hit.
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_doc_chunk   ON reg_embeddings(document_id, chunk_index);
-- HNSW (not IVFFlat): near-exact recall without probe tuning.
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_vector_hnsw
    ON reg_embeddings USING hnsw (embedding vector_cosine_ops);
-- Fast ILIKE for the exact-phrase definition search.
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_content_trgm
    ON reg_embeddings USING gin (content gin_trgm_ops);

-- ---------- ASSIGNMENTS ----------
CREATE TABLE IF NOT EXISTS reg_assignments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    school_id  UUID REFERENCES reg_schools(id)  ON DELETE CASCADE,
    teacher_id UUID REFERENCES reg_users(id)    ON DELETE CASCADE,
    subject_id UUID REFERENCES reg_subjects(id) ON DELETE SET NULL,
    class_id   UUID REFERENCES reg_classes(id)  ON DELETE SET NULL,
    title       VARCHAR(200) NOT NULL,
    description TEXT,
    due_date    TIMESTAMPTZ,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_assignments_school  ON reg_assignments(school_id);
CREATE INDEX IF NOT EXISTS idx_reg_assignments_teacher ON reg_assignments(teacher_id);

CREATE TABLE IF NOT EXISTS reg_assignment_targets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id UUID REFERENCES reg_assignments(id) ON DELETE CASCADE,
    student_id    UUID REFERENCES reg_users(id)       ON DELETE CASCADE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (assignment_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_assignment_targets_student ON reg_assignment_targets(student_id);

CREATE TABLE IF NOT EXISTS reg_assignment_submissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id UUID REFERENCES reg_assignments(id) ON DELETE CASCADE,
    student_id    UUID REFERENCES reg_users(id)       ON DELETE CASCADE,
    content   TEXT,
    grade     VARCHAR(50),
    feedback  TEXT,
    status    VARCHAR(20) DEFAULT 'submitted',  -- submitted | graded
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    graded_at    TIMESTAMPTZ,
    UNIQUE (assignment_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_submissions_assignment ON reg_assignment_submissions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_reg_submissions_student    ON reg_assignment_submissions(student_id);

-- ---------- updated_at trigger ----------
CREATE OR REPLACE FUNCTION reg_update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_reg_subjects_updated_at    ON reg_subjects;
DROP TRIGGER IF EXISTS update_reg_classes_updated_at     ON reg_classes;
DROP TRIGGER IF EXISTS update_reg_users_updated_at       ON reg_users;
DROP TRIGGER IF EXISTS update_reg_documents_updated_at   ON reg_documents;
DROP TRIGGER IF EXISTS update_reg_assignments_updated_at ON reg_assignments;
CREATE TRIGGER update_reg_subjects_updated_at    BEFORE UPDATE ON reg_subjects    FOR EACH ROW EXECUTE FUNCTION reg_update_updated_at_column();
CREATE TRIGGER update_reg_classes_updated_at     BEFORE UPDATE ON reg_classes     FOR EACH ROW EXECUTE FUNCTION reg_update_updated_at_column();
CREATE TRIGGER update_reg_users_updated_at       BEFORE UPDATE ON reg_users       FOR EACH ROW EXECUTE FUNCTION reg_update_updated_at_column();
CREATE TRIGGER update_reg_documents_updated_at   BEFORE UPDATE ON reg_documents   FOR EACH ROW EXECUTE FUNCTION reg_update_updated_at_column();
CREATE TRIGGER update_reg_assignments_updated_at BEFORE UPDATE ON reg_assignments FOR EACH ROW EXECUTE FUNCTION reg_update_updated_at_column();

-- ---------- RPC: similarity search (subject + class + school) ----------
CREATE OR REPLACE FUNCTION reg_match_embeddings(
    query_embedding vector(1536),
    match_subject_id UUID,
    match_class_id UUID,
    match_school_id UUID,
    match_count INT DEFAULT 5,
    match_threshold FLOAT DEFAULT 0.3
)
RETURNS TABLE (id UUID, content TEXT, page_number INT, chunk_index INT, metadata JSONB, similarity FLOAT)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT e.id, e.content, e.page_number, e.chunk_index, e.metadata,
           1 - (e.embedding <=> query_embedding) AS similarity
    FROM reg_embeddings e
    WHERE e.subject_id = match_subject_id
      AND e.class_id   = match_class_id
      AND e.school_id  = match_school_id
      AND 1 - (e.embedding <=> query_embedding) > match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ---------- RPC: similarity search (school-wide, used by the chat) ----------
CREATE OR REPLACE FUNCTION reg_match_embeddings_school(
    query_embedding vector(1536),
    match_school_id UUID,
    match_count INT DEFAULT 6,
    match_threshold FLOAT DEFAULT 0.15
)
RETURNS TABLE (id UUID, content TEXT, page_number INT, chunk_index INT, metadata JSONB, similarity FLOAT)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT e.id, e.content, e.page_number, e.chunk_index, e.metadata,
           1 - (e.embedding <=> query_embedding) AS similarity
    FROM reg_embeddings e
    WHERE e.school_id = match_school_id
      AND 1 - (e.embedding <=> query_embedding) > match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ---------- RPC helpers ----------
CREATE OR REPLACE FUNCTION reg_get_document_chunks(doc_id UUID)
RETURNS TABLE (chunk_id UUID, content TEXT, page_number INTEGER, chunk_index INTEGER)
LANGUAGE sql
AS $$
    SELECT id, content, page_number, chunk_index
    FROM reg_embeddings
    WHERE document_id = doc_id
    ORDER BY chunk_index;
$$;

CREATE OR REPLACE FUNCTION reg_delete_document_embeddings(doc_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM reg_embeddings WHERE document_id = doc_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


-- ============================================================
-- BUSINESS / TARA (biz_*) — isolated from the edu app
-- ============================================================
CREATE TABLE IF NOT EXISTS biz_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number VARCHAR(30) UNIQUE NOT NULL,
    name       VARCHAR(120),
    shop_name  VARCHAR(160),
    language   VARCHAR(10) DEFAULT 'en',
    currency   VARCHAR(10) DEFAULT 'KSh',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_users_phone ON biz_users(phone_number);

CREATE TABLE IF NOT EXISTS biz_customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES biz_users(id) ON DELETE CASCADE,
    name   VARCHAR(160) NOT NULL,
    phone  VARCHAR(30),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_biz_customers_user ON biz_customers(user_id);

CREATE TABLE IF NOT EXISTS biz_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES biz_users(id) ON DELETE CASCADE,
    name  VARCHAR(160) NOT NULL,
    unit  VARCHAR(30),
    cost_price NUMERIC(14,2),
    sell_price NUMERIC(14,2),
    stock_qty  NUMERIC(14,3) DEFAULT 0,
    low_stock_threshold NUMERIC(14,3) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_biz_items_user ON biz_items(user_id);

CREATE TABLE IF NOT EXISTS biz_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES biz_users(id) ON DELETE CASCADE,
    type VARCHAR(20) NOT NULL CHECK (type IN ('sale','restock','credit','payment','expense','other')),
    item_name  VARCHAR(200),
    item_id    UUID REFERENCES biz_items(id) ON DELETE SET NULL,
    quantity   NUMERIC(14,3),
    unit       VARCHAR(30),
    amount        NUMERIC(14,2),
    credit_amount NUMERIC(14,2) DEFAULT 0,
    unit_price NUMERIC(14,2),
    cost_price NUMERIC(14,2),
    customer_id   UUID REFERENCES biz_customers(id) ON DELETE SET NULL,
    customer_name VARCHAR(160),
    direction  VARCHAR(4),
    source     VARCHAR(10) DEFAULT 'text',
    raw_text   TEXT,
    message_id UUID,
    occurred_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_tx_user_time ON biz_transactions(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_biz_tx_customer  ON biz_transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_biz_tx_type      ON biz_transactions(user_id, type);
CREATE INDEX IF NOT EXISTS idx_biz_tx_message   ON biz_transactions(message_id);

CREATE TABLE IF NOT EXISTS biz_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES biz_users(id) ON DELETE CASCADE,
    role    VARCHAR(12) NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_messages_user ON biz_messages(user_id, created_at);

CREATE TABLE IF NOT EXISTS biz_wa_events (
    wamid      TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_wa_events_time ON biz_wa_events(created_at);

CREATE OR REPLACE FUNCTION biz_set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_biz_users_updated     ON biz_users;
DROP TRIGGER IF EXISTS trg_biz_customers_updated ON biz_customers;
DROP TRIGGER IF EXISTS trg_biz_items_updated     ON biz_items;
CREATE TRIGGER trg_biz_users_updated     BEFORE UPDATE ON biz_users     FOR EACH ROW EXECUTE FUNCTION biz_set_updated_at();
CREATE TRIGGER trg_biz_customers_updated BEFORE UPDATE ON biz_customers FOR EACH ROW EXECUTE FUNCTION biz_set_updated_at();
CREATE TRIGGER trg_biz_items_updated     BEFORE UPDATE ON biz_items     FOR EACH ROW EXECUTE FUNCTION biz_set_updated_at();


-- ============================================================
-- GATEWAY (WhatsApp tutor / past papers / practice mode)
-- Reconstructed from app/gateway/routes/* — VERIFY against the live DB.
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name   TEXT,
    last_name    TEXT,
    username     TEXT,
    phone_number TEXT UNIQUE,
    school_name  TEXT,
    grade_level  TEXT,
    curriculum   TEXT,
    role         TEXT DEFAULT 'student',
    schoolid     UUID,
    is_active    BOOLEAN DEFAULT TRUE,
    tier         SMALLINT NOT NULL DEFAULT 1 CHECK (tier IN (1, 2, 3)),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    messageid       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    userid          UUID,
    message         TEXT,
    role            TEXT,              -- 'student' | 'tutor'
    "timestamp"     TIMESTAMPTZ DEFAULT NOW(),
    processed       BOOLEAN DEFAULT FALSE,
    conversation_id UUID
);
CREATE INDEX IF NOT EXISTS messages_userid_role_timestamp_idx
    ON messages (userid, role, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS messages_conversation_timestamp_idx
    ON messages (conversation_id, "timestamp" ASC);

-- Past-paper index (files live in the GCEResources bucket).
CREATE TABLE IF NOT EXISTS documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject      TEXT,
    year         INTEGER,
    paper_number TEXT,
    curriculum   TEXT,
    level        TEXT,
    url          TEXT,
    "timestamp"  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS documents_lookup_idx
    ON documents (subject, curriculum, paper_number, year, level);

CREATE TABLE IF NOT EXISTS practice_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    userid          uuid NOT NULL,
    subject         text NOT NULL,
    topics          jsonb NOT NULL DEFAULT '[]'::jsonb,
    class_level     text NOT NULL DEFAULT 'a-level',
    difficulty      text NOT NULL DEFAULT 'mixed'
                    CHECK (difficulty IN ('easy','medium','hard','mixed')),
    questions       jsonb NOT NULL DEFAULT '[]'::jsonb,
    answers         jsonb NOT NULL DEFAULT '{}'::jsonb,
    score           numeric(5,2),
    status          text NOT NULL DEFAULT 'in_progress'
                    CHECK (status IN ('in_progress','completed','abandoned')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz
);
CREATE INDEX IF NOT EXISTS practice_sessions_user_started_idx ON practice_sessions (userid, started_at DESC);
CREATE INDEX IF NOT EXISTS practice_sessions_user_status_idx  ON practice_sessions (userid, status);


-- ============================================================
-- SEED DATA
-- If you will restore data from the old DB with a data-only dump, SKIP this
-- section: the dump already contains these rows (with their original ids),
-- and these seeds would create rows with different ids that clash on
-- name/slug.
-- ============================================================
-- INSERT INTO reg_subjects (name, slug, prompt_template, retrieval_k) VALUES
-- ('Mathematics',         'math',                'You are a mathematics tutor. Focus on step-by-step calculations and clear explanations of formulas.', 5),
-- ('Physics',             'physics',             'You are a physics tutor. Explain concepts with real-world examples and include relevant equations.', 5),
-- ('Chemistry',           'chemistry',           'You are a chemistry tutor. Explain reactions, molecular structures, and chemical principles clearly.', 5),
-- ('Biology',             'biology',             NULL, 5),
-- ('Further Mathematics', 'further-mathematics', NULL, 5),
-- ('Computer Science',    'computer-science',    NULL, 5),
-- ('ICT',                 'ict',                 NULL, 5),
-- ('Economics',           'economics',           NULL, 5),
-- ('Geography',           'geography',           NULL, 5),
-- ('History',             'history',             NULL, 5)
-- ON CONFLICT (slug) DO NOTHING;
--
-- INSERT INTO reg_classes (name, subject_id)
-- SELECT 'A-Level', s.id FROM reg_subjects s
-- ON CONFLICT (name, subject_id) DO NOTHING;


-- ============================================================
-- STORAGE BUCKETS
-- ============================================================
INSERT INTO storage.buckets (id, name, public)
VALUES ('reg_documents', 'reg_documents', false)
ON CONFLICT (id) DO NOTHING;

-- Past papers are served via get_public_url(), so this bucket is public.
INSERT INTO storage.buckets (id, name, public)
VALUES ('GCEResources', 'GCEResources', true)
ON CONFLICT (id) DO NOTHING;


-- ============================================================
-- API ROLE GRANTS — backend only.
-- There is no row-level security on these tables, so only service_role gets
-- access; the public anon key can read nothing. Set BOTH SUPABASE_KEY and
-- SUPABASE_SERVICE_ROLE_KEY to the service-role key.
-- ============================================================
GRANT USAGE ON SCHEMA public TO service_role;
GRANT ALL ON ALL TABLES    IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO service_role;
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;

NOTIFY pgrst, 'reload schema';


-- ============================================================
-- VERIFY THE GATEWAY TABLES against the old DB. Run this on BOTH databases
-- and compare; add any column the old one has that this file lacks:
--
-- SELECT table_name, column_name, data_type
-- FROM information_schema.columns
-- WHERE table_schema = 'public'
--   AND table_name IN ('users', 'messages', 'documents', 'practice_sessions')
-- ORDER BY table_name, ordinal_position;
-- ============================================================
