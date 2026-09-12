# -*- coding: utf-8 -*-
"""wm2 环境体检/修复一体脚本(项目搬家、换新电脑用它;项目里唯一的逻辑脚本,两个 .bat 只是双击壳)。

原则:所有可下载的依赖都装进项目目录(ffmpeg -> bin\\,Python 内嵌版 -> runtime\\,
pip 装进当前解释器对应的 env),不改系统、不需要管理员、不需要 conda。

用法(用任意一个能跑的 python 执行):
  python tools/env_check.py           体检:逐项报告,坏了给修法(退出码 0=全绿 / 1=有失败项)
  python tools/env_check.py --fix     体检 + 自动修复(缺 ffmpeg 下载到 bin\\,缺 pip 包补装,
                                      缺/坏模型权重 wam_mit.pth 自动下载,修完复检并给退出码)
  python tools/env_check.py --fix --yes   修复过程不再逐项确认

安全阀:--fix 只往"项目内环境"里装东西(runtime\\python 或名为 wm2 的 conda env);
检测到裸系统 Python 时拒绝安装,防止污染系统环境。
"""
import hashlib
import http.client
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
RUNTIME_PY = PROJ / "runtime" / "python" / "python.exe"
BIN = PROJ / "bin"
CKPT = PROJ / "third_party" / "watermark-anything" / "checkpoints" / "wam_mit.pth"
PARAMS = PROJ / "third_party" / "watermark-anything" / "checkpoints" / "params.json"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
# 模型权重官方 CDN(国内直连基本不可达、没有任何镜像,不要硬凑保底链,见 fetch_weights)
WM_URL = "https://dl.fbaipublicfiles.com/watermark_anything/wam_mit.pth"
# 完整性写死:日常体检只查精确字节数(stat 零成本);sha256 只在 --fix 下载完成那一刻算一次
WM_SHA256 = "90ef232384e023bd63245eb0c131abd69d2afc7b8f17a71ccedceb542bf009e2"
WM_BYTES = 377825938
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
            ("numpy", "numpy"), ("pillow", "PIL"),
            ("pycocotools", "pycocotools"), ("scikit-learn", "sklearn")]  # 后两个只有 WAM 训练路径 import,为项目完整性与官方 requirements 对齐
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


class DlIncomplete(IOError):
    """下载字节数不够(连接被中途掐断,read 静默短读):可断点续传"""


