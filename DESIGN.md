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
- **字号体系**（2026-10 新增，4 档变量 + h2，全部 font-size 只允许用变量，禁止散落 px）：
  `--fs-xs:13px`（辅助小字：角标/徽章/页脚）· `--fs-sm:14px`（控件：按钮/输入框/chips）·
  `--fs-md:15px`（正文次级：列表/元信息）· `--fs-base:17px`（正文基准 body）·
  `--fs-h2:22px`（页面级标题）；**放大档** `html[data-fs=lg]` 只覆盖变量（14.5/16/17/19/24），
  布局零改动。**菜单栏 .mnav 与大标题 clamp 为固定框架，保持 px 不参与调节**；
  FAB「A」按钮切换（标准/放大两档），偏好存 localStorage `mbd-fs`，head 内联脚本
  渲染前置位防闪烁；原 10–12.5px 的 mono 微字号就近并入 xs 档（视觉整体 +2px 左右）
- **形状分层（2026-10 与 GeoSciPlot 全面对齐）**：①「控件」一律胶囊
  `border-radius:999px` + `--fs-sm`——搜索框（连体壳：外壳 999px、内部按钮只圆右半
  `0 999px 999px 0` + `border-left` 内分隔 + `:focus-within` 整框亮 accent）、
  chips、日期输入、select、普通按钮（筛选/重置/上一页/下一页/页码/返回/复制）、
  排序胶囊组（外壳 `--card` 底，选中项 **accent 实底**，实底只给真单选组）、
  搜索页大输入框、统计页小 tab；卡片徽章（类型/期刊/周报 tag）也统一 mini 胶囊。
  ②「容器」保留小圆角：卡片/缩略图/下拉面板/虚线框 6px，大图/统计卡 8px。
  两层圆角必须分开，全胶囊会让卡片失去边界感。控件行 `align-items:center`
  （baseline 会让高矮不一的胶囊错位）；**表单控件不继承 body 字体**，每个
  button/input/select 都显式写等宽栈 + 字号，漏写就掉回浏览器默认字体
- 首页容器 1180px；**桌面端右内边距 74px** 给右侧悬浮队列让位（≤640px 队列转横排）
- 断点：1100px（首页瀑布流卡片 4→3 列）、760px（3→2 列）、640px（队列横排）

### 2.3 右侧悬浮按钮队列

42px 正圆 · `--card` 实底 · 发丝描边 · hover 变 accent 并 scale(1.06)。
顺序固定：全站搜索 / 返回 Home / GitHub / 明暗主题 / **字号 A**（标准/放大两档，
`html[data-fs=lg]` 覆盖字号变量，偏好存 `mbd-fs`）/ 回到顶部（与主站 rail 同款同序；
2026-10 应用户要求移除最初顶部的「返回主站」跨站回链 —— 主站入口改由菜单栏「更多▾」承担）
（历史：早期曾因「子站内容单一」回退过顶部导航栏，只留 rail；2026-10 应用户要求重新
引入顶部菜单栏（见 2.4），两者共存分工 —— 菜单栏管全站导航与预留入口，rail 管工具动作。）

### 2.4 顶部菜单栏（`.mnav`，2026-10 新增）

移植主站 zbhgis.com 的 header，同源数值：**sticky 吸顶**（z-50）· 发丝底边（`--line`）·
90% 不透明底 `--header-bg` + `backdrop-filter: blur(12px)` 毛玻璃；内栏 1080px 居中、高 56px。

- 左：站点名 brand（**26px 圆形 logo 图（favicon 同源）+ 站名**，17px semibold，间距 10px、
  图带 `--line` 细描边 —— zbhgis.com 头像 + 站名的同款规格，hover 变 accent）
- 右：导航组 `.mnav-links`（**与主站 v3-nav 同源**：15px、`6px 10px` 内边距、前置 **14px
  stroke 图标**、hover 变 text 且下划线 scaleX 0→1、`data-active=true` → accent 常亮）。
  现有项：**每周速递（纸飞机 → `/weekly/`，按 `path` 构建期标 `data-active` + aria-current）/
  全站统计（柱状图 → `/statistics/`，同款 active 检测）/ 更多▾**。
  「更多」= 主站 v3-more 同款：触发钮为 button（text + 11px caret），hover 或
  focus-visible 展开 `.mnav-dd`（display 直切无动画、caret 旋转 180°；面板 `--bg` 实底 +
  描边 + 主站同款投影 `0 10px 28px rgba(0,0,0,.16)`，项 14px / `8px 11px` / 15px faint
  图标：zbhgis 地球 · GeoSciPlot 图片样式（方框+圆点+山形，与主站「更多」菜单同款 SVG），
  target=_blank）
- ≤640px：桌面菜单隐藏，转主站同款 **details/summary 原生汉堡**（30px 方形 `.mnav-icon`，
  hover/open 变 accent + accent-soft 底；面板复用 `.mnav-dd`，含三项 + 分隔线 + 两外链）
  （历史：最初中间为「首页 / 全站搜索」+ 右一枚占位 icon 按钮；2026-10 改右侧三项导航组，
  随后整栏与主站 zbhgis.com 精确对齐并加图标）
- 新增 token：`--header-bg`（暗 `#0d1117e6` / 亮 `#ffffffe6`）、`--accent-soft`
  （暗 `#58a6ff1a` / 亮 `#0969da1a`）
- 窄屏（≤640px）：内栏 padding 收 16px，桌面菜单转 details 汉堡（见上）

## 3. 页面设计

### 3.1 首页 `/`

- Header：logo + 大标题 → gh-note（「文献数据存储于 GitHub，访问需具备 GitHub 访问能力
  （点此查看仓库）」，整条为指向仓库的链接）→ lede → meta-row（N 篇 · 标签 · 期刊 · 年份）
- 筛选维度三行：**标签 / 期刊 chips（多选模型）** + **时间区间**（时间 = online 发表
  日期）+ 排序（发表 新→旧【默认】、旧→新、**随机**——2026-10 由「被引 多→少」改来：
  站点不再携带被引数；每条目挂随机键做稳定洗牌，翻页 / 筛选不重排，点「随机」或重新
  访问页面才重新洗牌，`?sort=rand` 可分享）
