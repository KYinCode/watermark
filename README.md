# watermark-v2 项目说明

学习型隐形水印(WAM,Meta ICLR 2025)对视频/图片逐帧打水印,抗录屏、截图裁剪、画面合成、平台转码。
引擎内置色彩回补(抵消水印/编码带来的泛黄泛紫)与完整 bt709 色彩标签;自带本机 WebUI 工作台。
验收结论见 `docs/验收报告.md`,项目当前状态见 `docs/项目状态_交接.md`。

## 目录结构

```
watermark\
├── data\                      【输入】待打水印的原始文件直接放这里(扁平,不限文件名)
├── output\                    【输出】所有成品出现在这里
│   ├── DeepSeek+DSH_已加水印_v2.mp4    成品视频(1080p60, 22478帧)
│   ├── ChatGPT Image *_已加水印.png    成品图片 ×2
│   ├── 成品_meta.json                  成品技术参数
│   ├── verification\                   验收原始数据(verify_seg*.json)
│   └── samples\                        攻击效果对比样本图
├── src\                       代码
│   ├── common.py              公共库(路径/模型加载/ffmpeg 管道/码本)
│   ├── embed_video.py         全片嵌入(源视频 → 成品)
│   ├── embed_images.py        图片嵌入
│   ├── verify.py              验收重测(30 项攻击 × 两段 660 帧)
│   ├── make_report.py         从 verification JSON 生成验收报告
│   ├── run_all.py             一键全流程
│   └── s2_attacks.py          攻击原语库
├── tools\                     【取证提取 + 环境工具】
│   ├── extract_wm.py          水印提取工具(单帧/视频时间点,多 ID 查表)
│   ├── env_check.py           环境体检/修复一体脚本(搬家、换机用;唯一逻辑脚本)
│   └── codebook.json          码本 v2:works[] 多作品(ID↔版权文本;兼容旧单作品格式)
├── webui\                     【WebUI 工作台】(本机自用,http://127.0.0.1:8765)
│   ├── app.py                 FastAPI 后端(GPU 串行队列/任务持久化/码本管理)
│   ├── engine_embed.py        嵌入子进程(JSON 行进度,参数化作品文本)
│   ├── engine_worker.py       提取常驻进程(模型预热,秒级响应,stdin/stdout JSON 行协议)
│   ├── store.py               码本存储(tools/codebook.json v2,旧格式自动迁移)
│   ├── static\                前端(无框架 SPA,朱印视觉)
│   ├── data\                  运行数据(任务历史 jobs.json、日志)
│   ├── local_config.bat       (可选)手工指定的本机路径覆盖
│   ├── 启动WebUI.bat          双击启动(--check 传环境体检)
│   └── 安装环境.bat           新电脑一键装环境(全部下载进项目目录)
├── bin\                       (按需生成)ffmpeg/ffprobe 自动下载落位处
├── runtime\                   (按需生成)Python 内嵌版 + 依赖(新电脑免 conda)
├── hf_home\                   (按需生成)HuggingFace 缓存,留在项目内
├── experiments\               选型/专题实验脚本(s1~s9 选型、t2b 10bit、t3 色偏补偿)+ 过程数据(out\),不影响交付
├── third_party\watermark-anything\   Meta 官方库 + 模型权重 wam_mit.pth(377MB)
├── docs\                      需求书 / 验收报告 / 工具使用说明 / 交接文档
├── environment.yml            conda 环境清单
└── wm_env.sh                  代理与路径环境(每条命令 source)
```

## 常用操作

```bash
# 环境准备(二选一)
#   新电脑推荐:双击 webui\安装环境.bat —— 全自动,全部装进项目目录,不需要 conda
#   或者手动 conda:
conda create -n wm2 python=3.10 -y && conda activate wm2
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
pip install -r third_party/watermark-anything/requirements.txt
pip install fastapi "uvicorn[standard]" python-multipart   # WebUI 依赖

# WebUI 工作台(推荐):双击 webui\启动WebUI.bat,或
source wm_env.sh && python webui/app.py     # -> http://127.0.0.1:8765
#   环境体检:双击 webui\启动WebUI.bat --check,或 python tools/env_check.py(修复加 --fix)
#   打水印参数:画质 CRF(默认 14)/ 嵌入强度 scaling_w(隐形 1.5 · 标准 2.0 · 鲁棒 3.0)
#             / 色彩回补(默认开,随强度自动配量;治白底泛黄、黑边泛黄泛紫)

# 提取水印(取证,命令行与 WebUI 共用同一份码本)
python tools/extract_wm.py video "output/DeepSeek+DSH_已加水印_v2.mp4" --t 100
python tools/extract_wm.py image "某张截图.png"

# 重新打水印(把原始视频/图片直接丢进 data\ 根目录)
source wm_env.sh && python src/run_all.py            # 全流程
python src/run_all.py --skip-embed                   # 只重跑验收
python src/embed_video.py --crf 14 --scaling-w 2.0    # 只嵌视频;--comp 0.5 手动定回补量,--comp 0 关闭,省略=随强度自动
python src/embed_images.py                           # 只嵌图片
```

