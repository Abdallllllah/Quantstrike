-- ============================================================
-- 009_carati_v1.sql — Carati v1 (Requirements v0.2, phases 1–2).
--
--   * one student identity across app + WhatsApp (reg_users, phone-keyed)
--   * phone + OTP login (OTP optional until an SMS provider is configured)
--   * syllabus topics, answer tagging, "still unsure", ratings, answer cache
--   * daily allowances (15 questions / 2 papers, reset 00:00 WAT) + top-up wallet
--   * past paper bank (papers, questions, unlocks, marked attempts)
--   * student profile (per-topic stats)
--   * teacher tracks (profiles, tracks, steps, items, enrolments, posts, payouts)
--   * payments ledger (provider-agnostic; MoMo / Orange Money via an aggregator)
--
-- Follows caratidocs/DATABASE.md: idempotent, vector in `extensions`, RLS on
-- every new table, anon/authenticated revoked, service_role granted.
-- Does NOT drop anything. The old school-assignment tables are left in place.
-- Run as `postgres` in the Studio SQL editor (or psql). Safe to re-run.
-- ============================================================

-- ------------------------------------------------------------
-- 0. Helpers
-- ------------------------------------------------------------
-- Canonical phone form: +<digits>. A bare 9-digit Cameroon number (6xxxxxxxx /
-- 2xxxxxxxx) gets +237. Mirrored in app/edu/phone.py — keep them in sync.
CREATE OR REPLACE FUNCTION public.reg_normalize_phone(p text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN d = '' THEN NULL
        WHEN length(d) = 9 AND left(d, 1) IN ('6', '2') THEN '+237' || d
        WHEN left(d, 2) = '00' THEN '+' || substr(d, 3)
        ELSE '+' || d
    END
    FROM (SELECT regexp_replace(coalesce(p, ''), '\D', '', 'g') AS d) s;
$$;

-- Today's date in West Africa Time (UTC+1, no DST). Allowances reset at 00:00 WAT.
CREATE OR REPLACE FUNCTION public.reg_wat_today() RETURNS date
LANGUAGE sql STABLE AS $$
    SELECT (now() AT TIME ZONE 'Africa/Lagos')::date;
$$;

-- The shared corpus school. Every student sits here; retrieval is scoped to it.
INSERT INTO reg_schools (name, slug) VALUES ('General', 'general')
ON CONFLICT (slug) DO NOTHING;


-- ------------------------------------------------------------
-- 1. Users: one identity, onboarding fields, roles
-- ------------------------------------------------------------
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS level          text;
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS subjects       text[] NOT NULL DEFAULT '{}';
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS school_name    text;
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS region         text;
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS exam_year      integer;
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS onboarded_at   timestamptz;
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS last_seen_at   timestamptz;
-- users.id of the same person in the old WhatsApp gateway table (for history).
ALTER TABLE reg_users ADD COLUMN IF NOT EXISTS legacy_user_id uuid;

ALTER TABLE reg_users DROP CONSTRAINT IF EXISTS reg_users_level_check;
ALTER TABLE reg_users ADD CONSTRAINT reg_users_level_check
    CHECK (level IS NULL OR level IN ('lower_sixth', 'upper_sixth'));

-- 'admin' = Carati team. 'school_admin' is kept only so existing rows stay
-- valid; no endpoint accepts it any more.
ALTER TABLE reg_users DROP CONSTRAINT IF EXISTS reg_users_role_check;
ALTER TABLE reg_users ADD CONSTRAINT reg_users_role_check
    CHECK (role IN ('student', 'teacher', 'admin', 'school_admin'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_reg_users_legacy ON reg_users(legacy_user_id)
    WHERE legacy_user_id IS NOT NULL;

-- Normalise existing phone numbers where that doesn't collide with another row.
UPDATE reg_users u
SET phone_number = reg_normalize_phone(u.phone_number)
WHERE reg_normalize_phone(u.phone_number) IS NOT NULL
  AND u.phone_number <> reg_normalize_phone(u.phone_number)
  AND NOT EXISTS (
      SELECT 1 FROM reg_users o
      WHERE o.id <> u.id AND o.phone_number = reg_normalize_phone(u.phone_number)
  );

-- Merge the WhatsApp gateway's users into reg_users (phone is the identity).
-- Names are NOT copied: students are identified by phone only.
-- 1) link people who already have an app account
UPDATE reg_users r
SET legacy_user_id = g.id
FROM (
    SELECT DISTINCT ON (reg_normalize_phone(phone_number)) id, reg_normalize_phone(phone_number) AS np
    FROM users WHERE reg_normalize_phone(phone_number) IS NOT NULL
    ORDER BY reg_normalize_phone(phone_number), created_at
) g
WHERE r.phone_number = g.np AND r.legacy_user_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM reg_users x WHERE x.legacy_user_id = g.id);

-- 2) create app accounts for WhatsApp-only students
INSERT INTO reg_users (phone_number, role, school_id, school_name, legacy_user_id, preferences, created_at)
SELECT g.np, 'student',
       (SELECT id FROM reg_schools WHERE slug = 'general'),
       nullif(g.school_name, ''),
       g.id,
       jsonb_build_object('source', 'whatsapp_gateway', 'gateway_tier', g.tier,
                          'curriculum', g.curriculum, 'grade_level', g.grade_level),
       coalesce(g.created_at, now())
