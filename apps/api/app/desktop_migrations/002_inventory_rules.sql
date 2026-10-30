-- Extend the original warehouse fields used by the existing Vue contracts.
ALTER TABLE card_group ADD COLUMN card_prefix TEXT;
ALTER TABLE card_group ADD COLUMN password_prefix TEXT;
ALTER TABLE card_group ADD COLUMN cost_price NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE card_group ADD COLUMN suggested_price NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE card_group ADD COLUMN alert_threshold INTEGER NOT NULL DEFAULT 10;

-- Active duplicates are rejected, including NULL versus empty passwords.
-- Never discard existing duplicates silently: a conflicting upgrade rolls back.
CREATE UNIQUE INDEX desktop_card_identity ON card_item(group_id, card_key, COALESCE(card_value, '')) WHERE deleted = 0;
CREATE INDEX desktop_card_stock ON card_item(group_id, deleted, status);
CREATE INDEX desktop_rule_group ON delivery_rule(card_group_id, deleted);
