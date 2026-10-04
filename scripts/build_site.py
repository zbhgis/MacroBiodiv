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
    site/assets/weekly-data.js     每周速递各期全文（仅 /search/ 页注入，供周报内容检索）
    site/assets/image-lightbox.js  零依赖图片灯箱（详情页与周报文章页注入，源 assets_src/）
    site/assets/article-toc.js     文章目录（桌面侧栏高亮 + 移动端抽屉，同上两种页面注入）
    site/assets/stats-data.js      全站统计数据（英文原文与分面字段，仅 /statistics/ 页注入）
    site/assets/stats.js           全站统计客户端聚合（筛选 → 图表重算，仅 /statistics/ 页注入）

设计要点（样式与交互框架照搬 GeoSciPlot，把图片瀑布流换成文献信息卡片）：
  · 顶部菜单栏（.mnav）：sticky 毛玻璃，移植主站 zbhgis.com 的 header；全部页面由
    page_shell 统一注入；左站点名 / 右导航组（每周速递 · 全站统计 · 更多▾ 纯 CSS 下拉）
  · 详情页为公众号推文式排版（对齐「浩瀚地学」文献精选推文实测规格）：窄栏 677px、
    居中标题块 + accent 通栏底线、左竖线节标题（1.信息/2.摘要/3.图表/4.引用）、
    「字段名：值」字段行、摘要 15px/1.8 左对齐、图表图片淡蓝光晕
  · 首屏卡片由 Python 直接输出静态 HTML（对爬虫/AI 引擎友好），翻页与筛选改由 JS 渲染
  · 筛选维度：标签 / 期刊 / 发表日期区间（按论文发表时间，不是收录时间）
  · 排序：发表 新→旧（默认）/ 旧→新 / 随机（每次点「随机」重新洗牌；
    cited_by 仅随抓取入库存档，管理端与站点均不展示）
  · 文献 id = DOI（小写）sha1 前 10 位，详情页目录与 id 一致
  · 封面图（可选字段 cover）：源文件 assets_src/covers/{id}.{ext}（admin.py 上传落盘），
    构建时整体拷到 site/assets/covers/；卡片出 16:9 通栏顶图，详情页作者行下出大图，
    并写入 og:image 与 ScholarlyArticle JSON-LD 的 image（链接分享出封面卡片）
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
    "lede": "收集宏观生态与生物多样性领域公开发表的文献基本信息。",
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
:root{--bg:#0d1117;--text:#e6edf3;--dim:#8b949e;--faint:#6e7681;--line:#1c2129;--line2:#30363d;--accent:#58a6ff;--card:#161b22;--header-bg:#0d1117e6;--accent-soft:#58a6ff1a;color-scheme:dark}
:root[data-theme=dark]{color-scheme:dark}
:root[data-theme=light]{--bg:#fff;--text:#1f2328;--dim:#59636e;--faint:#818b98;--line:#e8ebef;--line2:#d0d7de;--accent:#0969da;--card:#f6f8fa;--header-bg:#ffffffe6;--accent-soft:#0969da1a;color-scheme:light}
/* ── 字号体系（4 档变量 + h2；全部 font-size 只允许用这些变量，禁止散落 px；
   菜单栏 .mnav 与大标题 clamp 为固定框架不参与调节。放大档 data-fs=lg 只覆盖变量，
   布局零改动。偏好存 localStorage mbd-fs，head 内联脚本渲染前置位防闪烁）── */
:root{--fs-xs:13px;--fs-sm:14px;--fs-md:15px;--fs-base:17px;--fs-h2:22px}
html[data-fs=lg]{--fs-xs:14.5px;--fs-sm:16px;--fs-md:17px;--fs-base:19px;--fs-h2:24px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:var(--fs-base)/1.7 ui-sans-serif,system-ui,"PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
/* ── 顶部菜单栏（移植主站 zbhgis.com 的 header，同源数值）：
   sticky 顶栏 · 发丝底边 · 90% 不透明底 + blur(12px) 毛玻璃；
   链接 hover 出下划线（scaleX 0→1），当前页 accent 常亮；
   右端一枚 30px 方形 icon 按钮（v3-nav-icon 同语言，当前为占位） ── */
.mnav{position:sticky;top:0;z-index:50;border-bottom:1px solid var(--line);background:var(--header-bg);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
.mnav-in{max-width:1080px;margin:0 auto;padding:0 24px;height:56px;display:flex;align-items:center;justify-content:space-between;gap:24px}
.mnav-brand{flex:none;display:inline-flex;align-items:center;gap:10px;font-size:17px;font-weight:600;letter-spacing:-.01em;color:var(--text);transition:color .16s}
.mnav-brand img{width:26px;height:26px;border-radius:50%;object-fit:cover;box-shadow:0 0 0 1px var(--line)}
.mnav-brand:hover{color:var(--accent)}
.mnav-links{display:flex;align-items:center;list-style:none;margin:0;padding:0}
.mnav-link{display:inline-flex;align-items:center;gap:5px;padding:6px 10px;font-size:15px;line-height:1.4;color:var(--dim);position:relative;transition:color .16s}
.mnav-link:after{content:"";position:absolute;bottom:1px;left:10px;right:10px;height:1px;background:currentColor;opacity:.45;transform:scaleX(0);transform-origin:0;transition:transform .2s,opacity .2s}
.mnav-link:hover{color:var(--text)}
.mnav-link:hover:after{transform:scaleX(1)}
.mnav-link[data-active=true]{color:var(--accent)}
.mnav-link[data-active=true]:after{opacity:.9;transform:scaleX(1)}
/* 菜单项图标：主站 v3-nav-ico 同源（14px，currentColor，随 .mnav-link 的 5px gap 定位） */
.mnav-ico{flex:none;width:14px;height:14px}
/* 「更多」下拉：主站 v3-more / v3-nav-menu 同源 —— hover 或触发钮 focus-visible 展开，
   display 直切无动画；caret 11px 悬停旋转；面板 --bg 实底 + 描边 + 主站同款投影 */
.mnav-more{position:relative}
.mnav-more-trigger{cursor:pointer;font-family:inherit;background:none;border:none}
/* 只继承字族、字号仍取 .mnav-link 的固定 15px —— 用 font:inherit 会继承 body 的
   --fs-base 字号，字号体系一上按钮就跟着变大（踩坑记录） */
.mnav-more-caret{flex:none;width:11px;height:11px;transition:transform .16s}
.mnav-more:hover .mnav-more-caret,.mnav-more:has(.mnav-more-trigger:focus-visible) .mnav-more-caret{transform:rotate(180deg)}
.mnav-dd{display:none;position:absolute;top:100%;right:0;min-width:148px;margin:0;padding:5px;list-style:none;background:var(--bg);border:1px solid var(--line2);border-radius:6px;box-shadow:0 10px 28px rgba(0,0,0,.16);z-index:60}
.mnav-more:hover .mnav-dd,.mnav-more:has(.mnav-more-trigger:focus-visible) .mnav-dd{display:block}
.mnav-dd a{display:flex;align-items:center;gap:8px;padding:8px 11px;border-radius:4px;font-size:14px;color:var(--dim);white-space:nowrap;transition:color .16s,background-color .16s}
.mnav-dd a .mnav-ico{width:15px;height:15px;color:var(--faint)}
.mnav-dd a:hover{color:var(--text);background:var(--accent-soft)}
.mnav-dd-sep{height:1px;background:var(--line);margin:5px 4px}
/* 窄屏汉堡：主站 sm 断点的 details/summary 原生方案（无 JS），
   summary 即主站 .v3-nav-icon（30px 方形 hover accent + accent-soft 底） */
.mnav-m{display:none;position:relative}
.mnav-icon{width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;border-radius:4px;color:var(--dim);cursor:pointer;transition:color .16s,background-color .16s;list-style:none}
.mnav-icon::-webkit-details-marker{display:none}
.mnav-icon:hover,.mnav-m[open] .mnav-icon{color:var(--accent);background:var(--accent-soft)}
.mnav-icon svg{width:18px;height:18px}
.mnav-m[open] .mnav-dd{display:block}
@media (max-width:640px){
  .mnav-in{padding:0 16px}
  .mnav-links{display:none}
  .mnav-m{display:block}
}
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
.kicker{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:0}
/* 仅首页 header 的 logo+标题行需要 flex —— 用专用类而非元素/层叠选择器：
   search/statistics 的 h1.spage-title 也在 header.site 内，全局 h1 或
   header.site h1 都会以特异度反超 .spage-title 的设计字号 */
.home-title{display:flex;align-items:center;gap:18px;flex-wrap:wrap;font-size:clamp(44px,6.5vw,68px);line-height:1.08;letter-spacing:-.03em;margin:24px 0 0;font-weight:700}
h1 img.logo{height:clamp(44px,5.4vw,58px);width:auto;flex:none;border-radius:12px}
.lede{font-size:var(--fs-base);color:var(--dim);max-width:52ch;margin:20px 0 0}
.gh-note{display:inline-flex;align-items:center;gap:9px;margin:18px 0 0;padding:9px 16px;border:1px solid var(--accent);border-left-width:3px;border-radius:6px;background:var(--card);font-size:var(--fs-sm);color:var(--text)}
.gh-note svg{width:16px;height:16px;flex:none;color:var(--accent)}
.meta-row{margin:28px 0 0;padding:14px 0;border-top:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--dim)}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:22px 0 6px}
.search{flex:1 1 260px;max-width:380px;padding:8px 12px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm)}
/* 搜索框 + 搜索按钮（连体胶囊）：外壳 999px，内部按钮只圆右半，focus-within 点亮 accent 边 */
.searchbox{display:inline-flex;align-items:center;gap:0;flex:1 1 300px;max-width:430px;border:1px solid var(--line2);border-radius:999px;background:transparent;transition:border-color .16s}
.searchbox:focus-within{border-color:var(--accent)}
.searchbox .sic{width:14px;height:14px;flex:none;margin-left:13px;color:var(--faint)}
.searchbox .search{flex:1;min-width:0;border:none;background:transparent;padding:8px 10px;max-width:none}
.searchbox .search:focus{outline:none}
.searchbox button{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);padding:0 15px;height:36px;border:none;border-left:1px solid var(--line2);border-radius:0 999px 999px 0;background:transparent;color:var(--dim);cursor:pointer;transition:color .16s,background-color .16s}
.searchbox button:hover{color:var(--accent);background:var(--card)}
.search:focus{outline:none;border-color:var(--accent)}
.search::placeholder{color:var(--faint)}
select{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);padding:7px 12px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--dim)}
.reset{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);padding:7px 14px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--faint);cursor:pointer}
.reset:hover{color:var(--accent);border-color:var(--accent)}
#filters[hidden]{display:none}
.sorter{display:inline-flex;align-items:center;gap:2px;padding:3px;border:1px solid var(--line2);border-radius:999px;background:var(--card)}
.sorter button{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);padding:6px 13px;border:none;border-radius:999px;background:transparent;color:var(--dim);cursor:pointer;transition:color .15s,background-color .15s}
.sorter button:hover{color:var(--text)}
.sorter button[aria-pressed=true]{background:var(--accent);color:var(--bg)}
.sorter button svg{width:13px;height:13px;flex:none}
.dateinp{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);padding:6px 12px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--dim);color-scheme:dark light}
.fgroup{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 0;padding-bottom:8px;border-bottom:1px solid var(--line)}
.flabel{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);min-width:44px;letter-spacing:.06em}
.chips{display:flex;flex-wrap:wrap;gap:8px}
/* 筛选 chips（多选模型，GSP 同源）：默认全选=可读描边胶囊 + 计数徽标；点击剔除=
   置灰删除线；hover=accent-soft 预选；行尾 全选/反选 为虚线胶囊。
   accent 实底只给真单选组（排序胶囊）——默认全选的 chips 用实底就是满屏蓝 */