- **chips 多选语义（GeoSciPlot 同源，2026-10 重做）**：默认全选（构建期
  `aria-pressed="true"`），点击某枚=剔除，行尾固定两枚虚线胶囊「全选」「反选」；计数
  徽标 `<i class="n">` 为 chip 内 mini 胶囊（`--line` 底、`min-width:22px` 定宽、
  `tabular-nums`）。状态矩阵：入选=透明底 + `--text` 字 + `--line2` 描边；剔除=
  `--faint` 字 + `--line` 边 + **删除线** + `opacity .75`；hover=accent 字/边 +
  `--accent-soft` 底。入选标签之间 **OR**（任一命中即显示）；全部选中=不筛选；
  被剔除的 ≤3 个时筛选摘要显示「标签/期刊 排除 X、Y」，否则「仅 入选值」；筛选激活时
  无标签/无期刊的内容随之隐藏。**标签行在 tags 数据为空时整行不输出**（管理员在后台
  补录 tags 后自动出现）——期刊同理走同一组件。chips 的 `aria-pressed` 是唯一状态
  真源，重置 = 全部写回 pressed=true（旧单选「只留第一枚」的写法已删净）
- 文献卡片（`.card`，GeoSciPlot 瀑布流同源语言）：**封面通栏顶图**（自然宽高比不裁切，
  底色作加载占位；**有 `cover_thumb` 时优先引用图床缩略外链**（`ct`/`data-full` 静态与
  JS 渲染一致），**`img` 带 width/height 预留位 + `.c-covbox::before`「加载中」呼吸层**——
  加载前按缩略图真实比例撑出占位区零抖动，图片画出来后自然盖住；加载失败先回退原图
  外链再降级；无封面或全部失败时换成期刊缩写占位块
  `.c-ph`（连 c-covbox 一起替换，撤掉加载层）——jsdelivr 多源降级耗尽后由
  `__coverPlaceholder` 兜底，不留破图，版式不塌）；
  说明区 `.c-cap` = 徽章行（文章类型 + 期刊）+ 主标题（**中文优先**，悬停显示另一语言
  原题，3 行截断）
- 瀑布流 **CSS multi-columns**：4 列、列距 18px，≤1100px 3 列、≤760px 2 列（列距 12px）；
  `break-inside:avoid` 防卡片跨列截断；hover 仅边框变 accent（无阴影，克制工程感）；
  **首屏 30 张由 Python 静态输出**（爬虫/AI 引擎友好），翻页与筛选由 JS 重渲染，两者输出必须完全一致（构建期 `sort_items` 与 JS `cmp` 同序；封面字段 `cv` 构建期写死带 `?v=`，静态与 JS 渲染同源）
- 分页 20/30/50；页码窗口随视口收窄；筛选/排序状态可记 localStorage（`mbd-per/mbd-sort2/mbd-filters`）；URL 参数 `?q=&tag=A|B|C&journal=A|B&from=&to=` 可分享（`encodeURIComponent`，
  多值 `|` 分隔，**旧单值链接天然兼容**；全部选中时该参数无意义，分享链接按选中集还原）

### 3.2 详情页 `/{id}/`（公众号推文式分节阅读，2026-10 对齐「浩瀚地学」文献精选排版）

排版语言移植自公众号「浩瀚地学」文献精选推文（`mp.weixin.qq.com` 实测规格），
色值仍走本站 token：**正文窄栏 677px 居中**（`.pbody`）· 标题块**居中** + accent **通栏底线**
· 节标题 = accent **5px 左竖线** + 18px 加粗（无底线）· 信息区为**「字段名：值」同行字段行**
· 摘要 15px/1.8 **左对齐** · 图表图片撑栏无圆角带**淡蓝光晕**（rgba(133,161,201,.5) 0 0 5px）

```
返回全部（左上，窄栏外）
┄ 窄栏 677px ┄
标题块 .p-head（居中，底部强调色通栏线 —— 颜色随渲染样式切换，见 §3.4）
  徽章行（期刊 · 年份 · 类型 · 行尾渲染颜色切换圆点×5）
  大标题（中文优先）→ 另一语言原题（副行）→ 作者（**最多两行**，papers.js 检测溢出后
          折叠为「展开全部 N 位作者 / 收起作者」；两行内或无 JS 完整展示）
1. 信息   字段行（字段名：值 同行）：DOI / 期刊（+缩写）/ 类型 / 发表（online 标注）
          / 卷期页 / 收录 / 被浏览 / 关键词 / 标签（可跳转 pills）
          —— DOI、类型、卷期页、关键词有值才输出该行
          （出版商 / 被引不展示：cited_by 仅随抓取入库存档，管理端与站点均不展示
          —— papers-data.js 不含 cited 字段，排序用随机替代）
2. 摘要   中文摘要为主阅读区（15px / 1.8 行高 / 左对齐）
          英文原文摘要收合折叠；无中文时英文直接作为主阅读区
          备注（accent 左边线强调块）
3. 图表   封面图（可选字段，仅在有图时出现，编号顺延；图片撑栏 + 淡蓝光晕）
3/4. 引用 GB/T 7714 引用条（mono 卡片）+ 「复制引用」「复制 BibTeX」
          （data-copy 通用复制机制）+ BibTeX 折叠查看
· END ·
上一篇 / 下一篇（等宽卡片，中文标题 + 期刊·年份）
```

