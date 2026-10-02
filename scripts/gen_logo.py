#!/usr/bin/env python3
"""MacroBiodiv 图标生成器 —— 纯几何绘制（不依赖字体），4x 超采样抗锯齿。

设计概念：
    M（MacroBiodiv）+ 叶（生物多样性）+ 节点（GIS 采样点/数据）
三个变体：
    A  渐变线条 M，中谷向上长出一片叶（绿），左肩一枚数据节点
    B  渐变线条 M，右臂顶端向外斜生一片叶
    C  变体 A + 背景淡点阵（物种分布图意象）

用法：
    python scripts/gen_logo.py            # 生成 assets_src/ 下预览与正式文件
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets_src"

TILE_BG_TOP = (16, 22, 31)      # #10161f
TILE_BG_BOT = (9, 13, 19)       # #090d13
ACCENT = (88, 166, 255)         # #58a6ff 站点强调蓝
GREEN = (63, 185, 80)           # #3fb950 生命绿
GREEN_LIGHT = (74, 222, 128)


def rounded_tile(size: int) -> Image.Image:
    """圆角深色底板 + 垂直微渐变。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.23)
    for y in range(size):
        t = y / (size - 1)
        c = tuple(int(TILE_BG_TOP[i] + (TILE_BG_BOT[i] - TILE_BG_TOP[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=c + (255,))
    # 圆角裁切
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    img.putalpha(mask)
    # 发丝描边（accent 低透明度）
    border = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle([1, 1, size - 2, size - 2], radius=r,
                                             outline=ACCENT + (55,), width=max(2, size // 128))
    img.alpha_composite(border)
    return img


def gradient_stroke(img: Image.Image, points: list[tuple[float, float]], width: int,
                    c1: tuple[int, int, int], c2: tuple[int, int, int]) -> None:
    """圆角粗折线，颜色沿路径从 c1 渐变到 c2（分段插值 + 端点圆帽）。"""
    d = ImageDraw.Draw(img)
    total = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    acc = 0.0
    for i in range(len(points) - 1):
        p0, p1 = points[i], points[i + 1]
        seg = math.dist(p0, p1)
        t0, t1 = acc / total, (acc + seg) / total
        acc += seg
        steps = max(2, int(seg / (width * 0.25)))
        for s in range(steps):
            ta = t0 + (t1 - t0) * s / steps
            tb = t0 + (t1 - t0) * (s + 1) / steps
            ca = tuple(int(c1[k] + (c2[k] - c1[k]) * ta) for k in range(3))
            cb = tuple(int(c1[k] + (c2[k] - c1[k]) * tb) for k in range(3))
            a = (p0[0] + (p1[0] - p0[0]) * s / steps, p0[1] + (p1[1] - p0[1]) * s / steps)
            b = (p0[0] + (p1[0] - p0[0]) * (s + 1) / steps, p0[1] + (p1[1] - p0[1]) * (s + 1) / steps)
            d.line([a, b], fill=ca, width=width)
            d.line([b, b], fill=cb, width=width)
    # 圆帽
    for idx in (0, -1):
        r = width / 2
        cx, cy = points[idx]
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c2 if idx == -1 else c1)


def leaf(img: Image.Image, base: tuple[float, float], angle_deg: float,
         length: float, width: float, color: tuple[int, int, int],
         alpha: int = 255) -> None:
    """叶形（两段正弦弧围成），base 为叶柄起点，angle 为轴向（度，-90=正上）。"""
    a = math.radians(angle_deg)
    ax, ay = math.cos(a), math.sin(a)
    px, py = -math.sin(a), math.cos(a)
    side, back = [], []
    n = 48
    for i in range(n + 1):
        t = i / n
        off = width / 2 * math.sin(math.pi * t) ** 0.9
        cx = base[0] + ax * length * t
        cy = base[1] + ay * length * t
        side.append((cx + px * off, cy + py * off))
        back.append((cx - px * off, cy - py * off))
    pts = side + back[::-1]
    solid = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(solid).polygon(pts, fill=color + (alpha,))
    # 中脉
    ImageDraw.Draw(solid).line(
        [base, (base[0] + ax * length * 0.9, base[1] + ay * length * 0.9)],
        fill=(255, 255, 255, 60), width=max(2, int(length * 0.026)))
    img.alpha_composite(solid)


def m_points(size: float) -> list[tuple[float, float]]:
    """M 折线关键点（按 512 基准设计，随 size 缩放）。"""
    s = size / 512
    return [(150 * s, 372 * s), (150 * s, 168 * s), (256 * s, 318 * s),
            (362 * s, 168 * s), (362 * s, 372 * s)]


def variant_a(size: int) -> Image.Image:
    """M 中谷长叶 + 左肩节点。"""
    s = size / 512
    img = rounded_tile(size)
    gradient_stroke(img, m_points(size), int(46 * s), ACCENT, GREEN)
    # 中谷向上的一片新叶
    stem_end = (256 * s, 250 * s)
    ImageDraw.Draw(img).line([(256 * s, 318 * s), stem_end], fill=GREEN, width=int(13 * s))
    leaf(img, stem_end, -90 + 16, 118 * s, 66 * s, GREEN_LIGHT)
    # 左肩数据节点
    d = ImageDraw.Draw(img)
    r = 13 * s
    d.ellipse([150 * s - r, 168 * s - r, 150 * s + r, 168 * s + r], fill=(230, 241, 255, 255))
    r2 = 5 * s
    d.ellipse([150 * s - r2, 168 * s - r2, 150 * s + r2, 168 * s + r2], fill=ACCENT + (255,))
    return img


def variant_b(size: int) -> Image.Image:
    """M 右臂顶端斜生一叶（生长/新芽意象）。"""
    s = size / 512
    img = rounded_tile(size)
    gradient_stroke(img, m_points(size), int(46 * s), ACCENT, GREEN)
    ImageDraw.Draw(img).line([(362 * s, 172 * s), (378 * s, 126 * s)],
                             fill=GREEN, width=int(15 * s))
    leaf(img, (378 * s, 126 * s), -62, 108 * s, 62 * s, GREEN_LIGHT)
    return img


def variant_c(size: int) -> Image.Image:
    """变体 A + 背景淡点阵（分布图意象）。"""
    img = variant_a(size)
    s = size / 512
    dots = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots)
    step = 34 * s
    start = 96 * s
    for gy in range(9):
        for gx in range(9):
            x = start + gx * step
            y = start + gy * step
            r = 2.6 * s
            dd.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT + (26,))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.23), fill=255)
    img.paste(dots, (0, 0), Image.composite(dots.split()[3], Image.new("L", img.size, 0), mask))
    return img


