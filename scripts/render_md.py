"""迷你 Markdown 渲染器（每周速递专用，纯标准库）。

内容源为固定结构的周报 md（front matter + `# 文献N` / `## 1.信息` 层级 +
粗斜体 + 独立图片行 + 裸 DOI URL），因此只实现这个子集；未识别的语法按
纯文本段落兜底（先 HTML 转义再套内联标记，保证安全）。标题整体降一级
（`# `→h2.md-h1、`## `→h3.md-h2），与主站 zbhgis.com 的 mdToHtml 行为一致。
"""
import html
import re

# front matter：--- 包裹的 key: value / key: ["a", "b"] 极简 YAML 子集。
# 容忍 BOM 与前导空白行（外部来源的 md 常带），否则 front matter 会漏进正文渲染
FM_RE = re.compile(r"\A[\s\uFEFF]*---\s*\n(.*?)\n---\s*\n?", re.S)
H_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
IMG_RE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
EM_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
URL_RE = re.compile(r"https?://[^\s<>\"'）)\]}，。；：！？]+")


def parse_front_matter(text: str) -> tuple[dict, str]:
    """返回 (meta, 正文)。无 front matter 时 meta 为空 dict。"""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("["):
            try:
                meta[key] = json_loads(val)
                continue
            except ValueError:
                pass
        meta[key] = val.strip('"').strip("'")
    return meta, text[m.end():]


def json_loads(s: str) -> list:
    import json
    return json.loads(s)


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _inline(text: str) -> str:
    """行内标记：先转义，再粗体/斜体/裸 URL 自动链接。"""
    s = _esc(text)
    s = BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = EM_RE.sub(r"<em>\1</em>", s)

    def _link(m: re.Match) -> str:
        url = m.group(0)
        # 剥掉粘在 URL 尾部的中文标点（DOI 行「…03025-1 中国…」类正文粘连不含标点）
        return f'<a href="{url}" target="_blank" rel="noopener">{url}</a>'

    s = URL_RE.sub(_link, s)
    return s