- **图片灯箱**（2026-10 新增，拷自 mystation `frontend/lib/image-lightbox.ts` 删类型转纯 JS，
  零依赖）：源文件 `assets_src/image-lightbox.js` 随 assets 拷贝循环自动入
  `site/assets/`，详情页（挂 `.pbody`）与周报文章页（挂 `.wk-content`）经
  `page_shell` 的 `extra_assets` 注入后按容器自动挂载 —— 首页 / 搜索 / 周报列表不加载；
  点击图片放大、同容器多图切换（箭头 / 方向键 / 触摸滑动）、滚轮以光标为锚点缩放、
  双击 / 双指捏合、拖拽平移、Esc / 点空白关闭、相邻图预加载、背景滚动锁定（带滚动条补偿）；
  链接内图片默认不劫持（`data-lightbox="on"` 强制），`data-no-lightbox` 排除，
  `data-lightbox-src` 指定灯箱内加载的高清版；样式运行时自动注入、类名 `lbx-` 前缀，
  不污染站点样式（配色可经 `--lbx-backdrop / --lbx-accent / --lbx-fg` 覆盖）
- **文章 TOC**（2026-10 新增，拷自 mystation：数据层 `md-shared.ts` 的 slugify /
  extractTOC + 组件 BlogTOC 改写为原生 JS → `assets_src/article-toc.js`，样式类
  v3-side-hd / v3-side-link / v3-toc-link / v3-noscrollbar / toc-fab 拷入 style.css，
  CSS 变量已映射本站 token）：详情页节标题原本无 id —— JS 用 slugify+去重现补
  （`1-信息` 这类锚点）；当前小节高亮走 IntersectionObserver（rootMargin
  `-80px 0px -66% 0px`，标题进入视口上部 1/3 点亮），点击平滑滚动 + pushState 更新 hash。
  分端行为：周报文章页桌面端沿用服务端渲染的 `.wk-toc` 侧栏（组件只做增强，active 态
  同步点亮），<1280px 出 FAB（≤640px 抬到 bottom:62px 避让底部横排 .fab 工具排）+
  全高右抽屉；文献详情页 ≥1280px 出 fixed 右侧栏（right:70px 避让竖排 .fab），
  <1280px 同款 FAB + 抽屉；配套给 `.sec-h` 补 `scroll-margin-top:80px`
  （修锚点跳转被吸顶导航遮挡，也让高亮观察带能命中）。仅这两种文章页注入

### 3.2.1 分享与收录（SEO / GEO）

- 页面输出 Open Graph 标签（og:title/description/type/url/site_name）：详情页 `og:type=article`，
  配置 `site_url` 后自动生成，聊天工具分享出卡片；**详情页有封面时附 `og:image`（绝对 URL）**，
  分享卡片带封面
- **canonical 全页输出**（2026-10 新增）：`page_shell` 在配置了 `site_url` 时为每页输出
  `<link rel="canonical">`（与 og:url 同值）——多入口/带参数访问时搜索引擎只认这一份
- 详情页内嵌 Schema.org **ScholarlyArticle JSON-LD**（标题/作者/期刊/出版商/日期/DOI/关键词/
  **封面 image**），搜索引擎可直接理解文献结构；首页内嵌 **WebSite JSON-LD**（2026-10 新增，
  `name=MacroBiodiv` + `alternateName=宏观生物多样性文献库` 中英双名，供引擎建立站点身份映射）
- **首页 title 带中文关键词**（2026-10 新增）：`宏观生物多样性文献库 · MacroBiodiv`——只写
  "MacroBiodiv" 时搜中文词在 title 上无命中
- 构建时生成 **robots.txt + sitemap.xml** 与 **atom.xml**（最新 20 篇订阅源，中文摘要优先），
  同样依赖 `site_url` 配置：
  - robots.txt 含 **AI 爬虫白名单**（2026-10 新增，与主站 zbhgis.com robots.ts 同一份 UA 清单：
    GPTBot / ClaudeBot / PerplexityBot / Google-Extended 等，允许抓取以产出引用）
  - sitemap 的 **lastmod 用真实日期**（2026-10 新增）：文献页=收录日 `added`、首页=最新收录日、
    搜索/统计/周报页=构建日——不用构建时间冒充全站更新（引擎会学会忽略该字段）
- **llms.txt（GEO，2026-10 新增）**：给 AI 引擎的自然语言站点地图——站点定位引言、四个栏目
  链接（首页/搜索/每周速递/统计）、全量文献索引（中文标题 + 链接 + 期刊年份 + DOI）
- **404.html（2026-10 新增）**：构建产出带 `noindex` 的 404 页；nginx 侧 `try_files` 末段用
  `=404`（`deploy/nginx-macrobiodiv.conf` 已同步）——旧写法兜底 `/index.html` 会把所有坏链
  变成 200 首页（软 404），引擎会索引一堆内容相同的 URL

### 3.3 全站搜索 `/search/`

- 无检索后端：在 `papers-data.js` 全量元数据 + `weekly-data.js` 周报全文上做客户端匹配，
  多词空格分隔 = AND（两份来源分别命中、分组展示）
- 检索域（`se`，构建期算好）：id / DOI / 标题(中英) / 作者 / 期刊 / 年月 / 收录日 / 备注 / 出版商 / 类型 / 关键词 / 标签 / **摘要全文(中英)**
- **每周速递检索**（2026-10 新增）：`build_weekly` 产出 `site/assets/weekly-data.js`
  （u 路径 / t 标题 / d 日期 / s 摘要行 / w 字数 / x = `parse_md` 的 plain 全文，
  **截 30000 字符**防无限增长，`<`→`\u003c` 防 `</script>` 截断；仅 /search/ 页经
  `page_shell` 的 `extra_assets` 注入，其他页面零开销）；计数行双口径
  「N 篇文献 · N 期周报」，周报结果行 = 期标题（高亮）+ 日期·收录摘要·字数 + 正文
  命中片段（`wkSnip` 在正文自身内定位首个命中词、前后各取 50/90 字符、`<mark>` 高亮、
  2 行截断），置于「每周速递」分组标题下
- 结果行：主标题（中文优先，命中高亮）+ 元信息行（作者·期刊·年份·DOI·英文原题，可高亮）；`?q=` 可分享

### 3.3.1 全站统计 `/statistics/`（2026-10 新增）

