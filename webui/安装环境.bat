@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"
title wm2 环境安装器(所有依赖装进项目目录,不改系统)

rem ---- 找现成 Python:项目 runtime\ > conda 常见位置 ----
set "PYEXE="
if exist "%ROOT%\runtime\python\python.exe" set "PYEXE=%ROOT%\runtime\python\python.exe"
if not defined PYEXE for %%P in (
  "F:\Environment\Anaconda\envs\wm2\python.exe"
  "%USERPROFILE%\anaconda3\envs\wm2\python.exe"
  "%USERPROFILE%\miniconda3\envs\wm2\python.exe"
  "C:\ProgramData\anaconda3\envs\wm2\python.exe"
  "C:\ProgramData\miniconda3\envs\wm2\python.exe"
) do if not defined PYEXE if exist "%%~P" set "PYEXE=%%~P"

if defined PYEXE goto fix

echo 没有可用的 Python。将下载 Python 3.10 内嵌版(约 11MB)到项目 runtime\ 目录,
echo 再把依赖装进 runtime\(含 torch CUDA 约 2.5GB,全程不需要 conda、不动系统设置)。
echo.
set /p GO=继续? 回车=继续 / 输入 N 退出:
if /i "%GO%"=="N" exit /b 1

echo [引导] 下载 Python 内嵌版...
mkdir "%ROOT%\runtime" 2>nul
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip' -OutFile '%ROOT%\runtime\py310.zip'"
if not exist "%ROOT%\runtime\py310.zip" (
  echo [引导] 官方源不通,换华为云镜像...
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://mirrors.huaweicloud.com/python/3.10.11/python-3.10.11-embed-amd64.zip' -OutFile '%ROOT%\runtime\py310.zip'"
)
if not exist "%ROOT%\runtime\py310.zip" (echo 两条源都下载失败,请检查网络后重试 & pause & exit /b 1)
powershell -NoProfile -Command "Expand-Archive -Force '%ROOT%\runtime\py310.zip' '%ROOT%\runtime\python'"
del "%ROOT%\runtime\py310.zip"
powershell -NoProfile -Command "(Get-Content '%ROOT%\runtime\python\python310._pth') -replace '#import site','import site' | Set-Content '%ROOT%\runtime\python\python310._pth'"
echo [引导] 安装 pip...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%ROOT%\runtime\get-pip.py'"
"%ROOT%\runtime\python\python.exe" "%ROOT%\runtime\get-pip.py" --no-warn-script-location
del "%ROOT%\runtime\get-pip.py" 2>nul
set "PYEXE=%ROOT%\runtime\python\python.exe"

:fix
echo 用 Python: %PYEXE%
set "PIP_CACHE_DIR=%ROOT%\runtime\pip_cache"
echo.
"%PYEXE%" tools\env_check.py --fix
echo.
echo 结束。以后日常使用双击 webui\启动WebUI.bat 即可;体检:双击前按住说明看 README。
pause
