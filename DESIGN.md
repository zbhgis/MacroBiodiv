# MacroBiodiv 设计文档

> 本文档沉淀站点的设计决策与实现规范。**凡改动视觉样式、页面结构、交互、数据模型、
> 抓取管线或 LLM 行为，必须同步更新本文档相应小节，并检查 README.md 是否需要跟着改。**
> 使用/部署说明见 [README.md](README.md)；本文只讲「设计是什么、为什么这么设计」。

## 1. 总体架构

```
fetch_doi.py ──► llm.py ──► admin.py ──► build_site.py ──► site/（纯静态）
   DOI抓取      LLM中文化    本地管理后台      站点生成          │
   Crossref/OpenAlex          127.0.0.1:5201              git push + scp
                                                            ▼
                                            nginx（macrobiodiv.zbhgis.com）
```

- 纯 Python 标准库（urllib / http.server / json），零第三方依赖、零数据库、零服务端运行时
- `meta/papers.json` 是唯一数据源；`site/` 为构建产物（gitignore，可随时重建）
- 样式与交互框架移植自 GeoSciPlot（同作者的图库站），共享其 token 与组件语言

## 2. 视觉体系

### 2.1 色彩 token（明暗双主题，见 style.css `:root`）

| token | 暗色 | 亮色 | 用途 |
|---|---|---|---|
| `--bg` / `--card` | #0d1117 / #161b22 | #fff / #f6f8fa | 背景 / 卡片 |
| `--text` / `--dim` / `--faint` | #e6edf3 / #8b949e / #6e7681 | #1f2328 / #59636e / #818b98 | 正文三级 |
| `--line` / `--line2` | #1c2129 / #30363d | #e8ebef / #d0d7de | 发丝线 / 控件描边 |
| `--accent` | #58a6ff | #0969da | 强调（徽章、链接、按压态） |

- 暗色为默认主题；手动切换记 `localStorage("mbd-theme")`
- 绿 #3fb950 只用于「生命」意象（logo 新芽）与成功态，不做大面积

### 2.2 字体与布局

- 正文：`ui-sans-serif / PingFang SC / Microsoft YaHei`；数据字段：`ui-monospace`
- 首页容器 1180px；**桌面端右内边距 74px** 给右侧悬浮队列让位（≤640px 队列转横排）
- 断点：1000px（卡片 3→2 列）、640px（2→1 列、队列横排）

### 2.3 右侧悬浮按钮队列

42px 正圆 · `--card` 实底 · 发丝描边 · hover 变 accent 并 scale(1.06)。
顺序固定：全站搜索 / 返回 Home / GitHub / 明暗主题 / 回到顶部 —— 与主站 zbhgis.com 的 rail 同款同序。

## 3. 页面设计

### 3.1 首页 `/`

- Header：logo + 大标题 → gh-note（数据存于 GitHub）→ lede → meta-row（N 篇 · 标签 · 期刊 · 年份）
- 筛选维度三行 chips：**标签 / 期刊 / 时间区间**（时间 = online 发表日期）+ 排序（发表 新→旧【默认】、旧→新、被引 多→少）
- 文献卡片（`.card`）：顶行 = 期刊徽章 + 文章类型徽章 + 年份；主标题（**中文优先**，悬停显示另一语言原题）；作者（mono，>3 人 et al.）；摘要节选 2 行（中文优先，200 字截断）；底行 = 标签 + 被引
- 网格 3/2/1 列等宽（文献无图，不用瀑布流）；**首屏 30 张由 Python 静态输出**（爬虫/AI 引擎友好），翻页与筛选由 JS 重渲染，两者输出必须完全一致（构建期 `sort_items` 与 JS `cmp` 同序）
- 分页 20/30/50；页码窗口随视口收窄；筛选/排序状态可记 localStorage（`mbd-per/mbd-sort2/mbd-filters`）；URL 参数 `?q=&tag=&journal=&from=&to=` 可分享

### 3.2 详情页 `/{id}/`（公众号推文式分节阅读）

```
徽章行（期刊 · 年份 · 类型 · 被引 · OA）
大标题（中文优先）→ 另一语言原题（副行）→ 作者（mono）
01 信息   信息卡网格（小标签在上、值在下，auto-fill 230px）
          期刊/类型/发表（标注 online）/卷期页/出版商/被引/收录/被浏览/DOI
          关键词（仅手动填写时显示）· 标签 —— 通栏 pills，可跳转
02 摘要   中文摘要为主阅读区（16px / 2.05 行距 / 两端对齐）
          英文原文摘要收合折叠；无中文时英文直接作为主阅读区
          备注（accent 左边线强调块）
03 引用   GB/T 7714 引用条（mono 卡片）+ 「复制引用」「复制 BibTeX」
          （data-copy 通用复制机制）+ BibTeX 折叠查看
· END ·
上一篇 / 下一篇（等宽卡片，中文标题 + 期刊·年份）
```

