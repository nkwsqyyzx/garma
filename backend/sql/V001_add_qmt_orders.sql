-- V001_add_qmt_orders.sql
-- QMT A 股委托记录表（设计文档 Section 8.3）

CREATE TABLE IF NOT EXISTS `qmt_orders` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `req_id` VARCHAR(64) NOT NULL COMMENT '系统生成唯一请求ID',
  `order_id` VARCHAR(64) DEFAULT NULL COMMENT 'QMT返回委托编号',
  `account_id` VARCHAR(32) NOT NULL COMMENT '证券账号',
  `stock_code` VARCHAR(20) NOT NULL COMMENT '股票代码（如600519.SH）',
  `stock_name` VARCHAR(50) DEFAULT NULL COMMENT '股票名称',
  `order_type` VARCHAR(10) NOT NULL COMMENT 'buy/sell',
  `order_volume` INT NOT NULL COMMENT '委托数量',
  `traded_volume` INT NOT NULL DEFAULT 0 COMMENT '成交数量',
  `price_type` VARCHAR(20) NOT NULL COMMENT 'limit/market/best5',
  `price` DECIMAL(10,4) NOT NULL DEFAULT 0 COMMENT '委托价格',
  `traded_price` DECIMAL(10,4) DEFAULT NULL COMMENT '成交均价',
  `status` VARCHAR(30) NOT NULL DEFAULT 'DRAFT' COMMENT '订单状态',
  `status_msg` VARCHAR(200) DEFAULT NULL COMMENT '状态说明',
  `strategy_name` VARCHAR(50) DEFAULT NULL COMMENT '策略名称',
  `order_remark` VARCHAR(200) DEFAULT NULL COMMENT '委托备注',
  `retry_count` TINYINT NOT NULL DEFAULT 0 COMMENT '重试次数',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_req_id` (`req_id`),
  KEY `idx_account_status` (`account_id`, `status`),
  KEY `idx_stock_created` (`stock_code`, `created_at`),
  KEY `idx_order_id` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='QMT A 股委托记录';
