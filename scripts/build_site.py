#!/usr/bin/env python3
"""MacroBiodiv 静态站生成器：读 meta/papers.json → 生成 site/ 纯静态站点。

用法：
    python scripts/build_site.py              # 生产构建（数据随 GitHub 仓走）

产出：
    site/index.html                文献库首页（卡片网格 + 分页 + 搜索 + 多维筛选 + 排序）
    site/{id}/index.html           每篇文献详情页（完整信息 + BibTeX + 上/下一篇）
    site/search/index.html         全站搜索独立页（结果行带命中高亮，任意终端可用）
    site/assets/style.css          样式
    site/assets/papers.js          分页/筛选/排序 + 统计打点 + BibTeX 复制
    site/assets/papers-data.js     全量文献元数据（首页网格与搜索页共用）

设计要点（样式与交互框架照搬 GeoSciPlot，把图片瀑布流换成文献信息卡片）：
  · 首屏卡片由 Python 直接输出静态 HTML（对爬虫/AI 引擎友好），翻页与筛选改由 JS 渲染
  · 筛选维度：标签 / 期刊 / 发表日期区间（按论文发表时间，不是收录时间）
  · 排序：发表 新→旧（默认）/ 旧→新 / 被引 多→少
  · 文献 id = DOI（小写）sha1 前 10 位，详情页目录与 id 一致
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_doi import to_bibtex  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "site.config.json"
PAPERS_JSON = ROOT / "meta" / "papers.json"
SITE = ROOT / "site"

PAGE_SIZE = 30
# 资源版本号（构建时间戳）：CSS/JS 引用统一带 ?v=，部署后老访客的浏览器
# 不会再用缓存的旧脚本配新页面（GeoSciPlot 踩过的坑，直接继承对策）
BUILD_VER = str(int(time.time()))

DEFAULT_CFG = {
    "title": "MacroBiodiv",
    "subtitle": "宏观生物多样性文献库",
    "lede": "收集宏观生态与生物多样性领域公开发表的文献基本信息，可搜索、可筛选、可溯源。",
    "repo": "MacroBiodiv",
    "branch": "main",
    "owner": "zbhgis",
    "tracker": "/api/v1/track",
    "api": "",
    "site_url": "",                  # 如 https://macrobiodiv.zbhgis.com，用于 og:url / sitemap
    "server": {"host": "", "webroot": "/var/www/macrobiodiv"},
}


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if CFG_PATH.exists():
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    return cfg


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def haystack(p: dict) -> str:
    """检索用全文字段（小写拼接）—— 覆盖全站内容：标题 / 作者 / DOI / 期刊 /
    关键词 / 标签 / 备注 / 摘要全文等，对齐 GeoSciPlot「全站搜索」的口径。"""
    return " ".join(filter(None, [
        p.get("id", ""),
        p.get("doi") or "",
        p.get("title") or "",
        p.get("subtitle") or "",
        p.get("title_zh") or "",
        " ".join(p.get("authors") or []),
        p.get("journal") or "",
        p.get("journal_short") or "",
        str(p.get("year") or ""),
        p.get("published") or "",
        p.get("added") or "",
        p.get("note") or "",
        p.get("publisher") or "",
        p.get("type") or "",
        p.get("article_type") or "",   # 体裁可搜（如 Perspective / Review）
        " ".join(p.get("keywords") or []),
        " ".join(p.get("tags") or []),
        p.get("abstract") or "",
        p.get("abstract_zh") or "",
    ])).lower()


def authors_preview(authors: list, n: int = 3) -> str:
    """卡片作者行：前 n 位 + et al.。"""
    if not authors:
        return "—"
    if len(authors) <= n + 1:
        return ", ".join(authors)
    return ", ".join(authors[:n]) + " et al."


def journal_badge(p: dict) -> str:
    return p.get("journal_short") or p.get("journal") or ""


def abstract_disp(p: dict, limit: int = 200) -> str:
    """卡片摘要节选：有中文摘要优先中文（中文读者扫读更快），否则英文。
    静态首屏与 JS 渲染的 papers-data.js 共用同一份，保证一致。"""
    ab = (p.get("abstract_zh") or "").strip() or (p.get("abstract") or "").strip()
    if not ab:
        return ""
    return ab[:limit] + "…" if len(ab) > limit else ab


def sort_items(items: list) -> list:
    """站点统一顺序：发表日期 新→旧，同日期按 id 升序 —— 与 site.js 默认排序
    完全一致（静态首屏 = JS 默认渲染），详情页上一篇/下一篇也按这个顺序走。"""
    return sorted(sorted(items, key=lambda p: p.get("id") or ""),
                  key=lambda p: p.get("published") or "", reverse=True)


CSS = """\
:root{--bg:#0d1117;--text:#e6edf3;--dim:#8b949e;--faint:#6e7681;--line:#1c2129;--line2:#30363d;--accent:#58a6ff;--card:#161b22;color-scheme:dark}
:root[data-theme=dark]{color-scheme:dark}
:root[data-theme=light]{--bg:#fff;--text:#1f2328;--dim:#59636e;--faint:#818b98;--line:#e8ebef;--line2:#d0d7de;--accent:#0969da;--card:#f6f8fa;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 ui-sans-serif,system-ui,"PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
/* 桌面/平板给右侧控件队列留出通道：队列占 right:16 + 42 = 58px，
   这里把正文右内边距顶到 74px，避免工具栏、卡片直接压到队列下面。
   只在队列仍是「垂直居中」的宽度区间生效（>640px），窄屏队列改横排后由 body 底边距接管。 */
@media (min-width:641px){
  .wrap{max-width:1180px;padding-right:74px}
}
header.site{padding:72px 0 0}
/* 右侧控件队列：桌面端垂直居中于视口右侧，与主站 zbhgis.com 的 .v3-rail 保持同一形状
   （42px 正圆 · --card 实底 · 发丝边框 · hover 变强调色并 scale 1.06） */
.fab{position:fixed;right:16px;top:50%;transform:translateY(-50%);display:flex;flex-direction:column;gap:9px;z-index:50}
.tbtn{display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;border:1px solid var(--line2);border-radius:50%;background:var(--card);color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s,transform .16s,opacity .25s ease}
.tbtn:hover{color:var(--accent);border-color:var(--accent);transform:scale(1.06)}
.tbtn svg{width:17px;height:17px;flex:none}
/* 控件内联小箭头：尺寸统一由 CSS 定，用 em 让图标跟着字号缩放；
   颜色一律 currentColor —— 与文字一起被 hover 染色，避免出现"文字变色图标不变"的割裂 */
.ico{width:1em;height:1em;flex:none;transition:transform .18s ease}
.ico-l{width:14px;height:14px}
.ico-r{width:13px;height:13px}
/* 回到顶部按钮：不参与 hover 的 scale 过渡，单独过渡 opacity，
   否则淡入淡出时会跟着缩放抖动 */
#topBtn{opacity:0;pointer-events:none}
#topBtn.show{opacity:1;pointer-events:auto}
/* 窄屏（≤640px）：垂直居中的队列会压住满宽内容，
   改为右下角横排 —— 断点、定位、尺寸全部对齐主站 zbhgis.com 的 .v3-rail 窄屏方案（统一设计）。
   同时给 body 补足底部内边距，避免遮住 footer。 */
@media (max-width:640px){
  .fab{right:12px;bottom:14px;top:auto;transform:none;flex-direction:row;gap:7px}
  .tbtn{width:36px;height:36px}
  .tbtn svg{width:15px;height:15px}
  body{padding-bottom:calc(14px + 36px + 16px + env(safe-area-inset-bottom))}
}
.tbtn .ic-sun{display:inline}.tbtn .ic-moon{display:none}
:root[data-theme=light] .tbtn .ic-sun{display:inline}:root[data-theme=light] .tbtn .ic-moon{display:none}
:root[data-theme=dark] .tbtn .ic-sun{display:none}:root[data-theme=dark] .tbtn .ic-moon{display:inline}
.kicker{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:0}
h1{display:flex;align-items:center;gap:18px;font-size:clamp(44px,6.5vw,68px);line-height:1.08;letter-spacing:-.03em;margin:24px 0 0;font-weight:700}
h1 img.logo{height:clamp(44px,5.4vw,58px);width:auto;flex:none}
.lede{font-size:16px;color:var(--dim);max-width:52ch;margin:20px 0 0}
.gh-note{display:inline-flex;align-items:center;gap:9px;margin:18px 0 0;padding:9px 16px;border:1px solid var(--accent);border-left-width:3px;border-radius:6px;background:var(--card);font-size:13.5px;color:var(--text)}
.gh-note svg{width:16px;height:16px;flex:none;color:var(--accent)}
.meta-row{margin:28px 0 0;padding:14px 0;border-top:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--dim)}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:22px 0 6px}
.search{flex:1 1 260px;max-width:380px;padding:8px 12px;border:1px solid var(--line2);border-radius:4px;background:transparent;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
/* 搜索框 + 搜索按钮（连体） */
.searchbox{display:inline-flex;align-items:center;gap:0;flex:1 1 300px;max-width:430px;border:1px solid var(--line2);border-radius:4px;background:transparent;transition:border-color .16s}
.searchbox:focus-within{border-color:var(--accent)}
.searchbox .sic{width:14px;height:14px;flex:none;margin-left:11px;color:var(--faint)}
.searchbox .search{flex:1;min-width:0;border:none;background:transparent;padding:8px 10px;max-width:none}
.searchbox .search:focus{outline:none}
.searchbox button{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:0 14px;height:34px;border:none;border-left:1px solid var(--line2);border-radius:0 3px 3px 0;background:transparent;color:var(--dim);cursor:pointer;transition:color .16s,background-color .16s}
.searchbox button:hover{color:var(--accent);background:var(--card)}
.search:focus{outline:none;border-color:var(--accent)}
.search::placeholder{color:var(--faint)}
select{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:7px 9px;border:1px solid var(--line2);border-radius:4px;background:transparent;color:var(--dim)}
.reset{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:7px 11px;border:1px solid var(--line2);border-radius:4px;background:transparent;color:var(--faint);cursor:pointer}
.reset:hover{color:var(--accent);border-color:var(--accent)}
#filters[hidden]{display:none}
.sorter{display:inline-flex;align-items:center;gap:2px;padding:3px;border:1px solid var(--line2);border-radius:999px;background:var(--card)}
.sorter button{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;padding:6px 13px;border:none;border-radius:999px;background:transparent;color:var(--dim);cursor:pointer;transition:color .15s,background-color .15s}
.sorter button:hover{color:var(--text)}
.sorter button[aria-pressed=true]{background:var(--accent);color:var(--bg)}
.sorter button svg{width:13px;height:13px;flex:none}
.dateinp{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:6px 9px;border:1px solid var(--line2);border-radius:4px;background:transparent;color:var(--dim);color-scheme:dark light}
.fgroup{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:10px 0 0;padding-bottom:8px;border-bottom:1px solid var(--line)}
.flabel{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;color:var(--faint);min-width:44px;letter-spacing:.06em}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chips button{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:4px 10px;border:1px solid var(--line2);border-radius:4px;background:transparent;color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s}
.chips button:hover{color:var(--text)}
.chips button[aria-pressed=true]{color:var(--accent);border-color:var(--accent)}
/* ── 文献卡片网格：等宽 grid（卡片高度由内容决定，文献没有图片，
   不需要瀑布流；卡片信息密度一致，等宽三列最整齐） ── */
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:26px}
@media (max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:640px){.grid{grid-template-columns:1fr;gap:11px}}
.card{display:flex;flex-direction:column;gap:7px;min-width:0;padding:13px 15px;border:1px solid var(--line);border-radius:6px;background:var(--card);transition:border-color .16s}
.card:hover{border-color:var(--accent)}
.c-top{display:flex;align-items:center;justify-content:space-between;gap:8px;min-width:0}
.c-l{display:flex;align-items:center;gap:6px;min-width:0;flex:1}
.c-ty{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10px;color:var(--dim);border:1px solid var(--line2);border-radius:3px;padding:1px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:12em}
.c-j{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 45%,transparent);border-radius:3px;padding:1px 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.c-y{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;color:var(--faint);flex:none}
.c-t{font-size:14.5px;font-weight:600;line-height:1.5;color:var(--text);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.c-a{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.c-abs{font-size:12px;line-height:1.65;color:var(--dim);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.c-f{margin-top:auto;display:flex;align-items:center;justify-content:space-between;gap:8px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;color:var(--faint)}
.c-f .tags{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.c-f .ct{flex:none}
.pgbar{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:6px;margin:44px 0 0;padding-top:24px;border-top:1px solid var(--line)}
/* 步进按钮：只有文字 + 一枚内联箭头，hover 才点亮（与主站 .v3-pager-step 同语言） */
.pgbar button{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.35;padding:5px 11px;border:1px solid var(--line2);border-radius:6px;background:transparent;color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s}
.pgbar button:hover:not(:disabled){color:var(--accent);border-color:var(--accent);background:var(--card)}
.pgbar button:disabled{opacity:.3;cursor:not-allowed}
/* 箭头 hover 时朝翻页方向平移 2px；禁用态不动 */
#prev:hover:not(:disabled) .ico-l{transform:translateX(-2px)}
#next:hover:not(:disabled) .ico-r{transform:translateX(2px)}
/* 页码：等宽 + 定宽定高，选中态用强调色描边配极淡底，不填色（保持克制的工程感） */
.pgnum{display:inline-flex;align-items:center;justify-content:center;min-width:30px;height:30px;padding:0 7px;border:1px solid var(--line2);border-radius:6px;background:transparent;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;font-variant-numeric:tabular-nums;color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s}
.pgnum:hover{color:var(--text);border-color:var(--accent)}
.pgnum[data-on=true]{color:var(--accent);border-color:var(--accent);background:var(--card)}
/* 当前页附近被"窗口"截断时用省略号占位，不可点 */
.pggap{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:30px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--faint);user-select:none}
/* 页码区与「共 N 篇」之间用一条发丝竖线隔开 */
.pgbar .info{margin-left:8px;padding-left:14px;border-left:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;color:var(--faint);white-space:nowrap}
.empty{padding:52px 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--faint);display:none;text-align:center}
footer.site{margin-top:56px;padding:24px 0 64px;border-top:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;color:var(--faint);display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between}
/* ── 详情页 ── */
.p-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:26px 0 0}
.p-top .c-j{font-size:11.5px}
.p-top .p-type,.p-top .p-ct{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;color:var(--faint)}
.p-title{font-size:clamp(22px,3.2vw,32px);line-height:1.4;letter-spacing:-.01em;margin:14px 0 0;font-weight:700}
.alt-title{font-size:15px;color:var(--dim);margin:10px 0 0;line-height:1.6}
.p-auth{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;color:var(--dim);margin:14px 0 0;line-height:1.9;word-break:break-word}
/* ── 公众号推文式分节：01 信息 / 02 摘要 / 03 引用 ── */
.sec{margin-top:46px}
.sec-h{display:flex;align-items:baseline;gap:12px;margin:0 0 18px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.sec-h .no{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--accent);letter-spacing:.12em}
.sec-h .tx{font-size:19px;font-weight:700;letter-spacing:-.01em}
/* 信息卡：小标签在上、值在下，自动换行铺开（公众号文末信息卡风格） */
.info-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px 20px}
.ig{min-width:0}
.ig i{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-style:normal;font-size:10.5px;color:var(--faint);letter-spacing:.08em;margin-bottom:5px}
.ig b{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13.5px;font-weight:500;color:var(--text);line-height:1.7;word-break:break-word}
.ig b .hl{color:var(--text)}
.ig b a{border-bottom:1px solid var(--line2);transition:color .16s,border-color .16s}
.ig b a:hover{color:var(--accent);border-color:var(--accent)}
.ig.wide{grid-column:1/-1}
.ig b a.tag{display:inline-block;margin:0 8px 8px 0;padding:3px 11px;border:1px solid var(--line2);border-radius:4px;font-size:13px;color:var(--dim)}
.ig b a.tag:hover{color:var(--accent);border-color:var(--accent)}
/* 摘要：中文为主阅读区（两端对齐、宽行距），英文原题收合 */
.abs-main{font-size:16px;line-height:2.05;margin:0;color:var(--text);text-align:justify}
.abs-main.abs-en-only{color:var(--dim);font-size:14px;line-height:1.9}
.abs-alt{margin-top:18px}
.abs-alt summary{cursor:pointer;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--faint);transition:color .16s}
.abs-alt summary:hover{color:var(--accent)}
.abs-alt p{margin:12px 0 0;font-size:13.5px;line-height:1.9;color:var(--dim);text-align:justify}
.abs-note{margin:18px 0 0;padding:11px 14px;border-left:2px solid var(--accent);background:var(--card);border-radius:0 6px 6px 0;font-size:13px;line-height:1.8;color:var(--dim)}
/* 引用条（GB/T 7714）+ 复制按钮 */
.cite-line{padding:13px 15px;border:1px solid var(--line);border-radius:6px;background:var(--card);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.85;color:var(--dim);word-break:break-word;margin:0 0 12px}
.a-end{margin:46px 0 0;text-align:center;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:.4em;color:var(--faint);user-select:none}
/* BibTeX：复制按钮 + 折叠查看 */
.btx{margin-top:28px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.btx button{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:6px 13px;border:1px solid var(--line2);border-radius:6px;background:transparent;color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s}
.btx button:hover{color:var(--accent);border-color:var(--accent);background:var(--card)}
.btx summary{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--faint);cursor:pointer}
.btx summary:hover{color:var(--accent)}
.btx pre{margin:12px 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:6px;background:var(--card);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;line-height:1.65;color:var(--dim);overflow:auto;max-height:320px;white-space:pre-wrap;word-break:break-all}
.btx .full{flex-basis:100%}
/* ── 详情页：上一篇 / 下一篇（与主站 .v3-prevnext 同语言）
   两列等宽卡片；缺一篇时用虚线占位，避免唯一那篇被拉成通栏 ── */
