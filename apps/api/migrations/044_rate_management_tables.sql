SET NAMES utf8mb4;

-- ============================================================
-- 评价管理（开源版）
-- 参考：商业版 automation-service V1.23/V1.25 + core-api V1.40
-- 简化：单管理员版本，去掉 tenant_id 维度
-- 包含：评价记录、同步任务追踪、账号级同步状态、自动评价配置、自动评价执行日志
-- ============================================================

-- 评价记录主表（订单维度：一个订单只允许一次卖家评价）
CREATE TABLE IF NOT EXISTS `xianyu_rate` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL COMMENT '所属闲鱼账号ID',
  `external_order_id` VARCHAR(64) NOT NULL COMMENT '订单ID（字符串存储避免大整数精度丢失）',
  `external_item_id` VARCHAR(64) NULL COMMENT '商品ID',
  `buyer_id` VARCHAR(120) NULL COMMENT '买家ID',
  `buyer_nick` VARCHAR(255) NULL COMMENT '买家昵称（脱敏存储）',
  `buyer_icon` TEXT NULL COMMENT '买家头像URL',
  `item_title` VARCHAR(500) NULL COMMENT '商品标题',
  `item_pic_url` TEXT NULL COMMENT '商品图片URL',
  `item_info_lines` TEXT NULL COMMENT '商品规格补充信息',
  `order_status` VARCHAR(64) NULL COMMENT '订单状态',
  `seller_rate_status` VARCHAR(16) NULL COMMENT '卖家评价状态码（原始字符串存储）',
  `in_refund` VARCHAR(16) NULL COMMENT '是否在退款中',
  `consign_time` DATETIME NULL COMMENT '发货时间',
  `order_create_time` DATETIME NULL COMMENT '订单创建时间',
  `pay_success_time` DATETIME NULL COMMENT '支付成功时间',
  `finish_time` DATETIME NULL COMMENT '交易完成时间',
  `logistics_company` VARCHAR(128) NULL COMMENT '物流公司',
  `logistics_mail_no` VARCHAR(128) NULL COMMENT '物流单号（脱敏存储）',
  `buyer_rate_content` TEXT NULL COMMENT '买家评价内容',
  `buyer_rate_level` VARCHAR(16) NULL COMMENT '买家评价等级',
  `buyer_rate_time` DATETIME NULL COMMENT '买家评价时间',
  `buyer_rate_images` TEXT NULL COMMENT '买家评价图片列表 JSON',
  `seller_rate_content` TEXT NULL COMMENT '卖家评价内容',
  `seller_rate_level` VARCHAR(16) NULL COMMENT '卖家评价等级',
  `seller_rate_time` DATETIME NULL COMMENT '卖家评价时间',
  `seller_rate_images` TEXT NULL COMMENT '卖家评价图片列表 JSON',
  `seller_rate_id` VARCHAR(64) NULL COMMENT '卖家评价ID',
  `has_seller_rate` TINYINT NOT NULL DEFAULT 0 COMMENT '1=已评价, 0=未评价',
  `rate_reviewable` TINYINT NOT NULL DEFAULT 0 COMMENT '1=可评价, 0=不可评价',
  `raw_json` TEXT NULL COMMENT '原始响应记录（脱敏）',
  `sync_status` VARCHAR(32) NOT NULL DEFAULT 'synced' COMMENT 'synced/pending_refresh',
  `last_synced_time` DATETIME(6) NULL,
  `deleted` SMALLINT NOT NULL DEFAULT 0,
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_rate_account_order` (`account_id`, `external_order_id`),
  INDEX `idx_rate_account` (`account_id`, `deleted`),
  INDEX `idx_rate_status` (`deleted`, `rate_reviewable`),
  INDEX `idx_rate_time` (`deleted`, `finish_time`),
  INDEX `idx_rate_sync_status` (`account_id`, `sync_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='闲鱼评价记录（订单维度）';

