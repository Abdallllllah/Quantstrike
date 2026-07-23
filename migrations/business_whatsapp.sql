-- ============================================================
-- business_whatsapp.sql — WhatsApp channel for the Tara section.
-- One table: a claim log of WhatsApp message ids so Meta's webhook retries
-- can never record the same sale twice. Idempotent.
-- ============================================================
CREATE TABLE IF NOT EXISTS biz_wa_events (
    wamid      TEXT PRIMARY KEY,          -- WhatsApp message id (wamid.…)
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_wa_events_time ON biz_wa_events(created_at);

NOTIFY pgrst, 'reload schema';
