-- V003_add_asset_snapshots_and_transfers.sql
-- 每日资产快照表 + 银证转账记录表

-- 每日资产快照（盘前/盘后各采集一次）
CREATE TABLE IF NOT EXISTS `daily_asset_snapshots` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` VARCHAR(32) NOT NULL COMMENT '证券账号',
  `trade_date` DATE NOT NULL COMMENT '交易日期',
  `snapshot_type` VARCHAR(20) NOT NULL COMMENT 'pre_market / post_market',
  `total_asset` DECIMAL(16,4) NOT NULL COMMENT '总资产',
  `cash` DECIMAL(16,4) NOT NULL COMMENT '可用资金',
  `frozen_cash` DECIMAL(16,4) NOT NULL COMMENT '冻结资金',
  `market_value` DECIMAL(16,4) NOT NULL COMMENT '证券市值',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_account_date_type` (`account_id`, `trade_date`, `snapshot_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日资产快照';

-- 银证转账记录
CREATE TABLE IF NOT EXISTS `fund_transfers` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` VARCHAR(32) NOT NULL COMMENT '证券账号',
  `trade_date` DATE NOT NULL COMMENT '交易日期',
  `direction` VARCHAR(10) NOT NULL COMMENT 'deposit / withdraw',
  `amount` DECIMAL(16,4) NOT NULL COMMENT '金额',
  `note` VARCHAR(200) DEFAULT NULL COMMENT '备注',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_account_date` (`account_id`, `trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='银证转账记录';
