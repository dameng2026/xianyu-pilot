-- ============================================================
-- 043: card_group 表增加 sku_property_key 字段（SKU 专属卡密池）
--
-- 用于支持多规格发货：每个 SKU 可绑定专属卡密分组，按 SKU 精确发货。
-- 字段可为空，旧卡密分组不受影响。
--
-- 兼容性：
--   - MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS，使用 INFORMATION_SCHEMA 动态 SQL
--   - 字段可为空，旧卡密分组（无 SKU 归属）不受影响
-- ============================================================

SET NAMES utf8mb4;

SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'card_group' AND COLUMN_NAME = 'sku_property_key');

SET @sql = IF(@col_exists = 0,
  'ALTER TABLE `card_group` ADD COLUMN `sku_property_key` VARCHAR(500) NULL COMMENT ''SKU 专属卡密池：绑定的 SKU property_key，用于多规格发货时按 SKU 匹配卡密组''',
  'SELECT 1');

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;