-- ============================================================
-- business_setup.sql — "Tara" shop-assistant section.
-- FULLY ISOLATED from the school app: every object is prefixed biz_.
-- Same Supabase project as reg_*, but nothing here touches reg_/edu.
-- Idempotent; safe to re-run.
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------- SHOPKEEPERS (the tenant — keyed by phone) ----------
CREATE TABLE IF NOT EXISTS biz_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number VARCHAR(30) UNIQUE NOT NULL,
    name       VARCHAR(120),
    shop_name  VARCHAR(160),
    language   VARCHAR(10) DEFAULT 'en',
    currency   VARCHAR(10) DEFAULT 'FCFA',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_users_phone ON biz_users(phone_number);

-- ---------- CUSTOMERS (a shopkeeper's debtors — the debt book) ----------
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

-- ---------- ITEMS (products — for margins, profit, restock) ----------
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

-- ---------- TRANSACTIONS (the ledger) ----------
CREATE TABLE IF NOT EXISTS biz_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES biz_users(id) ON DELETE CASCADE,
    type VARCHAR(20) NOT NULL CHECK (type IN ('sale','restock','credit','payment','expense','other')),
    item_name  VARCHAR(200),
    item_id    UUID REFERENCES biz_items(id) ON DELETE SET NULL,
    quantity   NUMERIC(14,3),
    unit       VARCHAR(30),
    amount        NUMERIC(14,2),            -- total value (FCFA)
    credit_amount NUMERIC(14,2) DEFAULT 0,  -- portion of a sale given on credit
    unit_price NUMERIC(14,2),
    cost_price NUMERIC(14,2),               -- per-unit cost (for profit)
    customer_id   UUID REFERENCES biz_customers(id) ON DELETE SET NULL,
    customer_name VARCHAR(160),
    direction  VARCHAR(4),                  -- 'in' | 'out'
    source     VARCHAR(10) DEFAULT 'text',  -- voice | text | photo
    raw_text   TEXT,
    occurred_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_tx_user_time ON biz_transactions(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_biz_tx_customer  ON biz_transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_biz_tx_type      ON biz_transactions(user_id, type);

-- ---------- MESSAGES (assistant conversation log) ----------
CREATE TABLE IF NOT EXISTS biz_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES biz_users(id) ON DELETE CASCADE,
    role    VARCHAR(12) NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_messages_user ON biz_messages(user_id, created_at);

-- ---------- updated_at trigger (self-contained) ----------
CREATE OR REPLACE FUNCTION biz_set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_biz_users_updated     ON biz_users;
DROP TRIGGER IF EXISTS trg_biz_customers_updated ON biz_customers;
DROP TRIGGER IF EXISTS trg_biz_items_updated     ON biz_items;
CREATE TRIGGER trg_biz_users_updated     BEFORE UPDATE ON biz_users     FOR EACH ROW EXECUTE FUNCTION biz_set_updated_at();
CREATE TRIGGER trg_biz_customers_updated BEFORE UPDATE ON biz_customers FOR EACH ROW EXECUTE FUNCTION biz_set_updated_at();
CREATE TRIGGER trg_biz_items_updated     BEFORE UPDATE ON biz_items     FOR EACH ROW EXECUTE FUNCTION biz_set_updated_at();

NOTIFY pgrst, 'reload schema';
