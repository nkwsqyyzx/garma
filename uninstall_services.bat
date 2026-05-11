@echo off
:: 卸载两个 Windows 服务
set NSSM=C:\nssm\nssm.exe

echo [INFO] Stopping and removing QmtTrade...
net stop QmtTrade 2>nul
%NSSM% remove QmtTrade confirm

echo [INFO] Stopping and removing QmtMarket...
net stop QmtMarket 2>nul
%NSSM% remove QmtMarket confirm

echo [OK] 两个服务已卸载
pause