FROM (
    SELECT DISTINCT ON (reg_normalize_phone(phone_number))
           id, reg_normalize_phone(phone_number) AS np, school_name, tier, curriculum, grade_level, created_at
    FROM users WHERE reg_normalize_phone(phone_number) IS NOT NULL
    ORDER BY reg_normalize_phone(phone_number), created_at
) g
WHERE length(g.np) <= 20
  AND NOT EXISTS (SELECT 1 FROM reg_users r WHERE r.phone_number = g.np)
  AND NOT EXISTS (SELECT 1 FROM reg_users r WHERE r.legacy_user_id = g.id);


-- ------------------------------------------------------------
-- 2. OTP codes
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reg_otp_codes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    phone       text NOT NULL,
    code_hash   text NOT NULL,
    expires_at  timestamptz NOT NULL,
    attempts    integer NOT NULL DEFAULT 0,
    consumed_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reg_otp_phone ON reg_otp_codes(phone, created_at DESC);


-- ------------------------------------------------------------
-- 3. Syllabus topics (subject -> topic -> sub-topic)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reg_syllabus_topics (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id uuid NOT NULL REFERENCES reg_subjects(id) ON DELETE CASCADE,
    parent_id  uuid REFERENCES reg_syllabus_topics(id) ON DELETE CASCADE,
    name       text NOT NULL,
    code       text,                         -- official syllabus reference code
    sort       integer NOT NULL DEFAULT 0,
    verified   boolean NOT NULL DEFAULT false, -- false = created by auto-tagging, awaiting admin review
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reg_topics_unique
    ON reg_syllabus_topics(subject_id, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name));
CREATE INDEX IF NOT EXISTS idx_reg_topics_subject ON reg_syllabus_topics(subject_id, parent_id);


-- ------------------------------------------------------------
-- 4. Chat messages: tagging, allowance, cache, rating, unsure
-- ------------------------------------------------------------
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS topic_id    uuid REFERENCES reg_syllabus_topics(id) ON DELETE SET NULL;
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS subtopic_id uuid REFERENCES reg_syllabus_topics(id) ON DELETE SET NULL;
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS counts_against_allowance boolean NOT NULL DEFAULT false;
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS allowance_source text;   -- free | season_pass | credit
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS cached       boolean NOT NULL DEFAULT false;
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS rating       smallint;  -- 1 up, -1 down
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS unsure       boolean NOT NULL DEFAULT false;  -- student said "still unsure" about this answer
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS is_reexplanation boolean NOT NULL DEFAULT false;
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS channel      text NOT NULL DEFAULT 'app';     -- app | whatsapp
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS client_ref   text;      -- idempotency key from the client
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS review_status text;     -- NULL | pending | corrected | dismissed
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS correction   text;

