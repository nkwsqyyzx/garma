-- V002_add_strategy_tables.sql
-- 策略成交流水表 + 每日持仓快照表

-- 策略成交流水（append-only，每笔买入/卖出生成一行）
CREATE TABLE IF NOT EXISTS `strategy_trades` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` VARCHAR(32) NOT NULL COMMENT '证券账号',
  `stock_code` VARCHAR(20) NOT NULL COMMENT '股票代码',
  `stock_name` VARCHAR(50) DEFAULT NULL COMMENT '股票名称',
  `direction` VARCHAR(4) NOT NULL COMMENT 'buy/sell',
  `volume` INT NOT NULL COMMENT '成交数量',
  `price` DECIMAL(12,4) NOT NULL COMMENT '成交均价',
  `amount` DECIMAL(16,4) NOT NULL COMMENT '成交金额',
  `strategy` VARCHAR(100) DEFAULT NULL COMMENT '策略名',
  `factor` VARCHAR(50) DEFAULT NULL COMMENT '因子',
  `remark` VARCHAR(200) DEFAULT NULL COMMENT '备注(原始其他字段)',
  `trade_date` DATE NOT NULL COMMENT '成交日期',
  `source` VARCHAR(20) NOT NULL DEFAULT 'order' COMMENT '来源: order/import/manual',
  `order_req_id` VARCHAR(64) DEFAULT NULL COMMENT '关联 qmt_orders.req_id',
  `linked_req_id` VARCHAR(64) DEFAULT NULL COMMENT '卖出时关联的买入order_req_id',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_account_date` (`account_id`, `trade_date`),
  KEY `idx_stock_date` (`stock_code`, `trade_date`),
  KEY `idx_order_req_id` (`order_req_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略成交流水';

-- 每日持仓快照（盘后定时任务生成，本期仅建表）
CREATE TABLE IF NOT EXISTS `daily_positions` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_id` VARCHAR(32) NOT NULL COMMENT '证券账号',
  `snapshot_date` DATE NOT NULL COMMENT '快照日期',
  `stock_code` VARCHAR(20) NOT NULL COMMENT '股票代码',
  `stock_name` VARCHAR(50) DEFAULT NULL COMMENT '股票名称',
  `strategy` VARCHAR(100) DEFAULT NULL COMMENT '策略名',
  `factor` VARCHAR(50) DEFAULT NULL COMMENT '因子',
  `remark` VARCHAR(200) DEFAULT NULL COMMENT '备注',
  `buy_date` DATE NOT NULL COMMENT '买入日期',
  `volume` INT NOT NULL COMMENT '持仓量',
  `avg_price` DECIMAL(12,4) NOT NULL COMMENT '加权均价',
  `cost` DECIMAL(16,4) NOT NULL COMMENT '持仓成本',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_snapshot_account_stock_strategy_buy` (`snapshot_date`, `account_id`, `stock_code`, `strategy`, `factor`, `buy_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日持仓快照';
