@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ============================================
echo  AI-Bid-Agent 启动中...
echo  页面: http://127.0.0.1:8000/
echo  停止: 在本窗口按 Ctrl+C，或直接关闭本窗口
echo ============================================

REM 先起服务，2秒后自动打开浏览器
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000/"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo 服务已停止。
pause