- 数据层 `build_stats_data` → `site/assets/stats-data.js`（window.MBD_STATS）：只带统计所需的
  **英文原文与分面字段**（t 英文原题 / ab 英文摘要 / kw 关键词 / j 期刊 / at 体裁 / y 年份 /
  po 发表日期）—— 中文翻译字段、作者、每周速递一律不进数据；`<`→`\u003c` 防 script 截断；
  仅 /statistics/ 页经 `extra_assets` 注入（stats-data.js + stats.js）
- **访客向数据面板**（2026-10 重设计；此前为筛选+六张纯 CSS 条形卡的管理口径版）：
  页面流 = **hero 总览四 tile**（收录文献/来源期刊/研究关键词，数字滚动 count-up 700ms
  ease-out，reduced-motion 直落；「文献被浏览」来自统计服务）→ **热读文献 Top 5** →
  筛选行 → 图表网格 → 末尾「去全站搜索 / 回首页」CTA 行
- **hero / 热读榜为全量口径**，不随筛选重算：一次 fetch `/api/v1/stats/views?prefix=/macrobiodiv/`
  同时喂 hero「文献被浏览」累计与热读榜（正则 `/^\/macrobiodiv\/[^/]+\/$/` 只留文献卡片页，
  **每周速递两段路径被排除**）；热读榜排序 = 浏览数降序、**同数随机**（`Math.random()` 键），
  行内淡色底条 `--w` = 浏览量占比，No.1 实心章 / 2·3 描边章（`.top/.pod`），副行显示期刊·年份；
  localhost / 无统计服务时 hero 显示「—」、热读榜显示本地预览提示，不发起请求
- **筛选 → 实时重算**（简化版即席查询：全站数据打底，不做 URL 分享与下钻）：
  期刊下拉 / 类型下拉 / **时间区间（两个原生 `type=date` 输入，按 online 发表日期
  年月日比较）** / 重置；筛选行与首页 fgroup 同语言（mono 小标签分组 + 胶囊控件 +
  描边重置，2026-10-05 重设计——此前重置按钮无样式掉浏览器默认外观、年份是
  number 输入）；**月精度的发表日期（YYYY-MM，Crossref 只给到月）按整月区间
  [月初, 月底] 与筛选区间取重叠匹配**（首页日期筛选同口径）；选项来自全量数据；
  过滤 → 聚合 → 图表重渲染，全部客户端完成；命中 0 时隐藏图表网格并出空态
- 图表网格（两栏 grid，≤860px 单栏；`.st-wide` 通栏卡）：
  ① **Online 发表动态**（通栏）：按文献 **online 发表日期**（`po` = `published` 字段；
  注意 `published_online` 在数据里是布尔标注而非日期）逐日累计的 **SVG 面积图**
  （accent 渐变填充），JS 按容器实测像素构建（文字不变形），resize 防抖 180ms 重绘；
  仅真实发表日出点，hover 显示「MM-DD · 累计 N 篇（+K）」（绝对定位 tip，
  getBoundingClientRect 定位）；x 轴刻度首/尾/中三档，端点优先 + 索引去重 +
  中点 44px 间距门槛（跨度 ≤1 天不叠字）
  ② **文章类型构成**：**SVG 环形图**（`r=15.9155` → 周长恰 100，dasharray 直接用百分比；
  中心合计「N 篇 · M 类」），段与图例**双向联动高亮**（互斥 off/加粗 big）；
  **画前 5 类 + 其余合并为「其他」扇区**（2026-10-05 起）——调色板 6 色，
  此前 topN=6 直接截断，第 7 类起的文献不进扇区也不进中心合计，
  出现「hero 103 vs 环形图 102」的口径分裂（实测踩过）；中心 M 类 = 真实类目数
  ③ **期刊 Top 10**：CSS 条形（细轨 + 进场 `stgrow` 生长动画，innerHTML 重建即触发；
  reduced-motion 关闭）
  ④ **研究热词**（通栏 tab 卡）：关键词 / 标题·摘要 两枚胶囊 tab 切换两张词云
  （均随筛选重渲染，tab 仅显隐；**`.st-cloud[hidden]` 需显式 display:none** ——
  作者 display:flex 会压过 UA 的 hidden 隐藏，实测踩过）
  **词云 v2 螺旋布局**（2026-10-04 重做，替代 flex 流式排布）：定高 300px 相对容器
  （≤480px 240px），词沿**阿基米德螺旋**外溢 —— 半径按容器实测宽高归一（宽卡铺满
  不留大空白），**DOM 实测 offsetWidth/Height 包围盒**精确碰撞（PAD 3px，放不下
  缩 15% 重试，兜底左上角）；字号按频次**对数**映射 11–34px（Top 32，窄屏 16 词）；
  颜色按名次分档（头 2 词 accent 蓝 → 绿 → 紫 → 橙 → 长尾 `--faint` 灰阶），
  中后段长词（i≥8 且 i%6=4 且宽>2.2 倍高）**竖排** rotate(-90°)（碰撞盒换轴）；
  布局**完全确定性**（不用 Math.random，主题切换/筛选重绘形状不跳变）；
  入场逐词淡入（stwin 45ms 级联，reduced-motion 关闭），hover 光晕 + 全亮；
  hidden 卡 display:none 无法测量 —— 渲染前临时 `visibility:hidden + display:block`
  展开、完成后复原（`unhideBox`）
  ~~年度发文分布、作者 Top 10 已按 2026-10-04 要求移除~~
  （高频词 = 小写化 → `[^a-z]+` 切词 → 停用词表剪除 → 长度 ≥3）
- **分类调色板**（环形图/图例/hero 顶条共用，`PAL` 6 组暗亮双值，站点 token 同源）：
  #58a6ff/#0969da · #3fb950/#1a7f37 · #a371f7/#8250df · #f0883e/#bc4c00 · #db61a2/#a2306e ·
  #39c5cf/#0a7c84；SVG 颜色构建时写死，**监听 `documentElement` 的 data-theme
  MutationObserver 整页重绘**（主题切换后图表换色）
- 导航「全站统计」占位链接改为真实 `/statistics/`（`is_stats` 按 path 标 active），sitemap 收录

### 3.4 每周速递 `/weekly/`（2026-10 新增，布局移植 mystation 博客）

