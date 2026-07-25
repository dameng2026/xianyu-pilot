-- Migration 036: model_config_image_prompt compatibility upgrade
-- 005_runtime_schema_compatibility.sql upgraded model_config to the new field
-- scheme but missed model_config_image_prompt. 001_init created this table with
-- the legacy fields (name/category_key/match_keywords/prompt_template/enabled/
-- sort_order) while the ORM ModelConfigImagePrompt expects
-- (model_config_id/prompt_name/prompt_content/negative_prompt/params_json/
-- deleted/created_time). This migration adds the missing columns and backfills
-- from the legacy fields without dropping anything, mirroring how 005 handled
-- model_config. Each ADD COLUMN is guarded by information_schema so the script
-- is safe to rerun after a partial failure.

SET NAMES utf8mb4;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'model_config_id'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `model_config_id` BIGINT NULL'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'prompt_name'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `prompt_name` VARCHAR(200) NULL'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'prompt_content'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `prompt_content` TEXT NULL'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'negative_prompt'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `negative_prompt` TEXT NULL'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'params_json'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `params_json` JSON NULL'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'deleted'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `deleted` SMALLINT DEFAULT 0'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'created_time'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `created_time` DATETIME NULL DEFAULT CURRENT_TIMESTAMP'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'status'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `status` SMALLINT NULL DEFAULT 1 COMMENT ''1启用 0禁用'''
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'updated_time'),
  'SELECT 1',
  'ALTER TABLE `model_config_image_prompt` ADD COLUMN `updated_time` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

-- ---- Data backfill: map legacy columns to ORM columns --------------------
-- Each UPDATE is guarded by information_schema so it only runs when the
-- legacy column still exists (the 001_init schema may have already been
-- partially migrated), mirroring the defensive approach used by 005.

-- prompt_name <- name
SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'name'),
  'UPDATE `model_config_image_prompt` SET `prompt_name` = COALESCE(NULLIF(`prompt_name`, ''''), `name`, '''') WHERE `prompt_name` IS NULL OR `prompt_name` = ''''',
  'SELECT 1'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

-- prompt_content <- prompt_template
SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND column_name = 'prompt_template'),
  'UPDATE `model_config_image_prompt` SET `prompt_content` = COALESCE(`prompt_content`, `prompt_template`) WHERE `prompt_content` IS NULL',
  'SELECT 1'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

-- params_json <- legacy scalar image fields (image_size + quality, if both present)
SET @ddl = IF(
  (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt'
      AND column_name IN ('image_size', 'quality')
  ) = 2,
  'UPDATE `model_config_image_prompt` SET `params_json` = COALESCE(`params_json`, JSON_OBJECT(''imageSize'', COALESCE(`image_size`, ''''), ''quality'', COALESCE(`quality`, ''''))) WHERE `params_json` IS NULL OR JSON_LENGTH(`params_json`) = 0',
  'SELECT 1'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

-- default timestamps, deleted and status (these columns are guaranteed to
-- exist after the ADD COLUMN block above, so no guard is needed here)
UPDATE `model_config_image_prompt`
SET `created_time` = COALESCE(`created_time`, `updated_time`, NOW()),
    `deleted` = COALESCE(`deleted`, 0),
    `status` = COALESCE(`status`, 1);

SET @ddl = IF(
  EXISTS(SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'model_config_image_prompt' AND index_name = 'idx_model_config_image_prompt_config'),
  'SELECT 1',
  'CREATE INDEX `idx_model_config_image_prompt_config` ON `model_config_image_prompt` (`model_config_id`, `deleted`)'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;
