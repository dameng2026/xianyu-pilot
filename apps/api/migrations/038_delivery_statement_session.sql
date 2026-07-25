-- Migration 038: delivery_statement_session table
-- Tracks per-order statement sessions for the "confirm before delivery" flow.
-- When the statement switch is enabled, a session row is created on payment;
-- the actual delivery is deferred until the buyer replies "confirm".
-- Uses CREATE TABLE IF NOT EXISTS for idempotent re-runs (non-destructive).

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `delivery_statement_session` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` INT UNSIGNED NOT NULL COMMENT 'xianyu account id',
  `order_id` VARCHAR(128) NOT NULL COMMENT 'external order id',
  `buyer_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'buyer external uid',
  `buyer_nick` VARCHAR(255) NOT NULL DEFAULT '' COMMENT ' buyer display name',
  `xy_goods_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'goods external id',
  `goods_title` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'goods title snapshot',
  `s_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'xianyu conversation sId',
  `pnm_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'payment message pnmId',
  `statement_content` TEXT NULL COMMENT 'rendered statement text sent to buyer',
  `status` ENUM('declaring','waiting','confirmed','cancelled') NOT NULL DEFAULT 'waiting' COMMENT 'session lifecycle state',
  `confirm_source` VARCHAR(32) NULL COMMENT 'who confirmed: buyer / seller / system',
  `cancel_source` VARCHAR(32) NULL COMMENT 'who cancelled: buyer / seller / system',
  `reply_msg_id` VARCHAR(128) NULL COMMENT 'buyer reply message id',
  `sent_at` DATETIME NULL COMMENT 'when the statement was sent to buyer',
  `confirmed_at` DATETIME NULL COMMENT 'when the buyer confirmed',
  `cancelled_at` DATETIME NULL COMMENT 'when the session was cancelled',
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted` TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `idx_account_order` (`account_id`, `order_id`, `status`, `deleted`),
  KEY `idx_account_sid` (`account_id`, `s_id`, `status`, `deleted`),
  KEY `idx_status` (`status`, `deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='发货声明会话（买家确认后才触发发货）';
