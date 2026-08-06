SET NAMES utf8mb4;

-- ============================================================
-- 041: 闲鱼账号会员等级表（开源版）
-- 参考：商业版 xianyu_account_membership 表
-- 简化：单管理员版本，去掉 tenant_id 维度
--
-- 字段说明：
--   account_id   - 关联 xianyu_account.id
--   level        - 会员等级：normal(普通) / vip / svip
--   expired_time - 过期时间（NULL 表示永久）
--   status       - 状态：1正常 0过期
--
-- 唯一索引：uk_account_membership(account_id) - 一个账号一条会员记录
-- 普通索引：idx_membership_level(level)       - 按等级查询
-- ============================================================

CREATE TABLE IF NOT EXISTS `xianyu_account_membership` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` BIGINT NOT NULL COMMENT '所属闲鱼账号 ID',
  `level` VARCHAR(20) NOT NULL DEFAULT 'normal' COMMENT '会员等级：normal/vip/svip',
  `expired_time` DATETIME NULL COMMENT '过期时间（NULL 表示永久）',
  `status` INT NOT NULL DEFAULT 1 COMMENT '1正常 0过期',
  `created_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_account_membership` (`account_id`),
  INDEX `idx_membership_level` (`level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='闲鱼账号会员等级';
