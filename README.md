<p align="center">
  <img src="assets_src/logo.png" alt="MacroBiodiv" width="180">
</p>

<h1 align="center">MacroBiodiv</h1>

<p align="center">宏观生物多样性文献库 —— 收集公开发表的文献基本信息，卡片浏览、可搜索、可溯源。</p>

<p align="center">在线浏览：<a href="https://macrobiodiv.zbhgis.com">macrobiodiv.zbhgis.com</a></p>

<p align="center">设计决策与实现规范：<a href="DESIGN.md">DESIGN.md</a>（样式/交互/数据/管线有改动须同步更新）</p>

## 维护者 · 日常维护

```bash
python scripts/admin.py        # 本地管理界面（仅本机可访问，自动打开 127.0.0.1:5201）
```

流程：**粘贴 DOI → 自动抓取基本信息 → 大模型中文化（初译+审校）→ 补标签/备注 → 点发布**。
抓取按 [doi2md](https://github.com/zbhgis/doi2md) 的思路走 **Crossref + OpenAlex** 双源：
标题 / 作者 / 期刊 / 发表日期 / 卷期页 / 摘要 / 关键词 / 被引 / OA。
中文翻译调用本地配置的大模型（两重处理：初译 → 审校复核，译文可在界面修改）。
发布自动执行：写 `meta/papers.json` → 构建站点 → git push。

上线服务器（生产包只含 HTML/JS，很小；需服务器 SSH 权限）：

```bash
cd site && scp -r ./* root@47.98.133.104:/var/www/macrobiodiv/
```

## 大模型中文化配置

抓取时自动把标题和摘要翻译成中文（两重：初译 + 审校复核），走 OpenAI 兼容接口：

```powershell
[Environment]::SetEnvironmentVariable("LLM_API_KEY", "sk-xxx", "User")
[Environment]::SetEnvironmentVariable("LLM_BASE_URL", "https://token.sensenova.cn/v1", "User")
[Environment]::SetEnvironmentVariable("LLM_MODEL_NAME", "sensenova-6.8-flash-lite", "User")
```

- 设置后**新开的终端**直接生效；已经开着的终端/进程会自动从注册表兜底读取
- 未配置或翻译失败不影响抓取入库，中文留空，之后可手动填写或点条目上的「重新翻译」
- 译文（中文标题 / 中文摘要）在表单里可直接修改，修改稿视为定稿：
  重新抓取 / 刷新被引不会覆盖，只有「重新翻译」会重写
- 推理模型注意：隐藏思考链计入输出配额，本模块已按 16k max_tokens 放宽；
  flash-lite 级别模型每篇约 0.5~3 分钟
- 翻译的同时大模型会按出版社惯例判定**文章类型**（article_type，如 Nature 系 Perspective /
  Science 系 Research Article / Cell 系 Spotlight），元数据体裁（OpenAlex type）作为辅助提示；
  判定结果可在管理界面修改

### 限流与故障预案

针对 429 / 超时 / 服务不稳定的分层回退，任何一层失败都不会阻断抓取与发布：

| 层级 | 机制 | 行为 |
|---|---|---|
| 请求层 | 全局节流 + 429 指数退避（2/4/8/16s）并遵守 `Retry-After` | 限流窗口内自动等待重试 |
| 会话层 | 断路器：120s 内连续 2 次「重试耗尽仍失败」→ 冷却 60s | 冷却期直接跳过翻译，不撞挂掉的服务 |
| 单篇层 | 审校失败 / 超耗时预算 → 自动降级「仅初译」并注明原因 | 保底有可用中文 |
| 流程层 | 初译也失败 → 中文留空、正常入库发布 | 事后补译 |
| 恢复层 | 文献管理「翻译缺中文的」批量补译（只补空缺、不覆盖手改、可反复执行） | 服务恢复后一键补齐 |

翻译状态在界面上明确标注：`初译+审校` / `仅初译（原因）` / `失败（原因）`。
耗时预算分级：抓取同步路径 200s（浏览器在等），批量补译任务 600s。

### 固定 Prompt

两重翻译的 prompt 集中维护在 `scripts/llm.py`：初译 `_TRANSLATOR_SYSTEM`、审校 `_REVIEWER_SYSTEM`。
内容包含：忠实完整性原则、术语规范（以术语在线为锚，附 16 条高频术语参考译名表 `_GLOSSARY`）、
专有名词与保留项规则（拉丁学名 / 单位 / 同位素记号 / 引用标记不翻译）、缩写首现规则、
标题句式规则，以及强约束的 JSON 输出格式。换研究领域时改 `_GLOSSARY` 即可。

## 站点功能

- 文献卡片网格（3/2/1 列响应式）：期刊 + 文章类型 + 年份徽章、标题（中文优先）、作者、摘要节选、标签、被引
- 分页 20 / 30（默认）/ 50
- 三维筛选 + 搜索：标签 / 期刊 / 时间区间筛选；首页搜索（标题 / 作者 / DOI / 期刊 / 关键词 / 摘要）
- **时间以在线发表（online）日期为准** —— 同一篇文献的 print / online / issued 日期可能不同，全站统一取 online（无 online 记录时回落 published → issued），筛选、排序、年份徽章均基于它
- 全站搜索独立页 `/search/`：检索范围覆盖**全部站点内容**（标题 / 副标题 / 作者 / DOI / 期刊 / 关键词 / 标签 / 备注 / 摘要全文），结果行带命中高亮，多词用空格分隔（需同时命中），支持 `?q=` 分享
- 排序：发表 新到旧 / 旧到新 / 被引 多到少
- 详情页为公众号推文式分节阅读版式（**01 信息 / 02 摘要 / 03 引用**）：中文摘要为主阅读区、
  英文原题摘要收合、信息卡网格、**GB/T 7714 引用条一键复制 + BibTeX**、上一篇/下一篇、· END ·
- **文章类型**按出版社惯例标注（Article / Research Article / Perspective / Comment / News & Views /
  Review / Spotight 等）：大模型结合 OpenAlex 体裁提示自动判定，管理界面可修改
- 右侧悬浮按钮队列：全站搜索 / 返回 Home / GitHub / 明暗主题 / 回到顶部（与主站 zbhgis.com 的 rail 同款同序）
- 筛选结果可通过 URL 参数分享：`/?tag=海冰&journal=Nature&from=2024-01-01&to=2026-12-31`

## 数据来源

- 元数据抓取自 **Crossref**（主力）与 **OpenAlex**（摘要兜底 / 被引 / OA），不抓出版社页面
- **关键词为手动维护**（按文章原文填写）：作者关键词在 Crossref/OpenAlex API 中不提供、
  仅存在于出版社页面，且 OpenAlex 的 keywords 属于内容推断的主题标签，已明确停用
- 文献 id = DOI（小写）sha1 前 10 位；`meta/papers.json` 是唯一数据源
- 手动字段（标签 / 中文标题 / 备注）只存 papers.json，重新抓取 / 刷新被引不会覆盖
- 站点零图片：文献库不需要图床，生产包只有 HTML/JS，几 KB 一篇

## 本地运行

**只想本地看看效果（任何人）**

```bash
git clone https://github.com/zbhgis/MacroBiodiv.git
cd MacroBiodiv
python scripts/build_site.py            # 生成静态站（Python 3.9+，无第三方依赖）
cd site && python -m http.server 7332   # 打开 http://127.0.0.1:7332，搜索页在 /search/
```

也可以直接 `python scripts/admin.py` 打开管理界面浏览/编辑——**但「发布」需要仓库写权限**，
非维护者会在这步失败（这不是 bug，是 GitHub 权限使然）。

**维护者在新电脑上（需要仓库写权限）**

```bash
git clone https://github.com/zbhgis/MacroBiodiv.git
cd MacroBiodiv
python scripts/admin.py                 # 打开 http://127.0.0.1:5201
```

- 索引随仓库一起来（`meta/papers.json`），开箱即可浏览、编辑、发布
- `site/`（构建产物）不在仓库里，发布时会自动重新生成
- 想让「同步站点到服务器」按钮可用：`ssh-copy-id root@47.98.133.104` 加一次公钥；
  没配也能用，手动 `scp -r site/* root@47.98.133.104:/var/www/macrobiodiv/` 即可

## 命令行（不进管理界面也能用）

```bash
python scripts/fetch_doi.py 10.1038/s41467-021-24264-9        # 试试抓取，输出 JSON
python scripts/build_site.py                                   # 重建站点
```

## 服务器部署

见 [`deploy/部署操作手册.md`](deploy/部署操作手册.md)：DNS 子域、acme 证书、
nginx 配置（`deploy/nginx-macrobiodiv.conf`）一步步来即可，与 geosciplot.zbhgis.com 同一台机器、同一套流程。

## 鸣谢

站点架构与样式移植自 [GeoSciPlot](https://github.com/zbhgis/GeoSciPlot)（同作者）；
DOI 抓取策略参考 [doi2md](https://github.com/zbhgis/doi2md)。
文献元数据来自 [Crossref](https://www.crossref.org/) 与 [OpenAlex](https://openalex.org/)，
版权归原出版方。
