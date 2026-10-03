<p align="center">
  <img src="assets_src/logo.png" alt="MacroBiodiv" width="160">
</p>

<h1 align="center">MacroBiodiv</h1>

<p align="center">宏观生物多样性文献库 —— 粘贴 DOI 自动建站：抓取元数据、大模型中文化、卡片浏览、可搜索可订阅。</p>

<p align="center"><a href="https://macrobiodiv.zbhgis.com">macrobiodiv.zbhgis.com</a> · <a href="DESIGN.md">设计文档</a> · <a href="https://github.com/zbhgis/GeoSciPlot">GeoSciPlot</a>（同作者姐妹项目）</p>

## 这是什么

一个**纯 Python 标准库 + 纯静态站**的文献展示系统：本地管理界面里粘贴 DOI，
自动抓取元数据（Crossref + OpenAlex）、调用你自己的大模型做中文翻译（初译 + 审校），
同时可上传**封面图**（粘贴 / 拖拽 / 点选，与文献按顺序自动配对），
一键发布成可搜索、可筛选、可订阅的静态网站。无框架、无数据库、无服务端运行时。

## 维护者 · 日常维护

```bash
python scripts/admin.py        # 本地管理界面（仅本机可访问，自动打开 127.0.0.1:5201）
```

流程：**粘贴 DOI（+ 粘贴/拖入封面图）→ 自动抓取 → 大模型中文化（初译+审校）
→ 补标签/备注 → 点发布**。
发布自动执行：写 `meta/papers.json` → 构建站点 → git push →（可选）同步服务器。

## 站点功能

- **顶部菜单栏**：与主站 zbhgis.com 同源的吸顶毛玻璃导航 —— 站点名居左，右侧带图标的
  「每周速递 / 全站统计 / 更多▾」（每周速递为周报栏目，全站统计为预留入口，「更多」悬停展开
  zbhgis 与 GeoSciPlot 外链，窄屏自动转汉堡菜单）；
- **每周速递 `/weekly/`**：Nature / Science / Cell 系列大尺度生物多样性研究每周精选
  （按月分组列表 + 文章页三栏版式：文章导航 / 正文 / 此页内容 TOC），布局与主站 zbhgis.com
  博客同源；内容为 `content/weekly/` 的 Markdown，标准库迷你渲染器转 HTML
- **周报上传即收录**：管理后台「每周速递」上传周报 md 后自动两件事 —— md 落盘
  `content/weekly/` 收进周报页；同时解析每篇「文献N」的 DOI 与图表图，新 DOI 抓取元数据
  生成主页卡片（周报自带的中文标题 / 摘要 / 体裁注记直接预填），图表图下载为文献封面
  （jsdelivr 多源自动重试：cdn → fastly → gcore → GitHub 源站，全失败才用站点 logo 兜底，
  重传同一期会自动补下真图覆盖），生成的卡片与手动添加的文献在「文献管理」统一管理
- **文献卡片瀑布流**：封面通栏顶图（自然比例，无封面显示期刊缩写占位块）+ 文章类型 + 期刊徽章 + 标题（中文优先），
  CSS multi-columns 错落布局（4/3/2 列响应式，GeoSciPlot 同源样式）
- **封面图**：管理界面识别粘贴 / 拖拽 / 点选的图片作为文章封面（PNG/JPEG/WebP/GIF，≤8MB），
  可与 DOI 按顺序批量配对；前台展示在卡片顶图与详情页，并写入 og:image（分享出封面卡片）
- **图片灯箱**：详情页与周报文章页点击图片即放大（零依赖纯前端模块，仅这两种页面加载）——
  同容器多图切换（箭头 / 方向键 / 触摸滑动）、滚轮 / 双击 / 双指缩放、拖拽平移、Esc 或点空白关闭，
  相邻图预加载秒开；链接内图片默认不劫持，`data-lightbox-src` 可指定高清版
- **文章目录 TOC**：详情页与周报文章页右侧「此页内容」——桌面端 sticky 侧栏 + 移动端浮动按钮
  与全高抽屉，滚动时自动高亮当前小节，点击平滑滚动并更新锚点；零依赖纯前端，
  与图片灯箱同为仅文章页加载的独立模块
- **全站统计 `/statistics/`**：年度分布 / 期刊 / 类型 / 关键词 / 作者 / 高频词六组图表
  （基于英文原文，不含每周速递），可按期刊 / 类型 / 年份区间筛选后实时重算；
  纯 CSS 条形图，零依赖，构建期生成数据、客户端聚合渲染
- **三维筛选 + 全站搜索**：标签 / 期刊 / 时间区间；标题 / 作者 / DOI / 期刊 / 关键词 / 摘要全文；
  搜索同时覆盖**每周速递各期正文**（命中行带上下文片段与高亮）；分页 20/30/50
- **筛选与排序均可通过 URL 分享**：`/?tag=海冰&journal=Nature&sort=rand`（sort 仅本次生效，不改写访客偏好；
  排序为 发表新→旧 / 旧→新 / 随机洗牌 —— 站点不再携带与展示被引数据）
- **详情页**为公众号推文式分节版式（**1. 信息 / 2. 摘要 / 3. 图表 / 4. 引用**，对齐「浩瀚地学」
  文献精选排版：窄栏阅读、居中标题块、左竖线节标题、字段行式信息区、图表淡蓝光晕）：
  中文摘要为主阅读区、英文原题收合、GB/T 7714 引用条 + BibTeX 一键复制、上一篇/下一篇
