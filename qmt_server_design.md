# QMT-Server 技术设计文档

- **版本**：v1.3
- **日期**：2026-05-20
- **适用系统**：Alpha Finance Terminal + 华泰证券 MiniQMT
- **状态**：待评审

> v1.3 变更（行情订阅架构调整）：
> - 行情订阅（全市场 Tick 推送 → Redis）由独立旧实现（qmt_tick.py）接管，数据存储在 Redis db0（pickle 格式）
> - qmt-market 不再负责行情订阅和实时推送，仅提供 xtdata 按需查询 API（K 线、板块、基本信息、历史下载）
> - 移除 WebSocket 推送端点（`/ws/quote`）、订阅管理 API（`/subscribe/*`）
> - 移除 MarketHub 中的 `_on_tick`/`_on_kline` 回调、订阅池管理、Redis Stream 写入
> - Alpha 后端行情数据直接从旧实现 Redis（db0）读取，不再消费 `qmt:stream:agg`

> v1.2 变更（Section 5 Alpha Finance Terminal 集成审阅修复）：
> - QmtStatusWorker：修复 `def` → `async def` 方法签名；Pub/Sub 路径同步更新 `_last_hash` 防止轮询重复推送
> - QmtOrderWorker：改用 Consumer Group（XREADGROUP + XACK）防止崩溃丢事件；统一处理所有订单状态（submitted/reported/partial/filled/canceled/rejected）替代原先 3 个独立 handler
> - QmtService.place_order：明确两步写入流程——先写 MySQL DRAFT 行，再写 Redis 队列
> - proxy 接口：新增路径白名单校验，禁止透传交易类和控制类操作
> - 一键熔断：新增 `POST/DELETE/GET /api/v1/qmt/kill-switch` API，前端通过后端 API 操作而非直连 Redis
> - 前端数据策略：WebSocket 为主，轮询兜底从 5s/10s 放宽至 30s
> - QmtMarketWorker：补充 Tick 格式映射说明（A 股独立组件，直接透传无需转换）
> - 前端 WebSocket：添加指数退避自动重连（1s→2s→4s→...→30s 封顶）

> v1.1 变更：行情服务（qmt-market）与交易服务（qmt-trade）拆分为独立进程；Python 环境路径确认；端口：行情 :8091 / 交易 :8090

## 目录

