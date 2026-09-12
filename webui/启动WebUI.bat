@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"
title wm2 水印工作台 http://127.0.0.1:8765

rem ---- 找 Python:webui\local_config.bat 手工指定 > 项目 runtime\ > conda 常见位置(与安装环境.bat 同序) ----
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
rem 只认 python 进程,防止别的程序恰好占用 8765 被误杀
set "IS_PY="
for /f "tokens=1" %%n in ('tasklist /FI "PID eq %OLDPID%" /NH 2^>nul') do if /i "%%n"=="python.exe" set "IS_PY=1"
if defined IS_PY goto confirm_restart
netstat -ano | findstr "LISTENING" | findstr ":8765" >nul || goto do_start
echo [X] 端口 8765 被其他程序占用(PID=%OLDPID%),不是 wm2 WebUI,为免误杀不做处理。
echo     关掉该程序,或改 webui\app.py 里的 PORT 换端口。
pause
exit /b 1

:confirm_restart
echo 检测到 wm2 WebUI 已在运行(PID=%OLDPID%,窗口可能早已关闭但进程还活着)。
set "ACT="
set /p ACT=回车=停掉旧的并重新启动 / 输入 N=直接打开浏览器用现有的:
if /i "%ACT%"=="N" (
  start "" http://127.0.0.1:8765/
  exit /b 0
)
echo 正在停止旧实例...
taskkill /PID %OLDPID% /T /F >nul 2>nul
rem 等端口真正释放再启动(最多约 10 秒),否则新进程 bind 失败、浏览器开了也是死页
set /a TRIES=0
:wait_free
ping -n 2 127.0.0.1 >nul
netstat -ano | findstr "LISTENING" | findstr ":8765" >nul
if errorlevel 1 goto do_start
set /a TRIES+=1
if %TRIES% lss 10 goto wait_free
echo [X] 旧实例 10 秒还没退出,先用 停止WebUI.bat 或任务管理器处理,再重新启动。
pause
exit /b 1

:do_start
rem 浏览器延迟打开;服务跑在本窗口前台,关窗即停(杀干净请用 停止WebUI.bat)
rem stderr -> temp file (printed on crash); stdout banner stays on console.
rem Self-heal: if app.py exits nonzero, probe `import fastapi, uvicorn` (~1s);
rem missing deps -> one fix + retry round, never loops.
rem Coverage note: app.py never imports torch (engine subprocess only), so a
rem missing torch does NOT block startup and is NOT handled here (see README).
start /min cmd /c "ping -n 5 127.0.0.1 >nul & start "" http://127.0.0.1:8765/"
set "ERRLOG=%TEMP%\wm2_start_stderr.txt"
"%PYEXE%" webui\app.py 2>"%ERRLOG%"
if not errorlevel 1 goto serve_done
set "RCCODE=%errorlevel%"
echo.
echo [!] WebUI 进程异常退出,退出码 %RCCODE%,诊断原因中...
"%PYEXE%" -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 goto need_fix
echo [X] 依赖完好,不是缺包能解决的问题。常见原因:8765 被抢 / app.py 报错 / 缺项目文件。原始错误输出:
type "%ERRLOG%"
echo     照上面输出排查;看不懂就把这段原样发出来问。
pause
exit /b 1

:need_fix
rem Safety valve: env_check only installs into project envs (runtime\python or conda env wm2).
set "PROJENV="
echo %PYEXE% | findstr /i /c:"envs\wm2" >nul && set "PROJENV=1"
echo %PYEXE% | findstr /i /c:"runtime\python" >nul && set "PROJENV=1"
if not defined PROJENV goto fix_manual
echo [!] 诊断:fastapi/uvicorn 导入失败,是依赖缺失/损坏。
set "ACT="
set /p ACT=自动修复? 回车=修复,可能要几分钟 / 输入 N=只看指引: 
if /i "%ACT%"=="N" goto fix_manual
"%PYEXE%" tools\env_check.py --fix
echo.
echo 正在重试启动,最多自愈一轮,再失败就停...
rem Re-open the browser only after the port is really listening (the first delayed tab is a dead page).
start /min cmd /c "for /l %%i in (1,1,30) do (netstat -ano | findstr LISTENING | findstr :8765 >nul && start "" http://127.0.0.1:8765/ && exit & ping -n 3 127.0.0.1 >nul)"
"%PYEXE%" webui\app.py 2>"%ERRLOG%"
if not errorlevel 1 goto serve_done
echo.
echo [X] 修复后重试仍然失败。原始错误输出:
type "%ERRLOG%"
echo     不再自动重试。可手动重跑 python tools\env_check.py --fix 后再启动,或照上面输出排查。
pause
exit /b 1

:fix_manual
echo     手动指引:双击 webui\安装环境.bat 把依赖装进项目内环境;系统 Python 会被 env_check 拒绝安装,必须先跑安装环境.bat。原始错误输出:
type "%ERRLOG%"
pause
exit /b 1

:serve_done
pause
exit /b 0

:check
if not defined PYEXE (
  echo [X] 没找到 Python,请先运行 webui\安装环境.bat
  exit /b 1
)
"%PYEXE%" tools\env_check.py %2 %3
exit /b %errorlevel%
