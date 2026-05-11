@echo off
:: NSSM 注册两个 Windows 服务
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