def dl(url, dest, proxy=None, timeout=30):
    """流式下载 url 到 dest:先写 dest.part,完成原子改名(半成品永远不会冒充成品)。
    已有"同一 URL 留下的".part 且服务器支持 Range 时断点续传,否则从头下;
    连接中断抛异常,已下字节保留在 .part 里等续传(重试策略归 dl_resumable 管)。"""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    marker = dest.with_name(dest.name + ".part.url")
    if marker.exists() and marker.read_text(encoding="utf-8", errors="ignore") != url:
        part.unlink(missing_ok=True)  # .part 是别的 URL 留下的,续传会拼出错内容(续传按 URL 生效)
    marker.write_text(url, encoding="utf-8")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    op = urllib.request.build_opener(*handlers)
    for fresh in (False, True):
        got = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url)
        if got and not fresh:
            req.add_header("Range", f"bytes={got}-")
        try:
            r = op.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 416 and got and not fresh:
                # 断点越界:要么上次死在"下载完但还没改名"(Content-Range 总长对得上 -> 直接改名收货),
                # 要么 .part 与真实文件对不上 -> 删掉整包重来,不赌
                cr = (e.headers.get("Content-Range") or "").rsplit("/", 1)[-1].strip()
                if cr.isdigit() and int(cr) == got:
                    os.replace(part, dest)
                    marker.unlink(missing_ok=True)
                    return
                part.unlink(missing_ok=True)
                continue
            raise
        resume = bool(got) and r.status == 206
        if not resume:
            got = 0  # 服务器不支持 Range,退回全量下载
        total = int(r.headers.get("Content-Length") or 0) + got
        with open(part, "ab" if resume else "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    print(f"\r      {got / 2**20:.0f}/{total / 2**20:.0f} MB", end="", flush=True)
        print()
        if total and got != total:
            raise DlIncomplete(f"连接在 {got / 2**20:.0f}/{total / 2**20:.0f} MB 处中断")
        os.replace(part, dest)
        marker.unlink(missing_ok=True)
        return


def _interrupted(e):
    """连接中断类错误(值得续传重试);404 之类直接判死,续传没意义"""
    if isinstance(e, DlIncomplete):
        return True
    if isinstance(e, urllib.error.HTTPError):
        return e.code in (408, 429, 500, 502, 503, 504)
    if isinstance(e, (http.client.IncompleteRead, ConnectionError, TimeoutError)):
        return True
    if isinstance(e, urllib.error.URLError):
        return _interrupted(e.reason) if isinstance(e.reason, Exception) else True
    return False


def dl_resumable(url, dest, proxy=None, timeout=30, retries=5):
    """带断点续传的下载:连接中断且已有断点时最多续传 5 次(与给 pip 定的 --resume-retries 对齐);
    一字节都没下到就失败的不空转重试,交给调用方换线路。"""
    part = Path(str(dest) + ".part")
    marker = Path(str(dest) + ".part.url")
    for i in range(retries + 1):
        try:
            dl(url, dest, proxy=proxy, timeout=timeout)
            return
        except Exception as e:
            if i >= retries or not _interrupted(e) or not part.exists():
                if not part.exists():
                    marker.unlink(missing_ok=True)  # 一字节没下到的失败,不留孤儿 marker
                raise
            print(f"\n      连接中断({e}),3 秒后从断点续传(剩 {retries - i} 次)...")
            time.sleep(3)


_PROXY_MEMO = {}  # 单次运行内存共享的代理信息,进程退出即忘;绝不写盘(用户定的"绝不记住"原则)


def detect_proxy():
    """探测本机代理:WM_PROXY 手工指定优先(不再探测直接信);否则依次试常见默认口。
    活着返回代理地址,死了返回 None。探测结果单次运行内缓存(ffmpeg 下载和 pip 安装不重复探测)。"""
    if "detect" in _PROXY_MEMO:
        return _PROXY_MEMO["detect"]
    found = None
    if WM_PROXY:
        found = WM_PROXY
    else:
        for port in (7890, 7897):  # Clash 系默认 7890,Clash Verge 默认 7897
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                found = f"http://127.0.0.1:{port}"
                break
            except OSError:
                continue
    _PROXY_MEMO["detect"] = found
    return found


def fetch_ffmpeg():
    """下载 ffmpeg 静态版到项目 bin\\。
    线路顺序:直连(覆盖 TUN 模式/海外/系统代理,快) -> 本机代理探测 -> 国内镜像轮试
    -> 问端口(最多 3 次,绝不记住) -> 手动方案。流式写 .part、断点续传(见 dl/dl_resumable)。"""
    BIN.mkdir(exist_ok=True)
    zip_path = BIN / "ffmpeg.zip"
    via = None
    print("      尝试直连...")
    try:
        dl_resumable(FFMPEG_URL, zip_path, timeout=20)
        via = "直连"
    except Exception as e:
        print(f"      直连失败: {e}")
    if via is None:
        proxy = detect_proxy()
        if proxy:
            print(f"      检测到本机代理 {proxy},走代理...")
            try:
                dl_resumable(FFMPEG_URL, zip_path, proxy=proxy, timeout=120)
                via = f"代理 {proxy}"
            except Exception as e:
                print(f"      代理线路失败: {e},改走国内镜像...")
        else:
            print("      未检测到本机代理,走国内镜像...")
    if via is None:
        for i, url in enumerate(FFMPEG_MIRRORS, 1):
            print(f"      尝试国内镜像{i}...")
            try:
                dl_resumable(url, zip_path)
                via = f"国内镜像{i}"
                break
            except Exception as e:
                print(f"      国内镜像{i} 失败: {e}")
    wrong = 0
    while via is None:
        addr = ask_proxy(fresh=wrong > 0)
        if not addr:
            print(f"      已放弃自动下载。手动办法:任何能上网的机器下载 ffmpeg win64 版,"
                  f"把 ffmpeg.exe 和 ffprobe.exe 放进 {BIN},再重跑体检即可")
            return False
        try:
            dl_resumable(FFMPEG_URL, zip_path, proxy=addr, timeout=120)
            via = f"你输入的代理 {addr}"
        except Exception as e:
            wrong += 1
            print(f"      这个地址不行: {e}")
            if wrong >= 3:
                print("      试了 3 次都没成,先放弃自动下载(上面的手动办法最稳)")
                return False
    print(f"      通过[{via}]下载完成 {zip_path.stat().st_size / 2**20:.0f} MB,解压中...")
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            for exe in ("ffmpeg.exe", "ffprobe.exe"):
                member = next((m for m in names if m.endswith("/" + exe) or m == exe), None)
                if member is None:
                    print(f"      压缩包里没找到 {exe},格式可能变了,请手动处理")
                    return False
                try:
                    (BIN / exe).write_bytes(z.read(member))
                except PermissionError:
                    print(f"      {exe} 被占用(WebUI 正开着?),关掉 WebUI 后重跑 --fix 即可")
                    return False
    finally:
        zip_path.unlink(missing_ok=True)
    print(f"      {OK} ffmpeg 就位: {BIN / 'ffmpeg.exe'}")
    return True


def ask_proxy(fresh=False):
    """问用户要代理地址;同一进程内问到的地址直接复用,不重复问同一件事(单次运行内存共享,绝不落盘)。
    fresh=True 强制重新问(重试循环里用户可能要改地址)。"""
    if not fresh and _PROXY_MEMO.get("asked") is not None:
        return _PROXY_MEMO["asked"]
    print("      自动线路都失败了。最后试你的代理:")
    print("      如果你电脑开着 Clash/v2rayN 之类,把它的代理地址抄进来,例: http://127.0.0.1:7890")
    try:
        addr = input("      不知道/没有代理就直接回车,给你手动下载的办法: ").strip()
    except EOFError:
        addr = ""
    if addr:
        _PROXY_MEMO["asked"] = addr
    return addr


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


def torch_state():
    """torch 安装状态:None=没装;'ok'=可用;其他字符串=装了但 import 失败(含原因)"""
    if importlib.util.find_spec("torch") is None:
        return None
    try:
        import torch
        torch.__version__
        return "ok"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def check_deps():
    # 逐包真 import(非 find_spec):半损坏的包(DLL 加载失败等)只有 import 才现形。
    # 全家桶 import 约 10-20 秒,体检是低频操作,可接受;torch 有 torch_state() 不重复。
    missing, broken = [], []
    for pip_name, mod in PIP_CORE:
        try:
            importlib.import_module(mod)
            continue
        except ImportError as e:
            # python-multipart 新旧版模块名不同,保留双名回退
            if mod == "multipart" and importlib.util.find_spec("python_multipart") is not None:
                continue
            if importlib.util.find_spec(mod) is None:
                missing.append(pip_name)
            else:  # 找得到但 import 失败:DLL 损坏之类
                broken.append(f"{pip_name}(装了但起不来: {e})")
        except Exception as e:
            broken.append(f"{pip_name}(装了但损坏: {type(e).__name__}: {e})")
    bad = missing + broken
    ts = torch_state()
    if ts is None:
        bad.append("torch")
    elif ts != "ok":
        bad.append(f"torch(已装但损坏: {ts})")
    if bad:
        print(f"[3/5] 依赖     {BAD} 缺/坏: {', '.join(bad)}")
        if broken:
            print("      修法:python -m pip install --force-reinstall --no-deps 对应包名(重装损坏包)")
        return False
    import torch
    print(f"[3/5] 依赖     {OK} torch {torch.__version__} 等 {len(PIP_CORE) + 1} 项齐")
    return True


def check_ckpt():
    """模型文件体检:params.json(已入库的小配置)+ wam_mit.pth。
    权重日常只查精确字节数(stat 零成本);sha256 不进日常检查,只在 --fix 下载完成那刻算一次
    (360MB 全量哈希没必要每次付)。"""
    probs = []
    if not PARAMS.exists():
        probs.append(f"缺 params.json(load_wam 第一步就要读的官方配置,几 KB,已随仓库入库)")
    if not CKPT.exists():
        probs.append("缺 wam_mit.pth(约 360MB)")
    elif CKPT.stat().st_size != WM_BYTES:
        probs.append(f"wam_mit.pth 大小不对({CKPT.stat().st_size} 字节,应为 {WM_BYTES},多半是没下完的半成品)")
    if probs:
        print(f"[4/5] 模型权重 {BAD} {';'.join(probs)}")
        print("      修法:权重缺/坏 -> python tools/env_check.py --fix 自动下载(或旧电脑拷/官方地址下);"
              "params.json 缺 -> git pull 或从旧电脑拷")
        return False
    print(f"[4/5] 模型权重 {OK} {CKPT.name} ({CKPT.stat().st_size / 2**20:.0f} MB) + params.json")
    return True


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _wm_manual_guide():
    print(f"      手动办法:浏览器打开 {WM_URL} 下载(约 360MB),放进 {CKPT.parent};"
          f"或从旧电脑把 checkpoints\\ 整个文件夹拷过来,然后重跑体检")


def _wm_download_via_any_line():
    """权重下载线路(只有三级):直连 -> 代理(WM_PROXY 手工指定 > 探测 7890/7897) -> 问代理(3 次)。
    官方 CDN 国内直连基本不可达、没有任何镜像,不要硬凑保底链。成功返回线路标签,失败返回 None。"""
    print("      尝试直连...")
    try:
        dl_resumable(WM_URL, CKPT, timeout=20)
        return "直连"
    except Exception as e:
        print(f"      直连失败: {e}")
    proxy = detect_proxy()
    if proxy:
        print(f"      检测到本机代理 {proxy},走代理...")
        try:
            dl_resumable(WM_URL, CKPT, proxy=proxy, timeout=120)
            return f"代理 {proxy}"
        except Exception as e:
            print(f"      代理线路失败: {e}")
    else:
        print("      未检测到本机代理")
    wrong = 0
    while wrong < 3:
        addr = ask_proxy(fresh=wrong > 0)
        if not addr:
            _wm_manual_guide()
            return None
        try:
            dl_resumable(WM_URL, CKPT, proxy=addr, timeout=120)
            return f"你输入的代理 {addr}"
        except Exception as e:
            wrong += 1
            print(f"      这个地址不行: {e}")
            if wrong >= 3:
                print("      试了 3 次都没成,放弃自动下载")
                _wm_manual_guide()
    return None


def fetch_weights():
    """自动下载模型权重 wam_mit.pth(约 360MB)。
    完整性:字节数(每次体检查)+ sha256(下载完成那刻算一次);校验失败 -> 删损坏产物整包重下,
    最多 3 次尝试,超限删文件给手动指引,绝不无限循环(防镜像/代理持续吐坏内容的病态情况)。
    只自动删"本次自己下载的产物";用户手工拷的存量文件删前先 confirm。
    连接中断走 dl_resumable 断点续传;sha 不对续传无意义(内容源头错了),必须整包重来。"""
    if CKPT.exists():  # 走到这说明大小不对,不然 check_ckpt 就过了
        print(f"      现有 {CKPT.name} 大小不对({CKPT.stat().st_size} 字节,应为 {WM_BYTES}),当作坏文件处理")
        if not confirm():
            print("      跳过删除。想保留就自己核对来源,或手动换一份完整的再重跑体检")
            return
        CKPT.unlink()
    for attempt in range(1, 4):
        via = _wm_download_via_any_line()
        if via is None:
            return  # 线路全败/用户放弃,指引已在里面给过
        print(f"      通过[{via}]下载完成,校验中...")
        if CKPT.stat().st_size != WM_BYTES:
            print(f"      字节数不对: {CKPT.stat().st_size}(应为 {WM_BYTES})")
        elif _sha256_file(CKPT) == WM_SHA256:
            print(f"      {OK} 权重就位: {CKPT}(sha256 校验通过)")
            return
        else:
            print("      sha256 不匹配,内容不对(线路可能在吐坏包)")
        CKPT.unlink()  # 刚才这把是自己下的,直接删,状态归零
        if attempt < 3:
            print(f"      已删损坏产物,整包重下(第 {attempt + 1}/3 次尝试;sha 不对续传无意义)...")
    print(f"      {BAD} 连续 3 次下载内容都不对,停止重试(绝不无限循环)。")
    _wm_manual_guide()


def fix_ckpt():
    if not PARAMS.exists():
        print(f"      缺 {PARAMS}(几 KB 的官方配置文件,已随仓库入库)")
        print("      修法:git pull 拿回;或从旧电脑拷 checkpoints\\params.json(只能人工补,不自动)")
    if CKPT.exists() and CKPT.stat().st_size == WM_BYTES:
        if not PARAMS.exists():
            print("      权重本身没问题,补上 params.json 后即齐")
        return
    print("  修复: 自动下载模型权重 wam_mit.pth(约 360MB,官方 CDN,可能要代理)?")
    if not confirm():
        _wm_manual_guide()
        return
    fetch_weights()


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


def _pip_version():
    r = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True)
    m = re.search(r"pip (\d+)\.(\d+)", r.stdout or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


_PIP_RESUME = None  # 探测结果进程内缓存,不重复探测


def pip_resume_ok():
    """pip 25.1 起支持 --resume-retries(torch 2.5GB 下到一半断了能续,完整 wheel 命中
    PIP_CACHE_DIR 缓存不再下载)。老 pip 顺带升级一次;升不动就退回无续传安装,行为同旧版。"""
    global _PIP_RESUME
    if _PIP_RESUME is None:
        _PIP_RESUME = _pip_version() >= (25, 1)
        if not _PIP_RESUME:
            print("      pip 版本较老(不支持 --resume-retries 断点续传),先升级 pip...")
            if pip("install", "-U", "pip", "-i", PIP_MIRROR):
                _PIP_RESUME = _pip_version() >= (25, 1)
    return _PIP_RESUME


def robust_pip(name, args, mirror_args=(), manual="重跑本脚本,或配好网络后再试"):
    """pip 安装统一保底链(与 ffmpeg 同款五级):直连 -> 本机代理(探测) -> 镜像 -> 问端口(3次) -> 放弃。
    换线路时续传缓存按 URL 生效:直连/代理是同一 URL 断点可续,镜像换 URL 后由 pip 自行按缓存续。"""
    resume = ["--resume-retries", "5"] if pip_resume_ok() else []
    attempts = [("直连", [*resume, *args], None)]
    proxy = detect_proxy()
    if proxy:
        attempts.append((f"本机代理 {proxy}", [*resume, *args], proxy))
    if mirror_args:
        attempts.append(("镜像", [*resume, *mirror_args], None))
    for label, a, px in attempts:
        print(f"      尝试{label}...")
        if pip("install", *a, *(["--proxy", px] if px else [])):
            return True
        print(f"      {label} 失败")
    wrong = 0
    while wrong < 3:
        addr = ask_proxy(fresh=wrong > 0)
        if not addr:
            break
        if pip("install", *resume, "--proxy", addr, *args):
            return True
        wrong += 1
        print(f"      该地址不行(剩 {3 - wrong} 次机会)")
    print(f"      {BAD} {name} 自动安装失败。{manual}")
    return False


def fix_deps():
    if py_kind() == "other":
        print("  修复中止: 当前是系统 Python,不往里装。请先双击 webui\\安装环境.bat 生成项目内环境")
        return
    ts = torch_state()
    if ts is not None and ts != "ok":
        # 损坏的安装 pip 会认为"已满足"而跳过,必须先卸载再装
        print(f"      torch 已装但损坏({ts}),先卸载再重装...")
        pip("uninstall", "-y", "torch", "torchvision")
        ts = torch_state()
        if ts is not None:
            print(f"      {BAD} 卸载没卸干净,请手动 pip uninstall torch torchvision 后重跑")
            return
    if ts is None:
        print("      装 torch cu124(约 2.5GB,耐心)...")
        robust_pip(
            "torch",
            ["torch==2.5.1", "torchvision==0.20.1",
             "--index-url", "https://download.pytorch.org/whl/cu124"],
            mirror_args=["torch==2.5.1+cu124", "torchvision==0.20.1+cu124",
                         "-f", "https://mirrors.aliyun.com/pytorch-wheels/cu124/"],
            manual="手动办法:浏览器下载 https://mirrors.aliyun.com/pytorch-wheels/cu124/ 里 "
                   "torch-2.5.1+cu124-cp310-cp310-win_amd64.whl 和对应 torchvision,"
                   f"用 {sys.executable} -m pip install 文件名 安装")
    print("      补齐其余依赖...")
    robust_pip(
        "依赖",
        [n for n, _ in PIP_CORE],
        mirror_args=["-i", PIP_MIRROR, *[n for n, _ in PIP_CORE]])


def confirm():
    if "--yes" in sys.argv:
        return True
    try:
        return input("      执行? 回车=是 / N=跳过: ").strip().lower() != "n"
    except EOFError:
        return False


def main():
    print(f"=== wm2 环境体检(项目: {PROJ}) ===")
    check_py()
    fixes = []
    if not check_ffmpeg():
        fixes.append(("ffmpeg", fix_ffmpeg))
    if not check_deps():
        fixes.append(("依赖", fix_deps))
    if not check_ckpt():
        fixes.append(("模型权重", fix_ckpt))
    gpu_ok = check_gpu()
    print("=== 结论 ===")
    if fixes and "--fix" in sys.argv:
        print(f"需修复 {len(fixes)} 项: {', '.join(n for n, _ in fixes)}")
        for name, fn in fixes:
            print(f"--- 修复 {name} ---")
            fn()
        print("=== 复检 ===")
        # torch 重装后 CUDA 可用性可能变化,GPU 一并复检
        gpu_ok = check_ffmpeg() and check_deps() and check_ckpt() and check_gpu()
        if gpu_ok:
            print("全部就绪,双击 webui\\启动WebUI.bat 即可")
            sys.exit(0)
        print("仍有未修复项(最多自动修一轮,不再循环),按上方输出手动处理")
        sys.exit(1)
    if fixes:
        print(f"需修复 {len(fixes)} 项: {', '.join(n for n, _ in fixes)}")
        print("自动修复: python tools/env_check.py --fix   (或双击 webui\\安装环境.bat)")
        sys.exit(1)
    if gpu_ok:
        print("环境完好。启动:双击 webui\\启动WebUI.bat")
        sys.exit(0)
    print("可启动,但 GPU/CUDA 不可用,打水印会失败(要 NVIDIA 显卡 + cu124 版 torch + 正常驱动)。")
    sys.exit(1)


if __name__ == "__main__":
    main()