def save(img: Image.Image, path: Path, out_size: int) -> None:
    img.resize((out_size, out_size), Image.LANCZOS).save(path)


def derive_from_source() -> bool:
    """用户提供的 logo 源图（assets_src/logo_source.png）→ 派生正式产物。

    · logo.png：居中裁成正方形，保持原始分辨率（展示位 ≤58px，无需放大）
    · favicon.png：64×64 LANCZOS 缩小
    存在该源图时跳过几何绘制 —— 重跑本脚本不会把用户 logo 覆盖回去。
    """
    src_path = OUT / "logo_source.png"
    if not src_path.exists():
        return False
    src = Image.open(src_path).convert("RGBA")
    w, h = src.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    square = src.crop((left, top, left + side, top + side))
    square.save(OUT / "logo.png")
    square.resize((64, 64), Image.LANCZOS).save(OUT / "favicon.png")
    print(f"已由 assets_src/logo_source.png 派生 logo.png（{side}×{side}）+ favicon.png（64）")
    return True


def main() -> int:
    OUT.mkdir(exist_ok=True)
    if derive_from_source():
        return 0
    # 预览（512）
    for name, fn in (("preview_a", variant_a), ("preview_b", variant_b), ("preview_c", variant_c)):
        save(fn(2048), OUT / f"{name}.png", 512)
    # 正式产物：变体 B
    save(variant_b(2048), OUT / "logo.png", 512)
    save(variant_b(256), OUT / "favicon.png", 64)
    print("已生成 assets_src/preview_a/b/c.png + logo.png + favicon.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
