-- ============================================================
-- 045: 售整自动上架 + 商品编辑快照表
--
-- 1. xianyu_goods 增加售整自动上架相关字段：
--    auto_relist_enabled / has_snapshot / original_quantity /
--    next_relist_goods_id / relist_source_goods_id / last_relist_at
-- 2. 新增 xianyu_goods_edit_snapshot 编辑快照表：
--    保存发布/编辑成功后的完整商品数据，用于编辑回显兜底与售整自动上架重发。
--
-- 兼容性：
--   - 全部使用 CREATE TABLE IF NOT EXISTS / 存在性检查 ADD COLUMN
--   - 仅追加，不修改已有表结构，不删除任何数据
--   - 字段可为空/带默认值，旧商品不受影响
-- ============================================================

SET NAMES utf8mb4;

-- --------------------------------------------------------------------------
-- 1. xianyu_goods 售整自动上架字段
-- --------------------------------------------------------------------------

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'xianyu_goods' AND column_name = 'auto_relist_enabled'),
  'SELECT 1',
  'ALTER TABLE `xianyu_goods` ADD COLUMN `auto_relist_enabled` TINYINT NOT NULL DEFAULT 0 COMMENT ''售整自动上架开关：0关 1开'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'xianyu_goods' AND column_name = 'has_snapshot'),
  'SELECT 1',
  'ALTER TABLE `xianyu_goods` ADD COLUMN `has_snapshot` TINYINT NOT NULL DEFAULT 0 COMMENT ''是否有完整数据快照：0无 1有'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'xianyu_goods' AND column_name = 'original_quantity'),
  'SELECT 1',
  'ALTER TABLE `xianyu_goods` ADD COLUMN `original_quantity` INT NULL COMMENT ''商品原始库存（售整场景=1）'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'xianyu_goods' AND column_name = 'next_relist_goods_id'),
  'SELECT 1',
  'ALTER TABLE `xianyu_goods` ADD COLUMN `next_relist_goods_id` BIGINT NULL COMMENT ''重发后的新商品记录ID（追溯重发链路）'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'xianyu_goods' AND column_name = 'relist_source_goods_id'),
  'SELECT 1',
  'ALTER TABLE `xianyu_goods` ADD COLUMN `relist_source_goods_id` BIGINT NULL COMMENT ''本商品由哪个原商品重发而来（防止无限链式重发）'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'xianyu_goods' AND column_name = 'last_relist_at'),
  'SELECT 1',
  'ALTER TABLE `xianyu_goods` ADD COLUMN `last_relist_at` DATETIME NULL COMMENT ''上次重发时间（限流与诊断）'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- --------------------------------------------------------------------------
-- 2. 商品编辑快照表（单租户精简版，无 tenant_id）
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `xianyu_goods_edit_snapshot` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL,
  `external_goods_id` VARCHAR(128) NOT NULL COMMENT '闲鱼商品itemId',
  `snapshot_json` JSON NOT NULL COMMENT '完整商品数据快照',
  `source` VARCHAR(32) NOT NULL DEFAULT 'publish' COMMENT '快照来源：publish/edit/detail_api/relist',
  `account_type` VARCHAR(16) NOT NULL DEFAULT 'fish_shop' COMMENT '账号类型：fish_shop / normal',
  `deleted` TINYINT NOT NULL DEFAULT 0,
  `created_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_snapshot_lookup` (`account_id`, `external_goods_id`, `account_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品编辑/发布快照（回显兜底与售整自动上架）';