.chips button{display:inline-flex;align-items:center;gap:7px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);padding:5px 13px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--text);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s,opacity .16s}
.chips button:hover{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
.chips button .n{font-style:normal;min-width:22px;padding:0 7px;border-radius:999px;background:var(--line);color:var(--faint);font-size:var(--fs-xs);line-height:19px;text-align:center;font-variant-numeric:tabular-nums}
.chips button[aria-pressed=false]{color:var(--faint);border-color:var(--line);text-decoration:line-through;opacity:.75}
.chips button[aria-pressed=false] .n{opacity:.55;text-decoration:none}
.chips .chip-act{border-style:dashed;color:var(--faint);font-size:var(--fs-xs);padding:5px 11px}
.chips .chip-act:hover{color:var(--accent);border-color:var(--accent);background:transparent}
/* ── 首页卡片瀑布流：移植 GeoSciPlot 的 multi-columns 方案（同源）。
   等宽 grid 同一行会被"最高的那张"定高、矮卡下方必然留空 —— columns
   每列独立堆叠，封面图自然比例不同，才能形成错落 ── */
.grid{columns:4;column-gap:18px;margin-top:26px}
@media (max-width:1100px){.grid{columns:3}}
@media (max-width:760px){.grid{columns:2;column-gap:12px}}
.card{break-inside:avoid;display:block;margin:0 0 18px;min-width:0;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:var(--card);transition:border-color .16s}
.card:hover{border-color:var(--accent)}
.c-ty{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--dim);border:1px solid var(--line2);border-radius:999px;padding:1px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:12em}
.c-j{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 45%,transparent);border-radius:999px;padding:1px 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
/* .c-y 详情页 .p-top 仍在复用 */
.c-y{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);flex:none}
.c-t{font-size:var(--fs-sm);font-weight:600;line-height:1.5;color:var(--text);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
/* 封面通栏顶图：自然宽高比不裁切（瀑布流错落靠比例）；底色作加载占位防白闪 */
.c-cov{display:block;width:100%;height:auto;background:var(--line)}
/* 无封面占位块：期刊缩写居中（GeoSciPlot .ph 同语言） */
.c-ph{display:flex;align-items:center;justify-content:center;min-height:140px;padding:18px;background:var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.08em;color:var(--faint);text-align:center}
.c-cap{display:block;padding:10px 12px 12px}
.c-meta{display:flex;flex-wrap:wrap;gap:5px;min-width:0;margin:0 0 7px}
.pgbar{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:6px;margin:44px 0 0;padding-top:24px;border-top:1px solid var(--line)}
/* 步进按钮：只有文字 + 一枚内联箭头，hover 才点亮（与主站 .v3-pager-step 同语言） */
.pgbar button{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);line-height:1.35;padding:5px 13px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s}
.pgbar button:hover:not(:disabled){color:var(--accent);border-color:var(--accent);background:var(--card)}
.pgbar button:disabled{opacity:.3;cursor:not-allowed}
/* 箭头 hover 时朝翻页方向平移 2px；禁用态不动 */
#prev:hover:not(:disabled) .ico-l{transform:translateX(-2px)}
#next:hover:not(:disabled) .ico-r{transform:translateX(2px)}
/* 页码：等宽 + 定宽定高，选中态用强调色描边配极淡底，不填色（保持克制的工程感） */
.pgnum{display:inline-flex;align-items:center;justify-content:center;min-width:30px;height:30px;padding:0 9px;border:1px solid var(--line2);border-radius:999px;background:transparent;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);font-variant-numeric:tabular-nums;color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s}
.pgnum:hover{color:var(--text);border-color:var(--accent)}
.pgnum[data-on=true]{color:var(--accent);border-color:var(--accent);background:var(--card)}
/* 当前页附近被"窗口"截断时用省略号占位，不可点 */
.pggap{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:30px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);color:var(--faint);user-select:none}
/* 页码区与「共 N 篇」之间用一条发丝竖线隔开 */
.pgbar .info{margin-left:8px;padding-left:14px;border-left:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);color:var(--faint);white-space:nowrap}
.empty{padding:52px 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);color:var(--faint);display:none;text-align:center}
footer.site{margin-top:56px;padding:24px 0 64px;border-top:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between}
/* ── 详情页（公众号推文式，移植「浩瀚地学」文献精选排版语言：
   窄栏 677px 居中 · 标题块居中 + accent 通栏底线 · 节标题 accent 左竖线 ·
   「字段名：值」同行字段行 · 摘要 15px/1.8 左对齐 · 图片撑栏淡蓝光晕；
   色值一律取本站 token，明暗主题各自适配） ── */
.pbody{max-width:677px;margin:0 auto}
.p-top{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;margin:26px 0 0}
.p-top .c-j{font-size:var(--fs-xs)}
.p-top .p-type,.p-top .p-ct{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint)}
.p-head{padding-bottom:16px;border-bottom:1px solid var(--rs,var(--accent));text-align:center}
.p-title{font-size:clamp(22px,3.2vw,30px);line-height:1.5;letter-spacing:-.01em;margin:14px 0 0;font-weight:700}
.alt-title{font-size:var(--fs-sm);color:var(--dim);margin:8px 0 0;line-height:1.7}
.p-auth{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--dim);margin:12px 0 0;line-height:1.9;word-break:break-word}
/* 作者行折叠：papers.js 检测到超过两行才加 .auth-clamp 并亮出切换按钮；
   无 JS / 两行以内 = 完整展示（渐进增强，内容不因样式丢失） */
.p-auth.auth-clamp{max-height:3.8em;overflow:hidden;transition:max-height .25s ease}
.p-auth.auth-clamp.open{max-height:60em}
.auth-toggle{display:none;margin:4px 0 0;padding:0;border:none;background:none;cursor:pointer;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);transition:color .16s}
.auth-toggle:hover{color:var(--accent)}
.auth-toggle.on{display:inline-block}
/* 图表：图片撑满栏宽、无圆角、淡蓝光晕（公众号 rgb(133,161,201) 同源） */
.p-cover{margin:6px 0 0;background:var(--bg);box-shadow:0 0 5px rgba(133,161,201,.5)}
.p-cover img{display:block;width:100%;height:auto}
/* 节标题：accent 5px 左竖线 + 18px 加粗（公众号同源：无底线、无背景） */
.sec{margin-top:32px}
.sec-h{display:flex;align-items:center;margin:0 0 14px;padding:2px 0 2px 11px;border-left:5px solid var(--rs,var(--accent));scroll-margin-top:80px}
.sec-h .tx{font-size:var(--fs-h2);font-weight:700;letter-spacing:0;color:var(--rs,var(--accent))}
/* ── 渲染颜色切换（文献详情 ↔ 周报文章，两页共用同一份用户偏好）：
   plain（周报默认）＝系统色标题、无装饰；accent（文献详情默认）/green/purple/orange
   ＝强调色渲染：--rs 按 data-rstyle 定义，节标题竖线与文字、详情页标题底线、备注线、
   周报彩色标题全部取 --rs；亮色主题下换深一档色值保证对比度 ── */
