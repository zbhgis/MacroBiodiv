<p align="center">
  <img src="assets_src/logo.png" alt="MacroBiodiv" width="160">
</p>

<h1 align="center">MacroBiodiv</h1>

<p align="center">宏观生物多样性文献库 —— 粘贴 DOI 自动建站：抓取元数据、大模型中文化、卡片浏览、可搜索可订阅。</p>

<p align="center"><a href="https://macrobiodiv.zbhgis.com">macrobiodiv.zbhgis.com</a> · <a href="DESIGN.md">设计文档</a> · <a href="https://github.com/zbhgis/GeoSciPlot">GeoSciPlot</a>（同作者姐妹项目）</p>

## 这是什么

一个**纯 Python 标准库 + 纯静态站**的文献展示系统：本地管理界面里粘贴 DOI，
自动抓取元数据（Crossref + OpenAlex）、调用你自己的大模型做中文翻译（初译 + 审校），
一键发布成可搜索、可筛选、可订阅的静态网站。无框架、无数据库、无服务端运行时。

## 维护者 · 日常维护

```bash
python scripts/admin.py        # 本地管理界面（仅本机可访问，自动打开 127.0.0.1:5201）
```

流程：**粘贴 DOI → 自动抓取 → 大模型中文化（初译+审校）→ 补标签/备注 → 点发布**。
发布自动执行：写 `meta/papers.json` → 构建站点 → git push →（可选）同步服务器。

## 站点功能

- **文献卡片网格**：期刊 + 文章类型 + 年份徽章、标题（中文优先）、作者、摘要节选、被引
- **三维筛选 + 搜索**：标签 / 期刊 / 时间区间；标题 / 作者 / DOI / 期刊 / 关键词 / 摘要全文；分页 20/30/50
- **筛选与排序均可通过 URL 分享**：`/?tag=海冰&journal=Nature&sort=cited`（sort 仅本次生效，不改写访客偏好）
- **详情页**为公众号推文式分节版式（**01 信息 / 02 摘要 / 03 引用**）：中文摘要为主阅读区、
  英文原题收合、GB/T 7714 引用条 + BibTeX 一键复制、上一篇/下一篇
- **文章类型**按出版社惯例标注（Article / Research Article / Perspective / Comment / News & Views…），
  大模型结合元数据体裁提示判定，管理界面可修改
- **分享与收录**：Open Graph 卡片、ScholarlyArticle JSON-LD、robots.txt / sitemap.xml / Atom 订阅源
- **顶部导航栏**（sticky 毛玻璃）：品牌 / 文献库 / 全站搜索 / GitHub / 主站「浩瀚地学」，移动端汉堡菜单
- 右侧悬浮工具队列（明暗主题 / 回到顶部）——导航管「去哪」、rail 管「工具」，与主站哲学一致
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

- 元数据抓取自 **Crossref**（主力）与 **OpenAlex**（摘要兜底 / 被引 / OA / 体裁提示），
  不抓出版社页面
- **时间以在线发表（online）日期为准**：同一篇文献的 print / online / issued 日期可能不同，
  全站（徽章 / 筛选 / 排序）统一取 online，无 online 记录时回落并以标记如实呈现
- **关键词为手动维护**（按文章原文填写）：作者关键词在 API 层不可得，
  OpenAlex 的内容推断关键词已明确停用
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

命令行（不进管理界面也能用）：

```bash
python scripts/fetch_doi.py 10.1038/s41467-021-24264-9   # 试试抓取，输出 JSON
python scripts/llm.py "英文标题"                          # 试试翻译
python scripts/build_site.py                              # 重建站点
python scripts/gen_logo.py                                # 重新生成 logo/favicon
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