### 3.2.1 分享与收录（SEO）

- 页面输出 Open Graph 标签（og:title/description/type/url/site_name）：详情页 `og:type=article`，
  配置 `site_url` 后自动生成，聊天工具分享出卡片
- 详情页内嵌 Schema.org **ScholarlyArticle JSON-LD**（标题/作者/期刊/出版商/日期/DOI/关键词），
  搜索引擎可直接理解文献结构
- 构建时生成 **robots.txt + sitemap.xml**（全站 URL + lastmod）与 **atom.xml**（最新 20 篇订阅源，
  中文摘要优先），同样依赖 `site_url` 配置

### 3.3 全站搜索 `/search/`

- 无检索后端：在 `papers-data.js` 全量元数据上做客户端匹配，多词空格分隔 = AND
- 检索域（`se`，构建期算好）：id / DOI / 标题(中英) / 作者 / 期刊 / 年月 / 收录日 / 备注 / 出版商 / 类型 / 关键词(手动) / 标签 / **摘要全文(中英)**
- 结果行：主标题（中文优先，命中高亮）+ 元信息行（作者·期刊·年份·DOI·英文原题，可高亮）；`?q=` 可分享

### 3.4 管理后台 `scripts/admin_ui.html`（127.0.0.1:5201）

沿用站点 token 与 GeoSciPlot 的 mono 工程风，按本站功能重新组织：

- **品牌头**：favicon 圆角图标 + 标题（与站点 logo 同源）
- **分节标题**：h2 带 accent 竖标（延续详情页 .sec-h 语言）
- **添加文献**：DOI 输入区（虚线框，粘贴/导入 .txt）→ 抓取结果卡片
  → 发布区（成组卡片 .pubbox，含提交信息与推送/同步开关）→ 任务日志（实时步骤）
- **抓取结果卡片**：左列 = 与站点卡片同一徽章语言（期刊徽章 + 体裁 chip + 日期顶行、
  英文标题粗体、中文标题副行、作者/卷期/被引/OA/id、**翻译状态徽章**
  「✓ 初译+审校 / ◑ 仅初译 / ✗ 失败」，悬停看原因，摘要 4 行截断）；
  右列 = 可编辑手动字段（中文标题 / 文章类型（datalist）/ 关键词 / 标签 / 中文摘要 / 备注）
- **文献管理**：行卡片同一徽章语言；标题中文优先 + 英文副行；
  操作：编辑 / 重新抓取 / 重新翻译 / 删除；筛选：搜索 + 标签 chips + 期刊下拉 + 日期区间
- **通用组件**：页内确认弹窗（替代原生 confirm）、toast、任务日志步骤条（…/✓/✗）、
  同批重复 DOI 客户端即时标出
- 明暗主题跟随系统（prefers-color-scheme）；只监听 127.0.0.1 免登录

## 4. 数据模型 `meta/papers.json`

```jsonc
{
  "generated": "2026-09-19", "count": 6,
  "items": [{
    "id": "548b53c99e",              // sha1(doi.lower())[:10]，目录名 = id
    "doi": "10.1038/s41586-...",     // 规范化（无 URL 前缀）
    // ── API 自动字段（重新抓取会更新）──
    "title": "...", "subtitle": "", "authors": ["Given Family", ...],
    "journal": "...", "journal_short": "...", "publisher": "...",
    "type": "journal-article",       // Crossref 粗类型
    "oa_type": "article",            // OpenAlex 体裁提示（喂给 LLM）
    "year": "2023", "published": "2023-03-15",   // 时间口径 = online 优先
    "published_online": true,        // published 是否确为 online 日期
    "volume": "", "issue": "", "pages": "", "issn": "", "url": "",
    "abstract": "...", "cited_by": 246, "oa": true, "source": "crossref+openalex",
    // ── LLM 生成字段（重新翻译时重写）──
    "title_zh": "...", "abstract_zh": "...", "article_type": "Research Article",
    // ── 手动字段（永不被自动流程覆盖）──
    "keywords": ["sea ice", ...],    // 按文章原文填写（API 拿不到作者关键词）
    "tags": ["样点分布图"], "note": "",
    // ── 统计 ──
    "added": "2026-09-19"
  }]
}
```

字段优先级规则：**手动 > LLM > 自动回落**。GeoSciPlot 的 titles.csv 副本被有意去掉，
papers.json 单一数据源避免双写不同步。

## 5. DOI 抓取管线（fetch_doi.py）

1. **规范化**：从任意文本提取 DOI（`10.x/...`，剥 doi.org 前缀），按「DOI 只含 ASCII」
   截断防全角标点混入，URL 侧再做一次 quote 兜底
