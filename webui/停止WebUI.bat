@echo off
chcp 65001 >nul
setlocal
title wm2 停止 WebUI 服务

set "OLDPID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8765"') do set "OLDPID=%%a"
if not defined OLDPID goto idle
rem 只认 python 进程,防止别的程序恰好占用 8765 被误杀
set "IS_PY="
for /f "tokens=1" %%n in ('tasklist /FI "PID eq %OLDPID%" /NH 2^>nul') do if /i "%%n"=="python.exe" set "IS_PY=1"
if defined IS_PY goto do_kill
echo [X] 端口 8765 被其他程序占用(PID=%OLDPID%),不是 wm2 WebUI,为免误杀不停止,请手动确认处理。
pause
exit /b 1

:do_kill
echo 发现服务进程 PID=%OLDPID%,正在停止,连带它的模型子进程...
taskkill /PID %OLDPID% /T /F >nul 2>nul
ping -n 3 127.0.0.1 >nul
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul
if %errorlevel%==0 goto fail
echo [√] 服务已停止,端口 8765 已释放
pause
exit /b 0

:fail
echo [X] 停止失败,请在任务管理器里手动结束 PID=%OLDPID%
pause
exit /b 1

:idle
echo 没有发现正在运行的 wm2 WebUI 服务,端口 8765 空闲
pause
exit /b 0
