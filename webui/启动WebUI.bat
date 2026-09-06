@echo off
rem wm2 水印工作台 一键启动(本机 127.0.0.1:8765)
cd /d "%~dp0.."
"F:\Environment\Anaconda\envs\wm2\python.exe" webui\app.py
start "" "http://127.0.0.1:8765/"
pause