html[data-rstyle=accent]{--rs:var(--accent)}
html[data-rstyle=green]{--rs:#3fb950}
html[data-rstyle=purple]{--rs:#a371f7}
html[data-rstyle=orange]{--rs:#f0883e}
html[data-theme=light][data-rstyle=green]{--rs:#1a7f37}
html[data-theme=light][data-rstyle=purple]{--rs:#8250df}
html[data-theme=light][data-rstyle=orange]{--rs:#bc4c00}
html[data-rstyle=plain] .sec-h{border-left:none;padding-left:0}
html[data-rstyle=plain] .sec-h .tx{color:var(--text)}
html[data-rstyle]:not([data-rstyle=plain]) .wk-content h2.md-h1,
html[data-rstyle]:not([data-rstyle=plain]) .wk-content h3.md-h2{padding:2px 0 2px 11px;border-bottom:none;border-left:5px solid var(--rs,var(--accent));color:var(--rs,var(--accent))}
/* 强调色渲染：正文作用域内所有用 accent 的文字相关元素（内容链接 / DOI 链接 /
   悬停反馈 / 期刊徽章 / 关键词胶囊 / 周报分类 chip / strong）一并跟随渲染颜色
   —— 直接重映射作用域内的 --accent，规则无需逐条改；plain 保持站点原色 */
html[data-rstyle]:not([data-rstyle=plain]) .pbody,
html[data-rstyle]:not([data-rstyle=plain]) .wk-main,
html[data-rstyle]:not([data-rstyle=plain]) .back{--accent:var(--rs)}
html[data-rstyle]:not([data-rstyle=plain]) .wk-content strong{color:var(--rs)}
.rstyle{display:inline-flex;align-items:center;gap:6px;margin-left:auto}
.rst-btn{width:26px;height:26px;display:inline-flex;align-items:center;justify-content:center;padding:0;border:none;background:none;border-radius:50%;cursor:pointer}
.rst-btn:hover{background:none}
.rst-dot{position:relative;display:block;width:15px;height:15px;border-radius:50%;transition:opacity .16s,box-shadow .16s}
.rst-dot-plain{border:1.5px solid var(--dim)}
.rst-dot-plain:before{content:"";position:absolute;left:1.5px;right:1.5px;top:50%;height:1.5px;margin-top:-1px;background:var(--dim);transform:rotate(-45deg)}
.rst-dot-accent{background:#58a6ff;border:1.5px solid #58a6ff}
html[data-theme=light] .rst-dot-accent{background:#0969da;border-color:#0969da}
.rst-dot-green{background:#3fb950;border:1.5px solid #3fb950}
.rst-dot-purple{background:#a371f7;border:1.5px solid #a371f7}
.rst-dot-orange{background:#f0883e;border:1.5px solid #f0883e}
html[data-theme=light] .rst-dot-green{background:#1a7f37;border-color:#1a7f37}
html[data-theme=light] .rst-dot-purple{background:#8250df;border-color:#8250df}
html[data-theme=light] .rst-dot-orange{background:#bc4c00;border-color:#bc4c00}
.rst-btn[aria-pressed=true] .rst-dot{box-shadow:0 0 0 2px var(--bg),0 0 0 3.5px var(--rs,var(--accent))}
.rst-btn[aria-pressed=true] .rst-dot-plain{box-shadow:0 0 0 2px var(--bg),0 0 0 3.5px var(--dim)}
.rst-btn[aria-pressed=false] .rst-dot{opacity:.4}
.p-top{position:relative}
.p-top .rstyle{position:absolute;right:0;top:50%;transform:translateY(-50%);margin-left:0}
@media (max-width:640px){.p-top .rstyle{position:static;transform:none;margin-left:auto}}
/* 信息节：字段行（字段名：值 同行，15px / 1.8 行高 / 8px 上下呼吸，与推文一致） */
.frows{margin:0}
.frow{display:flex;flex-wrap:wrap;margin:0;padding:8px 0;font-size:var(--fs-md);line-height:1.8;color:var(--text)}
.frow .fk{flex:none;color:var(--dim);font-style:normal}
.frow .fk::after{content:"："}
.frow .fv{flex:1;min-width:0;word-break:break-word}
.frow .fv a{border-bottom:1px solid var(--line2);transition:color .16s,border-color .16s}
.frow .fv a:hover{color:var(--accent);border-color:var(--accent)}
.frow .fv a.tag{display:inline-block;margin:0 8px 8px 0;padding:3px 12px;border:1px solid var(--line2);border-radius:999px;font-size:var(--fs-sm);color:var(--dim);transition:color .16s,border-color .16s,background-color .16s}
.frow .fv a.tag:hover{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
/* 摘要：15px / 1.8 行高左对齐（对齐推文正文）；英文原题收合、备注强调块保留 */
.abs-main{font-size:var(--fs-md);line-height:1.8;margin:0;color:var(--text);text-align:left}
.abs-main.abs-en-only{color:var(--dim)}
.abs-alt{margin-top:16px}
.abs-alt summary{cursor:pointer;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);transition:color .16s}
.abs-alt summary:hover{color:var(--accent)}
.abs-alt p{margin:10px 0 0;font-size:var(--fs-xs);line-height:1.8;color:var(--dim);text-align:justify}
.abs-note{margin:16px 0 0;padding:10px 13px;border-left:2px solid var(--rs,var(--accent));background:var(--card);border-radius:0 6px 6px 0;font-size:var(--fs-xs);line-height:1.8;color:var(--dim)}
/* 引用条（GB/T 7714）+ 复制按钮 */
.cite-line{padding:13px 15px;border:1px solid var(--line);border-radius:6px;background:var(--card);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);line-height:1.85;color:var(--dim);word-break:break-word;margin:0 0 12px}
.a-end{margin:46px 0 0;text-align:center;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.4em;color:var(--faint);user-select:none}
/* BibTeX：复制按钮 + 折叠查看 */
.btx{margin-top:28px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.btx button{display:inline-flex;align-items:center;gap:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);padding:7px 13px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s}
.btx button:hover{color:var(--accent);border-color:var(--accent);background:var(--card)}
.btx summary{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);cursor:pointer}
.btx summary:hover{color:var(--accent)}
.btx pre{margin:12px 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:6px;background:var(--card);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);line-height:1.65;color:var(--dim);overflow:auto;max-height:320px;white-space:pre-wrap;word-break:break-all}
.btx .full{flex-basis:100%}
/* ── 详情页：上一篇 / 下一篇（与主站 .v3-prevnext 同语言）
   两列等宽卡片；缺一篇时用虚线占位，避免唯一那篇被拉成通栏 ── */
.pager{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:46px 0 0;padding-top:24px;border-top:1px solid var(--line)}
.pager a{display:flex;flex-direction:column;gap:7px;min-width:0;padding:12px 14px;border:1px solid var(--line2);border-radius:6px;transition:color .16s,border-color .16s,background-color .16s}
.pager a:hover{border-color:var(--accent);background:var(--card)}
.pager .dir{display:inline-flex;align-items:center;gap:5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.1em;color:var(--faint);transition:color .16s}
.pager a:hover .dir{color:var(--accent)}
.pager a:hover .ico-l{transform:translateX(-2px)}
.pager a:hover .ico-r{transform:translateX(2px)}
.pager .ttl{font-size:var(--fs-sm);line-height:1.5;color:var(--text);transition:color .16s;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pager a:hover .ttl{color:var(--accent)}
.pager .pn-next{text-align:right}
.pager .pn-next .dir{justify-content:flex-end}
.pager .pn-empty{min-height:68px;border:1px dashed var(--line);border-radius:6px}
/* ── 返回全部：发丝边框小按钮，箭头 hover 左移 ── */
.back{display:inline-flex;align-items:center;gap:7px;margin:36px 0 20px;padding:6px 14px 6px 11px;border:1px solid var(--line2);border-radius:999px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm);line-height:1.35;color:var(--dim);transition:color .16s,border-color .16s,background-color .16s}
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
.spage-q{display:block;width:100%;max-width:520px;margin:24px 0 0;padding:10px 16px;border:1px solid var(--line2);border-radius:999px;background:transparent;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-md)}
.spage-q:focus{outline:none;border-color:var(--accent)}
.spage-count{margin:14px 0 2px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint)}
.spage-list{margin-top:6px}
.sres{display:flex;align-items:center;gap:14px;padding:11px 6px;border-top:1px solid var(--line)}
.sres:hover{background:var(--card)}
.sres-body{min-width:0;flex:1}
.sres-id{display:block;font-size:var(--fs-sm);line-height:1.5;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sres:hover .sres-id{color:var(--accent)}
.sres-meta{display:block;margin-top:3px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sres-meta .sres-sep{font-style:normal;color:var(--faint);margin:0 6px}
/* 周报结果：分组标题 + 正文命中片段（可多行，弱化色） */
.spage-grouphd{margin:22px 0 2px;padding:7px 6px;border-top:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.08em;color:var(--faint)}
.sres-snip{margin-top:5px;font-size:var(--fs-xs);line-height:1.7;color:var(--dim);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
mark{background:color-mix(in srgb,var(--accent) 24%,transparent);color:inherit;border-radius:2px;padding:0 1px}
.spage-hint,.spage-empty{padding:26px 6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint)}
@media (max-width:640px){
  .sres{gap:10px}
}
/* ── 每周速递（/weekly/）：移植自 mystation（zbhgis.com 主站）globals.css 的
   v3-* / blog-content 同款数值，token 已换名（--text-primary→--text、
   --text-secondary/muted→--dim、--text-faint→--faint、--hairline→--line、
   --hairline-strong/--border→--line2、accent-soft-fade→transparent）。
   静态站裁剪：ViewToggle/RSS/分组分页/侧栏手风琴/TOC scrollspy 均不做 ── */
.wk-col{max-width:760px;margin:0 auto;padding:56px 24px 96px}
.wk-head{display:flex;align-items:flex-end;gap:16px;margin:0 0 40px}
.wk-kicker{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.18em;text-transform:uppercase;color:var(--dim)}
.wk-deco{margin:16px 0 0;font-size:clamp(28px,4vw,40px);font-weight:600;letter-spacing:-.03em;line-height:1.15;color:var(--text)}
.wk-lede{margin:18px 0 0;font-size:var(--fs-base);line-height:1.75;color:var(--dim);max-width:46ch}
/* QuickNav 月份跳转 chip（v3-btn 同源） */
.wk-quicknav{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 36px}
.wk-btn{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);line-height:1.3;border:1px solid var(--line2);border-radius:999px;color:var(--dim);background:transparent;transition:color .16s,border-color .16s,background-color .16s}
.wk-btn:hover{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
.wk-btn .n{color:var(--faint)}
/* 分组 section（v3-label 同源：mono 大写 + 右侧计数） */
.wk-groups{display:flex;flex-direction:column;gap:40px}
.wk-label{display:flex;align-items:baseline;justify-content:space-between;gap:16px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.16em;text-transform:uppercase;color:var(--dim);padding-bottom:10px;border-bottom:1px solid var(--line2);margin:0 0 18px}
.wk-label-right{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.04em;text-transform:none;color:var(--dim)}
/* 条目行（v3-line 同源：标题 + 摘要两行 + 右侧标签列） */
.wk-line{display:block;padding:13px 2px;border-top:1px solid var(--line);transition:background-color .16s}
.wk-line:last-child{border-bottom:1px solid var(--line)}
.wk-line:hover{background:linear-gradient(90deg,var(--accent-soft),transparent 70%)}
.wk-line-in{display:flex;flex-direction:column;gap:8px}
.wk-line-main{min-width:0}
.wk-line-ttl{margin:0;font-size:var(--fs-md);font-weight:600;letter-spacing:-.012em;line-height:1.5;color:var(--text);transition:color .16s}
.wk-line:hover .wk-line-ttl{color:var(--accent)}
.wk-line-sum{margin:4px 0 0;font-size:var(--fs-xs);line-height:1.65;color:var(--dim);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.wk-line-side{flex:none}
.wk-line-tags{display:flex;gap:6px;flex-wrap:wrap}
.wk-line-date{display:block;margin:6px 0 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);font-variant-numeric:tabular-nums}
@media (min-width:640px){.wk-line-in{flex-direction:row;justify-content:space-between;align-items:flex-start;gap:24px}.wk-line-main{flex:1}.wk-line-side{text-align:right}}
/* 标签 chip（v3-tag 同源） */
.wk-tag{display:inline-flex;align-items:center;height:19px;padding:0 6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);line-height:1;border:1px solid var(--line2);border-radius:999px;color:var(--dim);white-space:nowrap}
.wk-tag-accent{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
/* ── 文章页三栏骨架（max-1400：左文章导航 250 / 中正文 760 / 右 TOC 180）── */
.wk-shell{display:flex;max-width:1400px;margin:0 auto}
.wk-side{display:none;width:250px;flex:none;border-right:1px solid var(--line)}
.wk-side-in{position:sticky;top:80px;height:calc(100vh - 80px);overflow-y:auto;padding:24px 12px 24px 0}
.wk-side-nav{padding:24px 16px}
.wk-side-hd{margin:0 0 6px;padding-bottom:8px;border-bottom:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.16em;text-transform:uppercase;color:var(--dim)}
.wk-side-link{display:block;padding:6px 8px;border-left:1px solid transparent;font-size:var(--fs-xs);line-height:1.5;color:var(--dim);transition:color .16s,border-color .16s}
.wk-side-link:hover{color:var(--text);border-left-color:var(--line2)}
.wk-side-link[data-active="true"]{color:var(--accent);border-left-color:var(--accent)}
.wk-toc{display:none;width:180px;flex:none}
.wk-toc-in{position:sticky;top:80px;height:calc(100vh - 80px);overflow-y:auto;padding:24px 8px 32px 0}
.wk-toc nav{display:flex;flex-direction:column;gap:4px}
.wk-toc-link{position:relative;display:block;padding:6px 8px;border-left:1px solid transparent;font-size:var(--fs-xs);line-height:1.5;color:var(--dim);transition:color .16s,border-color .16s,padding-left .16s}
.wk-toc-link:hover{color:var(--text);border-left-color:var(--line2)}
.wk-toc-link[data-lv="1"]{padding-left:8px;color:var(--dim)}
.wk-toc-link[data-lv="2"]{padding-left:20px}
.wk-toc-link[data-lv="3"]{padding-left:30px;font-size:var(--fs-xs);color:var(--faint)}
.wk-toc-link[data-lv="4"]{padding-left:42px;font-size:var(--fs-xs);color:var(--faint)}
/* 导轨竖线（lv3 起挂在父级文字下方，主站同源） */
.wk-toc-link[data-lv="3"]::before,.wk-toc-link[data-lv="4"]::before{content:"";position:absolute;top:5px;bottom:5px;width:1px;background:var(--line)}
.wk-toc-link[data-lv="3"]::before{left:26px}
.wk-toc-link[data-lv="4"]::before{left:36px}
.wk-toc-link[data-lv="3"]:hover::before,.wk-toc-link[data-lv="4"]:hover::before{background:var(--line2)}
@media (min-width:1024px){.wk-side{display:block}.wk-main{padding:40px 32px}}
@media (min-width:1280px){.wk-toc{display:block}.wk-main{padding-left:40px;padding-right:24px}}
/* ── 文章 TOC 组件（article-toc.js 配套样式；拷自 mystation，类名 v3- 前缀保留，
   CSS 变量已映射本站 token：--background→--bg · --hairline→--line ·
   --hairline-strong→--line2 · --text-muted→--dim · --text-primary→--text ·
   --text-faint→--faint）── */
.v3-side-hd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.16em;text-transform:uppercase;color:var(--dim);padding-bottom:8px;border-bottom:1px solid var(--line);margin-bottom:6px}
.v3-side-link{display:block;font-size:var(--fs-xs);line-height:1.5;padding:6px 8px;border-left:1px solid transparent;color:var(--dim);transition:color .16s,border-color .16s}
.v3-side-link:hover{color:var(--text);border-left-color:var(--line2)}
.v3-side-link[data-active="true"]{color:var(--accent);border-left-color:var(--accent)}
/* 目录层级：缩进阶跃 + 左侧导轨竖线（h2 起），层级越深字号/字色递减 —— 主站同源 */
.v3-toc-link{position:relative;padding-left:8px;transition:color .16s,border-color .16s,padding-left .16s}
.v3-toc-link[data-lv="1"]{padding-left:8px;font-size:var(--fs-xs);color:var(--dim)}
.v3-toc-link[data-lv="2"]{padding-left:20px;font-size:var(--fs-xs)}
.v3-toc-link[data-lv="3"]{padding-left:30px;font-size:var(--fs-xs);color:var(--faint)}
.v3-toc-link[data-lv="4"]{padding-left:42px;font-size:var(--fs-xs);color:var(--faint)}
.v3-toc-link[data-lv="3"]::before,.v3-toc-link[data-lv="4"]::before{content:"";position:absolute;top:5px;bottom:5px;width:1px;background:var(--line)}
.v3-toc-link[data-lv="3"]::before{left:26px}
.v3-toc-link[data-lv="4"]::before{left:36px}
.v3-toc-link[data-lv="3"]:hover::before,.v3-toc-link[data-lv="4"]:hover::before{background:var(--line2)}
.v3-toc-link[data-active="true"]::before{background:var(--accent)}
.v3-toc-link[data-lv="3"][data-active="true"],.v3-toc-link[data-lv="4"][data-active="true"]{color:var(--accent)}
.v3-noscrollbar{scrollbar-width:none;-ms-overflow-style:none}
.v3-noscrollbar::-webkit-scrollbar{width:0;height:0;display:none}
/* 周报既有侧栏链接的 active 态（组件同步点亮） */
.wk-toc-link[data-active="true"]{color:var(--accent);border-left-color:var(--accent)}
.wk-toc-link[data-lv="3"][data-active="true"]::before,.wk-toc-link[data-lv="4"][data-active="true"]::before{background:var(--accent)}
/* FAB + 全高抽屉：<1280px 显示（桌面侧栏 / 周报 aside 的档位） */
.toc-fab{display:none;position:fixed;right:16px;bottom:16px;z-index:40}
.toc-fab button{width:40px;height:40px;display:flex;align-items:center;justify-content:center;border:1px solid var(--line2);border-radius:50%;background:var(--card);color:var(--dim);cursor:pointer;transition:color .16s,border-color .16s}
.toc-fab button:hover{color:var(--accent);border-color:var(--accent)}
.toc-drawer{position:fixed;inset:0;z-index:60}
.toc-drawer .toc-mask{position:absolute;inset:0;background:rgba(0,0,0,.6)}
.toc-drawer .toc-panel{position:absolute;right:0;top:0;height:100%;width:288px;max-width:86vw;border-left:1px solid var(--line);background:var(--bg);padding:24px;overflow-y:auto}
.toc-panel-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}
.toc-panel-hd .v3-side-hd{flex:1;margin:0}
.toc-close{border:0;background:transparent;color:var(--faint);cursor:pointer;padding:4px;display:flex}
.toc-close:hover{color:var(--text)}
.toc-nav{display:flex;flex-direction:column;gap:4px}
@media (max-width:1279px){.toc-fab{display:flex}}
@media (max-width:640px){.toc-fab{bottom:62px}}   /* 避让底部横排 .fab 工具排（mystation 同款抬升） */
/* 详情页桌面侧栏：fixed 右栏，右移 70px 避让竖排 .fab 工具排（右 16–58px） */
.p-toc{display:none;position:fixed;right:70px;top:80px;width:180px;max-height:calc(100vh - 104px);overflow-y:auto;padding-bottom:24px}
@media (min-width:1280px){.p-toc{display:block}}
/* ── 全站统计页（/statistics/）：访客向数据面板 —— hero 总览 tile + 热读榜 +
   SVG 环形图/Online 发表动态面积图 + 条形图 + 词云 tab。筛选（期刊/类型/年份）
   联动重算图表；热读榜与 hero 浏览数来自统计服务，为全量口径不随筛选重算 ── */
.st-hero{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0 0}
@media (max-width:860px){.st-hero{grid-template-columns:1fr 1fr}}
.st-tile{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:8px;background:var(--card);padding:15px 18px 13px}
.st-tile:before{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:var(--tc,var(--accent))}
.st-tile b{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:30px;font-weight:600;line-height:1.25;letter-spacing:-.02em;color:var(--text);font-variant-numeric:tabular-nums}
.st-tile span{display:block;margin-top:3px;font-size:var(--fs-xs);color:var(--dim)}
.st-tc1{--tc:#58a6ff}.st-tc2{--tc:#3fb950}.st-tc3{--tc:#a371f7}.st-tc4{--tc:#f0883e}
html[data-theme=light] .st-tc1{--tc:#0969da}html[data-theme=light] .st-tc2{--tc:#1a7f37}
html[data-theme=light] .st-tc3{--tc:#8250df}html[data-theme=light] .st-tc4{--tc:#bc4c00}
/* 热读文献 Top 5：行内淡色底条 = 浏览量占比（--w），No.1 实心章 / 2·3 描边章 */
.st-hot{margin:14px 0 0}
.st-card h3{display:flex;align-items:baseline;gap:8px;margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);font-weight:500;letter-spacing:.06em;color:var(--dim)}
.st-card h3 em{margin-left:auto;font-style:normal;font-weight:400;letter-spacing:0;color:var(--faint)}
.st-ranks{display:flex;flex-direction:column;gap:6px;margin-top:12px}
.st-rank{position:relative;display:flex;align-items:center;gap:12px;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);overflow:hidden;transition:border-color .16s}
.st-rank:hover{border-color:var(--line2)}
.st-rank:before{content:"";position:absolute;left:0;top:0;bottom:0;width:var(--w,0%);background:linear-gradient(90deg,var(--accent-soft),transparent)}
.st-rank>*{position:relative}
.st-rank .rk{flex:none;width:24px;height:24px;display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--line2);border-radius:7px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--dim);background:var(--card)}
.st-rank.pod .rk{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.st-rank.top .rk{border-color:var(--accent);background:var(--accent);color:var(--bg)}
.st-rank .tt{flex:1;min-width:0}
.st-rank .tt a{display:block;color:var(--text);font-size:var(--fs-sm);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;transition:color .16s}
.st-rank .tt a:hover{color:var(--accent)}
.st-rank .tt small{display:block;margin-top:1px;font-size:var(--fs-xs);color:var(--faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st-rank .n{flex:none;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--dim);font-variant-numeric:tabular-nums}
.st-filter{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:16px 0 4px}
.st-filter select,.st-filter input{width:auto;padding:7px 10px;border-radius:999px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-sm)}
.st-year{display:inline-flex;align-items:center;gap:6px}
.st-dash{color:var(--faint)}
.st-count{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint)}
.st-empty{margin:26px 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint)}
.st-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}
@media (max-width:860px){.st-grid{grid-template-columns:1fr}}
.st-card{border:1px solid var(--line);border-radius:8px;background:var(--card);padding:14px 16px 16px}
.st-wide{grid-column:1/-1}
.st-none{margin:4px 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint)}
/* 条形图：细轨 + 进场生长动画（每次重渲染触发；reduced-motion 关闭） */
.st-bars{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.st-row{display:grid;grid-template-columns:minmax(84px,190px) 1fr 34px;gap:10px;align-items:center}
.st-k{font-size:var(--fs-xs);color:var(--dim);text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st-bar{position:relative;height:16px;border-radius:4px;background:var(--bg);overflow:hidden}
.st-bar i{position:absolute;left:0;top:0;bottom:0;min-width:3px;background:var(--accent);opacity:.8;border-radius:4px;transform-origin:left;animation:stgrow .55s cubic-bezier(.25,.8,.35,1) both}
@keyframes stgrow{from{transform:scaleX(.02)}}
@media (prefers-reduced-motion:reduce){.st-bar i{animation:none}}
.st-n{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint);text-align:right}
/* 环形图（文章类型构成）：stroke-dasharray 段 + 中心合计 + 图例联动高亮 */
.st-donut{display:flex;align-items:center;gap:20px;margin-top:12px;flex-wrap:wrap}
.st-donut svg{flex:none;width:158px;height:158px}
.st-donut .seg{fill:none;stroke-width:3.6;transition:opacity .15s,stroke-width .15s;cursor:default}
.st-donut .seg.big{stroke-width:5}
.st-donut .seg.off{opacity:.22}
.st-donut .don-v{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:5.4px;font-weight:600;fill:var(--text)}
.st-donut .don-k{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:2.6px;fill:var(--faint);letter-spacing:.04em}
.st-legend{flex:1;min-width:170px;display:flex;flex-direction:column;gap:5px}
.st-lg{display:flex;align-items:center;gap:8px;padding:3px 8px;border-radius:6px;font-size:var(--fs-xs);color:var(--dim);cursor:default;transition:background .12s}
.st-lg i{flex:none;width:10px;height:10px;border-radius:3px;background:var(--lc,var(--accent))}
.st-lg span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st-lg b{margin-left:auto;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:500;color:var(--text);font-variant-numeric:tabular-nums}
.st-lg em{font-style:normal;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--faint);min-width:34px;text-align:right;font-variant-numeric:tabular-nums}
.st-lg.hl{background:var(--accent-soft)}
/* Online 发表动态面积图：JS 按容器实测像素构建 SVG，hover 显示「日期 · 累计 N 篇」 */
.st-growth{position:relative;margin-top:10px}
.st-growth svg{display:block;width:100%;height:auto}
.st-growth .gl{stroke:var(--line);stroke-width:1}
.st-growth .gt{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:10.5px;fill:var(--faint)}
.st-growth .dotc{fill:var(--card);stroke:var(--accent);stroke-width:2}
.st-growth .hit{fill:transparent;cursor:default}
.st-gtip{position:absolute;left:0;top:0;pointer-events:none;background:var(--bg);border:1px solid var(--line2);border-radius:6px;padding:4px 9px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--dim);white-space:nowrap;transform:translate(-50%,calc(-100% - 9px));opacity:0;transition:opacity .12s;box-shadow:0 8px 24px rgba(0,0,0,.25);z-index:2}
.st-gtip b{color:var(--text);font-weight:500}
/* 词云 tab：一张卡两个来源（关键词 / 标题·摘要高频词） */
.st-tabs{margin-left:auto;display:inline-flex;gap:4px}
.st-tab{padding:2px 10px;border:1px solid var(--line2);border-radius:999px;background:none;color:var(--dim);font-family:inherit;font-size:var(--fs-xs);cursor:pointer;transition:color .16s,border-color .16s,background-color .16s}
.st-tab:hover{color:var(--text)}
/* 单选 tab 组与排序胶囊同一纪律：选中项 accent 实底 */
.st-tab.on{border-color:var(--accent);color:var(--bg);background:var(--accent)}
/* 词云 v2：定高相对容器 + 绝对定位螺旋布局（大词居中、小词外溢、低频词灰阶垫底）；
   [hidden] 显式声明兜底 —— 防作者 display 规则压过 UA 的 hidden 隐藏（实测踩过） */
.st-cloud[hidden]{display:none}
.st-cloud{position:relative;height:300px;margin-top:12px}
@media (max-width:480px){.st-cloud{height:240px}}
.st-cloud .st-w{position:absolute;left:0;top:0;white-space:nowrap;line-height:1.15;cursor:default;animation:stwin .45s both;transition:opacity .16s,text-shadow .16s}
.st-cloud .st-w.st-wv{transform:rotate(-90deg)}
.st-cloud .st-w:hover{opacity:1!important;text-shadow:0 0 9px var(--accent-soft),0 0 22px var(--accent-soft)}
@keyframes stwin{from{opacity:0}}
@media (prefers-reduced-motion:reduce){.st-cloud .st-w{animation:none}}
.st-cta{margin:26px 0 0;font-size:var(--fs-sm);color:var(--dim)}
.st-cta a{color:var(--accent)}
/* 字号切换按钮（FAB 内）：A 字标，放大档点亮 */
.tbtn-fs{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:16px;font-weight:600}
.tbtn-fs.on{color:var(--accent);border-color:var(--accent)}
.wk-main{flex:1;min-width:0;max-width:760px;width:100%;margin:0 auto;padding:40px 20px}
.wk-h1{margin:0 0 20px}
.wk-tagsrow{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:0 0 20px}
.wk-tagsrow .dot{color:var(--faint)}
.wk-meta{display:flex;flex-wrap:wrap;align-items:center;gap:16px;margin:0 0 12px;padding-bottom:16px;border-bottom:1px solid var(--line);font-size:var(--fs-xs);color:var(--dim)}
.wk-meta span,.wk-meta a{display:inline-flex;align-items:center;gap:6px}
.wk-meta svg{width:16px;height:16px;flex:none}
.wk-dates{display:flex;flex-wrap:wrap;align-items:center;gap:16px;margin:0 0 40px;padding-bottom:16px;border-bottom:1px solid var(--line);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);color:var(--faint)}
/* 正文排版（blog-content 同源） */
.wk-content{font-size:var(--fs-base);color:var(--dim)}
.wk-content h2.md-h1{margin:40px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line2);font-size:var(--fs-h2);font-weight:700;line-height:1.35;color:var(--text);scroll-margin-top:80px}
.wk-content h3.md-h2{margin:32px 0 12px;font-size:var(--fs-h2);font-weight:600;line-height:1.4;color:var(--text);scroll-margin-top:80px}
.wk-content h4{margin:20px 0 8px;font-size:var(--fs-base);font-weight:600;color:var(--text)}
.wk-content p{margin:0 0 16px;line-height:1.75}
.wk-content strong{color:#85a4ff}
.wk-content em{font-style:italic}
.wk-content a{color:var(--accent);overflow-wrap:anywhere}
.wk-content a:hover{text-decoration:underline}
.wk-content .wk-img{margin:24px 0}
.wk-content .wk-img img{display:block;max-width:100%;height:auto;border:1px solid var(--line2);border-radius:8px}
.wk-content blockquote{margin:0 0 16px;padding-left:16px;border-left:4px solid var(--line2);font-style:italic;color:var(--faint)}
.wk-content hr{border:none;border-top:1px solid var(--line2);margin:32px 0}
/* 上一篇 / 下一篇（v3-prevnext 同源：双栏边框卡 + 虚线空位） */
.wk-prevnext{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:46px;padding-top:24px;border-top:1px solid var(--line2)}
.wk-pn{display:flex;flex-direction:column;gap:7px;min-width:0;padding:12px 14px;border:1px solid var(--line2);border-radius:6px;transition:border-color .16s,background-color .16s}
.wk-pn:hover{border-color:var(--accent);background:var(--accent-soft)}
.wk-pn-dir{display:inline-flex;align-items:center;gap:5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--fs-xs);letter-spacing:.1em;color:var(--faint);transition:color .16s}
.wk-pn:hover .wk-pn-dir{color:var(--accent)}
.wk-pn-dir svg{width:12px;height:12px;flex:none;transition:transform .18s ease}
.wk-pn:hover .wk-pn-dir svg{transform:translateX(-2px)}
.wk-pn-to-next{text-align:right}
.wk-pn-to-next .wk-pn-dir{justify-content:flex-end}
.wk-pn:hover .wk-pn-to-next .wk-pn-dir svg{transform:translateX(2px)}
.wk-pn-ttl{font-size:var(--fs-sm);line-height:1.5;letter-spacing:-.008em;color:var(--text);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.wk-pn-empty{min-height:68px;border:1px dashed var(--line2);border-radius:6px}
@media (max-width:640px){
  .wk-col{padding:40px 16px 80px}
  .wk-shell{display:block}
  .wk-main{padding:32px 16px}
  .wk-prevnext{grid-template-columns:1fr}
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

  /* ── 每篇文献浏览量（来自统计服务的按 path 计数）。
     本地预览（localhost / 127.0.0.1 / file://）没有统计后端，请求注定失败，
     直接跳过不再发（消除控制台网络报错），与上方打点的 hostname 判断同一口径；
     占位「…」回写「—」，避免停留在加载中状态 ── */
  var viewsEl = document.getElementById("views");
  if (viewsEl && !location.hostname.match(/^(localhost|127\\.0\\.0\\.1|)$/)) {
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
  } else if (viewsEl) {
    viewsEl.textContent = "—";
  }

  /* ── 作者行两行折叠（仅详情页有 .p-auth / #authToggle）：
     超过两行才加 .auth-clamp 并亮出「展开全部 N 位作者」按钮；
     两行以内 / 无 JS = 完整展示，渐进增强 ── */
  var authEl = document.querySelector(".p-auth");
  var authBtn = document.getElementById("authToggle");
  if (authEl && authBtn) {
    var authLh = parseFloat(getComputedStyle(authEl).lineHeight) || 23;
    if (authEl.scrollHeight > authLh * 2 + 1) {
      authEl.classList.add("auth-clamp");
      var authN = authBtn.dataset.n;
      var authOpenTxt = authN ? "展开全部 " + authN + " 位作者" : "展开全部作者";
      authBtn.textContent = authOpenTxt;
      authBtn.classList.add("on");
      authBtn.addEventListener("click", function () {
        var opened = authEl.classList.toggle("open");
        authBtn.textContent = opened ? "收起作者" : authOpenTxt;
      });
    }
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

  /* ── 字号切换（FAB「A」按钮）：标准 / 放大两档，只覆盖 --fs-* 变量（菜单与大标题
     固定不动）；偏好存 localStorage mbd-fs，head 内联脚本已前置位防闪烁 ── */
  var fsBtn = document.getElementById("fsBtn");
  if (fsBtn) {
    var fsOn = false;
    try { fsOn = localStorage.getItem("mbd-fs") === "lg"; } catch (e) {}
    var syncFs = function () {
      if (fsOn) document.documentElement.setAttribute("data-fs", "lg");
      else document.documentElement.removeAttribute("data-fs");
      fsBtn.title = fsOn ? "字号：放大（点击还原）" : "字号：标准（点击放大）";
      fsBtn.classList.toggle("on", fsOn);
      try { localStorage.setItem("mbd-fs", fsOn ? "lg" : ""); } catch (e) {}
    };
    fsBtn.addEventListener("click", function () { fsOn = !fsOn; syncFs(); });
    syncFs();
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

  /* ── 全站搜索独立页（/search/）：静态站没有检索后端，直接在 papers-data.js 的
     文献元数据 + weekly-data.js 的周报全文上做客户端匹配（文献：标题/作者/DOI/期刊/
     关键词/标签/摘要；周报：期标题/日期/摘要行/正文全文。多词空格分隔 = 同时命中）。
     放在网格逻辑之前 —— 搜索页没有 #grid 会提前 return ── */
  var spageQ = document.getElementById("spage-q");
  if (spageQ) {
    var sList = document.getElementById("spage-list");
    var sCount = document.getElementById("spage-count");
    var sUp = sList ? (sList.getAttribute("data-up") || "") : "";
    /* 周报数据（仅搜索页注入 weekly-data.js）：u 路径 / t 标题 / d 日期 /
       s 摘要行 / w 字数 / x 正文全文（构建期已截 30000 字符） */
    var WK = window.MBD_WK || [];
    WK.forEach(function (w) {
      w.se = (w.t + " " + (w.d || "") + " " + (w.s || "") + " " + (w.x || "")).toLowerCase();
    });

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
    /* 周报正文命中片段：在正文自身内找首个命中词，前后各取若干字符，省略号收边
       （注意不能在标题+日期+正文拼接串里找 —— 那样的下标对纯正文切片是错位的） */
    function wkSnip(w, tokens) {
      var text = w.x || "", low = text.toLowerCase();
      var pos = -1;
      for (var i = 0; i < tokens.length && pos < 0; i++) pos = low.indexOf(tokens[i]);
      if (pos < 0) return escHtml(text.slice(0, 120)) + (text.length > 120 ? "…" : "");
      var start = Math.max(0, pos - 50), end = Math.min(text.length, pos + 90);
      return (start > 0 ? "…" : "") + hl(text.slice(start, end), tokens) + (end < text.length ? "…" : "");
    }
    function paperRow(it, tokens) {
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
    }
    function wkRow(w, tokens) {
      var meta = [];
      if (w.d) meta.push(escHtml(w.d));
      if (w.s) meta.push(hl(w.s, tokens));
      if (w.w) meta.push(escHtml(w.w + " 字"));
      return '<a class="sres" href="' + sUp + escHtml(w.u) + '">'
        + '<span class="sres-body"><span class="sres-id">' + hl(w.t, tokens) + '</span>'
        + '<span class="sres-meta">' + meta.join('<i class="sres-sep">·</i>') + '</span>'
        + '<span class="sres-snip">' + wkSnip(w, tokens) + '</span></span></a>';
    }
    function renderSearch(raw) {
      var tokens = raw.trim().toLowerCase().split(/\\s+/).filter(Boolean);
      if (!tokens.length) {
        sCount.textContent = "";
        sList.innerHTML = '<p class="spage-hint">输入 标题 / 作者 / DOI / 期刊 / 关键词 / 摘要 / 周报内容 开始检索；多个词用空格分隔（需同时命中）</p>';
        return;
      }
      var hits = ITEMS.filter(function (it) {
        var hay = (it.se || "").toLowerCase();
        return tokens.every(function (t) { return hay.indexOf(t) > -1; });
      });
      var wkHits = WK.filter(function (w) {
        return tokens.every(function (t) { return w.se.indexOf(t) > -1; });
      });
      sCount.textContent = "找到 " + hits.length + " / " + ITEMS.length + " 篇文献 · "
        + wkHits.length + " / " + WK.length + " 期周报";
      if (!hits.length && !wkHits.length) {
        sList.innerHTML = '<p class="spage-empty">未找到与 “' + escHtml(raw) + '” 相关的文献或周报</p>';
        return;
      }
      var html = hits.map(function (it) { return paperRow(it, tokens); }).join("");
      if (wkHits.length) {
        html += '<div class="spage-grouphd">每周速递</div>'
          + wkHits.map(function (w) { return wkRow(w, tokens); }).join("");
      }
      sList.innerHTML = html;
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
  var state = { q: "", tag: null, journal: null, from: "", to: "", sort: "pub", page: 1, per: PAGE };
  try {
    var savedPer = parseInt(localStorage.getItem("mbd-per"), 10);
    if ([20, 30, 50].indexOf(savedPer) > -1) state.per = savedPer;   // 仅接受合法档位，旧值自动回默认 30
    if (localStorage.getItem("mbd-sort2")) { state.sort = localStorage.getItem("mbd-sort2"); }
  } catch (e) {}
  reshuffle();       // 初始随机键：每次访问页面「随机」排序都是新顺序
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
    /* 多选语义（GSP 同源）：tag/journal 为 null = 全部选中 = 不筛选；
       否则为「入选值」数组，命中任一即通过（OR），无标签/期刊的内容随之隐藏 */
    if (state.tag && !(it.tg || []).some(function (t) { return state.tag.indexOf(t) > -1; })) return false;
    if (state.journal && state.journal.indexOf(it.j || "") === -1) return false;
    if (state.from && (it.pd || "") < state.from) return false;
    if (state.to && (it.pd || "") > state.to) return false;
    if (state.q && (it.se || "").indexOf(state.q) === -1) return false;
    return true;
  }
  function cmp(a, b) {
    var dir = state.sort === "pub_asc" ? 1 : -1;
    var aa = a.pd || "", ab = b.pd || "";
    if (aa !== ab) return (aa < ab ? -1 : 1) * dir;
    // 同日期内按 id 排：与 build_site.py 的静态首屏顺序保持一致
    return (a.id || "").localeCompare(b.id || "");
  }
  /* 随机排序：每条目挂一个随机键做稳定排序 —— 翻页 / 筛选时不重排，
     点「随机」按钮或重新访问页面才重新洗牌 */
  function reshuffle() { ITEMS.forEach(function (it) { it._rk = Math.random(); }); }
  function cmpRand(a, b) { return (a._rk || 0) - (b._rk || 0); }
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
    if (it.cv) {                          // 封面通栏顶图（自然比例），与静态首屏输出一致
      var cov = document.createElement("img");
      cov.className = "c-cov";
      cov.loading = "lazy";
      cov.src = it.cv;
      cov.alt = "封面图";
      a.appendChild(cov);
    } else {                              // 无封面：期刊缩写占位块
      a.appendChild(el("span", "c-ph", it.jb || it.j || "—"));
    }
    var cap = el("span", "c-cap");
    var meta = el("span", "c-meta");      // 文章类型 + 期刊徽章
    if (it.pt) meta.appendChild(el("span", "c-ty", it.pt));
    var jb = el("span", "c-j", it.jb || it.j || "—");
    if (it.j && it.jb) jb.title = it.j;
    meta.appendChild(jb);
    cap.appendChild(meta);
    cap.appendChild(el("span", "c-t", it.t));
    a.appendChild(cap);
    return a;
  }
  function perSize(list) { return state.per > 0 ? state.per : (list.length || 1); }
  function render() {
    var list = ITEMS.filter(pass);
    list.sort(state.sort === "rand" ? cmpRand : cmp);
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
      var filtered = state.q || state.tag || state.journal || state.from || state.to;
      count.textContent = filtered ? "匹配 " + list.length + " / " + ITEMS.length + " 篇"
                                   : "共 " + ITEMS.length + " 篇";
    }
    if (empty) empty.style.display = list.length ? "none" : "block";
    syncFilterBtn();
  }
  function resetPage() { state.page = 1; render(); }

  /* ── 多选 chips（GSP 同源）：aria-pressed 是唯一状态真源，state 从 DOM 派生。
     默认全选（构建期即 pressed=true）= 不筛选（state[key]=null）；
     点击 chip = 剔除/恢复；全选/反选 为虚线胶囊 chip-act ── */
  function chipBoxes() { return Array.prototype.slice.call(document.querySelectorAll(".chips[data-key]")); }
  function boxChips(box) { return box.querySelectorAll("button.chip[data-v]"); }
  function readChips(box) {
    var key = box.getAttribute("data-key"), sel = [], c = boxChips(box);
    c.forEach(function (b) { if (b.getAttribute("aria-pressed") === "true") sel.push(b.getAttribute("data-v")); });
    state[key] = sel.length === c.length ? null : sel;   // 全部选中 = 不筛选
  }
  function writeChips(box) {
    var sel = state[box.getAttribute("data-key")];
    boxChips(box).forEach(function (b) {
      b.setAttribute("aria-pressed", String(!sel || sel.indexOf(b.getAttribute("data-v")) > -1));
    });
  }
  chipBoxes().forEach(function (box) {
    box.addEventListener("click", function (e) {
      var act = e.target.closest ? e.target.closest(".chip-act") : null;
      if (act) {
        var all = act.getAttribute("data-act") === "all";
        boxChips(box).forEach(function (b) { b.setAttribute("aria-pressed", String(all ? true : b.getAttribute("aria-pressed") !== "true")); });
      } else {
        var chip = e.target.closest ? e.target.closest("button.chip[data-v]") : null;
        if (!chip) return;
        chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
      }
      readChips(box); resetPage();
    });
  });

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
    if (["pub", "pub_asc", "rand"].indexOf(state.sort) === -1) state.sort = "pub";
    var sortBtns = sortseg.querySelectorAll("button");
    sortBtns.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-sort") === state.sort));
      b.addEventListener("click", function () {
        state.sort = b.getAttribute("data-sort");
        if (state.sort === "rand") reshuffle();   // 每次点「随机」都重新洗牌
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
    state = { q: "", tag: null, journal: null, from: "", to: "", sort: state.sort, page: 1,
              per: state.per };   // 保留每页数量（此 bug 继承自 GeoSciPlot，文献多时重置后分页会失效）
    var fF = document.getElementById("f-from"), fT = document.getElementById("f-to");
    if (fF) fF.value = "";
    if (fT) fT.value = "";
    if (q) q.value = "";
    /* 重置 = 恢复默认全选：所有 chip 写回 pressed=true（老单选时代「只留第一枚」
       的写法必须删净，否则在 URL 写回后执行会覆盖状态 —— 实际踩坑） */
    chipBoxes().forEach(function (box) { writeChips(box); });
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
  /* 筛选摘要（GSP 同源）：被剔除的 ≤3 个显示「排除 X、Y」，否则「仅 入选值」 */
  function chipSummary(key, label) {
    if (!state[key]) return null;
    var box = document.querySelector('.chips[data-key="' + key + '"]');
    if (!box) return null;
    var off = [];
    boxChips(box).forEach(function (b) {
      if (b.getAttribute("aria-pressed") === "false") off.push(b.getAttribute("data-v"));
    });
    return label + " " + (off.length > 0 && off.length <= 3 ? "排除 " + off.join("、") : "仅 " + state[key].join("、"));
  }
  function activeFilters() {
    var parts = [], s;
    if ((s = chipSummary("tag", "标签"))) parts.push(s);
    if ((s = chipSummary("journal", "期刊"))) parts.push(s);
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

  /* 支持带参数的链接（标签跳转 / 分享筛选结果）：/?tag=海冰&journal=Nature&sort=rand */
  var applied = false;
  try {
    var params = new URLSearchParams(location.search);
    ["q", "tag", "journal", "from", "to", "sort"].forEach(function (k) {
      var v = params.get(k);
      if (!v) return;
      if (k === "sort") {
        // 排序可分享，但仅本次生效 —— 分享链接不应永久改写接收者的排序偏好
        if (["pub", "pub_asc", "rand"].indexOf(v) === -1) return;
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
      } else if (k === "tag" || k === "journal") {
        /* 多选：?tag=A|B|C（旧单值链接天然兼容）。先写 state 再写回 chip 的
           aria-pressed，最后从 DOM 重读 —— 保持「DOM 是唯一真源」 */
        var box = document.querySelector('.chips[data-key="' + k + '"]');
        if (box) {
          state[k] = v.split("|");
          writeChips(box);
          readChips(box);
        }
      } else {
        state[k] = v;
        // 日期区间：回填输入框，避免"筛选生效但输入框是空的"的困惑
        var inp = document.getElementById("f-" + k);
        if (inp) inp.value = v;
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

/* ── 渲染样式切换（文章详情头部的两枚圆点）：html[data-rstyle] 已由
   <head> 内联脚本按 localStorage 设好，这里只回写按钮选中态；
   点击写偏好后刷新 —— 样式切换走整页重渲染 ── */
(function () {
  var btns = document.querySelectorAll(".rst-btn");
  if (!btns.length) return;
  var cur = document.documentElement.getAttribute("data-rstyle") || "plain";
  Array.prototype.forEach.call(btns, function (b) {
    b.setAttribute("aria-pressed", String(b.getAttribute("data-rs") === cur));
    b.addEventListener("click", function () {
      try { localStorage.setItem("mbd-rstyle", b.getAttribute("data-rs")); } catch (e) {}
      location.reload();
    });
  });
})();
"""


def page_shell(cfg: dict, title: str, body: str, depth: int = 0, gh_url: str = "",
               description: str = "", path: str = "", og_type: str = "website",
               og_image: str = "", bare: bool = False, rstyle: str = "",
               extra_assets: list[str] | None = None, noindex: bool = False) -> str:
    up = "../" * depth          # depth=2（weekly 文章页）需要 ../../，此前按布尔少了一级
    gh = gh_url or "https://github.com/{}/{}".format(
        cfg.get("owner") or "OWNER", cfg["repo"])
    desc = description or f"{cfg['subtitle']} —— {cfg['lede']}"
    # Open Graph：配置了 site_url 才输出，链接分享（微信/Telegram/X）出卡片；
    # 有封面图时附 og:image（分享卡片带封面，og:image 需绝对 URL）
    base = (cfg.get("site_url") or "").rstrip("/")
    og = ""
    canonical = ""
    if base:
        og_rows = [
            f'<meta property="og:title" content="{esc(title)}">',
            f'<meta property="og:description" content="{esc(desc[:200])}">',
            f'<meta property="og:type" content="{esc(og_type)}">',
            f'<meta property="og:url" content="{esc(base + path)}">',
            f'<meta property="og:site_name" content="{esc(cfg["title"])}">',
        ]
        if og_image:
            og_rows.append(f'<meta property="og:image" content="{esc(og_image)}">')
        og = "\n".join(og_rows)
        # canonical 与 og:url 同值：多入口/带参数访问时搜索引擎只认这一份
        canonical = f'<link rel="canonical" href="{esc(base + path)}">'
    # ── 顶部菜单栏：全部页面共用，样式对齐主站 zbhgis.com（v3-nav 同源）。
    #    左站点名 / 右导航组：每周速递（/weekly/）· 全站统计（/statistics/，按 path 标 active）
    #    · 更多▾ 下拉（hover / focus-visible 展开，纯 CSS；zbhgis 与 GeoSciPlot 外链）。
    #    菜单项与下拉项均带主站同款 14/15px stroke 图标；≤640px 转 details 汉堡 ──
    is_weekly = path.startswith("/weekly")
    is_stats = path.startswith("/statistics")
    cur_w = ' aria-current="page"' if is_weekly else ''
    cur_s = ' aria-current="page"' if is_stats else ''
    ico_send = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/></svg>')
    ico_chart = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 20V10M12 20V4M6 20v-4"/></svg>')
    ico_globe = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>')
    # GeoSciPlot 图标与主站「更多」菜单同款（图片样式：方框 + 圆点 + 山形）
    ico_geosci = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>')
    nav = f"""<header class="mnav">
<nav class="mnav-in">
<a class="mnav-brand" href="/"><img src="{up}assets/favicon.png?v={BUILD_VER}" alt="" width="26" height="26">{esc(cfg['title'])}</a>
<ul class="mnav-links">
<li><a class="mnav-link" data-active="{str(is_weekly).lower()}" href="/weekly/"{cur_w}>{ico_send}每周速递</a></li>
<li><a class="mnav-link" data-active="{str(is_stats).lower()}" href="/statistics/"{cur_s}>{ico_chart}全站统计</a></li>
<li class="mnav-more"><button type="button" class="mnav-link mnav-more-trigger" aria-haspopup="true" title="更多站点">更多<svg class="mnav-more-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>
<ul class="mnav-dd">
<li><a href="https://zbhgis.com" rel="noopener" target="_blank">{ico_globe}zbhgis</a></li>
<li><a href="https://geosciplot.zbhgis.com" rel="noopener" target="_blank">{ico_geosci}GeoSciPlot</a></li>
</ul></li>
</ul>
<details class="mnav-m">
<summary class="mnav-icon" title="菜单" aria-label="打开菜单"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg></summary>
<ul class="mnav-dd">
<li><a href="/weekly/"{cur_w}>{ico_send}每周速递</a></li>
<li><a href="/statistics/">{ico_chart}全站统计</a></li>
<li class="mnav-dd-sep"></li>
<li><a href="https://zbhgis.com" rel="noopener" target="_blank">{ico_globe}zbhgis</a></li>
<li><a href="https://geosciplot.zbhgis.com" rel="noopener" target="_blank">{ico_geosci}GeoSciPlot</a></li>
</ul>
</details>
</nav>
</header>"""
    # bare=True（每周速递等全宽页面）：正文不进 .wrap（自带容器），仅 footer 包一层
    fab = f"""<div class="fab"><a class="tbtn" href="{up}search/" title="全站搜索" aria-label="全站搜索"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 14 14"/></svg></a><a class="tbtn" href="/" title="返回 Home（文献库首页）" aria-label="返回 Home"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8 8 3l5.5 5M4 7v6h8V7"/></svg></a><a class="tbtn" href="{gh}" rel="noopener" target="_blank" title="在 GitHub 查看仓库（数据与索引）"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></a><button type="button" class="tbtn" id="themeBtn" title="切换明暗主题" aria-label="切换明暗主题"><svg class="ic-sun" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1"/></svg><svg class="ic-moon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13.5 9.5A6 6 0 0 1 6.5 2.5a6 6 0 1 0 7 7z"/></svg></button><button type="button" class="tbtn tbtn-fs" id="fsBtn" title="字号：标准">A</button><button type="button" class="tbtn" id="topBtn" title="回到顶部" aria-label="回到顶部"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13.5v-9M4.5 8 8 4.5 11.5 8"/></svg></button></div>"""
    foot = f"""<footer class="site">
  <span>{esc(cfg['title'])} · {esc(cfg['subtitle'])}</span>
  <span><a href="https://github.com/{esc(cfg.get('owner') or 'OWNER')}/{esc(cfg['repo'])}" rel="noopener">GitHub 仓库</a> · 元数据来自 Crossref / OpenAlex · 版权归原出版方</span>
</footer>"""
    inner = f"""{fab}
<div class="wrap">
{body}
{foot}
</div>""" if not bare else f"""{fab}
{body}
<div class="wrap">
{foot}
</div>"""
    # 渲染样式偏好（仅文章详情类页面传入）：先读 localStorage，无则用页面默认，
    # 在 <head> 里就设好 html[data-rstyle]，避免正文样式闪烁
    rstyle_head = ""
    if rstyle:
        rstyle_head = (f'<script>try{{var r=localStorage.getItem("mbd-rstyle");'
                       f'document.documentElement.setAttribute("data-rstyle",r||"{rstyle}")}}'
                       f'catch(e){{document.documentElement.setAttribute("data-rstyle","{rstyle}")}}</script>')
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{'<meta name="robots" content="noindex">' if noindex else ''}
{og}
{canonical}
<link rel="stylesheet" href="{up}assets/style.css?v={BUILD_VER}">
<link rel="icon" type="image/png" href="{up}assets/favicon.png">
<script>try{{var t=localStorage.getItem("mbd-theme");if(t)document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>
<script>try{{var f=localStorage.getItem("mbd-fs");if(f)document.documentElement.setAttribute("data-fs",f)}}catch(e){{}}</script>
<script>
/* ── 外链图多源降级（GeoSciPlot gallery.js 同源思路）：
   jsdelivr gh 直链逐源切换 cdn → fastly → gcore → raw.githubusercontent；
   首页卡片封面（.c-cov）在非 jsdelivr 直链失败、或所有候选耗尽时，
   换成期刊缩写占位块（.c-ph，与无封面卡片同语言）——不留破图；
   周报图表图维持原行为（候选耗尽即止，不做占位替换） ── */
window.__coverPlaceholder = function (img) {{
  var card = img.closest ? img.closest("a.card") : null;
  if (!card) return;
  var ph = document.createElement("span");
  ph.className = "c-ph";
  var jb = card.querySelector(".c-j");
  ph.textContent = (jb && jb.textContent) || "—";
  img.replaceWith(ph);
}};
window.__imgFallback = function (img) {{
  var orig = img.dataset.origSrc || "";
  if (!orig) {{
    orig = img.src;
    if (orig.indexOf("https://cdn.jsdelivr.net/gh/") !== 0) {{ window.__coverPlaceholder(img); return; }}
    img.dataset.origSrc = orig;
    img.dataset.srcTry = "0";
  }}
  /* 真实外链形如 gh/{{user}}/{{repo}}@{{branch}}/{{path}}（@ 前是「用户/仓库」两段） */
  var m = orig.match(/^https:\\/\\/cdn\\.jsdelivr\\.net\\/gh\\/([^\\/@\\s]+)\\/([^\\/@\\s]+)(?:@([^\\/\\s]+))?\\/(.+)$/);
  if (!m) {{ window.__coverPlaceholder(img); return; }}
  var spec = m[1] + "/" + m[2] + (m[3] ? "@" + m[3] : "");
  var n = parseInt(img.dataset.srcTry || "0", 10);
  var alts = ["https://fastly.jsdelivr.net/gh/" + spec + "/" + m[4],
              "https://gcore.jsdelivr.net/gh/" + spec + "/" + m[4]];
  if (m[3]) alts.push("https://raw.githubusercontent.com/" + m[1] + "/" + m[2] + "/" + m[3] + "/" + m[4]);
  if (n < alts.length) {{
    img.dataset.srcTry = String(n + 1);
    img.src = alts[n];
  }} else {{
    window.__coverPlaceholder(img);
  }}
}};
document.addEventListener("error", function (e) {{
  var t = e.target;
  if (t && t.tagName === "IMG" && (t.classList.contains("c-cov") || t.src.indexOf("jsdelivr") > -1)) window.__imgFallback(t);
}}, true);
</script>
{rstyle_head}
</head>
<body>
{nav}
{inner}
{"".join(f'<script src="{up}assets/{a}?v={BUILD_VER}"></script>' for a in (extra_assets or []))}
<script src="{up}assets/papers-data.js?v={BUILD_VER}"></script>
<script src="{up}assets/papers.js?v={BUILD_VER}"></script>
</body>
</html>
"""


def flat(values, default: str = "—") -> Counter:
    return Counter([str(v) if v else default for v in values])


def chips(values: Counter, key: str) -> str:
    """多选 chips（GSP 同源）：默认全选（aria-pressed=true），点击剔除，
    行尾提供 全选/反选 虚线胶囊；计数徽标 <i class="n">（mini 胶囊、定宽防跳动）。"""
    items = []
    for name, n in sorted(values.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        items.append(f'<button type="button" class="chip" data-v="{esc(name)}" aria-pressed="true">{esc(name)}<i class="n">{n}</i></button>')
    items.append('<button type="button" class="chip chip-act" data-act="all">全选</button>')
    items.append('<button type="button" class="chip chip-act" data-act="invert">反选</button>')
    return f'<div class="chips" data-key="{key}">\n  ' + "\n  ".join(items) + "\n</div>"


def filter_row(label: str, inner: str) -> str:
    return f'<div class="fgroup"><span class="flabel">{esc(label)}</span>{inner}</div>'


def display_title(p: dict) -> str:
    """卡片/搜索结果的主展示标题：中文优先，无中文回落英文。"""
    return (p.get("title_zh") or "").strip() or (p.get("title") or "").strip()


RSTYLE_DOTS = (
    ("plain", "普通样式：系统色，标题无装饰", "rst-dot-plain"),
    ("accent", "强调样式：蓝色渲染", "rst-dot-accent"),
    ("green", "强调样式：绿色渲染", "rst-dot-green"),
    ("purple", "强调样式：淡紫渲染", "rst-dot-purple"),
    ("orange", "强调样式：橘色渲染", "rst-dot-orange"),
)


def rstyle_toggle(default: str) -> str:
    """渲染颜色切换按钮（五枚小圆点）：透明斜杠圈=plain（系统色无装饰），
    蓝/绿/淡紫/橘实心圈=对应强调色。点击写 localStorage 并刷新；
    aria-pressed 由服务端按页面默认渲染，papers.js 按用户实际偏好修正。"""
    btns = "".join(
        f'<button type="button" class="rst-btn" data-rs="{v}" title="{t}"'
        f' aria-pressed="{str(default == v).lower()}"><i class="rst-dot {c}"></i></button>'
        for v, t, c in RSTYLE_DOTS)
    return (f'<span class="rstyle" role="group" aria-label="渲染颜色切换">{btns}</span>')


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


def cover_src(cover: str, up: str = "") -> str:
    """cover 字段兼容两种取值：本地相对路径（assets/ 下）或完整外链 URL。
    外链原样返回（前端 __imgFallback 已提供 jsdelivr 多源降级），
    本地路径拼资产前缀并带 ?v= 防缓存。"""
    c = (cover or "").strip()
    if c.startswith(("http://", "https://")):
        return c
    return f"{up}assets/{c}?v={BUILD_VER}"


def card_html(p: dict) -> str:
    """首页瀑布流卡片（GeoSciPlot 同源语言）：封面图 + 类型/期刊徽章 + 标题。
    无封面输出期刊缩写占位块，保证瀑布流版式成立。"""
    jb = journal_badge(p) or "—"
    ty = display_type(p)
    ty_html = f'<span class="c-ty" title="{esc(ty)}">{esc(ty)}</span>' if ty else ''
    alt = alt_title(p)
    tip = esc(alt) if alt else esc(p.get('title'))
    cov = (p.get("cover") or "").strip()
    cov_html = (f'\n  <img class="c-cov" src="{cover_src(cov)}" alt="封面图" loading="lazy">'
                if cov else
                f'\n  <span class="c-ph">{esc(jb)}</span>')
    return f"""<a class="card" href="{esc(p['id'])}/" title="{tip}">{cov_html}
  <span class="c-cap"><span class="c-meta">{ty_html}<span class="c-j" title="{esc(p.get('journal') or '')}">{esc(jb)}</span></span>
  <span class="c-t">{esc(display_title(p))}</span></span>
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
  <h1 class="home-title"><img class="logo" src="assets/logo.png?v={BUILD_VER}" alt="MacroBiodiv logo">{esc(cfg['title'])}</h1>
  <a class="gh-note" href="https://github.com/{esc(cfg.get('owner') or 'OWNER')}/{esc(cfg['repo'])}" rel="noopener" target="_blank" title="在 GitHub 查看数据与索引">{ghsvg}<span>文献数据存储于 <b>GitHub</b>，访问需具备 <b>GitHub</b> 访问能力（点此查看仓库）</span></a>
  <p class="lede">{esc(cfg['lede'])}</p>
  <div class="meta-row"><span id="count">共 {len(items)} 篇</span> · {'%d 个标签 · ' % len(tag_counter) if tag_counter else ''}{len(journal_counter)} 本期刊 · {len(years)} 个年份 · 点击卡片查看详情</div>
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
{filter_row("标签", chips(tag_counter, "tag")) if tag_counter else "<!-- 标签为空：标签行隐藏，管理员在后台补录 tags 后自动出现 -->"}
{filter_row("期刊", chips(journal_counter, "journal"))}
{filter_row("时间", '<input type="date" id="f-from" class="dateinp" title="按在线发表（online）日期筛选">\n'
  + ' <span class="flabel" style="min-width:auto">至</span>\n'
  + '<input type="date" id="f-to" class="dateinp" title="按在线发表（online）日期筛选">\n'
  + ' <span class="flabel" style="min-width:auto;margin-left:18px">排序</span>\n'
  + ' <span class="sorter" id="sortseg" role="group" aria-label="排序" style="vertical-align:middle">'
  + '<button type="button" data-sort="pub" aria-pressed="true" title="发表日期 新→旧"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.5v8M4.8 7.3 8 10.5l3.2-3.2M3 13.5h10"/></svg><span>新到旧</span></button>'
  + '<button type="button" data-sort="pub_asc" aria-pressed="false" title="发表日期 旧→新"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13.5v-8M4.8 8.7 8 5.5l3.2 3.2M3 2.5h10"/></svg><span>旧到新</span></button>'
  + '<button type="button" data-sort="rand" aria-pressed="false" title="随机顺序（每次点击重新洗牌）"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 4.5h2.4l6.2 7h2.4M2.5 11.5h2.4l2-2.3M9.6 6.8l1.5-2.3h2.4M12.3 2.9l2.2 1.6-2.2 1.6M12.3 9.9l2.2 1.6-2.2 1.6"/></svg><span>随机</span></button>'
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
    # SEO/GEO：首页 title 带中文关键词（只写 "MacroBiodiv" 时，搜"宏观生物多样性
    # 文献库"在 title 上无命中）；附站点级 WebSite JSON-LD，中英双名都给引擎。
    base = (cfg.get("site_url") or "").rstrip("/")
    ld_site = ""
    if base:
        ld = {
            "@context": "https://schema.org", "@type": "WebSite",
            "name": cfg["title"], "alternateName": cfg["subtitle"],
            "url": base, "description": cfg["lede"], "inLanguage": "zh-CN",
        }
        ld_html = json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c")
        ld_site = '<script type="application/ld+json">' + ld_html + "</script>"
    return page_shell(cfg, f"{cfg['subtitle']} · {cfg['title']}", body + ld_site, path="/")


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

    # 标签 / 关键词做成可跳转：标签回首页并套用该标签筛选，关键词走全站搜索
    tags = p.get("tags") or []
    tags_html = "".join(
        f'<a class="tag" href="/?tag={quote(str(t), safe="")}">{esc(t)}</a>' for t in tags
    ) if tags else '<span style="color:var(--faint)">—</span>'
    kws = p.get("keywords") or []
    kws_html = "".join(
        f'<a class="tag" href="/search/?q={quote(str(k), safe="")}">{esc(k)}</a>' for k in kws
    ) if kws else ""

    # ── 信息区（公众号字段行式：「字段名：值」同行，15px/1.8，一行一字段） ──
    def frow(label: str, value: str) -> str:
        return (f'<p class="frow"><i class="fk">{esc(label)}</i>'
                f'<span class="fv">{value}</span></p>')

    journal_dd = esc(p.get("journal") or "—")
    if p.get("journal_short") and p["journal_short"] != p.get("journal"):
        journal_dd += f' <span style="color:var(--faint)">/ {esc(p["journal_short"])}</span>'
    date_dd = esc(p.get("published") or p.get("year") or "—")
    if p.get("published_online") and p.get("published"):
        date_dd += ' <span style="color:var(--faint)">（online）</span>'
    ty = display_type(p)

    # 字段顺序对齐公众号推文（标题在标题区展示，不重复入行）；可选字段有值才输出
    info = []
    if doi:
        info.append(frow("DOI", doi_html))
    info.append(frow("期刊", f'<span class="hl">{journal_dd}</span>'))
    if ty:
        info.append(frow("类型", esc(ty)))
    info.append(frow("发表", date_dd))
    if vp:
        info.append(frow("卷期页", esc(vp)))
    info.append(frow("收录", esc(p.get("added") or "—")))
    info.append(frow("被浏览", '<span id="views">…</span>'))
    if kws_html:
        info.append(frow("关键词", kws_html))
    info.append(frow("标签", tags_html))

    # 节标题：公众号「1.信息 / 2.摘要 / 3.图表」式，编号动态顺延（有封面图才有图表节）
    n_sec = 0

    def sech(txt: str) -> str:
        nonlocal n_sec
        n_sec += 1
        return f'<h2 class="sec-h"><span class="tx">{n_sec}. {esc(txt)}</span></h2>'

    info_sec = f'<section class="sec">{sech("信息")}<div class="frows">' + "".join(info) + "</div></section>"

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
    abs_sec = (f'<section class="sec">{sech("摘要")}'
               + "".join(abs_blocks) + "</section>") if abs_blocks else ""

    # ── 顶部徽章行 ──
    top_row = (f'<div class="p-top"><span class="c-j" title="{esc(p.get("journal") or "")}">{esc(journal_badge(p) or "—")}</span>'
               f'<span class="c-y">{esc(p.get("year") or "")}</span>'
               + (f'<span class="p-ct">{esc(ty)}</span>' if ty else "")
               + rstyle_toggle("accent") + '</div>')

    # ── 标题：中文优先（与卡片一致），另一语言作副行 ──
    alt = alt_title(p)
    alt_html = f'<p class="alt-title">{esc(alt)}</p>' if alt else ""
    back_ico = ('<svg class="ico ico-l" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
                'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<path d="M10 3 5 8l5 5"/></svg>')

    # ── 图表节（可选）：封面图独立成节（公众号「3.图表」式），无封面则整节不出现 ──
    cov = (p.get("cover") or "").strip()
    fig_sec = (f'<section class="sec">{sech("图表")}'
               f'<figure class="p-cover"><img src="{cover_src(cov, "../")}" '
               f'alt="文章图表 / 封面图"></figure></section>') if cov else ""

    body = f"""<a class="back" href="../">{back_ico}返回全部</a>
<div class="pbody">
<div class="p-head">{top_row}
<h1 class="p-title">{esc(display_title(p))}</h1>{alt_html}
<p class="p-auth">{esc(" · ".join(p.get("authors") or []) or "—")}</p>
<button type="button" class="auth-toggle" id="authToggle" data-n="{len(p.get("authors") or [])}">展开全部作者</button>
</div>
{info_sec}
{abs_sec}
{fig_sec}
<section class="sec">{sech("引用")}
  <div class="cite-line"><span id="citeline">{esc(citation(p))}</span></div>
  <div class="btx"><button type="button" data-copy="citeline">复制引用</button><button type="button" data-copy="bibtex">复制 BibTeX</button><details class="full"><summary>查看 BibTeX</summary><pre id="bibtex">{esc(to_bibtex(p))}</pre></details></div>
</section>
<p class="a-end">· END ·</p>
{chr(10).join(pager)}
</div>"""
    desc = (p.get("title") or "") + " — " + (p.get("journal") or "")
    if (p.get("abstract") or "").strip():
        desc += "：" + p["abstract"].strip()[:120]
    # 学术结构化数据（Schema.org ScholarlyArticle），利于搜索引擎理解文献信息
    base = (cfg.get("site_url") or "").rstrip("/")
    if cov.startswith(("http://", "https://")):
        og_image = cov                      # 外链封面本身就是绝对 URL
    else:
        og_image = (f"{base}/assets/{cov}" if (base and cov) else "")
    ld = {
        "@context": "https://schema.org", "@type": "ScholarlyArticle",
        "headline": p.get("title") or "", "inLanguage": "en",
        "author": [{"@type": "Person", "name": a} for a in (p.get("authors") or [])],
        "datePublished": p.get("published") or p.get("year") or None,
        "isPartOf": p.get("journal") or None,
        "publisher": p.get("publisher") or None,
        "identifier": p.get("doi") or None,
        "url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
        "image": og_image or None,
        "keywords": ", ".join((p.get("keywords") or []) + (p.get("tags") or [])) or None,
    }
    ld = {k: v for k, v in ld.items() if v}
    # "</script>" 会出现于标题/摘要时会把内嵌 script 提前截断：把 "<" 转成 Unicode 转义
    ld_html = (json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c") if base else "")
    ld_block = ('<script type="application/ld+json">' + ld_html + "</script>") if ld_html else ""
    body = body + ld_block
    return page_shell(cfg, f"{p.get('title')} · {cfg['title']}", body, depth=1,
                      gh_url=f"https://github.com/{cfg.get('owner') or 'OWNER'}/{cfg['repo']}",
                      description=desc, path=f"/{p['id']}/", og_type="article",
                      og_image=og_image, rstyle="accent",
                      extra_assets=["image-lightbox.js", "article-toc.js"])


def build_search_page(cfg: dict, items: list) -> str:
    """全站搜索独立页：版式对齐主站 zbhgis.com 的 /search。
    结果行由 papers.js 在客户端渲染（文献来自 papers-data.js，
    每周速递各期全文来自 weekly-data.js，两组分别展示）；文献没有缩略图，
    结果行 = 标题 + 作者/期刊/年份/DOI，周报结果行 = 标题 + 日期/摘要 + 命中片段。"""
    body = f"""<header class="site">
  <p class="kicker">{esc(cfg['title'].upper())} · SEARCH</p>
  <h1 class="spage-title">全站搜索</h1>
  <p class="lede">检索全部 {len(items)} 篇文献（标题 / 作者 / DOI / 期刊 / 关键词 / 摘要全文 / 标签）
  与每周速递各期正文；多个词用空格分隔（需同时命中）。</p>
</header>

<input id="spage-q" class="spage-q" type="search" placeholder="输入关键词搜索全站内容…" autocomplete="off" autofocus>
<div class="spage-count" id="spage-count"></div>
<div class="spage-list" id="spage-list" data-up="../"></div>"""
    return page_shell(cfg, f"全站搜索 · {cfg['title']}", body, depth=1, path="/search/",
                      extra_assets=["weekly-data.js"])


def build_stats_data(items: list) -> None:
    """全站统计数据 → site/assets/stats-data.js（仅 /statistics/ 页注入）。
    只带统计所需的英文原文与分面字段 —— 中文翻译字段与每周速递一律不进这份数据。"""
    data = [{
        "id": p.get("id") or "",               # 路径 → 文献 映射（热读榜用）
        "t": p.get("title") or "",             # 英文原题
        "ab": p.get("abstract") or "",         # 英文摘要
        "kw": p.get("keywords") or [],         # OpenAlex 英文词表
        "j": p.get("journal") or "",
        "at": display_type(p),                 # 文章体裁（article_type 优先）
        "y": p.get("year") or "",
        "po": p.get("published") or "",     # 发表日期（Online 发表动态面积图；注意
                                            # published_online 只是布尔标注，日期在 published）
    } for p in items]
    # "<" 转义防摘要正文里出现 </script> 提前截断内嵌 script
    (SITE / "assets" / "stats-data.js").write_text(
        "window.MBD_STATS = "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        + ";\n", encoding="utf-8")


def build_stats_page(cfg: dict, items: list) -> str:
    """全站统计页（访客向数据面板）：hero 总览 + 热读榜 + SVG 环形图/Online 发表
    动态面积图 + 条形图 + 词云 tab；数据来自 stats-data.js（仅本页注入）。
    筛选（期刊 / 类型 / 年份区间）→ 图表实时重算；热读榜与 hero 浏览数为全量口径。"""
    body = f"""<header class="site">
  <p class="kicker">{esc(cfg['title'].upper())} · STATS</p>
  <h1 class="spage-title">全站统计</h1>
  <p class="lede">这个文献库的一瞥——收录了多少文献、来自哪些期刊、大家都在读什么。
  热读榜按访客浏览量实时计入（不含每周速递）。</p>
</header>

<section class="st-hero" aria-label="收录总览">
  <div class="st-tile st-tc1"><b id="stv-papers">0</b><span>收录文献 · 篇</span></div>
  <div class="st-tile st-tc2"><b id="stv-journals">0</b><span>来源期刊 · 种</span></div>
  <div class="st-tile st-tc3"><b id="stv-kw">0</b><span>研究关键词 · 个</span></div>
  <div class="st-tile st-tc4"><b id="stv-views">…</b><span>文献被浏览 · 次</span></div>
</section>

<section class="st-card st-hot"><h3>热读文献<em>Top 5 · 按访客浏览量 · 全量口径</em></h3>
<div class="st-ranks" id="st-rank"><p class="st-none">加载中…</p></div></section>

<div class="st-filter">
  <select id="st-journal" aria-label="按期刊筛选"><option value="*">期刊 全部</option></select>
  <select id="st-type" aria-label="按类型筛选"><option value="*">类型 全部</option></select>
  <span class="st-year">
    <input type="number" id="st-from" placeholder="年份起" min="1800" max="2100" aria-label="年份起">
    <span class="st-dash">–</span>
    <input type="number" id="st-to" placeholder="年份止" min="1800" max="2100" aria-label="年份止">
  </span>
  <button id="st-reset" type="button">重置</button>
  <span class="st-count" id="st-count"></span>
</div>
<p class="st-empty" id="st-empty" hidden>当前筛选条件下没有文献</p>
<div class="st-grid" id="st-grid">
  <section class="st-card st-wide"><h3>Online 发表动态<em>按 online 发表日期 · 累计</em></h3>
    <div class="st-growth" id="st-growth"></div></section>
  <section class="st-card"><h3>文章类型构成</h3><div class="st-donut" id="st-types"></div></section>
  <section class="st-card"><h3>期刊 Top 10</h3><div class="st-bars" id="st-journals"></div></section>
  <section class="st-card st-wide"><h3>研究热词
    <span class="st-tabs" role="tablist">
      <button type="button" class="st-tab on" data-tab="kwc">关键词</button>
      <button type="button" class="st-tab" data-tab="wc">标题 · 摘要</button>
    </span></h3>
    <div class="st-cloud" id="st-kwc"></div>
    <div class="st-cloud" id="st-wc" hidden></div>
  </section>
</div>
<p class="st-cta">想找某篇文献？<a href="../search/">去全站搜索</a>，或回<a href="../">文献库首页</a>按标签与期刊浏览。</p>"""
    return page_shell(cfg, f"全站统计 · {cfg['title']}", body, depth=1, path="/statistics/",
                      extra_assets=["stats-data.js", "stats.js"])


# ── 每周速递（/weekly/）：内容源 content/weekly/*.md，布局与主站 zbhgis.com
#    博客同源（列表 v3-col/kicker/label/line，文章三栏 side/main/toc + prevnext）；
#    md 经 scripts/render_md.py（标准库迷你渲染器）转 HTML ──
WEEKLY_SRC = ROOT / "content" / "weekly"
WK_AUTHOR = "zbhgis 浩瀚地学"
WK_ICO_USER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>'
WK_ICO_DOC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>'
WK_ICO_CLOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
WK_ICO_LEFT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5m0 0l6-6m-6 6l6 6"/></svg>'
WK_ICO_RIGHT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14m0 0l-6-6m6 6l-6 6"/></svg>'


def _month_label(date: str) -> str:
    """2026-04-06 → 2026年4月（无日期返回空串）。"""
    m = re.match(r"(\d{4})-(\d{1,2})", date or "")
    return f"{m.group(1)}年{int(m.group(2))}月" if m else ""


def build_weekly(cfg: dict) -> list[str]:
    """构建 /weekly/ 列表页与各文章页；返回（供 sitemap 的）URL 路径列表。"""
    if not WEEKLY_SRC.is_dir():
        return []
    try:
        from render_md import parse_md
    except ImportError:
        print("· 每周速递：缺 scripts/render_md.py，跳过")
        return []

    posts = []
    for md in sorted(WEEKLY_SRC.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        meta, doc, toc, plain = parse_md(text)
        # 标题自动识别正文第一个一级标题（frontmatter 的 title 不作为标题来源）；
        # 文件直接以「文献N」开头（无总标题 H1）时回退 frontmatter title / 文件名
        h1 = next((txt for lv, hid, txt in toc
                   if hid == "doc-1" and not re.match(r"^文献\d+", txt)), "")
        title = (h1 or str(meta.get("title") or "")).strip() or md.stem
        m = re.search(r"精选(\d+)", title)
        slug = f"weekly-{m.group(1) if m else md.stem[:8]}"
        # 正文首个 `# ` 标题已用作页面标题：剥离（TOC 同步剔除 doc-1）
        doc = re.sub(r'<h2 id="doc-1" class="md-h1">.*?</h2>\n*', "", doc, count=1)
        toc = [t for t in toc if t[1] != "doc-1"]
        date = str(meta.get("date") or "")[:10]
        # 年月标签从标题提取：期号覆盖的周区间形如 260309-0315 → 2026年3月
        # （不看 frontmatter date——那是补录日期，不代表期号所属月份）；
        # 标题里没有日期段时回退 frontmatter date
        m_ym = re.search(r"(?<!\d)(\d{2})(0[1-9]|1[0-2])\d{2}(?!\d)", title)
        month = f"20{m_ym.group(1)}年{int(m_ym.group(2))}月" if m_ym else _month_label(date)
        words = len(plain)
        reading = max(1, -(-words // 200))          # ceil(words/200)，主站同口径
        n_papers = len([1 for lv, _, txt in toc if lv == 1])
        journals = list(dict.fromkeys(
            (j.strip() for j in re.findall(r"(?m)^期刊：(.+)$", text) if j.strip())))
        summ = f"本期收录 {n_papers} 篇 · " + " · ".join(journals[:3])
        if len(journals) > 3:
            summ += " 等"
        first_img = re.search(r'<p class="wk-img"><img src="([^"]+)"', doc)
        posts.append({
            "slug": slug, "title": title, "date": date, "month": month,
            "doc": doc, "toc": toc, "words": words, "reading": reading,
            "summary": summ, "og_image": first_img.group(1) if first_img else "",
            "plain": plain,
        })
    if not posts:
        return []
    posts.sort(key=lambda p: p["date"], reverse=True)   # 新→旧；i-1 更新（上一篇）

    # 周报搜索数据（/search/ 页客户端检索用）：全文存 plain（截 30000 字符防无限增长）；
    # "<" 转义防正文里出现 </script> 提前截断内嵌 script
    wk_data = [{
        "u": f"weekly/{p['slug']}/",
        "t": p["title"],
        "d": p["date"],
        "s": p["summary"],
        "w": p["words"],
        "x": p["plain"][:30000],
    } for p in posts]
    (SITE / "assets" / "weekly-data.js").write_text(
        "window.MBD_WK = "
        + json.dumps(wk_data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        + ";\n", encoding="utf-8")

    def side_nav(cur: str) -> str:
        rows = "".join(
            f'<a class="wk-side-link" data-active="{str(q["slug"] == cur).lower()}" '
            f'href="../{q["slug"]}/">{esc(q["title"])}</a>' for q in posts)
        return (f'<aside class="wk-side"><div class="wk-side-in"><nav class="wk-side-nav">'
                f'<div class="wk-side-hd">文章导航</div>{rows}</nav></div></aside>')

    def pn_cell(pn: dict | None, to_next: bool) -> str:
        if not pn:
            return '<span class="wk-pn-empty" aria-hidden="true"></span>'
        cls = "wk-pn wk-pn-to-next" if to_next else "wk-pn"
        ico, lbl = ((WK_ICO_RIGHT, "下一篇") if to_next else (WK_ICO_LEFT, "上一篇"))
        return (f'<a class="{cls}" href="../{pn["slug"]}/"><span class="wk-pn-dir">'
                f'{lbl}{ico}'
                f'</span><span class="wk-pn-ttl">{esc(pn["title"])}</span></a>')

    # 文章页：三栏骨架
    for i, p in enumerate(posts):
        # 标签行只保留年月（分类/子类标签不再渲染）
        tags = [f'<span class="wk-tag">{esc(p["month"])}</span>'] if p["month"] else []
        prev_p = posts[i - 1] if i > 0 else None
        next_p = posts[i + 1] if i + 1 < len(posts) else None
        toc_rows = "".join(
            f'<a class="wk-toc-link" data-lv="{lv}" href="#{hid}">{esc(txt)}</a>'
            for lv, hid, txt in p["toc"])
        body = f"""<div class="wk-shell">
{side_nav(p["slug"])}
<main class="wk-main"><article>
<h1 class="wk-deco wk-h1">{esc(p["title"])}</h1>
<div class="wk-tagsrow">{'<span class="dot">·</span>'.join(tags)}</div>
<div class="wk-meta"><span>{WK_ICO_USER}{WK_AUTHOR}</span><span>{WK_ICO_DOC}{p["words"]} 字</span><span>{WK_ICO_CLOCK}约 {p["reading"]} 分钟</span>{rstyle_toggle("plain")}</div>
<div class="wk-dates"><span>创建于 <time datetime="{p["date"]}">{p["date"]}</time></span></div>
<div class="wk-content">{p["doc"]}</div>
<nav class="wk-prevnext" aria-label="文章导航">{pn_cell(prev_p, False)}{pn_cell(next_p, True)}</nav>
</article></main>
<aside class="wk-toc"><div class="wk-toc-in"><h4 class="wk-side-hd">此页内容</h4><nav>{toc_rows}</nav></div></aside>
</div>"""
        d = SITE / "weekly" / p["slug"]
        d.mkdir(parents=True, exist_ok=True)
        html_txt = page_shell(cfg, p["title"], body, depth=2,
                              description=p["summary"], path=f"/weekly/{p['slug']}/",
                              og_type="article", og_image=p["og_image"], bare=True,
                              rstyle="plain",
                              extra_assets=["image-lightbox.js", "article-toc.js"])
        (d / "index.html").write_text(html_txt, encoding="utf-8")

    # 列表页：页头 + QuickNav 月份锚点 + 按月分组条目。
    # 分组键 = 标题识别的年月（与条目 chip、文章页同源），不看 frontmatter
    # date——补录期次的 date 是录入日，按它分会把「260223-0301」错归 3 月
    # （2026-10-04 实证）。组序显式按 YYYYMM 倒序、未知月份垫底：补录场景下
    # date 序不再等价于期号顺序，不能沿用插入序。
    def _month_key(label: str) -> str:
        m = re.match(r"(\d{4})年(\d{1,2})月", label or "")
        return f"{m.group(1)}{int(m.group(2)):02d}" if m else "0000"

    groups: dict[str, dict] = {}
    for p in posts:
        label = p["month"] or "未知月份"
        key = _month_key(p["month"])
        groups.setdefault(key, {"label": label, "items": []})
        groups[key]["items"].append(p)
    ordered = sorted(groups.items(), key=lambda kv: kv[0], reverse=True)
    chips = "".join(
        f'<a class="wk-btn" href="#m-{key}">{esc(g["label"])} <span class="n">{len(g["items"])}</span></a>'
        for key, g in ordered)
    sections = ""
    for key, g in ordered:
        rows = ""
        for p in g["items"]:
            rows += (f'<a class="wk-line" href="{p["slug"]}/"><span class="wk-line-in">'
                     f'<span class="wk-line-main"><h3 class="wk-line-ttl">{esc(p["title"])}</h3>'
                     f'<p class="wk-line-sum">{esc(p["summary"])}</p></span>'
                     f'<span class="wk-line-side"><span class="wk-line-tags">'
                     f'<span class="wk-tag">{esc(p["month"])}</span></span>'
                     f'<span class="wk-line-date">{p["date"]}</span></span></span></a>')
        sections += (f'<section class="wk-group" id="m-{key}">'
                     f'<div class="wk-label"><span>{esc(g["label"])}</span>'
                     f'<span class="wk-label-right">{len(g["items"])} 篇</span></div>{rows}</section>')
    n_week = len(posts)
    body = f"""<div class="wk-col">
<div class="wk-head"><div><p class="wk-kicker">{esc(cfg['title'].upper())}</p><h1 class="wk-deco">每周速递</h1></div></div>
<p class="wk-lede">Nature / Science / Cell 系列大尺度生物多样性研究每周精选，共 {n_week} 期；点击条目阅读本期文献速递全文。</p>
<div class="wk-quicknav">{chips}</div>
<div class="wk-groups">{sections}</div>
</div>"""
    (SITE / "weekly").mkdir(parents=True, exist_ok=True)
    (SITE / "weekly" / "index.html").write_text(
        page_shell(cfg, f"每周速递 · {cfg['title']}", body, depth=1,
                   description=f"Nature / Science / Cell 系列大尺度生物多样性研究每周精选，共 {n_week} 期。",
                   path="/weekly/", bare=True, rstyle="plain"), encoding="utf-8")
    # 过期的周报页目录（期次被删后的残留）→ 改名移入 site_trash/，绝不原地删除
    #（与上面过期详情页同一模式：shutil.rmtree 会触发沙箱批量删除保护，纯改名无此问题）
    trash = ROOT / "site_trash"
    live = {p["slug"] for p in posts}
    for d in (SITE / "weekly").iterdir():
        if d.is_dir() and d.name not in live:
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / (d.name + "-" + str(int(time.time())))
            print(f"· 过期周报页 {d.name} → site_trash/（不删除）")
            try:
                d.rename(dest)
            except OSError:
                pass
    print(f"· 每周速递：列表页 + {n_week} 篇文章页 → weekly/")
    return ["/weekly/"] + [f"/weekly/{p['slug']}/" for p in posts]


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
            if (d.is_dir() and d.name not in ("assets", "search", "weekly", "statistics")
                    and (d / "index.html").exists()):
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

    # 封面图整体随构建拷入站点（源文件在 assets_src/covers/，随 git 入库）。
    # 镜像式同步：管理端删过的封面不残留在构建产物里
    src_covers = ROOT / "assets_src" / "covers"
    if src_covers.is_dir():
        dst_covers = SITE / "assets" / "covers"
        shutil.copytree(src_covers, dst_covers, dirs_exist_ok=True)
        src_names = {f.name for f in src_covers.iterdir() if f.is_file()}
        for f in dst_covers.iterdir():
            if f.is_file() and f.name not in src_names:
                f.unlink()
        n_cov = len([f for f in dst_covers.iterdir() if f.is_file()])
        n_used = len([p for p in items if p.get("cover")])
        print(f"· 封面图 {n_cov} 张（{n_used} 篇文献引用）→ assets/covers/")

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
        "doi": p.get("doi") or "",
        "ab": abstract_disp(p),
        # cv = 封面（可选）：本地路径带 ?v= 防缓存；外链（http 开头）原样直显
        "cv": cover_src(p.get("cover") or ""),
        "se": haystack(p),
    } for p in items]
    (SITE / "assets" / "papers-data.js").write_text(
        "window.MBD_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8")

    # robots.txt + sitemap.xml：配置了 site_url 才生成（部署完整性 / 搜索引擎收录）
    weekly_urls = build_weekly(cfg)          # 每周速递：列表页 + 各文章页
    base = (cfg.get("site_url") or "").rstrip("/")
    if base:
        # AI 爬虫白名单（GEO）：与主站 zbhgis.com robots.ts 同一份清单——
        # 允许产出引用的 AI 引擎抓取，语义同主站"让模型看到、引用我们"
        ai_crawlers = (
            "GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web",
            "Claude-SearchBot", "PerplexityBot", "Perplexity-User", "Google-Extended",
            "CCBot", "Meta-ExternalAgent", "FacebookBot", "Applebot", "Applebot-Extended",
            "Amazonbot", "DuckAssistBot", "Bytespider", "PetalBot",
        )
        robots_lines = (["User-agent: *", "Allow: /", ""]
                        + [f"User-agent: {ua}\nAllow: /" for ua in ai_crawlers]
                        + ["", "Sitemap: " + base + "/sitemap.xml"])
        (SITE / "robots.txt").write_text("\n".join(robots_lines) + "\n", encoding="utf-8")
        today = time.strftime("%Y-%m-%d")
        # lastmod 用每篇文献真实的收录日期（added），而非构建时间——
        # 否则每次重新构建全站都声称"今天改过"，引擎会学会忽略这个字段
        home_lastmod = max((p.get("added") or "" for p in items), default="") or today
        urls = [("/", home_lastmod),
                ("/search/", today), ("/statistics/", today)]
        urls += [("/" + p["id"] + "/", (p.get("added") or "").split("T")[0] or today) for p in items]
        urls += [(u, today) for u in weekly_urls]
        sm = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for u, lm in urls:
            sm.append(f"<url><loc>{base}{u}</loc><lastmod>{lm}</lastmod></url>")
        sm.append("</urlset>")
        (SITE / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")
        # llms.txt（GEO）：给 AI 引擎的自然语言站点地图——定位、栏目、全量文献索引。
        # 与 sitemap 的纯 URL 清单互补：这里回答"是什么 / 有什么 / 去哪看"。
        n_weekly = sum(1 for u in weekly_urls if u != "/weekly/")
        paper_lines = "\n".join(
            f"- [{display_title(p)}]({base}/{p['id']}/): {(p.get('journal') or '—').strip()}"
            f" {p.get('year') or ''}".rstrip()
            + (f" · DOI: {p['doi']}" if p.get("doi") else "")
            for p in items)
        llms = f"""# {cfg['title']}（{cfg['subtitle']}）

> {cfg['lede']} 元数据来自 Crossref / OpenAlex，中文摘要为 AI 辅助翻译（初译+审校）。

- [首页]({base}/): 全部 {len(items)} 篇文献的卡片视图，可按标签/期刊/年份筛选
- [全站搜索]({base}/search/): 标题 / 作者 / DOI / 期刊 / 摘要全文检索
- [每周速递]({base}/weekly/): Nature / Science / Cell 系列大尺度生物多样性研究每周精选（{n_weekly} 期）
- [全站统计]({base}/statistics/): 关键词 / 期刊 / 年份分布

## 文献（{len(items)} 篇）

{paper_lines}
"""
        (SITE / "llms.txt").write_text(llms, encoding="utf-8")
        # 404.html：配合 nginx 的 try_files =404 + error_page 使用——
        # 兜底 /index.html 的旧写法会把所有坏链都变成 200 首页（软 404）
        nf_body = ('<header class="site"><h1 class="home-title">404</h1>'
                   '<p class="lede">页面不存在或已移动。</p>'
                   '<p><a href="/">返回文献库首页</a> · <a href="/search/">全站搜索</a></p></header>')
        (SITE / "404.html").write_text(
            page_shell(cfg, f"页面不存在 · {cfg['title']}", nf_body, path="/404.html",
                       noindex=True),
            encoding="utf-8")
        # IndexNow key 文件：与主站 zbhgis.com 共用同一把 key（IndexNow 允许同一
        # 所有者在多个站点托管同一 key 文件）。ping-search.sh 推送前探测本地址可达。
        key_file = ROOT / "deploy" / "indexnow.key"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
            (SITE / (key + ".txt")).write_text(key, encoding="utf-8")
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
    stats_dir = SITE / "statistics"
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / "index.html").write_text(build_stats_page(cfg, items), encoding="utf-8")
    build_stats_data(items)
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
