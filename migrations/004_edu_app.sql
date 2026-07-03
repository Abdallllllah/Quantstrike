-- ============================================================
-- 004_edu_app.sql — school / teacher / student layer on top of reg_*
-- ------------------------------------------------------------
-- Adds roles + enrollment to reg_users and the assignments feature.
-- Auth is name + phone (no passwords). Student RAG isolation is already
-- enforced by the existing reg_embeddings.school_id scoping — no change here.
-- Safe to run on the existing reg_ database (idempotent).
-- ============================================================

-- ---------- USERS: roles + enrollment ----------
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'student';
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS enrolled_by UUID REFERENCES reg_users(id) ON DELETE SET NULL;
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

-- Constrain the role to the three product roles. Drop-then-add so re-runs work.
ALTER TABLE reg_users DROP CONSTRAINT IF EXISTS reg_users_role_check;
ALTER TABLE reg_users ADD CONSTRAINT reg_users_role_check
    CHECK (role IN ('school_admin', 'teacher', 'student'));

CREATE INDEX IF NOT EXISTS idx_reg_users_role         ON reg_users(role);
CREATE INDEX IF NOT EXISTS idx_reg_users_enrolled_by  ON reg_users(enrolled_by);
CREATE INDEX IF NOT EXISTS idx_reg_users_school_role  ON reg_users(school_id, role);

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

-- Which students an assignment is given to.
CREATE TABLE IF NOT EXISTS reg_assignment_targets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id UUID REFERENCES reg_assignments(id) ON DELETE CASCADE,
    student_id    UUID REFERENCES reg_users(id)       ON DELETE CASCADE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (assignment_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_assignment_targets_student ON reg_assignment_targets(student_id);

-- Student submissions + teacher grading.
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

-- ---------- updated_at triggers ----------
DROP TRIGGER IF EXISTS update_reg_assignments_updated_at ON reg_assignments;
CREATE TRIGGER update_reg_assignments_updated_at BEFORE UPDATE ON reg_assignments
    FOR EACH ROW EXECUTE FUNCTION reg_update_updated_at_column();
