@echo off
title 创作工作台
cd /d "%~dp0"
echo ================================================
echo   创作工作台 正在启动...
echo   桌面窗口即将弹出，关闭窗口即可退出
echo ================================================
if exist "%~dp0创作工作台.exe" (
    "%~dp0创作工作台.exe"
    exit /b
)
echo.
echo   未找到 创作工作台.exe，改用源码模式启动（需已装 Python 依赖）...
set PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
"%PY%" run_workbench.py
if errorlevel 1 (
    echo.
    echo   源码模式启动失败：请先安装依赖
    echo       pip install -r requirements.txt
    echo   或重新打包：pyinstaller workbench.spec --noconfirm --clean
    pause
)
