-- Fix rag_chunk table: make old document_id and knowledge_base_id nullable
-- because the ORM now uses doc_id/kb_id instead

ALTER TABLE `rag_chunk`
  MODIFY COLUMN `document_id` BIGINT NULL COMMENT 'deprecated: use doc_id',
  MODIFY COLUMN `knowledge_base_id` BIGINT NULL COMMENT 'deprecated: use kb_id';

ALTER TABLE `rag_document`
  MODIFY COLUMN `knowledge_base_id` BIGINT NULL COMMENT 'deprecated: use kb_id';
