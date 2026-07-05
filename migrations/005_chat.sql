-- ============================================================
-- 005_chat.sql — group reg_messages into conversations for the chat UI.
-- Idempotent; safe to run on the existing reg_ database.
-- ============================================================
ALTER TABLE reg_messages ADD COLUMN IF NOT EXISTS conversation_id UUID;

CREATE INDEX IF NOT EXISTS idx_reg_messages_conversation
    ON reg_messages(user_id, conversation_id, created_at);

NOTIFY pgrst, 'reload schema';