内容源 `content/weekly/*.md`（front matter：title/date/categoryTags/subCategoryTags，
**date 是补录日期，仅作标题缺期号区间时的兜底，不参与展示**——见下方口径；
正文 `# 文献N` → `## 1.信息/2.摘要/3.图表`，图片为 jsdelivr CDN 外链）。md 经
`scripts/render_md.py`（**标准库迷你渲染器**：标题降级映射 `h2.md-h1`/`h3.md-h2` 带 anchor、
粗/斜体、独立图片行 → `.wk-img` 懒加载、裸 DOI URL 自动链接；先转义再套内联标记，未知语法纯文本兜底）。

- 口径（2026-10-04 起；**2026-10-05 起日期与年月统一自动识别自标题期号区间**）：
  **标题 = 正文第一个一级标题**（TOC 的 `doc-1` 条目，
  文件直接以「文献N」开头时回退 frontmatter title/文件名）；**期号区间从标题提取**
  （`260330-0405`，正则 `(?<!\d)(\d{2})(0[1-9]|1[0-2])(\d{2})-(0[1-9]|1[0-2])(\d{2})(?!\d)`，
  YYMMDD-MMDD、同年省略年份）：**年月标签 = 区间起始月**（`2026年3月`）、
  **展示日期 = 区间起始日**（= 收录周周一，列表行 / 搜索结果 / 排序 / `<time>` 均用它）、
  **详情页「收录周期」= `2026-03-30 ~ 04-05`**——跨月的期不会再出现「3月胶囊配 4月日期」；
  标题无日期段时全部回退 frontmatter date（展示文案退回「创建于」）；
  slug = `weekly-{期号}`（标题正则 `精选(\d+)`）；
  字数 = 去标记字符数（**不含 front matter**）；阅读时长 = `ceil(字数/200)` 分钟（主站同口径）；
  摘要 = 「本期收录 N 篇 · 期刊去重」；**front matter（title/date/categoryTags/subCategoryTags）
  一律不作为渲染与标签来源**——FM_RE 容忍 BOM/前导空白行，parse_md 的纯文本也取剥离后的正文
- **标签行只保留年月**（2026-10-04 起）：文章页 tags 行与列表页条目右侧 tags 均只渲染
  一个 `wk-tag`（年月），`categoryTags`/`subCategoryTags` 不再输出
- **列表页**：`wk-col` 760px → 页头（`wk-kicker` mono 大写 + `wk-deco` 大标题 + lede）→
  `wk-quicknav` 月份锚点 chip（`wk-btn` 带计数）→ 按月倒序分组：`wk-label`（月份 + N 篇）→
  `wk-line` 条目（标题 hover 变 accent + 摘要两行截断 + 右侧 tags（分类 accent + 月份）+ 日期 mono）
- **文章页**：三栏 `wk-shell`（max-1400）—— 左 `wk-side` 250px「文章导航」（sticky、
  <1024px 隐藏、全部 14 篇 `wk-side-link`，当前篇构建期写死 `data-active`）；中 `wk-main`
  760px（`wk-deco` 大标题 → tags 行 → meta 行（作者/字数/时长，12.5px 图标行）→ 日期行 →
  `wk-content` 正文 → `wk-prevnext` 上一篇/下一篇双栏卡 + 虚线空位）；右 `wk-toc` 180px
  「此页内容」（<1280px 隐藏、`wk-toc-link` data-lv 层级缩进 + 导轨竖线、静态锚点无 scrollspy）
- **正文排版 `wk-content`**（blog-content 同源）：`h2.md-h1` 24px border-b、`h3.md-h2` 20px、
  p 16px/1.75、`strong #85a4ff`（主站同源硬编码）、img 圆角+描边、blockquote 左线斜体
- CSS `wk-*` 全部移植自 `mystation/frontend/app/globals.css` 的 v3-*/blog-content，token 换名：
  `--text-primary→--text`、`--text-secondary/muted→--dim`、`--text-faint→--faint`、
  `--hairline→--line`、`--hairline-strong/--border→--line2`、`accent-soft-fade→transparent`
- **静态裁剪**（零 JS）：ViewToggle/RSS、分组分页 Pager/查看更多、侧栏手风琴、TOC scrollspy
  与移动端抽屉不做；`page_shell(bare=True)` 让正文绕开 `.wrap`（全宽三栏自管容器）
- **渲染颜色切换**（2026-10 新增，与 §3.2 文献详情页共用）：meta 行尾五枚小圆点
  （透明斜杠圈 = plain 普通样式：系统色标题无装饰，本页默认；蓝/绿/淡紫/橘实心圈 =
  对应强调色渲染：标题与左竖线换色，去 md-h1 底线，即文献详情的公众号式同构）。
  强调色经 `--rs` 变量统一驱动（详情页的节标题竖线与文字、标题底线、备注线一并换色）：
  蓝沿用 `--accent`；绿 `#3fb950`（亮 `#1a7f37`）· 淡紫 `#a371f7`（亮 `#8250df`）·
  橘 `#f0883e`（亮 `#bc4c00`，亮色取深一档保证对比度）。
  偏好存 localStorage `mbd-rstyle`，**两页共享** —— 任一页切换，另一页下次打开即跟随；
  `<head>` 内联脚本渲染前置位防闪烁，点击后 `location.reload()` 整页重渲染。
  **换色范围 = 正文全部文字相关强调色**：除标题/竖线/底线/备注线外，
  正文作用域（`.pbody` / `.wk-main` / `.back`）内的 `--accent` 整体重映射为 `--rs` ——
  内容链接（含裸 DOI 自动链接）、DOI 与标签胶囊 hover、期刊徽章、周报分类 chip、
  周报加粗字（原主站同源硬编码 #85a4ff）一并跟随；导航/页脚/切换圆点不受影响
  （切换按钮的四枚彩色圆点用字面色值固定，蓝点不随页面换色）。
  CSS 键：`html[data-rstyle=…]` 定义 `--rs` + 作用域覆盖（`.sec-h` ↔ `.wk-content .md-h1/.md-h2`）
