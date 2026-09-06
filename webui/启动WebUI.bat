@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"
title wm2 水印工作台 http://127.0.0.1:8765

rem ---- 找 Python:项目 runtime\ > webui\local_config.bat 手工指定 > conda 常见位置 ----
set "PYEXE="
if exist "%ROOT%\webui\local_config.bat" call "%ROOT%\webui\local_config.bat"
if defined WM2_PY if exist "%WM2_PY%" set "PYEXE=%WM2_PY%"
if not defined PYEXE if exist "%ROOT%\runtime\python\python.exe" set "PYEXE=%ROOT%\runtime\python\python.exe"
if not defined PYEXE for %%P in (
  "F:\Environment\Anaconda\envs\wm2\python.exe"
  "%USERPROFILE%\anaconda3\envs\wm2\python.exe"
  "%USERPROFILE%\miniconda3\envs\wm2\python.exe"
  "C:\ProgramData\anaconda3\envs\wm2\python.exe"
  "C:\ProgramData\miniconda3\envs\wm2\python.exe"
) do if not defined PYEXE if exist "%%~P" set "PYEXE=%%~P"

if "%~1"=="--check" goto check
if defined PYEXE goto launch

echo [X] 没找到可用的 Python。
echo     双击 webui\安装环境.bat —— 它会把 Python 和依赖全部下载装进项目目录,
echo     或手动新建 webui\local_config.bat 写一行: set "WM2_PY=你的python.exe完整路径"
pause
exit /b 1

:launch
rem 已有实例在跑?给选择,避免端口冲突报错
set "OLDPID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8765"') do set "OLDPID=%%a"
if not defined OLDPID goto do_start
echo 检测到 wm2 WebUI 已在运行(PID=%OLDPID%,窗口可能早已关闭但进程还活着)。
set "ACT="
set /p ACT=回车=停掉旧的并重新启动 / 输入 N=直接打开浏览器用现有的:
if /i "%ACT%"=="N" (
  start "" http://127.0.0.1:8765/
  exit /b 0
)
echo 正在停止旧实例...
taskkill /PID %OLDPID% /T /F >nul 2>nul
ping -n 3 127.0.0.1 >nul

:do_start
rem 浏览器延迟打开;服务跑在本窗口前台,关窗即停(杀干净请用 停止WebUI.bat)
start /min cmd /c "ping -n 5 127.0.0.1 >nul & start "" http://127.0.0.1:8765/"
"%PYEXE%" webui\app.py
pause
exit /b 0

:check
if not defined PYEXE (
  echo [X] 没找到 Python,请先运行 webui\安装环境.bat
  exit /b 1
)
"%PYEXE%" tools\env_check.py %2 %3
exit /b 0
