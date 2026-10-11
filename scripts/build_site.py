#!/usr/bin/env python3
"""MacroBiodiv 静态站生成器：读 meta/papers.json → 生成 site/ 纯静态站点。

用法：
    python scripts/build_site.py              # 生产构建（数据随 GitHub 仓走）

产出：
    site/index.html                文献库首页（卡片网格 + 分页 + 搜索 + 多维筛选 + 排序）
    site/{id}/index.html           每篇文献详情页（完整信息 + BibTeX + 上/下一篇）
    site/search/index.html         全站搜索独立页（结果行带命中高亮，任意终端可用）
    site/assets/style.css          样式（源 assets_src/style.css，构建时原样写出）
    site/assets/papers.js          分页/筛选/排序 + 统计打点 + BibTeX 复制
                                   （源 assets_src/papers.js，头部构建期注入 window.MBD 配置）
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
    """期刊徽章缩写：优先 journal_short（Crossref short-container-title）。
    各出版社入库的点号风格不一（Nat. Clim. Chang. vs Nat Commun），显示层统一
    去点（NLM 风格，与全站多数一致）；数据层已做一次迁移，这里兜底新入库条目。"""
    s = (p.get("journal_short") or p.get("journal") or "").strip()
    return s.replace(".", "") if s else ""


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


# ── 前端源码外置于 assets_src/（与 GeoSciPlot 同构）：改样式/脚本直接编辑
#    assets_src/style.css 与 assets_src/papers.js，改完重跑本脚本即可（?v= 防缓存）。
#    papers.js 构建时会在头部注入 window.MBD 配置（见 main），源文件本身不含配置
ASSETS_SRC = ROOT / "assets_src"
CSS = (ASSETS_SRC / "style.css").read_text(encoding="utf-8")
JS = (ASSETS_SRC / "papers.js").read_text(encoding="utf-8")


def page_shell(cfg: dict, title: str, body: str, depth: int = 0, gh_url: str = "",
               description: str = "", path: str = "", og_type: str = "website",
               og_image: str = "", bare: bool = False, rstyle: str = "",
               extra_assets: list[str] | None = None, noindex: bool = False,
               keywords: str = "", og_article: dict | None = None,
               og_image_alt: str = "") -> str:
    up = "../" * depth          # depth=2（weekly 文章页）需要 ../../，此前按布尔少了一级
    gh = gh_url or "https://github.com/{}/{}".format(
        cfg.get("owner") or "OWNER", cfg["repo"])
    desc = description or f"{cfg['subtitle']} —— {cfg['lede']}"
    # Open Graph：配置了 site_url 才输出，链接分享（微信/Telegram/X）出卡片；
    # 有封面图时附 og:image（分享卡片带封面，og:image 需绝对 URL）
    base = (cfg.get("site_url") or "").rstrip("/")
    og = ""
    twitter = ""
    canonical = ""
    if base:
        og_rows = [
            f'<meta property="og:title" content="{esc(title)}">',
            f'<meta property="og:description" content="{esc(desc[:200])}">',
            f'<meta property="og:type" content="{esc(og_type)}">',
            f'<meta property="og:url" content="{esc(base + path)}">',
            f'<meta property="og:site_name" content="{esc(cfg["title"])}">',
            '<meta property="og:locale" content="zh_CN">',
        ]
        if og_image:
            og_rows.append(f'<meta property="og:image" content="{esc(og_image)}">')
            og_rows.append(f'<meta property="og:image:alt" content="{esc(og_image_alt or title)}">')
        # article:* 是 og:type=article 的配套语义（分享卡片与 AI 引擎都读发布时间/栏目/标签）；
        # 值由调用方按页面给出，不在这里猜
        if og_type == "article" and og_article:
            if og_article.get("published"):
                og_rows.append('<meta property="article:published_time" content="'
                               f'{esc(og_article["published"])}">')
            if og_article.get("section"):
                og_rows.append(f'<meta property="article:section" content="{esc(og_article["section"])}">')
            for _t in (og_article.get("tags") or [])[:8]:
                og_rows.append(f'<meta property="article:tag" content="{esc(_t)}">')
        og = "\n".join(og_rows)
        # Twitter/X 卡片：全部从上面的 OG 值派生，不引入第二套数据（无图退摘要卡）
        tw_card = "summary_large_image" if og_image else "summary"
        tw_rows = [f'<meta name="twitter:card" content="{tw_card}">',
                   f'<meta name="twitter:title" content="{esc(title)}">',
                   f'<meta name="twitter:description" content="{esc(desc[:200])}">']
        if og_image:
            tw_rows.append(f'<meta name="twitter:image" content="{esc(og_image)}">')
        twitter = "\n".join(tw_rows)
        # canonical 与 og:url 同值：多入口/带参数访问时搜索引擎只认这一份
        canonical = f'<link rel="canonical" href="{esc(base + path)}">'
    # theme-color 与 :root token 同值（深 #0d1117 / 浅 #fff，见样式表 :root 两行），
    # 移动端浏览器地址条配色跟随主题；apple-touch-icon 复用 favicon.png；
    # atom.xml 早就在生成但页面从不引用 —— 补自动发现链接才算真的可订阅
    feed_href = (base + "/atom.xml") if base else f"{up}atom.xml"
    head_extra = "\n".join([
        '<meta name="theme-color" content="#0d1117" media="(prefers-color-scheme: dark)">',
        '<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">',
        f'<link rel="apple-touch-icon" href="{up}assets/favicon.png">',
        f'<link rel="alternate" type="application/atom+xml" '
        f'title="{esc(cfg["title"])} · {esc(cfg["subtitle"])}" href="{esc(feed_href)}">',
    ] + ([f'<meta name="keywords" content="{esc(keywords)}">'] if keywords else []))
    # ── 顶部菜单栏：全部页面共用，样式对齐主站 zbhgis.com（v3-nav 同源）。
    #    左站点名 / 右导航组：每周速递（/weekly/）· 全站统计（/statistics/，按 path 标 active）
    #    · 更多▾ 下拉（hover / focus-visible 展开，纯 CSS；zbhgis 与 GeoSciPlot 外链）。
    #    菜单项与下拉项均带主站同款 14/15px stroke 图标；≤640px 转 details 汉堡 ──
    is_weekly = path.startswith("/weekly")
    is_stats = path.startswith("/statistics")
    is_about = path.startswith("/about")
    cur_w = ' aria-current="page"' if is_weekly else ''
    cur_s = ' aria-current="page"' if is_stats else ''
    cur_a = ' aria-current="page"' if is_about else ''
    ico_send = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/></svg>')
    ico_chart = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 20V10M12 20V4M6 20v-4"/></svg>')
    ico_info = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>')
    ico_globe = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>')
    # GeoSciPlot 图标与主站「更多」菜单同款（图片样式：方框 + 圆点 + 山形）
    ico_geosci = ('<svg class="mnav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>')
    nav = f"""<header class="mnav">
