@echo off
chcp 65001 >nul
title 创作工作台 · 工作台服务重启
REM ============================================================
REM 创作工作台 v5.0 · 服务重启脚本（前后端分离架构）
REM 清理 8765-8785 残留实例（exe/旧 uvicorn）→ 以分离进程启动
REM 源码版 FastAPI 服务（SPA 托管 + API），窗口隐藏、随系统常驻。
REM 双击即可让 http://127.0.0.1:8765 恢复可用。
REM ============================================================
cd /d "%~dp0"

echo [1/3] 清理残留实例（exe + 旧 uvicorn）...
taskkill /f /im "创作工作台.exe" >nul 2>&1
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765,8766,8767,8768,8769,8770 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
timeout /t 2 /nobreak >nul

echo [2/3] 启动工作台服务（分离进程，本窗口关闭后服务继续运行）...
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden python -ArgumentList '-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8765' -RedirectStandardOutput 'data\uvicorn_service.log' -RedirectStandardError 'data\uvicorn_service.err.log'"

echo [3/3] 等待服务就绪...
set /a tries=0
:wait
timeout /t 1 /nobreak >nul
set /a tries+=1
curl -s -o nul http://127.0.0.1:8765/api/health 2>nul
if errorlevel 1 (
  if %tries% lss 15 goto wait
  echo [失败] 服务 15 秒内未就绪，请检查 data/workbench_error.log
  pause
  exit /b 1
)
echo [完成] 工作台已就绪：http://127.0.0.1:8765
timeout /t 2 >nul