ALTER TABLE reg_messages DROP CONSTRAINT IF EXISTS reg_messages_rating_check;
ALTER TABLE reg_messages ADD CONSTRAINT reg_messages_rating_check CHECK (rating IS NULL OR rating IN (-1, 1));

CREATE UNIQUE INDEX IF NOT EXISTS idx_reg_messages_client_ref
    ON reg_messages(user_id, client_ref) WHERE client_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reg_messages_review
    ON reg_messages(review_status, created_at DESC) WHERE review_status IS NOT NULL;

CREATE TABLE IF NOT EXISTS reg_answer_cache (
    question_hash text PRIMARY KEY,           -- sha256 of the normalised question
    question      text NOT NULL,
    answer        text NOT NULL,
    subject_id    uuid REFERENCES reg_subjects(id) ON DELETE SET NULL,
    topic_id      uuid REFERENCES reg_syllabus_topics(id) ON DELETE SET NULL,
    hits          integer NOT NULL DEFAULT 0,
    disabled      boolean NOT NULL DEFAULT false,  -- set when an answer is rated down / corrected
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_hit_at   timestamptz
);


-- ------------------------------------------------------------
-- 5. Allowances, wallet, usage ledger
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reg_daily_usage (
    user_id        uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    day            date NOT NULL,
    questions_used integer NOT NULL DEFAULT 0,  -- free units only
    papers_used    integer NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS reg_wallets (
    user_id           uuid PRIMARY KEY REFERENCES reg_users(id) ON DELETE CASCADE,
    question_credits  integer NOT NULL DEFAULT 0 CHECK (question_credits >= 0),
    paper_credits     integer NOT NULL DEFAULT 0 CHECK (paper_credits >= 0),
    season_pass_until date,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- One row per unit consumed. (user, kind, day, ref) is unique, so retrying the
-- same request never charges twice.
CREATE TABLE IF NOT EXISTS reg_usage_events (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    kind       text NOT NULL CHECK (kind IN ('question', 'paper')),
    day        date NOT NULL,
    ref        text NOT NULL,
    source     text NOT NULL CHECK (source IN ('free', 'season_pass', 'credit')),
    subject_id uuid REFERENCES reg_subjects(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, kind, day, ref)
);
CREATE INDEX IF NOT EXISTS idx_reg_usage_events_day ON reg_usage_events(day, kind);


-- ------------------------------------------------------------
-- 6. Past paper bank
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reg_past_papers (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid UNIQUE REFERENCES reg_documents(id) ON DELETE SET NULL,
    subject_id    uuid REFERENCES reg_subjects(id) ON DELETE SET NULL,
    filename      text NOT NULL,
    title         text NOT NULL,
    years         integer[] NOT NULL DEFAULT '{}',
    paper_numbers integer[] NOT NULL DEFAULT '{}',
    kind          text NOT NULL DEFAULT 'past_paper' CHECK (kind IN ('past_paper', 'mock', 'marking_scheme')),
    is_active     boolean NOT NULL DEFAULT true,
    questions_extracted_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reg_past_papers_subject ON reg_past_papers(subject_id);

CREATE TABLE IF NOT EXISTS reg_past_questions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id       uuid NOT NULL REFERENCES reg_past_papers(id) ON DELETE CASCADE,
    sort           integer NOT NULL,
    number         text NOT NULL,              -- as printed: "1", "2(a)", "MCQ 14"
    text           text NOT NULL,
    marks          numeric(6,2),
    marking_scheme text,
    topic_id       uuid REFERENCES reg_syllabus_topics(id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (paper_id, sort)
);

CREATE TABLE IF NOT EXISTS reg_paper_unlocks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    paper_id     uuid NOT NULL REFERENCES reg_past_papers(id) ON DELETE CASCADE,
    day          date NOT NULL,
    source       text NOT NULL,              -- free | season_pass | credit | reopen | completed | track
    completed_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, paper_id, day)
);
CREATE INDEX IF NOT EXISTS idx_reg_paper_unlocks_user ON reg_paper_unlocks(user_id, paper_id);


-- ------------------------------------------------------------
-- 7. Profile: per-student, per-topic stats
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reg_topic_stats (
    user_id       uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    topic_id      uuid NOT NULL REFERENCES reg_syllabus_topics(id) ON DELETE CASCADE,
    subject_id    uuid NOT NULL REFERENCES reg_subjects(id) ON DELETE CASCADE,
    asked         integer NOT NULL DEFAULT 0,
    attempted     integer NOT NULL DEFAULT 0,
    marks_awarded numeric(10,2) NOT NULL DEFAULT 0,
    marks_total   numeric(10,2) NOT NULL DEFAULT 0,
    unsure_count  integer NOT NULL DEFAULT 0,
    last_activity timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_topic_stats_subject ON reg_topic_stats(user_id, subject_id);


-- ------------------------------------------------------------
-- 8. Teachers, tracks, enrolments
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reg_teacher_profiles (
    user_id               uuid PRIMARY KEY REFERENCES reg_users(id) ON DELETE CASCADE,
    display_name          text NOT NULL,       -- teachers ARE named (public catalogue)
    school                text,
    years_experience      integer,
    bio                   text,
    results_claim         text,
    results_evidence_url  text,
    results_verified      boolean NOT NULL DEFAULT false,
    id_document_url       text,
    credential_url        text,
    id_verified           boolean NOT NULL DEFAULT false,
    credential_verified   boolean NOT NULL DEFAULT false,
    school_confirmed      boolean NOT NULL DEFAULT false,
    verification_status   text NOT NULL DEFAULT 'pending'
                          CHECK (verification_status IN ('pending', 'verified', 'rejected')),
    revenue_share         numeric(4,3) NOT NULL DEFAULT 0.700 CHECK (revenue_share BETWEEN 0.650 AND 0.750),
    agreement_accepted_at timestamptz,
    payout_provider       text,                -- mtn_momo | orange_money
    payout_wallet         text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reg_tracks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id  uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    subject_id  uuid NOT NULL REFERENCES reg_subjects(id),
    exam_year   integer NOT NULL,
    title       text NOT NULL,
    description text,
    variant     text NOT NULL DEFAULT 'season' CHECK (variant IN ('season', 'sprint')),
    price_fcfa  integer NOT NULL CHECK (price_fcfa >= 0),
    start_date  date NOT NULL,
    exam_date   date NOT NULL,
    status      text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    cloned_from uuid REFERENCES reg_tracks(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reg_tracks_catalogue ON reg_tracks(status, subject_id, exam_year);
CREATE INDEX IF NOT EXISTS idx_reg_tracks_teacher   ON reg_tracks(teacher_id);

CREATE TABLE IF NOT EXISTS reg_track_steps (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id    uuid NOT NULL REFERENCES reg_tracks(id) ON DELETE CASCADE,
    week_number integer NOT NULL CHECK (week_number >= 1),
    title       text NOT NULL,
    topic       text,
    topic_id    uuid REFERENCES reg_syllabus_topics(id) ON DELETE SET NULL,
    notes       text,
    checkpoint  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (track_id, week_number)
);

CREATE TABLE IF NOT EXISTS reg_track_items (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    step_id          uuid NOT NULL REFERENCES reg_track_steps(id) ON DELETE CASCADE,
    kind             text NOT NULL CHECK (kind IN ('note', 'document', 'audio', 'worked_question', 'question')),
    title            text,
    body             text,
    storage_path     text,     -- file in the private `track_content` bucket (document/audio)
    past_question_id uuid REFERENCES reg_past_questions(id) ON DELETE SET NULL,
    sort             integer NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reg_track_items_step ON reg_track_items(step_id, sort);

CREATE TABLE IF NOT EXISTS reg_enrolments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id     uuid NOT NULL REFERENCES reg_tracks(id) ON DELETE CASCADE,
    student_id   uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    parent_phone text NOT NULL,
    payment_id   uuid,
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'refunded', 'cancelled')),
    start_date   date,
    created_at   timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    UNIQUE (track_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_enrolments_student ON reg_enrolments(student_id, status);

CREATE TABLE IF NOT EXISTS reg_step_progress (
    enrolment_id uuid NOT NULL REFERENCES reg_enrolments(id) ON DELETE CASCADE,
    step_id      uuid NOT NULL REFERENCES reg_track_steps(id) ON DELETE CASCADE,
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (enrolment_id, step_id)
);

-- Weekly async group Q&A + the teacher's one clarification per week.
CREATE TABLE IF NOT EXISTS reg_track_posts (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id   uuid NOT NULL REFERENCES reg_tracks(id) ON DELETE CASCADE,
    author_id  uuid REFERENCES reg_users(id) ON DELETE CASCADE,  -- NULL = answered by Carati (LLM)
    kind       text NOT NULL CHECK (kind IN ('question', 'answer', 'clarification', 'announcement')),
    body       text NOT NULL,
    reply_to   uuid REFERENCES reg_track_posts(id) ON DELETE CASCADE,
    week_start date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reg_track_posts ON reg_track_posts(track_id, week_start, created_at);

CREATE TABLE IF NOT EXISTS reg_attempts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    question_id   uuid NOT NULL REFERENCES reg_past_questions(id) ON DELETE CASCADE,
    track_id      uuid REFERENCES reg_tracks(id) ON DELETE SET NULL,
    submission    text,
    had_image     boolean NOT NULL DEFAULT false,
    marks_awarded numeric(6,2),
    marks_total   numeric(6,2),
    feedback      text,
    breakdown     jsonb NOT NULL DEFAULT '[]'::jsonb,
    marked_by     text NOT NULL DEFAULT 'llm' CHECK (marked_by IN ('llm', 'teacher', 'pending_teacher')),
    teacher_id    uuid REFERENCES reg_users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    marked_at     timestamptz
);
CREATE INDEX IF NOT EXISTS idx_reg_attempts_user     ON reg_attempts(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reg_attempts_question ON reg_attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_reg_attempts_teacher  ON reg_attempts(track_id, marked_by);


-- ------------------------------------------------------------
-- 9. Payments and payouts (all amounts FCFA)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reg_payments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,  -- the student who benefits
    payer_phone  text NOT NULL,                                             -- the parent who pays
    type         text NOT NULL CHECK (type IN ('track', 'question_pack', 'paper_pack', 'season_pass')),
    product_code text NOT NULL,
    quantity     integer NOT NULL DEFAULT 0,
    amount_fcfa  integer NOT NULL CHECK (amount_fcfa >= 0),
    track_id     uuid REFERENCES reg_tracks(id) ON DELETE SET NULL,
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed', 'refunded')),
    provider     text NOT NULL DEFAULT 'manual',
    provider_ref text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    paid_at      timestamptz,
    metadata     jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reg_payments_provider_ref
    ON reg_payments(provider, provider_ref) WHERE provider_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reg_payments_user ON reg_payments(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reg_payouts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id   uuid NOT NULL REFERENCES reg_users(id) ON DELETE CASCADE,
    amount_fcfa  integer NOT NULL CHECK (amount_fcfa >= 0),
    period_start date NOT NULL,
    period_end   date NOT NULL,
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed')),
    provider_ref text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    paid_at      timestamptz
);
CREATE INDEX IF NOT EXISTS idx_reg_payouts_teacher ON reg_payouts(teacher_id, period_end DESC);


-- ------------------------------------------------------------
-- 10. Allowance functions (server-side, atomic, idempotent)
-- ------------------------------------------------------------
-- Raised cap applies when the student has an ACTIVE enrolment in a track for
-- this subject whose exam has not passed.
CREATE OR REPLACE FUNCTION public.reg_has_raised_cap(p_user uuid, p_subject uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT p_subject IS NOT NULL AND EXISTS (
        SELECT 1 FROM reg_enrolments e JOIN reg_tracks t ON t.id = e.track_id
        WHERE e.student_id = p_user AND e.status = 'active'
          AND t.subject_id = p_subject AND t.exam_date >= reg_wat_today()
    );
$$;

-- Consume one unit. Order: free daily unit -> season pass -> paid credit.
-- Returns allowed=false (and consumes nothing) when everything is used up.
-- The same (user, kind, ref) on the same WAT day is never charged twice.
CREATE OR REPLACE FUNCTION public.reg_consume_allowance(
    p_user uuid, p_kind text, p_ref text, p_subject uuid,
    p_free_cap int, p_raised_cap int
) RETURNS TABLE (allowed boolean, source text, used integer, cap integer, already boolean)
LANGUAGE plpgsql AS $$
DECLARE
    v_day date := reg_wat_today();
    v_cap int;
    v_used int;
    v_src text;
    v_pass date;
    v_credits int;
BEGIN
    IF p_kind NOT IN ('question', 'paper') THEN
        RAISE EXCEPTION 'bad kind %', p_kind;
    END IF;
    -- Serialise per user+kind so concurrent requests can't both take the last unit.
    PERFORM pg_advisory_xact_lock(hashtext(p_user::text || ':' || p_kind));

    v_cap := CASE WHEN reg_has_raised_cap(p_user, p_subject) THEN p_raised_cap ELSE p_free_cap END;

    INSERT INTO reg_daily_usage (user_id, day) VALUES (p_user, v_day) ON CONFLICT DO NOTHING;
    SELECT CASE WHEN p_kind = 'question' THEN d.questions_used ELSE d.papers_used END
      INTO v_used FROM reg_daily_usage d WHERE d.user_id = p_user AND d.day = v_day;

    SELECT ev.source INTO v_src FROM reg_usage_events ev
     WHERE ev.user_id = p_user AND ev.kind = p_kind AND ev.day = v_day AND ev.ref = p_ref;
    IF FOUND THEN
        RETURN QUERY SELECT true, v_src, v_used, v_cap, true;
        RETURN;
    END IF;

    IF v_used < v_cap THEN
        v_src := 'free';
    ELSE
        INSERT INTO reg_wallets (user_id) VALUES (p_user) ON CONFLICT DO NOTHING;
        SELECT w.season_pass_until,
               CASE WHEN p_kind = 'question' THEN w.question_credits ELSE w.paper_credits END
          INTO v_pass, v_credits
          FROM reg_wallets w WHERE w.user_id = p_user FOR UPDATE;
        IF v_pass IS NOT NULL AND v_pass >= v_day THEN
            v_src := 'season_pass';
        ELSIF v_credits > 0 THEN
            v_src := 'credit';
            IF p_kind = 'question' THEN
                UPDATE reg_wallets SET question_credits = question_credits - 1, updated_at = now() WHERE user_id = p_user;
            ELSE
                UPDATE reg_wallets SET paper_credits = paper_credits - 1, updated_at = now() WHERE user_id = p_user;
            END IF;
        ELSE
            RETURN QUERY SELECT false, NULL::text, v_used, v_cap, false;
            RETURN;
        END IF;
    END IF;

    INSERT INTO reg_usage_events (user_id, kind, day, ref, source, subject_id)
    VALUES (p_user, p_kind, v_day, p_ref, v_src, p_subject);

    IF v_src = 'free' THEN
        IF p_kind = 'question' THEN
            UPDATE reg_daily_usage SET questions_used = questions_used + 1 WHERE user_id = p_user AND day = v_day;
        ELSE
            UPDATE reg_daily_usage SET papers_used = papers_used + 1 WHERE user_id = p_user AND day = v_day;
        END IF;
        v_used := v_used + 1;
    END IF;

    RETURN QUERY SELECT true, v_src, v_used, v_cap, false;
END;
$$;

-- Add credits / season pass after a confirmed payment (idempotent per payment).
CREATE OR REPLACE FUNCTION public.reg_grant_payment(p_payment uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    p reg_payments%ROWTYPE;
BEGIN
    SELECT * INTO p FROM reg_payments WHERE id = p_payment FOR UPDATE;
    IF NOT FOUND THEN RETURN 'not_found'; END IF;
    IF p.status = 'paid' THEN RETURN 'already_paid'; END IF;

    UPDATE reg_payments SET status = 'paid', paid_at = now() WHERE id = p_payment;
    INSERT INTO reg_wallets (user_id) VALUES (p.user_id) ON CONFLICT DO NOTHING;

    IF p.type = 'question_pack' THEN
        UPDATE reg_wallets SET question_credits = question_credits + p.quantity, updated_at = now() WHERE user_id = p.user_id;
    ELSIF p.type = 'paper_pack' THEN
        UPDATE reg_wallets SET paper_credits = paper_credits + p.quantity, updated_at = now() WHERE user_id = p.user_id;
    ELSIF p.type = 'season_pass' THEN
        UPDATE reg_wallets
           SET season_pass_until = greatest(coalesce(season_pass_until, reg_wat_today()),
                                            (p.metadata->>'until')::date),
               updated_at = now()
         WHERE user_id = p.user_id;
    ELSIF p.type = 'track' THEN
        UPDATE reg_enrolments
           SET status = 'active', activated_at = now(), start_date = coalesce(start_date, reg_wat_today())
         WHERE id = (p.metadata->>'enrolment_id')::uuid;
    END IF;
    RETURN 'granted';
END;
$$;

-- Atomic per-topic counters for the student profile.
CREATE OR REPLACE FUNCTION public.reg_bump_topic_stat(
    p_user uuid, p_topic uuid, p_subject uuid,
    p_asked int DEFAULT 0, p_attempted int DEFAULT 0,
    p_awarded numeric DEFAULT 0, p_total numeric DEFAULT 0, p_unsure int DEFAULT 0
) RETURNS void
LANGUAGE sql AS $$
    INSERT INTO reg_topic_stats AS s
        (user_id, topic_id, subject_id, asked, attempted, marks_awarded, marks_total, unsure_count, last_activity)
    VALUES (p_user, p_topic, p_subject, p_asked, p_attempted, p_awarded, p_total, p_unsure, now())
    ON CONFLICT (user_id, topic_id) DO UPDATE SET
        asked         = s.asked + excluded.asked,
        attempted     = s.attempted + excluded.attempted,
        marks_awarded = s.marks_awarded + excluded.marks_awarded,
        marks_total   = s.marks_total + excluded.marks_total,
        unsure_count  = s.unsure_count + excluded.unsure_count,
        last_activity = now();
$$;


-- ------------------------------------------------------------
-- 11. Storage bucket for track content (private; served in-app only)
-- ------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public)
VALUES ('track_content', 'track_content', false)
ON CONFLICT (id) DO NOTHING;


-- ------------------------------------------------------------
-- 12. Security: RLS on, anon/authenticated out, service_role in
-- ------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'reg_otp_codes', 'reg_syllabus_topics', 'reg_answer_cache', 'reg_daily_usage',
        'reg_wallets', 'reg_usage_events', 'reg_past_papers', 'reg_past_questions',
        'reg_paper_unlocks', 'reg_topic_stats', 'reg_teacher_profiles', 'reg_tracks',
        'reg_track_steps', 'reg_track_items', 'reg_enrolments', 'reg_step_progress',
        'reg_track_posts', 'reg_attempts', 'reg_payments', 'reg_payouts'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', t);
        EXECUTE format('GRANT ALL ON public.%I TO service_role', t);
    END LOOP;
END $$;

REVOKE EXECUTE ON FUNCTION public.reg_normalize_phone(text) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.reg_wat_today() FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.reg_has_raised_cap(uuid, uuid) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.reg_consume_allowance(uuid, text, text, uuid, int, int) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.reg_grant_payment(uuid) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.reg_bump_topic_stat(uuid, uuid, uuid, int, int, numeric, numeric, int) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.reg_normalize_phone(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.reg_consume_allowance(uuid, text, text, uuid, int, int) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.reg_grant_payment(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.reg_bump_topic_stat(uuid, uuid, uuid, int, int, numeric, numeric, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reg_normalize_phone(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.reg_wat_today() TO service_role;
GRANT EXECUTE ON FUNCTION public.reg_has_raised_cap(uuid, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.reg_consume_allowance(uuid, text, text, uuid, int, int) TO service_role;
GRANT EXECUTE ON FUNCTION public.reg_grant_payment(uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.reg_bump_topic_stat(uuid, uuid, uuid, int, int, numeric, numeric, int) TO service_role;

NOTIFY pgrst, 'reload schema';
