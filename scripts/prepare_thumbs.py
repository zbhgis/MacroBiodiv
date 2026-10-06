#!/usr/bin/env python3
"""MacroBiodiv 封面图片本地化管线（GeoSciPlot images/ 同款存储，2026-10-05 起）：

把 papers.json 里的外链封面收进**本仓库**，原图与缩略图都在：
    images/full/{id}.{ext}     原图（逐字节复制，不压缩）
    images/thumb/{id}.webp     缩略图（长边 ≤480 · WEBP quality 78 · LANCZOS ·
                               EXIF 转正 · 透明垫白；已在本仓库的旧图床缩略图直接复用）
并把引用改写为本仓库的 jsDelivr 链接（fallback 链自动跟随到 raw.githubusercontent）：
    cover       → https://cdn.jsdelivr.net/gh/zbhgis/MacroBiodiv@main/images/full/{id}.{ext}
    cover_thumb → https://cdn.jsdelivr.net/gh/zbhgis/MacroBiodiv@main/images/thumb/{id}.webp
    cover_thumb_w/h → 缩略图像素尺寸（卡片预留位）

与旧版（缩略图推 BlogImg 图床）的差异：原图 + 缩略图全部自持，BlogImg 仅作历史备份；
文件写入工作区后由发布流程的 git add -A 统一提交推送（本脚本不做 git 操作），
管理后台点「发布」会自动增量运行本脚本（失败不阻断发布）。

用法：
    python scripts/prepare_thumbs.py            # 增量：只处理未本地化的外链封面
    python scripts/prepare_thumbs.py --force    # 全部重新下载重做（⚠ 覆盖同名路径，
                                                # jsDelivr 对已有路径有缓存，可能滞后）
    python scripts/prepare_thumbs.py --dry-run  # 只报告目标清单

依赖：Pillow（仅本工具需要；站点构建/管理后台保持纯标准库零依赖）。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
PAPERS_JSON = ROOT / "meta" / "papers.json"
IMAGES_DIR = ROOT / "images"
FULL_DIR = IMAGES_DIR / "full"
THUMB_DIR = IMAGES_DIR / "thumb"
OWN_BASE = "https://cdn.jsdelivr.net/gh/zbhgis/MacroBiodiv@main/images"
LEGACY_THUMBS = ROOT.parent / "BlogImg" / "blog" / "thumb"   # 旧图床缩略图，可复用
THUMB_MAX = 480
THUMB_Q = 78


def sniff_ext(raw: bytes, fallback: str = "png") -> str:
    """魔数嗅探真实图片格式（Content-Type 可伪造）。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:4] in (b"GIF8",):
        return "gif"
    return fallback


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    req = Request(url, headers={"User-Agent": "MacroBiodivImages/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def make_thumb(raw: bytes) -> tuple[bytes, int, int]:
    """原图字节 → (webp 缩略图字节, 宽, 高)（GeoSciPlot make_assets 同款处理链）。"""
    from PIL import Image, ImageOps          # Pillow 仅此处需要
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "WEBP", quality=THUMB_Q, method=6)
        return out.getvalue(), im.size[0], im.size[1]


def webp_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size[0], im.size[1]


def process_one(item: dict, force: bool) -> dict:
    """下载原图 → 本地化。返回 {id, ok, ext, w, h, orig_kb, err}。
    changed 恒为 True（本地化必然改写 cover/cover_thumb 两个字段）。"""
    fid = str(item.get("id") or "")
    cover = str(item.get("cover") or "")
    if not fid or not cover.startswith(("http://", "https://")):
        return {"id": fid or "?", "ok": False, "err": "无外链封面", "ext": "", "w": 0, "h": 0}
    if cover.startswith(OWN_BASE) and not force:
        return {"id": fid, "ok": None, "err": "已本地化，跳过", "ext": "", "w": 0, "h": 0}
    try:
        raw = fetch_bytes(cover)
        ext = sniff_ext(raw)
        full_path = FULL_DIR / f"{fid}.{ext}"
        full_path.write_bytes(raw)                      # 原图逐字节复制
        thumb_path = THUMB_DIR / f"{fid}.webp"
        legacy = LEGACY_THUMBS / f"{fid}.webp"
        if legacy.exists() and not force:
            w, h = webp_size(legacy)                    # 旧图床缩略图直接复用
            thumb_path.write_bytes(legacy.read_bytes())
        else:
            tb, w, h = make_thumb(raw)                  # 现场压缩
            thumb_path.write_bytes(tb)
        return {"id": fid, "ok": True, "ext": ext, "w": w, "h": h,
                "orig_kb": len(raw) // 1024}
    except Exception as e:                    # 网络/解码失败：留待下次增量重跑
        return {"id": fid, "ok": False, "err": f"{type(e).__name__}: {e}"[:120],
                "ext": "", "w": 0, "h": 0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--force", action="store_true", help="已本地化的也重新下载重做")
    ap.add_argument("--dry-run", action="store_true", help="只报告目标清单")
    ap.add_argument("--workers", type=int, default=6, help="并发下载数")
    ap.add_argument("--quiet", action="store_true",
                    help="不逐条打印（发布任务自动调用时保持日志简洁）")
    args = ap.parse_args()

    try:
        import PIL                             # noqa: F401  提前给出可读报错
    except ImportError:
        print("缺少 Pillow：pip install pillow（仅本工具需要，站点构建/管理后台不受影响）")
        return 2

    papers = json.loads(PAPERS_JSON.read_text(encoding="utf-8"))
    items = [it for it in papers.get("items", [])
             if str(it.get("cover") or "").startswith(("http://", "https://"))]
    targets = [it for it in items
               if args.force or not str(it.get("cover") or "").startswith(OWN_BASE)]
    print(f"外链封面 {len(items)} 张，本次待本地化 {len(targets)} 张"
          + ("（--force 全量）" if args.force else "（增量）"))
    if args.dry_run:
        for it in targets:
            print(f"  - {it.get('id')} {str(it.get('cover'))[:70]}")
        return 0
    if not targets:
        print("· 没有需要本地化的封面")
        return 0
    FULL_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    done: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for res in ex.map(lambda it: process_one(it, args.force), targets):
            if not args.quiet:
                mark = {True: "✓", False: "✗", None: "·"}.get(res["ok"], "?")
                size = f" {res.get('orig_kb', 0)}KB" if res["ok"] else ""
                print(f"  {mark} {res['id']}{size} {res['err'] if res['ok'] is False else ''}")
            done.append(res)

    ok = [r for r in done if r["ok"]]
    skipped = len([r for r in done if r["ok"] is None])
    failed = len([r for r in done if r["ok"] is False])
    for r in ok:
        for it in papers["items"]:
            if it.get("id") == r["id"]:
                it["cover"] = f"{OWN_BASE}/full/{r['id']}.{r['ext']}"
                it["cover_thumb"] = f"{OWN_BASE}/thumb/{r['id']}.webp"
                it["cover_thumb_w"] = r["w"]
                it["cover_thumb_h"] = r["h"]
                break
    PAPERS_JSON.write_text(
        json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"· 本地化 {len(ok)} 张 → images/full + images/thumb（cover/cover_thumb 已改写为本仓库链接）")
    if skipped:
        print(f"· 跳过 {skipped} 张（已本地化）")
    if failed:
        print(f"· 失败 {failed} 张（重跑本命令即可增量补齐）")
    print("· 提示：images/ 随下一次 git 提交推送上线，jsDelivr 对新路径即时生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
