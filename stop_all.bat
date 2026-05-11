@echo off
:: 停止两个服务
echo [INFO] Stopping QMT services...
net stop QmtTrade 2>nul
net stop QmtMarket 2>nul
echo [OK] 行情服务(:8091) 和 交易服务(:8090) 已停止