<nav class="mnav-in">
<a class="mnav-brand" href="/"><img src="{up}assets/favicon.png?v={BUILD_VER}" alt="" width="26" height="26">{esc(cfg['title'])}</a>
<ul class="mnav-links">
<li><a class="mnav-link" data-active="{str(is_weekly).lower()}" href="/weekly/"{cur_w}>{ico_send}每周速递</a></li>
<li><a class="mnav-link" data-active="{str(is_stats).lower()}" href="/statistics/"{cur_s}>{ico_chart}全站统计</a></li>
<li><a class="mnav-link" data-active="{str(is_about).lower()}" href="/about/"{cur_a}>{ico_info}关于本站</a></li>
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
<li><a href="/statistics/"{cur_s}>{ico_chart}全站统计</a></li>
<li><a href="/about/"{cur_a}>{ico_info}关于本站</a></li>
<li class="mnav-dd-sep"></li>
<li><a href="https://zbhgis.com" rel="noopener" target="_blank">{ico_globe}zbhgis</a></li>
<li><a href="https://geosciplot.zbhgis.com" rel="noopener" target="_blank">{ico_geosci}GeoSciPlot</a></li>
</ul>
</details>
</nav>
</header>"""
    # bare=True（每周速递等全宽页面）：正文不进 .wrap（自带容器），仅 footer 包一层
    fab = f"""<div class="fab"><a class="tbtn" href="{up}search/" title="全站搜索" aria-label="全站搜索"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 14 14"/></svg></a><a class="tbtn" href="/" title="返回 Home（文献库首页）" aria-label="返回 Home"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8 8 3l5.5 5M4 7v6h8V7"/></svg></a><a class="tbtn" href="{gh}" rel="noopener" target="_blank" title="在 GitHub 查看仓库（数据与索引）"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></a><button type="button" class="tbtn tbtn-fs" id="fsBtn" title="字号：标准">A</button><button type="button" class="tbtn" id="themeBtn" title="切换明暗主题" aria-label="切换明暗主题"><svg class="ic-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="3.6"/><path d="M12 2.8v2.2m0 14v2.2M21.2 12H19M5 12H2.8m14.5 6.2-1.6-1.6M8.3 8.3 6.7 6.7m10.8 0-1.6 1.6M8.3 15.7l-1.6 1.6"/></svg><svg class="ic-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.354 15.354A9 9 0 0 1 8.646 3.646 9.003 9.003 0 0 0 12 21a9.003 9.003 0 0 0 8.354-5.646z"/></svg></button><button type="button" class="tbtn" id="topBtn" title="回到顶部" aria-label="回到顶部"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13.5v-9M4.5 8 8 4.5 11.5 8"/></svg></button></div>"""
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
{twitter}
{head_extra}
<link rel="stylesheet" href="{up}assets/style.css?v={BUILD_VER}">
<link rel="icon" type="image/png" href="{up}assets/favicon.png?v={BUILD_VER}">
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
  /* 连 c-covbox 一起换掉：占位块就位的同时把「加载中」层撤掉 */
  (img.closest(".c-covbox") || img).replaceWith(ph);
}};
window.__imgFallback = function (img) {{
  /* 缩略外链失败 → 先回退原图外链（data-full），原图走下面的多源降级链 */
  if (img.dataset.full && !img.dataset.fullTried) {{
    img.dataset.fullTried = "1";
    img.src = img.dataset.full;
    return;
  }}
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
    ctb = (p.get("cover_thumb") or "").strip()
    ctw, cth = p.get("cover_thumb_w") or 0, p.get("cover_thumb_h") or 0
    dims = f' width="{ctw}" height="{cth}"' if ctw and cth else ""
    # alt 带题名（此前全是「封面图」——图片检索与无障碍都拿不到信息，截 40 字防超长中文标题）
    alt_cov = esc((display_title(p) or p.get("title") or "封面图")[:40])
    if ctb:
        # 缩略外链优先（图床 webp，30-60KB）；width/height 预留位 + 「加载中」占位层；
        # data-full 供加载失败时回退原图外链
        cov_html = (f'\n  <span class="c-covbox"><img class="c-cov" src="{esc(ctb)}"'
                    f' data-full="{cover_src(cov)}"{dims} alt="《{alt_cov}》封面图" loading="lazy"></span>')
    elif cov:
        cov_html = (f'\n  <span class="c-covbox"><img class="c-cov" src="{cover_src(cov)}"'
                    f' alt="《{alt_cov}》封面图" loading="lazy"></span>')
    else:
        cov_html = f'\n  <span class="c-ph">{esc(jb)}</span>'
    return f"""<a class="card" href="{esc(p['id'])}/" title="{tip}">{cov_html}
  <span class="c-cap"><span class="c-meta">{ty_html}<span class="c-j" title="{esc(p.get('journal') or '')}">{esc(jb)}</span></span>
  <span class="c-t">{esc(display_title(p))}</span></span>