.pager{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:46px 0 0;padding-top:24px;border-top:1px solid var(--line)}
.pager a{display:flex;flex-direction:column;gap:7px;min-width:0;padding:12px 14px;border:1px solid var(--line2);border-radius:6px;transition:color .16s,border-color .16s,background-color .16s}
.pager a:hover{border-color:var(--accent);background:var(--card)}
.pager .dir{display:inline-flex;align-items:center;gap:5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.1em;color:var(--faint);transition:color .16s}
.pager a:hover .dir{color:var(--accent)}
.pager a:hover .ico-l{transform:translateX(-2px)}
.pager a:hover .ico-r{transform:translateX(2px)}
.pager .ttl{font-size:13.5px;line-height:1.5;color:var(--text);transition:color .16s;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pager a:hover .ttl{color:var(--accent)}
.pager .pn-next{text-align:right}
.pager .pn-next .dir{justify-content:flex-end}
.pager .pn-empty{min-height:68px;border:1px dashed var(--line);border-radius:6px}
/* ── 返回全部：发丝边框小按钮，箭头 hover 左移 ── */
.back{display:inline-flex;align-items:center;gap:7px;margin:36px 0 20px;padding:5px 11px 5px 9px;border:1px solid var(--line2);border-radius:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.35;color:var(--dim);transition:color .16s,border-color .16s,background-color .16s}
.back:hover{color:var(--accent);border-color:var(--accent);background:var(--card)}
.back:hover .ico-l{transform:translateX(-2px)}
/* ── 窄屏收尾（必须写在上面这些规则之后，否则同优先级会被覆盖） ──
   分页条允许换行、末页提示去掉那根竖线；上一篇/下一篇改为上下堆叠，
   右对齐失去意义统一左对齐；被隐藏的占位块正是"堆叠后不再需要"的元素 */
@media (max-width:640px){
  .pgbar{gap:5px}
  .pgbar .info{margin-left:0;padding-left:0;border-left:none}
  .pager{grid-template-columns:1fr}
  .pager .pn-next{text-align:left}
  .pager .pn-next .dir{justify-content:flex-start}
  .pager .pn-empty{display:none}
}
/* ── 全站搜索独立页（/search/）：版式参考主站 zbhgis.com 的 /search
   （kicker + 大标题 + 结果行）；文献没有缩略图，结果行就是标题 + 元信息；
   颜色一律取自主题变量，明暗两套自动跟随 ── */
.spage-title{font-size:clamp(30px,4.5vw,44px);letter-spacing:-.02em;margin:18px 0 0}
.spage-q{display:block;width:100%;max-width:520px;margin:24px 0 0;padding:10px 13px;border:1px solid var(--line2);border-radius:6px;background:transparent;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.spage-q:focus{outline:none;border-color:var(--accent)}
.spage-count{margin:14px 0 2px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;color:var(--faint)}
.spage-list{margin-top:6px}
.sres{display:flex;align-items:center;gap:14px;padding:11px 6px;border-top:1px solid var(--line)}
.sres:hover{background:var(--card)}
.sres-body{min-width:0;flex:1}
.sres-id{display:block;font-size:14px;line-height:1.5;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sres:hover .sres-id{color:var(--accent)}
.sres-meta{display:block;margin-top:3px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sres-meta .sres-sep{font-style:normal;color:var(--faint);margin:0 6px}
mark{background:color-mix(in srgb,var(--accent) 24%,transparent);color:inherit;border-radius:2px;padding:0 1px}
.spage-hint,.spage-empty{padding:26px 6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--faint)}
@media (max-width:640px){
  .sres{gap:10px}
}
"""

JS = """\
(function () {
  var CFG = window.MBD, ITEMS = window.MBD_DATA || [], PAGE = window.MBD_PAGE || 30;
  if (!CFG) return;

  /* 统计打点：所有页面（首页 + 详情页）都要执行。之前放在网格逻辑之后，
     详情页因没有 #grid 提前 return，打点从未跑过（GeoSciPlot 踩过的坑） */
  if (CFG.tracker && !location.hostname.match(/^(localhost|127\\.0\\.0\\.1|)$/)) {
    try {
      if (navigator.doNotTrack === "1") return;
      fetch(CFG.tracker, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path: "/macrobiodiv" + location.pathname, referrer: document.referrer || undefined }),
        keepalive: true,
      }).catch(function () {});
    } catch (e) {}
  }

  /* ── 每篇文献浏览量（来自统计服务的按 path 计数） ── */
  var viewsEl = document.getElementById("views");
  if (viewsEl) {
    fetch((CFG.api || "") + "/api/v1/stats/views?prefix=/macrobiodiv/")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.items) throw 0;
        var map = {};
        d.items.forEach(function (x) { map[x.path.replace(/\\/$/, "")] = x.views; });
        var n = map["/macrobiodiv" + location.pathname.replace(/\\/$/, "")] || 0;
        viewsEl.textContent = n ? n + " 次" : "首次";
        viewsEl.style.color = "var(--text)";
      }).catch(function () { viewsEl.textContent = "—"; });
  }

  var themeBtn = document.getElementById("themeBtn");
  if (themeBtn) themeBtn.addEventListener("click", function () {
    var root = document.documentElement;
    // 默认暗色（不跟随系统偏好）；仅当访客手动切过才用其选择
    var light = root.getAttribute("data-theme") === "light";
    var t = light ? "dark" : "light";
    root.setAttribute("data-theme", t);
    try { localStorage.setItem("mbd-theme", t); } catch (e) {}
  });
  var topBtn = document.getElementById("topBtn");
  if (topBtn) {
    topBtn.addEventListener("click", function () { window.scrollTo({ top: 0, behavior: "smooth" }); });
    // 阈值 400px 与主站 zbhgis.com 的 .v3-rail-top 保持一致，两站行为统一
    var onScroll = function () { topBtn.classList.toggle("show", window.scrollY > 400); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ── 详情页：通用复制按钮（data-copy 指向源元素 id：引用条 / BibTeX） ── */
  var copyFallback = function (t) {
    var ta = document.createElement("textarea");
    ta.value = t;
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    ta.remove();
  };
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-copy"));
      var txt = src ? (src.textContent || "") : "";
      var old = btn.textContent;
      var done = function () {
        btn.textContent = "✓ 已复制";
        setTimeout(function () { btn.textContent = old; }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done, function () { copyFallback(txt); done(); });
      } else { copyFallback(txt); done(); }
    });
  });

  /* ── 全站搜索独立页（/search/）：静态站没有检索后端，直接在 papers-data.js
     的全量元数据上做客户端匹配（标题 / 作者 / DOI / 期刊 / 关键词 / 标签，
     多词空格分隔 = 同时命中）。放在网格逻辑之前 —— 搜索页没有 #grid 会提前 return ── */
  var spageQ = document.getElementById("spage-q");
  if (spageQ) {
    var sList = document.getElementById("spage-list");
    var sCount = document.getElementById("spage-count");
    var sUp = sList ? (sList.getAttribute("data-up") || "") : "";

    function escHtml(s) {
      return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }
    function hl(text, tokens) {
      // 先转义再高亮：token 也过同一套转义，两边一致，特殊字符不会破坏 HTML 结构
      var out = escHtml(text);
      tokens.forEach(function (t) {
        var et = escHtml(t).replace(/[.*+?^${}()|[\\]\\\\]/g, "$&");
        out = out.replace(new RegExp(et, "gi"), "<mark>$&</mark>");
      });
      return out;
    }
    function renderSearch(raw) {
      var tokens = raw.trim().toLowerCase().split(/\\s+/).filter(Boolean);
      if (!tokens.length) {
        sCount.textContent = "";
        sList.innerHTML = '<p class="spage-hint">输入 标题 / 作者 / DOI / 期刊 / 关键词 / 摘要关键词 开始检索；多个词用空格分隔（需同时命中）</p>';
        return;
      }
      var hits = ITEMS.filter(function (it) {
        var hay = (it.se || "").toLowerCase();
        return tokens.every(function (t) { return hay.indexOf(t) > -1; });
      });
      sCount.textContent = "找到 " + hits.length + " / " + ITEMS.length + " 篇";
      if (!hits.length) {
        sList.innerHTML = '<p class="spage-empty">未找到与 “' + escHtml(raw) + '” 相关的文献</p>';
        return;
      }
      sList.innerHTML = hits.map(function (it) {
        var meta = [];
        if (it.a) meta.push(hl(it.a, tokens));
        if (it.j) meta.push(hl(it.j, tokens));
        if (it.y) meta.push(escHtml(it.y));
        if (it.doi) meta.push(hl(it.doi, tokens));
        // 主标题已是中文优先；此时把英文原题放进元信息行（可高亮），英文关键词搜索不失上下文
        if (it.tz && it.t === it.tz && it.te) meta.push(hl(it.te, tokens));
        return '<a class="sres" href="' + sUp + escHtml(it.id) + '/">'
          + '<span class="sres-body"><span class="sres-id">' + hl(it.t, tokens) + '</span>'
          + '<span class="sres-meta">' + meta.join('<i class="sres-sep">·</i>') + '</span></span></a>';
      }).join("");
    }
    // ?q= 预填：与主站 /search?q= 行为一致，结果可分享
    var sq = "";
    try { sq = new URLSearchParams(location.search).get("q") || ""; } catch (e) {}
    spageQ.value = sq;
    renderSearch(sq);
    spageQ.addEventListener("input", function () {
      renderSearch(spageQ.value);
      try {
        var v = spageQ.value.trim();
        history.replaceState(null, "", v ? "?q=" + encodeURIComponent(v) : location.pathname);
      } catch (e) {}
    });
  }

  var grid = document.getElementById("grid");
  if (!grid) return;

  /* ── 分页 + 筛选 + 排序 ── */
  var state = { q: "", tag: "*", journal: "*", from: "", to: "", sort: "pub", page: 1, per: PAGE };
  try {
    var savedPer = parseInt(localStorage.getItem("mbd-per"), 10);
    if ([20, 30, 50].indexOf(savedPer) > -1) state.per = savedPer;   // 仅接受合法档位，旧值自动回默认 30
    if (localStorage.getItem("mbd-sort2")) { state.sort = localStorage.getItem("mbd-sort2"); }
  } catch (e) {}
  var q = document.getElementById("q");
  var empty = document.getElementById("empty");
  var count = document.getElementById("count");
  var info = document.getElementById("pageinfo");
  var prev = document.getElementById("prev");
  var next = document.getElementById("next");
  var pgnums = document.getElementById("pgnums");

  /* 视口越窄，页码窗口收得越小：
     ≥1000 → 当前页 ±2 且总页数 ≤9 时全列；≥640 → ±1；更窄 → 只列当前页（总数缩到 4 个节点） */
  function windowSize() {
    var w = window.innerWidth || 1024;
    return w < 640 ? 0 : (w < 1000 ? 1 : 2);
  }

  /* 页码节点用事件委托：一次绑定，翻页时只重建 innerHTML，不重新挂监听 */
  function paintNums(page, pages) {
    if (!pgnums) return;
    if (pages <= 1) { pgnums.innerHTML = ""; return; }
    var win = windowSize();
    var nums = [];
    for (var i = 1; i <= pages; i++) {
      if (i === 1 || i === pages || Math.abs(i - page) <= win) nums.push(i);
    }
    var html = "", last = 0;
    nums.forEach(function (i) {
      if (last && i - last > 1) html += '<span class="pggap">…</span>';
      html += '<button type="button" class="pgnum" data-page="' + i + '"'
            + (i === page ? ' data-on="true" aria-current="page"' : '')
            + ' title="第 ' + i + ' 页">' + i + '</button>';
      last = i;
    });
    pgnums.innerHTML = html;
  }
  if (pgnums) {
    pgnums.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest(".pgnum") : null;
      if (!b) return;
      var n = parseInt(b.getAttribute("data-page"), 10);
      if (!n || n === state.page) return;
      state.page = n;
      render();
      var bar = document.querySelector(".pgbar");
      if (bar && bar.getBoundingClientRect().top < 0) bar.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function pass(it) {
    if (state.tag !== "*" && (it.tg || []).indexOf(state.tag) === -1) return false;
    if (state.journal !== "*" && (it.j || "") !== state.journal) return false;
    if (state.from && (it.pd || "") < state.from) return false;
    if (state.to && (it.pd || "") > state.to) return false;
    if (state.q && (it.se || "").indexOf(state.q) === -1) return false;
    return true;
  }
  function cmp(a, b) {
    var dir = state.sort === "pub_asc" ? 1 : -1;
    if (state.sort === "cited") {
      var dc = ((b.ct || 0) - (a.ct || 0));
      if (dc) return dc;
    }
    var aa = a.pd || "", ab = b.pd || "";
    if (aa !== ab) return (aa < ab ? -1 : 1) * dir;
    // 同日期内按 id 排：与 build_site.py 的静态首屏顺序保持一致
    return (a.id || "").localeCompare(b.id || "");
  }
  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined) e.textContent = txt;
    return e;
  }
  function cardNode(it) {
    var a = el("a", "card");
    a.href = it.id + "/";
    // 主标题已中文优先（数据构建期定好）；悬停提示给另一种语言的标题
    a.title = (it.tz && it.t === it.tz) ? (it.te || it.t) : (it.tz || it.t);
    var top = el("span", "c-top");
    var left = el("span", "c-l");
    var jb = el("span", "c-j", it.jb || it.j || "—");
    if (it.j && it.jb) jb.title = it.j;
    left.appendChild(jb);
    if (it.pt) left.appendChild(el("span", "c-ty", it.pt));
    top.appendChild(left);
    top.appendChild(el("span", "c-y", it.y || ""));
    a.appendChild(top);
    a.appendChild(el("span", "c-t", it.t));
    a.appendChild(el("span", "c-a", it.a || ""));
    if (it.ab) a.appendChild(el("span", "c-abs", it.ab));
    var foot = el("span", "c-f");
    foot.appendChild(el("span", "tags", it.sub || "—"));
    foot.appendChild(el("span", "ct", (it.ct || 0) > 0 ? "被引 " + it.ct : ""));
    a.appendChild(foot);
    return a;
  }
  function perSize(list) { return state.per > 0 ? state.per : (list.length || 1); }
  function render() {
    var list = ITEMS.filter(pass).sort(cmp);
    var per = perSize(list);
    var pages = Math.max(1, Math.ceil(list.length / per));
    if (state.page > pages) state.page = pages;
    if (state.page < 1) state.page = 1;
    var slice = list.slice((state.page - 1) * per, state.page * per);
    grid.innerHTML = "";
    slice.forEach(function (it) { grid.appendChild(cardNode(it)); });
    if (info) info.textContent = "共 " + pages + " 页";
    if (prev) prev.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= pages;
    paintNums(state.page, pages);
    if (count) {
      var filtered = state.q || state.tag !== "*" || state.journal !== "*" || state.from || state.to;
      count.textContent = filtered ? "匹配 " + list.length + " / " + ITEMS.length + " 篇"
                                   : "共 " + ITEMS.length + " 篇";
    }
    if (empty) empty.style.display = list.length ? "none" : "block";
    syncFilterBtn();
  }
  function resetPage() { state.page = 1; render(); }

  function bindChips(sel) {
    var box = document.querySelector(sel);
    if (!box) return;
    box.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        state[box.getAttribute("data-key")] = b.getAttribute("data-v");
        box.querySelectorAll("button").forEach(function (x) {
          x.setAttribute("aria-pressed", String(x === b));
        });
        resetPage();
      });
    });
  }
  ["[data-key=tag]", "[data-key=journal]"].forEach(bindChips);

  if (q) {
    q.addEventListener("input", function () { state.q = q.value.trim().toLowerCase(); resetPage(); });
    q.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { q.value = ""; state.q = ""; resetPage(); }
      if (e.key === "Enter") { e.preventDefault(); runSearch(); }
    });
  }
  var qBtn = document.getElementById("qBtn");
  function runSearch() {
    state.q = (q ? q.value : "").trim().toLowerCase();
    resetPage();
    var g = document.getElementById("grid");
    if (g && g.getBoundingClientRect().top < 0) g.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (qBtn) qBtn.addEventListener("click", runSearch);
  var sortseg = document.getElementById("sortseg");
  if (sortseg) {
    if (["pub", "pub_asc", "cited"].indexOf(state.sort) === -1) state.sort = "pub";
    var sortBtns = sortseg.querySelectorAll("button");
    sortBtns.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-sort") === state.sort));
      b.addEventListener("click", function () {
        state.sort = b.getAttribute("data-sort");
        try { localStorage.setItem("mbd-sort2", state.sort); } catch (e) {}
        sortBtns.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        resetPage();
      });
    });
  }
  var fFrom = document.getElementById("f-from"), fTo = document.getElementById("f-to");
  if (fFrom) fFrom.addEventListener("change", function () { state.from = fFrom.value; resetPage(); });
  if (fTo) fTo.addEventListener("change", function () { state.to = fTo.value; resetPage(); });
  if (prev) prev.addEventListener("click", function () { state.page -= 1; render(); });
  if (next) next.addEventListener("click", function () { state.page += 1; render(); });

  var reset = document.getElementById("reset");
  if (reset) reset.addEventListener("click", function () {
    state = { q: "", tag: "*", journal: "*", from: "", to: "", sort: state.sort, page: 1,
              per: state.per };   // 保留每页数量（此 bug 继承自 GeoSciPlot，文献多时重置后分页会失效）
    var fF = document.getElementById("f-from"), fT = document.getElementById("f-to");
    if (fF) fF.value = "";
    if (fT) fT.value = "";
    if (q) q.value = "";
    document.querySelectorAll(".chips").forEach(function (box) {
      box.querySelectorAll("button").forEach(function (x, i) {
        x.setAttribute("aria-pressed", String(i === 0));
      });
    });
    render();
  });

  /* ── 每页显示数量 ── */
  var perSel = document.getElementById("perpage");
  if (perSel) {
    perSel.value = String(state.per);
    perSel.addEventListener("change", function () {
      state.per = parseInt(perSel.value, 10) || 0;
      try { localStorage.setItem("mbd-per", String(state.per)); } catch (e) {}
      resetPage();
    });
  }

  /* ── 筛选区折叠（状态记在 localStorage） ── */
  var filterBtn = document.getElementById("filterBtn");
  var filterBox = document.getElementById("filters");
  function activeFilters() {
    var parts = [];
    if (state.tag !== "*") parts.push("标签 " + state.tag);
    if (state.journal !== "*") parts.push("期刊 " + state.journal);
    if (state.from || state.to) parts.push("时间 " + (state.from || "…") + " ~ " + (state.to || "…"));
    if (state.q) parts.push("搜索 " + state.q);
    return parts;
  }
  function syncFilterBtn() {
    if (!filterBtn || !filterBox) return;
    var open = !filterBox.hasAttribute("hidden");
    var list = activeFilters();
    filterBtn.textContent = (open ? "筛选 ▴" : "筛选 ▾") + (list.length ? " · " + list.length : "");
    filterBtn.setAttribute("aria-expanded", String(open));
    filterBtn.title = list.length ? "当前筛选：" + list.join(" / ") : "展开或收起筛选条件";
  }
  if (filterBox) {
    var collapsed = false;
    try { collapsed = localStorage.getItem("mbd-filters") === "hidden"; } catch (e) {}
    if (collapsed) filterBox.setAttribute("hidden", "");
  }
  if (filterBtn && filterBox) {
    filterBtn.addEventListener("click", function () {
      var open = !filterBox.hasAttribute("hidden");
      if (open) filterBox.setAttribute("hidden", ""); else filterBox.removeAttribute("hidden");
      try { localStorage.setItem("mbd-filters", open ? "hidden" : "shown"); } catch (e) {}
      syncFilterBtn();
    });
  }

  /* 支持带参数的链接（标签跳转 / 分享筛选结果）：/?tag=海冰&journal=Nature&sort=cited */
  var applied = false;
  try {
    var params = new URLSearchParams(location.search);
    ["q", "tag", "journal", "from", "to", "sort"].forEach(function (k) {
      var v = params.get(k);
      if (!v) return;
      if (k === "sort") {
        // 排序可分享，但仅本次生效 —— 分享链接不应永久改写接收者的排序偏好
        if (["pub", "pub_asc", "cited"].indexOf(v) === -1) return;
        applied = true;
        state.sort = v;
        if (sortseg) {
          sortseg.querySelectorAll("button").forEach(function (x) {
            x.setAttribute("aria-pressed", String(x.getAttribute("data-sort") === v));
          });
        }
        return;
      }
      applied = true;
      if (k === "q") {
        state.q = v.trim().toLowerCase();
        if (q) q.value = v;
      } else {
        state[k] = v;
        var box = document.querySelector('.chips[data-key="' + k + '"]');
        if (box) {
          box.querySelectorAll("button").forEach(function (x) {
            x.setAttribute("aria-pressed", String(x.getAttribute("data-v") === v));
          });
        } else {
          // 日期区间：回填输入框，避免"筛选生效但输入框是空的"的困惑
          var inp = document.getElementById("f-" + k);
          if (inp) inp.value = v;
        }
      }
    });
  } catch (e) {}

  /* 首屏卡片是构建时静态输出的（对爬虫友好）。只有当"每页数量/排序"被用户改过、
     或链接带了筛选参数时，才用 JS 重新渲染，保证 DOM 与状态一致。
     （不重渲染的默认情况下，静态输出与 JS 首屏完全一致） */
  var needRender = applied || state.per !== PAGE || state.sort !== "pub";
  if (needRender) {
    state.page = 1;
    render();
  } else {
    var per0 = perSize(ITEMS);
    var pages0 = Math.max(1, Math.ceil(ITEMS.length / per0));
    if (info) info.textContent = "共 " + pages0 + " 页";
    if (prev) prev.disabled = true;
    if (next) next.disabled = ITEMS.length <= per0;
    paintNums(1, pages0);
    syncFilterBtn();
  }

  /* 窄屏 / 宽屏切换时页码窗口会变，重算一次节点（不重建卡片） */
  var lastWin = windowSize();
  var onResize = function () {
    if (windowSize() === lastWin) return;
    lastWin = windowSize();
    var per1 = perSize(ITEMS.filter(pass));
    paintNums(state.page, Math.max(1, Math.ceil(ITEMS.filter(pass).length / per1)));
  };
  window.addEventListener("resize", onResize);

})();
"""


def page_shell(cfg: dict, title: str, body: str, depth: int = 0, gh_url: str = "",
               description: str = "", path: str = "", og_type: str = "website") -> str:
    up = "../" if depth else ""
    gh = gh_url or "https://github.com/{}/{}".format(
        cfg.get("owner") or "OWNER", cfg["repo"])
    desc = description or f"{cfg['subtitle']} —— {cfg['lede']}"
    # Open Graph：配置了 site_url 才输出，链接分享（微信/Telegram/X）出卡片
    base = (cfg.get("site_url") or "").rstrip("/")
    og = ""
    if base:
        og_rows = [
            f'<meta property="og:title" content="{esc(title)}">',
            f'<meta property="og:description" content="{esc(desc[:200])}">',
            f'<meta property="og:type" content="{esc(og_type)}">',
            f'<meta property="og:url" content="{esc(base + path)}">',
            f'<meta property="og:site_name" content="{esc(cfg["title"])}">',
        ]
        og = "\n".join(og_rows)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{og}
<link rel="stylesheet" href="{up}assets/style.css?v={BUILD_VER}">
<link rel="icon" type="image/png" href="{up}assets/favicon.png">
<script>try{{var t=localStorage.getItem("mbd-theme");if(t)document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>
</head>
<body>
<div class="wrap">
<div class="fab"><a class="tbtn" href="{up}search/" title="全站搜索" aria-label="全站搜索"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 14 14"/></svg></a><a class="tbtn" href="/" title="返回 Home（文献库首页）" aria-label="返回 Home"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8 8 3l5.5 5M4 7v6h8V7"/></svg></a><a class="tbtn" href="{gh}" rel="noopener" target="_blank" title="在 GitHub 查看仓库（数据与索引）"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></a><button type="button" class="tbtn" id="themeBtn" title="切换明暗主题" aria-label="切换明暗主题"><svg class="ic-sun" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1"/></svg><svg class="ic-moon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13.5 9.5A6 6 0 0 1 6.5 2.5a6 6 0 1 0 7 7z"/></svg></button><button type="button" class="tbtn" id="topBtn" title="回到顶部" aria-label="回到顶部"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13.5v-9M4.5 8 8 4.5 11.5 8"/></svg></button></div>
{body}
<footer class="site">
  <span>{esc(cfg['title'])} · {esc(cfg['subtitle'])}</span>
  <span><a href="https://github.com/{esc(cfg.get('owner') or 'OWNER')}/{esc(cfg['repo'])}" rel="noopener">GitHub 仓库</a> · 元数据来自 Crossref / OpenAlex · 版权归原出版方</span>
</footer>
</div>
<script src="{up}assets/papers-data.js?v={BUILD_VER}"></script>
<script src="{up}assets/papers.js?v={BUILD_VER}"></script>
</body>
</html>
"""


