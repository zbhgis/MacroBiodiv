# MacroBiodiv · 维护者手册

> 面向仓库维护者的操作文档：本地管理界面、发布流水线、踩坑清单、部署与大模型配置。
> 用户向介绍见 [README.md](README.md)；设计决策与实现规范见 [DESIGN.md](DESIGN.md)。

## 维护者 · 日常维护

```bash
python scripts/admin.py        # 本地管理界面（仅本机可访问，自动打开 127.0.0.1:5201）
```

流程：**粘贴 DOI（+ 粘贴/拖入封面图）→ 自动抓取 → 大模型中文化（初译+审校）
→ 补标签/备注 → 点发布**。

管理界面三个标签页：**添加文献**（粘贴 DOI → 抓取 → 补手动字段 → 发布）、
**每周速递**（上传周报 md → 周报页收录 + 解析 DOI/图表自动生成文献卡片，
期次列表支持勾选多选批量删，**列表翻页每页 20 期**）、**文献管理**（编辑 / 重抓 / 补译 / 删除，
支持勾选多选批量删，「全选」作用于当前筛选结果；**列表翻页每页 20 条**，
筛选变更自动回第 1 页，跨页勾选保留）；发布 = 写索引 → 构建站点
→ git 提交推送 →（可选）同步服务器。后台任务进度区在标签页上方置顶显示
（三标签页共用，长列表滚动也可见）。

### 发布流水线（点「发布」后自动执行）

1. **封面缩略图增量补齐**：有新封面时自动下载 → 压 webp → 推图床
   （`zbhgis/BlogImg`）→ 写回 `cover_thumb`；失败不阻断发布（卡片回退原图外链，
   下次发布自动重试），无新封面时秒过；
2. 写 `meta/papers.json` → `build_site.py` 构建静态站（robots/sitemap/llms.txt/404 页/
   IndexNow key 文件一并产出）——**检索数据（papers-data.js / weekly-data.js）随构建
   全量再生，发布即最新，无需单独重建索引**；
3. `git add -A` → 提交 → 推送 GitHub（网络慢时超时放宽到 5 分钟）；
4. **staging 原子换台同步服务器**（勾选"同步到服务器"时）：`site/` 整体 scp 到服务器
   `.staging` 目录，成功后两连 `mv` 就位——发布期间旧版本完整在线，scp 失败线上原样
   保留（旧的「清空 webroot → 逐文件 scp」有分钟级空窗，期间站点 403/资源 404，
   2026-10-04 弃用）；
5. 调用主站的 `ping-search.sh` 做 IndexNow 增量推送（key 文件随构建部署在本站根目录）。

**每周速递上传联动**：上传周报 md 后自动两件事——md 落盘 `content/weekly/` 收进周报页；
同时解析每篇「文献N」的 DOI 与图表图，新 DOI 抓取元数据生成主页卡片。周报页的
标题取正文第一个一级标题、年月标签从标题的周区间提取（如 260309 → 2026年3月）、
frontmatter 只用于「创建于」展示——md 带不带 frontmatter 都能正确处理。
**中文摘要自动取自周报**：新文献直接预填标题/摘要/体裁；已在库但中文空缺的（如先前经
「添加文献」入库、LLM 未跑成）重传同一期时自动回填——只补空缺，绝不覆盖已有译文，
周报标注「无」的不生成（这类可事后用「翻译缺中文的」走 LLM 补齐）。

### 发布前必查（踩坑清单）

- **新增/更换了封面 → 先跑 `python scripts/prepare_thumbs.py --push --git-proxy http://127.0.0.1:7897`**
  （增量，只为新封面生成缩略图并推图床；忘跑不会坏——线上卡片回退原图外链，只是每张 2MB 级）。
- **`git add -A` 会收编工作区全部改动**：发布提交是全量的——先 `git status`
  确认没有无关的半成品文件，或先把它们单独提交/清理。
- **发布失败不影响线上**：build 失败 → 未提交未同步；scp 失败 → 线上保持旧版本且
  残局自动清理。日志区每步状态可见：push 失败多半是网络波动，稍后手动 `git push`
  （连不上可挂代理：`git -c http.proxy=http://127.0.0.1:7897 push`）；
  同步失败检查本机公钥是否在服务器 `authorized_keys`。
- **nginx 配对关系**：构建产出的 `404.html` 依赖服务器 nginx 的
  `error_page 404 /404.html` + `try_files ... =404`（留档在 `deploy/nginx-macrobiodiv.conf`，
  与服务器现行版一致）——别改回 `/index.html` 兜底，那会把坏链变成 200 首页（软 404）。
- **文档同步**：改构建逻辑/页面结构/数据模型后，按 AGENTS.md 约定同步 DESIGN.md
  对应小节（及本文件的 功能/使用描述）再提交。

### 手工部署（应急/全量重传）