</a>"""


# 筛选控件首帧预置：静态 HTML 把全部控件写成默认态（新到旧按下、chips 全选、
# 输入框留空、面板展开），papers.js 加载后才按「URL 参数 > localStorage 偏好」纠正
# ——慢网下能看到一瞬间默认态再跳变（?sort=rand 分享链接、收起过筛选面板等场景实测）。
# STATE_PRESTATE 内联脚本紧跟 sortseg 输出、解析期同步执行（先于卡片网格绘制），
# 按与 papers.js 相同的优先级预置各控件状态消除闪烁；不写 localStorage，
# 「URL 排序仅本次生效、不改写对方偏好」的语义保持不变
STATE_PRESTATE = """<script>(function(){try{
var P=new URLSearchParams(location.search);
var col=false;try{col=localStorage.getItem("mbd-filters")==="hidden"}catch(e){}
if(col){var fb=document.getElementById("filters");if(fb)fb.setAttribute("hidden","");
var fbB=document.getElementById("filterBtn");if(fbB){fbB.textContent="筛选 ▾";
fbB.setAttribute("aria-expanded","false")}}
var q=P.get("q");if(q){var qi=document.getElementById("q");if(qi)qi.value=q}
["from","to"].forEach(function(k){var v=P.get(k);if(v){
var el=document.getElementById("f-"+k);if(el)el.value=v}});
["tag","journal"].forEach(function(k){var t=P.get(k);if(t){var names=t.split("|");
document.querySelectorAll('#filters .chips[data-key="'+k+'"] button.chip[data-v]')
.forEach(function(b){b.setAttribute("aria-pressed",
String(names.indexOf(b.getAttribute("data-v"))>-1))})}});
var v=P.get("sort"),ok=["pub","pub_asc","rand"];
if(ok.indexOf(v)<0){v=null;try{v=localStorage.getItem("mbd-sort2")}catch(e){}
if(ok.indexOf(v)<0)v=null}
if(v)document.querySelectorAll("#sortseg button").forEach(function(b){
b.setAttribute("aria-pressed",String(b.getAttribute("data-sort")===v))})
}catch(e){}})();</script>"""


def build_index(cfg: dict, items: list) -> str:
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
  + '</span>' + STATE_PRESTATE)}
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
            # SearchAction：站点有 /search/ 全文检索，声明后引擎可把站内检索挂进结果
            "potentialAction": {
                "@type": "SearchAction",
                "target": {"@type": "EntryPoint", "urlTemplate": base + "/search/?q={search_term_string}"},
                "query-input": "required name=search_term_string",
            },
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
    fig_alt = esc(f"《{display_title(p)[:40]}》文章图表 / 图形摘要")
    fig_sec = (f'<section class="sec">{sech("图表")}'
               f'<figure class="p-cover"><img src="{cover_src(cov, "../")}" '
               f'alt="{fig_alt}"></figure></section>') if cov else ""

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
    # ── SEO/GEO：站点版式与卡片一律中文优先，title / description 此前漏跟上（用的还是
    #    Crossref 英文原题，中文查询在权重最高的字段上零命中）——这里统一改中文题名优先。
    #    英文原题仍在 H1 副行、JSON-LD name 与正文里出现，英文检索由页面正文命中 ──
    jnl = (p.get("journal") or "").strip()
    jshort = (p.get("journal_short") or "").strip() or jnl
    zh_abs = (p.get("abstract_zh") or "").strip()
    en_abs = (p.get("abstract") or "").strip()
    seo_title = f"{display_title(p)} · {jshort}" if jshort else f"{display_title(p)} · {cfg['title']}"
    desc = f"{display_title(p)}｜{jshort} {p.get('year') or ''}".strip()
    lead = zh_abs or en_abs
    if lead:
        # 摘要节选优先落在句末（约 110 字内）——[:120] 硬切会把断在半句的尾巴发给引擎；
        # 首句本身就超长时明确加「…」，不假装是完整句。
        # 不带 DOI 尾巴：整条 description 要控制在 og:description 的 200 字裁剪线内
        cut = lead[:110]
        i = max(cut.rfind("。"), cut.rfind(". "))
        if i >= 50:
            cut = cut[:i + (1 if cut[i] == "。" else 2)]
        else:
            cut = cut.rstrip("，、；: ;") + "…"
        desc += "：" + cut.strip()
    kw_all = [str(x) for x in ((p.get("keywords") or []) + (p.get("tags") or [])) if str(x).strip()]
    # 学术结构化数据（Schema.org ScholarlyArticle），利于搜索引擎与 AI 引擎理解文献信息：
    # url 指本页 canonical（引擎据此把文献实体锚定到本站），DOI 走 identifier + sameAs；
    # description 必须给摘要 —— 这是答案引擎最常整段引用的字段；卷/页/ISSN/体裁一并进实体。
    base = (cfg.get("site_url") or "").rstrip("/")
    if cov.startswith(("http://", "https://")):
        og_image = cov                      # 外链封面本身就是绝对 URL
    else:
        og_image = (f"{base}/assets/{cov}" if (base and cov) else "")
    ld = {
        "@context": "https://schema.org", "@type": "ScholarlyArticle",
        "name": p.get("title") or "",
        "headline": p.get("title") or "",          # 与 name 同值，兼容只读 headline 的引擎
        "alternateName": (p.get("title_zh") or "").strip() or None,
        "inLanguage": "en",                 # 描述的是原论文语言（页面本身 lang=zh-CN）
        "description": zh_abs or en_abs or None,
        "author": [{"@type": "Person", "name": a} for a in (p.get("authors") or [])],
        "datePublished": p.get("published") or p.get("year") or None,
        "isPartOf": ({"@type": "Periodical", "name": jnl,
                      "issn": (p.get("issn") or "").strip() or None} if jnl else None),
        "publisher": ({"@type": "Organization", "name": p["publisher"]} if p.get("publisher") else None),
        "identifier": ({"@type": "PropertyValue", "propertyID": "doi",
                        "value": p["doi"]} if p.get("doi") else None),
        "sameAs": (f"https://doi.org/{p['doi']}" if p.get("doi") else None),
        "url": (base + f"/{p['id']}/") if base else None,
        "image": og_image or None,
        "keywords": ", ".join(kw_all) or None,
        "genre": (p.get("article_type") or "").strip() or None,
        "volume": (p.get("volume") or "").strip() or None,
        "pagination": (p.get("pages") or "").strip() or None,
    }
    ld = {k: ({kk: vv for kk, vv in v.items() if vv} if isinstance(v, dict) else v)
          for k, v in ld.items() if v}
    # "</script>" 会出现于标题/摘要时会把内嵌 script 提前截断：把 "<" 转成 Unicode 转义
    ld_html = (json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c") if base else "")
    ld_block = ('<script type="application/ld+json">' + ld_html + "</script>") if ld_html else ""
    body = body + ld_block
    return page_shell(cfg, seo_title, body, depth=1,
                      gh_url=f"https://github.com/{cfg.get('owner') or 'OWNER'}/{cfg['repo']}",
                      description=desc, path=f"/{p['id']}/", og_type="article",
                      og_image=og_image, rstyle="accent",
                      keywords=", ".join(kw_all[:10]),
                      og_article={"published": p.get("published") or "", "section": jnl,
                                  "tags": kw_all[:8]},
                      og_image_alt=f"{display_title(p)}（{jshort}）封面 / 图形摘要",
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
  <span class="st-fgroup"><span class="flabel">期刊</span>
    <select id="st-journal" aria-label="按期刊筛选"><option value="*">全部</option></select></span>
  <span class="st-fgroup"><span class="flabel">类型</span>
    <select id="st-type" aria-label="按类型筛选"><option value="*">全部</option></select></span>
  <span class="st-fgroup"><span class="flabel">时间</span>
    <input type="date" id="st-from" class="dateinp" aria-label="online 发表日期 起"
           title="按 online 发表日期筛选（仅精确到月的文献按整月重叠匹配）">
    <span class="st-dash" aria-hidden="true">–</span>
    <input type="date" id="st-to" class="dateinp" aria-label="online 发表日期 止"></span>
  <button id="st-reset" class="reset" type="button">重置</button>
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


def build_about(cfg: dict) -> str:
    """关于本站页（/about/）：内容源 content/about.md（render_md 渲染），
    窄栏 prose 版式复用周报正文 .wk-content 排版；缺文件时只出标题不报错。"""
    src = ROOT / "content" / "about.md"
    doc = ""
    if src.exists():
        try:
            from render_md import parse_md
            _, doc, _, _ = parse_md(src.read_text(encoding="utf-8"))
        except ImportError:
            print("· 关于本站：缺 scripts/render_md.py，正文跳过")
    # 正文首个 `# ` 标题与页面大标题重复：剥离（与 build_weekly 同款处理）
    doc = re.sub(r'<h2 id="doc-1" class="md-h1">.*?</h2>\n*', "", doc, count=1)
    body = f"""<header class="site">
  <p class="kicker">{esc(cfg['title'].upper())} · ABOUT</p>
  <h1 class="spage-title">关于本站</h1>