-- 评价同步任务追踪
CREATE TABLE IF NOT EXISTS `xianyu_rate_sync_task` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `sync_id` VARCHAR(80) NOT NULL COMMENT '同步任务ID（唯一）',
  `account_id` BIGINT NULL COMMENT '账号ID（NULL=全部账号）',
  `scope` VARCHAR(20) NOT NULL DEFAULT 'single' COMMENT 'single/all',
  `status` VARCHAR(30) NOT NULL DEFAULT 'queued' COMMENT 'queued/running/completed/failed',
  `progress` INT NOT NULL DEFAULT 0,
  `total_count` INT NOT NULL DEFAULT 0,
  `new_count` INT NOT NULL DEFAULT 0,
  `updated_count` INT NOT NULL DEFAULT 0,
  `failed_count` INT NOT NULL DEFAULT 0,
  `succeeded_count` INT NOT NULL DEFAULT 0,
  `duration_seconds` FLOAT NOT NULL DEFAULT 0,
  `error_message` TEXT NULL,
  `started_time` DATETIME(6) NULL,
  `finished_time` DATETIME(6) NULL,
  `deleted` SMALLINT NOT NULL DEFAULT 0,
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_rate_sync_id` (`sync_id`),
  INDEX `idx_rate_sync_account` (`account_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='评价同步任务追踪';

-- 账号级评价同步状态
CREATE TABLE IF NOT EXISTS `xianyu_rate_account_state` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL COMMENT '闲鱼账号ID',
  `last_sync_time` DATETIME(6) NULL,
  `last_sync_status` VARCHAR(30) NULL COMMENT 'success/failed/partial',
  `last_sync_error` VARCHAR(500) NULL,
  `last_total_count` INT NULL,
  `is_syncing` SMALLINT NOT NULL DEFAULT 0 COMMENT '1=同步中',
  `sync_started_time` DATETIME(6) NULL,
  `last_full_sync_time` DATETIME(6) NULL,
  `deleted` SMALLINT NOT NULL DEFAULT 0,
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_rate_state_account` (`account_id`),
  INDEX `idx_rate_state_syncing` (`is_syncing`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账号级评价同步状态';

-- 账号自动评价配置
CREATE TABLE IF NOT EXISTS `xianyu_account_auto_rate_config` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL COMMENT '闲鱼账号ID',
  `enabled` TINYINT NOT NULL DEFAULT 0 COMMENT '是否启用自动评价',
  `rate_type` VARCHAR(20) NOT NULL DEFAULT 'text' COMMENT 'text=固定文本 api=API模式',
  `text_content` TEXT NULL COMMENT '固定评价内容',
  `api_url` VARCHAR(500) NULL COMMENT '外部API地址',
  `schedule_hour` INT NOT NULL DEFAULT 9 COMMENT '每天执行时间（0-23），默认9点',
  `deleted` SMALLINT NOT NULL DEFAULT 0,
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_xyaarc_account` (`account_id`),
  INDEX `idx_xyaarc_enabled_hour` (`enabled`, `schedule_hour`, `deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账号自动评价配置';

-- 自动补评价执行日志
CREATE TABLE IF NOT EXISTS `xianyu_auto_rate_log` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL COMMENT '闲鱼账号ID',
  `run_time` DATETIME(6) NOT NULL COMMENT '本次执行时间',
  `schedule_hour` INT NULL COMMENT '配置的执行时间（手动触发为NULL）',
  `trigger_type` VARCHAR(20) NOT NULL DEFAULT 'scheduled' COMMENT 'scheduled=定时, manual=手动',
  `status` VARCHAR(20) NOT NULL DEFAULT 'success' COMMENT 'success/skip/failed/partial',
  `total_pending` INT NOT NULL DEFAULT 0,
  `total_success` INT NOT NULL DEFAULT 0,
  `total_failed` INT NOT NULL DEFAULT 0,
  `total_skipped` INT NOT NULL DEFAULT 0,
  `error_message` VARCHAR(500) NULL,
  `details_json` TEXT NULL COMMENT '每条订单处理结果明细',
  `duration_seconds` FLOAT NOT NULL DEFAULT 0,
  `deleted` SMALLINT NOT NULL DEFAULT 0,
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_arl_account_time` (`account_id`, `run_time`),
  INDEX `idx_arl_status` (`status`, `deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='自动补评价执行日志';
