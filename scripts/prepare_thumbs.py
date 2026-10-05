#!/usr/bin/env python3
"""MacroBiodiv 封面缩略图预处理：把 papers.json 里的外链封面压缩成 webp 缩略图，
存入图床仓库（zbhgis/BlogImg，路径 blog/thumb/{id}.webp），并把缩略外链写回
papers.json 的 cover_thumb 字段（卡片优先引用，详情页保持原图外链）。

机制学自 GeoSciPlot scripts/prepare.py（images/thumb + Pillow 同款参数），
差异：MacroBiodiv 的原图 100% 是外链（不落盘），所以没有 raw/ 投放区，
直接以 cover 字段的 URL 为源、以文献 id 为缩略图文件名。

用法：
    python scripts/prepare_thumbs.py            # 增量：只为没有 cover_thumb 的封面生成
    python scripts/prepare_thumbs.py --force    # 全部重新生成（覆盖图床里的同名文件）
    python scripts/prepare_thumbs.py --push     # 生成后 git add/commit/push 图床仓库
    python scripts/prepare_thumbs.py --dry-run  # 只报告目标清单，不下载不写

**已并入发布流程**：管理后台「发布」会自动以 --push --quiet 调用本脚本（失败不阻断
发布，缺缩略图的卡片回退原图外链）；手动运行仅在应急/全量重建时需要。

依赖：Pillow（仅本工具需要；站点构建 build_site.py 与管理后台 admin.py 保持
纯标准库零依赖 —— 缩略图缺失时卡片自动回退原图外链，行为向后兼容）。
github.com 直连不通时给 --git-proxy 挂本地代理（仅用于 push，jsdelivr 下载不走代理）。

缩略图规格（与 GeoSciPlot 一致）：长边 ≤480px · WEBP quality 78 method 6 ·
LANCZOS · EXIF 转正 · 透明通道垫白底。实测约 2MB PNG → 30-60KB（30-40 倍）。
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
PAPERS_JSON = ROOT / "meta" / "papers.json"
THUMB_MAX = 480
THUMB_Q = 78
DEFAULT_SPEC = "zbhgis/BlogImg@main"          # jsdelivr 的 {owner}/{repo}@{branch}
DEFAULT_REPO = ROOT.parent / "BlogImg"        # 图床仓库本地克隆位置
PROXY_FALLBACK = "http://127.0.0.1:7897"      # push 直连失败时的自动重试代理（Clash 系默认端口）


def thumb_url(spec: str, fid: str) -> str:
    return f"https://cdn.jsdelivr.net/gh/{spec}/blog/thumb/{fid}.webp"


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": "MacroBiodivThumbs/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def make_thumb(raw: bytes) -> tuple[bytes, int, int]:
    """原图字节 → (webp 缩略图字节, 宽, 高)（GeoSciPlot make_assets 同款处理链）。
    宽高随缩略图返回，写进 papers.json 供卡片在图片加载前预留位（防抖动）。"""
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
    """读取已生成缩略图的尺寸（本地文件，不下载）。"""
    from PIL import Image
    with Image.open(path) as im:
        return im.size[0], im.size[1]


def process_one(item: dict, spec: str, force: bool, thumb_dir: Path) -> dict:
    """下载 → 压缩。返回 {id, ok, thumb(字节)|err, url, w, h, changed}。
    本地克隆里已有同名缩略图（上次生成但推送失败留下的）直接复用，不重复下载。
    changed=True 表示该缩略图可能还没上 CDN（需要 push）。"""
    fid = str(item.get("id") or "")
    cover = str(item.get("cover") or "")
    url = thumb_url(spec, fid)
    if not fid or not cover.startswith(("http://", "https://")):
        return {"id": fid or "?", "ok": False, "err": "无外链封面", "url": url,
                "w": 0, "h": 0, "changed": False}
    if (item.get("cover_thumb") and item.get("cover_thumb_w") and not force):
        return {"id": fid, "ok": None, "err": "已有 cover_thumb，跳过", "url": url,
                "w": 0, "h": 0, "changed": False}
    local = thumb_dir / f"{fid}.webp"
    try:
        if local.exists() and not force:
            # 复用本地文件：cover_thumb 已在的说明 CDN 上也有（只补尺寸元数据），
            # 没在的说明上次推送失败（本次要重推）
            w, h = webp_size(local)
            return {"id": fid, "ok": True, "thumb": local.read_bytes(), "url": url,
                    "w": w, "h": h, "changed": not item.get("cover_thumb"),
                    "orig_kb": 0}
        raw = fetch_bytes(cover)
        thumb, w, h = make_thumb(raw)
        return {"id": fid, "ok": True, "thumb": thumb, "url": url,
                "w": w, "h": h, "changed": True, "orig_kb": len(raw) // 1024}
    except Exception as e:                    # 网络/解码失败：留待下次增量重跑
        return {"id": fid, "ok": False, "err": f"{type(e).__name__}: {e}"[:120],
                "url": url, "w": 0, "h": 0, "changed": False}


def git_push(repo: Path, n: int, proxy: str | None) -> bool:
    """图床仓库 add/commit/push。push 直连失败时自动改走 PROXY_FALLBACK 重试。"""
    def run(args: list[str], px: str | None) -> tuple[bool, str]:
        cmd = ["git", "-C", str(repo)]
        if px:
            cmd += ["-c", f"http.proxy={px}", "-c", f"https.proxy={px}"]
        r = subprocess.run(cmd + args, capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()

    if not run(["add", "-A", "blog/thumb"], proxy)[0]:
        print("  git add 失败")
        return False
    ok_c, out_c = run(["commit", "-m", f"thumb: MacroBiodiv 封面缩略图 {n} 张"], proxy)
    if not ok_c and "nothing to commit" not in out_c:
        print(f"  git commit 失败：{out_c[:200]}")
        return False
    ok_p, out_p = run(["push"], proxy)
    if not ok_p and proxy is None:
        print(f"  push 直连失败，改走代理 {PROXY_FALLBACK} 重试…")
        ok_p, out_p = run(["push"], PROXY_FALLBACK)
    if not ok_p:
        print(f"  push 失败：{out_p[:200]}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--force", action="store_true", help="忽略已有 cover_thumb 全部重做")
    ap.add_argument("--push", action="store_true", help="生成后提交并推送图床仓库")
    ap.add_argument("--dry-run", action="store_true", help="只报告目标清单")
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="图床仓库本地路径")
    ap.add_argument("--spec", default=DEFAULT_SPEC, help="图床 {owner}/{repo}@{branch}")
    ap.add_argument("--workers", type=int, default=6, help="并发下载数")
    ap.add_argument("--git-proxy", default=None,
                    help="push 走的代理（如 http://127.0.0.1:7897），下载不走代理；"
                         "不指定时 push 直连失败会自动改走内置兜底代理")
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
    targets = [it for it in items if args.force or not it.get("cover_thumb")
               or not it.get("cover_thumb_w")]   # 缺尺寸的顺带回填（读本地文件，不下载）
    print(f"外链封面 {len(items)} 张，本次待生成 {len(targets)} 张"
          + ("（--force 全量）" if args.force else "（增量）"))
    if args.dry_run:
        for it in targets:
            print(f"  - {it.get('id')} {str(it.get('cover'))[:70]}")
        return 0
    if not targets:
        return 0
    if not args.repo.is_dir():
        print(f"图床仓库不存在：{args.repo}（先 git clone，或用 --repo 指定路径）")
        return 2
    thumb_dir = args.repo / "blog" / "thumb"

    done: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for res in ex.map(lambda it: process_one(it, args.spec, args.force, thumb_dir),
                          targets):
            mark = {True: "✓", False: "✗", None: "·"}.get(res["ok"], "?")
            if not args.quiet:
                size = f" {res['orig_kb']}KB→{len(res['thumb']) // 1024}KB" if res.get("ok") else ""
                print(f"  {mark} {res['id']}{size} {res['err'] if res['ok'] is False else ''}")
            done.append(res)

    ok = [r for r in done if r["ok"]]
    skipped = len([r for r in done if r["ok"] is None])
    failed = len([r for r in done if r["ok"] is False])
    if not ok:
        print("· 没有需要生成的缩略图")
        return 0
    thumb_dir.mkdir(parents=True, exist_ok=True)
    for r in ok:
        (thumb_dir / f"{r['id']}.webp").write_bytes(r["thumb"])
    print(f"· 缩略图 {len(ok)} 张 → {thumb_dir}（{args.spec}）")
    if skipped:
        print(f"· 跳过 {skipped} 张（已有 cover_thumb）")
    if failed:
        print(f"· 失败 {failed} 张（重跑本命令即可增量补齐）")

    def write_back() -> None:
        for r in ok:
            for it in papers["items"]:
                if it.get("id") == r["id"]:
                    it["cover_thumb"] = r["url"]
                    it["cover_thumb_w"] = r["w"]
                    it["cover_thumb_h"] = r["h"]
                    break
        PAPERS_JSON.write_text(
            json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.push:
        write_back()
        print("· cover_thumb 已写回 papers.json")
        return 0
    # --push 模式：先推图床，推送成功才写 cover_thumb —— 否则站点会引用 CDN 上
    # 还不存在的缩略图（虽有原图回退链，也是白白发一轮请求）。
    # 没有新文件时（仅尺寸回填）直接写回，不做无谓的 push
    if not any(r["changed"] for r in ok):
        write_back()
        print("· 缩略图均已在图床，仅补尺寸元数据")
        return 0
    print("· 推送图床仓库…")
    if git_push(args.repo, len(ok), args.git_proxy):
        write_back()
        print("· cover_thumb 已写回 papers.json")
        return 0
    print("· 推送失败：cover_thumb 本次未写回（缩略图文件已留在本地克隆，重跑会直接推已生成文件）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
