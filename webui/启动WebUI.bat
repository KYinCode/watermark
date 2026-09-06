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
rem 浏览器延迟打开;服务跑在本窗口前台,关窗即停
start /min cmd /c "timeout /t 4 /nobreak >nul & start "" http://127.0.0.1:8765/"
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
