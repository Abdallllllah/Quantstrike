-- ============================================================
-- Quant AI Tutor — FULL DATABASE SETUP (run once on a new DB)
-- All objects are prefixed with "reg_" for distinction/isolation.
-- Consolidates: 001_initial + 002_embeddings + school isolation
--               + 1536-dim embedding upgrade + biology subject.
-- Paste this whole file into the Supabase SQL Editor and run.
-- Final embedding model: OpenAI text-embedding-3-large (1536 dims)
-- ============================================================

-- ============================================================
-- RESET (DESTRUCTIVE): drop pre-existing reg_ tables/functions so the
-- CREATE statements below build a clean, correct schema.
-- Runs in reverse-dependency order. CASCADE also removes dependent
-- objects (FKs, the reg_embeddings/reg_messages rows, etc.).
-- >>> Skip / comment out this block if the target DB has data to keep. <<<
-- ============================================================
DROP FUNCTION IF EXISTS reg_match_embeddings(vector, uuid, uuid, uuid, int, float) CASCADE;
DROP FUNCTION IF EXISTS reg_match_embeddings_school(vector, uuid, int, float) CASCADE;
DROP FUNCTION IF EXISTS reg_get_document_chunks(uuid) CASCADE;
DROP FUNCTION IF EXISTS reg_delete_document_embeddings(uuid) CASCADE;

DROP TABLE IF EXISTS reg_assignment_submissions CASCADE;
DROP TABLE IF EXISTS reg_assignment_targets     CASCADE;
DROP TABLE IF EXISTS reg_assignments            CASCADE;
DROP TABLE IF EXISTS reg_embeddings CASCADE;
DROP TABLE IF EXISTS reg_messages   CASCADE;
DROP TABLE IF EXISTS reg_documents  CASCADE;
DROP TABLE IF EXISTS reg_users      CASCADE;
DROP TABLE IF EXISTS reg_classes    CASCADE;
DROP TABLE IF EXISTS reg_subjects   CASCADE;
DROP TABLE IF EXISTS reg_schools    CASCADE;

-- ---------- EXTENSIONS ----------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

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

-- 10 GCE (Cameroon) Advanced Level subjects.
INSERT INTO reg_subjects (name, slug, prompt_template, retrieval_k) VALUES
('Mathematics',         'math',                'You are a mathematics tutor. Focus on step-by-step calculations and clear explanations of formulas.', 5),
('Physics',             'physics',             'You are a physics tutor. Explain concepts with real-world examples and include relevant equations.', 5),
('Chemistry',           'chemistry',           'You are a chemistry tutor. Explain reactions, molecular structures, and chemical principles clearly.', 5),
('Biology',             'biology',             NULL, 5),
('Further Mathematics', 'further-mathematics', NULL, 5),
('Computer Science',    'computer-science',    NULL, 5),
('ICT',                 'ict',                 NULL, 5),
('Economics',           'economics',           NULL, 5),
('Geography',           'geography',           NULL, 5),
('History',             'history',             NULL, 5)
ON CONFLICT (slug) DO NOTHING;

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

-- An "A-Level" class for every subject.
INSERT INTO reg_classes (name, subject_id)
SELECT 'A-Level', s.id FROM reg_subjects s
ON CONFLICT (name, subject_id) DO NOTHING;

-- ---------- USERS ----------
CREATE TABLE IF NOT EXISTS reg_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    display_name VARCHAR(100),
    school_id UUID REFERENCES reg_schools(id),
    role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('school_admin', 'teacher', 'student')),
    enrolled_by UUID REFERENCES reg_users(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT TRUE,
    current_subject_id UUID REFERENCES reg_subjects(id),
    current_class_id UUID REFERENCES reg_classes(id),
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_users_phone       ON reg_users(phone_number);
CREATE INDEX IF NOT EXISTS idx_reg_users_school      ON reg_users(school_id);
CREATE INDEX IF NOT EXISTS idx_reg_users_role        ON reg_users(role);
CREATE INDEX IF NOT EXISTS idx_reg_users_enrolled_by ON reg_users(enrolled_by);

-- ---------- MESSAGES (conversation history) ----------
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

-- ---------- EMBEDDINGS (pgvector, 1536-dim) ----------
CREATE TABLE IF NOT EXISTS reg_embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID REFERENCES reg_documents(id) ON DELETE CASCADE,
    school_id UUID REFERENCES reg_schools(id),
    subject_id UUID REFERENCES reg_subjects(id) ON DELETE CASCADE,
    class_id UUID REFERENCES reg_classes(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding VECTOR(1536),  -- OpenAI text-embedding-3-large
    page_number INTEGER,
    chunk_index INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_class_scope ON reg_embeddings(subject_id, class_id);
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_school      ON reg_embeddings(school_id);
CREATE INDEX IF NOT EXISTS idx_reg_embeddings_vector
    ON reg_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ---------- ASSIGNMENTS (edu app) ----------
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

-- ---------- updated_at TRIGGER ----------
CREATE OR REPLACE FUNCTION reg_update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

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

-- ---------- SIMILARITY SEARCH RPC (school-scoped, 1536-dim) ----------
CREATE OR REPLACE FUNCTION reg_match_embeddings(
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
    FROM reg_embeddings e
    WHERE e.subject_id = match_subject_id
      AND e.class_id   = match_class_id
      AND e.school_id  = match_school_id
      AND 1 - (e.embedding <=> query_embedding) > match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ---------- SIMILARITY SEARCH RPC (school-wide, subject-agnostic) ----------
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

-- ---------- HELPER: get chunks for a document ----------
CREATE OR REPLACE FUNCTION reg_get_document_chunks(doc_id UUID)
RETURNS TABLE (chunk_id UUID, content TEXT, page_number INTEGER, chunk_index INTEGER)
LANGUAGE sql
AS $$
    SELECT id, content, page_number, chunk_index
    FROM reg_embeddings
    WHERE document_id = doc_id
    ORDER BY chunk_index;
$$;

-- ---------- HELPER: delete embeddings for a document ----------
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
-- STORAGE BUCKET: "reg_documents" (private) — for PDF backups
-- The app also auto-creates this on first upload (app/db/storage.py),
-- but creating it here is safe and idempotent.
-- ============================================================
INSERT INTO storage.buckets (id, name, public)
VALUES ('reg_documents', 'reg_documents', false)
ON CONFLICT (id) DO NOTHING;
