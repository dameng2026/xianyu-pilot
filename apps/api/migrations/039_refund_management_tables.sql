SET NAMES utf8mb4;

-- ============================================================
-- 退款管理（开源版）
-- 参考：商业版 V1.41 + automation-service V1.22
-- 简化：单管理员版本，去掉 tenant_id 维度
-- ============================================================

-- 退款记录主表（退款 ID 维度：一个订单可有多次退款）
CREATE TABLE IF NOT EXISTS `xianyu_refund` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL COMMENT '所属闲鱼账号 ID',
  `external_refund_id` VARCHAR(64) NOT NULL COMMENT '闲鱼退款 ID',
  `external_order_id` VARCHAR(64) NULL COMMENT '订单 ID',
  `external_item_id` VARCHAR(64) NULL COMMENT '商品 ID',
  `item_title` VARCHAR(500) NULL COMMENT '商品标题',
  `item_pic_url` TEXT NULL COMMENT '商品图片 URL',
  `item_info_lines` TEXT NULL COMMENT '商品规格补充信息',
  `buy_num` VARCHAR(32) NULL COMMENT '购买件数',
  `refund_fee` DECIMAL(18,4) NULL COMMENT '退款金额',
  `auction_price` DECIMAL(18,4) NULL COMMENT '商品成交单价',
  `order_status` VARCHAR(64) NULL COMMENT '退款大类（未发货退款/已发货退款/退货退款）',
  `order_simple_remark` VARCHAR(255) NULL COMMENT '订单退款简要状态',
  `refund_status` VARCHAR(64) NULL COMMENT '退款详细状态',
  `refund_status_desc` VARCHAR(500) NULL COMMENT '状态倒计时或补充说明',
  `common_refund_status` VARCHAR(64) NULL COMMENT '服务端状态代码',
  `refund_reason` VARCHAR(500) NULL COMMENT '退款原因',
  `cs_status` VARCHAR(64) NULL COMMENT '客服介入状态',
  `logistics_company` VARCHAR(128) NULL COMMENT '物流公司',
  `logistics_mail_no` VARCHAR(128) NULL COMMENT '物流单号（脱敏）',
  `consign_time` DATETIME NULL COMMENT '发货时间',
  `refund_create_time` DATETIME NULL COMMENT '退款申请时间',
  `common_create_time` DATETIME NULL COMMENT '订单创建时间',
  `buyer_nick` VARCHAR(255) NULL COMMENT '买家昵称（脱敏）',
  `right_buttons_json` TEXT NULL COMMENT '操作按钮列表 JSON',
  `ext_total_refund_fee` DECIMAL(18,4) NULL COMMENT '当前查询范围退款总金额',
  `raw_json` TEXT NULL COMMENT '原始响应记录（脱敏）',
  `sync_status` VARCHAR(32) NOT NULL DEFAULT 'synced' COMMENT 'synced/pending_refresh',
  `last_synced_time` DATETIME(6) NULL,
  `deleted` SMALLINT NOT NULL DEFAULT 0,
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_refund_account_external` (`account_id`, `external_refund_id`),
  INDEX `idx_refund_account` (`account_id`, `deleted`),
  INDEX `idx_refund_status` (`deleted`, `order_status`),
  INDEX `idx_refund_time` (`deleted`, `refund_create_time`),
  INDEX `idx_refund_sync_status` (`account_id`, `sync_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='闲鱼退款记录';

-- 退款同步任务追踪
CREATE TABLE IF NOT EXISTS `xianyu_refund_sync_task` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `sync_id` VARCHAR(80) NOT NULL COMMENT '同步任务 ID',
  `account_id` BIGINT NULL COMMENT '账号 ID（NULL=全部账号）',
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
  UNIQUE KEY `uk_refund_sync_id` (`sync_id`),
  INDEX `idx_refund_sync_account` (`account_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='退款同步任务追踪';

-- 账号级退款同步状态
CREATE TABLE IF NOT EXISTS `xianyu_refund_account_state` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL COMMENT '闲鱼账号 ID',
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
  UNIQUE KEY `uk_refund_state_account` (`account_id`),
  INDEX `idx_refund_state_syncing` (`is_syncing`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账号级退款同步状态';
