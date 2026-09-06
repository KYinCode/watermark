# -*- coding: utf-8 -*-
"""wm2 环境体检/修复一体脚本(项目搬家、换新电脑用它;项目里唯一的逻辑脚本,两个 .bat 只是双击壳)。

原则:所有可下载的依赖都装进项目目录(ffmpeg -> bin\\,Python 内嵌版 -> runtime\\,
pip 装进当前解释器对应的 env),不改系统、不需要管理员、不需要 conda。

用法(用任意一个能跑的 python 执行):
  python tools/env_check.py           体检:逐项报告,坏了给修法
  python tools/env_check.py --fix     体检 + 自动修复(缺 ffmpeg 下载到 bin\\,缺 pip 包补装)
  python tools/env_check.py --fix --yes   修复过程不再逐项确认

安全阀:--fix 只往"项目内环境"里装东西(runtime\\python 或名为 wm2 的 conda env);
检测到裸系统 Python 时拒绝安装,防止污染系统环境。
"""
import importlib.util
import io
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
RUNTIME_PY = PROJ / "runtime" / "python" / "python.exe"
BIN = PROJ / "bin"
CKPT = PROJ / "third_party" / "watermark-anything" / "checkpoints" / "wam_mit.pth"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
# 国内镜像(GitHub 加速前缀,这类服务时好时坏,所以备多个自动轮试;2026-09-06 实测 gh-proxy.com 活)
FFMPEG_MIRRORS = [
    "https://gh-proxy.com/" + FFMPEG_URL,
    "https://ghfast.top/" + FFMPEG_URL,
    "https://ghproxy.net/" + FFMPEG_URL,
]
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
WM_PROXY = os.environ.get("WM_PROXY")  # 可选的手工指定代理(local_config.bat 里 set),没设就不存在
FF_BASES = (r"C:\Environment\FFmpeg\FFmpeg_Builds\bin",  # 本机既有安装(兜底)
            r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin")
PIP_CORE = [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("python-multipart", "multipart"),
            ("omegaconf", "omegaconf"), ("einops", "einops"), ("timm", "timm"),
            ("opencv-python", "cv2"), ("lpips", "lpips"), ("scikit-image", "skimage"),
            ("numpy", "numpy"), ("pillow", "PIL")]
OK, BAD = "[√]", "[✗]"


def resolve_ffmpeg(name="ffmpeg.exe"):
    """与 src/common.py 同一套解析顺序:WM_FFMPEG -> 项目 bin\\ -> PATH -> 常见安装位置"""
    env = os.environ.get("WM_FFMPEG")
    if env:
        p = Path(env)
        cand = p / name if p.is_dir() else p
        if cand.exists():
            return str(cand)
    if (BIN / name).exists():
        return str(BIN / name)
    which = shutil.which(name)
    if which:
        return which
    for base in FF_BASES:
        if (Path(base) / name).exists():
            return str(Path(base) / name)
    return None


def py_kind():
    """当前解释器属于哪类环境(决定 --fix 能不能往里装)"""
    if Path(sys.executable).as_posix().lower() == RUNTIME_PY.as_posix().lower():
        return "runtime"
    if "/envs/wm2/" in Path(sys.executable).as_posix().lower():
        return "conda-wm2"
    return "other"


def pip(*args):
    r = subprocess.run([sys.executable, "-m", "pip", *args])
    return r.returncode == 0


def dl(url, proxy=None, timeout=30):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    op = urllib.request.build_opener(*handlers)
    r = op.open(url, timeout=timeout)
    total = int(r.headers.get("Content-Length") or 0)
    buf, got = io.BytesIO(), 0
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        buf.write(chunk)
        got += len(chunk)
        if total:
            print(f"\r      {got / 2**20:.0f}/{total / 2**20:.0f} MB", end="", flush=True)
    print()
    return buf.getvalue()


def detect_proxy():
    """探测本机代理:WM_PROXY 手工指定优先(不再探测直接信);否则探测常见默认口 7897。
    活着返回代理地址,死了返回 None。"""
    if WM_PROXY:
        return WM_PROXY
    try:
        socket.create_connection(("127.0.0.1", 7897), timeout=1).close()
        return "http://127.0.0.1:7897"
    except OSError:
        return None


def fetch_ffmpeg():
    """下载 ffmpeg 静态版到项目 bin\\。
    线路顺序:代理活着优先走代理(通常最快) -> 死了走国内镜像轮试 -> 镜像全挂现场问端口(绝不记住)。"""
    BIN.mkdir(exist_ok=True)
    data, via = None, ""
    proxy = detect_proxy()
    if proxy:
        print(f"      检测到本机代理 {proxy},优先走代理(通常比镜像快)...")
        try:
            data = dl(FFMPEG_URL, proxy=proxy, timeout=120)
            via = f"代理 {proxy}"
        except Exception as e:
            print(f"      代理线路失败: {e},改走国内镜像...")
    else:
        print("      未检测到本机代理,走国内镜像...")
    if data is None:
        for label, url in [("国内镜像1", FFMPEG_MIRRORS[0]), ("国内镜像2", FFMPEG_MIRRORS[1]),
                           ("国内镜像3", FFMPEG_MIRRORS[2]), ("直连", FFMPEG_URL)]:
            print(f"      尝试{label}...")
            try:
                data = dl(url)
                via = label
                break
            except Exception as e:
                print(f"      {label} 失败: {e}")
    wrong = 0
    while data is None:
        addr = ask_proxy()
        if not addr:
            print(f"      已放弃自动下载。手动办法:任何能上网的机器下载 ffmpeg win64 版,"
                  f"把 ffmpeg.exe 和 ffprobe.exe 放进 {BIN},再重跑体检即可")
            return False
        try:
            data = dl(FFMPEG_URL, proxy=addr, timeout=120)
            via = f"你输入的代理 {addr}"
        except Exception as e:
            wrong += 1
            print(f"      这个地址不行: {e}")
            if wrong >= 3:
                print("      试了 3 次都没成,先放弃自动下载(上面的手动办法最稳)")
                return False
    print(f"      通过[{via}]下载完成 {len(data) / 2**20:.0f} MB,解压中...")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            member = next((m for m in names if m.endswith("/" + exe) or m == exe), None)
            if member is None:
                print(f"      压缩包里没找到 {exe},格式可能变了,请手动处理")
                return False
            (BIN / exe).write_bytes(z.read(member))
    print(f"      {OK} ffmpeg 就位: {BIN / 'ffmpeg.exe'}")
    return True


def ask_proxy():
    print("      镜像和直连都失败了。最后试你的代理:")
    print("      如果你电脑开着 Clash/v2rayN 之类,把它的代理地址抄进来,例: http://127.0.0.1:7890")
    try:
        return input("      不知道/没有代理就直接回车,给你手动下载的办法: ").strip()
    except EOFError:
        return ""


def check_py():
    print(f"[1/5] Python   {OK} {sys.executable}")
    return True


def check_ffmpeg():
    ff = resolve_ffmpeg()
    if ff:
        print(f"[2/5] ffmpeg   {OK} {ff}")
        return True
    print(f"[2/5] ffmpeg   {BAD} 没找到(打水印/预览必需)")
    return False


def check_deps():
    missing = [pip_name for pip_name, mod in PIP_CORE
               if not (importlib.util.find_spec(mod)
                       or (mod == "multipart" and importlib.util.find_spec("python_multipart")))]
    torch_ok = importlib.util.find_spec("torch") is not None
    if missing or not torch_ok:
        print(f"[3/5] 依赖     {BAD} 缺: {', '.join(([] if torch_ok else ['torch']) + missing)}")
        return False
    import torch
    print(f"[3/5] 依赖     {OK} torch {torch.__version__} 等 {len(PIP_CORE) + 1} 项齐")
    return True


def check_ckpt():
    if CKPT.exists():
        print(f"[4/5] 模型权重 {OK} {CKPT.name} ({CKPT.stat().st_size / 2**20:.0f} MB)")
        return True
    print(f"[4/5] 模型权重 {BAD} 缺 {CKPT}")
    print("      修法:从旧电脑把 third_party\\watermark-anything\\checkpoints\\ 整个文件夹拷过来")
    return False


def check_gpu():
    try:
        import torch
        ok = torch.cuda.is_available()
        name = torch.cuda.get_device_name(0) if ok else ""
    except Exception:
        ok, name = False, ""
    if ok:
        print(f"[5/5] GPU      {OK} {name}")
    else:
        print(f"[5/5] GPU      {BAD} CUDA 不可用(打水印需要 NVIDIA 显卡 + 装的是 cu124 版 torch)")
    return ok


def fix_ffmpeg():
    print("  修复: 下载 ffmpeg 到项目 bin\\ ?")
    if not confirm():
        print("      跳过。也可以自己装好后,在 webui\\local_config.bat 写: set \"WM_FFMPEG=你的ffmpeg目录\"")
        return
    fetch_ffmpeg()


def fix_deps():
    if py_kind() == "other":
        print("  修复中止: 当前是系统 Python,不往里装。请先双击 webui\\安装环境.bat 生成项目内环境")
        return
    if importlib.util.find_spec("torch") is None:
        print("      pip 装 torch cu124(约 2.5GB,耐心)...")
        if not pip("install", "torch==2.5.1", "torchvision==0.20.1",
                   "--index-url", "https://download.pytorch.org/whl/cu124"):
            print("      torch 官方源没装上(网络原因);重跑本脚本可续,或配好代理后再试")
    print("      pip 补齐其余依赖...")
    if not pip("install", *[n for n, _ in PIP_CORE]):
        print(f"      默认 PyPI 源失败,换清华镜像重试...")
        pip("install", "-i", PIP_MIRROR, *[n for n, _ in PIP_CORE])


def confirm():
    if "--yes" in sys.argv:
        return True
    try:
        return input("      执行? 回车=是 / N=跳过: ").strip().lower() != "n"
    except EOFError:
        return False


def main():
    fixes = []
    print(f"=== wm2 环境体检(项目: {PROJ}) ===")
    check_py()
    if not check_ffmpeg():
        fixes.append(("ffmpeg", fix_ffmpeg))
    if not check_deps():
        fixes.append(("依赖", fix_deps))
    check_ckpt()  # 只能给人工修法,不进自动修复
    check_gpu()
    print("=== 结论 ===")
    if not fixes:
        print("环境完好。启动:双击 webui\\启动WebUI.bat")
        return
    print(f"需修复 {len(fixes)} 项: {', '.join(n for n, _ in fixes)}")
    if "--fix" not in sys.argv:
        print("自动修复: python tools/env_check.py --fix   (或双击 webui\\安装环境.bat)")
        return
    for name, fn in fixes:
        print(f"--- 修复 {name} ---")
        fn()
    print("=== 复检 ===")
    left = []
    if not check_ffmpeg():
        left.append("ffmpeg")
    if not check_deps():
        left.append("依赖")
    print("全部就绪,双击 webui\\启动WebUI.bat 即可" if not left else f"仍未解决: {', '.join(left)}")


if __name__ == "__main__":
    main()
