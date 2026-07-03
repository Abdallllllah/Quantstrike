-- ============================================================
-- CLEANUP: drop the CaratiTutor LMS tables (feature removed)
-- ============================================================
-- Target project: bdnvaahnvqrkqredddsv.supabase.co
--   (this is the gateway's main project — SUPABASE_URL in .env,
--    NOT the reg_* RAG project ptcganlvwzwoljulnuso.)
--
-- DESTRUCTIVE and IRREVERSIBLE. This permanently deletes all tutor/student
-- LMS data: tutors, students, quizzes, assignments, reports, chat history, etc.
-- Take a backup first (Supabase dashboard → Database → Backups, or
-- `pg_dump`) before running.
--
-- Run in the Supabase SQL Editor of the correct project. Wrapped in a
-- transaction so it's all-or-nothing. CASCADE clears the foreign keys
-- between these tables, so order does not matter.
-- ============================================================

BEGIN;

DROP TABLE IF EXISTS caratitutor_quiz_attempt_answers    CASCADE;
DROP TABLE IF EXISTS caratitutor_quiz_attempts           CASCADE;
DROP TABLE IF EXISTS caratitutor_quiz_assignments        CASCADE;
DROP TABLE IF EXISTS caratitutor_quiz_questions          CASCADE;
DROP TABLE IF EXISTS caratitutor_quizzes                 CASCADE;
DROP TABLE IF EXISTS caratitutor_assignment_submissions  CASCADE;
DROP TABLE IF EXISTS caratitutor_assignment_targets      CASCADE;
DROP TABLE IF EXISTS caratitutor_assignments             CASCADE;
DROP TABLE IF EXISTS caratitutor_challenges              CASCADE;
DROP TABLE IF EXISTS caratitutor_activity_events         CASCADE;
DROP TABLE IF EXISTS caratitutor_student_reports         CASCADE;
DROP TABLE IF EXISTS caratitutor_student_chat_messages   CASCADE;
DROP TABLE IF EXISTS caratitutor_materials               CASCADE;
DROP TABLE IF EXISTS caratitutor_notes                   CASCADE;
DROP TABLE IF EXISTS caratitutor_invitations             CASCADE;
DROP TABLE IF EXISTS caratitutor_students                CASCADE;
DROP TABLE IF EXISTS caratitutor_subjects                CASCADE;
DROP TABLE IF EXISTS caratitutor_tutors                  CASCADE;

-- Trigger helper used only by the tables above.
DROP FUNCTION IF EXISTS caratitutor_set_updated_at() CASCADE;

COMMIT;

-- ============================================================
-- REVIEW BEFORE RUNNING — NOT dropped automatically.
-- ============================================================
-- The following are *suspected* redundant but touch other flows or live in a
-- different project. Confirm each is truly unused before dropping.
--
-- 1) Old un-prefixed worker schema, superseded by the reg_* tables in
--    Quantstrike/migrations/000_full_setup.sql. Defined in
--    Quantstrike/app/db/migrations/001_initial.sql + 002_embeddings.sql.
--    Only drop if this DB no longer serves the pre-reg_ schema:
--       -- DROP TABLE IF EXISTS embeddings CASCADE;
--       -- DROP TABLE IF EXISTS documents  CASCADE;   -- NOTE: the GATEWAY still
--       --   uses a `documents` table (past papers) in the bdnvaahnvqrkqredddsv
--       --   project via app/gateway/routes/search.py + upload.py. Do NOT drop that one.
--       -- DROP TABLE IF EXISTS messages   CASCADE;   -- still used by controller.py tier gate
--       -- DROP TABLE IF EXISTS users      CASCADE;   -- still used by auth/practice/controller
--       -- DROP TABLE IF EXISTS classes    CASCADE;
--       -- DROP TABLE IF EXISTS subjects   CASCADE;
--
-- 2) Second RAG project (ptcganlvwzwoljulnuso.supabase.co): after the merge the
--    gateway no longer writes to its `schools` / `classes` / `documents` tables
--    (those routes were removed — the reg_* worker owns that data now). If
--    nothing else uses that project, its schools/classes/documents tables are
--    orphaned and can be dropped *in that project*. Verify no other service
--    reads them first.
--
-- practice_sessions is KEPT — routes/practice.py is still active.
-- ============================================================
