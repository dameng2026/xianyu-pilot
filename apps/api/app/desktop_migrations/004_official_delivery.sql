ALTER TABLE delivery_rule ADD COLUMN sku_id TEXT NOT NULL DEFAULT '';
ALTER TABLE delivery_rule ADD COLUMN config_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE delivery_rule ADD COLUMN plan_json TEXT;
ALTER TABLE delivery_rule ADD COLUMN trigger_json TEXT;
CREATE UNIQUE INDEX desktop_goods_sku_rule ON delivery_rule(goods_id,sku_id) WHERE goods_id IS NOT NULL AND deleted=0;
CREATE TABLE delivery_goods_config (
    id INTEGER PRIMARY KEY,
    goods_id INTEGER NOT NULL UNIQUE REFERENCES xianyu_goods(id),
    config_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    deleted INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE xianyu_trade_order ADD COLUMN official_status TEXT;
ALTER TABLE xianyu_trade_order ADD COLUMN official_checked_at REAL;
ALTER TABLE xianyu_trade_order ADD COLUMN official_seller_id TEXT;
ALTER TABLE xianyu_trade_order ADD COLUMN official_refunding INTEGER NOT NULL DEFAULT 0;
CREATE TABLE delivery_authorization (
    order_id INTEGER PRIMARY KEY REFERENCES xianyu_trade_order(id),
    rule_id INTEGER NOT NULL REFERENCES delivery_rule(id),
    rule_version INTEGER NOT NULL,
    verified_at REAL NOT NULL
);
CREATE TABLE delivery_keyword_grant (
    account_id INTEGER NOT NULL REFERENCES xianyu_account(id),
    external_order_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    rule_id INTEGER NOT NULL REFERENCES delivery_rule(id),
    rule_version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(account_id,message_id)
);
CREATE INDEX desktop_keyword_order ON delivery_keyword_grant(account_id,external_order_id,rule_id,rule_version,created_at);
CREATE TABLE delivery_inbox (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES xianyu_account(id),
    message_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    UNIQUE(account_id,message_id)
);
CREATE TABLE delivery_monitor_state (
    account_id INTEGER PRIMARY KEY REFERENCES xianyu_account(id),
    state TEXT NOT NULL,
    reason TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE delivery_daily_reservation (
    delivery_id INTEGER PRIMARY KEY REFERENCES delivery_record(id),
    rule_id INTEGER NOT NULL REFERENCES delivery_rule(id),
    day TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity>0)
);
CREATE INDEX desktop_daily_rule ON delivery_daily_reservation(rule_id,day);
