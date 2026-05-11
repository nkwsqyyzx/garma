@echo off
:: 行情服务启动脚本
set PYTHON=C:\Users\admin\miniconda3\envs\qmt\python.exe
set BASE=%~dp0

echo [INFO] Starting qmt-market service...
%PYTHON% "%BASE%qmt-market\main.py"
