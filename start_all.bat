@echo off
:: 同时启动行情和交易服务
set PYTHON=C:\Users\admin\miniconda3\envs\qmt\python.exe
set BASE=%~dp0

echo [INFO] Starting qmt-market service...
start "QMT Market" %PYTHON% "%BASE%qmt-market\main.py"

echo [INFO] Starting qmt-trade service...
start "QMT Trade" %PYTHON% "%BASE%qmt-trade\main.py"

echo [OK] 行情服务(:8091) 和 交易服务(:8090) 已启动
