#!/usr/bin/env bash
# wm2 项目统一环境(每个 Bash 调用 source 本文件)。
# 路径全部自适应:项目搬家/换机不用改本文件。AI 会话用;双击用户走 webui\*.bat。
_PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJ="$_PROJ_DIR"

# 代理:尊重已设好的代理 > WM_PROXY 环境变量 > 探测本机 7897;都没有就走直连
if [ -z "$HTTP_PROXY" ] && [ -z "$http_proxy" ]; then
  _px="${WM_PROXY:-http://127.0.0.1:7897}"
  _port="$(echo "$_px" | grep -oE '[0-9]+$')"
  if timeout 1 bash -c "</dev/tcp/127.0.0.1/$_port" 2>/dev/null; then
    export HTTP_PROXY="$_px" HTTPS_PROXY="$_px" http_proxy="$_px" https_proxy="$_px"
    export NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
  fi
  unset _px _port
fi

# ffmpeg/ffprobe:项目 bin\ 优先,回退常见安装位置(与 src/common.py 解析链同序)
for _ff in "$_PROJ_DIR/bin" "/c/Environment/FFmpeg/FFmpeg_Builds/bin" "/c/ffmpeg/bin"; do
  [ -x "$_ff/ffmpeg.exe" ] && export PATH="$_ff:$PATH" && break
done

# HF/torch 模型缓存留在项目内
export HF_HOME="$_PROJ_DIR/hf_home"
export TORCH_HOME="$_PROJ_DIR/torch_home"

# python(wm2 环境):项目 runtime\ > 常见 conda 布局逐个探测
for _py in "$_PROJ_DIR/runtime/python/python.exe" \
           "/f/Environment/Anaconda/envs/wm2/python.exe" \
           "$HOME/anaconda3/envs/wm2/python.exe" "$HOME/miniconda3/envs/wm2/python.exe" \
           "/c/ProgramData/anaconda3/envs/wm2/python.exe" "/c/ProgramData/miniconda3/envs/wm2/python.exe"; do
  [ -f "$_py" ] && export PY="$_py" && break
done
export PY="${PY:-python}"

unset _PROJ_DIR _ff _py