def flat(values, default: str = "—") -> Counter:
    return Counter([str(v) if v else default for v in values])


def chips(values: Counter, key: str, all_label: str) -> str:
    items = [f'<button data-v="*" aria-pressed="true">{esc(all_label)}</button>']
    for name, n in sorted(values.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        items.append(f'<button data-v="{esc(name)}" aria-pressed="false">{esc(name)} <span style="opacity:.55">{n}</span></button>')
    return f'<div class="chips" data-key="{key}">\n  ' + "\n  ".join(items) + "\n</div>"


def filter_row(label: str, inner: str) -> str:
    return f'<div class="fgroup"><span class="flabel">{esc(label)}</span>{inner}</div>'


def display_title(p: dict) -> str:
    """卡片/搜索结果的主展示标题：中文优先，无中文回落英文。"""
    return (p.get("title_zh") or "").strip() or (p.get("title") or "").strip()


def alt_title(p: dict) -> str:
    """悬停提示用的另一种语言标题（主展示是中文时给英文原题，反之给中文）。"""
    zh = (p.get("title_zh") or "").strip()
    en = (p.get("title") or "").strip()
    return en if display_title(p) == zh and en else (zh if zh else "")


_TYPE_FALLBACK = {
    "journal-article": "Article", "book-chapter": "Book Chapter",
    "proceedings-article": "Conference Paper", "preprint": "Preprint",
    "report": "Report", "posted-content": "Preprint",
}


def display_type(p: dict) -> str:
    """文章体裁：优先大模型判定的出版社惯例标签（article_type），
    缺失时回落 Crossref 类型映射。"""
    at = (p.get("article_type") or "").strip()
    if at:
        return at
    return _TYPE_FALLBACK.get(p.get("type", ""), "")


_NAME_PARTICLES = {"de", "van", "von", "del", "della", "di", "da", "la", "le",
                   "du", "ten", "ter", "der", "den", "el", "bin", "ibn"}


_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def _cite_author(name: str) -> str:
    """『Hiroshi Sumata』→『Sumata H』（GB/T 7714 风格，末词为姓）；
    姓氏粒子词（de/van/von…）并入姓：『Laura de Steur』→『de Steur L』；
    名后缀（Jr./III…）先剥掉：『Robert F. Spielhagen Jr.』→『Spielhagen R F』。"""
    parts = [p for p in name.strip().split() if p.lower() not in _NAME_SUFFIXES]
    if not parts:
        return name.strip()
    if len(parts) == 1:
        return parts[0]
    i = len(parts) - 2
    while i > 0 and parts[i].lower() in _NAME_PARTICLES:
        i -= 1
    family = " ".join(parts[i + 1:])
    initials = " ".join(p[0] for p in parts[:i + 1])
    return (family + " " + initials).strip()


def citation(p: dict) -> str:
    """GB/T 7714 风格引用串（展示用，中文学术习惯）。"""
    auth = p.get("authors") or []
    if auth:
        if len(auth) <= 3:
            auth_s = ", ".join(_cite_author(a) for a in auth)
        else:
            auth_s = ", ".join(_cite_author(a) for a in auth[:3]) + ", et al"
        auth_s += ". "
    else:
        auth_s = ""
    s = f"{auth_s}{p.get('title', '')}"
    if p.get("journal"):
        s += "[J]. " + p["journal"] + ", "
    else:
        s += ". "
    s += p.get("year") or ""
    vp = _volume_pages(p)
    if vp:
        s += ", " + vp
    if p.get("doi"):
        s += f". DOI: {p['doi']}."
    elif not s.endswith("."):
        s += "."
    return s


def card_html(p: dict) -> str:
    tags = p.get("tags") or []
    cap = " · ".join(tags) if tags else "—"
    ab = abstract_disp(p)
    cited = (p.get("cited_by") or 0)
    cited_html = f'<span class="ct">被引 {cited}</span>' if cited else '<span class="ct"></span>'
    jb = journal_badge(p) or "—"
    ty = display_type(p)
    ty_html = f'<span class="c-ty" title="{esc(ty)}">{esc(ty)}</span>' if ty else ''
    alt = alt_title(p)
    tip = esc(alt) if alt else esc(p.get('title'))
    return f"""  <a class="card" href="{esc(p['id'])}/" data-id="{esc(p['id'])}" title="{tip}">
    <span class="c-top"><span class="c-l"><span class="c-j" title="{esc(p.get('journal') or '')}">{esc(jb)}</span>{ty_html}</span><span class="c-y">{esc(p.get('year') or '')}</span></span>
    <span class="c-t">{esc(display_title(p))}</span>
    <span class="c-a">{esc(authors_preview(p.get('authors') or []))}</span>{f'{chr(10)}    <span class="c-abs">{esc(ab)}</span>' if ab else ''}
    <span class="c-f"><span class="tags">{esc(cap)}</span>{cited_html}</span>
  </a>"""


def build_index(cfg: dict, items: list) -> str:
    years = flat([p.get("year") for p in items if p.get("year")])
    tag_counter: Counter = Counter()
    journal_counter: Counter = Counter()
    for p in items:
        for t in p.get("tags", []):
            tag_counter[str(t)] += 1
        j = p.get("journal") or ""
        if j:
            journal_counter[j] += 1

    # 首屏静态渲染，其余交给 JS（首屏只输出 PAGE_SIZE 个卡片节点）
    first_page = items[:PAGE_SIZE]

    ghsvg = ('<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
             '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>')
    # 分页条的内联箭头：尺寸/颜色/位移全交给 .ico 系列 CSS，这里只出几何形状
    pg_ico_l = ('<svg class="ico ico-l" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
                'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<path d="M10 3 5 8l5 5"/></svg>')
    pg_ico_r = ('<svg class="ico ico-r" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
                'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<path d="M6 3l5 5-5 5"/></svg>')
    body = f"""<header class="site">
  <h1><img class="logo" src="assets/logo.png" alt="MacroBiodiv logo">{esc(cfg['title'])}</h1>
  <a class="gh-note" href="https://github.com/{esc(cfg.get('owner') or 'OWNER')}/{esc(cfg['repo'])}" rel="noopener" target="_blank" title="在 GitHub 查看数据与索引">{ghsvg}<span>文献数据与索引存储于 <b>GitHub</b>（点此查看仓库）</span></a>
  <p class="lede">{esc(cfg['lede'])}</p>
  <div class="meta-row"><span id="count">共 {len(items)} 篇</span> · {len(tag_counter)} 个标签 · {len(journal_counter)} 本期刊 · {len(years)} 个年份 · 点击卡片查看详情</div>
</header>

<div class="toolbar">
  <span class="searchbox"><svg class="sic" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 14 14"/></svg><input id="q" class="search" type="search" placeholder="搜索 标题 / 作者 / DOI / 期刊 / 关键词 / 摘要…" autocomplete="off"><button id="qBtn" type="button" title="搜索" aria-label="搜索">搜索</button></span>
  <select id="perpage" aria-label="每页数量">
    <option value="20">每页 20</option>
    <option value="30" selected>每页 30</option>
    <option value="50">每页 50</option>
  </select>
  <button id="filterBtn" class="reset" aria-expanded="true" aria-controls="filters">筛选 ▴</button>
  <button id="reset" class="reset">重置筛选</button>
</div>

<div id="filters">
{filter_row("标签", chips(tag_counter, "tag", "全部"))}
{filter_row("期刊", chips(journal_counter, "journal", "全部"))}
{filter_row("时间", '<input type="date" id="f-from" class="dateinp" title="按在线发表（online）日期筛选">\n'
  + ' <span class="flabel" style="min-width:auto">至</span>\n'
  + '<input type="date" id="f-to" class="dateinp" title="按在线发表（online）日期筛选">\n'
  + ' <span class="flabel" style="min-width:auto;margin-left:18px">排序</span>\n'
  + ' <span class="sorter" id="sortseg" role="group" aria-label="排序" style="vertical-align:middle">'
  + '<button type="button" data-sort="pub" aria-pressed="true" title="发表日期 新→旧"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.5v8M4.8 7.3 8 10.5l3.2-3.2M3 13.5h10"/></svg><span>新到旧</span></button>'
  + '<button type="button" data-sort="pub_asc" aria-pressed="false" title="发表日期 旧→新"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13.5v-8M4.8 8.7 8 5.5l3.2 3.2M3 2.5h10"/></svg><span>旧到新</span></button>'
  + '<button type="button" data-sort="cited" aria-pressed="false" title="被引次数 多→少"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3.5h10M5 8h6M7 12.5h2"/></svg><span>被引</span></button>'
  + '</span>')}
</div>

<main class="grid" id="grid">
{chr(10).join(card_html(p) for p in first_page)}
</main>
<p class="empty" id="empty"{' style="display:block"' if not items else ''}>{'文献库暂无内容 —— 运行 python scripts/admin.py 添加文献' if not items else '没有符合条件的文献'}</p>

<div class="pgbar">
  <button id="prev" type="button" title="上一页">{pg_ico_l}上一页</button>
  <span id="pgnums"></span>
  <button id="next" type="button" title="下一页">下一页{pg_ico_r}</button>
  <span class="info" id="pageinfo">共 {max(1, -(-len(items) // PAGE_SIZE))} 页</span>
</div>

<script>window.MBD_PAGE = {PAGE_SIZE};</script>"""
    return page_shell(cfg, cfg["title"], body, path="/")


def _volume_pages(p: dict) -> str:
    """卷期页紧凑串（引用样式）：12(1): 1151-1159 / 12: 3948；
    缺卷号只有期号时不输出悬空括号（期号并入页码前段省略）。"""
    vol = (p.get("volume") or "").strip()
    issue = (p.get("issue") or "").strip()
    pages = (p.get("pages") or "").strip()
    parts = vol
    if vol and issue:
        parts += f"({issue})"
    if pages:
        parts += (": " if parts else "") + pages
    return parts


def build_detail(cfg: dict, items: list, idx: int) -> str:
    p = items[idx]
    prev_p = items[idx - 1] if idx > 0 else None
    next_p = items[idx + 1] if idx < len(items) - 1 else None

    # 上一篇 / 下一篇：两列等宽卡片，主文案用标题，副文案用 期刊 · 年份。
    # 缺一篇时输出虚线占位块，否则唯一那篇会被 grid 拉成通栏。
    chev_l = ('<svg class="ico ico-l" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
              'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
              '<path d="M10 3 5 8l5 5"/></svg>')
    chev_r = ('<svg class="ico ico-r" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
              'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
              '<path d="M6 3l5 5-5 5"/></svg>')

    def pn_item(p_: dict | None, to_next: bool) -> str:
        if p_ is None:
            return '<span class="pn-empty" aria-hidden="true"></span>'
        cls = "pn-next" if to_next else "pn-prev"
        label = "下一篇" if to_next else "上一篇"
        arrow = chev_r if to_next else chev_l
        inner = (f'{label}{arrow}' if to_next else f'{arrow}{label}')
        sub = " · ".join(x for x in (p_.get("journal") or "", p_.get("year") or "") if x) or (p_.get("doi") or "—")
        return (f'<a class="{cls}" href="../{esc(p_["id"])}/">'
                f'<span class="dir">{inner}</span>'
                f'<span class="ttl">{esc(display_title(p_))}</span>'
                f'<span class="dir">{esc(sub)}</span></a>')

    pager = ['<div class="pager">',
             pn_item(prev_p, False),
             pn_item(next_p, True),
             '</div>']

    doi = (p.get("doi") or "").strip()
    doi_html = f'<a href="https://doi.org/{esc(doi)}" rel="noopener" target="_blank">{esc(doi)}</a>' if doi else "—"
    vp = _volume_pages(p)
    cited = p.get("cited_by") or 0

    # 标签 / 关键词做成可跳转：标签回首页并套用该标签筛选，关键词走全站搜索
    tags = p.get("tags") or []
    tags_html = "".join(
        f'<a class="tag" href="/?tag={quote(str(t), safe="")}">{esc(t)}</a>' for t in tags
    ) if tags else '<span style="color:var(--faint)">—</span>'
    kws = p.get("keywords") or []
    kws_html = "".join(
        f'<a class="tag" href="/search/?q={quote(str(k), safe="")}">{esc(k)}</a>' for k in kws
    ) if kws else ""

    # ── 信息区（公众号「文末信息卡」式：小标签在上、值在下，网格铺开） ──
    def ig(label: str, value: str, wide: bool = False) -> str:
        cls = ' class="ig wide"' if wide else ' class="ig"'
        return f'<div{cls}><i>{esc(label)}</i><b>{value}</b></div>'

    journal_dd = esc(p.get("journal") or "—")
    if p.get("journal_short") and p["journal_short"] != p.get("journal"):
        journal_dd += f' <span style="color:var(--faint)">/ {esc(p["journal_short"])}</span>'
    date_dd = esc(p.get("published") or p.get("year") or "—")
    if p.get("published_online") and p.get("published"):
        date_dd += ' <span style="color:var(--faint)">（online）</span>'

    info = [ig("期刊", f'<span class="hl">{journal_dd}</span>')]
    ty = display_type(p)
    if ty:
        info.append(ig("类型", esc(ty)))
    info.append(ig("发表", date_dd))
    if vp:
        info.append(ig("卷期页", esc(vp)))
    if p.get("publisher"):
        info.append(ig("出版商", esc(p["publisher"])))
    info.append(ig("被引", f"{cited}（OpenAlex）" if cited else "—"))
    info.append(ig("收录", esc(p.get("added") or "—")))
    info.append(ig("被浏览", '<span id="views">…</span>'))
    if doi:
        info.append(ig("DOI", doi_html, wide=True))
    if kws_html:
        info.append(ig("关键词", kws_html, wide=True))
    info.append(ig("标签", tags_html, wide=True))

    # ── 摘要区：中文摘要为主阅读区，英文原题摘要收合 ──
    abs_en = (p.get("abstract") or "").strip()
    abs_zh = (p.get("abstract_zh") or "").strip()
    abs_blocks = []
    if abs_zh:
        abs_blocks.append(f'<p class="abs-main">{esc(abs_zh)}</p>')
    if abs_en:
        if abs_zh:
            abs_blocks.append(f'<details class="abs-alt"><summary>原文摘要（English）</summary><p>{esc(abs_en)}</p></details>')
        else:
            abs_blocks.append(f'<p class="abs-main abs-en-only">{esc(abs_en)}</p>')
    note = (p.get("note") or "").strip()
    if note:
        abs_blocks.append(f'<p class="abs-note">{esc(note)}</p>')
    abs_sec = (f'<section class="sec"><h2 class="sec-h"><span class="no">02</span><span class="tx">摘要</span></h2>'
               + "".join(abs_blocks) + "</section>") if abs_blocks else ""

    # ── 顶部徽章行 ──
    cited_html = f'<span class="p-ct">被引 {cited}</span>' if cited else ""
    oa_html = '<span class="p-ct" style="color:var(--accent)">OA</span>' if p.get("oa") else ""
    top_row = (f'<div class="p-top"><span class="c-j" title="{esc(p.get("journal") or "")}">{esc(journal_badge(p) or "—")}</span>'
               f'<span class="c-y">{esc(p.get("year") or "")}</span>'
               + (f'<span class="p-ct">{esc(ty)}</span>' if ty else "")
               + cited_html + oa_html + '</div>')

    # ── 标题：中文优先（与卡片一致），另一语言作副行 ──
    alt = alt_title(p)
    alt_html = f'<p class="alt-title">{esc(alt)}</p>' if alt else ""
    back_ico = ('<svg class="ico ico-l" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
                'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<path d="M10 3 5 8l5 5"/></svg>')

    body = f"""<a class="back" href="../">{back_ico}返回全部</a>
{top_row}
<h1 class="p-title">{esc(display_title(p))}</h1>{alt_html}
<p class="p-auth">{esc(" · ".join(p.get("authors") or []) or "—")}</p>
<section class="sec">
  <h2 class="sec-h"><span class="no">01</span><span class="tx">信息</span></h2>
  <div class="info-grid">
    {chr(10).join('    ' + x for x in info)}
  </div>
</section>
{abs_sec}
<section class="sec">
  <h2 class="sec-h"><span class="no">03</span><span class="tx">引用</span></h2>
  <div class="cite-line"><span id="citeline">{esc(citation(p))}</span></div>
  <div class="btx"><button type="button" data-copy="citeline">复制引用</button><button type="button" data-copy="bibtex">复制 BibTeX</button><details class="full"><summary>查看 BibTeX</summary><pre id="bibtex">{esc(to_bibtex(p))}</pre></details></div>
</section>
<p class="a-end">· END ·</p>
{chr(10).join(pager)}"""
    desc = (p.get("title") or "") + " — " + (p.get("journal") or "")
    if (p.get("abstract") or "").strip():
        desc += "：" + p["abstract"].strip()[:120]
    # 学术结构化数据（Schema.org ScholarlyArticle），利于搜索引擎理解文献信息
    base = (cfg.get("site_url") or "").rstrip("/")
    ld = {
        "@context": "https://schema.org", "@type": "ScholarlyArticle",
        "headline": p.get("title") or "", "inLanguage": "en",
        "author": [{"@type": "Person", "name": a} for a in (p.get("authors") or [])],
        "datePublished": p.get("published") or p.get("year") or None,
        "isPartOf": p.get("journal") or None,
        "publisher": p.get("publisher") or None,
        "identifier": p.get("doi") or None,
        "url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
        "keywords": ", ".join((p.get("keywords") or []) + (p.get("tags") or [])) or None,
    }
    ld = {k: v for k, v in ld.items() if v}
    # "</script>" 会出现于标题/摘要时会把内嵌 script 提前截断：把 "<" 转成 Unicode 转义
    ld_html = (json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c") if base else "")
    ld_block = ('<script type="application/ld+json">' + ld_html + "</script>") if ld_html else ""
    body = body + ld_block
    return page_shell(cfg, f"{p.get('title')} · {cfg['title']}", body, depth=1,
                      gh_url=f"https://github.com/{cfg.get('owner') or 'OWNER'}/{cfg['repo']}",
                      description=desc, path=f"/{p['id']}/", og_type="article")


def build_search_page(cfg: dict, items: list) -> str:
    """全站搜索独立页：版式对齐主站 zbhgis.com 的 /search。
    结果行由 papers.js 在客户端渲染（数据来自 papers-data.js）；
    文献没有缩略图，结果行 = 标题 + 作者/期刊/年份/DOI。"""
    body = f"""<header class="site">
  <p class="kicker">{esc(cfg['title'].upper())} · SEARCH</p>
  <h1 class="spage-title">全站搜索</h1>
  <p class="lede">检索全部 {len(items)} 篇文献的 标题 / 作者 / DOI / 期刊 / 关键词 / 摘要全文 / 标签；多个词用空格分隔（需同时命中）。</p>
</header>

<input id="spage-q" class="spage-q" type="search" placeholder="输入关键词搜索全站内容…" autocomplete="off" autofocus>
<div class="spage-count" id="spage-count"></div>
<div class="spage-list" id="spage-list" data-up="../"></div>"""
    return page_shell(cfg, f"全站搜索 · {cfg['title']}", body, depth=1, path="/search/")


def main() -> int:
    ap = argparse.ArgumentParser(description="MacroBiodiv 静态站生成")
    ap.parse_args()

    cfg = load_cfg()
    if not PAPERS_JSON.exists():
        # papers.json 缺失视同空库，照常出空态站（管理界面首启前也能构建预览）
        print("! meta/papers.json 不存在 —— 生成空态站点")
        items = []
    else:
        items = sort_items(json.loads(PAPERS_JSON.read_text(encoding="utf-8")).get("items", []))
    if not items:
        # 空库也照常出站（删光文献后站点显示空态，而不是构建失败）
        print("! 文献索引为空 —— 生成空态站点")

    # 过期的详情页目录 → 改名移入 site_trash/（绝不原地删除）。
    # GeoSciPlot 的经验：shutil.rmtree 会触发沙箱的批量删除保护；纯改名则无此问题。
    # site_trash/ 已 gitignore，偶尔手动清空即可。
    if SITE.exists():
        trash = ROOT / "site_trash"
        for d in SITE.iterdir():
            if d.is_dir() and d.name not in ("assets", "search") and (d / "index.html").exists():
                trash.mkdir(parents=True, exist_ok=True)
                dest = trash / (d.name + "-" + str(int(time.time())))
                print(f"· 过期详情页 {d.name} → site_trash/（不删除）")
                try:
                    d.rename(dest)
                except OSError:
                    pass

    (SITE / "assets").mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "assets_src").glob("*"):
        if f.is_file():
            shutil.copyfile(f, SITE / "assets" / f.name)

    (SITE / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    inline = {
        "tracker": cfg.get("tracker", ""),
        "api": cfg.get("api", ""),
    }
    (SITE / "assets" / "papers.js").write_text(
        "window.MBD = " + json.dumps(inline, ensure_ascii=False) + ";\n" + JS, encoding="utf-8")

    # 全量元数据：首页网格与全站搜索页共用这一份数据（详情页没有内嵌数据，
    # 靠它实现跨页搜索）。se = 检索 haystack，构建期算好免去 JS 重复拼接。
    # t = 主展示标题（中文优先），tz/te = 中/英标题（悬停提示与搜索行副文案用）。
    data = [{
        "id": p["id"],
        "t": display_title(p),
        "tz": (p.get("title_zh") or "").strip(),
        "te": (p.get("title") or "").strip(),
        "a": authors_preview(p.get("authors") or []),
        "j": p.get("journal") or "",
        "jb": journal_badge(p),
        "pt": display_type(p),
        "y": p.get("year") or "",
        "pd": p.get("published") or "",
        "ad": p.get("added") or "",
        "tg": p.get("tags") or [],
        "sub": " · ".join(p.get("tags") or []),
        "ct": p.get("cited_by") or 0,
        "doi": p.get("doi") or "",
        "ab": abstract_disp(p),
        "se": haystack(p),
    } for p in items]
    (SITE / "assets" / "papers-data.js").write_text(
        "window.MBD_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8")

    # robots.txt + sitemap.xml：配置了 site_url 才生成（部署完整性 / 搜索引擎收录）
    base = (cfg.get("site_url") or "").rstrip("/")
    if base:
        robots_lines = ["User-agent: *", "Allow: /", "Sitemap: " + base + "/sitemap.xml"]
        (SITE / "robots.txt").write_text("\n".join(robots_lines) + "\n", encoding="utf-8")
        today = time.strftime("%Y-%m-%d")
        urls = ["/", "/search/"] + ["/" + p["id"] + "/" for p in items]
        sm = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for u in urls:
            sm.append(f"<url><loc>{base}{u}</loc><lastmod>{today}</lastmod></url>")
        sm.append("</urlset>")
        (SITE / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")
        # Atom 订阅源：最新 20 篇（按收录日期），文献库的订阅闭环
        # XML 不允许的控制字符（\x00-\x08 等）会导致解析失败，先剥掉
        xml_bad = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

        def xml_text(s: str) -> str:
            return esc(xml_bad.sub("", s or ""))

        newest = sorted(items, key=lambda p: p.get("added") or "", reverse=True)[:20]
        atom = ['<?xml version="1.0" encoding="utf-8"?>',
                '<feed xmlns="http://www.w3.org/2005/Atom">',
                f'<title>{xml_text(cfg["title"])} · {xml_text(cfg["subtitle"])}</title>',
                f'<link href="{base}/"/><id>{base}/</id>',
                f'<updated>{time.strftime("%Y-%m-%dT00:00:00Z")}</updated>']
        for p in newest:
            summ = (p.get("abstract_zh") or p.get("abstract") or "")[:300]
            atom += ['<entry>',
                     f'<title>{xml_text(display_title(p))}</title>',
                     f'<link href="{base}/{p["id"]}/"/>',
                     f'<id>urn:doi:{xml_text(p.get("doi", p["id"]))}</id>',
                     f'<updated>{p.get("added") or time.strftime("%Y-%m-%d")}T00:00:00Z</updated>',
                     f'<summary>{xml_text(summ)}</summary>']
            for a in (p.get("authors") or [])[:5]:
                atom.append(f'<author><name>{xml_text(a)}</name></author>')
            atom.append('</entry>')
        atom.append('</feed>')
        (SITE / "atom.xml").write_text("\n".join(atom) + "\n", encoding="utf-8")
        print(f"· robots.txt + sitemap.xml（{len(urls)} 个 URL）+ atom.xml（{len(newest)} 条）")

    (SITE / "index.html").write_text(build_index(cfg, items), encoding="utf-8")
    search_dir = SITE / "search"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "index.html").write_text(build_search_page(cfg, items), encoding="utf-8")
    for i, p in enumerate(items):
        d = SITE / p["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(build_detail(cfg, items, i), encoding="utf-8")

    pages = max(1, -(-len(items) // PAGE_SIZE))
    index_kb = len((SITE / "index.html").read_bytes()) / 1024
    journals = len({p.get("journal") for p in items if p.get("journal")})
    print(f"· 生成 {len(items)} 篇文献 + {pages} 页（每页 {PAGE_SIZE}）+ {journals} 本期刊")
    print(f"· index.html {index_kb:.0f}KB（首屏静态输出 {min(len(items), PAGE_SIZE)} 个卡片）")
    print(f"· 输出目录：{SITE.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
