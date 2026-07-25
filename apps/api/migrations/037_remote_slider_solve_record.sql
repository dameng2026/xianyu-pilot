-- Migration 037: remote slider solve record table
-- Stores call records for the remote slider solve API integration.
-- This table is append-only (only INSERT/SELECT), non-destructive.
-- Uses CREATE TABLE IF NOT EXISTS for idempotent re-runs.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `xianyu_remote_slider_solve_record` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `request_id` VARCHAR(64) NOT NULL COMMENT 'request unique id',
  `account_id` BIGINT NULL COMMENT 'related xianyu account id',
  `account_name` VARCHAR(128) NULL COMMENT 'account name snapshot',
  `trigger_scene` VARCHAR(32) NOT NULL DEFAULT 'manual',
  `status` VARCHAR(32) NOT NULL DEFAULT 'retrying',
  `failure_reason` VARCHAR(64) NOT NULL DEFAULT '',
  `error_message` TEXT NULL,
  `duration_ms` INT NOT NULL DEFAULT 0,
  `token_charged` INT NOT NULL DEFAULT 0,
  `remote_status` VARCHAR(32) NOT NULL DEFAULT '',
  `remote_solved` TINYINT NOT NULL DEFAULT 0,
  `client_ip` VARCHAR(64) NOT NULL DEFAULT '',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_request_id` (`request_id`),
  KEY `idx_account_created` (`account_id`, `created_at`),
  KEY `idx_status_created` (`status`, `created_at`),
  KEY `idx_trigger_scene` (`trigger_scene`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='remote slider solve record';