2. **分层**：Crossref（`works/{doi}`，mailto 礼貌池）主力 → OpenAlex（`works/doi:{doi}`）补
   摘要（还原倒排索引）/ 被引 / OA / 体裁提示；**不抓出版社页面**（Cloudflare 反爬，稳定优先）
3. **时间口径**：`published-online` → `published` → `issued`（一篇文献有多个日期，全站统一
   以 online 为准；无 online 记录时回落并以 `published_online` 标记如实呈现）
4. **关键词**：作者关键词 API 层不可得，OpenAlex keywords 属内容推断已**停用**，
   关键词由管理端按原文手动填写

## 6. LLM 中文化（llm.py）

- 配置：环境变量 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME` → Windows 注册表
  HKCU\Environment 兜底（老进程读不到新设的 User 变量）
- **两重处理**：初译（`_TRANSLATOR_SYSTEM`）→ 审校（`_REVIEWER_SYSTEM`）；两重同时判定
  `article_type`（按出版社惯例：Nature 系 Perspective/News & Views、Science 系 Research
  Article/Report、Cell 系 Spotlight……，元数据体裁作提示，拿不准选保守通用体裁）
- 固定 prompt 原则：忠实完整、术语以「术语在线」规范为锚（`_GLOSSARY` 锚点表）、
  拉丁学名/单位/同位素/引用标记保留原样、缩写首现「中文全称（缩写）」、标题不加句号
- 输出：严格 JSON + `_extract_json` 容错（剥围栏）+ 解析失败自动重试一次；
  `max_tokens: 16384`（推理模型思考链计入输出配额，实测可吃 1000+ tokens）
- **限流与回退分层**（详见 README 表格）：请求节流 → 429 指数退避 + Retry-After →
  断路器（120s 内 2 次全败冷却 60s）→ 审校失败降级「仅初译」→ 初译失败留空正常入库 →
  「翻译缺中文的」批量幂等补译（只补空缺不覆盖手改）
- 预算分级：抓取同步路径 200s（浏览器在等）/ 批量任务 600s

## 7. 部署

- 数据与索引随 GitHub 仓 `zbhgis/MacroBiodiv`；站点无图，生产包只有 HTML/JS
- nginx：`deploy/nginx-macrobiodiv.conf`（webroot /var/www/macrobiodiv，/api/ 反代主站
  FastAPI 做浏览统计；安全响应头 nosniff / SAMEORIGIN / Referrer-Policy；
  /assets/ 因带 ?v= 版本号放行 30d 长缓存）；DNS / 证书步骤见 `deploy/部署操作手册.md`
- 端口约定：**管理后台 5201**（5200 被 MultiColor 的管理后台占用），本地预览 7332
- 浏览统计沿用主站 tracker：打点 `/macrobiodiv/{path}`，读取 `?prefix=/macrobiodiv/`

## 8. 设计决策记录

| 决策 | 原因 |
|---|---|
| 纯标准库 + 静态站，无框架 | 与 GeoSciPlot 同构，零运维，服务器只跑 nginx |
| 文献卡片用等宽 grid 而非瀑布流 | 文献无图，信息密度一致，瀑布流无意义 |
| 首屏静态输出 + JS 重渲染双轨 | 爬虫/AI 引擎可见 + 交互灵活；两者排序逻辑必须一致 |
| 详情页中文标题/摘要优先 | 站点受众为中文读者；英文原题保留（副行/折叠/悬停）保证溯源 |
| 不抓出版社页面 | Cloudflare 反爬不可靠；代价是作者关键词、出版社精确栏目名需手动补 |
| 去掉 titles.csv | 条目少结构稳，papers.json 单源比双写可靠 |
| 时间以 online 为准 | print/online/issued 多日期中 online 最早最常用，全站（徽章/筛选/排序）统一 |
| admin 只监听 127.0.0.1 | 免登录；发布用本机 git 凭据，服务器不存 token |

## 9. 维护约定

- **改动即更新**：视觉样式、页面结构、交互、`papers.json` 字段、抓取管线、LLM 行为
  有任何变化 → 更新本文档对应小节 + 检查 README.md；仓库根的 `AGENTS.md` 已约定
  后续维护会话自动遵守
- 样式常量集中在 `scripts/build_site.py` 的 `CSS`/`JS` 字符串；与 GeoSciPlot 共享的部分
  （token、fab 队列、分页条、搜索页版式）修改前先比对原站，保持同源语言
- 资源引用统一带 `?v=构建时间戳` 防缓存；改 CSS/JS 后必须重新 build_site.py
- logo/图标：`scripts/gen_logo.py` 生成（纯几何，4x 超采样），改参数重跑即可
