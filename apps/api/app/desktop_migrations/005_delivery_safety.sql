-- Immutable ownership and immutable delivery snapshots. Never unlock on timeout.
ALTER TABLE delivery_record ADD COLUMN platform_item_id TEXT;
ALTER TABLE delivery_record ADD COLUMN confirmation_manual_budget INTEGER NOT NULL DEFAULT 0;
UPDATE delivery_record SET platform_item_id=(SELECT item_id FROM xianyu_trade_order WHERE id=delivery_record.order_id) WHERE desktop_managed=1;
UPDATE card_item SET used_order_id=(SELECT order_id FROM delivery_card_reservation WHERE card_id=card_item.id)
    WHERE EXISTS(SELECT 1 FROM delivery_card_reservation WHERE card_id=card_item.id);

CREATE TABLE delivery_operator_action (
    delivery_id INTEGER NOT NULL REFERENCES delivery_record(id),
    request_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action='retry_platform_confirmation'),
    created_time TEXT NOT NULL,
    PRIMARY KEY(delivery_id,request_key)
);

CREATE TRIGGER desktop_snapshot_readonly BEFORE UPDATE ON delivery_record
WHEN OLD.desktop_managed=1 AND (
    NEW.snapshot_json IS NOT OLD.snapshot_json OR NEW.identity_hash IS NOT OLD.identity_hash
    OR NEW.order_id IS NOT OLD.order_id OR NEW.account_id IS NOT OLD.account_id
    OR NEW.receipt_mode IS NOT OLD.receipt_mode OR NEW.desktop_managed IS NOT OLD.desktop_managed
    OR NEW.platform_item_id IS NOT OLD.platform_item_id
    OR NEW.confirmation_operation_id IS NOT OLD.confirmation_operation_id)
BEGIN SELECT RAISE(ABORT,'delivery snapshot is immutable'); END;

CREATE TRIGGER desktop_delivery_no_delete BEFORE DELETE ON delivery_record WHEN OLD.desktop_managed=1
BEGIN SELECT RAISE(ABORT,'delivery history must be retained'); END;

CREATE TRIGGER desktop_segment_payload_readonly BEFORE UPDATE ON delivery_segment
WHEN NEW.delivery_id IS NOT OLD.delivery_id OR NEW.position IS NOT OLD.position OR NEW.kind IS NOT OLD.kind
    OR NEW.payload_json IS NOT OLD.payload_json OR NEW.operation_id IS NOT OLD.operation_id OR NEW.card_id IS NOT OLD.card_id
BEGIN SELECT RAISE(ABORT,'delivery segment payload is immutable'); END;

CREATE TRIGGER desktop_segment_no_delete BEFORE DELETE ON delivery_segment
BEGIN SELECT RAISE(ABORT,'delivery segment history must be retained'); END;

CREATE TRIGGER desktop_segment_result_guard BEFORE UPDATE OF state ON delivery_segment
WHEN (OLD.state='confirmed' AND NEW.state!='confirmed') OR (OLD.state='unknown' AND NEW.state NOT IN ('unknown','confirmed'))
BEGIN SELECT RAISE(ABORT,'confirmed or unknown delivery cannot be replayed'); END;

CREATE TRIGGER desktop_confirmation_monotone BEFORE UPDATE OF confirmation_state ON delivery_record
WHEN OLD.desktop_managed=1 AND OLD.confirmation_state='confirmed' AND NEW.confirmation_state!='confirmed'
BEGIN SELECT RAISE(ABORT,'confirmed fulfillment cannot be replayed'); END;

CREATE TRIGGER desktop_segment_sealed BEFORE INSERT ON delivery_segment
WHEN (SELECT workflow_state FROM delivery_record WHERE id=NEW.delivery_id)!='stock_blocked'
BEGIN SELECT RAISE(ABORT,'cannot append to a sealed delivery'); END;

CREATE TRIGGER desktop_reservation_owner_readonly BEFORE UPDATE ON delivery_card_reservation
WHEN NEW.card_id IS NOT OLD.card_id OR NEW.order_id IS NOT OLD.order_id OR NEW.delivery_id IS NOT OLD.delivery_id
    OR (OLD.state='delivered' AND NEW.state!='delivered') OR (OLD.state='unknown' AND NEW.state='reserved')
BEGIN SELECT RAISE(ABORT,'card reservation ownership is permanent'); END;

CREATE TRIGGER desktop_reservation_no_delete BEFORE DELETE ON delivery_card_reservation
BEGIN SELECT RAISE(ABORT,'card reservation must be retained'); END;

CREATE TRIGGER desktop_claim_guard BEFORE UPDATE ON card_item
WHEN EXISTS(SELECT 1 FROM delivery_card_reservation r WHERE r.card_id=OLD.id) AND (
    NEW.status NOT IN (1,2) OR NEW.status IS NULL OR NEW.deleted IS NOT 0
    OR NEW.used_order_id IS NOT (SELECT order_id FROM delivery_card_reservation WHERE card_id=OLD.id)
    OR (OLD.claim_token IS NOT NULL AND NEW.claim_token IS NOT OLD.claim_token)
    OR NEW.group_id IS NOT OLD.group_id OR NEW.card_key IS NOT OLD.card_key OR NEW.card_value IS NOT OLD.card_value
    OR (OLD.is_used=1 AND NEW.is_used IS NOT 1)
    OR (OLD.used_time IS NOT NULL AND NEW.used_time IS NULL)
    OR (NEW.used_by_order_id IS NOT NULL AND NEW.used_by_order_id IS NOT NEW.used_order_id))
BEGIN SELECT RAISE(ABORT,'reserved card cannot be released or reassigned'); END;

CREATE TRIGGER desktop_claim_no_delete BEFORE DELETE ON card_item
WHEN EXISTS(SELECT 1 FROM delivery_card_reservation r WHERE r.card_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'reserved card cannot be deleted'); END;
