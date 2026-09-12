# wm2 · 隐形水印工作台

**给自己的视频和图片打上"看不见的版权水印"。** 画面肉眼几乎无变化,但作品被盗图、被搬运、被录屏、被平台压缩之后,你都能从残留画面里提取出自己的版权信息,作为维权取证。

> 基于 Meta FAIR 的 [Watermark Anything Model](https://github.com/facebookresearch/watermark-anything)(WAM,ICLR 2025),本地运行,不上传任何数据。
> 面向 Windows + NVIDIA GPU。

---

## 这是什么

一个"打水印 + 查水印"的完整工作台:

- **打水印(嵌入)**:给视频逐帧、图片整张嵌入一个 32-bit 的版权 ID。水印由模型按"人眼不敏感"的位置分散隐藏,不是贴Logo、不是暗纹,截掉角、调亮度、压缩转码都洗不掉。
- **查水印(提取)**:拿到任何一张截图、一段录屏、一个转码后的文件,工具自动尝试多种恢复策略,解码出 32-bit ID,再查码本还原成你的完整版权文本。
- **取证**:提取结果可导出 JSON(含置信度、命中策略、原始数据),留下可复核的记录。

典型用法:UP 主发布原创视频前打上水印 → 视频被人搬运/录屏 → 从搬运稿里截图送查 → 提取到"©XXX 版权所有|禁止搬运 Bili:xxx" → 取证。

## 实测能力

| 项目 | 数据 |
|---|---|
| 画质 | 全片 PSNR 39.6 dB(隐形优先档 40~42.5 dB),肉眼不感知差异 |
| 抗二次压缩 | crf23 重编码后提取准确率 ~100% |
| 截图提取 | 单帧约 0.5 秒(热);视频时间点约 2 秒 |
| 攻击验收 | 截图裁剪/亮度色相调整/旋转缩放/高斯噪声/平台转码等 30 项攻击,13/14 大项达标 |
| 色彩 | 成品自动带 bt709 三标签;内置色彩回补,白区色差归零附近 |

**已知边界**:唯一未达项是"组合搬运一条龙"(重压缩+裁剪+变速+遮挡叠加)在 UI 录屏类内容上的表现;黑边处偶发的绿边是 4:2:0 色度采样的物理代价,已尽量缓解但无法根除。详见 `docs/验收报告.md`。

## 快速开始

```
1. 把整个项目文件夹放到任意位置(无需安装到系统)
2. 双击 webui\安装环境.bat        ← 仅新电脑第一次:自动下载 Python 与依赖到项目目录内
3. 双击 webui\启动WebUI.bat      ← 日常启动:自动开浏览器 http://127.0.0.1:8765
```

- 需要:**Windows 10/11 + NVIDIA 显卡**(打水印用 GPU;只查水印也走 GPU,有卡即可)
- 模型权重 `wam_mit.pth`(360MB)不随仓库分发(官方配置 `params.json` 已入库,clone 即有):
  体检报缺后 `python tools/env_check.py --fix` 自动下载;或从旧机器拷到
  `third_party\watermark-anything\checkpoints\`,或从[官方地址](https://dl.fbaipublicfiles.com/watermark_anything/wam_mit.pth)下载
- 不确定环境是否健康:命令行运行 `webui\启动WebUI.bat --check`(双击带不了参数,需在 cmd/PowerShell 里执行),或 `python tools/env_check.py`
- 日常停止:直接关服务窗口,或双击 `webui\停止WebUI.bat`
- 换电脑/挪位置:见下文[搬家 / 换电脑](#搬家--换电脑),整个文件夹拷走即可

## 使用流程

**第一次(30 秒)**:打开网页 → 「新建作品」→ 输入你的版权文本(如"©XXX 版权所有|禁止搬运")→ 系统自动派生出唯一 ID(文本不可改,改了就是新作品)。

**打水印**:「选素材」丢入视频或图片 → 选参数(不懂就保持默认:CRF 14 + 标准档 + 色彩回补开)→ 「提交任务」→ GPU 队列自动跑,完成后成品出现在 `output\`,并自动抽帧自检。

**查水印**:「查水印」页 → 丢入截图(或视频+时间点,或全片扫描)→ 提取出 ID → 命中码本即显示完整版权文本 → 一键复制 / 导出取证 JSON。**提取不需要原始文件**,盲提取。

## 参数怎么选(打水印页)

| 参数 | 含义 | 建议 |
|---|---|---|
| 视频画质 CRF | 压缩松紧:越小越清晰、文件越大 | 默认 14,别动 |
| 嵌入强度 scaling_w | 水印烙多深:越深越抗折腾,画质略降 | 三档预设见下 |
| 色彩回补 | 抵消水印带来的泛黄/泛紫 | 保持开启,随强度自动配量 |

强度三档(视频/图片):

| 档位 | 强度 | 画质 | 适合 |
|---|---|---|---|
| 隐形优先 | 1.5 / 2.0 | 最好(PSNR 40~42.5) | 日常出稿;抗压余量略小但实测截图/重编码 100% 可读 |
| 标准 | 2.0 / 2.5 | 好 | 验收达标的均衡档 |
| 鲁棒优先 | 3.0 / 3.0 | 有感知(约 35) | 预期遭遇"搬运一条龙"等严苛场景 |

## 工作原理(一段话版)

视频/图片 → 按"人眼不敏感"的掩码(JND)逐帧叠加水印残差(深度模型 WAM)→ 编码输出(视频走 x264/bt709);提取时反向走"策略链":全帧解码 → 镜像 → 亮度/色相反补偿网格 → 定位框裁剪 → 3×3 多窗 → 角度搜索,任一路径解出 32-bit ID 即命中,查码本还原版权文本。ID = 版权文本 SHA-256 的前 4 字节,派生规则不可变,同一文本永远同一个 ID。

## 目录结构

```
watermark\
├── data\                      【输入】待打水印的原始文件直接放这里(扁平)
├── output\                    【输出】所有成品出现在这里(<源名>_已加水印.mp4/.png + meta)
│   ├── verification\          验收原始数据
│   └── samples\               攻击效果对比样本图
├── src\                       引擎
│   ├── common.py              公共库(路径自适应/模型加载/ffmpeg 管道/码本)
│   ├── embed_video.py         全片嵌入 CLI(--scaling-w/--comp/--crf)
│   ├── embed_images.py        图片嵌入
│   ├── verify.py              验收重测(30 项攻击 × 两段 660 帧)
│   ├── make_report.py         从验收 JSON 生成报告
│   └── run_all.py             一键全流程
├── tools\                     【取证提取 + 环境工具】
│   ├── extract_wm.py          提取工具(图片/视频时间点,多 ID 查表)
│   ├── env_check.py           环境体检/修复一体脚本(唯一逻辑脚本)
│   └── codebook.json          码本 v2(多作品 ID↔版权文本)
├── webui\                     【WebUI 工作台】(http://127.0.0.1:8765,仅本机)
│   ├── app.py                 FastAPI 后端(GPU 串行队列/任务持久化)
│   ├── engine_embed.py        嵌入子进程(JSON 行进度)
│   ├── engine_worker.py       提取常驻进程(预热,秒级响应)
│   ├── static\                前端(无框架 SPA,"朱印"视觉)
│   ├── 启动WebUI.bat          双击启动(--check 传体检;自动处理旧实例)
│   ├── 停止WebUI.bat          双击停止并释放端口
│   └── 安装环境.bat           新电脑一键装环境
├── bin\ runtime\ hf_home\ torch_home\   (按需生成)下载的环境与缓存,全在项目内
├── experiments\               选型/专题实验脚本与过程数据
├── third_party\watermark-anything\   Meta 官方库(MIT)+ 模型权重(360MB,不入库)
├── docs\                      需求书 / 验收报告 / 工具使用说明 / 项目状态交接
└── wm_env.sh                  AI 会话用的环境脚本(路径自适应)
```

## 命令行用法(与 WebUI 同一套码本)

```bash
# 查水印(取证)
python tools/extract_wm.py image "某张截图.png"
python tools/extract_wm.py video "成品.mp4" --t 100

# 打水印(CLI;data\ 下多视频时加 --input 指定)
python src/embed_video.py --scaling-w 1.5            # --comp 0 关闭色彩回补,省略=自动
python src/embed_images.py

# 验收/体检
python src/verify.py                                  # 30 项攻击重测
python tools/env_check.py                             # 环境体检(--fix 自动修复)
```

## 输入 / 输出约定

- **输入**:原始文件放 `data\` 根目录(扁平);多视频时 CLI 用 `--input` 指定。
- **输出**:成品只写 `output\`,命名 `<源名>_已加水印.mp4/.png`,同名 meta JSON 记录
  参数(crf / scaling_w / comp / ID);重名自动加 `_v2/_v3` 尾缀。
- 验收套件按 1080p60 设计,其他规格素材建议只做核心项抽查,或改 `src/common.py` 的 `W/H/FPS`。

## 搬家 / 换电脑

项目按"自包含"设计:所有下载的依赖都装在项目目录里,代码零写死路径。

- **同机挪位置**:直接剪切整个文件夹,双击启动即可。
- **换新电脑**:① 整夹拷贝或 `git clone`(模型权重 360MB 需单独补:`python tools/env_check.py --fix` 自动下载,或从旧机器拷);
  ② 双击 `webui\安装环境.bat`(自动下载 Python 内嵌版、依赖、ffmpeg 到项目目录,不需要 conda、不需要管理员);
  ③ 双击 `webui\启动WebUI.bat`。
- 下载线路:直连(自动吃系统代理/TUN)→ 本机代理探测(7890/7897 或 `WM_PROXY` 指定)→ 国内镜像轮试(仅 ffmpeg)→ 问代理端口(3 次,绝不记住)→ 手动指引。
  大文件断点续传(连接中断自动从断点继续;torch 走 pip 的 `--resume-retries`);模型权重无镜像,只有直连/代理两条线路,下载完做 sha256 校验。
- 手工覆盖:新建 `webui\local_config.bat` 写 `set "WM2_PY=..."` 或 `set "WM_FFMPEG=..."`。

## 已知边界与注意事项

- 组合搬运一条龙在 UI 录屏类内容上未达(其余场景达标);黑边偶发绿边为 4:2:0 物理代价。
- 需要 NVIDIA GPU;CPU 无法运行推理。
- 启动自愈:依赖缺失导致 WebUI 起不来时,`启动WebUI.bat` 会自动诊断、补装依赖并重试一次(最多一轮,绝不循环),修好自动补开浏览器;torch 只在打水印/提取的引擎子进程里加载,缺 torch 不挡启动,由任务报错和状态页 GPU 检测暴露。
- WebUI 仅监听 127.0.0.1,无鉴权,请勿放行到公网。
- 同一台机器请勿同时跑两个打水印任务(GPU 严格串行,队列已自动处理)。

## 第三方组件与致谢

- **[watermark-anything](https://github.com/facebookresearch/watermark-anything)**(Meta FAIR):
  水印模型与推理代码,MIT License。本项目以源码形式内置在 `third_party\watermark-anything\`
  (未做修改,对方 LICENSE 原样保留);模型权重 `wam_mit.pth` 同为 MIT,不随本仓库分发,
  可 `python tools/env_check.py --fix` 自动下载(带 sha256 校验),或从
  [官方地址](https://dl.fbaipublicfiles.com/watermark_anything/wam_mit.pth) 下载放入
  `checkpoints\`,或直接从旧机器拷贝。

## 许可证

本项目代码以 [MIT License](LICENSE) 发布(`Copyright (c) 2026 KYinCode`)。
`third_party\watermark-anything\` 内的第三方代码与模型权重归 Meta FAIR 所有,按其自带的 MIT License 授权。