## 输入 / 输出约定

- **输入**:原始视频/图片直接放进 `data\` 根目录(扁平,不限文件名/分辨率/帧率)。
  `data\` 下只有一个视频时自动识别;有多个时用 `--input` 指定要嵌哪个。
- **输出**:一切成品只写 `output\`,命名规则为 `<源文件名>_已加水印.mp4/.png`,meta 同名。
  实验过程的中间产物不进 `output\`,在 `experiments\out\`。
- 注意:验收套件 `src/verify.py` 的攻击几何按 1080p60 设计,其他规格素材建议只做核心项抽查,
  或改 `src\common.py` 的 `W/H/FPS` 后重跑。

## 水印方案速览

- 模型:WAM `wam_mit.pth`(MIT),逐帧嵌 32-bit ID `96e6955d` = 版权文本 SHA-256 前 4 字节;
  提取命中码本即输出完整版权文本(见 codebook.json)。
- 提取工具内置两步式策略链:全帧 → 镜像 → 亮度/色相反补偿网格 → 定位框裁剪 → 3×3 多窗 → 角度搜索。
- **色彩回补**(2026-09-06):水印残差会让白底泛黄、深蓝泛紫(白区蓝差实测约 -0.9 级/255,黑边更重)。
  嵌入前对画面红绿各预减 c 级抵消,c = 0.4 + 0.2×(scaling_w−1.5)(WebUI 默认开,随强度自动配量;
  图片走无损 PNG 链路基本不偏色,不补偿)。小样实测:白区色差回到 ±0.3 内、黑边泛黄减半、
  PSNR 代价 ≤0.1dB、直解与 crf23 重编码鲁棒性 100%。数据见 `experiments/t3_compensate.py`。
- 成品统一写入 bt709 三色彩标签(色彩空间/传递/原色);rawvideo 无标签输入时须在滤镜链加
  `setparams` 才能写全(引擎已内置)。
- 已知边界:组合搬运一条龙在 UI 录屏类内容上未达(详见 docs/验收报告.md 第 5 节)。

## 搬家 / 换电脑

项目按"自包含"设计:可下载的依赖一律装在项目目录内,代码里不写死任何盘符。

- **同一台电脑挪位置**:直接剪切整个文件夹到新位置,双击 `webui\启动WebUI.bat` 即可
  (所有路径运行时自适应;ffmpeg 按 `WM_FFMPEG` 环境变量 → 项目 `bin\` → PATH → 常见安装位置 的顺序解析)。
- **换新电脑**:
  1. 整个文件夹拷过去(`git clone` 也行,但模型权重 `third_party\watermark-anything\checkpoints\`
     约 360MB 不在 git 里,需从旧机器单独拷贝);
  2. 双击 `webui\安装环境.bat`:自动下载 Python 3.10 内嵌版(约 11MB)进 `runtime\`、
     装 torch CUDA 等依赖(约 2.5GB)、缺 ffmpeg 时自动下载静态版进 `bin\`
     (也可以在 `webui\local_config.bat` 写 `set "WM_FFMPEG=你的ffmpeg目录"` 用本机已装的)。
     全程不需要 conda、不需要管理员、不改系统设置;
  3. 随时体检:`webui\启动WebUI.bat --check` 或 `python tools/env_check.py`(修复加 `--fix`)。
- 诊断入口只有 `tools/env_check.py` 一个逻辑脚本;两个 .bat 只是双击壳
  (新机器没有任何 Python 时,由 bat 用 PowerShell 先引导内嵌版 Python,其余全在 py 里)。