1. [系统概述](#1-系统概述)
2. [整体架构](#2-整体架构)
3. [QMT-Server 详细设计（Windows 端）](#3-qmt-server-详细设计windows-端)
    - 3.1 目录结构
    - 3.2 核心模块
    - 3.3 行情模块（按需查询）    - 3.4 交易模块
    - 3.5 XtQuantTraderCallback 回调系统（核心）
    - 3.6 账户模块（事件驱动 + 轮询兜底）
    - 3.7 HTTP API 层（行情 :8091 / 交易 :8090 独立端口）
    - 3.8 Redis 桥接层（shared/redis_bridge.py 共享）
4. [数据协议规范](#4-数据协议规范)
    - 4.1 Redis 键命名规范
    - 4.2 行情数据格式
    - 4.3 交易数据格式
    - 4.4 HTTP 接口全表
5. [Alpha Finance Terminal 集成（Linux 端）](#5-alpha-finance-terminal-集成linux-端)
    - 5.1 新增服务层
    - 5.2 新增 API 路由
    - 5.3 前端页面设计
        - 5.3.0 页面职责划分（强制隔离）
        - 5.3.1 A 股交易页（/qmt-trade，独立）
        - 5.3.2 状态感知设计（前端三层）
        - 5.3.3 QMT 调试台（/qmt-debug，admin）
6. [高可用与一致性保障](#6-高可用与一致性保障)
7. [配置管理](#7-配置管理)
8. [部署指南](#8-部署指南)
9. [监控与健康检查](#9-监控与健康检查)
10. [风控设计](#10-风控设计)

## 1. 系统概述

### 1.1 背景

Alpha Finance Terminal 当前接入 Interactive Brokers (IBKR) 进行美股期权交易与行情订阅。
现需扩展接入华泰证券 MiniQMT 系统，实现：
A 股实时行情订阅：Level-2 Tick、分时、K 线数据
A 股量化下单：限价单、市价单、撤单
账户管理：资金查询、持仓查询、委托/成交查询
在线调试：系统管理员可在 Web 界面直接调用全部接口进行测试

### 1.2 核心设计原则

| 原则         | 实现方式                                                               |
|------------|--------------------------------------------------------------------|
| 高时效        | xtquant 回调 → Redis Stream（XADD）链路，P99 延迟目标 < 50ms                  |
| 稳定可靠       | 独立进程，心跳 watchdog，自动重连，NSSM Windows 服务托管                            |
| 数据一致性      | Redis 原子操作（Pipeline/Lua）+ 版本号 CAS 防覆盖                              |
| 进程级解耦      | 行情服务（qmt-market）与交易服务（qmt-trade）独立进程，零共享代码，纯 Redis 数据协议为唯一边界       |
| 量化策略低成本接入  | 策略进程只需连接 Redis：读 Stream 获取行情，写 List 发送指令，回报通过 Stream 订阅；无需了解服务内部实现 |
| 模块对外隔离     | QMT 两个服务与 Alpha 后端通过 Redis 异步通信，HTTP API 仅用于调试/控制指令                |
| 部署一次，不频繁迭代 | 完整接口覆盖，配置外置，版本兼容策略                                                 |

### 1.3 技术选型

| 组件            | 选型                             | 理由                                     |
|---------------|--------------------------------|----------------------------------------|
| QMT-Server 框架 | FastAPI + Uvicorn              | 异步 HTTP，与 Alpha 后端保持一致；xtquant 回调在独立线程 |
| QMT SDK       | xtquant（xtdata + xttrader）     | 华泰 MiniQMT 官方 Python SDK               |
| 进程管理          | Windows 服务 / NSSM              | 开机自启，崩溃自动重启                            |
| 与 Alpha 通信    | Redis（Stream + Pub/Sub + Hash） | 现有基础设施，无需额外组件                          |
| 配置            | config.json + 环境变量             | 避免硬编码，支持热更新部分配置                        |

## 2. 整体架构

┌────────────────────────────────────────────────────────────────────────┐
│ 局域网（192.168.3.0/24） │
│ │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ Windows 主机（192.168.3.10） │ │
│ │ │ │
│ │ ┌───────────────────────────────┐ │ │
│ │ │ 华泰 MiniQMT 交易终端 │ │ │
│ │ │     (userdata_mini 连接)                      │ │ │
│ │ └────────┬──────────────┬────────┘ │ │
│ │ │ xtdata IPC │ xttrader IPC │ │
│ │ ┌────────▼──────────┐ ┌▼─────────────────────────────────┐ │ │
│ │ │ qmt-market │ │ qmt-trade │ │ │
│ │ │   (HTTP :8091)          │ │                  (HTTP :8090)             │ │ │
│ │ │ │ │ │ │ │
│ │ │ MarketHub │ │ TradeHub + AccountHub │ │ │
│ │ │ StatusReporter │ │ CallbackHandler + CmdConsumer │ │ │
│ │ │ /quote /health │ │ SessionMgr + StatusReporter │ │ │
│ │ │ /control │ │ /trade /account /health │ │ │
│ │ └────────┬───────────┘ └──────────────┬────────────────────┘ │ │
│ │ │ XADD Stream │ HSET/XADD/BRPOPLPUSH │ │
│ └───────────┼──────────────────────────────┼───────────────────────┘ │
│ │ │ │
│ └───────────────┬──────────────┘ │
│ │ │
│ （
Redis 192.168.3.80:6379 ）— 进程间唯一数据边界 │
│ qmt_tick:{YYYYMMDD}:{code} （旧实现行情数据，db0 pickle） │
│ qmt:cmd:queue / qmt:event:order_update （交易指令与回报） │
│ qmt:market:status / qmt:trade:status （状态快照） │
│ │ │
│ ┌───────────────┤ │
│ │ │ │
│ ┌──────────▼──────┐ ┌─────▼────────────────────────────────────┐ │
│ │ 量化策略进程 │ │ Linux 主机（192.168.3.80） │ │
│ │ （可部署于任意 │ │ │ │
│ │ 可达 Redis 的 │ │ ┌─────────────────────────────────────┐ │ │
│ │ 机器） │ │ │ Alpha Finance Terminal │ │ │
│ │ │ │ │ qmt_service.py qmt_order_worker │ │ │
│ │ LPUSH qmt:          │ │ │ /api/v1/qmt/*      qmt_status_worker │ │ │
│ │ cmd:queue │ │ └─────────────────────────────────────┘ │ │
│ │ LPUSH qmt:          │ │ │ │
│ │ cmd:queue │ │ ┌─────────────────────────────────────┐ │ │
│ └─────────────────┘ │ │ Frontend （React） │ │ │
│ │ │ /qmt-trade /qmt-debug │ │ │
│ │ └─────────────────────────────────────┘ │ │
│ └───────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
核心设计思想：行情与交易通过 Redis 数据协议完全解耦
服务 依赖 SDK 端口 Redis 写入 Redis 读取
qmt- xtdata   :3301 qmt:market:status —
market only
qmt- xttrader :3300 qmt:event:order_update, qmt:order:status, qmt:cmd:queue
trade only qmt:snapshot:asset/position

量化策略接入只需两步：

## 1. 连接 Redis（192.168.1.70:6379）

## 2. `LRANGE qmt_tick:{YYYYMMDD}:{code} -1 -1` — 读取最新 tick

## 3. `LPUSH qmt:cmd:queue {JSON}` — 发送交易指令，`XREAD qmt:event:order_update`

接收回报

### 2.1 数据流说明

行情链路（旧实现，独立进程）
xtquant subscribe_whole_quote 回调
→ qmt_tick.py（独立进程）
→ Redis db0 LPUSH qmt_tick:{YYYYMMDD}:{code} （pickle 格式，每只股票每日列表）
→ Alpha 后端 QmtService.get_snapshot / get_tick（直接从 Redis 读取）

行情查询链路（按需查询，qmt-market）
HTTP 请求 → MarketHub → xtdata.get_full_tick / get_market_data_ex / get_instrument_detail / get_stock_list_in_sector
→ 直接返回查询结果

交易链路（可靠命令队列）
Alpha 后端 QmtService.place_order()
→ 写 MySQL qmt_orders 表（DRAFT 状态）
→ Redis LPUSH qmt:cmd:queue（命令入队）
→ QMT-Server CmdConsumer（BRPOPLPUSH 可靠弹出）
→ xttrader.order_stock_async()
→ 写 Redis HSET qmt:order:status:{order_id}
→ Redis XADD qmt:event:order_update（回报事件）
→ Alpha 后端消费，更新 MySQL + 通知

## 3. QMT-Server 详细设计（Windows 端）

### 3.1 目录结构

行情服务和交易服务作为两个完全独立的进程存在于同一代码仓库中，共用 `shared/` 公共
模块，但进程间无任何 Python 级别的直接调用，只通过 Redis 数据协议通信。
qmt-server/ # 代码仓库根目录
│
├── shared/ # 公共模块（两个服务共享，零业务逻辑）
│ ├── __init__.py
│ ├── redis_bridge.py 连接与原子操作封装（线程安全同步连接）
# Redis
│ ├── const.py # 全局常量（Redis 键模板、状态码映射、价格类型映
│ └── schemas/
│ ├── __init__.py
│ ├── quote.py # 行情 Pydantic 模型（Tick / Kline）
│ ├── trade.py # 交易命令与回报 Pydantic 模型
│ └── account.py # 账户资产 / 持仓 / 委托 Pydantic 模型
│
├── qmt-market/ # =====行情查询服务（独立进程）=====
│ ├── main.py # 启动入口：FastAPI :3301 + MarketHub
│ ├── requirements.txt # 仅含 xtdata 所需依赖（无 xttrader）
│ │
│ ├── core/
│ │ ├── __init__.py
│ │ ├── market_hub.py xtdata 按需查询（K线/板块/基本信息）
│ │ └── status_reporter.py # 行情服务健康上报（每10s写 qmt:market:status
│ │
│ └── api/
│ ├── __init__.py
│ ├── router.py # 路由总注册
│ ├── quote.py # 行情查询：最新 tick、K 线、基本信息、板块、历史
│ └── health.py # 健康检查：/health（xtdata 状态 + Redis 状态
│
├── qmt-trade/ # =====交易服务（独立进程）=====
│ ├── main.py # 启动入口：FastAPI :8090 + 交易后台线程群
│ ├── requirements.txt # 仅含 xttrader 所需依赖（无 xtdata）
│ │
│ ├── core/
│ │ ├── __init__.py
│ │ ├── trade_hub.py # xttrader 实例管理（下单 / 撤单 / 查询封装）
│ │ ├── callback_handler.py # XtQuantTraderCallback 实现（核心事件回调）
│ │ ├── account_hub.py # 账户状态内存快照（事件驱动 + 轮询兜底）
│ │ ├── cmd_consumer.py # BRPOPLPUSH 可靠队列消费（Redis → xttrader）
│ │ ├── session_mgr.py # xttrader 会话管理（重连 + 退避策略）
│ │ └── status_reporter.py # 交易服务健康上报（每10s写 qmt:trade:status）
│ │
│ └── api/
│ ├── __init__.py
│ ├── router.py # 路由总注册
│ ├── trade.py # 交易接口：下单 / 撤单
│ ├── account.py # 账户接口：资金 / 持仓 / 委托 / 成交
│ └── health.py # 健康检查：/health（xttrader 状态 + CmdCons
│
├── config.json # 共享配置（mini_path / account_id / redis
├── config.secret.json # 敏感配置（Redis 密码、通知 token，不纳入版本控
├── start_market.bat # 启动行情服务（NSSM 启动 / 直接运行均可）
├── start_trade.bat # 启动交易服务
├── start_all.bat # 同时启动两个服务
├── stop_all.bat # 同时停止两个服务
├── install_services.bat # NSSM 注册两个 Windows 服务
├── uninstall_services.bat # 卸载 Windows 服务
└── logs/
├── market/
│ └── app.log # 行情服务日志
└── trade/
├── app.log # 交易服务日志
└── trade_audit.log # 交易审计日志（独立，永久保留）

服务独立性保证：
| 约束项 | 说明 |
|--------|------|
| 无跨进程 Python import | `qmt-market/` 代码不 import `qmt-trade/` 任何模块，反之亦然 |
| 独立 requirements.txt | `qmt-market` 不安装 xttrader；`qmt-trade` 不安装 xtdata（若 xtquant
打包在一起则安装同一包但各自只使用各自子模块） |
| 独立启动 / 独立重启 | 行情服务崩溃不影响交易服务，反之亦然 |
| 独立健康状态 | `qmt:market:status` 和 `qmt:trade:status` 分别独立上报，前端分开展示 |
| 共享仅限 `shared/` | 仅 `redis_bridge.py` / `const.py` / `schemas/` 可被两个服务 import；业务逻辑严格隔离 |

### 3.2 核心模块关系

两个独立进程各自的模块树，仅通过 Redis 数据协议交互。

#### 3.2.1 行情进程（qmt-market/main.py）

qmt-market/main.py
├── FastAPI app（HTTP :3301）
│ ├── /quote — 行情查询（最新 tick / K 线 / 基本信息 / 板块 / 历史）
│ └── /health — 健康检查
├── MarketHub（xtdata 按需查询）
│ ├── get_tick / get_ticks_batch — 实时查询最新 Tick
│ ├── get_kline — K 线数据
│ ├── get_instrument_detail — 股票基本信息
│ ├── get_sector_list — 板块成分股
│ └── download_history — 历史数据下载
└── StatusReporter（独立线程，每10s）
├── 采集：xtdata 连接状态 + Redis 连通性
├── SET qmt:market:status（TTL=35s）
├── PUBLISH qmt:status:notify（频道，Alpha 后端监听）
└── 异常时企业微信告警（去重，仅首次触发）

#### 3.2.2 交易进程（qmt-trade/main.py）

qmt-trade/main.py
├── FastAPI app（HTTP :8090）
│ ├── /trade — 下单 / 撤单（HTTP 直接调用，调试用）
│ ├── /account — 资金 / 持仓 / 委托 / 成交查询
│ └── /health — 健康检查
├── SessionMgr（xttrader 会话 + 重连）
│ └── 注册 CallbackHandler 到 XtQuantTrader
├── CallbackHandler（XtQuantTraderCallback 实现）
│ ├── on_stock_order → AccountHub.apply_order_event()
│ ├── on_stock_trade → AccountHub.apply_trade_event()
│ ├── on_order_error → AccountHub.apply_order_error()
│ ├── on_cancel_error → AccountHub.apply_cancel_error()
│ ├── on_account_status → AccountHub.apply_account_status()
│ └── on_disconnected → SessionMgr.handle_disconnected()
├── AccountHub（账户状态管理，事件驱动 + 轮询兜底）
│ ├── 内存快照：_positions / _asset / _orders / _trades
│ ├── 事件入口：apply_*() 方法（由 CallbackHandler 调用，持锁写入）
│ └── 轮询线程：每 10s 全量拉取兜底（修正回调漏失事件）
├── CmdConsumer（Redis 命令消费，独立线程）
│ └── BRPOPLPUSH qmt:cmd:queue → xttrader.order_stock_async()
└── StatusReporter （独立线程，每10s）
├── 采集：xttrader 连接状态 + CmdConsumer 积压深度 + Redis 连通性
├── SET qmt:trade:status（TTL=35s）
├── PUBLISH qmt:status:notify（与行情服务共享同一通知频道）
└── 异常时企业微信告警

#### 3.2.3 线程模型汇总

| 进程                     | 线程                | 来源                         | 写入目标                                |
|------------------------|-------------------|----------------------------|-------------------------------------|
| qmt-market             | StatusReporter 线程 | 定时器（10s）                   | `qmt:market:status` + Pub/Sub       |
| qmt-trade              | xtquant 内部线程      | XtQuantTraderCallback 全部回调 | AccountHub 内存 + Redis               |
| qmt-trade              | CmdConsumer 线程    | BRPOPLPUSH 阻塞等待            | xttrader 下单 + Redis 回报              |
| qmt-trade              | AccountHub 轮询线程   | 定时器（10s）                   | AccountHub 内存 + Redis 快照            |
| qmt-trade              | StatusReporter 线程 | 定时器（10s）                   | `qmt:trade:status` + Pub/Sub        |
| qmt-trade / qmt-market | FastAPI 主线程       | HTTP 请求                    | 仅读内存 / xtdata，不写 Redis             |

所有 AccountHub 写操作（`apply_*` + 轮询）通过 `threading.Lock` 保护内存一致性；
Redis 写操作使用线程安全的同步 `redis.Redis` 连接。

### 3.3 行情模块（MarketHub）— 属于 qmt-market 进程

职责：封装 xtdata 同步查询接口，提供按需行情查询 API。
进程隔离：本模块不 import 任何 xttrader / TradeHub / AccountHub 相关代码。

> **v1.3 架构变更**：行情订阅（全市场 Tick 推送 → Redis）已由旧实现接管（`qmt_tick.py`，独立进程），
> 数据存储在 Redis db0（`qmt_tick:{YYYYMMDD}:{code}`，pickle 格式）。
> MarketHub 不再订阅行情、不再有回调、不再写入 Redis Stream/Hash。
> 本模块仅保留 xtdata 按需查询功能。

#### 3.3.1 查询接口

| 方法                    | 说明                                     |
|-----------------------|----------------------------------------|
| get_tick(code)        | 通过 xtdata.get_full_tick 实时查询单只股票 Tick |
| get_ticks_batch(codes) | 批量查询最新 Tick（最多 50 只）                |
| get_kline(code, period, count) | 查询 K 线数据                        |
| get_instrument_detail(code) | 查询股票基本信息（名称、涨停价等）             |
| get_sector_list(sector) | 查询板块成分股列表                          |
| get_full_tick(code)   | 查询完整 Tick（含逐笔）                    |
| download_history(...) | 触发历史行情下载                           |

### 3.4 交易模块（TradeHub + CmdConsumer）— 属于 qmt-trade 进程

职责：封装 xttrader 的下单/撤单操作，实现可靠命令队列消费。
进程隔离：本模块不 import 任何 xtdata / MarketHub 相关代码。

#### 3.4.1 CmdConsumer（可靠队列消费）

使用 BRPOPLPUSH 模式，防止消费中断导致命令丢失：
主队列： qmt:cmd:queue （LPUSH 写入，BRPOPLPUSH 弹出）
备份队列：qmt:cmd:queue:backup （消费中暂存，完成后 LREM 清除）
死信队列：qmt:cmd:dlq （失败 3 次后转入，人工处理）

消费流程：

1. BRPOPLPUSH qmt:cmd:queue → qmt:cmd:queue:backup （阻塞等待，超时 5s）
2. 解析命令 JSON
3. 执行 xttrader 操作（带超时，最长 10s）
4. 写回报到 Redis：
    - HSET qmt:order:status:{req_id} {状态 JSON}
    - XADD qmt:event:order_update {回报 JSON}
5. 成功：LREM qmt:cmd:queue:backup 删除备份
6. 失败（含超时）：
    - retry_count < 3：更新 cmd.retry_count += 1，写入 Redis 延迟队列（ZADD qmt:cmd:delayqueue，score = now + retry_count ×
      2s），实现指数退避（2s → 4s → 6s），避免快速失败循环
    - retry_count >= 3：LPUSH qmt:cmd:dlq 转死信，企业微信告警

延迟队列消费：独立线程每秒轮询 ZRANGEBYSCORE qmt:cmd:delayqueue 0 <now>，到期的命令重新 LPUSH qmt:cmd:queue。

#### 3.4.2 命令格式（Redis Queue）

    {
        "req_id": "uuid4",                   // 请求唯一 ID（Alpha 后端生成）
        "cmd": "place_order",                // 命令类型
        "account_id": "666631557962",
        "stock_code": "600519.SH",
        "order_type": "buy",                 // buy / sell
        "order_volume": 100,
        "price_type": "limit",               // limit / market / best5
        "price": 1850.00,
        "strategy_name": "alpha",
        "order_remark": "A   股轮动-买入",
        "retry_count": 0,
        "created_at": 1713000000.0
    }

#### 3.4.3 价格类型映射

| Alpha 传入        | xtquant price_type                           | 说明           |
|-----------------|----------------------------------------------|--------------|
| `limit`         | `xtconstant.FIX_PRICE`（11）                   | 限价委托         |
| `market`        | `xtconstant.MARKET_PEER_PRICE_FIRST`（5档撮合转限） | 市价（最优五档即时成交） |
| `best5`         | `xtconstant.MARKET_BEST_PRICE`（对手方最优）        | 对手方最优价       |
| `cancel_remain` | `xtconstant.MARKET_CANCEL_REMAIN`（剩余撤）       | 五档成交剩余撤单     |

#### 3.4.4 订单状态映射

| xtquant 状态           | 系统规范状态              | 说明     |
|----------------------|---------------------|--------|
| 50 (WAITING)         | PENDING             | 等待报送   |
| 55 (REPORTED)        | SUBMITTED           | 已报送交易所 |
| 56 (REPORTED_CANCEL) | CANCELING           | 正在撤单   |
| 57 (PARTICAL_CANCEL) | PARTIALLY_CANCELLED | 部分撤单   |
| 58 (CANCELED)        | CANCELLED           | 全部撤单   |
| 59 (PARTICAL_DEAL)   | PARTIALLY_FILLED    | 部分成交   |
| 60 (DEAL)            | FILLED              | 全部成交   |
| 61 (CANCEL_FAILED)   | CANCEL_FAILED       | 撤单失败   |
| -1 (FAILED)          | REJECTED            | 委托失败   |

### 3.5 XtQuantTraderCallback 回调系统（核心）

#### 3.5.1 设计原则

              是 xtquant 推送账户实时状态的唯一主动通道，所有事件（委托

`XtQuantTraderCallback`

变化、成交、断线）均在 xtquant 内部线程中回调。系统设计遵循以下原则：
| 原则 | 实现方式 |
|------|---------|
| 回调即更新 | 每个回调方法立即更新内存快照 + 写 Redis，不延迟到轮询周期 |
| 线程安全 | AccountHub 所有内存写操作加 `threading.Lock`；Redis 用同步连接，天然线程安全 |
| 回调不阻塞 | 回调方法内禁止任何同步网络 I/O（HTTP、数据库）；写 Redis 例外（毫秒级）；耗时操作投入线程池 |
| 幂等处理 | 同一 order_id 的回调可能重复到达（xtquant 保证），用 order_id 做去重 |
| 轮询兜底 | 每 10s 全量拉取一次，修正回调漏失场景（网络抖动、重连期间的事件） |

#### 3.5.2 CallbackHandler 完整实现

```python
   # core/callback_handler.py


import time
import logging
import threading
from typing import Optional
from xtquant.xttrader import XtQuantTraderCallback
from xtquant.xttype import XtOrder, XtTrade, XtCancelError, XtOrderError

logger = logging.getLogger(__name__)


class CallbackHandler(XtQuantTraderCallback):
    """
   XtQuantTrader事件回调中枢。
   所有方法均在 xtquant 内部线程中调用，禁止执行任何阻塞 I/O。
   AccountHub 由外部注入，实现解耦。
   """

    def __init__(self, account_hub, session_mgr, redis_bridge, executor)
        """
       account_hub               实例，负责内存状态管理
                      : AccountHub
       session_mgr : SessionMgr 实例，负责重连调度
       redis_bridge : RedisBridge 实例，负责 Redis 写操作
       executor     : ThreadPoolExecutor，用于投递耗时后续任务
       """
        self._hub = account_hub
        self._session = session_mgr
        self._redis = redis_bridge
        self._executor = executor
        self._lock = threading.Lock()  # 保护回调内部共享状态

    # ------------------------------------------------------------------
    # 1.   断线回调
    # ------------------------------------------------------------------

    def on_disconnected(self) -> None:
        """
       触发时机：xttrader 与 MiniQMT 连接断开。
       处理：
        - 标记交易子系统 offline
        - 委托 SessionMgr 异步重连（不在本线程阻塞）
        - 更新 Redis 状态供 Alpha 后端感知
       """

    logger.error("[ERROR] XtQuantTrader   连接断开，触发重连流程")
    self._redis.set_component_status("xttrader", "offline")
    #   投入线程池异步执行重连，避免阻塞回调线程
    self._executor.submit(self._session.handle_disconnected)


# ------------------------------------------------------------------
# 2.   账户状态变化
# ------------------------------------------------------------------


def on_account_status(self, status: XtAccountStatus) -> None:
    """
触发时机：账户登录/登出/状态变化。
XtAccountStatus 字段：
  .account_id   str 账户号
  .account_type str 账户类型（STOCK/CREDIT 等）
  .status       int 状态码（0=正常，其他见 xtconstant）


处理：
 - 更新 Redis 账户连接状态
 - 登录成功后触发一次全量账户数据拉取（冷启动/重连场景）
"""


account_id = status.account_id
account_type = status.account_type
status_code = status.status

logger.info(
    f"[INFO]   账户状态变化 account={account_id} "
    f"type={account_type} status={status_code}"
)

# status=0   表示账户正常在线
is_online = (status_code == 0)
self._redis.set_account_online_status(account_id, is_online)

if is_online:
    # 账户上线：投入线程池全量拉取一次账户数据（重连后补全漏失状态）
    logger.info(f"[INFO] 账户 {account_id} 上线，触发全量数据拉取")
    self._executor.submit(self._hub.full_sync)


# ------------------------------------------------------------------
# 3.   委托回报（状态变化：报送 / 部分成交 / 全成 / 撤单等）
# ------------------------------------------------------------------


def on_stock_order(self, order: XtOrder) -> None:
    """
触发时机：每次委托状态发生变化（SUBMITTED / PARTIALLY_FILLED /
      FILLED / CANCELLED / CANCEL_FAILED 等）
                                          。
注意：同一笔委托的多次状态变化会多次回调（如 SUBMITTED → PARTIALLY_FILLE
XtOrder   关键字段：
.order_id            委托编号（xtquant 内部）
                  int
.order_sysid   str   交易所委托编号
.account_id    str   账户号
.stock_code    str   股票代码（如 600519.SH）
.order_type    int   买卖方向（23=买, 24=卖）
.order_volume int    委托数量
.price         float 委托价格
.traded_volume int   累计成交数量
.traded_price float 成交均价
.order_status int    当前状态码（见 3.4.4 映射表）
.status_msg    str   状态说明文字
.order_time    str   委托时间（"09:30:00"）
.strategy_name str   策略名
.order_remark str    委托备注（用于关联 req_id）
"""


try:
    normalized_status = self._map_order_status(order.order_status
    req_id = self._parse_req_id(order.order_remark)

    event = {
        "event_type": "order_update",
        "req_id": req_id,
        "order_id": str(order.order_id),
        "order_sysid": order.order_sysid or "",
        "account_id": order.account_id,
        "stock_code": order.stock_code,
        "order_type": "buy" if order.order_type == 23 else "s
                                                           "order_volume":    order.order_volume,
    "traded_volume": order.traded_volume,
    "price": order.price,
    "traded_price": order.traded_price,
    "status": normalized_status,
    "status_msg": order.status_msg or "",
    "order_time": order.order_time or "",
    "strategy_name": order.strategy_name or "",
    "timestamp": time.time(),
    }


    # 1.   更新内存委托快照（加锁）
    self._hub.apply_order_event(event)

    # 2.   更新单笔委托状态 Redis Key（供 Alpha 直接查询）
    self._redis.set_order_status(req_id or str(order.order_id), e

    # 3.   发布到回报 Stream（Alpha QmtOrderWorker 消费）
    self._redis.publish_order_event(event)

    logger.info(
        f"[INFO]   委托回报 order_id={order.order_id} "
        f"stock={order.stock_code} status={normalized_status} "
        f"traded={order.traded_volume}/{order.order_volume}"
    )


    except Exception:
    logger.error("[ERROR] on_stock_order   处理异常", exc_info=True)


# ------------------------------------------------------------------
# 4.   成交回报（每笔实际成交推送一次）
# ------------------------------------------------------------------


def on_stock_trade(self, trade: XtTrade) -> None:
    """
触发时机：每发生一笔实际成交（大单可能拆成多笔推送）。
与 on_stock_order 的区别：
 - on_stock_order 推送委托状态变化（粗粒度）
 - on_stock_trade 推送每笔实际成交明细（细粒度，含成交价/量/金额）


XtTrade       关键字段：
     .order_id      int   对应委托编号
     .account_id    str   账户号
     .stock_code    str   股票代码
     .order_type    int   买卖方向
     .traded_volume int   本笔成交数量
     .traded_price float 本笔成交价格
     .traded_amount float 本笔成交金额（traded_volume × traded_price）
     .traded_id     str   成交编号（唯一）
     .traded_time   str   成交时间（"09:30:01"）
     .strategy_name str   策略名
     .order_remark str    委托备注（用于关联 req_id）
"""
    try:
        req_id = self._parse_req_id(trade.order_remark)

        event = {
            "event_type": "trade",
            "req_id": req_id,
            "order_id": str(trade.order_id),
            "traded_id": trade.traded_id or "",
            "account_id": trade.account_id,
            "stock_code": trade.stock_code,
            "order_type": "buy" if trade.order_type == 23 else "s
                                                               "traded_volume":   trade.traded_volume,
        "traded_price": trade.traded_price,
        "traded_amount": trade.traded_amount,
        "traded_time": trade.traded_time or "",
        "strategy_name": trade.strategy_name or "",
        "timestamp": time.time(),
        }


        # 1.   更新内存成交列表（去重：traded_id 唯一）
        self._hub.apply_trade_event(event)

        # 2.   发布到回报 Stream
        self._redis.publish_order_event(event)

        # 3.   成交后账户持仓和资金发生变化，投入线程池延迟 200ms 后拉取最新快照
        #      （xttrader 成交后内部状态有短暂刷新窗口）
        self._executor.submit(self._hub.refresh_asset_and_positions,

                              logger.info(
                                  f"[INFO]   成交回报 order_id={trade.order_id} "
                                  f"traded_id={trade.traded_id} stock={trade.stock_code} "
                                  f"volume={trade.traded_volume} price={trade.traded_price}
                              )


    except Exception:
        logger.error("[ERROR] on_stock_trade   处理异常", exc_info=True)


# ------------------------------------------------------------------
# 5.   下单失败回报
# ------------------------------------------------------------------


def on_order_error(self, order_error: XtOrderError) -> None:
    """
触发时机：委托被交易所/柜台拒绝（下单失败，区别于撤单失败）。
XtOrderError   关键字段：
     .order_id  int 委托编号
     .account_id   str 账户号
     .stock_code str 股票代码
     .error_id    int 错误码
     .error_msg   str 错误说明
     .order_remark str 委托备注（用于关联 req_id）
"""
    try:
        req_id = self._parse_req_id(order_error.order_remark)

        event = {
            "event_type": "order_error",
            "req_id": req_id,
            "order_id": str(order_error.order_id),
            "account_id": order_error.account_id,
            "stock_code": order_error.stock_code,
            "status": "REJECTED",
            "error_id": order_error.error_id,
            "error_msg": order_error.error_msg or "",
            "timestamp": time.time(),
        }

        # 1.   更新内存委托快照
        self._hub.apply_order_error(event)

        # 2.   更新单笔委托状态 Redis Key
        self._redis.set_order_status(req_id or str(order_error.order_

        # 3.   发布到回报 Stream（Alpha 侧触发通知）
        self._redis.publish_order_event(event)

        logger.error(
            f"[ERROR]     下单失败 order_id={order_error.order_id} "
            f"stock={order_error.stock_code} "
            f"error_id={order_error.error_id} msg={order_error.error_
            )

except Exception:
logger.error("[ERROR] on_order_error   处理异常", exc_info=True)


# ------------------------------------------------------------------
# 6.   撤单失败回报
# ------------------------------------------------------------------
def on_cancel_error(self, cancel_error: XtCancelError) -> None:
    """
    触发时机：发起撤单但交易所/柜台拒绝撤单（委托状态变为 CANCEL_FAILED）。
    XtCancelError关键字段：
     .order_id   int 委托编号
     .account_id str 账户号
     .error_id   int 错误码
     .error_msg str 错误说明
    """
    try:
        # 从内存委托快照中查找原始 order_remark 以提取 req_id
        order_id = str(cancel_error.order_id)
        req_id = self._hub.get_req_id_by_order_id(
            cancel_error.account_id, order_id
        )
        event = {
            "event_type": "cancel_error",
            "req_id": req_id,
            "order_id": order_id,
            "account_id": cancel_error.account_id,
            "status": "CANCEL_FAILED",
            "error_id": cancel_error.error_id,
            "error_msg": cancel_error.error_msg or "",
            "timestamp": time.time(),
        }

        #   更新委托状态为 CANCEL_FAILED
        self._hub.apply_cancel_error(event)
        self._redis.publish_order_event(event)

        logger.error(
            f"[ERROR]   撤单失败 order_id={cancel_error.order_id} "
            f"error_id={cancel_error.error_id} msg={cancel_error.erro
            )


    except Exception:
        logger.error("[ERROR] on_cancel_error    处理异常", exc_info=True
                                                                     # ------------------------------------------------------------------
                                                                     #   辅助方法
                                                                     # ------------------------------------------------------------------

                                                                     @ staticmethod


def _map_order_status(xt_status: int) -> str:
    """   将 xtquant 状态码映射到系统规范状态字符串"""
    _MAP = {
        50: "PENDING",
        55: "SUBMITTED",
        56: "CANCELING",
        57: "PARTIALLY_CANCELLED",
        58: "CANCELLED",
        59: "PARTIALLY_FILLED",
        60: "FILLED",
        61: "CANCEL_FAILED",
        -1: "REJECTED",
    }
    return _MAP.get(xt_status, f"UNKNOWN_{xt_status}")


@staticmethod


def _parse_req_id(order_remark: Optional[str]) -> Optional[str]:
    """
    从 order_remark 中提取 req_id。
    约定：下单时 order_remark 格式为 "alpha:{uuid4}"
    严格 UUID 校验，防止误解析非 Alpha 来源的 remark。
    """
    if not order_remark:
        return None
    candidate = None
    if order_remark.startswith("alpha:"):
        candidate = order_remark[6:]
    elif len(order_remark) == 36 and order_remark.count("-") == 4:
        candidate = order_remark  # 兼容旧格式
    if candidate:
        try:
            uuid.UUID(candidate, version=4)
            return candidate
        except ValueError:
            pass
    return None
```

#### 3.5.3 回调事件在系统中的完整流向

xtquant 内部线程
│
├─ on_account_status(status)
│ └─► set_account_online_status(Redis)
│ 若 online → executor.submit(AccountHub.full_sync)
│
├─ on_stock_order(order)
│ └─► AccountHub.apply_order_event(event)            内存，加锁]
[
│ RedisBridge.set_order_status(req_id, event) [qmt:order:status:{i
│ RedisBridge.publish_order_event(event)         [qmt:event:order_up
│ └─► Alpha QmtOrderWorker 消费
│ ├─ UPDATE qmt_orders SET status=... WHERE req_id=...
│ └─ 企业微信/iMessage 通知（FILLED/REJECTED 时）
│
├─ on_stock_trade(trade)
│ └─► AccountHub.apply_trade_event(event)            [内存，加锁，traded_id
│ RedisBridge.publish_order_event(event)          [qmt:event:order_up
│ executor.submit(AccountHub.refresh_asset_and_positions, delay=20
│ └─► xttrader.query_stock_asset_async()
│ xttrader.query_stock_positions_async()
│ → 更新内存快照 + Redis（pipeline 原子写）
│
├─ on_order_error(order_error)
│ └─► AccountHub.apply_order_error(event)            [内存，加锁]
│ RedisBridge.set_order_status(req_id, event)
│ RedisBridge.publish_order_event(event)
│
├─ on_cancel_error(cancel_error)
│ └─► AccountHub.apply_cancel_error(event)           [内存，加锁]
│ RedisBridge.publish_order_event(event)
│
└─ on_disconnected()
└─► RedisBridge.set_component_status("xttrader", "offline")
executor.submit(SessionMgr.handle_disconnected)
└─► 指数退避重连（1s→2s→4s→...→60s 上限）
重连成功后 AccountHub.full_sync()

#### 3.5.4 req_id 关联机制

系统通过 `order_remark` 字段在 xtquant 委托与 Alpha 订单记录之间建立关联：
下单时（CmdConsumer）：
xttrader.order_stock_async(
order_remark=f"alpha:{req_id}"     # 格式固定，便于解析
)

回调时（CallbackHandler）：
req_id = _parse_req_id(order.order_remark)
→ 写 Redis：qmt:order:status:{req_id}
→ 写 Stream event 携带 req_id

Alpha QmtOrderWorker 消费时：
→ UPDATE qmt_orders SET status=... WHERE req_id=req_id

xtquant 保证 `order_remark` 字段原样回传（最长 128 字符），`alpha:` 前缀 + UUID4 共
43 字符，不超限。

> **注意**：`order_remark` 具有双重用途：① 作为 QMT 客户端委托列表中的备注显示；②
> 携带 `alpha:{uuid4}` 格式的 req_id 用于回调关联。由于格式固定为 `alpha:` 前缀，
> 在 QMT 客户端中会显示为 `alpha:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`。若需更友
> 好的展示名称，可在 `alpha:` 后附加策略标识（如 `alpha:策略A:{uuid4}`），但需同步
> 更新 `_parse_req_id` 中的解析逻辑。当前设计选择简洁性，仅使用 `alpha:{uuid4}`。

### 3.6 账户模块（事件驱动 + 轮询兜底）

#### 3.6.1 架构：事件驱动优先，轮询兜底

原设计依赖纯轮询（3~5s 周期），存在明显状态滞后。新设计改为回调驱动为主、轮询为
辅：
| 触发方式 | 场景 | 延迟 |
|---------|------|------|
| `on_stock_order` 回调 | 委托状态变化 | < 100ms |
| `on_stock_trade` 回调 | 成交发生 | < 100ms |
| 成交后延迟 200ms 拉取 | 持仓/资金更新 | ~300ms |
| 全量轮询（10s） | 兜底，修正漏失事件 | 最差 10s |

#### 3.6.2 AccountHub 内存结构

```python
    class AccountHub:
    def __init__(self):
        self._lock = threading.Lock()

        #   账户维度快照（支持多账户扩展）
        self._asset: dict = {}  # account_id → asset dict
        self._positions: dict = {}  # account_id → {stock_code: position
        self._orders: dict = {}  # account_id → {order_id: order dict
        self._trades: dict = {}  # account_id → [trade dict]   （按时间序
        self._account_status: dict = {}  # account_id → "online"/"offline

        #   元信息
        self._last_full_sync_at: float = 0.0
        self._last_asset_sync_at: float = 0.0

    # ---    事件入口（由 CallbackHandler 调用，xtquant 线程） ---
    def apply_order_event(self, event: dict) -> None:
        """更新内存委托快照，同时刷新 Redis 委托列表"""
        with self._lock:
            account_id = event["account_id"]
            order_id = event["order_id"]
            if account_id not in self._orders:
                self._orders[account_id] = {}
            self._orders[account_id][order_id] = event
            # 锁内拷贝，避免锁外写 Redis 时数据被其他线程修改
            snapshot = dict(self._orders[account_id])
        self._redis.flush_orders(account_id, snapshot)

    def apply_trade_event(self, event: dict) -> None:
        """追加成交记录（traded_id 去重），同时刷新 Redis 成交列表"""
        with self._lock:
            account_id = event["account_id"]
            traded_id = event["traded_id"]
            if account_id not in self._trades:
                self._trades[account_id] = {}
            if traded_id not in self._trades[account_id]:  # 去重
                self._trades[account_id][traded_id] = event
            snapshot = dict(self._trades[account_id])
        self._redis.flush_trades(account_id, snapshot)


def apply_order_error(self, event: dict) -> None:
    """   委托失败：同 apply_order_event"""
    self.apply_order_event(event)


def apply_cancel_error(self, event: dict) -> None:
    """   撤单失败：同 apply_order_event"""
    self.apply_order_event(event)


def apply_account_status(self, account_id: str, is_online: bool) -> N
    with self._lock:
        self._account_status[account_id] = "online" if is_online else


# ---   主动拉取（由线程池调用，非 xtquant 线程） ---
def refresh_asset_and_positions(self, delay_ms: int = 0) -> None:
    """成交后触发：延迟 delay_ms 毫秒再拉取资金和持仓。
    去重机制：若上一次 refresh 尚未完成或距今不足 500ms，
    则跳过本次，避免 on_stock_trade 连续回调导致大量并发刷新。
    """
    now = time.time()
    with self._lock:
        if self._refreshing:
            logger.debug("refresh_asset_and_positions skipped: previous refresh in progress")
            return
        if now - self._last_asset_sync_at < 0.5:
            logger.debug("refresh_asset_and_positions skipped: too frequent")
            return
        self._refreshing = True

    try:
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
        asset = self._trader.query_stock_asset_async(self._account)
        positions = self._trader.query_stock_positions_async(self._account)
        with self._lock:
            self._asset[self._account.account_id] = _normalize_asset(asset)
            self._positions[self._account.account_id] = _normalize_positions(positions)
            self._last_asset_sync_at = time.time()
        # 原子写 Redis（Lua 脚本保证 asset + positions 同时更新）
        self._redis.flush_account_snapshot(
            self._asset[self._account.account_id],
            list(self._positions[self._account.account_id].values())
        )
    finally:
        with self._lock:
            self._refreshing = False


def full_sync(self) -> None:
    """全量拉取：资金 + 持仓 + 委托 + 成交（重连 / 定时兜底调用）。
    四个查询通过线程池并行执行，降低总耗时。
    """
    account_id = self._account.account_id
    # 并行查询：四个 xtquant 异步调用通过 ThreadPool 并行
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_asset = pool.submit(self._trader.query_stock_asset_async, self._account)
        future_positions = pool.submit(self._trader.query_stock_positions_async, self._account)
        future_orders = pool.submit(self._trader.query_stock_orders_async, self._account)
        future_trades = pool.submit(self._trader.query_stock_trades_async, self._account)
        asset = future_asset.result(timeout=10)
        positions = future_positions.result(timeout=10)
        orders = future_orders.result(timeout=10)
        trades = future_trades.result(timeout=10)

    with self._lock:
        self._asset[account_id] = _normalize_asset(asset)
        self._positions[account_id] = {p.stock_code: _normalize_position(p)
                                       for p in (positions or [])}
        self._orders[account_id] = {str(o.order_id): _normalize_order(o)
                                    for o in (orders or [])}
        self._trades[account_id] = {t.traded_id: _normalize_trade(t)
                                    for t in (trades or [])}
        self._last_full_sync_at = time.time()

    #   原子写 Redis
    self._redis.flush_full_account(
        account_id,
        self._asset[account_id],
        list(self._positions[account_id].values()),
        list(self._orders[account_id].values()),
        list(self._trades[account_id].values()),
    )


logger.info(f"[OK] AccountHub     全量同步完成 account={account_id}")


# ---   只读查询（HTTP 线程调用，加锁读内存，不走网络） ---
def get_asset(self, account_id: str) -> dict:
    with self._lock:
        return dict(self._asset.get(account_id, {}))


def get_positions(self, account_id: str) -> list:
    with self._lock:
        return list(self._positions.get(account_id, {}).values())


def get_orders(self, account_id: str, cancelable_only: bool = False)
    with self._lock:
        orders = [dict(o) for o in self._orders.get(account_id, {}).values()]
        if cancelable_only:
            cancelable = {"SUBMITTED", "PARTIALLY_FILLED", "PENDING"}
            orders = [o for o in orders if o.get("status") in cancelable]
        return orders


def get_trades(self, account_id: str) -> list:
    with self._lock:
        return list(self._trades.get(account_id, {}).values())
```

#### 3.6.3 定时兜底轮询

# 在 AccountHub 启动时在独立线程中运行

```python
   def _poll_loop(self):
    """   每 10s 全量拉取一次，修正回调漏失的状态"""
    while self._running:
        time.sleep(10)
        try:
            self.full_sync()
        except Exception:
            logger.error("[ERROR] AccountHub   轮询同步失败", exc_info=True)


轮询间隔设定为
10
s
的理由：
正常情况下回调已覆盖全部事件，轮询仅作兜底
轮询间隔过短会增加
xttrader
查询压力，影响主链路性能
A
股
T + 0
日内持仓变化通过成交回调实时更新，10
s
的轮询延迟不影响关键路径
```

#### 3.6.4 账户数据格式

资金（`qmt:account:asset`）

{
"account_id": "666631557962",
"total_asset":              1500000.00,
"cash":                     150000.00,
"frozen_cash":              0.00,
"market_value":             1350000.00,
"profit_loss":              25000.00,
"profit_loss_ratio":        0.0167,
"updated_at":               1713000000.0,
"updated_by":               "trade_callback"     // "trade_callback" | "full_sy
}

持仓（`qmt:account:positions`）

[
{
"account_id":              "666631557962",
"stock_code":              "600519.SH",
"stock_name":              "贵州茅台",
"volume":                  1000,
"can_use_volume":          1000,
"avg_price":               1800.00,
"market_value":            1855000.00,
"profit_loss":             55000.00,
"profit_loss_ratio":       0.0306,
"open_price":              1798.00,
"updated_at":              1713000000.0
}
]

委托列表（`qmt:account:orders`）

[
{
"order_id":         "123456789",
"order_sysid":      "A2026041300001",
"req_id":           "550e8400-e29b-41d4-a716-446655440000",
"account_id":       "666631557962",
"stock_code":       "600519.SH",
"stock_name":       "贵州茅台",
"order_type":       "buy",
"order_volume":     100,
"traded_volume":    100,
"price":            1855.00,
"traded_price":     1854.50,
"status":           "FILLED",
"status_msg":       "全部成交",
"order_time":        "09:30:00",
"strategy_name":     "alpha",
"updated_at":        1713000001.0
}
]

成交明细（`qmt:account:trades`）

[
{
"traded_id":         "T2026041300001",
"order_id":          "123456789",
"req_id":            "550e8400-e29b-41d4-a716-446655440000",
"account_id":        "666631557962",
"stock_code":        "600519.SH",
"stock_name":        "贵州茅台",
"order_type":        "buy",
"traded_volume":     100,
"traded_price":      1854.50,
"traded_amount":     185450.00,
"traded_time":       "09:30:01",
"strategy_name":     "alpha"
}
]

### 3.7 HTTP API 层

两个独立进程各自暴露独立的 HTTP API，端口不同、职责不重叠。

#### 3.7.1 全局规范

服务 Base URL 说明
qmt-market               `http://192.168.3.10:8091`                       行情服务
qmt-trade                `http://192.168.3.10:8090`                       交易服务

    认证：API Key（Header：`X-Api-Key`），配置在 `config.secret.json`（两个服务共
    享同一密钥）
      响应格式：

      {
          "code": 0,             成功，非0=错误
                             // 0=
          "msg": "ok",       // 错误时为错误描述
          "data": {}         // 业务数据
      }


      错误码：

| code | 说明                 |
|------|--------------------|
| 0    | 成功                 |
| 1001 | 参数错误               |
| 1002 | 行情未连接（qmt-market）  |
| 1003 | 交易未连接（qmt-trade）   |
| 1004 | 账户未登录（qmt-trade）   |
| 1005 | ~~已废弃（v1.3 移除订阅管理）~~ |
| 2001 | 风控拒绝               |
| 2002 | 委托失败               |
| 5000 | 内部错误               |

#### 3.7.2 行情服务接口表（qmt-market :3301）

**健康检查**

| 方法  | 路径        | 说明                                |
|-----|-----------|-----------------------------------|
| GET | `/health` | 行情服务健康状态（xtdata 连接 + Redis 连通性） |

**行情查询**

| 方法   | 路径                       | 说明                   |
|------|--------------------------|----------------------|
| GET  | `/quote/tick`            | 批量查询最新 Tick（最多 50 只） |
| GET  | `/quote/tick/{code}`     | 查询单只股票最新 Tick        |
| GET  | `/quote/kline`           | 查询 K 线数据             |
| GET  | `/quote/detail/{code}`   | 查询股票基本信息（名称、涨停价等）    |
| GET  | `/quote/sector/{sector}` | 查询板块成分股列表            |
| GET  | `/quote/full_tick`       | 查询完整 Tick（含逐笔）       |
| POST | `/quote/history`         | 查询历史行情（支持批量下载触发）     |

**控制**

| 方法   | 路径                   | 说明         |
|------|----------------------|------------|
| POST | `/control/reconnect` | 重连 xtdata  |
| GET  | `/control/config`    | 查看当前配置（脱敏） |

#### 3.7.3 交易服务接口表（qmt-trade :8090）

**健康检查**

| 方法  | 路径        | 说明                                         |
|-----|-----------|--------------------------------------------|
| GET | `/health` | 交易服务健康状态（xttrader 连接 + 账户状态 + CmdConsumer） |

**账户查询**

| 方法  | 路径                   | 说明             |
|-----|----------------------|----------------|
| GET | `/account/asset`     | 查询资金           |
| GET | `/account/positions` | 查询持仓           |
| GET | `/account/orders`    | 查询当日委托（可选：仅可撤） |
| GET | `/account/trades`    | 查询当日成交         |

**交易操作**

> 注意：HTTP 交易接口同样通过 Redis 命令队列投递（写入 `qmt:cmd:queue`），保持与 Alpha 后端一致的可
> 靠链路（BRPOPLPUSH + 重试 + 死信队列）。HTTP 接口不直接调用 xttrader，确保 kill_switch 熔断检查
> 和命令追踪的一致性。

| 方法   | 路径                      | 说明                |
|------|-------------------------|-------------------|
| POST | `/trade/order`          | 下单（限价/市价），写入命令队列  |
| POST | `/trade/cancel`         | 撤单，写入命令队列         |
| POST | `/trade/cancel_all`     | 撤销全部可撤委托          |
| GET  | `/trade/order/{req_id}` | 查询委托状态（通过 req_id） |

**控制**

| 方法   | 路径                   | 说明          |
|------|----------------------|-------------|
| POST | `/control/reconnect` | 重连 xttrader |
| GET  | `/control/config`    | 查看当前配置（脱敏）  |

### 3.8 Redis 桥接层（RedisBridge）

统一封装所有 Redis 写操作，解耦 xtquant 与 Redis 的直接依赖：

```python
  class RedisBridge:
    """   所有对 Redis 的写操作集中于此，便于测试 mock 和监控"""

    def publish_order_event(self, event: dict) -> None:
        """   写入订单回报事件"""

    def update_account_snapshot(self, asset: dict, positions: list) -> No
        """   原子更新账户快照（使用 Pipeline）"""

    def consume_cmd(self, timeout: int = 5) -> Optional[dict]:
        """BRPOPLPUSH   弹出命令"""

    def ack_cmd(self, cmd: dict) -> None:
        """   命令处理成功，从备份队列删除"""

    def nack_cmd(self, cmd: dict, reason: str) -> None:
        """   命令处理失败，重入队列或转死信"""

    def publish_status(self, status: dict, service: str) -> None:
        """
        主动状态上报（service = 'market' | 'trade'）：
```

## 1. SET qmt:{service}:status {json} EX 35（离线后 TTL 自动过期

## 2. PUBLISH qmt:status:notify {json} （两个服务共享同一通知

## 3. 若有新告警：LPUSH qmt:status:alerts / LTRIM 保留最近 200 条

           """

```python
        def set_component_status(self, component: str, status: str) -> None:
    """   更新单个子系统状态（on_disconnected 等紧急场景直接调用）"""
```

### 3.8.1 优雅关机（Graceful Shutdown）

两个服务均需实现优雅关机流程，确保数据不丢失、状态不残留：

```
收到 SIGTERM / SIGINT（NSSM stop 或 Ctrl+C）
  │
  ├─ 1. 停止接受新 HTTP 请求（FastAPI shutdown 事件）
  ├─ 2. 设置 self._running = False（通知所有后台线程退出）
  │
  ├─ 【qmt-trade 特有】
  │   ├─ 3. 等待 CmdConsumer 完成当前命令（join timeout=15s）
  │   ├─ 4. 检查 qmt:cmd:queue:backup 是否有未确认的命令
  │   │      若有 → 重新 LPUSH 回 qmt:cmd:queue（确保下次启动可消费）
  │   └─ 5. 停止延迟队列轮询
  │
  ├─ 6. 等待 AccountHub / MarketHub 后台线程退出（join timeout=5s）
  ├─ 7. 等待 StatusReporter 当前上报完成（join timeout=5s）
  ├─ 8. SET qmt:{service}:status {"overall_status": "shutting_down"}（TTL=10s）
  ├─ 9. 断开 xtquant 连接（xttrader.stop() / xtdata.unsubscribe_all()）
  └─ 10. 关闭 Redis 连接池
```

关机超时保护：总关机时间上限 30s，超时后强制退出（NSSM 配置 `AppExit` 为 `Restart`）。

冷启动对账：

- qmt-trade 启动时检查 `qmt:cmd:queue:backup` 是否有遗留命令
- 若有 → 重新 LPUSH 到 `qmt:cmd:queue`，确保命令不丢失

### 3.10 StatusReporter（主动状态上报）

两个服务（qmt-market / qmt-trade）各自包含一个独立的 StatusReporter 实例，独立采集
各自进程的健康状态，共享同一 Redis 通知频道。

#### 3.10.1 职责与设计目标

StatusReporter 是各服务的"自述"模块，每 10 秒主动采集并广播一次系统全状态，确保：
各进程的子系统健康状态可独立观测
异常发生后 10s 内 Alpha 后端和前端均能感知
进程宕机后 35s 内（TTL 超时）Alpha 后端自动判断对应服务离线

#### 3.10.2 状态采集指标

qmt-market StatusReporter（写入 `qmt:market:status`）
| 类别 | 指标 | 说明 |
|------|------|------|
| 行情（xtdata） | `connected` | xtdata 是否连接 |
| | `last_tick_at` | 最后一次收到 Tick 的时间戳 |
| | `last_tick_delay_s` | 距今多少秒（> 60s 视为行情中断） |
| | `subscribed_count` | 当前订阅只数 |
| | `status` | `healthy` / `degraded` / `offline` |

Redis            `connected`           Redis 是否可达
`latency_ms`          PING 往返延迟
类别 指标 说明
`status`                       / /
`healthy` `degraded` `offline`

整体             `source`              固定为 `"market"`（区分通知频道消息来源）
`overall_status`      所有组件取最差等级
`uptime`              进程运行时长（秒）
`alerts`              当前活跃告警列表

qmt-trade StatusReporter（写入 `qmt:trade:status`）
类别 指标 说明
交易（xttrader）    `connected`          xttrader 是否连接
`account_status`     账户在线状态
`last_order_at`      最后一次委托时间戳（无委托时为 null）
`status`             `healthy` / `offline`

Redis           `connected`          Redis 是否可达
`latency_ms`         PING 往返延迟
`cmd_queue_depth`    `qmt:cmd:queue` 积压数

                `dlq_depth`          `qmt:cmd:dlq` 死信数（> 0 必告警）


                `status`             `healthy` / `degraded` / `offline`

CmdConsumer     `running`            消费线程是否存活
`last_consumed_at`   最后一次消费命令时间戳
`processed_today`    今日已处理命令数
`status`             `healthy` / `offline`

整体              `source`             固定为 `"trade"`（区分通知频道消息来源）
`overall_status`     所有组件取最差等级
`uptime`             进程运行时长（秒）
类别 指标 说明
`alerts`                   当前活跃告警列表

#### 3.10.3 StatusReporter 完整实现

```python
     # core/status_reporter.py


import time
import json
import logging
import threading
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

_BEIJING_TZ = pytz.timezone('Asia/Shanghai')

#   各子系统降级判定阈值
_TICK_STALE_SECONDS = 60  # 行情超过 60s 无推送 → degraded
_TICK_OFFLINE_SECONDS = 120  # 超过 120s → offline
_REDIS_LATENCY_WARN_MS = 100  # Redis 延迟超过 100ms → degraded
_STATUS_TTL_SECONDS = 35  # Redis 状态键 TTL（比上报周期多 5s 冗余）
_ALERT_HISTORY_MAX = 200  # 告警历史最多保留条数


class StatusReporter:
    """
    每 REPORT_INTERVAL 秒采集一次本进程的系统状态：
    - 写入 Redis qmt:{service}:status（TTL 自动过期，离线感知）
    - PUBLISH qmt:status:notify（两个服务共享频道，payload 含 source 字段区分）
    - 新告警写入 qmt:status:alerts（持久化历史）


    service: 'market'      （qmt-market 进程）或 'trade'（qmt-trade 进程）
    """

    REPORT_INTERVAL = 10  # 秒

    def __init__(self, service: str, redis_bridge, notifier, config, **co
        """
        service           或 'trade'，决定写入哪个 Redis 键
                   : 'market'
        components : 各进程自己的核心组件引用（market_hub / account_hub 等）
                                                            ，
          用于采集指标，各服务只传自己有的组件
"""

    self._service = service
    self._redis = redis_bridge
    self._notifier = notifier  # 企业微信/iMessage 通知接口
    self._config = config
    self._components = components  # 灵活接收各进程组件
    self._start_time = time.time()
    self._running = False
    self._thread = None

    #   告警去重：记录上一次上报的告警集合，避免重复发企业微信
    self._active_alert_keys: set = set()


def start(self) -> None:
    self._running = True
    self._thread = threading.Thread(
        target=self._loop, name="StatusReporter", daemon=True
    )
    self._thread.start()
    logger.info("[OK] StatusReporter   已启动，上报周期 %ds", self.REPORT


def stop(self) -> None:
    self._running = False


def _loop(self) -> None:
    while self._running:
        try:
            self._report_once()
        except Exception:
            logger.error("[ERROR] StatusReporter   上报失败", exc_info=T
            time.sleep(self.REPORT_INTERVAL)


def _report_once(self) -> None:
    """   采集状态 → 写 Redis → Pub/Sub 广播 → 处理告警"""
    status = self._collect()
    payload = json.dumps(status, ensure_ascii=False)

    # 1.   写状态快照（TTL 自动续期，key 由 service 决定）
    self._redis.publish_status(status, service=self._service)

    # 2.   处理本次新增告警
    self._handle_alerts(status.get("alerts", []))
    logger.debug(
        "[INFO]   状态上报完成 overall=%s alerts=%d",
        status["overall_status"], len(status["alerts"])
    )


def _collect(self) -> dict:
    """采集全量状态，返回标准结构。通过 collect_fn 回调采集各服务特有指标，避免跨服务访问属性。"""
    now = time.time()
    alerts = []

    # 通过注入的采集函数收集各服务特有指标
    # market 服务注入 collect_fn → 收集 xtdata 状态
    # trade 服务注入 collect_fn → 收集 xttrader / CmdConsumer / Account 状态
    components_status = {}
    if hasattr(self, '_collect_fn') and self._collect_fn:
        components_status, alerts = self._collect_fn(now)


if not trader_connected:
    xttrader_status = "offline"
    alerts.append(self._make_alert("xttrader_offline", "error",
                                   "交易连接断开（xttrader not conn
    elif account_online != "online":
    xttrader_status = "degraded"
    alerts.append(self._make_alert("account_offline", "warning",
                                   f"   账户状态异常：{account_online}
    else:
    xttrader_status = "healthy"

    xttrader_info = {
        "connected": trader_connected,
        "account_id": self._config.qmt.account_id,
        "account_status": account_online,
        "last_order_at": self._account.last_order_timestamp,
        "status": xttrader_status,
    }

    # ---- Redis   状态 ----
    redis_latency_ms = self._redis.ping_latency_ms()  # 执行 PING 计时
    cmd_queue_depth = self._redis.llen("qmt:cmd:queue")
    dlq_depth = self._redis.llen("qmt:cmd:dlq")

    if redis_latency_ms is None:
        redis_status = "offline"
    alerts.append(self._make_alert("redis_offline", "error",
                                   "Redis   连接失败"))
    elif redis_latency_ms > _REDIS_LATENCY_WARN_MS:
    redis_status = "degraded"
    alerts.append(self._make_alert("redis_slow", "warning",
                                   f"Redis   延迟过高：{redis_latency_ms:.1f}ms"))
    else:
    redis_status = "healthy"

    if dlq_depth > 0:
        alerts.append(self._make_alert("dlq_has_items", "error",
                                       f"   死信队列积压 {dlq_depth} 条，需人工处理"))
    redis_info = {
        "connected": redis_latency_ms is not None,
        "latency_ms": redis_latency_ms,
        "cmd_queue_depth": cmd_queue_depth,
        "dlq_depth": dlq_depth,
        "status": redis_status,
    }

    # ---- CmdConsumer    状态 ----
    consumer_alive = self._consumer.is_alive()
    last_consumed_at = self._consumer.last_consumed_timestamp
    processed_today = self._consumer.processed_count_today

    if not consumer_alive:
        consumer_status = "offline"
    alerts.append(self._make_alert("consumer_dead", "error",
                                   "CmdConsumer   线程已停止"))
    else:
    consumer_status = "healthy"

    consumer_info = {
        "running": consumer_alive,
        "last_consumed_at": last_consumed_at,
        "processed_today": processed_today,
        "status": consumer_status,
    }

    # ----   整体状态（取最差等级）----
    component_statuses = [
        xtdata_status, xttrader_status, redis_status, consumer_status
    ]
    if "offline" in component_statuses:
        overall = "offline"
    elif "degraded" in component_statuses:
        overall = "degraded"
    else:
        overall = "healthy"

return {
    "version": "1.0.0",
    "overall_status": overall,
    "server_time": datetime.now(_BEIJING_TZ).strftime("%Y-%m-%
                                                      "timestamp":         now,
"uptime": int(now - self._start_time),
"components": {
    "xtdata": xtdata_info,
    "xttrader": xttrader_info,
    "redis": redis_info,
    "cmd_consumer": consumer_info,
},
"alerts": alerts,
}

def _make_alert(self, key: str, level: str, msg: str) -> dict:
    """   构造告警记录"""
    return {
        "key": key,  # 用于去重
        "level": level,  # error / warning
        "msg": msg,
        "timestamp": time.time(),
        "server_time": datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d %
    }


def _handle_alerts(self, alerts: list) -> None:
    """
    与上次活跃告警对比：
    - 新出现的告警：写历史 + 发企业微信
    - 已消除的告警：发企业微信"已恢复"通知
    """
    current_keys = {a["key"] for a in alerts}

    #   新增告警
    for alert in alerts:
        if alert["key"] not in self._active_alert_keys:
            self._redis.push_alert_history(alert)
            #   仅 error 级别实时推企业微信，warning 仅记录
            if alert["level"] == "error":
                self._notifier.send_qmt_alert(
                    f"[QMT   告警] {alert['msg']}\n时间：{alert['server_t
                   )
              logger.warning("[ALERT] %s: %s", alert["level"].upper(),


   #   告警消除
   recovered =
                self._active_alert_keys - current_keys
                for key in recovered:
                    self._notifier.send_qmt_alert(f"[QMT   恢复] {key} 已恢复正常")
                logger.info("[OK]      告警已消除: %s", key)
                self._active_alert_keys = current_keys

    def get_latest_status(self) -> dict:
        """HTTP /health   接口直接调用，从内存返回最新状态（无网络开销）"""
        key = f"qmt:{self._service}:status"
        return self._redis.get_json(key) or {"overall_status": "unknown"}
```

#### 3.10.4 上报与推送机制

StatusReporter._report_once()    （各进程独立运行）
│
├─ SET qmt:{service}:status {full_json} EX 35
│ └─► Alpha 后端轮询兜底（每 15s 分别读两个键，与上次对比）
│
├─ PUBLISH qmt:status:notify {full_json}
│ │ 包含 "source": "market" 或 "source": "trade"
payload
│ └─► Alpha QmtStatusWorker（订阅此共享 channel）
│ ├─ 按 source 字段分别更新 market/trade 状态
│ ├─ 有变化时：通过 WebSocket 广播所有已连接前端
│ └─ 解析 alerts → 前端显示 Toast 告警
│
└─ 新增 error 告警时
├─ LPUSH qmt:status:alerts {alert_json}
├─ LTRIM qmt:status:alerts 0 199 （保留最近 200 条）
└─ 企业微信 send_qmt_alert()

服务离线检测：
TTL=35s 意味着：若某个服务停止上报（崩溃/网络断开），35s 内对应的
`qmt:market:status` 或 `qmt:trade:status` 键自然过期。Alpha `QmtStatusWorker` 每

次轮询读到键不存在时，立即对前端推送对应服务的 `{"overall_status": "offline"}`。
两个服务独立检测，互不影响。

## 4. 数据协议规范

### 4.1 Redis 键命名规范（QMT 专用）

所有 QMT 相关键统一使用 `qmt:` 前缀，避免与现有 IBKR 键冲突。
Redis Key Type 说明 TTL 写入者
`qmt:market:status`        String 行情服务（qmt- 35s qmt-market
market）完整状态快 StatusReporter
照 JSON （10s上报）
`qmt:trade:status`         String 交易服务（qmt- 35s qmt-trade
trade）完整状态快照 StatusReporter
JSON （10s上报）
`qmt:status:notify`        Pub/Sub 实时状态广播通道 — 两个
（两个服务共享， StatusReporter
payload 含 source 字
段）
`qmt:status:alerts`        List 告警历史（LPUSH， 永久 两个
最近 200 条） StatusReporter
`qmt:account:online:       String 账户在线状态                60s         on_account_status
{account_id}`                      （"online"/"offline"） 回调
`qmt:sub:pool`             Hash ~~已废弃（v1.3 移除订阅管理）~~
`qmt:snapshot:tick`        Hash ~~已废弃（v1.3 行情由旧实现接管）~~
`qmt:snapshot:kline:       Hash ~~已废弃（v1.3 行情由旧实现接管）~~
{period}`
`qmt:stream:agg`           Stream ~~已废弃（v1.3 行情由旧实现接管）~~
`qmt:stream:tick:{code}`   Stream ~~已废弃（v1.3 行情由旧实现接管）~~
`qmt:stream:kline:         Stream ~~已废弃（v1.3 行情由旧实现接管）~~
{period}:{code}`
`qmt_tick:{YYYYMMDD}       List 旧实现 Tick 数据（pickle） 当日 qmt_tick.py
:{code}`                          （LRANGE -1 -1 读取最新）
`qmt:market:last_updated`  String ~~已废弃（v1.3 心跳由 StatusReporter 管理）~~

`qmt:account:asset`        String 资金快照 JSON 60s on_stock_trade 回
调 / full_sync
`qmt:account:positions`    String 持仓快照 JSON 60s on_stock_trade 回
调 / full_sync
`qmt:account:orders`       String 当日委托快照 JSON 60s on_stock_order 回
调 / full_sync
`qmt:account:trades`       String 当日成交快照 JSON 60s on_stock_trade 回
调 / full_sync
Redis Key Type 说明 TTL 写入者
`qmt:cmd:queue`             List 交易命令队列 永久 Alpha 后端
（LPUSH 写，
BRPOPLPUSH 读）
`qmt:cmd:queue:backup`    List 消费中备份队列 永久 CmdConsumer
`qmt:cmd:dlq`             List 死信队列（失败 3 次） 永久 CmdConsumer
`qmt:order:status:        String 单笔委托状态 JSON 86400s（1天） on_stock_order /
{req_id}`                                                   on_order_error 回
调
`qmt:event:order_update`  Stream 订单回报事件 Stream maxlen=10000 全部 on_stock_* 回
（含成交明细） 调
`qmt:market:last_updated` String 最后行情更新时间戳 永久 MarketHub
`qmt:kill_switch`         String 熔断开关（"1"=激活） 永久 Alpha 后端写入，
TradeHub 读取
`qmt:config:sub_limit`    String ~~已废弃（v1.3 移除订阅管理）~~

### 4.2 行情数据格式

Tick 数据（标准化后）
{
"code": "600519.SH",
"name": "贵州茅台",
"time": "14:30:00",
"datetime": "2026-04-13 14:30:00",
"timestamp": 1713000000,
"open": 1800.00,
"high": 1870.00,
"low": 1790.00,
"last": 1855.00,
"close": 1820.00, // 昨收
"bid1": 1854.50,
"bid2": 1854.00,
"bid3": 1853.50,
"bid4": 1853.00,
"bid5": 1852.50,
"ask1": 1855.50,
"ask2": 1856.00,
"ask3": 1856.50,
"ask4": 1857.00,
"ask5": 1857.50,
"bid_vol1": 100,
"bid_vol2": 200,
"bid_vol3": 500,
"bid_vol4": 300,
"bid_vol5": 400,
"ask_vol1": 150,
"ask_vol2": 250,
"ask_vol3": 600,
"ask_vol4": 350,
"ask_vol5": 450,
"volume": 12345678, 成交量（股）
//
"amount": 22956098100.0, // 成交额（元）
"avg_price": 1858.33, // 均价
"limit_up": 2002.00, // 涨停价
"limit_down": 1638.00, // 跌停价
"pct_change": 1.92, // 涨跌幅（%）
"change": 35.00 // 涨跌额
}

K 线数据
{
"code": "600519.SH",
"period": "1m",
"datetime": "2026-04-13 14:30:00",
"timestamp": 1713000000,
"open": 1850.00,
"high": 1860.00,
"low": 1848.00,
"close": 1855.00,
"volume": 12345,
"amount": 22887325.0
}

### 4.3 交易数据格式

下单请求（POST /trade/order）
{
"req_id": "550e8400-e29b-41d4-a716-446655440000",
"stock_code": "600519.SH",
"order_type": "buy", // buy / sell
"order_volume": 100, // 股数（必须是 100 的整数倍）
"price_type": "limit", // limit / market / best5 / cancel_remain
"price": 1855.00, // 限价单必填；市价单填 0
"strategy_name": "alpha",
"order_remark": "A 股轮动-买入"
}

下单响应
{
"code": 0,
"msg": "ok",
"data": {
"req_id": "550e8400-e29b-41d4-a716-446655440000",
"order_id": 123456789, // xtquant 返回的委托编号
"status": "SUBMITTED",
"stock_code": "600519.SH",
"order_volume": 100,
"price": 1855.00,
"submitted_at": 1713000000.0
}
}

订单回报事件（Redis Stream：qmt:event:order_update）
{
"req_id": "550e8400-e29b-41d4-a716-446655440000",
"order_id": "123456789",
"account_id": "666631557962",
"stock_code": "600519.SH",
"stock_name": "  贵州茅台",
"order_type": "buy",
"order_volume": 100,
"traded_volume": 100,
"price": 1855.00,
"traded_price": 1855.00,
"status": "FILLED",
"status_msg": "     全部成交",
"order_time": "14:30:00",
"traded_time": "14:30:01",
"timestamp": 1713000001.0
}

持仓数据格式
[
{
"account_id": "666631557962",
"stock_code": "600519.SH",
"stock_name": "  贵州茅台",
"volume": 1000, //持仓数量
"can_use_volume": 1000, // 可用数量（T+1 限制）
"avg_price": 1800.00, // 持仓均价
"market_value": 1855000.00, // 当前市值
"profit_loss": 55000.00, // 浮动盈亏
"profit_loss_ratio": 0.0306 // 浮盈比例
}
]

## 5. Alpha Finance Terminal 集成（Linux 端）

### 5.1 新增服务层

#### 5.1.1 `backend/service/qmt_service.py`

职责：对 QMT-Server HTTP API 的封装，提供 Alpha 后端调用的统一接口。优先从 Redis 缓
存读取（行情/账户），HTTP 调用仅用于实时控制指令和调试。

```python
class QmtService:
    """QMT-Server 代理服务
    行情/账户数据：优先读 Redis 缓存（QMT-Server 定时推送）
    交易指令：先写 MySQL（DRAFT 状态），再写 Redis 队列（异步可靠投递）
    调试/控制：直接 HTTP 调用 QMT-Server
    """

    def __init__(self):
        self._base_url: str       # 从 ConfigCenter 读取（默认 http://192.168.3.10:8090）
        self._api_key: str        # 从 ConfigCenter 读取
        self._http_client: httpx.AsyncClient
        self._order_counter: dict  # 限流计数器

    # --- 行情（Redis 优先） ---
    async def get_snapshot(self, codes: list[str]) -> dict: ...
    async def get_tick(self, code: str) -> dict: ...
    async def get_kline(self, code: str, period: str, count: int) -> list: ...

    # --- 订阅管理 ---
    async def add_subscribe(self, codes: list[str]) -> dict: ...
    async def remove_subscribe(self, codes: list[str]) -> dict: ...
    async def list_subscribe(self) -> list: ...

    # --- 账户（Redis 优先） ---
    async def get_asset(self) -> dict: ...
    async def get_positions(self) -> list: ...
    async def get_orders(self, cancelable_only: bool = False) -> list: ...
    async def get_trades(self) -> list: ...

    # --- 交易（MySQL + Redis Queue） ---
    async def place_order(self, request: QmtOrderRequest) -> str:
        """
        下单流程（两步写入，确保数据不丢失）：
        1. 写 MySQL qmt_orders 表（status=DRAFT），确保后续 UPDATE 有目标行
        2. 写 Redis qmt:cmd:queue（QMT-Server 消费）
        返回 req_id 供前端轮询状态。
        """
        req_id = str(uuid4())
        # Step 1: 写 MySQL（DRAFT 状态）
        db.execute(insert(QmtOrder).values(
            req_id=req_id,
            account_id=self._account_id,
            stock_code=request.stock_code,
            stock_name=request.stock_name,
            order_type=request.order_type,
            order_volume=request.order_volume,
            price_type=request.price_type,
            price=request.price,
            status="DRAFT",
        ))
        await db.commit()
        # Step 2: 写 Redis 队列
        cmd = {
            "req_id": req_id,
            "cmd": "place_order",
            "stock_code": request.stock_code,
            "order_type": request.order_type,
            "order_volume": request.order_volume,
            "price_type": request.price_type,
            "price": request.price,
            "retry_count": 0,
        }
        await asyncio.to_thread(
            self._redis.rpush, "qmt:cmd:queue", json.dumps(cmd)
        )
        return req_id

    async def cancel_order(self, order_id: str) -> dict: ...
    async def cancel_all(self) -> dict: ...

    # --- 健康检查 ---
    async def health(self) -> dict: ...
    async def is_online(self) -> bool: ...  # 检查心跳是否在 30s 内

    # --- 直接 HTTP 转发（供在线调试使用，有路径白名单限制） ---
    async def proxy_request(self, method: str, path: str, body: dict) -> dict:
        """透传请求到 QMT-Server，禁止交易类和危险操作路径"""
        BLOCKED_PREFIXES = (
            "/trade/order", "/trade/cancel", "/control/",
        )
        if any(path.startswith(p) for p in BLOCKED_PREFIXES):
            raise HTTPException(403, "交易/控制类操作请使用专用接口")
        ...
```

#### 5.1.2 `backend/worker/qmt_market_worker.py`（已移除）

> **v1.3 变更**：行情订阅已由旧实现（`qmt_tick.py`）接管，Tick 数据存储在 Redis db0（pickle 格式）。
> Alpha 后端通过 `QmtService.get_snapshot` / `get_tick` 直接从 Redis 读取，
> 不再需要独立的 Worker 消费 `qmt:stream:agg`。此文件已删除。

#### 5.1.3 `backend/worker/qmt_order_worker.py`

职责：使用 Consumer Group 消费 `qmt:event:order_update` Stream，更新 MySQL 订单状态，触发通知。

> **可靠性要求**：使用 XREADGROUP + XACK 确保事件不丢失。处理成功才 ACK；崩溃后未 ACK 的事件会在重启后重新投递。MySQL 写入依赖 `uk_req_id` 唯一键保证幂等（ON DUPLICATE KEY UPDATE）。

```python
class QmtOrderWorker:
    """消费 QMT 订单回报事件，更新 DB 和推送通知"""

    GROUP_NAME = "alpha-order-worker"
    CONSUMER_NAME = "worker-1"  # 单实例部署，固定名称

    async def start(self):
        """持续消费 qmt:event:order_update（Consumer Group 模式）"""
        # 启动时确保 Consumer Group 存在（id="0" 从头消费历史，首次部署不丢数据）
        await asyncio.to_thread(
            self._redis.xgroup_create,
            "qmt:event:order_update", self.GROUP_NAME, id="0", mkstream=True
        )
        while True:
            try:
                messages = await asyncio.to_thread(
                    self._redis.xreadgroup,
                    self.GROUP_NAME, self.CONSUMER_NAME,
                    {"qmt:event:order_update": ">"},  # ">" 只读新消息
                    count=10, block=5000
                )
                for stream, msgs in messages:
                    for msg_id, data in msgs:
                        await self._handle_order_event(data)
                        # 处理成功才 ACK，失败不 ACK → 重启后重投递
                        await asyncio.to_thread(
                            self._redis.xack,
                            "qmt:event:order_update", self.GROUP_NAME, msg_id
                        )
            except Exception as e:
                logger.error("[ERROR] QmtOrderWorker 消费失败: %s", e)
                await asyncio.sleep(3)

    async def _handle_order_event(self, event: dict):
        """统一处理所有订单状态变更事件"""
        status = event["status"]  # submitted / reported / partial / filled / canceled / rejected
        req_id = event["req_id"]

        updates = {
            "status": status,
            "updated_at": func.now(),
        }
        # 已报状态：QMT 确认委托，返回 order_id
        if event.get("order_id"):
            updates["order_id"] = event["order_id"]
        # 成交相关状态：更新成交量和均价
        if status in ("partial", "filled"):
            updates["traded_volume"] = event.get("traded_volume", 0)
            updates["traded_price"] = event.get("traded_price")
        # 失败/废单状态
        if status == "rejected":
            updates["status_msg"] = event.get("error_msg", "委托失败")

        # UPSERT by req_id（幂等保护：重复消费不报错）
        db.execute(
            insert(QmtOrder).values(
                req_id=req_id, **updates
            ).on_duplicate_key_update(**updates)
        )
        await db.commit()

        # 通知分发
        if status == "filled":
            await self._notify_fill(event)
        elif status == "rejected":
            await self._notify_reject(event)

    async def _notify_fill(self, event: dict):
        """全部成交：企业微信通知"""

    async def _notify_reject(self, event: dict):
        """委托失败：企业微信通知"""
```

#### 5.1.4 `backend/worker/qmt_status_worker.py`

职责：订阅 Redis Pub/Sub `qmt:status:notify`，实时将 QMT-Server 状态推送给所有已
连接的前端 WebSocket 客户端。兼备轮询兜底（Pub/Sub 断线保护）。

```python
class QmtStatusWorker:
    """
    双路监听两个 QMT 服务（market / trade）的状态并实时推送前端：
    主路：Redis Pub/Sub qmt:status:notify（两个服务共享频道，payload 含 source 字段区分）
    备路：每 15s 分别轮询 qmt:market:status / qmt:trade:status（Pub/Sub 断线保底）
    """

    POLL_INTERVAL = 15  # 轮询兜底间隔（秒）
    OFFLINE_TTL = 35  # 超过此秒数无心跳视为离线

    def __init__(self, redis_client, ws_manager):
        self._redis = redis_client  # 同步 redis.Redis（Pub/Sub 需独立连接）
        self._ws = ws_manager       # WebSocket 广播管理器
        # 分别跟踪两个服务上次状态的 MD5，用于变化检测（Pub/Sub 和轮询共享）
        self._last_hash = {"market": "", "trade": ""}

    async def start(self) -> None:
        """并发启动 Pub/Sub 监听 + 轮询兜底"""
        await asyncio.gather(
            self._pubsub_loop(),
            self._poll_loop(),
        )

    async def _pubsub_loop(self) -> None:
        """订阅 qmt:status:notify，收到消息立即推前端"""
        pubsub = self._redis.pubsub()
        pubsub.subscribe("qmt:status:notify")
        logger.info("[OK] QmtStatusWorker 已订阅 qmt:status:notify")
        while True:
            try:
                message = await asyncio.to_thread(
                    pubsub.get_message, timeout=1.0
                )
                if message and message["type"] == "message":
                    raw = message["data"]
                    status = json.loads(raw)
                    # 同步更新 _last_hash，防止轮询重复推送同一条消息
                    svc = status.get("source", "market")
                    self._last_hash[svc] = _md5(raw)
                    await self._broadcast(status)
            except Exception as e:
                logger.error("[ERROR] QmtStatusWorker Pub/Sub 接收失败: %s", e)
                await asyncio.sleep(3)  # 短暂等待后重试

    async def _poll_loop(self) -> None:
        """每 15s 轮询兜底：分别检查两个服务的状态键，Pub/Sub 断线时保底推送"""
        while True:
            await asyncio.sleep(self.POLL_INTERVAL)
            for svc in ("market", "trade"):
                try:
                    raw = await asyncio.to_thread(
                        self._redis.get, f"qmt:{svc}:status"
                    )
                    if raw:
                        status = json.loads(raw)
                    else:
                        # 键不存在 → 对应服务已超时离线
                        status = {
                            "source": svc,
                            "overall_status": "offline",
                            "server_time": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                            "timestamp": time.time(),
                            "components": {},
                            "alerts": [{
                                "key": f"{svc}_offline",
                                "level": "error",
                                "msg": f"qmt-{svc} 心跳超时，进程可能已宕机",
                            }],
                        }

                    new_hash = _md5(raw or f"{svc}_offline")
                    if new_hash != self._last_hash[svc]:
                        await self._broadcast(status)
                    self._last_hash[svc] = new_hash

                except Exception as e:
                    logger.error("[ERROR] QmtStatusWorker 轮询 %s 失败: %s", svc, e)

    async def _broadcast(self, status: dict) -> None:
        """通过 WebSocket 广播给所有订阅 QMT 状态的客户端"""
        message = json.dumps({
            "type": "qmt_status",
            "data": status,
        }, ensure_ascii=False)
        await self._ws.broadcast_to_channel("qmt_status", message)
        logger.debug("[INFO] QMT 状态已推送 overall=%s", status.get("overall_status"))
```

WebSocket 通道约定：前端连接 `ws://server/alpha/ws/qmt-status` 后订阅 `qmt_status` 频道，收到的消息格式：

```json
// 来自 qmt-market 进程的推送（source = "market"）
{
    "type": "qmt_status",
    "data": {
        "source": "market",
        "overall_status": "degraded",
        "server_time": "2026-04-13 14:30:05",
        "timestamp": 1713000005.0,
        "uptime": 3605,
        "components": {
            "xtdata": {"connected": true, "status": "degraded", "last_tick_delay_s": 75.2},
            "redis": {"connected": true, "status": "healthy", "latency_ms": 1.2}
        },
        "alerts": [
            {"key": "xtdata_stale", "level": "warning", "msg": "行情 75s 无推送，可能存在延迟"}
        ]
    }
}

// 来自 qmt-trade 进程的推送（source = "trade"）
{
    "type": "qmt_status",
    "data": {
        "source": "trade",
        "overall_status": "healthy",
        "server_time": "2026-04-13 14:30:06",
        "timestamp": 1713000006.0,
        "uptime": 7210,
        "components": {
            "xttrader": {"connected": true, "status": "healthy", "account_status": "online"},
            "redis": {"connected": true, "status": "healthy", "latency_ms": 1.0,
                      "cmd_queue_depth": 0, "dlq_depth": 0},
            "cmd_consumer": {"running": true, "status": "healthy", "processed_today": 12}
        },
        "alerts": []
    }
}
```

### 5.2 新增 API 路由

新增路由文件：`backend/api/v1/endpoints/qmt.py`
在 `backend/api/v1/api.py` 注册：`router.include_router(qmt.router,
prefix="/qmt", tags=["QMT"])`

接口列表
**行情接口**

| 方法  | 路径                                  | 说明          |
|-----|-------------------------------------|-------------|
| GET | `/api/v1/qmt/quote/snapshot`        | 批量查询最新 Tick |
| GET | `/api/v1/qmt/quote/tick/{code}`     | 单只股票最新 Tick |
| GET | `/api/v1/qmt/quote/kline`           | 查询 K 线      |
| GET | `/api/v1/qmt/quote/detail/{code}`   | 股票基础信息      |
| GET | `/api/v1/qmt/quote/sector/{sector}` | 板块股票列表      |

**账户接口**

| 方法  | 路径                              | 说明   |
|-----|---------------------------------|------|
| GET | `/api/v1/qmt/account/asset`     | 账户资金 |
| GET | `/api/v1/qmt/account/positions` | 持仓列表 |
| GET | `/api/v1/qmt/account/orders`    | 当日委托 |
| GET | `/api/v1/qmt/account/trades`    | 当日成交 |

**交易接口**

| 方法   | 路径                                 | 说明     |
|------|------------------------------------|--------|
| POST | `/api/v1/qmt/trade/order`          | 下单     |
| POST | `/api/v1/qmt/trade/cancel`         | 撤单     |
| POST | `/api/v1/qmt/trade/cancel_all`     | 全部撤单   |
| GET  | `/api/v1/qmt/trade/order/{req_id}` | 查询委托状态 |

**健康与调试**

| 方法   | 路径                   | 说明                                              |
|------|----------------------|-------------------------------------------------|
| GET  | `/api/v1/qmt/health` | QMT-Server 连接状态                                 |
| POST | `/api/v1/qmt/proxy`  | 透传请求到 QMT-Server（调试用，禁止交易类和控制类路径）                |

> **proxy 路径白名单**：`/trade/order`、`/trade/cancel`、`/trade/cancel_all`、`/control/*` 路径被禁止透传，必须通过专用接口调用以确保风控检查生效。

**风控（一键熔断）**

| 方法     | 路径                          | 说明                               |
|--------|-----------------------------|----------------------------------|
| GET    | `/api/v1/qmt/kill-switch`   | 查询熔断状态（读 Redis `qmt:kill_switch`） |
| POST   | `/api/v1/qmt/kill-switch`   | 激活熔断（写 Redis `qmt:kill_switch = 1`，QMT-Server CmdConsumer 立即停止处理新命令） |
| DELETE | `/api/v1/qmt/kill-switch`   | 解除熔断（DEL Redis `qmt:kill_switch`）    |

> A 股熔断与美股 IBKR 熔断互相独立，互不影响。激活状态下：前端下单面板所有按钮禁用，显示"A 股熔断中"红色横幅。

**设置**

| 方法  | 路径                   | 说明                  |
|-----|----------------------|---------------------|
| GET | `/api/v1/qmt/config` | 查看 QMT 配置（脱敏）       |
| PUT | `/api/v1/qmt/config` | 更新 QMT-Server 地址等配置 |

### 5.3 前端页面设计

#### 5.3.0 页面职责划分（强制隔离）

A 股交易与美股交易使用完全独立的页面，禁止混用任何组件或路由。
| 页面 | 路由 | 券商 | 说明 |
|------|------|------|------|
| 美股/期权交易（现有） | `/execution` | IBKR | 保持原样，不做任何修改 |
| A 股交易（新增） | `/qmt-trade` | 华泰 QMT | 独立页面，本节重点设计 |
| QMT 接口调试（新增） | `/qmt-debug` | — | admin 专用调试工具 |

导航栏结构（在 `Sidebar.tsx` 中新增两条菜单项）：
── 账户信息 /
── 持仓信息 /positions
── 交易分析 /orders
── 美股交易 /execution ← 现有，IBKR
── A股交易 /qmt-trade ← 新增，QMT（本节设计）
── 自动交易 /auto-trade
── 开仓计划 /plan
...
── 系统设置 /settings
── QMT调试台 /qmt-debug ← 新增，admin 可见

#### 5.3.1 A 股交易页（`/qmt-trade`）

前端文件结构：
frontend/src/pages/qmt-trade/
├── index.ts # 导出 QmtTradeDashboard
├── QmtTradeDashboard.tsx # 主页面（布局容器）
├── QmtAccountCard.tsx # 顶部资金概览卡片
├── QmtOrderPanel.tsx # A股下单面板（核心）
├── QmtPositionList.tsx # A股持仓列表
├── QmtOrderList.tsx # 当日委托列表
└── QmtTradeList.tsx # 当日成交列表

页面整体布局：
┌─────────────────────────────────────────────────────────────────┐
│ A 股交易                        [● QMT   正常] [一键熔断] [刷新] │
├─────────────────────────────────────────────────────────────────┤
│ 资金概览（QmtAccountCard） │
│ 总资产：1,500,000 可用：150,000 持仓市值：1,350,000 │
│ 今日盈亏：+25,000（+1.67%） 最后更新：14:30:05 │
├────────────────────────┬────────────────────────────────────────┤
│ 下单面板 │ 持仓列表 │
│ QmtOrderPanel │ QmtPositionList │
│ │ │
│ │ │
├────────────────────────┴────────────────────────────────────────┤
│ Tabs ：[ 当日委托 ] [ 当日成交 ]                                  │
│ QmtOrderList / QmtTradeList │
└─────────────────────────────────────────────────────────────────┘

QmtOrderPanel（下单面板）详细设计：
┌──────────────────────────────┐
│ A 股下单 │
├──────────────────────────────┤
│ 股票代码 │
│ [   600519.SH       ] [  查询] │
│ 贵州茅台 现价：1855.00 │
│ 涨跌：+35.00（+1.92%） │
│ 涨停：2002.00 跌停：1638.00 │
├──────────────────────────────┤
│ 买卖方向 │
│ [ 买入 ] [ 卖出 ]                     │
├──────────────────────────────┤
│ 价格类型 │
│ [限价▼]                             │
│ 限价 / 市价（五档撮合） │
│ 市价（对手方最优） │
│ 市价（五档撤剩） │
├──────────────────────────────┤
│ 委托价格 元 │
│ [         1855.00    ]                │
│ 快速填价：[买一] [现价] [卖一]│
├──────────────────────────────┤
│ 委托数量 手 │
│ [             1      ]                │
股 约 185,500 元
│ = 100 │
│ 可用：150,000 元 │
│ 可买：0 手（资金不足） │
├──────────────────────────────┤
│ [        提交委托（买入）         ]   │
└──────────────────────────────┘

A 股交易关键约束（区别于美股）：
| 规则 | 说明 |
|------|------|
| 数量单位 | 以"手"为单位（1手=100股），买入必须为整手，卖出可为零股 |
| T+1 限制 | 今日买入的股票不可当日卖出（`can_use_volume` 字段控制） |
| 涨跌停限制 | 委托价格不得超出涨停/跌停价；界面实时显示 |
| 交易时段 | 09:15-09:25 集合竞价；09:30-11:30、13:00-15:00 连续竞价；非交易时段提示"非交易时间" |
| 时区 | 全部使用北京时间（`Asia/Shanghai`），无需双时区展示 |
| 市价单价格 | 市价单委托价格字段填 0，界面灰色禁用 |

QmtPositionList（持仓列表）：
股票代码 名称 持仓 可用 成本价 现价 市值 盈亏 盈亏% 操作
600519.SH 贵州茅台 10手 10手 1800.00 1855.00 185,500 +5,500 +3.06% [卖出]
000001.SZ 平安银行 20手 0手 12.50 13.20 26,400 +1,400 +5.60% [T+1]

注意事项：
`   可用 = 0` 且 `持仓 > 0`：今日买入，T+1 限制，显示"T+1"徽章而非"卖出"按钮
点击"卖出"按钮：自动预填下单面板（方向=卖出，代码=该股，数量=可用数量，价格=
现价）
现价来自 Redis db0 `qmt_tick:{YYYYMMDD}:{code}`（旧实现实时推送，若无行情则显示"--"）
QmtOrderList（当日委托）：
时间 代码 名称 方向 数量 价格 成交量 成交均价 状态 操作
09:30:00 600519.SH 贵州茅台 买入 100股 1855.00 100股 1854.50 全部成交 -
13:01:00 000001.SZ 平安银行 买入 200股 13.20 0股 - 已报   [撤单

状态颜色：全部成交=绿，部分成交=蓝，已报/待报=黄，已撤/失败=灰/红
可撤状态（已报/部分成交）显示撤单按钮，撤单需二次确认弹窗
QmtTradeList（当日成交）：
成交时间 代码 名称 方向 成交量 成交价 成交金额
09:30:01 600519.SH 贵州茅台 买入 100股 1854.50 185,450.00

QmtAccountCard（资金概览）实时更新逻辑：

// 数据来源策略（WebSocket 为主，轮询为辅）：
// 1. 页面加载时立即请求一次 GET /api/v1/qmt/account/asset
// 2. WebSocket qmt_status 消息实时推送更新（< 1s 延迟）
// 3. 轮询兜底 GET /api/v1/qmt/account/asset（每 30s，仅 WebSocket 断线时作为保底）

// 持仓和委托数据：
// 1. 页面加载时立即请求一次
// 2. WebSocket qmt_status 消息触发后立即请求（有成交/委托变化时）
// 3. 轮询兜底 GET /api/v1/qmt/account/positions（每 30s）
// 4. 用户点击"刷新"手动触发

一键熔断按钮（A 股专用）：
与美股 IBKR 熔断互相独立，互不影响
激活：调用 POST /api/v1/qmt/kill-switch（后端写 Redis `qmt:kill_switch = 1`），QMT-Server CmdConsumer 立即停止处理新命令
激活状态下：下单面板所有按钮禁用，显示"A 股熔断中"红色横幅
解除：调用 DELETE /api/v1/qmt/kill-switch（后端清除 Redis 键），需二次确认弹窗

#### 5.3.2 状态感知设计（前端三层）

前端对 QMT 状态的感知分三个层次，覆盖全部使用场景：
Layer 1 — 全局状态指示灯（所有页面可见）
在侧边栏底部或顶部导航栏嵌入一个极简状态徽章：
侧边栏底部：
[●] QMT 正常 ← 绿色，所有组件 healthy
[●] QMT 降级 ← 橙色，存在 warning 告警
[●] QMT 离线 ← 红色闪烁，overall_status=offline
[?] QMT 未配置 ← 灰色，未配置 QMT 地址

    点击徽章：弹出浮层，展示各组件状态摘要（不跳转页面）
    数据来源：WebSocket `qmt_status` 频道（近实时，10s 内到达）

Layer 2 — Dashboard QMT 状态卡片
在 Dashboard 页面新增一个 QMT 状态卡片（与 IBKR 连接状态卡片并列）：
┌─────────────────────────────────────────────────────┐
│ QMT 系统状态                     [● 正常] [刷新] │
├──────────────┬──────────────┬───────────────────────┤
│ 行情（xtdata）│ 交易(xttrader)│ 命令队列 │
│ ● 正常 │ ● 正常 │ 积压 0 死信 0 │
│ 订阅 52 只 │ 账户已登录 │ │
│ 延迟 < 1s │ │ │
├──────────────┴──────────────┴───────────────────────┤
│ 运行时长：01:02:35 最后上报：14:30:05 │
│ [当前无告警]                                                  │
└─────────────────────────────────────────────────────┘

    数据来源：WebSocket（自动更新）
    告警时：卡片背景变橙/红，展示告警文字

Layer 3 — QMT 调试页面详细状态面板

#### 5.3.3 QMT 调试台（`/qmt-debug`，admin 专用）

前端文件结构：
frontend/src/pages/qmt-debug/
├── index.ts
├── QmtDebugDashboard.tsx # 主页面（Tabs 容器）
├── QmtStatusPanel.tsx # Tab1：系统状态 + 告警历史
├── QmtApiDebugger.tsx # Tab2：接口调试（表单 + 响应）
├── QmtRedisViewer.tsx # 侧边栏：Redis 键值查看器
└── QmtStreamPreview.tsx # 侧边栏：Stream 实时预览

页面结构：
QMT 接口调试台
│
├── 顶部：全局状态横幅（始终可见）
│ ├── QMT-Server 地址（点击可编辑）
：http://192.168.3.10:8090
│ ├── 整体状态：[● 正常 / ● 降级 / ● 离线]
│ ├── 运行时长 + 最后上报时间
│ └── [手动刷新] 按钮
│
├── Tab 1 ：系统状态（默认展开，异常时自动切换到此 Tab）
│ ├── 组件状态卡片矩阵（2×2 布局）
│ │ ├── 行情（xtdata）
│ │ │ ├── 状态：healthy / degraded / offline
│ │ │ ├── 订阅：52 只
│ │ │ ├── 最后 Tick：0.3s 前
│ │ │ └── 状态灯（颜色）
│ │ ├── 交易（xttrader）
│ │ │ ├── 状态灯
│ │ │ ├── 账户：666631557962 已登录
│ │ │ └── 最后委托：14:28:30
│ │ ├── Redis
│ │ │ ├── 状态灯 + 延迟：1.2ms
│ │ │ ├── 命令队列积压：0
│ │ │ └── 死信队列：0 [若>0 显示红色 + 清空按钮]
│ │ └── CmdConsumer
│ │ ├── 状态灯
│ │ ├── 今日处理：12 条
│ │ └── 最后消费：14:28:30
│ │
│ └── 告警历史面板
│ ├── 筛选：全部 / error / warning
│ ├── 最近 50 条（来自 qmt:status:alerts）
│ └── 每条：时间 | 级别 | 告警内容
│
├── Tab 2 ：接口调试
│ ├── 左侧：接口分类导航
│ │ ├── 健康检查
│ │ ├── 行情查询
│ │ ├── 订阅管理
│ │ ├── 账户查询
│ │ └── 交易操作（下单二次确认弹窗）
│ │
│ ├── 中间：接口详情区
│ │ ├── 接口名称 + 方法标签 + 路径
│ │ ├── 参数表单（按接口配置自动渲染）
│ │ │ ├── 路径参数（文本输入）
│ │ │ ├── 查询参数（文本输入）
│ │ │ └── 请求体（Monaco JSON 编辑器 / 表单切换）
│ │ ├── [发送请求] 按钮（交易类先弹确认弹窗）
│ │ └── 响应区域
│ │ ├── HTTP 状态码 + 耗时（ms）
│ │ └── JSON 语法高亮（可展开/折叠节点）
│ │
│ └── 右侧：实时辅助面板
│ ├── Redis 键值查看器
│ │ ├── 输入框 + [查询] 按钮
│ │ └── 常用键快捷入口：
│ │ qmt:market:status / qmt:trade:status / qmt:account:asset /
│ │ qmt:account:positions / qmt:cmd:queue
│ ├── QMT Stream预览
│ │ ├── 选择 Stream（qmt:event:order_update）
│ │ ├── [开始/停止] 自动刷新（2s）
│ │ └── 最新 10 条记录（时间 + JSON 折叠）
│ └── 请求历史（最近 30 次）
│ ├── 每条：时间 + 方法 + 路径 + 状态码 + 耗时
│ └── 点击展开：完整请求 + 响应 JSON
│
└── Tab 3 ：告警配置（预留，后续实现）
└── 配置告警阈值（Tick 超时秒数、Redis 延迟告警值等）

状态变化的 Toast 通知：

```javascript
// 前端 QMT 状态处理逻辑（含自动重连）
const prevStatus = useRef<string>('unknown');
const retryCount = useRef<number>(0);

function connectQmtStatusWs() {
    const ws = new WebSocket('/alpha/ws/qmt-status');

    ws.onopen = () => {
        retryCount.current = 0;  // 连接成功，重置重试计数
    };

    ws.onmessage = (e) => {
        const { data } = JSON.parse(e.data);
        const overall = data.overall_status;

        // 状态恶化时弹 Toast
        if (prevStatus.current === 'healthy' && overall !== 'healthy') {
            const alerts = data.alerts ?? [];
            toast.error(`QMT 系统告警：${alerts[0]?.msg ?? overall}`, {
                duration: 8000,
                position: 'top-right',
            });
        }

        // 状态恢复时弹 Toast
        if (prevStatus.current !== 'healthy' && overall === 'healthy') {
            toast.success('QMT 系统已恢复正常', { duration: 4000 });
        }

        // QMT 离线（TTL 超时，overall_status='offline'）
        if (overall === 'offline') {
            toast.error('QMT-Server 离线，交易功能不可用', {
                duration: 0,       // 不自动消失，必须手动关闭
                id: 'qmt-offline', // 同 ID 不重复弹
            });
        }

        prevStatus.current = overall;
        setQmtStatus(data);
    };

    // 断线后指数退避重连（1s → 2s → 4s → ... → 30s 封顶）
    ws.onclose = () => {
        const delay = Math.min(1000 * Math.pow(2, retryCount.current), 30000);
        retryCount.current += 1;
        setTimeout(connectQmtStatusWs, delay);
    };
}

useEffect(() => {
    connectQmtStatusWs();
}, []);
```

#### 5.3.3 关键组件

接口配置表（驱动自动生成表单）：

interface ApiConfig {
id: string;
name: string;
method: 'GET' | 'POST' | 'PUT' | 'DELETE';
path: string; // 支持 :param 路径变量
params?: ParamDef[]; // 查询参数
body?: BodyDef; // 请求体 Schema
description: string;
example?: Record<string, unknown>; // 示例数据（自动填入表单）
}

// 完整接口定义表（QMT-Server 全部接口 + Alpha 代理接口）
const QMT_API_LIST: ApiConfig[] = [
{
id: 'health',
name: '   整体健康状态',
method: 'GET',
path: '/health',
description: '   查询 QMT-Server 各子系统状态',
},
{
id: 'tick_single',
name: '   查询单只股票 Tick',
method: 'GET',
path: '/quote/tick/:code',
params: [{ name: 'code', type: 'string', required: true, example: '60
description: '   查询指定股票最新 Tick 数据（优先从 Redis 快照读取）',
},
{
id: 'place_order',
name: '   下单',
method: 'POST',
path: '/trade/order',
body: {
schema: { /* JSON Schema */ },
},
example: {
stock_code: '600519.SH',
order_type: 'buy',
order_volume: 100,
price_type: 'limit',
price: 1855.00,
},
description: '   提交限价/市价委托（通过 Redis 可靠队列投递）',
},
// ... 全部接口
];

请求代理说明：
所有调试请求通过 Alpha 后端的 `/api/v1/qmt/proxy` 透传，解决跨域问题，同时保留认
证。请求流程：
浏览器 → POST /api/v1/qmt/proxy
{ method, path, params, body }
后端：添加 X-Api-Key 头 → 转发到 QMT-Server
→ Alpha
→ 返回响应 JSON

菜单可见性控制（`Sidebar.tsx`）：
调试台仅对 admin 角色可见，普通用户不展示此菜单项：

// Sidebar.tsx 伪代码
const adminOnlyItems = ['/qmt-debug'];
const filteredNavItems = navItems.filter(item =>
!adminOnlyItems.includes(item.href) || user.role === 'admin'
);

## 6. 高可用与一致性保障

### 6.1 Redis 写入一致性

Tick 数据（允许轻微丢失，追求低延迟）
快照 Hash 与 Stream 写入使用 Redis Pipeline（单次 RTT）
不使用 MULTI/EXEC 事务（不必要的性能损耗）
行情数据天然允许单条丢失（下条 Tick 会覆盖）
账户快照（必须原子更新）
使用 Lua 脚本或 Pipeline 保证 asset + positions 同时更新：

-- Lua 原子脚本：同时更新资金和持仓，避免读到半状态
redis.call('SET', KEYS[1], ARGV[1], 'EX', 30)
redis.call('SET', KEYS[2], ARGV[2], 'EX', 30)
redis.call('SET', KEYS[3], tostring(ARGV[3]))
return 1

交易命令（严格不丢失）
BRPOPLPUSH 保证弹出即备份
服务重启时检查备份队列是否有遗留命令（冷启动对账）
失败3次后进死信队列，通过企业微信告警人工处理

### 6.2 QMT-Server 断线重连

xtdata 断线（行情中断）
检测：心跳 watchdog 每 10s 检查 qmt:market:last_updated
若超过 30s 无更新，触发重连
重连：xtdata.reconnect()（xtquant 内置接口）
重连成功后重建全部订阅（从 qmt:sub:pool 恢复）
告警：企业微信推送"QMT 行情中断"通知

xttrader 断线（交易中断）
检测：心跳 watchdog + xttrader.connected 属性
重连：XtQuantTrader 重新 connect()
重连后查询当日委托状态，与 Redis 记录对账
告警：企业微信推送"QMT 交易中断"通知

### 6.3 Alpha 后端感知 QMT 离线

Alpha 后端读取 `qmt:server:heartbeat`，若 TTL 已过（30s），返回 503
前端收到 503 时显示"QMT 离线"橙色标识

### 6.4 时区处理

A 股交易时间为北京时间（Asia/Shanghai），无夏令时，所有时间字段：
内部存储：Unix 时间戳（毫秒级）
展示时：前端使用 `Asia/Shanghai` 时区格式化
严禁硬编码 `+8h` 偏移

## 7. 配置管理

### 7.1 QMT-Server 配置（Windows 端）

`config.json`   （版本控制，不含密码）
{
"server": {
"host": "0.0.0.0",
"log_level": "INFO",
"workers": 1
},
"market_server": {
"port": 8091
},
"trade_server": {
"port": 8090
},
"qmt": {
"mini_path": "C:\\ 迅投极速策略交易系统交易终端 华泰证券QMT实盘\\userdata_mi
"account_id": "666631557962",
"account_type": "STOCK",
"session_id": 1001,
"connect_timeout": 10,
"reconnect_interval": 30,
"reconnect_max_retry": -1
},
"redis": {
"host": "192.168.3.80",
"port": 6379,
"db": 0,
"socket_timeout": 3,
"socket_connect_timeout": 3,
"retry_on_timeout": true,
"health_check_interval": 30
},
"market": {
},
"trade": {
"cmd_queue_timeout": 5,
"order_timeout": 10,
"max_retry": 3,
"dlq_alert": true
},
"account": {
"sync_interval_asset": 5,
"sync_interval_positions": 5,
"sync_interval_orders": 3,
"sync_interval_trades": 3
}
}

`config.secret.json`      （不纳入版本控制，.gitignore）
{
"server": {
"api_key": "your-secret-api-key-here"
},
"redis": {
"password": ""
}
}

### 7.2 Alpha Finance Terminal 配置（Linux 端）

在 MySQL `configs` 表新增：
| key | value | 说明 |
|-----|-------|------|
| `qmt_server_url` | `http://192.168.3.10:8090` | QMT-Server 地址（可通过 UI 修改） |
| `qmt_server_api_key` | `your-secret-api-key-here` | API 密钥 |
| `qmt_server_timeout` | `10` | HTTP 请求超时（秒） |
| `qmt_market_enabled` | `true` | 是否启用 QMT 行情消费 |
| `qmt_trade_enabled` | `true` | 是否启用 QMT 交易 |

## 8. 部署指南

### 8.1 QMT-Server 部署（Windows）

#### 8.1.1 前置条件

     Windows 10/11（必须与 MiniQMT 同机运行，xtquant 通过 IPC 连接本机 MiniQMT）
     MiniQMT 交易终端已安装并登录账号 `666631557962`
     Redis 服务器可访问（192.168.3.80:6379）

Python 环境（已确认，勿自行安装新版本）：
Python解释器：C:\Users\admin\miniconda3\envs\qmt\python.exe
pip 安装命令：C:\Users\admin\miniconda3\envs\qmt\python.exe -m pip install <pa

#### 8.1.2 安装步骤

:: 1. 将 qmt-server/ 目录拷贝到 Windows 机器 C:\qmt-server\
cd C:\qmt-server

:: 2. 安装依赖（两个服务共享同一 Python 环境，各自 requirements.txt 合并安装）
C:\Users\admin\miniconda3\envs\qmt\python.exe -m pip install -r qmt-marke
C:\Users\admin\miniconda3\envs\qmt\python.exe -m pip install -r qmt-trade

:: 3. 配置（两个服务共享根目录下的配置文件）
copy config.json.example config.json
::   编辑 config.json：确认 mini_path、account_id、redis 地址、端口
copy config.secret.json.example config.secret.json
::   编辑 config.secret.json：填写 redis_password、api_key、wecom_webhook 等
:: 4.分别测试运行
:: 终端 1：行情服务
C:\Users\admin\miniconda3\envs\qmt\python.exe qmt-market\main.py
::   访问 http://localhost:8091/health 确认正常
::   终端 2：交易服务
C:\Users\admin\miniconda3\envs\qmt\python.exe qmt-trade\main.py
::   访问 http://localhost:8090/health 确认正常
:: 5. 注册 Windows 服务（使用 NSSM），两个服务分别注册
install_services.bat

#### 8.1.3 `requirements.txt`

`qmt-market/requirements.txt`   （行情服务，无 xttrader 依赖）
fastapi==0.115.0
uvicorn[standard]==0.30.0
xtquant>=1.0.0 # 华泰官方，需从迅投官网获取（含 xtdata + xttrader，仅用 x
redis[hiredis]==5.0.0
pydantic==2.7.0
python-dotenv==1.0.0
loguru==0.7.2
pytz==2024.1

`qmt-trade/requirements.txt`    （交易服务，无 xtdata 行情依赖）
fastapi==0.115.0
uvicorn[standard]==0.30.0
xtquant>=1.0.0 # 华泰官方（含 xtdata + xttrader，仅用 xttrader 部分）
redis[hiredis]==5.0.0
pydantic==2.7.0
httpx==0.27.0
python-dotenv==1.0.0
loguru==0.7.2
pytz==2024.1

“注：xtquant 包同时包含 xtdata 和 xttrader，无法只安装其中一个。但通过目录隔离和

```python
  import 规范，代码层面保证两个服务不相互引用对方的功能模块。”
```

#### 8.1.4 `install_services.bat`（两个 Windows 服务一次性注册）

@echo off
set NSSM=C:\nssm\nssm.exe
set PYTHON=C:\Users\admin\miniconda3\envs\qmt\python.exe
set BASE=C:\qmt-server

:: ---- 行情服务 ----
set MARKET_SVC=QmtMarket
%NSSM% install %MARKET_SVC% %PYTHON% "%BASE%\qmt-market\main.py"
%NSSM% set %MARKET_SVC% AppDirectory %BASE%
%NSSM% set %MARKET_SVC% AppStdout "%BASE%\logs\market\service.log"
%NSSM% set %MARKET_SVC% AppStderr "%BASE%\logs\market\service_err.log"
%NSSM% set %MARKET_SVC% Start SERVICE_AUTO_START
%NSSM% start %MARKET_SVC%
echo [OK] QmtMarket 服务安装并启动（:8091）
:: ---- 交易服务 ----
set TRADE_SVC=QmtTrade
%NSSM% install %TRADE_SVC% %PYTHON% "%BASE%\qmt-trade\main.py"
%NSSM% set %TRADE_SVC% AppDirectory %BASE%
%NSSM% set %TRADE_SVC% AppStdout "%BASE%\logs\trade\service.log"
%NSSM% set %TRADE_SVC% AppStderr "%BASE%\logs\trade\service_err.log"
%NSSM% set %TRADE_SVC% Start SERVICE_AUTO_START
%NSSM% start %TRADE_SVC%
echo [OK] QmtTrade 服务安装并启动（:8090）
pause

#### 8.1.5 `start_all.bat` / `stop_all.bat`（日常运维）

:: start_all.bat
@echo off
net start QmtMarket
net start QmtTrade
echo [OK]   行情服务(:8091) 和 交易服务(:8090) 已启动
:: stop_all.bat
@echo off
net stop QmtTrade
net stop QmtMarket
echo [OK]   两个服务已停止

#### 8.1.6 Windows 防火墙

入站规则：允许 TCP 8090（交易服务，局域网 192.168.3.0/24）
入站规则：允许 TCP 8091（行情服务，局域网 192.168.3.0/24）

### 8.2 Alpha Finance Terminal 更新（Linux）

# 1. 安装新增 Python 依赖（httpx 已存在则跳过）

pip install httpx==0.27.0

# 2.运行数据库迁移（新增 qmt_orders 表）

# 具体 SQL 见 sql/V0XX_add_qmt_orders.sql

# 3. 重启后端

bash stop.sh && bash start.sh

### 8.3 新增数据库表

`qmt_orders`   表（DDL）
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
`linked_req_id` VARCHAR(64) DEFAULT NULL COMMENT '卖出时关联的买入order_req_id',
`retry_count` TINYINT NOT NULL DEFAULT 0 COMMENT '重试次数',
`created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时
`updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURR
PRIMARY KEY (`id`),
UNIQUE KEY `uk_req_id` (`req_id`),
KEY `idx_account_status` (`account_id`, `status`),
KEY `idx_stock_created` (`stock_code`, `created_at`),
KEY `idx_order_id` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='QMT A 股委托记录';

`strategy_trades` 表（DDL，策略成交流水）
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

`daily_positions` 表（DDL，每日持仓快照，盘后定时任务生成）
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

## 9. 监控与健康检查

### 9.1 完整状态上报数据结构

两个服务各自独立上报，存入不同的 Redis 键（TTL=35s）：
`qmt:market:status`（qmt-market 进程，行情服务状态）
：

{
"source": "market",
"version": "1.0.0",
"overall_status": "healthy",
"server_time": "2026-04-13 14:30:05",
"timestamp": 1713000005.0,
"uptime": 3605,
"components": {
"xtdata": {
"connected": true,
"last_tick_at": 1713000005.0,
"last_tick_delay_s": 0.3,
"subscribed_count": 52,
"status": "healthy"
},
"redis": {
"connected": true,
"latency_ms": 1.2,
"status": "healthy"
}
},
"alerts": []
}

`qmt:trade:status`    （qmt-trade 进程，交易服务状态）：

{
"source": "trade",
"version": "1.0.0",
"overall_status": "degraded",
"server_time": "2026-04-13 14:30:05",
"timestamp": 1713000005.0,
"uptime": 3605,
"components": {
"xttrader": {
"connected": true,
"account_id": "666631557962",
"account_status": "online",
"last_order_at": 1712999400.0,
"status": "healthy"
},
"redis": {
"connected": true,
"latency_ms": 1.2,
"cmd_queue_depth": 0,
"dlq_depth": 0,
"status": "healthy"
},
"cmd_consumer": {
"running": true,
"last_consumed_at": 1712999400.0,
"processed_today": 12,
"status": "healthy"
}
},
"alerts": [
{
"key": "xtdata_stale",
"level": "warning",
"msg": "行情 75s 无推送，可能存在延迟",
"timestamp": 1713000005.0,
"server_time": "2026-04-13 14:30:05"
}
]
}

overall_status 判定规则（每个服务独立判定）：
| overall_status | 条件 |
|---------------|------|
| `healthy` | 本进程所有组件均为 healthy，无告警 |
| `degraded` | 存在任意 warning 告警，但无 offline 组件 |
| `offline` | 存在任意 offline 组件，或对应 Redis 键不存在（TTL 超时，进程宕机） |

### 9.2 状态感知链路总览

                             （

qmt-market StatusReporter 10s 周期） （
qmt-trade StatusReporter 10s 周
├─ SET qmt:market:status EX 35 ├─ SET qmt:trade:status EX
└─ PUBLISH qmt:status:notify (source=market)      └─ PUBLISH qmt:status:notif
│ │
└──────────────────┬───────────────────────────┘
│ 共享通知频道
Alpha QmtStatusWorker（订阅）
├─ 主路：Pub/Sub 监听（source 字段区分来源）
└─ 备路：每 15s 分别轮询两个状态键
├─ qmt:market:status 不存在 → market offlin
├─ qmt:trade:status 不存在 → trade offline
└─ 有变化 → 推 WebSocket
│
└─ ws://server/alpha/ws/qmt-status
├─ 前端全局状态指示灯（侧边栏，双灯）
├─ Dashboard QMT 状态卡片（market/
├─ QMT 调试页面状态面板
└─ 状态变化时弹 Toast 通知

### 9.3 Alpha 后端 HTTP 状态接口

`GET /api/v1/qmt/health`    响应（供前端主动查询/轮询兜底，合并两个服务状态）：

    {
        "market": {
         "online": true,
         "last_report_age_s": 8.2,
         "status": { "source": "market", "overall_status": "healthy", "..." }
        },
        "trade": {
         "online": false,
         "last_report_age_s": null,
         "status": { "source": "trade", "overall_status": "offline" }
        }
    }


    `online = false`：Redis 中对应服务的状态键不存在（TTL 超时）
    `last_report_age_s`：距离最后一次上报的秒数（键不存在时为 null）

### 9.4 告警策略

告警触发条件与通知方式
告警 key 级别 触发条件 通知方式 恢复通知
`xtdata_offline`   error xtdata 断线 企业微信 + 前端 Toast 是
`xtdata_stale`     warning > 60s 无 Tick 仅日志 + 前端 Toast 是
`xtdata_no_tick`   error > 120s 无 Tick 企业微信 + 前端 Toast 是
`xttrader_offline` error xttrader 断线 企业微信 + 前端 Toast 是
`account_offline`  warning 账户状态异常 企业微信 + 前端 Toast 是
`dlq_has_items`    error 死信队列 > 0 企业微信（含命令详情） 否（需人工
+ Toast 清理）
`redis_offline`    error Redis 不可达 进程日志（Redis 宕机 —
时无法写）
`redis_slow`       warning 延迟 > 100ms 仅日志 + 前端 Toast 是
`consumer_dead`    error CmdConsumer 线程停止 企业微信 + Toast 是
`market_offline`   error qmt-market TTL 超时（进 Alpha 侧感知后企业微 是
程宕机） 信
`trade_offline`    error qmt-trade TTL 超时（进程 Alpha 侧感知后企业微 是
宕机） 信

告警去重机制
StatusReporter 维护 `_active_alert_keys` 集合：
首次出现（新进入集合）：写历史 + 发企业微信（error 级别）
持续存在（已在集合中）：仅写历史，不重复发企业微信
消失（从集合中移除）：发"已恢复"企业微信通知
告警历史查询接口
`GET /api/v1/qmt/alerts?limit=50&level=error`

从 Redis `qmt:status:alerts` 读取，返回最近 N 条告警记录，格式同上文告警 JSON。

### 9.5 `/health` 接口（QMT-Server 本地）

                                   （行情服务）和 `GET

`GET http://192.168.3.10:8091/health`

http://192.168.3.10:8090/health`（交易服务）直接从各自 StatusReporter 内存读取

（无网络调用），响应即为对应服务状态键的完整内容。可用于：
NSSM 服务健康检查探针
局域网其他工具直接查询
调试页面手动触发检查

## 10. 风控设计

### 10.1 QMT-Server 本地风控

在 QMT-Server 的 TradeHub 层实现轻量风控（与 Alpha 后端风控互为兜底）：
规则 参数 说明
下单频率限制 5笔/分钟（可配置） 进程级滑动窗口
价格偏离保护 委托价偏离现价 > 2% 拒绝 防止异常价格委托
单笔数量上限 10,000 股（可配置） 防胖手指
非交易时段拦截 09:1511:30，13:0015:00 以外拒绝 基于北京时间，不含盘前竞价外
一键熔断 Redis `qmt:kill_switch` = 1 时拒绝全部下单 Alpha 后端写入，立即生效

### 10.2 Alpha 后端风控（调用 QMT 时）

在 `QmtService.place_order()` 中调用现有 `RiskService.check_qmt_order()`（新增方
法），复用以下规则逻辑：
胖手指保护：单笔金额 < 50 万人民币（A 股适配）
价格偏离：偏离 Tick 现价 > 2% 拒绝
一键熔断：检查 `qmt:kill_switch` 状态

## 附录 A：xtquant 关键 API 参考

A.1 xtdata 行情 API

# 订阅 Tick（推送到 callback，count=-1 表示持续订阅）

seq = xtdata.subscribe_quote(
stock_code='600519.SH',
period='tick',
callback=on_tick_callback,
count=-1
)

# 取消订阅

xtdata.unsubscribe_quote(seq)

# 获取最新完整 Tick（同步拉取）

tick_data = xtdata.get_full_tick(['600519.SH', '000001.SZ'])

# 获取历史 K 线

kline = xtdata.get_market_data(
fields=['open', 'high', 'low', 'close', 'volume', 'amount'],
stock_codes=['600519.SH'],
period='1m',
start_time='20260413 09:30:00',
end_time='20260413 15:00:00'
)

# 获取股票基础信息

detail = xtdata.get_instrument_detail('600519.SH')

# 获取板块股票列表

codes = xtdata.get_stock_list_in_sector('  沪深A股')

A.2 xttrader 交易 API

```python
  from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant

#   初始化并连接（callback 必须在 start 前注册）
trader = XtQuantTrader(mini_path, session_id=1001)
account = StockAccount('666631557962', 'STOCK')
callback = CallbackHandler(account_hub, session_mgr, redis_bridge, execut
trader.register_callback(callback)  # 注册回调（必须在 start 之前）
trader.start()
connect_result = trader.connect()
trader.subscribe(account)  # 订阅账户回报（必须调用，否则回调不触发）
#   查询资金（返回 XtAsset 对象）
asset_obj = trader.query_stock_asset_async(account)
# XtAsset    字段：
#    .account_id     str   账户号
#    .cash           float 可用资金
#    .frozen_cash    float 冻结资金
#    .market_value   float 持仓市值
#    .total_asset    float 总资产


#   查询持仓（返回 list[XtPosition]）
positions = trader.query_stock_positions_async(account)
# XtPosition   字段：
#    .account_id     str   账户号
#    .stock_code     str   股票代码
#    .volume         int   持仓数量
#    .can_use_volume int   可用数量（T+1）
#    .avg_price      float 持仓均价
#    .market_value   float 当前市值
#    .open_price     float 今日开盘均价


#   查询委托（返回 list[XtOrder]，cancelable_only=True 仅返回可撤委托）
orders = trader.query_stock_orders_async(account, cancelable_only=False)
#   查询成交（返回 list[XtTrade]）
trades = trader.query_stock_trades_async(account)

# 下单（立即返回委托编号 order_id，-1 表示下单失败）
# 注意：order_remark 用于携带 req_id，格式为 "alpha:{uuid4}"
order_id = trader.order_stock_async(
    account=account,
    stock_code='600519.SH',
    order_type=xtconstant.STOCK_BUY,  # 23=   买入, 24=卖出
    order_volume=100,
    price_type=xtconstant.FIX_PRICE,  # 11=   限价
    price=1855.00,
    strategy_name='alpha',
    order_remark=f'alpha:{req_id}'  # 关键：用于回调中关联 req_id
)
# order_id == -1     时视为下单被柜台前置拒绝，但 on_order_error 回调仍会触发
#   撤单（返回 0=成功提交, -1=失败）
cancel_result = trader.cancel_order_stock_async(account, order_id)

A
.3
XtQuantTraderCallback
回调接口完整说明


class XtQuantTraderCallback:
    """
         官方回调基类，所有方法均在 xtquant 内部线程调用。
    xtquant
    子类继承并实现所需方法。
    """

    def on_disconnected(self) -> None:
        """   连接断开。重连由外部 SessionMgr 负责。"""

    def on_account_status(self, status) -> None:
        """
        账户状态变化。
        status.account_id   : str
                            （"STOCK" / "CREDIT"）
        status.account_type : str
        status.status : int （0=正常，其他见 xtconstant.ACCOUNT_STAT
        触发时机：登录成功、账户踢线、账户异常等。
        """

    def on_stock_order(self, order) -> None:
        """
        委托状态变化推送。每次委托状态变化均会回调（含部分成交中间态）。
        关键字段见 3.5.2 实现注释中的 XtOrder 说明。
        注意：on_stock_order 与 on_stock_trade 会同时触发（成交时），
            on_stock_order 反映委托累计状态，on_stock_trade 反映本笔成交明细
        """

    def on_stock_trade(self, trade) -> None:
        """
        实际成交明细推送（每笔成交一次）。
        关键字段见 3.5.2 实现注释中的 XtTrade 说明。
        大单被拆成多笔成交时，每笔独立回调。
        """

    def on_order_error(self, order_error) -> None:
        """
        委托报送失败（柜台前置拒绝）。
        注意：order_id 可能为 -1（柜台未分配编号直接拒绝）。
        """

    def on_cancel_error(self, cancel_error) -> None:
        """
        撤单请求被拒绝（委托已成交、委托不存在等原因）。
        """


回调触发时序示例（限价买单完整生命周期）：
下单
order_stock_async()
返回
order_id = 123456
│
├─ on_stock_order(order_status=PENDING)  # 报送到柜台
├─ on_stock_order(order_status=SUBMITTED)  # 交易所受理
│
│   （等待成交）
│
├─ on_stock_trade(traded_volume=50)  # 部分成交
├─ on_stock_order(order_status=PARTIALLY_FILLED, traded_volume=50)
│
├─ on_stock_trade(traded_volume=50)  # 再次成交
└─ on_stock_order(order_status=FILLED, traded_volume=100)

撤单
cancel_order_stock_async(order_id=123456)
├─ on_stock_order(order_status=CANCELING)
└─ on_stock_order(order_status=CANCELLED)  # 或 on_cancel_error（若已全成）
```

## 附录 B：常见问题

Q：xtquant 回调在哪个线程？
A：xtquant 内部线程，非主线程。RedisBridge 的写操作需使用线程安全的同步
`redis.Redis` 连接，不使用 asyncio。`account_hub.apply_*()` 内部有

`threading.Lock` 保护内存。

Q：`trader.subscribe(account)` 必须调用吗？
A：必须。xtquant 要求显式订阅账户才会触发 `on_stock_order`、`on_stock_trade` 等委
托类回调。遗漏此调用是最常见的回调不触发 bug。
Q：`order_stock_async` 返回 -1 代表什么？
A：-1 表示订单未被柜台分配编号就被前置拒绝（如账户未登录、代码错误等）。此时
`on_order_error` 的 `order_id` 也为 -1，需要靠 `order_remark` 中的 `req_id` 关联原
始请求。
Q：QMT-Server 是否需要运行在 MiniQMT 同一台机器？
A：xtquant 通过 userdata_mini 路径与本机 MiniQMT 进程通信（IPC），必须在同一台
Windows 机器上运行。
Q：Redis 密码如何安全传递给 Windows 端？
A：写入 `config.secret.json`，该文件不纳入版本控制，手工拷贝到目标机器。
Q：A 股 T+1 限制如何处理？
A：持仓数据中 `can_use_volume` 字段即为 T+1 后可用数量。Alpha 前端显示时需区分"持
仓数量"和"可用数量"。
Q：在线调试页面如何防止误操作下真实委托？
A：下单类接口（trade/*）在调试页面增加二次确认弹窗，显示完整委托信息。建议正式环境
通过角色权限控制，仅超级管理员可访问此页面。
文档结束。待用户审阅通过后，开始编码实现。