- **图片外链策略**（2026-10 定）：文章页图片保持 jsdelivr gh 直链外显（懒加载），不做
  构建期本地化 / 不入库 —— 几千张的量级入库会把 git 仓库撑到 GB 级；可靠性由**前端多源
  自动降级**保证：`page_shell` head 注入全局 `__imgFallback` + 捕获阶段 error 监听，
  gh/用户/仓库@分支/路径 两段式解析，失败按 cdn → fastly → gcore → raw.githubusercontent
  逐源切换（`data-orig-src` 记原始 URL、`data-src-try` 记游标）；非 jsdelivr 图不处理
- sitemap 追加 `/weekly/` 与各文章页；og:type=article + 正文首图 og:image

### 3.5 管理后台 `scripts/admin_ui.html`（127.0.0.1:5201）

沿用站点 token 与 GeoSciPlot 的 mono 工程风，按本站功能重新组织：

- **品牌头**：favicon 圆角图标 + 标题（与站点 logo 同源）
- **分节标题**：h2 带 accent 竖标（延续详情页 .sec-h 语言）
- **添加文献**：DOI 输入区（虚线框，粘贴 / 导入 .txt，**同时识别粘贴 / 拖入的图片**）
  → **封面待配区**（DOI 框下的缩略图条，抓取成功后按顺序自动配给新文献 —— GeoSciPlot
  「标准导入」同款配对；点缩略图移除）→ 抓取结果卡片（右列首位为封面区块）
  → 发布区（成组卡片 .pubbox，含提交信息与推送/同步开关）→ 任务日志（实时步骤）
- **封面图交互**：每篇卡片的封面区块支持拖拽 / Ctrl+V / 点选三种上传路径（共享 file input
  经 `coverPick` 定位目标，无目标则进待配区）；上传立即经 `POST /api/cover` 落盘
  （服务端**魔数嗅探**真实格式，PNG/JPEG/WebP/GIF、≤20MB、id 白名单防路径穿越），
  存 `assets_src/covers/{id}.{ext}`；预览走 `GET /api/cover/{id}`；
  「移除（不入库）」与删除文献都会连带删封面文件；文献管理编辑表单同样有封面区块，
  行列表有缩略图；发布 / 更新 / 重新抓取时服务端按 id **探测**封面文件写 `cover` 字段
  （不信任客户端路径，文件在才是真相；换格式上传自动清旧格式文件）
- **封面外链（2026-10 新增）**：封面区块第三枚按钮「外链」，贴 http/https 图片 URL
  直接写入 `cover` 字段（`set_cover_url`，与周报管线「外链直显」同模式，**不落盘**；
  本地旧封面文件一并清掉 —— 编辑保存以磁盘文件为准，留着文件外链不会生效）。
  已入库文献立即写 papers.json；新文献尚未入库时先记在条目上，发布时随条目写入
  （服务端仅在本地文件存在时才覆盖 `cover`）。预览：UI 对外链直接用 URL；
  行列表缩略图走 `GET /api/cover/{id}`，本地无文件时 **302 到外链**。
  「移除封面」= 删文件 + 清外链字段（`clear_cover_url`），否则保存后 URL 又会回来
- **抓取结果卡片**：左列 = 与站点卡片同一徽章语言（期刊徽章 + 体裁 chip + 日期顶行、
  英文标题粗体、中文标题副行、作者/卷期/id、**翻译状态徽章**
  「✓ 初译+审校 / ◑ 仅初译 / ✗ 失败」，悬停看原因，摘要 4 行截断）；
  右列 = 可编辑手动字段（中文标题 / 文章类型（datalist）/ 关键词 / 标签 / 中文摘要 / 备注）
- **文献管理**：行卡片同一徽章语言；标题中文优先 + 英文副行；
  操作：编辑 / 重新抓取 / 重新翻译 / 删除；筛选：搜索 + 标签 chips + 期刊下拉 + 日期区间