def render(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """正文 → (HTML, TOC)。TOC 项 = (层级 1|2, anchor id, 标题文本)。"""
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    h2 = 0
    h3 = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = IMG_RE.match(line.strip())
        if m:
            flush()
            alt, src = _esc(m.group(1)), _esc(m.group(2))
            out.append(f'<p class="wk-img"><img src="{src}" alt="{alt}" loading="lazy"></p>')
            continue
        m = H_RE.match(line)
        if m:
            flush()
            level, txt = len(m.group(1)), m.group(2).strip()
            plain = _esc(re.sub(r"\*+", "", txt))
            if level == 1:
                h2 += 1
                h3 = 0
                hid = f"doc-{h2}"
                toc.append((1, hid, plain))
                out.append(f'<h2 id="{hid}" class="md-h1">{_inline(txt)}</h2>')
            elif level == 2:
                h3 += 1
                hid = f"doc-{h2}-{h3}" if h2 else f"doc-0-{h3}"
                toc.append((2, hid, plain))
                out.append(f'<h3 id="{hid}" class="md-h2">{_inline(txt)}</h3>')
            else:  # 更深层级本栏目不出现，兜底降级渲染
                out.append(f"<h4>{_inline(txt)}</h4>")
            continue
        para.append(line.strip())
    flush()
    return "\n".join(out), toc


def plain_text(text: str) -> str:
    """去标记纯文本（字数统计 / 摘要提取用）。"""
    body = FM_RE.sub("", text)
    body = re.sub(r"(?m)^#{1,6}\s+", "", body)
    body = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", body)
    body = body.replace("**", "").replace("*", "")
    return re.sub(r"\s+", "", body)


def parse_md(text: str) -> tuple[dict, str, list, str]:
    """一次解析：返回 (meta, 正文 HTML, TOC, 纯文本)。"""
    meta, body = parse_front_matter(text)
    doc, toc = render(body)
    # 纯文本取剥离 front matter 之后的正文——搜索索引与字数统计不收元数据
    return meta, doc, toc, plain_text(body)


# ── 「文献N」结构解析（管理后台上传收录用）──
# 周报每篇文献固定为：# 文献N（体裁注记可省）+ 1.信息（标题/DOI/期刊）
# + 2.摘要（个别期作「核心内容」）+ 3.图表（图片行可有可无 ——「图表为无」时
# 无图片行，由调用方用 logo 兜底）。注记体裁（如「（review）」）抽出作
# article_type 预填。个别期的小节会丢「##」前缀，因此字段一律在整个
# 文献小节内搜索，不依赖小节层级。
WK_PAPER_RE = re.compile(r"(?m)^#[ \t]*文献[ \t]*(\d+)[ \t]*(?:（([^）]*)）)?[^\n]*$")
WK_TITLE_RE = re.compile(r"(?m)^标题[:：][ \t]*(.+)$")
WK_DOI_RE = re.compile(r"(?m)^DOI[:：][ \t]*(.+)$", re.I)
WK_JOURNAL_RE = re.compile(r"(?m)^期刊[:：][ \t]*(.+)$")
WK_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
# 摘要节标题：## 2.摘要 / 2.核心内容 等；正文截到下一个标题行（#… 或 数字.）为止
WK_ABS_RE = re.compile(r"(?m)^(?:#{1,6}[ \t]*)?(?:\d+[.、][ \t]*)?(?:摘要|核心内容)[ \t]*$")
WK_HEAD_RE = re.compile(r"^(?:#{1,6}[ \t]*|\d+[.、][ \t]*)")
CJK_RE = re.compile(r"[\u3400-\u9fff]")


def split_title_line(line: str) -> tuple[str, str]:
    """「标题：」行的「英文 中文」拆分：首个 CJK 字符起视为中文段（英文标题不含 CJK）。"""
    m = CJK_RE.search(line)
    if not m:
        return line.strip(), ""
    return line[: m.start()].strip(), line[m.start():].strip()


def parse_weekly_papers(text: str) -> list[dict]:
    """提取周报里每篇文献的结构化字段。

    返回 [{no, genre, doi_line, title_en, title_zh, journal, abstract_zh, images}]；
    doi_line 交由调用方 normalize_doi 提取（本模块不依赖 fetch_doi）。
    """
    _, body = parse_front_matter(text)
    parts = WK_PAPER_RE.split(body)
    out: list[dict] = []
    for no, genre, sec in zip(parts[1::3], parts[2::3], parts[3::3]):
        m = WK_TITLE_RE.search(sec)
        title_en, title_zh = split_title_line(m.group(1).strip()) if m else ("", "")
        md_ = WK_DOI_RE.search(sec)
        mj_ = WK_JOURNAL_RE.search(sec)
        abstract = ""
        ma = WK_ABS_RE.search(sec)
        if ma:
            lines: list[str] = []
            for ln in sec[ma.end():].splitlines()[1:]:   # 首行是标题行残段，跳过
                if WK_HEAD_RE.match(ln):
                    break
                lines.append(ln.strip())
            abstract = "\n".join(x for x in lines if x)
        # 周报用「无」标记原文没有摘要（短文类常见）—— 视作空缺，否则会被当成
        # 正文写进 abstract_zh，详情页把占位符显示成摘要（2026-10-10 暴露）
        if re.sub(r"[。.\s]", "", abstract) in ("无", "暂无"):
            abstract = ""
        # 注记体裁 → article_type 预填：小写英文词首字母大写（review→Review，
        # perspectives→Perspectives），首字母已大写的原样保留（Letter/Spotlight）
        genre = (genre or "").strip()
        if genre and genre[:1].isalpha() and genre[:1].islower():
            genre = genre[0].upper() + genre[1:]
        out.append({
            "no": int(no),
            "genre": genre,
            "doi_line": md_.group(1).strip() if md_ else "",
            "title_en": title_en,
            "title_zh": title_zh,
            "journal": mj_.group(1).strip() if mj_ else "",
            "abstract_zh": abstract,
            "images": WK_IMG_RE.findall(sec),
        })
    return out
