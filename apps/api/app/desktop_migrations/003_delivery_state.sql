-- Extend original business tables; never create a second order or stock store.
ALTER TABLE xianyu_trade_order ADD COLUMN verification_source TEXT NOT NULL DEFAULT 'unverified';
ALTER TABLE xianyu_trade_order ADD COLUMN payment_evidence TEXT;
CREATE UNIQUE INDEX desktop_order_identity ON xianyu_trade_order(account_id, external_order_id) WHERE external_order_id IS NOT NULL;

ALTER TABLE delivery_record ADD COLUMN desktop_managed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE delivery_record ADD COLUMN workflow_state TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE delivery_record ADD COLUMN receipt_mode TEXT;
ALTER TABLE delivery_record ADD COLUMN identity_hash TEXT;
ALTER TABLE delivery_record ADD COLUMN snapshot_json TEXT;
ALTER TABLE delivery_record ADD COLUMN confirmation_state TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE delivery_record ADD COLUMN confirmation_operation_id TEXT;
ALTER TABLE delivery_record ADD COLUMN confirmation_retry_safe INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX desktop_first_delivery ON delivery_record(order_id) WHERE desktop_managed=1;

CREATE TABLE delivery_segment (
    id INTEGER PRIMARY KEY,
    delivery_id INTEGER NOT NULL REFERENCES delivery_record(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('text','card','image','link')),
    payload_json TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','confirmed','failed','unknown')),
    retry_safe INTEGER NOT NULL DEFAULT 0 CHECK(retry_safe IN (0,1)),
    card_id INTEGER REFERENCES card_item(id),
    updated_time TEXT NOT NULL,
    UNIQUE(delivery_id, position)
);
CREATE TABLE delivery_card_reservation (
    card_id INTEGER PRIMARY KEY REFERENCES card_item(id),
    delivery_id INTEGER NOT NULL REFERENCES delivery_record(id),
    order_id INTEGER NOT NULL REFERENCES xianyu_trade_order(id),
    state TEXT NOT NULL CHECK(state IN ('reserved','delivered','unknown')),
    created_time TEXT NOT NULL,
    updated_time TEXT NOT NULL
);
CREATE TABLE delivery_step_attempt (
    id INTEGER PRIMARY KEY,
    delivery_id INTEGER NOT NULL REFERENCES delivery_record(id),
    segment_id INTEGER REFERENCES delivery_segment(id),
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('sending','confirmed','not_sent','unknown')),
    receipt_ref TEXT,
    created_time TEXT NOT NULL,
    updated_time TEXT NOT NULL
);
CREATE UNIQUE INDEX desktop_inflight_operation ON delivery_step_attempt(operation_id) WHERE state='sending';
CREATE INDEX desktop_segment_queue ON delivery_segment(delivery_id,position,state);
CREATE TABLE delivery_audit (
    id INTEGER PRIMARY KEY,
    delivery_id INTEGER NOT NULL REFERENCES delivery_record(id),
    operation_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    evidence TEXT,
    created_time TEXT NOT NULL
);