- **多选批量删除**（2026-10 新增，文献管理与每周速递共用同款交互）：行首勾选框
  （`.mrow.msel` 三列网格，两类行通用）；文献勾选状态存 `mSelected`（按 id）、
  期次存 `wkSelected`（按文件名），跨筛选 / 重渲染保留，列表刷新后剔除已不存在的条目；
  「全选」文献侧只作用于当前筛选结果（期次列表无筛选即全部期次），
  部分选中时全选框呈 indeterminate；「删除所选」按钮随勾选数实时显数，
  经页内确认列出标题 / 期次清单（最多 8 条）后走与单条删除相同的后台任务 ——
  `POST /api/delete` 入参 `ids` 数组（兼容旧的单 `id`），`do_delete` 逐条移除
  papers.json 条目并连带删封面文件；`POST /api/weekly-delete` 入参 `names` 数组
  （兼容旧的单 `name`），只删 content/weekly/*.md、已生成卡片保留。
  部分成功也算成功、逐条记日志；默认提交信息单期/单条沿用旧格式
  （`delete: 文献 {id}` / `weekly: 删除 {file}`），多条为
  `delete: 文献 N 篇（id…）` / `weekly: 删除 N 期（file…）`
- **每周速递**（2026-10 新增）：上传周报 md（点选 / 拖入，可多选）→ 三步自动流水线：
  ① md 落盘 `content/weekly/`（周报页随构建自动收录）；② 解析「# 文献N」小节
  （`render_md.parse_weekly_papers`，见 §3.4），新 DOI 走 Crossref+OpenAlex 抓取入库，
  **周报自带中文直接预填**（标题行首个 CJK 起为中文段 → title_zh；摘要节 → abstract_zh；
  「文献N」注记体裁 → article_type，不走大模型，空缺事后用「翻译缺中文的」回补）；
  ③ 封面：图表图**外链直显**（2026-10 由「下载落盘」改为 cover 字段存完整 jsdelivr URL，
  不下载不入库 —— 可靠性靠前端 `__imgFallback` 多源降级：cdn → fastly → gcore →
  raw.githubusercontent；**图表为无 → 站点 logo 兜底**，`assets_src/logo.png` 复制为
  `covers/{id}.png`，字节与 logo 相同即「兜底标记」—— 同期重传时若解析到图表图，
  已入库条目的封面自动升级为外链）。上传前先 `/api/weekly-parse`
  同步预解析出确认弹窗（新 N 篇 / 跳过 N 篇 / 封面策略 / 期号冲突警告），确认后进
  后台任务（构建 + 推送 / 同步沿用发布区开关）。期次列表显示每篇 DOI 的收录状态
  （● 已入库 · ○ 待生成 · × 无 DOI）；同名文件重传 = 覆盖更新该期（库内 DOI 自动跳过）；
  「删除此期」与勾选多选「删除所选」（见上方多选批量删除）只删周报页，已生成卡片需到文献管理单独删
- **通用组件**：页内确认弹窗（替代原生 confirm，支持取消回调）、toast、任务日志步骤条
  （…/✓/✗，三个标签页各有独立日志区）、同批重复 DOI 客户端即时标出
- **封面兜底可视化**（2026-10 新增）：封面为 logo 兜底的条目处处可辨 —— `/api/items` 逐篇
  附 `coverLogo`、`/api/weekly` 逐期附 `nLogo`；文献管理行卡片挂橙色「logo 兜底」chip +
  指引行（重传该期周报自动补真图），期次列表显示「⚠ N 篇封面为 logo 兜底」，
  未上传封面显示中性「无封面」chip（站点卡片为占位块，非故障）
- **任务运行提示**：上传周报的确认弹窗写明耗时与「勿关本页面 / 后台程序」（任务在
  daemon 线程，进程一关即中断、索引不落盘）；任务执行期间日志区底部常驻
  「⏳ 后台任务执行中」提示行，完成与否以「收录完成」步骤为准
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
    "abstract": "...", "cited_by": 246, "source": "crossref+openalex",
    // ── LLM 生成字段（重新翻译时重写）──
    "title_zh": "...", "abstract_zh": "...", "article_type": "Research Article",
    // ── 手动字段（永不被自动流程覆盖）──
    "keywords": ["sea ice", ...],    // 抓取自动预填（OpenAlex 词表截前 10，
                                     // Crossref subject 兜底），可手动修改；
                                     // 重新抓取只回填空缺、不覆盖已填值
    "tags": ["样点分布图"], "note": "",
    "cover": "covers/{id}.png",      // 封面图（可选），两种取值：
                                     // ① 本地：相对 assets/ 的路径（上传/周报 logo 兜底），
                                     //    服务端按 assets_src/covers/ 实际文件探测得出，
                                     //    上传/移除即落盘，发布/更新/重抓时自动增删此字段
                                     // ② 外链：完整 http(s) URL（周报图表图直显，
                                     //    不落盘；探测不到本地文件也不会被清除，
                                     //    前端 __imgFallback 提供多源降级）
    "cover_thumb": "https://cdn.jsdelivr.net/gh/zbhgis/BlogImg@main/blog/thumb/{id}.webp",
                                     // 缩略外链（可选，2026-10-05 新增）：图床上的 webp
                                     // 缩略图，**卡片优先引用**（详情页/og:image 保持原图）；
                                     // 由 scripts/prepare_thumbs.py 生成并写回；
                                     // 缺失时卡片回退 cover（行为与历史版本一致）
    "cover_thumb_w": 480, "cover_thumb_h": 327,
                                     // 缩略图像素尺寸（prepare_thumbs 写回）：卡片
                                     // <img width/height> 预留位用 —— 加载前浏览器
                                     // 按同比例撑出「加载中」占位层，零抖动
    // ── 统计 ──
    "added": "2026-09-19"
  }]
}
```

字段优先级规则：**手动 > LLM > 自动回落**。GeoSciPlot 的 titles.csv 副本被有意去掉，
papers.json 单一数据源避免双写不同步。

#### 封面图管线（GeoSciPlot 图片上传的同源移植）

```
粘贴/拖拽/点选图片 → base64 → POST /api/cover → 魔数校验 → assets_src/covers/{id}.{ext}
                                                                    │（随 git 入库）
发布/更新时 detect_cover(id) ←──── 同 id 一一对应 ────┘
        ↓ 写 papers.json 的 cover 字段
build_site.py：整体拷到 site/assets/covers/（镜像式，删除不残留）
        ↓
卡片自然比例通栏顶图（无图占位块） · 详情页大图 · og:image · JSON-LD image
```

- 一篇文献至多一张封面（文章封面 / 图形摘要语义），重传即覆盖；id 由 DOI 派生，
  封面与文献的关联天然稳定，无需额外索引
- **原图不压缩/转码**：零第三方依赖约束下，站点构建（build_site.py）与管理后台
  （admin.py）不引 Pillow，原图原样入库（20MB 上限兜底，周报图表原图常超 10MB）；
  体积优化交给**缩略图管线**（下条）
- **缩略图管线（2026-10-05 新增，学 GeoSciPlot scripts/prepare.py）**：
  `scripts/prepare_thumbs.py` 把外链封面压缩成 webp（**长边 ≤480 · quality 78 ·
  LANCZOS · EXIF 转正 · 透明垫白**，实测 2MB PNG → 30-60KB），存图床仓库
  **zbhgis/BlogImg**（本地克隆 `E:/AAAproject/BlogImg`）的 `blog/thumb/{id}.webp`
  并 `git push`，把缩略外链与像素尺寸（`cover_thumb_w/h`，供卡片预留位）写回
  papers.json。**Pillow 仅此工具需要**
  （site 构建/管理后台仍零依赖）；增量运行（跳过已有 cover_thumb）、`--force` 全量、
  `--push` 提交推送、`--git-proxy` 给 push 挂本地代理（github 直连不通时）。
  卡片引用顺序：`cover_thumb` → `cover`（缩略图加载失败先回退原图外链 data-full，
  再走多源降级，最后占位块）；详情页 / og:image / JSON-LD 始终原图。
  **已并入发布任务自动执行（2026-10-05）**：`pipeline_after_content` 第一步以
  `--push --quiet` 调用本脚本（超时 580s），在 build 之前补齐新封面缩略图；
  **推送成功才写 cover_thumb**（否则站点会引用 CDN 上不存在的文件）；push 直连
  失败自动改走内置兜底代理重试；失败不阻断发布，下次发布自动重试。
  手动运行仅作应急/全量重建：`python scripts/prepare_thumbs.py --push`

## 5. DOI 抓取管线（fetch_doi.py）

1. **规范化**：从任意文本提取 DOI（`10.x/...`，剥 doi.org 前缀），按「DOI 只含 ASCII」
   截断防全角标点混入，URL 侧再做一次 quote 兜底
2. **分层**：Crossref（`works/{doi}`，mailto 礼貌池）主力 → OpenAlex（`works/doi:{doi}`）补
   摘要（还原倒排索引）/ 关键词 / 被引 / 体裁提示；**不抓出版社页面**（Cloudflare 反爬，稳定优先）
3. **时间口径**：`published-online` → `published` → `issued`（一篇文献有多个日期，全站统一
   以 online 为准；无 online 记录时回落并以 `published_online` 标记如实呈现）
4. **关键词**：OpenAlex keywords（基于标题 / 摘要抽取的词表，与作者关键词高度重合，
   截前 10 个防长尾）自动采集，Crossref subject（出版社学科分类，多数不填）兜底；
   管理端可手动修改 —— keywords 仍属手动字段，重新抓取只在空缺时回填、绝不覆盖
5. **HTML 实体反转义**：部分出版社的 deposit 元数据自带 HTML 实体（如期刊名
   「Nature Ecology &amp; Evolution」），`_finalize` 对全部字符串字段统一
   `html.unescape`（摘要此前已在 `_clean_abstract` 处理），避免站点 `esc()`
   二次转义出现「&amp;amp;」；BibTeX 输出（`_bib_escape`）也随之拿到干净的 `&`

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

- 数据与索引随 GitHub 仓 `zbhgis/MacroBiodiv`；封面图存 `assets_src/covers/` 同样入库，
  构建时拷入 `site/assets/covers/`，生产包为 HTML/JS + 封面图片
- **图片流量不经站点服务器（2026-10-05 起）**：卡片优先引用图床缩略外链
  （`zbhgis/BlogImg@main/blog/thumb/{id}.webp`），详情页大图为原图外链——
  nginx 只出 HTML/JS/JSON 与本地封面兜底文件；缩略图由
  `scripts/prepare_thumbs.py --push` 维护（见「封面图管线」小节），部署本身零图片改动
- nginx：`deploy/nginx-macrobiodiv.conf`（webroot /var/www/macrobiodiv，/api/ 反代主站
  FastAPI 做浏览统计；安全响应头 nosniff / SAMEORIGIN / Referrer-Policy；
  /assets/ 因带 ?v= 版本号放行 30d 长缓存）；DNS / 证书步骤见 `deploy/部署操作手册.md`
- 端口约定：**管理后台 5201**（5200 被 MultiColor 的管理后台占用），本地预览 7332
- 浏览统计沿用主站 tracker：打点 `/macrobiodiv/{path}`，读取 `?prefix=/macrobiodiv/`

## 8. 设计决策记录

| 决策 | 原因 |
|---|---|
| 纯标准库 + 静态站，无框架 | 与 GeoSciPlot 同构，零运维，服务器只跑 nginx |
| 首页卡片瀑布流（CSS multi-columns，2026-10 由等宽 grid 改入） | 封面升级为卡片常规要素（封面+标题+类型+期刊），自然比例错落成瀑布流，与 GeoSciPlot 保持同源；无封面用期刊缩写占位块兜底版式 |
| 封面存 assets_src/covers + 探测式写库；周报图表封面走外链（2026-10） | site/ 是构建产物不入库；本地封面由服务端按磁盘文件探测，杜绝客户端伪造路径与状态漂移；周报图表图量大会撑爆仓库 → cover 存 jsdelivr URL 外链直显（探测保 URL 不清除），前端多源降级 |
| 封面原图不压缩/转码，缩略图走图床管线（2026-10-05 起） | 零第三方约束限定在站点构建与管理后台（不引 Pillow），压缩交给独立工具 `scripts/prepare_thumbs.py`（Pillow 仅此工具需要）；原图 20MB 上限兜底，缩略图长边 480 webp 存 BlogImg 图床，卡片引用缩略外链（首屏图片流量 60MB → 0.7MB），缺失时回退原图直显 |
| 首屏静态输出 + JS 重渲染双轨 | 爬虫/AI 引擎可见 + 交互灵活；两者排序逻辑必须一致 |
| 文章图片外链直显 + 前端多源降级（2026-10 定） | 几千张规模：入库/本地化会把仓库或部署包撑到 GB 级；外链零存储负担，可靠性靠 cdn → fastly → gcore → raw 逐源自动切换 |
| 菜单栏 active 构建期静态判定 | `path` 在生成时已知，无需 JS 参与；静态首屏与 JS 渲染天然一致 |
| 菜单栏右端只放一枚占位按钮 | 用户要求预留（功能未定）；样式取主站 `.v3-nav-icon` 语言，之后挂功能只动 `page_shell` 一处 |
| 详情页中文标题/摘要优先 | 站点受众为中文读者；英文原题保留（副行/折叠/悬停）保证溯源 |
| 不抓出版社页面 | Cloudflare 反爬不可靠；代价是出版社精确栏目名需手动补（关键词已改由 OpenAlex 词表自动采集） |
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
- logo/图标：优先用户源图 —— `assets_src/logo_source.png` 存在时，`gen_logo.py`
  由它派生 `logo.png`（居中裁方、保持原分辨率）与 `favicon.png`（64），重跑不会覆盖
  用户 logo；源图不存在时回落纯几何绘制（4x 超采样）。站点 header 的 `.logo` 出
  `border-radius:12px` 圆角贴纸观感（admin 品牌头 10px 同语言）
