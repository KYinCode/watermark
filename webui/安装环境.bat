@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"
title wm2 环境安装器(所有依赖装进项目目录,不改系统)

rem ---- 找现成 Python:local_config.bat 手工指定 > 项目 runtime\ > conda 常见位置(与启动WebUI.bat 同序) ----
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
if not exist "%ROOT%\runtime\py310.zip" goto py_ask
goto py_ok

:py_ask
echo 两条自动线路都失败了。若你开着代理(Clash/v2rayN 等),把地址抄进来,例: http://127.0.0.1:7890
set "PX="
set /p PX=不知道就直接回车,给你手动办法:
if "%PX%"=="" goto py_manual
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Proxy '%PX%' -Uri 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip' -OutFile '%ROOT%\runtime\py310.zip'"
if exist "%ROOT%\runtime\py310.zip" goto py_ok
echo 代理线路也没成功。

:py_manual
echo 手动办法:浏览器下载 https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip
echo 放到 %ROOT%\runtime\py310.zip ,然后重新双击本脚本
pause
exit /b 1

:py_ok
powershell -NoProfile -Command "Expand-Archive -Force '%ROOT%\runtime\py310.zip' '%ROOT%\runtime\python'"
del "%ROOT%\runtime\py310.zip"
powershell -NoProfile -Command "(Get-Content '%ROOT%\runtime\python\python310._pth') -replace '#import site','import site' | Set-Content '%ROOT%\runtime\python\python310._pth'"
echo [引导] 安装 pip...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%ROOT%\runtime\get-pip.py'"
if not exist "%ROOT%\runtime\get-pip.py" powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://mirrors.aliyun.com/pypi/get-pip.py' -OutFile '%ROOT%\runtime\get-pip.py'"
if exist "%ROOT%\runtime\get-pip.py" goto pip_ok
echo [引导] get-pip.py 官方源和阿里云镜像都没通。若开着代理,把地址抄进来,例: http://127.0.0.1:7890
set "PX2="
set /p PX2=不知道/没代理就直接回车,给你手动办法:
if "%PX2%"=="" goto pip_manual
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Proxy '%PX2%' -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%ROOT%\runtime\get-pip.py'"
if exist "%ROOT%\runtime\get-pip.py" goto pip_ok

:pip_manual
echo [X] 没拿到 get-pip.py。手动办法:浏览器下载 https://bootstrap.pypa.io/get-pip.py
echo     放到 %ROOT%\runtime\get-pip.py,然后重新双击本脚本
pause
exit /b 1

:pip_ok
"%ROOT%\runtime\python\python.exe" "%ROOT%\runtime\get-pip.py" --no-warn-script-location
del "%ROOT%\runtime\get-pip.py" 2>nul
set "PYEXE=%ROOT%\runtime\python\python.exe"

:fix
echo 用 Python: %PYEXE%
set "PIP_CACHE_DIR=%ROOT%\runtime\pip_cache"
echo.
"%PYEXE%" tools\env_check.py --fix
echo.
echo 结束。以后日常使用双击 webui\启动WebUI.bat 即可;体检:命令行跑 python tools\env_check.py(说明见 README)。
pause
