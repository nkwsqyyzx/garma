@echo off
:: 交易服务启动脚本
set PYTHON=C:\Users\admin\miniconda3\envs\qmt\python.exe
set BASE=%~dp0

echo [INFO] Starting qmt-trade service...
%PYTHON% "%BASE%qmt-trade\main.py"
