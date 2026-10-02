#!/usr/bin/env python3
"""DOI 元数据抓取 —— 按 DOI 获取文献基本信息（Crossref 主力 + OpenAlex 补充）。

分层策略（参考 doi2md，只取基本信息、不抓出版社页面，避开 Cloudflare 反爬）：
    1. Crossref  api.crossref.org/works/{doi}   → 标题 / 作者 / 期刊 / 年月 / 卷期页 / 摘要(若有)
    2. OpenAlex  api.openalex.org/works/doi:{doi} → 补摘要(还原倒排索引) / 关键词 / 被引 / OA

输出统一为「规范记录」字典（papers.json 单条去掉手动字段后的样子）。
无第三方依赖，仅标准库。

CLI 自测：
    python scripts/fetch_doi.py 10.1038/s41467-021-24264-9
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

MAILTO = "zbhgis@example.com"          # Crossref 礼貌池（更快更稳）
UA = f"MacroBiodivAdmin/1.0 (mailto:{MAILTO})"
TIMEOUT = 30

# quote 的旧绑定（_get_json 里用 urllib.parse.quote）
quote = urllib.parse.quote

# 从任意粘贴文本里提取 DOI：10.xxxx/xxxxx（剥掉 doi.org 前缀后匹配正文）
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>，。；、）)\]}]+", re.I)
# 提取后去掉常见的行尾标点（括号/句号等可能属于句子而非 DOI）
DOI_TAIL = ".,;:)]}>"


class FetchError(Exception):
    """抓取失败（DOI 不存在 / 网络错误 / 限流），message 面向管理界面展示。"""


def normalize_doi(text: str) -> str:
    """从任意文本提取第一个 DOI，剥掉 URL 前缀与行尾标点。无 DOI 返回空串。"""
    if not text:
        return ""
    text = text.strip()
    # 显式 URL 先剥前缀，防止 doi.org/10.1038/... 之外的形式漏提取
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text, flags=re.I)
    m = DOI_RE.search(text)
    if not m:
        return ""
    # DOI 规范上只含 ASCII 可打印字符：从首个非 ASCII 字符处截断
    # （防止「10.xxx/xxxx（补充材料）」这类全角括号 / 中文注释混进 URL）
    ascii_part = re.match(r"[\x21-\x7e]+", m.group(0))
    if not ascii_part:
        return ""
    doi = ascii_part.group(0)
    # 带 URL 查询串的粘贴（doi.org/10.x/xxx?utm=…）会把参数当成 DOI，在 ? 处截断
    doi = doi.split("?", 1)[0].rstrip(DOI_TAIL)
    # 行尾标点被截断后可能留下悬空括号内容，去掉不成对的右括号
    while doi and doi[-1] in ")" and doi.count("(") < doi.count(")"):
        doi = doi[:-1]
    return doi


def _get_json(url: str) -> dict:
    req = urllib.request.Request(quote(url, safe=":/?&="), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FetchError("DOI 不存在（Crossref/OpenAlex 均未收录）") from None
        if e.code == 429:
            raise FetchError("被限流（429），稍等几秒重试") from None
        raise FetchError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise FetchError(f"网络错误：{e.reason}") from None
    except TimeoutError:
        raise FetchError("请求超时") from None


def _clean_abstract(raw: str) -> str:
    """Crossref 摘要带 JATS 标签（<jats:p> 等），剥标签 + 还原实体 + 压空白。"""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Crossref 的 JATS 里 <title>Abstract</title> 会被剥成正文开头的孤立词，去掉
    return re.sub(r"^abstract\s*[:：]?\s*", "", text, count=1, flags=re.I) or text


def _date_str(parts: list | None) -> str:
    """[[2021, 6, 24]] → '2021-06-24'；缺失的位自动降级（2021-06 / 2021）。"""
    if not parts:
        return ""
    p = [int(x) for x in parts[0] if x is not None] if parts[0] else []
    if not p:
        return ""
    if len(p) == 1:
        return str(p[0])
    if len(p) == 2:
        return f"{p[0]:04d}-{p[1]:02d}"
    return f"{p[0]:04d}-{p[1]:02d}-{p[2]:02d}"


def fetch_crossref(doi: str) -> dict:
    d = _get_json(f"https://api.crossref.org/works/{doi}?mailto={MAILTO}")["message"]

    authors = []
    for a in d.get("author", []):
        name = " ".join(x for x in (a.get("given", ""), a.get("family", "")) if x).strip()
        if not name:
            name = (a.get("name") or "").strip()
        if name:
            authors.append(name)

    issued = d.get("issued", {}).get("date-parts")
    published = d.get("published", {}).get("date-parts") or issued
    # 时间一律以在线发表（online）为准 —— 同一篇文献有 print / online / issued 多个日期，
    # online 最早也最常被引用；无 online 记录时回落 published → issued
    online = d.get("published-online", {}).get("date-parts")
    pd_parts = online or published
    pd = _date_str(pd_parts)
    year = pd[:4] if pd else ""

    page = (d.get("page") or "").strip()
    art = (d.get("article-number") or "").strip()

    return {
        "doi": (d.get("DOI") or doi).strip(),
        "title": (d.get("title") or [""])[0].strip(),
        "subtitle": (d.get("subtitle") or [""])[0].strip(),
        "authors": authors,
        "journal": (d.get("container-title") or [""])[0].strip(),
        "journal_short": (d.get("short-container-title") or [""])[0].strip(),
        "publisher": (d.get("publisher") or "").strip(),
        "type": (d.get("type") or "").strip(),
        "year": year,
        "published": pd,
        "published_online": bool(online),
        "volume": (d.get("volume") or "").strip(),
        "issue": (d.get("issue") or "").strip(),
        "pages": page or art,
        "abstract": _clean_abstract(d.get("abstract") or ""),
        "issn": (d.get("ISSN") or [""])[0] if d.get("ISSN") else "",
        "url": ((d.get("resource") or {}).get("primary") or {}).get("URL") or d.get("URL") or "",
        "source": "crossref",
    }


def fetch_openalex(doi: str) -> dict:
    d = _get_json(f"https://api.openalex.org/works/doi:{doi}?mailto={MAILTO}")

    # abstract_inverted_index：{word: [位置...]} → 按位置还原成句
    abstract = ""
    aii = d.get("abstract_inverted_index")
    if aii:
        pos: dict[int, str] = {}
        for w, idxs in aii.items():
            for i in idxs:
                pos[i] = w
        abstract = " ".join(pos[i] for i in sorted(pos))

    src = ((d.get("primary_location") or {}).get("source") or {})
    pd = d.get("publication_date") or ""
    return {
        "abstract": abstract.strip(),
        # 不采集 OpenAlex keywords —— 那是基于内容归类的主题标签（机器推断），
        # 不是文章作者提供的关键词；keywords 改为管理端手动维护
        "cited_by": int(d.get("cited_by_count") or 0),
        "oa": bool((d.get("open_access") or {}).get("is_oa")),
        # OpenAlex 体裁（article/review/editorial/letter/erratum…），作为大模型判定文章类型的提示
        "oa_type": (d.get("type") or "").strip(),
        "journal": (src.get("display_name") or "").strip(),
        "year": str(d.get("publication_year") or ""),
        "published": pd,
        "title": (d.get("title") or "").strip(),
        "authors": [a.get("author", {}).get("display_name", "").strip()
                    for a in d.get("authorships", []) if a.get("author", {}).get("display_name")],
        "source": "openalex",
    }


def fetch_paper(doi: str) -> dict:
    """抓取并合并成规范记录。Crossref 缺失时整体回落 OpenAlex。"""
    doi = normalize_doi(doi)
    if not doi:
        raise FetchError("无法从输入中提取 DOI")

    cr: dict | None = None
    cr_err = ""
    try:
        cr = fetch_crossref(doi)
    except FetchError as e:
        cr_err = str(e)

    oa: dict | None = None
    try:
        oa = fetch_openalex(doi)
    except FetchError:
        pass

    if not cr and not oa:
        raise FetchError(cr_err or "Crossref 与 OpenAlex 均未收录该 DOI")

    if not cr:
        # OpenAlex 兜底成完整记录（其 publication_date 即在线发表日期）
        rec = {"doi": doi, "subtitle": "", "journal_short": "", "publisher": "", "type": "",
               "pages": "", "issn": "", "url": f"https://doi.org/{doi}", "abstract": "",
               "published_online": True, "source": "openalex"}
        rec.update({k: v for k, v in oa.items() if v})
        return _finalize(rec)

    rec = dict(cr)
    if oa:
        if not rec.get("abstract"):
            rec["abstract"] = oa.get("abstract", "")
        rec["cited_by"] = oa.get("cited_by", 0)
        rec["oa"] = oa.get("oa", False)
        rec["oa_type"] = oa.get("oa_type", "")     # 体裁提示（供 LLM 判定文章类型）
        if not rec.get("journal"):
            rec["journal"] = oa.get("journal", "")
        if not rec.get("year") and oa.get("year"):
            rec["year"] = oa["year"]
        if not rec.get("published") and oa.get("published"):
            rec["published"] = oa["published"]
        if not rec.get("title") and oa.get("title"):
            rec["title"] = oa["title"]
        rec["source"] = "crossref+openalex"
    return _finalize(rec)


def _unescape_deep(v):
    """上游元数据偶带 HTML 实体（部分出版社 deposit 的期刊名如
    「Nature Ecology &amp; Evolution」），入库前统一反转义 —— 否则站点
    esc() 再转义一次，页面会出现「&amp;amp;」。"""
    if isinstance(v, str):
        return _html.unescape(v) if "&" in v else v
    if isinstance(v, list):
        return [_unescape_deep(x) for x in v]
    return v


def _finalize(rec: dict) -> dict:
    """补默认字段，保证记录形状一致（papers.json 里手动字段由管理端另加）。"""
    rec = {k: _unescape_deep(v) for k, v in rec.items()}
    rec.setdefault("keywords", [])
    rec.setdefault("cited_by", 0)
    rec.setdefault("oa", False)
    rec.setdefault("abstract", "")
    rec.setdefault("published_online", False)
    rec["title"] = (rec.get("title") or "").strip()
    if not rec.get("url"):
        rec["url"] = f"https://doi.org/{rec.get('doi', '')}"
    return rec


def paper_id(doi: str) -> str:
    """文献 id：DOI（小写）sha1 前 10 位，与 GeoSciPlot 的图片 id 同风格。"""
    import hashlib
    return hashlib.sha1(doi.lower().encode("utf-8")).hexdigest()[:10]


def _bib_escape(s: str) -> str:
    return s.replace("\\", "\\textbackslash{}").replace("&", "\\&").replace("%", "\\%")\
            .replace("_", "\\_").replace("#", "\\#").replace("{", "\\{").replace("}", "\\}")\
            .replace("~", "\\textasciitilde{}").replace("^", "\\textasciicircum{}")


def to_bibtex(p: dict) -> str:
    """由规范记录生成 BibTeX（@article 为主，非期刊文章回落 @misc）。"""
    family = (p.get("authors") or ["unknown"])[0].split(" ")[-1].lower()
    family = re.sub(r"[^a-z]", "", family) or "ref"
    year = p.get("year") or "nd"
    key = f"{family}{year}"
    authors = " and ".join(p.get("authors") or [])

    lines: list[str] = []
    if p.get("type") == "journal-article" or p.get("journal"):
        lines = [f"@article{{{key},"]
        if authors:
            lines.append(f"  author = {{{_bib_escape(authors)}}},")
        lines.append(f"  title = {{{_bib_escape(p.get('title', ''))}}},")
        if p.get("journal"):
            lines.append(f"  journal = {{{_bib_escape(p['journal'])}}},")
        if year != "nd":
            lines.append(f"  year = {{{year}}},")
        if p.get("volume"):
            lines.append(f"  volume = {{{_bib_escape(p['volume'])}}},")
        if p.get("issue"):
            lines.append(f"  number = {{{_bib_escape(p['issue'])}}},")
        if p.get("pages"):
            lines.append(f"  pages = {{{_bib_escape(p['pages'])}}},")
        lines.append(f"  doi = {{{p.get('doi', '')}}},")
        if p.get("publisher"):
            lines.append(f"  publisher = {{{_bib_escape(p['publisher'])}}},")
    else:
        lines = [f"@misc{{{key},"]
        if authors:
            lines.append(f"  author = {{{_bib_escape(authors)}}},")
        lines.append(f"  title = {{{_bib_escape(p.get('title', ''))}}},")
        if year != "nd":
            lines.append(f"  year = {{{year}}},")
        lines.append(f"  doi = {{{p.get('doi', '')}}},")
        if p.get("url"):
            lines.append(f"  howpublished = {{\\url{{{p['url']}}}}},")
    lines.append("}")
    return "\n".join(lines)


def fetch_many(dois: list[str], sleep: float = 0.3) -> list[dict]:
    """批量抓取（顺序执行，礼貌限速）。返回 [{ok, input, record?, error?, duplicate_of?}]。"""
    out = []
    for i, raw in enumerate(dois):
        if i:
            time.sleep(sleep)
        doi = normalize_doi(raw)
        item: dict = {"input": raw, "doi": doi, "ok": False}
        if not doi:
            item["error"] = "无法从输入中提取 DOI"
            out.append(item)
            continue
        try:
            rec = fetch_paper(doi)
            rec["id"] = paper_id(rec["doi"])
            item["ok"] = True
            item["record"] = rec
        except FetchError as e:
            item["error"] = str(e)
        out.append(item)
    return out


def main() -> int:
    dois = [a for a in sys.argv[1:] if a.strip() and a != "--"]
    if not dois:
        print("用法：python scripts/fetch_doi.py DOI [DOI ...]")
        return 1
    results = fetch_many(dois)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
