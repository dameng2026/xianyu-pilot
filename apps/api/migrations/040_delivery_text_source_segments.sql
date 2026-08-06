-- ============================================================
-- 040: 为 delivery_text_source 增加 segments JSON 列
-- 同步商业版 V1.66 功能：货源库支持多条正文逐条发送 + 图片发货
--
-- segments 结构（JSON 数组，可为空）：
--   [
--     {"type": "text",  "content": "您好，商品已发货..."},
--     {"type": "image", "imageUrl": "/uploads/images/xxx.jpg", "assetId": 123},
--     {"type": "text",  "content": "请确认收货后给个好评~"}
--   ]
--   - type=text  : 纯文本消息（content 必填）
--   - type=image : 图片消息（imageUrl 必填，assetId 可选）
--   - 每条 segment 只能是 text 或 image 二选一
--
-- 兼容性：
--   - MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS，使用 INFORMATION_SCHEMA 动态 SQL
--   - 字段可为空，旧货源（仅 content 字段）不受影响，执行端回退到单条发送
--   - 保留 content 列不动，向后兼容
-- ============================================================

SET NAMES utf8mb4;

SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'delivery_text_source' AND COLUMN_NAME = 'segments');

SET @sql = IF(@col_exists = 0,
  'ALTER TABLE `delivery_text_source` ADD COLUMN `segments` JSON NULL COMMENT ''多条正文配置（JSON 数组，每条 type=text/image 二选一，空则回退 content 单条发送）''',
  'SELECT 1');

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
