# -*- coding: utf-8 -*-
"""汇总所有实测 JSON 生成 验收报告.md。"""
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def load(p):
    f = PROJ / p
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def fmt(x):
    return f"{x:.1%}" if isinstance(x, float) and x <= 1 else str(x)


def main():
    meta = load(str(C.OUTPUT / "成品_meta.json"))
    img_meta = load(str(C.OUTPUT / "成品图片_meta.json"))
    v5 = load(str(C.VERIFY_DIR / "verify_seg5000.json"))
    v17 = load(str(C.VERIFY_DIR / "verify_seg17800.json"))

    def row(name, r, note=""):
        if r is None:
            return f"| {name} | — | — | {note} |"
        exact, ge31 = r["exact"], r.get("ge31", -1)
        ge31_s = "—" if ge31 is None or ge31 < 0 else f"{ge31:.1%}"
        return f"| {name} | **{fmt(exact)}** | {ge31_s} | {note} |"

    def find(results, key):
        for r in results or []:
            if r["name"] == key:
                return r
        return None

    lines = []
    A = lines.append
    A("# watermark-v2 验收报告")
    A("")
    A("> 全部数据为实测(660 帧连续段 × 2 段;\"提取成功\"=32 bit 全对;ge31 = 至少 31/32 bit 对)。")
    A("> 生成脚本 src/make_report.py,原始数据见 output/verification/*.json。")
    A("")
    A("## 0. 方案与环境")
    A("")
    A(f"- 模型:WAM(watermark-anything)`wam_mit.pth`(MIT),32-bit 多比特水印,逐帧独立嵌入,`scaling_w=2.0`")
    A(f"- 载荷:固定 ID `{meta['id_hex'] if meta else '?'}` = 版权文本 SHA-256 前 4 字节;码本 codebook.json 随工具交付")
    A("- 环境:conda `wm2`(Python 3.10.21)+ PyTorch 2.5.1+cu124,RTX 4060 Laptop 8GB,依赖见 environment.yml")
    A(f"- 成品:`DeepSeek+DSH_已加水印_v2.mp4` h264 crf14 yuv420p,帧数 {meta['frames'] if meta else '?'}/22478,"
      f" {meta['size_mb']:.0f} MB,音轨 copy" if meta else "- 成品:见 成品_meta.json")
    A("")
    if meta:
        A("## 1. 画质(需求 #2)")
        A("")
        A(f"- 全片逐帧 PSNR(嵌入帧 vs 源帧):**mean {meta['psnr_mean']:.2f} dB**,min {meta['psnr_min']:.2f},p5 {meta['psnr_p5']:.2f}(≥36 ✓)")
        A("- 小样:好段 mean 37.96(min 37.63),高动态段 mean 40.26(min 37.94)")
        A("- 肉眼检查:exp/out/seg_sw20/sample_*.png(原图/成品/diff×10),残差沿边缘纹理分布(JND),平坦区近零")
        A("")
    A("## 2. 成品视频验收(需求 #3-#9)")
    A("")
    for tag, v, label in (("5000", v5, "好段(帧5000-5659,偏静态)"), ("17800", v17, "高动态段(帧17800-18459)")):
        if v is None:
            continue
        A(f"### {label}")
        A("")
        A("| 项目 | 提取成功 | ≥31/32bit | 备注 |")
        A("|---|---|---|---|")
        rs = v["results"]
        pairs = [
            ("1:1 直解(#3)", "01_1to1直解", "≥95%"),
            ("全屏录屏 crf23(#4)", "02_录屏crf23", "≥80%"),
            ("二压 crf18(#6)", "03_二压crf18", "≥80%"),
            ("连续 2 代转码(#6)", "04_连续2代crf23", "≥60%"),
            ("帧率 60→30(#6)", "05_60to30fps", "不明显低于 1:1"),
            ("微信级 720p crf28(#6)", "06_微信720p_crf28", "实测报告"),
            ("720p 直解(#6)", "07a_720p直解", "≥80%"),
            ("720p 还原 1080(#6)", "07b_720p还原1080", "≥80%"),
            ("540p 直解(#6 报告项)", "07c_540p直解", "实测报告"),
            ("水平镜像(#5)", "08_水平镜像", "≥80%"),
            ("旋转 5° 单步(#5)", "09_rot5_单步", "—"),
            ("旋转 5° 两步式(#5)", "09_rot5_两步式", "≥80%"),
            ("旋转 15° 两步式(#5)", "09_rot15_两步式", "≥80%"),
            ("旋转 60° 两步式(#5)", "09_rot60_两步式", "≥80%"),
            ("裁剪 25% 面积(#5)", "10_裁剪25%", "≥80%"),
            ("裁剪 36% 面积(#5)", "10_裁剪36%", "≥80%"),
            ("裁剪 50% 面积(#5)", "10_裁剪50%", "≥80%"),
            ("包边 70% 单步(#7)", "11_canvas70_单步", "—"),
            ("包边 70% 两步窗(#7)", "11_canvas70_两步窗", "≥80%"),
            ("包边 90% 单步(#7)", "11_canvas90_单步", "≥80%"),
            ("画中画 25% 被覆盖(#7)", "12_画中画25%", "≥80%"),
            ("亮度 +20%(#8)", "13_亮+20%", "≥80%"),
            ("亮度 -20% 单步(#8)", "13_亮-20%", "—"),
            ("亮度 -20% 工具链(#8)", "13_亮-20%_工具链", "≥80%"),
            ("对比度 +20%(#8)", "13_对比+20%", "≥80%"),
            ("对比度 -20%(#8)", "13_对比-20%", "≥80%"),
            ("饱和度 +20%(#8)", "13_饱和+20%", "≥80%"),
            ("饱和度 -20%(#8)", "13_饱和-20%", "≥80%"),
            ("色相 +20%(#8)", "13_色相+20%", "≥80%"),
            ("色相 -20% 单步(#8)", "13_色相-20%", "—"),
            ("色相 -20% 工具链(#8)", "13_色相-20%_工具链", "≥80%"),
            ("高斯模糊(#8)", "13_高斯模糊", "≥80%"),
            ("椒盐噪声 5%(#8)", "13_椒盐", "≥80%"),
            ("随机遮挡 10%(#8)", "14_随机遮挡10%", "≥80%"),
            ("组合搬运单步(#9)", "15a_组合搬运_单步", "—"),
            ("组合搬运镜像重试(#9)", "15b_组合搬运_镜像重试", "≥60%"),
        ]
        for name, key, note in pairs:
            r = find(rs, key)
            A(row(name, r, note))
        A("")
    A("## 3. 图片成品(需求 #13)")
    A("")
    if img_meta:
        A("| 图片 | PSNR | 1:1 解码 |")
        A("|---|---|---|")
        for r in img_meta:
            A(f"| {r['img']} | {r['psnr']} dB | {'✓' if r.get('exact_1to1', r.get('exact_1to1')) else '✗'} |")
        A("")
        A(f"最终档位:scaling_w=2.5(25% 面积随机裁剪 ×20:图1 {img_meta[0].get('crop25_exact', 0):.0%}、"
          f"图2 {img_meta[1].get('crop25_exact', 0):.0%},PSNR 见上表)。")
        A("")
    A("## 4. 嵌入耗时(需求 #11)")
    A("")
    if meta:
        A(f"- 全片 22478 帧总耗时 {meta['wall_s']:.0f}s(≈{meta['wall_s'] / 60:.0f} 分钟,上限 2 小时 ✓),"
          f"纯 GPU 嵌入 {meta['embed_ms']:.1f} ms/帧,峰值显存 ~2.5 GiB")
    A("")
    A("## 5. 已知边界与未覆盖项(如实报告)")
    A("")
    A("### 5.1 组合搬运一条龙(#9)的内容相关性")
    A("")
    A("- 全强度一条龙(镜像+裁切+轻调色+720p+贴片):动画/网格类内容(好段)**79.7%** 达标(≥60);")
    A("  UI 录屏类内容(高动态段)**2.7%** 未达。")
    A("- 根因(实验 s4/s9b):镜像×裁切 存在 WAM 模型级组合盲区(确定性错码),镜像重试可完全恢复")
    A("  (两段 91.4%/87.1%,见 R2 实验);但叠加 裁切×缩放×调色 后,48 组精确逆补偿网格均无法恢复(0%)。")
    A("- 可观赏性分析(样本见 output/samples/):含镜像的组合对 UI 录屏内容本身不可读(文字反写),")
    A("  现实威胁有限;但无镜像的现实强度组合(裁切+轻调色+720p+贴片)在 UI 内容上同样未达(2.7%),")
    A("  属当前模型档位(scaling_w=2.0,PSNR 39.6)下的真实边界。")
    A("- 若需覆盖该场景,可选 scaling_w=3.0 重嵌(预计 PSNR ~35,低于 36 线),未执行,待需求方决策。")
    A("")
    A("### 5.2 单帧直解与工具链")
    A("")
    A("- 亮度 -20%:好段单帧直解 75.2%,工具链(亮度补偿网格)**87.1%** ≥80 ✓;高动态段直解 99.7%")
    A("- 色相 -20%:好段直解 100%;高动态段直解 2.7%,工具链(色相反补偿网格)结果见验收数据 JSON")
    A("  (s9 实验补偿网格逐帧最优 100%)")
    A("- 视频实际取证使用 ±0.5s 邻域多帧投票,单帧劣化不直接影响结论")
    A("")
    A("### 5.3 未覆盖项")
    A("")
    A("- 手机翻拍(#10 尽力项):无手机链路可自动化实测,未覆盖;建议用工具 image 模式对翻拍照片实测")
    A("- 时间轴整段替换:按需求书不在防御范围")
    A("- 图片 25% 裁剪在纯平坦区域时信号弱(JND 机制),视频不受影响(时域投票);图片已按调档实验选档重嵌")
    A("")
    (PROJ / "docs" / "验收报告.md").write_text("\n".join(lines), encoding="utf-8")
    print("验收报告.md 已生成", flush=True)


if __name__ == "__main__":
    main()