```bash
python scripts/build_site.py            # 生成 site/
scp -r site/. root@47.98.133.104:/var/www/macrobiodiv/
```

> 注意手工 scp **不删除**服务器上已下线的旧文献目录（会累积失效页）——日常发布走
> 管理界面（staging 换台自带清理 + 搜索推送）。

### 发布后验证

首页 200、任一文献详情页 200 且带 canonical、`/weekly/` 列表按月分组、
IndexNow key 文件 200、随机坏路径返回**真 404**（不是首页）。
全站无服务器编译——构建永远在本地做，服务器只放静态文件。

## 大模型中文化

抓取时自动翻译标题与摘要（**两重处理：初译 → 审校复核**），走 OpenAI 兼容接口：

```powershell
[Environment]::SetEnvironmentVariable("LLM_API_KEY", "sk-xxx", "User")
[Environment]::SetEnvironmentVariable("LLM_BASE_URL", "https://token.sensenova.cn/v1", "User")
[Environment]::SetEnvironmentVariable("LLM_MODEL_NAME", "sensenova-6.8-flash-lite", "User")
```

- 设置后**新开的终端**直接生效；已开着的进程自动从注册表兜底读取
- 译文（中文标题 / 中文摘要）在管理界面可直接修改，修改稿视为定稿：
  重新抓取不会覆盖，只有「重新翻译」会重写
- 同时判定**文章类型**（按出版社惯例），可在界面修改
- **限流与故障分层预案**（详见 [DESIGN.md](DESIGN.md) §6）：请求节流 → 429 指数退避 +
  Retry-After → 断路器冷却 → 审校失败降级「仅初译」→ 初译失败留空正常发布 →
  「翻译缺中文的」批量幂等补译。任何失败都不阻断抓取与发布
- 固定 prompt 集中在 `scripts/llm.py`（`_TRANSLATOR_SYSTEM` / `_REVIEWER_SYSTEM`），
  含术语锚点表 `_GLOSSARY`，换研究领域改这一处

## 数据来源与口径

- 元数据抓取自 **Crossref**（主力）与 **OpenAlex**（摘要兜底 / 关键词 / 被引 / 体裁提示），
  不抓出版社页面——两个例外：`10.1038`（Nature 系）双源皆无摘要时抓 nature.com 文章页的
  摘要 meta；仍无时走学术聚合 API（Semantic Scholar → Europe PMC → PubMed）——
  当月最新非 OA 文可能尚未进任何索引，这类等收录后「重新抓取」即可补上
- **时间以在线发表（online）日期为准**：同一篇文献的 print / online / issued 日期可能不同，
  全站（徽章 / 筛选 / 排序）统一取 online；Elsevier / AAAS 等不提供 online 记录时，以
  Crossref 的 DOI 注册日（即真实在线日）兜底，并以标记如实呈现
- **关键词自动抓取、可手动改**：取 OpenAlex keywords 词表（基于标题 / 摘要抽取，
  与作者关键词高度重合，截前 10 个），Crossref subject 兜底；管理界面可修改，
  重新抓取只回填空缺、不覆盖已填值；详情页关键词胶囊点击直达全站搜索
- 文献 id = DOI（小写）sha1 前 10 位；`meta/papers.json` 是唯一数据源

## 命令行工具（不进管理界面也能用）

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

## 站点功能补充说明

- **顶部菜单栏**：与主站 zbhgis.com 同源的吸顶毛玻璃导航——「每周速递 / 全站统计 /
  关于本站 / 更多▾」，窄屏自动转汉堡菜单
- **每周速递列表翻页**：每页最多 3 个月（页码条切换、月份 chip 跳转、`#pN` 记位）；
  文章图片 jsdelivr 外链直显 + 前端多源自动降级（cdn → fastly → gcore → GitHub 源站）
- **筛选与排序 URL 分享**：`/?tag=海冰|冻土&journal=Nature|Science&sort=rand`
  （多值 `|` 分隔，旧单值链接兼容；sort 仅本次生效，不改写访客偏好；
  排序为 发表新→旧 / 旧→新 / 随机洗牌——站点不携带与展示被引数据）
- **渲染颜色切换**：文献详情页与周报文章页头部圆点（斜杠圈 = 系统色；蓝/绿/淡紫/橘 =
  对应强调色渲染），偏好两种页面共享
- **周报收录的封面策略**：图表图 jsdelivr 外链直显（不落盘不入库，前端多源降级兜底），
  图表为无用站点 logo 兜底——重传该期周报自动补真图

## 项目文档

- [DESIGN.md](DESIGN.md) —— 设计决策与实现规范（视觉体系 / 页面结构 / 数据模型 /
  抓取管线 / LLM 行为 / 管理后台设计），**改动须同步更新**
- [AGENTS.md](AGENTS.md) —— 维护会话约定（文档同步 / 设计基线 / 工程约定）
