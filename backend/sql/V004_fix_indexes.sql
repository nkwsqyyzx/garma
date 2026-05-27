-- V004_fix_indexes.sql
-- 修复索引缺失和列缺失问题

-- 1. qmt_orders: 添加 batch_id 列（ORM 已定义但 SQL DDL 缺失）
-- 使用 ADD COLUMN IF NOT EXISTS 的 MySQL 8.0 兼容写法
SET @col_exists = 0;
SELECT COUNT(*) INTO @col_exists
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'qmt_orders'
   AND COLUMN_NAME = 'batch_id';

SET @sql = IF(@col_exists = 0,
    'ALTER TABLE `qmt_orders` ADD COLUMN `batch_id` VARCHAR(64) DEFAULT NULL COMMENT ''拆单批次ID, 同一批拆单共享'' AFTER `linked_req_id`',
    'SELECT ''qmt_orders.batch_id already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2. strategy_trades: 添加 batch_id 列（V002 DDL 缺失，ORM 已定义）
SET @col_exists = 0;
SELECT COUNT(*) INTO @col_exists
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'strategy_trades'
   AND COLUMN_NAME = 'batch_id';

SET @sql = IF(@col_exists = 0,
    'ALTER TABLE `strategy_trades` ADD COLUMN `batch_id` VARCHAR(64) DEFAULT NULL COMMENT ''拆单批次ID, 同一批拆单共享'' AFTER `linked_req_id`',
    'SELECT ''strategy_trades.batch_id already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3. strategy_trades: 添加 linked_req_id 索引（目前无索引，查询全表扫描）
SET @idx_exists = 0;
SELECT COUNT(*) INTO @idx_exists
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'strategy_trades'
   AND INDEX_NAME = 'idx_linked_req_id';

SET @sql = IF(@idx_exists = 0,
    'ALTER TABLE `strategy_trades` ADD KEY `idx_linked_req_id` (`linked_req_id`)',
    'SELECT ''idx_linked_req_id already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 4. strategy_trades: 添加 batch_id 索引（ORM 有定义但 SQL 缺失）
SET @idx_exists = 0;
SELECT COUNT(*) INTO @idx_exists
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'strategy_trades'
   AND INDEX_NAME = 'idx_batch_id';

SET @sql = IF(@idx_exists = 0,
    'ALTER TABLE `strategy_trades` ADD KEY `idx_batch_id` (`batch_id`)',
    'SELECT ''idx_batch_id already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
