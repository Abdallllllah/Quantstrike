-- ============================================================
-- business_add_message_id.sql — for EXISTING business databases.
-- Links each transaction to the message that created it, so a shopkeeper can
-- edit a message and have the record update. Idempotent.
-- (Fresh installs already get this via business_setup.sql.)
-- ============================================================
ALTER TABLE biz_transactions ADD COLUMN IF NOT EXISTS message_id UUID;
CREATE INDEX IF NOT EXISTS idx_biz_tx_message ON biz_transactions(message_id);

NOTIFY pgrst, 'reload schema';
