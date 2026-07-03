-- Run once on Supabase. Adds the per-user tier column used by the
-- daily-message-limit gate in /api/chat.
--   tier 1 = 25 messages / day
--   tier 2 = 60 messages / day
--   tier 3 = 100 messages / day
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS tier smallint NOT NULL DEFAULT 1
    CHECK (tier IN (1, 2, 3));

-- Speeds up the "messages sent today by this student" count.
CREATE INDEX IF NOT EXISTS messages_userid_role_timestamp_idx
    ON messages (userid, role, "timestamp" DESC);

-- Conversation tracking. The `conversation_id` column already exists in
-- `messages` (insertMessage.py writes it), but defending in case it doesn't.
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS conversation_id uuid;

-- Speeds up "load all messages for these conversations" when reconstructing
-- a user's chat history on a new device.
CREATE INDEX IF NOT EXISTS messages_conversation_timestamp_idx
    ON messages (conversation_id, "timestamp" ASC);

-- ============================================================================
--  Practice mode (Bloom-taxonomy exam prep). Lives on the controller side —
--  the RAG service is only used as a question generator, no schema there.
-- ============================================================================
CREATE TABLE IF NOT EXISTS practice_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    userid          uuid NOT NULL,                       -- references users.id
    subject         text NOT NULL,
    topics          jsonb NOT NULL DEFAULT '[]'::jsonb,  -- array of topic strings
    class_level     text NOT NULL DEFAULT 'a-level',
    difficulty      text NOT NULL DEFAULT 'mixed'
                    CHECK (difficulty IN ('easy','medium','hard','mixed')),
    questions       jsonb NOT NULL DEFAULT '[]'::jsonb,  -- full question set incl. correct answers
    answers         jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {question_id: {answer, is_correct, score, feedback}}
    score           numeric(5,2),                        -- 0-100, final % when completed
    status          text NOT NULL DEFAULT 'in_progress'
                    CHECK (status IN ('in_progress','completed','abandoned')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz
);

CREATE INDEX IF NOT EXISTS practice_sessions_user_started_idx
    ON practice_sessions (userid, started_at DESC);
CREATE INDEX IF NOT EXISTS practice_sessions_user_status_idx
    ON practice_sessions (userid, status);