</header>
<div class="about-wrap">
<article class="wk-content">{doc}</article>
</div>"""
    return page_shell(cfg, f"关于本站 · {cfg['title']}", body, depth=1, path="/about/")


def build_weekly(cfg: dict) -> tuple[list[str], list[dict]]:
    """构建 /weekly/ 列表页与各文章页；返回（供 sitemap 的）URL 路径列表与期次索引。"""
    if not WEEKLY_SRC.is_dir():
        return [], []
    try:
        from render_md import parse_md
    except ImportError:
        print("· 每周速递：缺 scripts/render_md.py，跳过")
        return [], []

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
        # 展示日期与年月标签都自动识别自标题里的期号区间（形如 260330-0405，
        # YYMMDD-MMDD、同年省略年份）：区间起始日 = 该期收录周的周一，月份与日期
        # 天然一致，跨月的期（260330-0405）不会再出现「3月胶囊配 4月日期」。
        # frontmatter date 是补录日期，只作标题无区间时的回退，不再参与展示
        m_rng = re.search(
            r"(?<!\d)(\d{2})(0[1-9]|1[0-2])(\d{2})-(0[1-9]|1[0-2])(\d{2})(?!\d)", title)
        if m_rng:
            date = f"20{m_rng.group(1)}-{m_rng.group(2)}-{m_rng.group(3)}"
            month = f"20{m_rng.group(1)}年{int(m_rng.group(2))}月"
            period = f"{date} ~ {m_rng.group(4)}-{m_rng.group(5)}"
        else:
            month = _month_label(date)
            period = date
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
            "period": period,
            "doc": doc, "toc": toc, "words": words, "reading": reading,
            "summary": summ, "og_image": first_img.group(1) if first_img else "",
            "plain": plain,
        })
    if not posts:
        return [], []
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
<div class="wk-dates"><span>{'收录周期' if p["period"] != p["date"] else '创建于'} <time datetime="{p["date"]}">{p["period"]}</time></span></div>
<div class="wk-content">{p["doc"]}</div>
<nav class="wk-prevnext" aria-label="文章导航">{pn_cell(prev_p, False)}{pn_cell(next_p, True)}</nav>
</article></main>
<aside class="wk-toc"><div class="wk-toc-in"><h4 class="wk-side-hd">此页内容</h4><nav>{toc_rows}</nav></div></aside>
</div>"""
        d = SITE / "weekly" / p["slug"]
        d.mkdir(parents=True, exist_ok=True)
        # 周报文章页此前完全没有结构化数据，title 也不带站点名（长标题在结果里被截断
        # 后认不出是谁家的）—— 补 BlogPosting + 站点名后缀 + article:* 语义
        wbase = (cfg.get("site_url") or "").rstrip("/")
        ld_w = {
            "@context": "https://schema.org", "@type": "BlogPosting",
            "headline": p["title"],
            "description": (p["summary"] or "").strip() or None,
            "datePublished": p["date"] or None,
            "inLanguage": "zh-CN",
            "url": (wbase + f"/weekly/{p['slug']}/") if wbase else None,
            "author": {"@type": "Organization", "name": cfg["title"], "url": wbase or None},
            "publisher": {"@type": "Organization", "name": cfg["title"], "url": wbase or None},
            "image": p["og_image"] or None,
            "articleSection": "每周速递",
            "keywords": "宏观生物多样性, 每周速递, Nature, Science, Cell 系列",
        }
        ld_w = {k: ({kk: vv for kk, vv in v.items() if vv} if isinstance(v, dict) else v)
                for k, v in ld_w.items() if v}
        ld_w_html = (json.dumps(ld_w, ensure_ascii=False).replace("<", "\\u003c") if wbase else "")
        ld_w_block = ('<script type="application/ld+json">' + ld_w_html + "</script>") if ld_w_html else ""
        html_txt = page_shell(cfg, f"{p['title']} · {cfg['title']}", body + ld_w_block, depth=2,
                              description=p["summary"], path=f"/weekly/{p['slug']}/",
                              og_type="article", og_image=p["og_image"], bare=True,
                              rstyle="plain",
                              keywords="宏观生物多样性, 每周速递, Nature, Science, Cell 系列精选",
                              og_article={"published": p["date"] or "", "section": "每周速递",
                                          "tags": ["宏观生物多样性", "Nature", "Science", "Cell"]},
                              og_image_alt=f"{p['title']} 图表",
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
    # 翻页：月份分组按「每页最多 3 个月」切片（2026-10-10，期次渐多后单页过长）。
    # 静态预渲染全部分组与页码（首屏无闪烁、爬虫可见全部）：非当前页分组置 hidden，
    # JS 只做切换；无 JS 时 noscript 展开全部分组（渐进兜底，行为同旧版长页）
    PER_PAGE_MONTHS = 3
    pages = [ordered[i:i + PER_PAGE_MONTHS] for i in range(0, len(ordered), PER_PAGE_MONTHS)]
    n_pages = max(1, len(pages))
    page_of = {key: n for n, pg in enumerate(pages, 1) for key, _ in pg}
    chips = "".join(
        f'<a class="wk-btn" href="#m-{key}" data-page="{page_of[key]}">{esc(g["label"])} <span class="n">{len(g["items"])}</span></a>'
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
        pg = page_of[key]
        sections += (f'<section class="wk-group" id="m-{key}" data-page="{pg}"'
                     f'{"" if pg == 1 else " hidden"}>'
                     f'<div class="wk-label"><span>{esc(g["label"])}</span>'
                     f'<span class="wk-label-right">{len(g["items"])} 篇</span></div>{rows}</section>')
    if n_pages > 1:
        # 静态只渲染第 1 页的页码窗口（首尾 + 当前 ±2 + 「…」省略，与首页 paintNums
        # 同规则）；切页后 papers.js 的 paintWkNums 按当前页重建。全罗列会随月份
        # 增长线性变长（每页 3 个月）。无 JS 时 noscript 展开全部分组、隐藏翻页条
        # （渐进兜底，行为同旧版长页），窗口化不影响该路径
        nums = [n for n in range(1, n_pages + 1) if n == 1 or n == n_pages or n - 1 <= 2]
        parts, last_n = [], 0
        for n in nums:
            if last_n and n - last_n > 1:
                parts.append('<span class="pggap">…</span>')
            parts.append(f'<button type="button" class="wk-btn wk-pgbtn{" on" if n == 1 else ""}"'
                         f' data-p="{n}" aria-current="{str(n == 1).lower()}">{n}</button>')
            last_n = n
        pgbtns = "".join(parts)
        pager = (f'<nav class="wk-pgbar" id="wk-pgbar" aria-label="每周速递翻页">'
                 f'<button type="button" class="wk-btn wk-pgbtn" id="wk-pg-prev" disabled>‹ 上一页</button>'
                 f'<span id="wk-pgnums">{pgbtns}</span>'
                 f'<button type="button" class="wk-btn wk-pgbtn" id="wk-pg-next">下一页 ›</button>'
                 f'</nav><noscript><style>.wk-group[hidden]{{display:block}}'
                 f'.wk-pgbar{{display:none}}</style></noscript>')
    else:
        pager = ""
    n_week = len(posts)
    body = f"""<div class="wk-col">
<div class="wk-head"><div><p class="wk-kicker">{esc(cfg['title'].upper())}</p><h1 class="wk-deco">每周速递</h1></div></div>
<p class="wk-lede">Nature / Science / Cell 系列大尺度生物多样性研究每周精选，共 {n_week} 期；点击条目阅读本期文献速递全文。</p>
<div class="wk-quicknav">{chips}</div>
<div class="wk-groups">{sections}</div>
{pager}
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
    # 第二返回值给 llms.txt / llms-full.txt 用：期次索引（slug / 标题 / 日期 / 摘要 /
    # 纯文本正文截 4000 字）。周报是本站独有的中文解读内容，只给引擎一条 /weekly/
    # 链接等于没给——它需要的正是这批可读文本
    wk_index = [{"slug": p["slug"], "title": p["title"], "date": p["date"],
                 "summary": p["summary"], "plain": p["plain"][:4000]} for p in posts]
    return ["/weekly/"] + [f"/weekly/{p['slug']}/" for p in posts], wk_index


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
        # 本轮仍要生成的详情页（SITE / p["id"]）必须留在原地——白名单只排除了
        # 固定栏目，漏了这条 live 比对会把全部详情页当"过期"搬进 site_trash
        # 再原样重建，site_trash 每次构建近似翻倍（实测曾堆到 1.5 万目录/348MB）。
        # 与下方周报清理的 live 比对同一写法；空库时 live 为空集，详情页全部照搬。
        live = {p["id"] for p in items}
        for d in SITE.iterdir():
            if (d.is_dir() and d.name not in ("assets", "search", "weekly", "statistics", "about")
                    and d.name not in live
                    and (d / "index.html").exists()):
                trash.mkdir(parents=True, exist_ok=True)
                dest = trash / (d.name + "-" + str(int(time.time())))
                print(f"· 过期详情页 {d.name} → site_trash/（不删除）")
                try:
                    d.rename(dest)
                except OSError:
                    pass

    (SITE / "assets").mkdir(parents=True, exist_ok=True)
    # GENERATED 集合内的文件由后面的专门步骤处理（style.css 原样写出、papers.js
    # 要注入 window.MBD 配置、*-data.js 为构建期生成）——原样拷贝会覆盖掉成品
    GENERATED = {"style.css", "papers.js", "papers-data.js", "weekly-data.js", "stats-data.js"}
    for f in ASSETS_SRC.glob("*"):
        if f.is_file() and f.name not in GENERATED:
            shutil.copyfile(f, SITE / "assets" / f.name)

    # 封面图整体随构建拷入站点（源文件在 assets_src/covers/，随 git 入库）。
    # 镜像式同步：管理端删过的封面不残留在构建产物里
    src_covers = ASSETS_SRC / "covers"
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

    # newline="" 保持 \n 原样落盘：write_text 默认在 Windows 上把行尾翻成 CRLF，
    # 产物就会偏离 assets_src 源文件的「原样写出」口径
    (SITE / "assets" / "style.css").write_text(CSS, encoding="utf-8", newline="")
    inline = {
        "tracker": cfg.get("tracker", ""),
        "api": cfg.get("api", ""),
    }
    (SITE / "assets" / "papers.js").write_text(
        "window.MBD = " + json.dumps(inline, ensure_ascii=False) + ";\n" + JS,
        encoding="utf-8", newline="")

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
        # ct = 缩略外链（可选，图床 webp）：卡片优先用，加载失败回退 cv（data-full）
        "cv": cover_src(p.get("cover") or ""),
        "ct": (p.get("cover_thumb") or "").strip(),
        "ctw": p.get("cover_thumb_w") or 0,
        "cth": p.get("cover_thumb_h") or 0,
        "se": haystack(p),
    } for p in items]
    (SITE / "assets" / "papers-data.js").write_text(
        # < 转义成 \u003c：与 stats-data.js / weekly-data.js 同一口径——数据若改为
        # 内联 <script>（首屏直嵌），原始 "</script>" 或标签文本会截断脚本
        "window.MBD_DATA = "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        + ";\n",
        encoding="utf-8")

    # robots.txt + sitemap.xml：配置了 site_url 才生成（部署完整性 / 搜索引擎收录）
    weekly_urls, weekly_index = build_weekly(cfg)   # 每周速递：列表页 + 各文章页（+ 期次索引）
    base = (cfg.get("site_url") or "").rstrip("/")
    if base:
        # AI 爬虫白名单（GEO）：与主站 zbhgis.com robots.ts 同一份清单——
        # 允许产出引用的 AI 引擎抓取，语义同主站"让模型看到、引用我们"。
        # 另补中文搜索引擎与其 AI 抓取端（本站受众在中文侧，B 家引擎不吃默认 * 的兼容匹配）
        ai_crawlers = (
            "GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web",
            "Claude-SearchBot", "PerplexityBot", "Perplexity-User", "Google-Extended",
            "CCBot", "Meta-ExternalAgent", "FacebookBot", "Applebot", "Applebot-Extended",
            "Amazonbot", "DuckAssistBot", "Bytespider", "PetalBot",
            "Omgilibot", "YouBot", "Diffbot", "Bytedance",
        )
        # 中文搜索引擎（本站受众在中文侧）；Sogou 的 UA 名含空格，不符合 RFC 9309 的
        # UA-name 字符集（写了会被忽略），故不列——通配 * 已允许它抓取
        cn_engines = ("Baiduspider", "360Spider", "YisouSpider")
        robots_lines = (["User-agent: *", "Allow: /", ""]
                        + [f"User-agent: {ua}\nAllow: /" for ua in ai_crawlers + cn_engines]
                        + ["", "Sitemap: " + base + "/sitemap.xml"])
        (SITE / "robots.txt").write_text("\n".join(robots_lines) + "\n", encoding="utf-8")
        today = time.strftime("%Y-%m-%d")
        # lastmod 用每篇文献真实的收录日期（added），而非构建时间——
        # 否则每次重新构建全站都声称"今天改过"，引擎会学会忽略这个字段
        home_lastmod = max((p.get("added") or "" for p in items), default="") or today
        # (路径, lastmod, changefreq, priority)：文献详情页是主要落地页（0.8），
        # 首页聚合全部（1.0）；周报文章页一经发布不再变动 → yearly；
        # /search/ 与 /statistics/ 是交互工具页，权重给低但不排除（保持既有收录策略）
        urls = [("/", home_lastmod, "weekly", "1.0"),
                ("/search/", today, "monthly", "0.4"),
                ("/statistics/", today, "monthly", "0.5"),
                ("/about/", today, "yearly", "0.3")]
        urls += [("/" + p["id"] + "/", (p.get("added") or "").split("T")[0] or today,
                  "monthly", "0.8") for p in items]
        urls += [(u, today, "weekly" if u == "/weekly/" else "yearly",
                  "0.8" if u == "/weekly/" else "0.7") for u in weekly_urls]
        sm = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for u, lm, cf, pr in urls:
            sm.append(f"<url><loc>{base}{u}</loc><lastmod>{lm}</lastmod>"
                      f"<changefreq>{cf}</changefreq><priority>{pr}</priority></url>")
        sm.append("</urlset>")
        (SITE / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")
        # llms.txt / llms-full.txt（GEO）：分两层，与 llms.txt 规范一致 ——
        # llms.txt 是**索引**（栏目 / 主题 / 全量题录 / 周报期次，保持小巧）；
        # llms-full.txt 是**全文**（每篇带中英文摘要 + 周报正文），答案引擎要整段引用时拉它。
        n_weekly = sum(1 for u in weekly_urls if u != "/weekly/")

        def md_safe(s) -> str:
            """纯文本文件里不做 HTML 转义，但要防方括号破坏 markdown 链接、防换行折断条目。"""
            return re.sub(r"\s+", " ", str(s or "")).replace("[", "［").replace("]", "］").strip()

        tag_ct = Counter(str(t).strip() for p in items for t in (p.get("tags") or []) if str(t).strip())
        top_tags = " · ".join(f"[{md_safe(t)}]({base}/?tag={quote(t, safe='')}): {n} 篇"
                              for t, n in tag_ct.most_common(12)) or "（暂未打标签）"
        paper_lines = "\n".join(
            f"- [{display_title(p)}]({base}/{p['id']}/): {(p.get('journal') or '—').strip()}"
            f" {p.get('year') or ''}".rstrip()
            + (f" · DOI: {p['doi']}" if p.get("doi") else "")
            for p in items)
        weekly_lines = "\n".join(
            f"- [{md_safe(w['title'])}]({base}/weekly/{w['slug']}/): {w['date']}"
            + (f" · {md_safe(w['summary'])[:60]}" if w["summary"] else "")
            for w in weekly_index)
        llms = f"""# {cfg['title']}（{cfg['subtitle']}）

> {cfg['lede']} 元数据来自 Crossref / OpenAlex，中文摘要为 AI 辅助翻译（初译+审校）。
> 共 {len(items)} 篇文献、{n_weekly} 期每周速递；最近更新 {home_lastmod}。
> 需要摘要全文（中英文）用于回答或引用时，读 [{base}/llms-full.txt]({base}/llms-full.txt)。

## 栏目

- [首页]({base}/): 全部 {len(items)} 篇文献的卡片视图，可按标签/期刊/年份筛选
- [全站搜索]({base}/search/): 标题 / 作者 / DOI / 期刊 / 摘要全文检索
- [每周速递]({base}/weekly/): Nature / Science / Cell 系列大尺度生物多样性研究每周精选（{n_weekly} 期）
- [全站统计]({base}/statistics/): 关键词 / 期刊 / 年份分布
- [关于本站]({base}/about/): 站点定位、数据来源与说明
- [Atom 订阅]({base}/atom.xml): 最新收录 20 篇

## 主题标签（按篇数）

{top_tags}

## 文献（{len(items)} 篇）

{paper_lines}

## 每周速递（{n_weekly} 期，新→旧）

{weekly_lines}
"""
        (SITE / "llms.txt").write_text(llms, encoding="utf-8")
        # 全文文件：每篇一段，字段顺序固定（题名 / 出处 / DOI / 作者 / 关键词 / 摘要），
        # 摘要不设截断——这一层的意义就是让引擎拿到可引用的原文
        blocks: list[str] = []
        for p in items:
            zh_t = display_title(p)
            head = [f"### {md_safe(zh_t)}", f"链接: {base}/{p['id']}/"]
            if (p.get("title") or "").strip() and p["title"].strip() != zh_t.strip():
                head.append(f"英文题名: {md_safe(p['title'])}")
            src = f"出处: {(p.get('journal') or '—').strip()}"
            if p.get("year"):
                src += f" {p['year']}"
            vp = []
            if p.get("volume"):
                vp.append(f"卷 {p['volume']}")
            if p.get("issue"):
                vp.append(f"期 {p['issue']}")
            if p.get("pages"):
                vp.append(f"页 {p['pages']}")
            if vp:
                src += " · " + " ".join(vp)
            head.append(src)
            if p.get("published"):
                head.append(f"在线发表: {p['published']}")
            if p.get("doi"):
                head.append(f"DOI: {p['doi']} · https://doi.org/{p['doi']}")
            if p.get("article_type"):
                head.append(f"体裁: {md_safe(p['article_type'])}")
            if p.get("authors"):
                head.append("作者: " + ", ".join(md_safe(a) for a in p["authors"]))
            kws = [str(x).strip() for x in ((p.get("keywords") or []) + (p.get("tags") or [])) if str(x).strip()]
            if kws:
                head.append("关键词: " + "; ".join(md_safe(k) for k in kws[:12]))
            if (p.get("abstract_zh") or "").strip():
                head.append("中文摘要: " + " ".join(str(p["abstract_zh"]).split()))
            if (p.get("abstract") or "").strip():
                head.append("原文摘要（English）: " + " ".join(str(p["abstract"]).split()))
            blocks.append("\n".join(head))
        wk_blocks = [
            f"### {md_safe(w['title'])}\n链接: {base}/weekly/{w['slug']}/\n发布于: {w['date']}\n"
            "正文: " + " ".join(str(w["plain"] or w["summary"]).split())
            for w in weekly_index]
        papers_md = "\n\n".join(blocks)
        weekly_md = "\n\n".join(wk_blocks)
        llms_full = f"""# {cfg['title']}（{cfg['subtitle']}）· 全文索引

> 本文件面向大模型与答案引擎，收录本站全部 {len(items)} 篇文献的题录与中英文摘要、
> 以及 {n_weekly} 期「每周速递」中文精选正文。元数据来自 Crossref / OpenAlex，
> 中文摘要为 AI 辅助翻译（初译 + 审校），可能存在术语偏差，引用请以 DOI 指向的原文为准。
> 站点首页: {base}/ · 索引文件: {base}/llms.txt · 生成: {today}

## 文献（{len(items)} 篇，新→旧）

{papers_md}

## 每周速递（{n_weekly} 期，新→旧）

{weekly_md}
"""
        (SITE / "llms-full.txt").write_text(llms_full, encoding="utf-8")
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
        # 时区按 +08:00 标注（站点在中文语境制作，此前写死 Z=UTC 是把日期谎称早 8 小时）
        cst = "+08:00"
        atom = ['<?xml version="1.0" encoding="utf-8"?>',
                '<feed xmlns="http://www.w3.org/2005/Atom">',
                f'<title>{xml_text(cfg["title"])} · {xml_text(cfg["subtitle"])}</title>',
                f'<link rel="alternate" type="text/html" href="{base}/"/>',
                f'<link rel="self" type="application/atom+xml" href="{base}/atom.xml"/>',
                f'<id>{base}/</id>',
                f'<updated>{time.strftime("%Y-%m-%dT00:00:00")}{cst}</updated>',
                f'<author><name>{xml_text(cfg["title"])}</name><uri>{base}/</uri></author>']
        for p in newest:
            summ = (p.get("abstract_zh") or p.get("abstract") or "")[:300]
            atom += ['<entry>',
                     f'<title>{xml_text(display_title(p))}</title>',
                     f'<link rel="alternate" type="text/html" href="{base}/{p["id"]}/"/>',
                     f'<id>urn:doi:{xml_text(p.get("doi", p["id"]))}</id>',
                     f'<published>{p.get("added") or time.strftime("%Y-%m-%d")}T00:00:00{cst}</published>',
                     f'<updated>{p.get("added") or time.strftime("%Y-%m-%d")}T00:00:00{cst}</updated>',
                     f'<summary>{xml_text(summ)}</summary>']
            if p.get("journal"):
                atom.append(f'<category term="{xml_text(p["journal"])}"/>')
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
    about_dir = SITE / "about"
    about_dir.mkdir(parents=True, exist_ok=True)
    (about_dir / "index.html").write_text(build_about(cfg), encoding="utf-8")
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