- **渲染颜色切换**：文献详情页与每周速递文章页头部各有一排切换圆点
  （透明斜杠圈 = 普通样式：系统色标题；蓝 / 绿 / 淡紫 / 橘实心圈 = 对应强调色渲染：
  标题、节标题、正文链接、加粗字、徽章、胶囊等文字相关颜色整体换色，
  亮色主题自动取深一档色值），点击刷新生效，偏好两种页面共享 —— 任一页切换，另一页同步跟随
- **文章类型**按出版社惯例标注（Article / Research Article / Perspective / Comment / News & Views…），
  大模型结合元数据体裁提示判定，管理界面可修改
- **分享与收录**：Open Graph 卡片、ScholarlyArticle JSON-LD、robots.txt / sitemap.xml / Atom 订阅源
- 右侧悬浮按钮队列：搜索 / Home / GitHub / 明暗主题 / 回到顶部（与主站 zbhgis.com 同款）
- 浏览计数走主站统计 API；明暗双主题

## 大模型中文化

抓取时自动翻译标题与摘要（**两重处理：初译 → 审校复核**），走 OpenAI 兼容接口：

```powershell
[Environment]::SetEnvironmentVariable("LLM_API_KEY", "sk-xxx", "User")
[Environment]::SetEnvironmentVariable("LLM_BASE_URL", "https://token.sensenova.cn/v1", "User")
[Environment]::SetEnvironmentVariable("LLM_MODEL_NAME", "sensenova-6.8-flash-lite", "User")
```

- 设置后**新开的终端**直接生效；已开着的进程自动从注册表兜底读取
- 译文（中文标题 / 中文摘要）在管理界面可直接修改，修改稿视为定稿：
  重新抓取 / 刷新被引不会覆盖，只有「重新翻译」会重写
- 同时判定**文章类型**（按出版社惯例），可在界面修改
- **限流与故障分层预案**（详见 [DESIGN.md](DESIGN.md) §6）：请求节流 → 429 指数退避 +
  Retry-After → 断路器冷却 → 审校失败降级「仅初译」→ 初译失败留空正常发布 →
  「翻译缺中文的」批量幂等补译。任何失败都不阻断抓取与发布
- 固定 prompt 集中在 `scripts/llm.py`（`_TRANSLATOR_SYSTEM` / `_REVIEWER_SYSTEM`），
  含术语锚点表 `_GLOSSARY`，换研究领域改这一处

## 数据来源与口径

- 元数据抓取自 **Crossref**（主力）与 **OpenAlex**（摘要兜底 / 关键词 / 被引 / 体裁提示），
  不抓出版社页面
- **时间以在线发表（online）日期为准**：同一篇文献的 print / online / issued 日期可能不同，
  全站（徽章 / 筛选 / 排序）统一取 online，无 online 记录时回落并以标记如实呈现
- **关键词自动抓取、可手动改**：取 OpenAlex keywords 词表（基于标题 / 摘要抽取，
  与作者关键词高度重合，截前 10 个），Crossref subject 兜底；管理界面可修改，
  重新抓取只回填空缺、不覆盖已填值；详情页关键词胶囊点击直达全站搜索
- 文献 id = DOI（小写）sha1 前 10 位；`meta/papers.json` 是唯一数据源

## 本地运行

**只想本地看看效果（任何人）**

```bash
git clone https://github.com/zbhgis/MacroBiodiv.git
cd MacroBiodiv
python scripts/build_site.py            # 生成静态站（Python 3.9+，无第三方依赖）
cd site && python -m http.server 7332   # 打开 http://127.0.0.1:7332，搜索页在 /search/
```

**维护者（需要仓库写权限）**

```bash
git clone https://github.com/zbhgis/MacroBiodiv.git
cd MacroBiodiv
python scripts/admin.py                 # 打开 http://127.0.0.1:5201
```

管理界面三个标签页：**添加文献**（粘贴 DOI → 抓取 → 补手动字段 → 发布）、
**每周速递**（上传周报 md → 周报页收录 + 解析 DOI/图表自动生成文献卡片，
期次列表支持勾选多选批量删）、**文献管理**（编辑 / 重抓 / 补译 / 刷新被引 / 删除，
支持勾选多选批量删，「全选」作用于当前筛选结果）；发布 = 写索引 → 构建站点
→ git 提交推送 →（可选）同步服务器。

命令行（不进管理界面也能用）：

```bash
python scripts/fetch_doi.py 10.1038/s41467-021-24264-9   # 试试抓取，输出 JSON
python scripts/llm.py "英文标题"                          # 试试翻译
python scripts/build_site.py                              # 重建站点
python scripts/gen_logo.py                                # 由 assets_src/logo_source.png 重新派生 logo/favicon
```

## 服务器部署

与 [GeoSciPlot](https://github.com/zbhgis/GeoSciPlot) 同一台机器、同一套流程：
DNS 子域 A 记录 → acme.sh 签证书 → 挂载 `deploy/nginx-macrobiodiv.conf`
（含安全响应头与静态资源长缓存）→ `cd site && scp -r ./* root@<服务器>:/var/www/macrobiodiv/`。
逐步命令见 [`deploy/部署操作手册.md`](deploy/部署操作手册.md)。

## 项目文档

- [DESIGN.md](DESIGN.md) —— 设计决策与实现规范（视觉体系 / 页面结构 / 数据模型 /
  抓取管线 / LLM 行为 / 管理后台设计），**改动须同步更新**
- [AGENTS.md](AGENTS.md) —— 维护会话约定（文档同步 / 设计基线 / 工程约定）

## 鸣谢

站点架构与样式移植自 [GeoSciPlot](https://github.com/zbhgis/GeoSciPlot)（同作者）；
DOI 抓取策略参考 [doi2md](https://github.com/zbhgis/doi2md)。
文献元数据来自 [Crossref](https://www.crossref.org/) 与 [OpenAlex](https://openalex.org/)，
版权归原出版方。
