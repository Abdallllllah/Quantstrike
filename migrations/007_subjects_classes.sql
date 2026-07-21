-- ============================================================
-- 007_subjects_classes.sql
-- Expand to the 10 GCE (Cameroon) Advanced Level subjects and give every
-- registered subject an "A-Level" class. Idempotent — safe to re-run.
-- Existing seed already has: Mathematics, Physics, Chemistry, Biology.
-- ============================================================
INSERT INTO reg_subjects (name, slug) VALUES
    ('Further Mathematics', 'further-mathematics'),
    ('Computer Science',    'computer-science'),
    ('ICT',                 'ict'),
    ('Economics',           'economics'),
    ('Geography',           'geography'),
    ('History',             'history')
ON CONFLICT (slug) DO NOTHING;

-- Create an "A-Level" class for EVERY subject that doesn't already have one.
INSERT INTO reg_classes (name, subject_id)
SELECT 'A-Level', s.id
FROM reg_subjects s
ON CONFLICT (name, subject_id) DO NOTHING;

NOTIFY pgrst, 'reload schema';
