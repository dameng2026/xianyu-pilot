-- ============================================================
-- 042: 商品多规格 SKU 表（同步商业版多规格发货功能）
--
-- 新增三张表：
--   xianyu_goods_property        规格类型表（颜色/尺码等）
--   xianyu_goods_property_value  规格值表（红/蓝/S/M等）
--   xianyu_goods_sku             SKU 表（记录每个商品的 SKU 及其属性键）
--
-- 兼容性：
--   - 全部使用 CREATE TABLE IF NOT EXISTS，已有数据不受影响
--   - 仅追加表，不修改已有表结构
--   - 字段可为空，旧商品（无 SKU）不受影响，执行端回退到商品通用配置
-- ============================================================

SET NAMES utf8mb4;

-- 规格类型表（颜色/尺码等）
CREATE TABLE IF NOT EXISTS `xianyu_goods_property` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL,
  `goods_id` BIGINT NOT NULL,
  `property_name` VARCHAR(100) NOT NULL COMMENT '规格名称（颜色/尺码等）',
  `created_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_goods_property_goods` (`goods_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品规格类型（颜色/尺码等）';

-- 规格值表（红/蓝/S/M等）
CREATE TABLE IF NOT EXISTS `xianyu_goods_property_value` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `property_id` BIGINT NOT NULL,
  `value_name` VARCHAR(200) NOT NULL COMMENT '规格值（红/蓝/S/M等）',
  `created_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_property_value_property` (`property_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品规格值（红/蓝/S/M等）';

-- SKU 表
CREATE TABLE IF NOT EXISTS `xianyu_goods_sku` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL,
  `goods_id` BIGINT NOT NULL,
  `sku_id` VARCHAR(64) NOT NULL COMMENT '闲鱼SKU ID',
  `property_key` VARCHAR(500) NOT NULL COMMENT '规范化键（排序后的属性键值对）',
  `property_list_json` TEXT NULL COMMENT '原始属性列表JSON',
  `price` DECIMAL(10,2) NULL,
  `stock` INT NULL,
  `created_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_goods_sku` (`goods_id`, `sku_id`),
  INDEX `idx_goods_sku_goods` (`goods_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品 SKU 记录';